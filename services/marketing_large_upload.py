"""
AITA Marketing — subida por partes de vídeos grandes a la Biblioteca (solo con el bucket S3).

Flujo: start → part 1..N (en orden, 8 MB cada una salvo la última) → complete | abort.

* Las mismas puertas que la subida normal: owner/manager, módulo activo, Biblioteca habilitada, cuota
  reservada de forma atómica ANTES de recibir bytes (clave 'upload:<media_id>').
* Solo vídeo (MP4, MOV, WebM). La extensión y el MIME declarado deben coincidir desde el inicio; la
  primera parte ya debe tener la firma real del formato y ningún contenido activo, o se cancela.
* Al completar se valida el objeto entero en el bucket sin cargarlo en memoria: tamaño exacto, firma,
  contenido activo al principio y al final, estructura MP4 caja por caja (lecturas acotadas), duración
  y dimensiones, y la suma SHA-256 recorriéndolo en trozos (para no guardar duplicados). Si algo falla,
  el objeto se borra y la reserva se libera.
* Validar el formato no es un antivirus: malware_scan_status queda 'unavailable'.
* Cada parte alarga la reserva 30 minutos: una subida abandonada caduca sola y libera su espacio.
"""
import hashlib
import logging
import math
import os
import struct
import uuid
from datetime import timedelta
from typing import Any, Dict

from services import marketing_media_files as mf
from services import marketing_privacy as pv
from services import marketing_retention as rt
from services import marketing_storage as ms
from services import object_store
from services.marketing import _guard
from services.marketing_library import MEDIA_COLS, LibraryService
from services.member_portal import PortalError

logger = logging.getLogger(__name__)
PART_BYTES = 8 * 1024 * 1024
LARGE_MAX_BYTES = int(os.getenv("MARKETING_LARGE_MAX_UPLOAD_BYTES", str(500 * 1024 * 1024)))
MOOV_MAX_BYTES = 64 * 1024 * 1024
EXTEND_MINUTES = 30
SESSION_COLS = ("id,tenant_id,media_id,created_by,storage_path,multipart_id,original_filename,safe_filename,mime_type,"
                "byte_size,part_size,parts_done,etags,reservation_key,status,expires_at")
VIDEO_MIMES = ("video/mp4", "video/quicktime", "video/webm")


def parts_for(size: int, part: int = 0) -> int:
    return max(math.ceil(int(size) / int(part or PART_BYTES)), 1)


class LargeUploadService(LibraryService):
    def _available(self) -> None:
        if not object_store.large_uploads_available(self.db):
            raise PortalError("large_upload_unavailable", 503)

    def large_limit(self, c) -> int:
        limit = min(int(c.settings["max_upload_bytes"] or 0), LARGE_MAX_BYTES)
        storage = c.settings["library_storage_limit_bytes"]
        if storage is not None:
            if int(storage) <= 0:
                raise PortalError("library_disabled", 403)
            limit = min(limit, max(int(storage) - self._used_bytes(c), 0))
            if limit <= 0:
                raise PortalError("limit_library_storage", 409)
        return limit

    def _session(self, c, sid: Any) -> Dict[str, Any]:
        try:
            sid = str(uuid.UUID(str(sid)))
        except ValueError:
            raise PortalError("upload_not_found", 404)
        rows = self.db.select("marketing_upload_sessions", {"id": f"eq.{sid}", "tenant_id": f"eq.{c.tenant_id}",
                                                            "select": SESSION_COLS, "limit": "1"})
        if not rows or rows[0]["created_by"] != c.user["id"]:
            raise PortalError("upload_not_found", 404)
        return rows[0]

    def _close(self, c, s: Dict[str, Any], status: str, error: str = "", remove_object: bool = False) -> None:
        """Cierra la sesión: cancela la subida en el bucket (o borra el objeto) y libera la reserva."""
        try:
            if remove_object:
                self.db.objects.remove(s["storage_path"])
            else:
                self.db.objects.abort_multipart(s["storage_path"], s["multipart_id"])
        except Exception as e:
            logger.warning(f"MARKETING_LARGE_UPLOAD_CLEANUP {type(e).__name__}")
        ms.release(self.db, c.tenant_id, s["reservation_key"], consumed=False)
        self.db.update("marketing_upload_sessions", {"id": f"eq.{s['id']}", "tenant_id": f"eq.{c.tenant_id}"},
                       {"status": status, "error": error or None, "updated_at": c.now.isoformat()})

    # ------------------------------------------------------------------ inicio
    @_guard
    def start(self, jwt: str, tenant_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        c = self.ctx(jwt, tenant_id)
        self._writable(c)
        self._available()
        filename = str(body.get("filename") or "")[:300]
        mime = str(body.get("mime_type") or "").split(";")[0].strip().lower()
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if ext not in mf.ALLOWED or mf.ALLOWED[ext][1] != "video":
            raise PortalError("unsupported_extension", 415)
        if mime != mf.ALLOWED[ext][0]:
            raise PortalError("mime_mismatch", 415)
        try:
            size = int(body.get("byte_size"))
        except (TypeError, ValueError):
            raise PortalError("invalid_upload", 400)
        if size <= 0:
            raise PortalError("empty_file", 400)
        if size > self.large_limit(c):
            raise PortalError("file_too_large", 413)
        media_id = str(uuid.uuid4())
        safe = mf.safe_filename(filename, mf.MIME_TO_EXT[mime])
        path = self._path(c, mf.original_path(c.tenant_id, media_id, safe))
        key = ms.reserve(self.db, c.tenant_id, "upload", size, f"upload:{media_id}")
        try:
            multipart_id = self.db.objects.create_multipart(path, mime)
        except Exception as e:
            ms.release(self.db, c.tenant_id, key, consumed=False)
            logger.error(f"MARKETING_LARGE_UPLOAD_START {type(e).__name__}")
            raise PortalError("storage_unavailable", 503)
        row = self.db.insert("marketing_upload_sessions", {
            "tenant_id": c.tenant_id, "media_id": media_id, "created_by": c.user["id"], "storage_path": path,
            "multipart_id": multipart_id, "original_filename": os.path.basename(filename.replace("\\", "/"))[:200],
            "safe_filename": safe, "mime_type": mime, "byte_size": size, "part_size": PART_BYTES,
            "reservation_key": key, "status": "uploading", "parts_done": 0, "etags": [],
            "expires_at": (c.now + timedelta(hours=24)).isoformat()})
        return {"upload_id": row["id"], "part_bytes": PART_BYTES, "parts": parts_for(size)}

    # ------------------------------------------------------------------ partes
    @_guard
    def put_part(self, jwt: str, tenant_id: str, sid: str, number: Any, data: bytes) -> Dict[str, Any]:
        c = self.ctx(jwt, tenant_id)
        self._writable(c)
        self._available()
        s = self._session(c, sid)
        if s["status"] != "uploading":
            raise PortalError("upload_closed", 409)
        if ms._expired(s["expires_at"], c.now):
            self._close(c, s, "expired", "expired")
            raise PortalError("upload_expired", 410)
        total = parts_for(s["byte_size"], s["part_size"])
        try:
            n = int(number)
        except (TypeError, ValueError):
            raise PortalError("invalid_part", 400)
        if n < 1 or n > total:
            raise PortalError("invalid_part", 400)
        if n <= s["parts_done"]:
            return {"parts_done": s["parts_done"], "parts": total}          # reintento de una parte ya guardada
        if n != s["parts_done"] + 1:
            raise PortalError("upload_part_out_of_order", 409)
        expected = s["part_size"] if n < total else s["byte_size"] - s["part_size"] * (total - 1)
        if len(data) != expected:
            raise PortalError("invalid_part_size", 400)
        if n == 1:
            code = self._head_problem(data[:mf.SCAN_WINDOW], s["mime_type"])
            if code:
                self._close(c, s, "failed", code)
                raise PortalError(code, 415)
        if not self.db.rpc("marketing_extend_storage_reservation",
                           {"p_tenant": c.tenant_id, "p_key": s["reservation_key"], "p_minutes": EXTEND_MINUTES}):
            self._close(c, s, "expired", "reservation_expired")
            raise PortalError("upload_expired", 410)
        try:
            etag = self.db.objects.upload_part(s["storage_path"], s["multipart_id"], n, data)
        except Exception as e:
            logger.error(f"MARKETING_LARGE_UPLOAD_PART {type(e).__name__}")
            raise PortalError("storage_unavailable", 503)
        done = self.db.update("marketing_upload_sessions",
                              {"id": f"eq.{s['id']}", "tenant_id": f"eq.{c.tenant_id}", "status": "eq.uploading",
                               "parts_done": f"eq.{n - 1}"},
                              {"parts_done": n, "etags": list(s["etags"] or []) + [etag], "updated_at": c.now.isoformat()})
        if not done:
            raise PortalError("upload_part_out_of_order", 409)              # otra petición se adelantó
        return {"parts_done": n, "parts": total}

    @staticmethod
    def _head_problem(head: bytes, mime: str) -> str:
        if mf.looks_dangerous(head):
            return "forbidden_file_type"
        real = mf.sniff(head)
        if real is None:
            return "unrecognized_content"
        return "" if real == mime else "extension_mismatch"

    # ------------------------------------------------------------------ cancelar / completar
    @_guard
    def abort(self, jwt: str, tenant_id: str, sid: str) -> Dict[str, Any]:
        c = self.ctx(jwt, tenant_id)
        s = self._session(c, sid)
        if s["status"] == "uploading":
            self._close(c, s, "aborted")
        return {"status": "aborted"}

    @_guard
    def complete(self, jwt: str, tenant_id: str, sid: str) -> Dict[str, Any]:
        c = self.ctx(jwt, tenant_id)
        self._writable(c)
        self._available()
        s = self._session(c, sid)
        if s["status"] != "uploading":
            raise PortalError("upload_closed", 409)
        if s["parts_done"] != parts_for(s["byte_size"], s["part_size"]):
            raise PortalError("upload_incomplete", 409)
        if not self.db.update("marketing_upload_sessions",
                              {"id": f"eq.{s['id']}", "tenant_id": f"eq.{c.tenant_id}", "status": "eq.uploading"},
                              {"status": "completing", "updated_at": c.now.isoformat()}):
            raise PortalError("upload_closed", 409)
        try:
            self.db.objects.complete_multipart(s["storage_path"], s["multipart_id"], list(s["etags"]))
        except Exception as e:
            logger.error(f"MARKETING_LARGE_UPLOAD_COMPLETE {type(e).__name__}")
            self._close(c, s, "failed", "storage_unavailable")
            raise PortalError("storage_unavailable", 503)
        try:
            info = validate_object(self.db.objects, s["storage_path"], s["mime_type"], s["byte_size"])
        except mf.MediaFileError as e:
            self._close(c, s, "failed", e.code, remove_object=True)
            raise PortalError(e.code, e.status)
        except Exception as e:
            logger.error(f"MARKETING_LARGE_UPLOAD_VALIDATE {type(e).__name__}")
            self._close(c, s, "failed", "storage_unavailable", remove_object=True)
            raise PortalError("storage_unavailable", 503)
        dup = self.db.select("marketing_media", {"tenant_id": f"eq.{c.tenant_id}", "checksum": f"eq.{info['checksum']}",
                                                 "processing_status": "neq.deleted", "select": MEDIA_COLS, "limit": "1"})
        if dup:
            self._close(c, s, "completed", "duplicate", remove_object=True)
            return {**dup[0], "duplicate": True}
        media_id = s["media_id"]
        row = self.db.insert("marketing_media", {
            "id": media_id, "tenant_id": c.tenant_id, "storage_path": s["storage_path"],
            "original_filename": s["original_filename"], "media_type": "video", "mime_type": s["mime_type"],
            "byte_size": s["byte_size"], "width": info["width"], "height": info["height"],
            "duration_ms": info["duration_ms"], "checksum": info["checksum"], "uploaded_by": c.user["id"],
            "validation_status": "passed", "malware_scan_status": "unavailable", "contains_people": None,
            "contains_minors": None, "people_policy": "exclude", "consent_status": "unknown",
            "processing_status": "uploaded", "metadata": {"upload": "multipart"},
            "retention_days": rt.default_days(c.settings), "retention_status": "active",
            "expires_at": (c.now + timedelta(days=rt.default_days(c.settings))).isoformat()})
        ms.release(self.db, c.tenant_id, s["reservation_key"], consumed=True)
        self._media_event(c, media_id, "upload", {"mime_type": s["mime_type"], "byte_size": s["byte_size"],
                                                  "malware_scan_status": "unavailable", "multipart": True})
        self.db.update("marketing_media", {"id": f"eq.{media_id}", "tenant_id": f"eq.{c.tenant_id}"},
                       {"processing_status": "ready"})
        self.db.update("marketing_upload_sessions", {"id": f"eq.{s['id']}", "tenant_id": f"eq.{c.tenant_id}"},
                       {"status": "completed", "updated_at": c.now.isoformat()})
        row["processing_status"] = "ready"
        return {**row, "privacy_class": pv.original_class(row)}


# ---------------------------------------------------------------------- validación en el bucket
def _mp4_moov(store, path: str, size: int):
    """Recorre las cajas de primer nivel con lecturas de 16 bytes. Devuelve (inicio, tamaño) de moov o None.
    Archivo truncado o cajas imposibles → MediaFileError."""
    i, n, moov = 0, 0, None
    while i + 8 <= size and n < mf.MP4_MAX_BOXES:
        h = store.read_range(path, i, min(i + 15, size - 1))
        box, kind = struct.unpack(">I4s", h[:8])
        header = 8
        if box == 1 and len(h) >= 16:
            box, header = struct.unpack(">Q", h[8:16])[0], 16
        if box == 0:
            box = size - i                                    # la última caja llega hasta el final
        if box < header or i + box > size:
            raise mf.MediaFileError("truncated_file", 415)
        if kind == b"moov" and moov is None:
            moov = (i, box)
        i += box
        n += 1
    if i != size or n == 0:
        raise mf.MediaFileError("truncated_file", 415)
    return moov


def validate_object(store, path: str, mime: str, expected_size: int) -> Dict[str, Any]:
    size = store.size(path)
    if size is None or size != int(expected_size):
        raise mf.MediaFileError("truncated_file", 415)
    head = store.read_range(path, 0, min(mf.SCAN_WINDOW, size) - 1)
    tail = store.read_range(path, max(size - mf.SCAN_WINDOW, 0), size - 1).lower()
    if mf.looks_dangerous(head) or any(marker in tail for marker in mf._EMBEDDED):
        raise mf.MediaFileError("forbidden_file_type", 415)
    if mf.sniff(head) != mime:
        raise mf.MediaFileError("extension_mismatch", 415)
    width = height = duration = None
    if mime in ("video/mp4", "video/quicktime"):
        moov = _mp4_moov(store, path, size)
        if moov and moov[1] <= MOOV_MAX_BYTES:
            data = store.read_range(path, moov[0], moov[0] + moov[1] - 1)
            try:
                width, height, duration = mf._mp4_info(data)
            except (struct.error, IndexError, ValueError):
                width = height = duration = None
    elif size < 32:
        raise mf.MediaFileError("truncated_file", 415)
    if any(v is not None and not (1 <= v <= mf.MAX_DIMENSION) for v in (width, height)):
        raise mf.MediaFileError("invalid_media_metadata", 415)
    if duration is not None and (duration > mf.MAX_DURATION_MS
                                 or (duration > 0 and size * 1000 / duration < mf.MIN_VIDEO_BYTES_PER_SECOND)):
        raise mf.MediaFileError("invalid_media_metadata", 415)
    digest = hashlib.sha256()
    for chunk in store.stream(path, chunk_size=1024 * 1024):
        digest.update(chunk)
    return {"checksum": digest.hexdigest(), "width": width, "height": height, "duration_ms": duration}


def expire_stale(db, now) -> int:
    """Subidas abandonadas (sin partes nuevas en 2 horas; su reserva ya caducó a los 30 minutos): se
    cancelan en el bucket y la sesión queda 'expired'. Para el purgador programado."""
    if not object_store.large_uploads_available(db):
        return 0
    idle = (now - timedelta(hours=2)).isoformat()
    rows = db.select("marketing_upload_sessions", {"status": "eq.uploading", "updated_at": f"lt.{idle}",
                                                   "select": SESSION_COLS, "limit": "200"}) or []
    for s in rows:
        try:
            db.objects.abort_multipart(s["storage_path"], s["multipart_id"])
        except Exception as e:
            logger.warning(f"MARKETING_LARGE_UPLOAD_EXPIRE {type(e).__name__}")
        ms.release(db, s["tenant_id"], s["reservation_key"], consumed=False)
        db.update("marketing_upload_sessions", {"id": f"eq.{s['id']}", "status": "eq.uploading"},
                  {"status": "expired", "error": "expired", "updated_at": now.isoformat()})
    return len(rows)
