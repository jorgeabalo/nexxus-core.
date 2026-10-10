"""
AITA Marketing (Fase 2) — Biblioteca multimedia privada.

* Solo owner/manager del tenant (puerta de Fase 1). Todo pasa por el backend.
* Subida: extensión + MIME declarado + firma real (services/marketing_media_files.py); ruta
  construida en el servidor: {tenant_id}/originals/{asset_id}/{safe_filename}; x-upsert=false.
* El original nunca se modifica ni se borra (también lo impone la base de datos). Los derivados
  van a {tenant_id}/derivatives/{asset_id}/{derivative_id}.{ext}.
* Vista previa con URL firmada de corta duración (nunca URLs públicas permanentes). Las URLs
  firmadas no se registran en logs.
* Personas reales: clasificación explícita; por defecto exclude. Anonimizar crea un derivado que
  SIEMPRE necesita revisión humana antes de usarse.
"""
import logging
import struct
import uuid
import zlib
from typing import Any, Dict, Optional

from services import marketing_jobs_domain as jd
from services import marketing_media_files as mf
from services import marketing_privacy as pv
from services.marketing import _guard
from services.marketing_domain import DomainError
from services.marketing_studio_base import BUCKET, StudioBase, signed_ttl, uid
from services.member_portal import PortalError

logger = logging.getLogger(__name__)
MEDIA_COLS = ("id,tenant_id,storage_path,original_filename,media_type,mime_type,byte_size,width,height,duration_ms,"
              "checksum,uploaded_by,created_at,updated_at,contains_people,people_policy,consent_status,"
              "processing_status,metadata")
DER_COLS = ("id,tenant_id,media_id,kind,method,storage_path,mime_type,status,detection_confidence,review_required,"
            "reviewed_by,reviewed_at,created_by,created_at,metadata")


def placeholder_png(w: int = 9, h: int = 16, gray: int = 128) -> bytes:
    """PNG gris mínimo (sin dependencias) para los derivados simulados: deja claro que es un mock."""
    raw = b"".join(b"\x00" + bytes([gray]) * w for _ in range(h))
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


class LibraryService(StudioBase):
    def __init__(self, db, now=None, providers=None, detector: Optional[pv.FaceDetector] = None):
        super().__init__(db, now=now, providers=providers)
        self.detector = detector or pv.UnavailableFaceDetector()

    # ------------------------------------------------------------------ listar / ver
    def library(self, jwt: str, tenant_id: str, status: str = "") -> Dict[str, Any]:
        c = self.ctx(jwt, tenant_id)
        params = {"tenant_id": f"eq.{c.tenant_id}", "select": MEDIA_COLS, "order": "created_at.desc", "limit": "500"}
        if status:
            if status not in jd.MEDIA_STATUSES:
                raise PortalError("invalid_status", 400)
            params["processing_status"] = f"eq.{status}"
        items = self.db.select("marketing_media", params) or []
        ders = self.db.select("marketing_media_derivatives", {"tenant_id": f"eq.{c.tenant_id}", "select": DER_COLS,
                                                              "limit": "2000"}) or []
        for m in items:
            m["privacy_class"] = pv.original_class(m)
            m["derivatives"] = [x for x in ders if x["media_id"] == m["id"]]
        used = sum(int(m.get("byte_size") or 0) for m in (self.db.select("marketing_media", {
            "tenant_id": f"eq.{c.tenant_id}", "select": "byte_size", "limit": "100000"}) or []))
        return {"items": items, "storage": {"used_bytes": used, "limit_bytes": c.settings["library_storage_limit_bytes"],
                                            "max_upload_bytes": c.settings["max_upload_bytes"]},
                "warnings": list(pv.WARNINGS), "enabled": c.settings["marketing_enabled"],
                "can_upload": bool(c.settings["marketing_enabled"])}

    def preview(self, jwt: str, tenant_id: str, media_id: str, derivative_id: str = "") -> Dict[str, Any]:
        """URL firmada de corta duración, solo para un archivo de ESTE tenant."""
        c = self.ctx(jwt, tenant_id)
        m = self._one(c, "marketing_media", media_id, MEDIA_COLS)
        path = m["storage_path"]
        if derivative_id:
            der = self._one(c, "marketing_media_derivatives", derivative_id, DER_COLS)
            if der["media_id"] != m["id"] or not der.get("storage_path"):
                raise PortalError("not_found", 404)
            path = der["storage_path"]
        if not mf.path_belongs_to(path, c.tenant_id):
            raise PortalError("not_found", 404)
        ttl = signed_ttl()
        try:
            url = self.db.storage_sign(BUCKET, path, ttl)
        except Exception as e:
            logger.error(f"MARKETING_SIGN_ERROR {type(e).__name__}")     # sin la URL ni la ruta
            raise PortalError("storage_unavailable", 503)
        return {"url": url, "expires_in": ttl}

    # ------------------------------------------------------------------ subir
    @_guard
    def upload(self, jwt: str, tenant_id: str, filename: str, declared_mime: str, data: bytes) -> Dict[str, Any]:
        c = self.ctx(jwt, tenant_id)
        self._writable(c)
        try:
            info = mf.validate(data, filename, declared_mime, int(c.settings["max_upload_bytes"] or 0))
        except mf.MediaFileError as e:
            raise PortalError(e.code, e.status)
        limit = c.settings["library_storage_limit_bytes"]
        if limit is not None:
            used = sum(int(m.get("byte_size") or 0) for m in (self.db.select("marketing_media", {
                "tenant_id": f"eq.{c.tenant_id}", "select": "byte_size", "limit": "100000"}) or []))
            if used + info["byte_size"] > int(limit):
                raise PortalError("limit_library_storage", 409)
        dup = self.db.select("marketing_media", {"tenant_id": f"eq.{c.tenant_id}", "checksum": f"eq.{info['checksum']}",
                                                 "select": MEDIA_COLS, "limit": "1"})
        if dup:
            return {**dup[0], "duplicate": True}          # mismo archivo: no se sube dos veces
        media_id = str(uuid.uuid4())
        path = mf.original_path(c.tenant_id, media_id, info["safe_filename"])
        try:
            self.db.storage_upload(BUCKET, path, data, info["mime_type"])
        except Exception as e:
            logger.error(f"MARKETING_UPLOAD_ERROR {type(e).__name__}")
            raise PortalError("storage_unavailable", 503)
        row = self.db.insert("marketing_media", {
            "id": media_id, "tenant_id": c.tenant_id, "storage_path": path,
            "original_filename": info["original_filename"], "media_type": info["media_type"],
            "mime_type": info["mime_type"], "byte_size": info["byte_size"], "width": info["width"],
            "height": info["height"], "duration_ms": info["duration_ms"], "checksum": info["checksum"],
            "uploaded_by": c.user["id"], "contains_people": None, "people_policy": "exclude",
            "consent_status": "unknown", "processing_status": "uploaded", "metadata": {}})
        # Validación completa en el servidor: el archivo queda listo, pero con personas "desconocido"
        # y política exclude hasta que owner/manager lo clasifique.
        self.db.update("marketing_media", {"id": f"eq.{media_id}", "tenant_id": f"eq.{c.tenant_id}"},
                       {"processing_status": "ready"})
        row["processing_status"] = "ready"
        return {**row, "privacy_class": pv.original_class(row)}

    # ------------------------------------------------------------------ clasificar personas
    @_guard
    def classify(self, jwt: str, tenant_id: str, media_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        c = self.ctx(jwt, tenant_id)
        self._writable(c)
        m = self._one(c, "marketing_media", media_id, MEDIA_COLS)
        if m["processing_status"] in ("archived", "rejected"):
            raise PortalError("not_editable", 409)
        cp = body.get("contains_people")
        out = pv.classify(cp if cp in (True, False) else None, str(body.get("people_policy") or "exclude"),
                          str(body.get("consent_status") or "unknown"))
        note = str(body.get("consent_note") or "")[:300]
        meta = dict(m.get("metadata") or {})
        if note:
            meta["consent_note"] = note
        meta["classified_by"], meta["classified_at"] = c.user["id"], c.now.isoformat()
        rows = self.db.update("marketing_media", {"id": f"eq.{m['id']}", "tenant_id": f"eq.{c.tenant_id}"},
                              {"contains_people": out["contains_people"], "people_policy": out["people_policy"],
                               "consent_status": out["consent_status"], "metadata": meta})
        return {**(rows[0] if rows else m), "privacy_class": out["privacy_class"]}

    @_guard
    def set_archived(self, jwt: str, tenant_id: str, media_id: str, archived: bool) -> Dict[str, Any]:
        """Archivar oculta el archivo de la selección; nunca lo borra."""
        c = self.ctx(jwt, tenant_id)
        self._writable(c)
        m = self._one(c, "marketing_media", media_id, MEDIA_COLS)
        target = "archived" if archived else "ready"
        jd.check_media_transition(m["processing_status"], target)
        rows = self.db.update("marketing_media", {"id": f"eq.{m['id']}", "tenant_id": f"eq.{c.tenant_id}",
                                                  "processing_status": f"eq.{m['processing_status']}"},
                              {"processing_status": target})
        if not rows:
            raise PortalError("conflict", 409)
        return rows[0]

    # ------------------------------------------------------------------ anonimizar (derivado)
    @_guard
    def anonymize(self, jwt: str, tenant_id: str, media_id: str, method: str) -> Dict[str, Any]:
        c = self.ctx(jwt, tenant_id)
        self._writable(c)
        m = self._one(c, "marketing_media", media_id, MEDIA_COLS)
        if m["processing_status"] != "ready":
            raise PortalError("media_not_ready", 409)
        if m.get("people_policy") != "anonymize":
            raise PortalError("policy_not_anonymize", 409)
        # Detección local, sin identificar a nadie. Si no hay detector fiable, se pide revisión humana.
        # En esta fase el detector es simulado o no existe: no hace falta descargar el original.
        detection = self.detector.detect(b"", m["mime_type"])
        plan = pv.anonymization_plan(method, detection)
        der_id = str(uuid.uuid4())
        path = None
        mock = getattr(self.detector, "name", "") == "mock"
        if mock and not plan["low_confidence"]:
            # Derivado SIMULADO: un marcador gris, nunca una copia del original.
            path = mf.derivative_path(c.tenant_id, m["id"], der_id, "png")
            try:
                self.db.storage_upload(BUCKET, path, placeholder_png(), "image/png")
            except Exception as e:
                logger.error(f"MARKETING_UPLOAD_ERROR {type(e).__name__}")
                raise PortalError("storage_unavailable", 503)
        row = self.db.insert("marketing_media_derivatives", {
            "id": der_id, "tenant_id": c.tenant_id, "media_id": m["id"], "kind": "anonymized", "method": plan["method"],
            "storage_path": path, "mime_type": "image/png" if path else None, "status": "needs_review",
            "detection_confidence": plan["detection_confidence"], "review_required": True, "created_by": c.user["id"],
            "metadata": {"mock": mock, "low_confidence": plan["low_confidence"], "stop_reason": plan["stop_reason"],
                         "guarantee": plan["guarantee"], "detector": getattr(self.detector, "name", "unknown")}})
        return {**row, "warnings": plan["warnings"]}

    @_guard
    def review_derivative(self, jwt: str, tenant_id: str, derivative_id: str, approve: bool,
                          confirm_reviewed: bool = False) -> Dict[str, Any]:
        """Una persona revisa el derivado anonimizado. Solo así puede quedar 'ready'."""
        c = self.ctx(jwt, tenant_id)
        self._writable(c)
        der = self._one(c, "marketing_media_derivatives", derivative_id, DER_COLS)
        if der["status"] != "needs_review":
            raise PortalError("invalid_transition", 409)
        if approve and (not confirm_reviewed or not der.get("storage_path")):
            raise DomainError("human_review_required", 409)
        values = {"status": "ready" if approve else "rejected", "reviewed_by": c.user["id"],
                  "reviewed_at": c.now.isoformat()}
        rows = self.db.update("marketing_media_derivatives", {"id": f"eq.{der['id']}", "tenant_id": f"eq.{c.tenant_id}",
                                                              "status": "eq.needs_review"}, values)
        if not rows:
            raise PortalError("conflict", 409)
        return rows[0]


def media_for_job(svc: StudioBase, c, media_ids, derivative_ids) -> Dict[str, Any]:
    """Resuelve las entradas de un trabajo SOLO dentro del tenant y calcula su clase de privacidad.
    exclude → no se puede usar; anonymize → solo con derivado revisado."""
    ids = [uid(x, "invalid_media", 400) for x in (media_ids or [])][:20]
    ders = {uid(x, "invalid_media", 400) for x in (derivative_ids or [])}
    out, kinds = [], set()
    for mid in ids:
        m = svc._one(c, "marketing_media", mid, MEDIA_COLS)
        if m["processing_status"] != "ready":
            raise PortalError("media_not_ready", 409)
        der = None
        if m.get("people_policy") == "anonymize":
            cands = svc.db.select("marketing_media_derivatives", {
                "tenant_id": f"eq.{c.tenant_id}", "media_id": f"eq.{m['id']}", "kind": "eq.anonymized",
                "status": "eq.ready", "select": DER_COLS, "limit": "20"}) or []
            cands = [x for x in cands if x["id"] in ders] or cands[:1]
            if not cands:
                raise PortalError("anonymization_required", 409)
            der = cands[0]
        elif m.get("people_policy") == "exclude" and m.get("contains_people") is not False:
            raise PortalError("media_excluded", 409)
        cls = pv.input_class(m, der)
        if cls == "restricted":
            raise PortalError("privacy_blocked", 409)
        out.append({"media_id": m["id"], "derivative_id": der["id"] if der else None, "privacy_class": cls,
                    "media_type": m["media_type"]})
        kinds.add(m["media_type"])
    order = ("consented_people", "anonymized_people", "business_media_no_people", "synthetic_only")
    worst = next((k for k in order if any(i["privacy_class"] == k for i in out)), "synthetic_only")
    return {"inputs": out, "privacy_class": worst, "kinds": sorted(kinds)}
