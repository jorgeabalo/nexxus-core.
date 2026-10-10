"""
AITA Marketing (Fase 2) — reproducción de la Biblioteca con HTTP Range real.

El elemento <video> no puede enviar la cabecera Authorization, así que el panel pide primero una
AUTORIZACIÓN TEMPORAL same-origin (POST …/stream-token, con el JWT): un token aleatorio de un solo
archivo, ligado a tenant + usuario + archivo, que caduca en minutos y se puede revocar. Solo se guarda
su hash. Con él, el navegador pide directamente HEAD y rangos (GET/HEAD …/library/stream/{token}) y
puede avanzar y retroceder sin descargar el vídeo completo.

Cada petición (también HEAD y cada Range) vuelve a validar: token vigente y no revocado, usuario aún
owner/manager activo del tenant, módulo Marketing activo, archivo validado, no eliminado ni pendiente
de purga, y ruta del propio tenant. Nunca se carga el archivo entero en memoria.
"""
import hashlib
import logging
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from services import marketing_media_files as mf
from services import marketing_retention as rt
from services.marketing_studio_base import BUCKET, StudioBase
from services.member_portal import PortalError

logger = logging.getLogger(__name__)
STREAM_PATH = "/api/manager/marketing/library/stream/"
_TOKEN = re.compile(r"^[A-Za-z0-9_-]{32,64}$")
MEDIA_COLS = ("id,tenant_id,storage_path,mime_type,byte_size,validation_status,processing_status,retention_status")
DER_COLS = "id,tenant_id,media_id,storage_path,mime_type,byte_size,status"


def stream_ttl() -> int:
    """Duración del token (segundos). Corta por diseño: 60..900, 600 por defecto."""
    try:
        v = int(os.getenv("MARKETING_STREAM_TTL", "600"))
    except ValueError:
        v = 600
    return min(max(v, 60), 900)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def serve(db, path: str, mime: str, size: int, range_header: Optional[str], head: bool) -> Dict[str, Any]:
    """Respuesta 200/206/416 en trozos. HEAD no toca Storage."""
    base = {"Accept-Ranges": "bytes", "Content-Type": mime}
    try:
        rng = mf.parse_range(range_header, size)
    except mf.RangeNotSatisfiable:
        return {"status": 416, "headers": {**base, "Content-Range": f"bytes */{size}", "Content-Length": "0"}, "chunks": None}
    start, end = rng if rng else (0, size - 1)
    headers = {**base, "Content-Length": str(end - start + 1)}
    if rng:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    status = 206 if rng else 200
    if head:
        return {"status": status, "headers": headers, "chunks": None}
    try:
        chunks = db.storage_stream(BUCKET, path, byte_range=(start, end) if rng else None)
    except Exception as e:
        logger.error(f"MARKETING_PREVIEW_ERROR {type(e).__name__}")
        raise PortalError("storage_unavailable", 503)
    return {"status": status, "headers": headers, "chunks": chunks}


def resolve(svc: StudioBase, c, media_id: Any, derivative_id: Any = None):
    """(ruta, mime, tamaño) de un archivo visible de ESTE tenant, o 404."""
    m = svc._one(c, "marketing_media", media_id, MEDIA_COLS)
    if (m.get("validation_status") != "passed" or m["processing_status"] in ("rejected", "deleted")
            or m.get("retention_status") in rt.PURGE_STATES):
        raise PortalError("not_found", 404)                      # pendiente de purga o purgado: sin acceso
    path, mime, size = m["storage_path"], m["mime_type"], m.get("byte_size")
    if derivative_id:
        d = svc._one(c, "marketing_media_derivatives", derivative_id, DER_COLS)
        if d["media_id"] != m["id"] or d["status"] in ("deleted", "mock_only") or not d.get("storage_path"):
            raise PortalError("not_found", 404)
        path, mime, size = d["storage_path"], d.get("mime_type"), d.get("byte_size")
    if not path or not mf.path_belongs_to(path, c.tenant_id) or path.split("/")[2] != m["id"]:
        logger.warning("MARKETING_PATH_REJECTED")
        raise PortalError("not_found", 404)
    if mime not in mf.MIME_TO_EXT or not size or int(size) <= 0:
        raise PortalError("not_found", 404)
    return m, path, mime, int(size)


class MediaStreamService(StudioBase):
    def _clock(self) -> datetime:
        return self._now or datetime.now(timezone.utc)

    def issue_token(self, jwt: str, tenant_id: str, media_id: str, derivative_id: str = "") -> Dict[str, Any]:
        c = self.ctx(jwt, tenant_id)
        m, _path, mime, _size = resolve(self, c, media_id, derivative_id or None)
        token, ttl = secrets.token_urlsafe(32), stream_ttl()
        self.db.insert("marketing_stream_tokens", {
            "tenant_id": c.tenant_id, "user_id": c.user["id"], "media_id": m["id"], "derivative_id": derivative_id or None,
            "token_hash": token_hash(token), "created_at": self._clock().isoformat(),
            "expires_at": (self._clock() + timedelta(seconds=ttl)).isoformat()})
        return {"url": f"{STREAM_PATH}{token}", "expires_in": ttl, "mime": mime}

    def revoke(self, jwt: str, tenant_id: str, media_id: str) -> Dict[str, Any]:
        """Revoca los tokens del usuario para ese archivo (p. ej. al cerrar la vista previa)."""
        c = self.ctx(jwt, tenant_id)
        m = self._one(c, "marketing_media", media_id, "id")
        rows = self.db.update("marketing_stream_tokens", {"tenant_id": f"eq.{c.tenant_id}", "media_id": f"eq.{m['id']}",
                                                          "user_id": f"eq.{c.user['id']}", "revoked_at": "is.null"},
                              {"revoked_at": self._clock().isoformat()}) or []
        return {"revoked": len(rows)}

    def stream(self, token: str, range_header: Optional[str], head: bool) -> Dict[str, Any]:
        """Sin JWT: el token es la autorización. Cada petición lo revalida TODO."""
        if not token or not _TOKEN.match(token):
            raise PortalError("not_found", 404)
        row = (self.db.select("marketing_stream_tokens", {"token_hash": f"eq.{token_hash(token)}", "select": "*",
                                                          "limit": "1"}) or [None])[0]
        if not row or row.get("revoked_at") or rt._dt(row["expires_at"]) <= self._clock():
            raise PortalError("not_found", 404)                  # caducado, revocado o inexistente: 404 sin detalles
        try:
            c = self._ctx_for({"id": row["user_id"]}, row["tenant_id"])   # rol, tenant y módulo, de nuevo
        except PortalError:
            raise PortalError("not_found", 404)
        _m, path, mime, size = resolve(self, c, row["media_id"], row.get("derivative_id"))
        return serve(self.db, path, mime, size, range_header, head)


def revoke_all(db, tenant_id: str, media_id: str, now: datetime) -> None:
    """Al eliminar, purgar o retirar el consentimiento: ningún token sigue sirviendo el archivo."""
    db.update("marketing_stream_tokens", {"tenant_id": f"eq.{tenant_id}", "media_id": f"eq.{media_id}",
                                          "revoked_at": "is.null"}, {"revoked_at": now.isoformat()})


class RedactStreamTokens(logging.Filter):
    """Los logs de acceso de uvicorn nunca muestran el token de streaming."""
    _re = re.compile(r"(/library/stream/)[A-Za-z0-9_-]+")

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(self._re.sub(r"\1***", a) if isinstance(a, str) else a for a in record.args)
        if isinstance(record.msg, str):
            record.msg = self._re.sub(r"\1***", record.msg)
        return True
