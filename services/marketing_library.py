"""
AITA Marketing (Fase 2) — Biblioteca multimedia privada.

* Solo owner/manager del tenant (puerta de Fase 1). Todo pasa por el backend.
* Subida: el límite de tamaño se aplica ANTES de leer el cuerpo (ruta), luego extensión + MIME +
  firma real + estructura (services/marketing_media_files.py). Ruta construida en el servidor:
  {tenant_id}/originals/{asset_id}/{safe_filename}; x-upsert=false (nunca se sobrescribe).
* Validación de formato ≠ antivirus: malware_scan_status queda 'unavailable' mientras no haya
  escáner; nunca 'clean' sin un escáner real.
* Vista previa entregada por el backend en trozos (mismo origen): ninguna URL de Storage llega al
  navegador y la CSP no se abre a otros dominios.
* Borrado controlado: auditado, bloqueado si un trabajo activo usa el archivo; la fila queda como
  registro ('deleted') y los objetos de Storage se eliminan.
* Personas reales y menores: clasificación explícita; por defecto exclude. Consentimiento retirable.
  Anonimizar hoy es SIMULADO: el derivado queda 'mock_only' / 'awaiting_processing' y no se usa.
"""
import logging
import uuid
from datetime import timedelta
from typing import Any, Dict, Optional

from services import marketing_jobs_domain as jd
from services import marketing_media_files as mf
from services import marketing_privacy as pv
from services import marketing_retention as rt
from services.marketing import _guard
from services.marketing_studio_base import BUCKET, StudioBase, uid
from services.member_portal import PortalError

logger = logging.getLogger(__name__)
MEDIA_COLS = ("id,tenant_id,storage_path,original_filename,media_type,mime_type,byte_size,width,height,duration_ms,"
              "checksum,uploaded_by,created_at,updated_at,validation_status,malware_scan_status,contains_people,"
              "contains_minors,people_policy,consent_status,consent_updated_at,processing_status,deleted_at,metadata,"
              "retention_days,expires_at,retention_status,protected_until,purge_reason")
DER_COLS = ("id,tenant_id,media_id,kind,method,storage_path,mime_type,status,is_mock,detection_confidence,"
            "review_required,reviewed_by,reviewed_at,created_by,created_at,metadata")
ACTIVE_JOBS = ("draft", "awaiting_generation_approval", "queued", "processing")


class UnavailableMalwareScanner:
    """No hay escáner antivirus configurado: el estado es 'unavailable', nunca 'clean'."""
    name = "unavailable"

    def scan(self, data: bytes) -> str:
        return "unavailable"


class LibraryService(StudioBase):
    def __init__(self, db, now=None, providers=None, detector: Optional[pv.FaceDetector] = None, scanner=None):
        super().__init__(db, now=now, providers=providers)
        self.detector = detector or pv.UnavailableFaceDetector()
        self.scanner = scanner or UnavailableMalwareScanner()

    # ------------------------------------------------------------------ helpers
    def _path(self, c, path: Optional[str]) -> str:
        """Nunca se construye ni se consulta una ruta de otro tenant."""
        if not path or not mf.path_belongs_to(path, c.tenant_id):
            logger.warning("MARKETING_PATH_REJECTED")                 # sin la ruta
            raise PortalError("not_found", 404)
        return path

    def _media_event(self, c, media_id: str, action: str, detail: Optional[Dict] = None, derivative_id=None):
        self.db.insert("marketing_media_events", {"tenant_id": c.tenant_id, "media_id": media_id,
                                                  "derivative_id": derivative_id, "action": action,
                                                  "actor_id": c.user["id"], "actor_role": c.role, "detail": detail or {}})

    def _used_bytes(self, c) -> int:
        return sum(int(m.get("byte_size") or 0) for m in (self.db.select("marketing_media", {
            "tenant_id": f"eq.{c.tenant_id}", "processing_status": "neq.deleted", "select": "byte_size",
            "limit": "100000"}) or []))

    def _media(self, c, media_id: Any) -> Dict[str, Any]:
        m = self._one(c, "marketing_media", media_id, MEDIA_COLS)
        if m["processing_status"] == "deleted":
            raise PortalError("not_found", 404)
        return m

    # ------------------------------------------------------------------ listar / ver
    def library(self, jwt: str, tenant_id: str, status: str = "") -> Dict[str, Any]:
        c = self.ctx(jwt, tenant_id)
        params = {"tenant_id": f"eq.{c.tenant_id}", "processing_status": "neq.deleted", "select": MEDIA_COLS,
                  "order": "created_at.desc", "limit": "500"}
        if status:
            if status not in jd.MEDIA_STATUSES or status == "deleted":
                raise PortalError("invalid_status", 400)
            params["processing_status"] = f"eq.{status}"
        items = self.db.select("marketing_media", params) or []
        ders = self.db.select("marketing_media_derivatives", {"tenant_id": f"eq.{c.tenant_id}", "status": "neq.deleted",
                                                              "select": DER_COLS, "limit": "2000"}) or []
        for m in items:
            m["retention"] = rt.info(m, c.now)
            m["privacy_class"] = pv.original_class(m)
            m["usable"] = pv.usable_media(m) is None and m["privacy_class"] != "restricted"
            m["derivatives"] = [x for x in ders if x["media_id"] == m["id"]]
        st, used = c.settings, self._used_bytes(c)
        state = jd.library_state(st["library_storage_limit_bytes"], used)
        return {"items": items, "storage": {"used_bytes": used, "limit_bytes": st["library_storage_limit_bytes"],
                                            "max_upload_bytes": st["max_upload_bytes"], "state": state},
                "warnings": list(pv.WARNINGS), "enabled": st["marketing_enabled"],
                "can_upload": bool(st["marketing_enabled"]) and state in ("enabled", "unlimited"),
                "generation_enabled": jd.generation_enabled(st), "malware_scanner": self.scanner.name,
                "retention": {"max_days": rt.plan_max(st), "choices": rt.allowed_choices(st)},
                "anonymization": "simulated"}

    def content(self, jwt: str, tenant_id: str, media_id: str, derivative_id: str = "",
                range_header: Optional[str] = None, head: bool = False) -> Dict[str, Any]:
        """Vista previa entregada por el backend (mismo origen), con HEAD y Range para poder avanzar y
        retroceder en el vídeo. CADA petición (también HEAD y cada Range) vuelve a validar sesión, rol,
        módulo, tenant, estado del archivo, que no esté eliminado y que la ruta sea de este tenant.
        Devuelve {status, headers, chunks}; chunks es None en HEAD y en 416. Nunca carga el archivo entero."""
        c = self.ctx(jwt, tenant_id)
        m = self._media(c, media_id)
        if (m.get("validation_status") != "passed" or m["processing_status"] in ("rejected", "deleted")
                or m.get("retention_status") in rt.PURGE_STATES):
            raise PortalError("not_found", 404)                    # pendiente de purga o purgado: sin acceso
        path, mime, size = m["storage_path"], m["mime_type"], m.get("byte_size")
        if derivative_id:
            der = self._one(c, "marketing_media_derivatives", derivative_id, DER_COLS + ",byte_size")
            if der["media_id"] != m["id"] or der["status"] == "deleted" or not der.get("storage_path"):
                raise PortalError("not_found", 404)
            path, mime, size = der["storage_path"], der.get("mime_type"), der.get("byte_size")
        path = self._path(c, path)
        if mime not in mf.MIME_TO_EXT or not size or int(size) <= 0:
            raise PortalError("not_found", 404)
        size = int(size)
        base = {"Accept-Ranges": "bytes", "Content-Type": mime}
        try:
            rng = mf.parse_range(range_header, size)
        except mf.RangeNotSatisfiable:
            return {"status": 416, "headers": {**base, "Content-Range": f"bytes */{size}", "Content-Length": "0"},
                    "chunks": None}
        start, end = rng if rng else (0, size - 1)
        headers = {**base, "Content-Length": str(end - start + 1)}
        if rng:
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        status = 206 if rng else 200
        if head:
            return {"status": status, "headers": headers, "chunks": None}
        try:
            chunks = self.db.storage_stream(BUCKET, path, byte_range=(start, end) if rng else None)
        except Exception as e:
            logger.error(f"MARKETING_PREVIEW_ERROR {type(e).__name__}")
            raise PortalError("storage_unavailable", 503)
        return {"status": status, "headers": headers, "chunks": chunks}

    # ------------------------------------------------------------------ subir
    def upload_limit(self, jwt: str, tenant_id: str) -> int:
        """Límite en bytes para ESTA subida (se aplica antes de leer el cuerpo). 0 = no se puede subir."""
        c = self.ctx(jwt, tenant_id)
        self._writable(c)
        limit = min(int(c.settings["max_upload_bytes"] or 0), mf.HARD_MAX_BYTES)
        storage = c.settings["library_storage_limit_bytes"]
        if storage is not None:
            if int(storage) <= 0:
                raise PortalError("library_disabled", 403)          # el operador aún no la habilitó
            limit = min(limit, max(int(storage) - self._used_bytes(c), 0))
            if limit <= 0:
                raise PortalError("limit_library_storage", 409)
        return limit

    @_guard
    def upload(self, jwt: str, tenant_id: str, filename: str, declared_mime: str, data: bytes) -> Dict[str, Any]:
        c = self.ctx(jwt, tenant_id)
        self._writable(c)
        limit = self.upload_limit(jwt, tenant_id)
        try:
            info = mf.validate(data, filename, declared_mime, limit)
        except mf.MediaFileError as e:
            raise PortalError(e.code, e.status)
        dup = self.db.select("marketing_media", {"tenant_id": f"eq.{c.tenant_id}", "checksum": f"eq.{info['checksum']}",
                                                 "processing_status": "neq.deleted", "select": MEDIA_COLS, "limit": "1"})
        if dup:
            return {**dup[0], "duplicate": True}          # mismo archivo: no se sube dos veces
        media_id = str(uuid.uuid4())
        path = self._path(c, mf.original_path(c.tenant_id, media_id, info["safe_filename"]))
        scan = self.scanner.scan(data)
        if scan == "clean" and self.scanner.name == "unavailable":
            scan = "unavailable"                          # imposible declarar limpio sin escáner
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
            "uploaded_by": c.user["id"], "validation_status": "passed", "malware_scan_status": scan,
            "contains_people": None, "contains_minors": None, "people_policy": "exclude",
            "consent_status": "unknown", "processing_status": "uploaded", "metadata": {},
            "retention_days": rt.default_days(c.settings), "retention_status": "active",
            "expires_at": (c.now + timedelta(days=rt.default_days(c.settings))).isoformat()})
        self._media_event(c, media_id, "upload", {"mime_type": info["mime_type"], "byte_size": info["byte_size"],
                                                  "malware_scan_status": scan})
        # Formato validado: queda listo, pero excluido hasta que owner/manager clasifique personas y menores.
        self.db.update("marketing_media", {"id": f"eq.{media_id}", "tenant_id": f"eq.{c.tenant_id}"},
                       {"processing_status": "ready"})
        row["processing_status"] = "ready"
        return {**row, "privacy_class": pv.original_class(row)}

    # ------------------------------------------------------------------ clasificar / consentimiento
    @_guard
    def classify(self, jwt: str, tenant_id: str, media_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        c = self.ctx(jwt, tenant_id)
        self._writable(c)
        m = self._media(c, media_id)
        if m["processing_status"] in ("archived", "rejected") or m.get("retention_status") in rt.PURGE_STATES:
            raise PortalError("not_editable", 409)
        tri = lambda v: v if v in (True, False) else None   # noqa: E731
        out = pv.classify(tri(body.get("contains_people")), str(body.get("people_policy") or "exclude"),
                          str(body.get("consent_status") or "unknown"), tri(body.get("contains_minors")))
        meta = dict(m.get("metadata") or {})
        note = str(body.get("consent_note") or "")[:300]
        if note:
            meta["consent_note"] = note
        values = {k: out[k] for k in ("contains_people", "contains_minors", "people_policy", "consent_status")}
        values["metadata"] = meta
        if out["consent_status"] != m.get("consent_status"):
            values.update({"consent_updated_by": c.user["id"], "consent_updated_at": c.now.isoformat()})
        rows = self.db.update("marketing_media", {"id": f"eq.{m['id']}", "tenant_id": f"eq.{c.tenant_id}"}, values)
        revoked = out["consent_status"] == "revoked" and m.get("consent_status") != "revoked"
        self._media_event(c, m["id"], "consent_revoke" if revoked else "classify",
                          {k: out[k] for k in ("contains_people", "contains_minors", "people_policy", "consent_status")})
        return {**(rows[0] if rows else m), "privacy_class": out["privacy_class"]}

    @_guard
    def revoke_consent(self, jwt: str, tenant_id: str, media_id: str) -> Dict[str, Any]:
        """Retirar el consentimiento: el archivo queda excluido al instante (también para trabajos
        pendientes, que se vuelven a comprobar al aprobar y al procesar)."""
        c = self.ctx(jwt, tenant_id)
        self._writable(c)
        m = self._media(c, media_id)
        out = self.classify(jwt, tenant_id, m["id"], {"contains_people": m.get("contains_people"),
                                                      "contains_minors": m.get("contains_minors"),
                                                      "people_policy": "exclude", "consent_status": "revoked"})
        # Cancelar trabajos pendientes, bloquear resultados que lo usen y pedir la purga prioritaria.
        # (Si algún día hay trabajos externos, aquí se pediría también su cancelación al proveedor.)
        jobs = rt.cancel_jobs_for_media(self.db, c.tenant_id, m["id"], "consent_revoked", c.now, (c.user["id"], c.role))
        self._media_event(c, m["id"], "jobs_cancelled", {"reason": "consent_revoked", "jobs": len(jobs)})
        self.db.update("marketing_media", {"id": f"eq.{m['id']}", "tenant_id": f"eq.{c.tenant_id}",
                                           "processing_status": "neq.deleted"},
                       {"retention_status": "purge_pending", "purge_reason": "consent_revoked",
                        "purge_requested_at": c.now.isoformat()})
        self._media_event(c, m["id"], "purge_requested", {"reason": "consent_revoked"})
        return {**out, "retention_status": "purge_pending", "cancelled_jobs": jobs}

    @_guard
    def set_archived(self, jwt: str, tenant_id: str, media_id: str, archived: bool) -> Dict[str, Any]:
        c = self.ctx(jwt, tenant_id)
        self._writable(c)
        m = self._media(c, media_id)
        target = "archived" if archived else "ready"
        jd.check_media_transition(m["processing_status"], target)
        rows = self.db.update("marketing_media", {"id": f"eq.{m['id']}", "tenant_id": f"eq.{c.tenant_id}",
                                                  "processing_status": f"eq.{m['processing_status']}"},
                              {"processing_status": target})
        if not rows:
            raise PortalError("conflict", 409)
        self._media_event(c, m["id"], "archive" if archived else "restore")
        return rows[0]

    # ------------------------------------------------------------------ borrado controlado
    @_guard
    def delete(self, jwt: str, tenant_id: str, media_id: str, reason: str, confirm: bool) -> Dict[str, Any]:
        c = self.ctx(jwt, tenant_id)
        self._writable(c)
        if confirm is not True:
            raise PortalError("confirmation_required", 400)
        m = self._media(c, media_id)
        jd.check_media_transition(m["processing_status"], "deleted")
        uses = self.db.select("marketing_generation_inputs", {"tenant_id": f"eq.{c.tenant_id}", "media_id": f"eq.{m['id']}",
                                                              "select": "job_id", "limit": "1000"}) or []
        if uses:
            ids = ",".join(sorted({u["job_id"] for u in uses}))
            if self.db.select("marketing_generation_jobs", {"tenant_id": f"eq.{c.tenant_id}", "id": f"in.({ids})",
                                                            "status": f"in.({','.join(ACTIVE_JOBS)})",
                                                            "select": "id", "limit": "1"}):
                raise PortalError("media_in_use", 409)
        reason = str(reason or "").strip()[:300] or None
        self._media_event(c, m["id"], "delete", {"reason": reason, "checksum": m["checksum"], "byte_size": m["byte_size"]})
        if reason:
            self.db.update("marketing_media", {"id": f"eq.{m['id']}", "tenant_id": f"eq.{c.tenant_id}"}, {"delete_reason": reason})
        # Misma purga que el worker: bloquea el acceso, borra original y derivados, conserva auditoría mínima.
        full = self._one(c, "marketing_media", m["id"])
        result = rt.RetentionRunner(self.db, now=c.now).purge(full, "user_deleted", actor=(c.user["id"], c.role))
        return {"id": m["id"], "retention_status": "purged" if result == "purged" else "purge_failed",
                "processing_status": "deleted" if result == "purged" else m["processing_status"],
                "storage_removed": result == "purged"}

    # ------------------------------------------------------------------ retención
    @_guard
    def set_retention(self, jwt: str, tenant_id: str, media_id: str, days: Any) -> Dict[str, Any]:
        """Owner/manager eligen 7/30/60/90 días desde la subida, nunca por encima del máximo del plan.
        Sirve para acortar o para extender dentro del máximo; nunca se extiende automáticamente."""
        c = self.ctx(jwt, tenant_id)
        self._writable(c)
        m = self._media(c, media_id)
        if m.get("retention_status") in rt.PURGE_STATES:
            raise PortalError("media_pending_deletion", 409)
        d = rt.check_choice(c.settings, days)
        exp = rt._dt(m["created_at"]) + timedelta(days=d)
        if exp <= c.now:
            raise PortalError("retention_too_short", 409)
        status = m["retention_status"]
        if status in ("active", "expiring"):
            status = "expiring" if exp - c.now <= timedelta(days=rt.WARN_DAYS) else "active"
        rows = self.db.update("marketing_media", {"id": f"eq.{m['id']}", "tenant_id": f"eq.{c.tenant_id}",
                                                  "processing_status": "neq.deleted"},
                              {"retention_days": d, "expires_at": exp.isoformat(), "retention_status": status})
        self._media_event(c, m["id"], "retention_change", {"days": d, "expires_at": exp.isoformat()})
        row = rows[0] if rows else {**m, "retention_days": d, "expires_at": exp.isoformat()}
        return {**row, "retention": rt.info(row, c.now)}

    # ------------------------------------------------------------------ anonimizar (simulado)
    @_guard
    def anonymize(self, jwt: str, tenant_id: str, media_id: str, method: str) -> Dict[str, Any]:
        c = self.ctx(jwt, tenant_id)
        self._writable(c)
        m = self._media(c, media_id)
        if m["processing_status"] != "ready":
            raise PortalError("media_not_ready", 409)
        if m.get("people_policy") != "anonymize":
            raise PortalError("policy_not_anonymize", 409)
        # Detección local sin identidad. En esta fase es simulada (o no existe): no se descarga el
        # original, no se genera ningún archivo y el derivado NUNCA se considera anonimizado.
        plan = pv.anonymization_plan(method, self.detector.detect(b"", m["mime_type"]), simulated=True)
        row = self.db.insert("marketing_media_derivatives", {
            "id": str(uuid.uuid4()), "tenant_id": c.tenant_id, "media_id": m["id"], "kind": "anonymized",
            "method": plan["method"], "storage_path": None, "status": plan["status"], "is_mock": plan["is_mock"],
            "detection_confidence": plan["detection_confidence"], "review_required": True, "created_by": c.user["id"],
            "metadata": {"simulated": True, "low_confidence": plan["low_confidence"], "stop_reason": plan["stop_reason"],
                         "guarantee": plan["guarantee"], "detector": getattr(self.detector, "name", "unknown")}})
        self._media_event(c, m["id"], "anonymize_request", {"method": plan["method"], "status": plan["status"]},
                          derivative_id=row["id"])
        return {**row, "warnings": plan["warnings"]}

    @_guard
    def review_derivative(self, jwt: str, tenant_id: str, derivative_id: str, approve: bool,
                          confirm_reviewed: bool = False) -> Dict[str, Any]:
        """Revisión humana de un derivado REAL. Un derivado simulado nunca se aprueba."""
        c = self.ctx(jwt, tenant_id)
        self._writable(c)
        der = self._one(c, "marketing_media_derivatives", derivative_id, DER_COLS)
        if der.get("is_mock") or der["status"] == "mock_only":
            if not approve:
                rows = self.db.update("marketing_media_derivatives", {"id": f"eq.{der['id']}",
                                      "tenant_id": f"eq.{c.tenant_id}"}, {"status": "rejected"})
                return rows[0] if rows else der
            raise PortalError("mock_derivative", 409)
        if der["status"] != "needs_review":
            raise PortalError("derivative_not_processed", 409)
        if approve and (not confirm_reviewed or not der.get("storage_path")):
            raise PortalError("human_review_required", 409)
        rows = self.db.update("marketing_media_derivatives", {"id": f"eq.{der['id']}", "tenant_id": f"eq.{c.tenant_id}",
                                                              "status": "eq.needs_review"},
                              {"status": "ready" if approve else "rejected", "reviewed_by": c.user["id"],
                               "reviewed_at": c.now.isoformat()})
        if not rows:
            raise PortalError("conflict", 409)
        return rows[0]


def media_for_job(svc: StudioBase, c, media_ids, derivative_ids=None) -> Dict[str, Any]:
    """Resuelve las entradas de un trabajo SOLO dentro del tenant y calcula su clase de privacidad.
    Excluido, menores, consentimiento retirado, sin validar o eliminado → no se puede usar.
    anonymize → solo con un derivado real, revisado y no simulado (hoy no existe ninguno)."""
    ids = [uid(x, "invalid_media", 400) for x in (media_ids or [])][:20]
    wanted = {uid(x, "invalid_media", 400) for x in (derivative_ids or [])}
    out, kinds, clean = [], set(), True
    for mid in ids:
        m = svc._one(c, "marketing_media", mid, MEDIA_COLS)
        if m["processing_status"] == "deleted":
            raise PortalError("not_found", 404)
        why = pv.usable_media(m)
        if why:
            raise PortalError(why, 409)
        if rt._dt(m["expires_at"]) <= c.now:
            raise PortalError("media_expired", 409)
        der = None
        if m.get("people_policy") == "anonymize":
            cands = [x for x in (svc.db.select("marketing_media_derivatives", {
                "tenant_id": f"eq.{c.tenant_id}", "media_id": f"eq.{m['id']}", "kind": "eq.anonymized",
                "status": "eq.ready", "select": DER_COLS, "limit": "20"}) or []) if not x.get("is_mock")]
            cands = [x for x in cands if x["id"] in wanted] or cands[:1]
            if not cands:
                raise PortalError("anonymization_required", 409)
            der = cands[0]
        cls = pv.input_class(m, der)
        if cls == "restricted":
            raise PortalError("privacy_blocked", 409)
        clean = clean and pv.malware_clean(m)
        out.append({"media_id": m["id"], "derivative_id": der["id"] if der else None, "privacy_class": cls,
                    "media_type": m["media_type"]})
        kinds.add(m["media_type"])
    order = ("consented_people", "anonymized_people", "business_media_no_people", "synthetic_only")
    worst = next((k for k in order if any(i["privacy_class"] == k for i in out)), "synthetic_only")
    return {"inputs": out, "privacy_class": worst, "kinds": sorted(kinds), "malware_clean": clean and bool(out)}
