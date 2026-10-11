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
from contextlib import contextmanager
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
        # Cancelación externa por un camino explícito de la base (aunque un worker lo esté procesando).
        res = db.rpc("marketing_cancel_generation", {"p_tenant": tenant_id, "p_job": j["id"], "p_to": to,
                                                     "p_error_code": code, "p_actor": actor[0] if actor else None,
                                                     "p_role": actor[1] if actor else "system"}) or {}
        if res.get("status") == to:
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

    def _acquire(self, timeout: Optional[float] = None) -> bool:
        return bool(self.db.rpc("marketing_acquire_runtime_lease", {"p_name": LEASE_NAME, "p_holder": self.holder,
                                                                    "p_ttl_seconds": LEASE_TTL_SECONDS}, timeout=timeout))

    def _revalidate(self, point: str) -> None:
        """Revalida el lease CONTRA LA BASE antes/después de cada paso destructivo. Sin lease (borrado pedido
        por owner/manager, fuera del purgador) no aplica: ese cierre tiene su propio camino en la base."""
        if self._keeper is None:
            return
        if self._keeper.lost or not self._acquire(min(self.renew_seconds, 10.0)):
            self._keeper.stop()
            raise LeaseLost(point)

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
    @contextmanager
    def _leased(self):
        """Lease del purgador durante todo el bloque, renovado en segundo plano. Al salir solo se libera si
        sigue siendo nuestro: la base borra la fila únicamente cuando holder coincide (nunca el lease nuevo)."""
        with LeaseKeeper(self._acquire, self.renew_seconds, name=LEASE_NAME,
                         renew_timeout=min(self.renew_seconds, 10.0)) as keeper:
            self._keeper = keeper
            try:
                yield keeper
            finally:
                self._keeper = None
                keeper.stop()
        self.db.rpc("marketing_release_runtime_lease", {"p_name": LEASE_NAME, "p_holder": self.holder},
                    timeout=min(self.renew_seconds, 10.0))

    def run(self, limit: int = 200) -> Dict[str, Any]:
        """Un lote. Solo un purgador a la vez (lease en la base); si otro lo tiene, no hace nada."""
        if not self._acquire(min(self.renew_seconds, 10.0)):      # renovar antes de cada lote
            return {"locked": True}
        with self._leased():
            return self._run(limit)

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
                self._revalidate("batch:before_media")
                out[self.process(m)] += 1
            out["derivatives"] = self._purge_derivatives(limit)
            out["outputs"] = self._purge_outputs(limit)
            # Trabajos atascados (lease vencido sin reintentos, o >24 h sin terminar) y reservas abandonadas
            # (subidas 15 min, trabajos 24 h): se cierran UNA vez, de forma atómica, en la base.
            self._revalidate("batch:before_timeouts")
            out["timed_out"] = int(self.db.rpc("marketing_timeout_jobs", {"p_max_attempts": JOB_MAX_ATTEMPTS}) or 0)
            self._revalidate("batch:before_expire")
            out["reservations_expired"] = ms.expire_abandoned(self.db)
        except LeaseLost as e:
            out.update({"lease_lost": True, "stopped_at": str(e)})   # otro purgador lo tiene: parar aquí
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
        """Purga un archivo. Fuera de un lote, sin actor (owner/manager), toma primero el lease del purgador:
        ninguna purga del sistema corre sin él. Si el lease se pierde en cualquier punto → LeaseLost: se para
        sin cerrar nada; el archivo sigue purge_pending y el purgador vigente lo retoma (borrados idempotentes)."""
        if self._keeper is None and actor is None:
            if not self._acquire(min(self.renew_seconds, 10.0)):
                return "locked"
            with self._leased():
                return self._purge(m, reason, None)
        return self._purge(m, reason, actor)

    def _purge(self, m: Dict[str, Any], reason: str, actor) -> str:
        if m.get("retention_status") == "purged" or m.get("processing_status") == "deleted":
            return "purged"
        t, mid = m["tenant_id"], m["id"]
        self._revalidate("purge:start")
        if m.get("retention_status") != "purge_pending":
            self._upd(m, {"retention_status": "purge_pending", "purge_reason": reason,
                          "purge_requested_at": m.get("purge_requested_at") or self.now.isoformat()})
            self._revalidate("purge:after_pending")
        # Ningún trabajo sigue usando el archivo y ninguna reserva suya queda abierta (liberación idempotente).
        cancel_jobs_for_media(self.db, t, mid, "media_pending_deletion", self.now, actor)
        self._revalidate("purge:after_cancel")
        ms.release(self.db, t, f"upload:{mid}", consumed=False)
        self._revalidate("purge:after_release")
        # Invalidar cualquier acceso: tokens de reproducción revocados antes de borrar nada.
        self.db.update("marketing_stream_tokens", {"tenant_id": f"eq.{t}", "media_id": f"eq.{mid}",
                                                   "revoked_at": "is.null"}, {"revoked_at": self.now.isoformat()})
        ders = self.db.select("marketing_media_derivatives", {"tenant_id": f"eq.{t}", "media_id": f"eq.{mid}",
                                                              "select": "id,storage_path,status", "limit": "500"}) or []
        try:
            self._revalidate("purge:before_original")
            self._remove(t, mid, m["storage_path"])
            self._revalidate("purge:after_original")
            for d in ders:
                self._revalidate("purge:before_derivative")
                self._remove(t, mid, d.get("storage_path"))
                self._revalidate("purge:after_derivative")
        except LeaseLost:
            raise                                                  # se para aquí: lo retoma el purgador vigente
        except Exception as e:
            code = "invalid_path" if isinstance(e, PermissionError) else "storage_error"
            logger.error(f"MARKETING_PURGE_ERROR {code}")          # sin rutas ni detalles
            self._revalidate("purge:before_failed")
            self._upd(m, {"retention_status": "purge_failed", "last_purge_error": code,
                          "purge_attempts": int(m.get("purge_attempts") or 0) + 1})
            self._event(m, "purge_failed", {"error": code}, actor)
            return "failed"
        # Cierre en la base, en una transacción y condicional: exige el lease vigente de ESTE purgador (o un
        # owner/manager), solo cierra si sigue purge_pending y escribe un único evento 'purged'.
        self._revalidate("purge:before_close")
        res = self.db.rpc("marketing_close_purge", {
            "p_tenant": t, "p_media": mid, "p_holder": self.holder if self._keeper is not None else None,
            "p_reason": m.get("purge_reason") or reason, "p_actor": actor[0] if actor else None,
            "p_role": actor[1] if actor else "system"}) or {}
        status = res.get("status")
        if status == "lease_lost":
            raise LeaseLost("purge:close")
        if status != "closed":
            return "skipped"                                       # ya cerrado por otro: nada que duplicar
        m.update({"processing_status": "deleted", "retention_status": "purged"})
        return "purged"

    def _purge_derivatives(self, limit: int) -> int:
        n = 0
        for d in self.db.select("marketing_media_derivatives", {"status": "neq.deleted", "expires_at": f"lte.{self.now.isoformat()}",
                                                                "select": "*", "limit": str(limit)}) or []:
            self._revalidate("derivative:before")
            try:
                self._remove(d["tenant_id"], d["media_id"], d.get("storage_path"))
            except Exception:
                continue                                          # se reintenta en la próxima pasada
            self._revalidate("derivative:after")
            self.db.update("marketing_media_derivatives", {"id": f"eq.{d['id']}", "tenant_id": f"eq.{d['tenant_id']}"},
                           {"status": "deleted", "purged_at": self.now.isoformat(), "metadata": {}})
            n += 1
        return n

    def _purge_outputs(self, limit: int) -> int:
        n = 0
        for o in self.db.select("marketing_generation_outputs", {"expires_at": f"lte.{self.now.isoformat()}", "purged_at": "is.null",
                                                                 "select": "id,tenant_id,storage_path", "limit": str(limit)}) or []:
            self._revalidate("output:before")
            if o.get("storage_path") and not mf.path_belongs_to(o["storage_path"], o["tenant_id"]):
                continue
            try:
                if o.get("storage_path"):
                    self.db.storage_remove(BUCKET, o["storage_path"])
            except Exception:
                continue
            self._revalidate("output:after")
            self.db.update("marketing_generation_outputs", {"id": f"eq.{o['id']}", "tenant_id": f"eq.{o['tenant_id']}"},
                           {"storage_path": None, "metadata": {}, "purged_at": self.now.isoformat()})
            n += 1
        return n
