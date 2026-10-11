"""
AITA Marketing (Fase 2) — retención y purga de la Biblioteca.

Política (ningún archivo se guarda indefinidamente):
  * archivo sin usar: 30 días (o menos si el plan lo fija) — owner/manager eligen 7/30/60/90 días,
    nunca por encima de max_retention_days del plan (lo fija el operador; máximo absoluto 90);
  * derivados simulados / temporales: 7 días; resultados de trabajos fallidos o cancelados: 7 días;
  * usado por un trabajo activo o una publicación programada: protegido como MÁXIMO 14 días desde que
    empieza la protección; después el trabajo se cancela (idempotente) y se solicita la purga;
  * publicación confirmada: expires_at = mínimo(published_at + 30 días, subida + 90 días);
    máximo absoluto del piloto: 90 días desde la subida; nunca hay retención permanente;
  * consentimiento retirado: purga prioritaria, cancelación de trabajos pendientes y bloqueo de reutilización;
  * aviso 7 días antes; nunca se extiende automáticamente.

Purga: idempotente, aislada por tenant, reintentable, segura si el objeto ya no existe, auditada y
sin borrar nunca una ruta que no pertenezca al tenant y al archivo. Se conservan solo metadatos
mínimos (id, tenant, hash, tipo, tamaño, quién subió, fechas, motivo).

RetentionRunner.run() es para un worker / tarea programada. En este PR NO está programado en ningún sitio.
"""
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from services import marketing_media_files as mf
from services import marketing_storage as ms
from services.marketing_lease import LeaseKeeper, LeaseLost, validate as validate_lease
from services.marketing_domain import DomainError

logger = logging.getLogger(__name__)
CHOICES = (7, 30, 60, 90)
HARD_MAX_DAYS = 90
DEFAULT_DAYS = 30
MOCK_DAYS = FAILED_JOB_DAYS = 7
PUBLISHED_DAYS = 30
WARN_DAYS = 7
PROTECTION_GRACE_DAYS = 14
MAX_ATTEMPTS = 5
JOB_TIMEOUT_HOURS = 24                      # igual que la caducidad de la reserva de espacio de un trabajo
JOB_MAX_ATTEMPTS = 3                        # igual que services/marketing_worker.MAX_ATTEMPTS
LEASE_NAME = "retention_runner"
LEASE_TTL_SECONDS = 900
ACTIVE_JOBS = ("draft", "awaiting_generation_approval", "queued", "processing")
PURGE_STATES = ("purge_pending", "purged", "purge_failed")
BUCKET = "marketing-assets"


def _dt(v: Any) -> datetime:
    d = v if isinstance(v, datetime) else datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def is_expired(media: Dict[str, Any], now: datetime) -> bool:
    """Vencido = inaccesible, aunque el purgador aún no haya corrido. Sin fecha → vencido (falla cerrado)."""
    return not media.get("expires_at") or _dt(media["expires_at"]) <= now


def plan_max(settings: Dict[str, Any]) -> int:
    try:
        v = int(settings.get("max_retention_days") or DEFAULT_DAYS)
    except (TypeError, ValueError):
        v = DEFAULT_DAYS
    return min(max(v, 7), HARD_MAX_DAYS)


def allowed_choices(settings: Dict[str, Any]) -> List[int]:
    return [d for d in CHOICES if d <= plan_max(settings)]


def default_days(settings: Dict[str, Any]) -> int:
    return max(d for d in allowed_choices(settings) if d <= DEFAULT_DAYS)


def check_choice(settings: Dict[str, Any], days: Any) -> int:
    try:
        d = int(days)
    except (TypeError, ValueError):
        raise DomainError("invalid_retention", 400)
    if d not in CHOICES:
        raise DomainError("invalid_retention", 400)          # nunca "permanente"
    if d > plan_max(settings):
        raise DomainError("retention_exceeds_plan", 409)
    return d


def info(media: Dict[str, Any], now: datetime) -> Dict[str, Any]:
    exp = _dt(media["expires_at"])
    left = (exp - now).total_seconds() / 86400
    return {"expires_at": exp.isoformat(), "days_left": max(int(left + 0.999), 0), "warning": 0 <= left <= WARN_DAYS,
            "retention_days": media.get("retention_days"), "retention_status": media.get("retention_status")}


def cancel_jobs_for_media(db, tenant_id: str, media_id: str, code: str, now: datetime, actor=None) -> List[str]:
    """Cancela (o marca como fallidos) los trabajos activos que usan el archivo. Devuelve sus ids."""
    uses = db.select("marketing_generation_inputs", {"tenant_id": f"eq.{tenant_id}", "media_id": f"eq.{media_id}",
                                                     "select": "job_id", "limit": "1000"}) or []
    ids = sorted({u["job_id"] for u in uses})
    done = []
    for j in (db.select("marketing_generation_jobs", {"tenant_id": f"eq.{tenant_id}", "id": f"in.({','.join(ids)})",
                                                      "status": f"in.({','.join(ACTIVE_JOBS)})",
                                                      "select": "id,status", "limit": "1000"}) or []) if ids else []:
        to = "failed" if j["status"] == "processing" else "cancelled"
        if db.update("marketing_generation_jobs", {"id": f"eq.{j['id']}", "tenant_id": f"eq.{tenant_id}",
                                                   "status": f"eq.{j['status']}"},
                     {"status": to, "error_code": code, "completed_at": now.isoformat()}):
            db.insert("marketing_generation_job_events", {
                "tenant_id": tenant_id, "job_id": j["id"], "action": "fail" if to == "failed" else "cancel",
                "from_status": j["status"], "to_status": to, "detail": {"error_code": code},
                "actor_id": actor[0] if actor else None, "actor_role": actor[1] if actor else "system"})
            done.append(j["id"])
    # Ningún resultado que contenga ese material puede reutilizarse.
    for jid in ids:
        db.update("marketing_generation_outputs", {"tenant_id": f"eq.{tenant_id}", "job_id": f"eq.{jid}"},
                  {"review_status": "rejected", "metadata": {"blocked": code},
                   "expires_at": (now + timedelta(days=FAILED_JOB_DAYS)).isoformat()})
    return done


class RetentionRunner:
    """Purga simulable con reloj controlado. Usa el service role: cada operación filtra por tenant_id."""

    def __init__(self, db, now: Optional[datetime] = None, holder: Optional[str] = None,
                 renew_seconds: Optional[float] = None):
        self.db = db
        self.now = now or datetime.now(timezone.utc)
        self.holder = holder or str(uuid.uuid4())
        self.renew_seconds = validate_lease(LEASE_TTL_SECONDS, renew_seconds)   # < TTL/3
        self._keeper: Optional[LeaseKeeper] = None

    def _acquire(self) -> bool:
        return bool(self.db.rpc("marketing_acquire_runtime_lease", {"p_name": LEASE_NAME, "p_holder": self.holder,
                                                                    "p_ttl_seconds": LEASE_TTL_SECONDS}))

    def _ensure_lease(self) -> None:
        """Antes de cada paso destructivo: si el lease se perdió, no se procesa nada más."""
        if self._keeper is not None and self._keeper.lost:
            raise LeaseLost(LEASE_NAME)

    def _event(self, m, action, detail=None, actor=None):
        self.db.insert("marketing_media_events", {"tenant_id": m["tenant_id"], "media_id": m["id"], "action": action,
                                                  "actor_id": actor[0] if actor else None,
                                                  "actor_role": actor[1] if actor else "system", "detail": detail or {}})

    def _upd(self, m, values):
        return self.db.update("marketing_media", {"id": f"eq.{m['id']}", "tenant_id": f"eq.{m['tenant_id']}",
                                                  "processing_status": "neq.deleted"}, values)

    def _settings(self, tenant_id):
        return (self.db.select("marketing_settings", {"tenant_id": f"eq.{tenant_id}", "select": "*", "limit": "1"})
                or [{}])[0]

    # ------------------------------------------------------------------ protección
    def _jobs_using(self, m) -> List[Dict[str, Any]]:
        uses = self.db.select("marketing_generation_inputs", {"tenant_id": f"eq.{m['tenant_id']}", "media_id": f"eq.{m['id']}",
                                                              "select": "job_id", "limit": "1000"}) or []
        ids = sorted({u["job_id"] for u in uses})
        return (self.db.select("marketing_generation_jobs", {"tenant_id": f"eq.{m['tenant_id']}", "id": f"in.({','.join(ids)})",
                                                             "select": "id,status,content_id", "limit": "1000"}) or []) if ids else []

    def _protection(self, m, jobs) -> Optional[str]:
        if any(j["status"] in ACTIVE_JOBS for j in jobs):
            return "active_job"
        cids = sorted({j["content_id"] for j in jobs if j.get("content_id")})
        if cids and self.db.select("marketing_content", {"tenant_id": f"eq.{m['tenant_id']}", "id": f"in.({','.join(cids)})",
                                                         "status": "in.(scheduled,publishing)", "select": "id", "limit": "1"}):
            return "scheduled_publication"
        return None

    def _published_at(self, m, jobs) -> Optional[datetime]:
        cids = sorted({j["content_id"] for j in jobs if j.get("content_id")})
        pubs = self.db.select("marketing_publications", {"tenant_id": f"eq.{m['tenant_id']}", "content_id": f"in.({','.join(cids)})",
                                                         "status": "eq.published", "select": "published_at",
                                                         "limit": "100"}) if cids else []
        dates = [_dt(p["published_at"]) for p in (pubs or []) if p.get("published_at")]
        return max(dates) if dates else None

    def _published_media(self, limit: int) -> List[Dict[str, Any]]:
        """Archivos usados por contenido con publicación confirmada (cada consulta, dentro de su tenant)."""
        out = []
        for p in self.db.select("marketing_publications", {"status": "eq.published", "select": "tenant_id,content_id",
                                                           "order": "published_at.desc", "limit": str(limit)}) or []:
            t = p["tenant_id"]
            jobs = self.db.select("marketing_generation_jobs", {"tenant_id": f"eq.{t}", "content_id": f"eq.{p['content_id']}",
                                                                "select": "id", "limit": "50"}) or []
            for j in jobs:
                for i in self.db.select("marketing_generation_inputs", {"tenant_id": f"eq.{t}", "job_id": f"eq.{j['id']}",
                                                                        "select": "media_id", "limit": "50"}) or []:
                    out += self.db.select("marketing_media", {"tenant_id": f"eq.{t}", "id": f"eq.{i['media_id']}",
                                                              "processing_status": "neq.deleted", "select": "*",
                                                              "limit": "1"}) or []
        return out

    # ------------------------------------------------------------------ ciclo
    def run(self, limit: int = 200) -> Dict[str, Any]:
        """Un lote. Solo un purgador a la vez (lease en la base); si otro lo tiene, no hace nada."""
        if not self._acquire():                                    # renovar antes de cada lote
            return {"locked": True}
        try:
            with LeaseKeeper(self._acquire, self.renew_seconds, name=LEASE_NAME) as keeper:   # y durante el lote
                self._keeper = keeper
                return self._run(limit)
        finally:
            self._keeper = None
            # Solo borra la fila si sigue siendo nuestra (si otro la tomó, no se toca).
            self.db.rpc("marketing_release_runtime_lease", {"p_name": LEASE_NAME, "p_holder": self.holder})

    def _run(self, limit: int) -> Dict[str, Any]:
        out: Dict[str, Any] = {"warned": 0, "protected": 0, "purged": 0, "failed": 0, "skipped": 0,
                               "derivatives": 0, "outputs": 0}
        horizon = (self.now + timedelta(days=WARN_DAYS)).isoformat()
        rows = {r["id"]: r for r in (self.db.select("marketing_media", {
            "processing_status": "neq.deleted", "retention_status": "in.(purge_pending,purge_failed)",
            "select": "*", "order": "purge_requested_at.asc", "limit": str(limit)}) or [])}
        for r in self.db.select("marketing_media", {"processing_status": "neq.deleted", "expires_at": f"lte.{horizon}",
                                                    "select": "*", "order": "expires_at.asc", "limit": str(limit)}) or []:
            rows.setdefault(r["id"], r)
        for m in self._published_media(limit):                     # publicación confirmada: recalcular vencimiento
            rows.setdefault(m["id"], m)
        try:
            for m in rows.values():
                self._ensure_lease()
                out[self.process(m)] += 1
        except LeaseLost:
            out["lease_lost"] = True                              # otro purgador lo tiene: parar aquí
            return out
        out["derivatives"] = self._purge_derivatives(limit)
        out["outputs"] = self._purge_outputs(limit)
        # Trabajos atascados (lease vencido sin reintentos, o >24 h sin terminar) y reservas abandonadas
        # (subidas 15 min, trabajos 24 h): se cierran UNA vez, de forma atómica, en la base.
        out["timed_out"] = int(self.db.rpc("marketing_timeout_jobs", {"p_max_attempts": JOB_MAX_ATTEMPTS}) or 0)
        out["reservations_expired"] = ms.expire_abandoned(self.db)
        return out

    def process(self, m: Dict[str, Any]) -> str:
        if m["retention_status"] in ("purge_pending", "purge_failed"):
            if m["retention_status"] == "purge_failed" and int(m.get("purge_attempts") or 0) >= MAX_ATTEMPTS:
                return "failed"                                   # requiere revisión del operador
            return self.purge(m, m.get("purge_reason") or "expired")
        jobs = self._jobs_using(m)
        pub = self._published_at(m, jobs)
        created = _dt(m["created_at"])
        if pub:   # publicado: expires_at = mínimo(publicación + 30 días, subida + 90 días)
            exp = min(pub + timedelta(days=PUBLISHED_DAYS), created + timedelta(days=HARD_MAX_DAYS))
            if exp.isoformat() != _dt(m["expires_at"]).isoformat() or not m.get("published_at"):
                self._upd(m, {"published_at": pub.isoformat(), "expires_at": exp.isoformat()})
                m.update({"published_at": pub.isoformat(), "expires_at": exp.isoformat()})
        effective = _dt(m["expires_at"])
        if effective > self.now:
            if m["retention_status"] == "active" and effective - self.now <= timedelta(days=WARN_DAYS):
                self._upd(m, {"retention_status": "expiring"})
                self._event(m, "expiry_warning", {"expires_at": effective.isoformat()})
                return "warned"
            return "skipped"
        why = self._protection(m, jobs)
        if why:                                    # un trabajo activo protege como MÁXIMO 14 días
            until = (_dt(m["protected_until"]) if m.get("protected_until")
                     else min(self.now + timedelta(days=PROTECTION_GRACE_DAYS),
                              created + timedelta(days=HARD_MAX_DAYS + PROTECTION_GRACE_DAYS)))
            if not m.get("protected_until"):
                self._upd(m, {"retention_status": "protected_by_workflow", "protected_until": until.isoformat()})
                self._event(m, "protected", {"reason": why, "until": until.isoformat()})
            if self.now < until:
                return "protected"
            # Límite alcanzado: cancelar (idempotente) y solicitar la purga.
            cancelled = cancel_jobs_for_media(self.db, m["tenant_id"], m["id"], "timeout", self.now)
            self._event(m, "jobs_cancelled", {"reason": "workflow_timeout", "jobs": len(cancelled)})
            self._upd(m, {"retention_status": "purge_pending", "purge_reason": "workflow_timeout",
                          "purge_requested_at": self.now.isoformat()})
            m.update({"retention_status": "purge_pending", "purge_reason": "workflow_timeout"})
            return self.purge(m, "workflow_timeout")
        return self.purge(m, "publication_done" if pub else "expired")

    # ------------------------------------------------------------------ purga
    def _remove(self, tenant_id: str, media_id: str, path: Optional[str]) -> None:
        if not path:
            return
        parts = path.split("/")
        if not mf.path_belongs_to(path, tenant_id) or parts[2] != media_id:
            raise PermissionError("invalid_path")                 # nunca se borra una ruta no validada
        try:
            self.db.storage_remove(BUCKET, path)
        except Exception as e:
            if "-> 404" in str(e) or "not found" in str(e).lower():
                return                                            # ya no existe: la purga es idempotente
            raise

    def purge(self, m: Dict[str, Any], reason: str, actor=None) -> str:
        if m.get("retention_status") == "purged" or m.get("processing_status") == "deleted":
            return "purged"
        if m.get("retention_status") != "purge_pending":
            self._upd(m, {"retention_status": "purge_pending", "purge_reason": reason,
                          "purge_requested_at": m.get("purge_requested_at") or self.now.isoformat()})
        # Ningún trabajo sigue usando el archivo y ninguna reserva suya queda abierta (liberación idempotente).
        cancel_jobs_for_media(self.db, m["tenant_id"], m["id"], "media_pending_deletion", self.now, actor)
        ms.release(self.db, m["tenant_id"], f"upload:{m['id']}", consumed=False)
        # Invalidar cualquier acceso: tokens de reproducción revocados antes de borrar nada.
        self.db.update("marketing_stream_tokens", {"tenant_id": f"eq.{m['tenant_id']}", "media_id": f"eq.{m['id']}",
                                                   "revoked_at": "is.null"}, {"revoked_at": self.now.isoformat()})
        ders = self.db.select("marketing_media_derivatives", {"tenant_id": f"eq.{m['tenant_id']}", "media_id": f"eq.{m['id']}",
                                                              "select": "id,storage_path,status", "limit": "500"}) or []
        try:
            self._ensure_lease()
            self._remove(m["tenant_id"], m["id"], m["storage_path"])
            for d in ders:
                self._remove(m["tenant_id"], m["id"], d.get("storage_path"))
        except LeaseLost:
            raise                                                  # nada borrado de este archivo: lo retoma otro
        except Exception as e:
            code = "invalid_path" if isinstance(e, PermissionError) else "storage_error"
            logger.error(f"MARKETING_PURGE_ERROR {code}")          # sin rutas ni detalles
            self._upd(m, {"retention_status": "purge_failed", "last_purge_error": code,
                          "purge_attempts": int(m.get("purge_attempts") or 0) + 1})
            self._event(m, "purge_failed", {"error": code}, actor)
            return "failed"
        for d in ders:
            if d["status"] != "deleted":
                self.db.update("marketing_media_derivatives", {"id": f"eq.{d['id']}", "tenant_id": f"eq.{m['tenant_id']}"},
                               {"status": "deleted", "purged_at": self.now.isoformat(), "metadata": {}})
        # Cierre condicional (solo si nadie lo cerró): un archivo nunca se cierra dos veces.
        if not self._upd(m, {"processing_status": "deleted", "retention_status": "purged", "purged_at": self.now.isoformat(),
                             "deleted_at": self.now.isoformat(), "deleted_by": actor[0] if actor else None,
                             "purge_reason": m.get("purge_reason") or reason, "original_filename": None, "metadata": {},
                             "storage_path": f"{m['tenant_id']}/originals/{m['id']}/purged", "last_purge_error": None}):
            return "skipped"
        self._event(m, "purged", {"reason": m.get("purge_reason") or reason}, actor)
        return "purged"

    def _purge_derivatives(self, limit: int) -> int:
        n = 0
        for d in self.db.select("marketing_media_derivatives", {"status": "neq.deleted", "expires_at": f"lte.{self.now.isoformat()}",
                                                                "select": "*", "limit": str(limit)}) or []:
            if self._keeper is not None and self._keeper.lost:
                break
            try:
                self._remove(d["tenant_id"], d["media_id"], d.get("storage_path"))
            except Exception:
                continue                                          # se reintenta en la próxima pasada
            self.db.update("marketing_media_derivatives", {"id": f"eq.{d['id']}", "tenant_id": f"eq.{d['tenant_id']}"},
                           {"status": "deleted", "purged_at": self.now.isoformat(), "metadata": {}})
            n += 1
        return n

    def _purge_outputs(self, limit: int) -> int:
        n = 0
        for o in self.db.select("marketing_generation_outputs", {"expires_at": f"lte.{self.now.isoformat()}", "purged_at": "is.null",
                                                                 "select": "id,tenant_id,storage_path", "limit": str(limit)}) or []:
            if self._keeper is not None and self._keeper.lost:
                break
            if o.get("storage_path") and not mf.path_belongs_to(o["storage_path"], o["tenant_id"]):
                continue
            try:
                if o.get("storage_path"):
                    self.db.storage_remove(BUCKET, o["storage_path"])
            except Exception:
                continue
            self.db.update("marketing_generation_outputs", {"id": f"eq.{o['id']}", "tenant_id": f"eq.{o['tenant_id']}"},
                           {"storage_path": None, "metadata": {}, "purged_at": self.now.isoformat()})
            n += 1
        return n
