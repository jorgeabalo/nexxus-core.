"""
AITA Marketing (Fase 2) — ejecución de trabajos de generación (preparada para worker / cola).

* Un trabajo solo se ejecuta si está 'queued' y aprobado. Se reclama con marketing_claim_job
  (FOR UPDATE SKIP LOCKED + lease): dos workers nunca toman el mismo. Durante TODO el trabajo un
  LeaseKeeper renueva el lease en segundo plano (como mucho cada lease/3; la base decide la hora), también
  mientras una subtarea larga está en curso. Si se pierde (o llega SIGTERM), el worker se detiene: no
  registra resultados ni costos, no toca reservas y solo borra los temporales de ESTE intento. Un lease
  vencido se reintenta (máx. MAX_ATTEMPTS, misma idempotency_key: nada se cobra dos veces) o termina en timeout.
* Antes de enviar nada se vuelven a comprobar las entradas: consentimiento retirado, menores,
  archivo excluido o eliminado → el trabajo falla con un código público y no se envía nada.
* Cada subtarea lleva su idempotency_key: un reintento nunca crea un segundo cargo.
* Nada se escribe hasta el final: costos y resultados se acumulan en memoria y se publican en UNA
  transacción (marketing_finalize_generation): dueño + lease vigente, presupuesto, tamaño real frente a la
  reserva y la cuota, costo idempotente, resultados sin duplicados, succeeded y reserva consumida. Si algo
  no cabe o no se cumple, no queda nada publicado; el fallo (marketing_fail_generation) también exige el lease.
* Vídeo, render, anonimización y proveedores reales son trabajo pesado y deben ejecutarse en un
  proceso aparte (run_pending, llamado por un worker programado). En esta fase NO hay worker
  desplegado: la petición HTTP solo puede ejecutar trabajos 100 % simulados (rápidos, sin red).
"""
import threading
import uuid
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from services import marketing_jobs_domain as jd
from services import marketing_privacy as pv
from services import marketing_retention as rt
from services.marketing_lease import LeaseKeeper, validate as validate_lease
from services.marketing_studio_base import BUCKET
from services.marketing_ai_router import RouteRequest

JOB_COLS = ("id,tenant_id,content_id,status,quality_tier,maximum_cost,estimated_cost,reserved_cost,currency,approved_at,approved_by,"
            "request_metadata,result_metadata,real_media_percent,ai_media_percent")
MEDIA_CHECK = ("id,processing_status,validation_status,contains_people,contains_minors,people_policy,consent_status,"
               "retention_status,expires_at")


OUTPUT_KINDS = ("image", "video", "subtitles", "cover", "render")


def temp_file_ok(path: Any, tenant_id: str, job_id: str, prefix: Optional[str] = None) -> bool:
    """Los temporales de un trabajo viven SOLO en {tenant}/derivatives/{job}/{archivo} (carpeta del propio
    trabajo; los ids de trabajo y de archivo nunca coinciden)."""
    parts = str(path or "").split("/")
    return (len(parts) == 4 and parts[0] == str(tenant_id) and parts[1] == "derivatives" and parts[2] == str(job_id)
            and parts[3] not in ("", ".", "..") and "/" not in parts[3] and len(parts[3]) <= 120
            and (prefix is None or parts[3].startswith(prefix)))


LEASE_SECONDS = 120
MAX_ATTEMPTS = 3


class WorkerError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def recheck_inputs(db, tenant_id: str, job_id: str, now: Optional[datetime] = None) -> Optional[str]:
    """None si todas las entradas siguen permitidas; si no, el código público del motivo."""
    rows = db.select("marketing_generation_inputs", {"tenant_id": f"eq.{tenant_id}", "job_id": f"eq.{job_id}",
                                                     "select": "media_id,derivative_id", "limit": "100"}) or []
    for r in rows:
        if r.get("derivative_id"):
            d = (db.select("marketing_media_derivatives", {"tenant_id": f"eq.{tenant_id}", "id": f"eq.{r['derivative_id']}",
                                                           "select": "status,is_mock", "limit": "1"}) or [None])[0]
            if not d or d.get("is_mock") or d.get("status") != "ready":
                return "privacy_blocked"
        if r.get("media_id"):
            m = (db.select("marketing_media", {"tenant_id": f"eq.{tenant_id}", "id": f"eq.{r['media_id']}",
                                               "select": MEDIA_CHECK, "limit": "1"}) or [None])[0]
            if not m:
                return "media_not_ready"
            why = pv.usable_media(m)
            if why:
                return why
            if rt.is_expired(m, now or datetime.now(timezone.utc)):
                return "media_expired"                    # vencido: nunca entra en un trabajo
    return None


def all_mock(est: Dict[str, Any]) -> bool:
    subs = est.get("subtasks") or []
    return bool(subs) and all(s.get("provider") == "mock" and not s.get("external") for s in subs)


class JobRunner:
    def __init__(self, db, router, adapters: Dict[str, Any], now: Optional[datetime] = None,
                 worker_id: Optional[str] = None, lease_seconds: int = LEASE_SECONDS,
                 heartbeat_seconds: Optional[float] = None):
        self.db, self.router, self.adapters, self._now = db, router, adapters, now
        self.worker_id = worker_id or str(uuid.uuid4())
        self.heartbeat_seconds = validate_lease(lease_seconds, heartbeat_seconds)   # rechaza valores inseguros
        self.lease_seconds = int(lease_seconds)
        self.stop_event = threading.Event()                    # SIGTERM: el keeper deja de renovar al instante

    @property
    def temp_prefix(self) -> str:
        """Los temporales de ESTE intento llevan el worker_id: al perder el lease solo se borran los propios."""
        return f"{self.worker_id}-"

    def claim(self, job_id: Optional[str] = None) -> Dict[str, Any]:
        """Reclamo atómico en la base: {status: claimed|empty|skipped, job?}."""
        return self.db.rpc("marketing_claim_job", {"p_worker": self.worker_id, "p_lease_seconds": int(self.lease_seconds),
                                                   "p_job": job_id, "p_max_attempts": MAX_ATTEMPTS}) or {"status": "empty"}

    def _renew(self, j, timeout: Optional[float] = None) -> bool:
        return bool(self.db.rpc("marketing_job_heartbeat", {"p_job": j["id"], "p_worker": self.worker_id,
                                                            "p_lease_seconds": int(self.lease_seconds)}, timeout=timeout))

    def _heartbeat(self, j) -> None:
        if not self._renew(j, min(self.heartbeat_seconds, 10.0)):
            raise WorkerError("lease_lost")                      # timeout u otro worker: no se escribe nada más

    def _check(self, keeper: Optional[LeaseKeeper]) -> None:
        if self.stop_event.is_set() or (keeper is not None and keeper.cancelled):
            raise WorkerError("interrupted")
        if keeper is not None and keeper.lost:
            raise WorkerError("lease_lost")

    def _c(self, tenant_id: str):
        return SimpleNamespace(tenant_id=tenant_id, now=self._now or datetime.now(timezone.utc))

    def _event(self, c, job_id, action, frm, to, detail=None):
        self.db.insert("marketing_generation_job_events", {
            "tenant_id": c.tenant_id, "job_id": job_id, "action": action, "from_status": frm, "to_status": to,
            "detail": detail or {}, "actor_id": None, "actor_role": "system"})

    def _move(self, c, job, to, action, values=None, detail=None):
        """Solo para trabajos AÚN NO reclamados (en cola). Un trabajo en proceso termina únicamente por las
        funciones de la base (finalize/fail), que exigen el lease."""
        jd.check_job_transition(job["status"], to, approved=bool(job.get("approved_at")))
        if job["status"] == "processing":
            raise WorkerError("not_runnable")
        rows = self.db.update("marketing_generation_jobs",
                              {"id": f"eq.{job['id']}", "tenant_id": f"eq.{c.tenant_id}", "status": f"eq.{job['status']}"},
                              {**(values or {}), "status": to})
        if not rows:
            raise WorkerError("already_claimed")
        self._event(c, job["id"], action, job["status"], to, detail)
        return rows[0]

    def _files_ok(self, c, j, files: List[Dict[str, Any]]) -> bool:
        try:
            return all(temp_file_ok(f.get("temp_path"), c.tenant_id, j["id"], self.temp_prefix)
                       and f.get("kind", "render") in OUTPUT_KINDS and int(f["byte_size"]) > 0 for f in files)
        except (KeyError, TypeError, ValueError):
            return False

    def _discard(self, c, j, files: List[Dict[str, Any]]) -> None:
        """Borra SOLO los temporales de este intento. Las reservas no se tocan aquí: las cierra la base una sola
        vez al terminar el trabajo; con el lease perdido, son de quien lo tenga ahora."""
        for f in files:
            if temp_file_ok(f.get("temp_path"), c.tenant_id, j["id"], self.temp_prefix):
                try:
                    self.db.storage_remove(BUCKET, f["temp_path"])
                except Exception:                                       # noqa: BLE001 — la purga lo reintenta
                    pass

    def _fail(self, c, j, code: str, usage: Optional[List[Dict[str, Any]]] = None,
              files: Optional[List[Dict[str, Any]]] = None):
        """Falla el trabajo. En cola (sin reclamar): actualización condicional. En proceso: SOLO por
        marketing_fail_generation (lease vigente y mismo worker_id), que registra los costos ya incurridos."""
        if j["status"] != "processing":
            out = self._move(c, j, "failed", "fail", {"error_code": jd.public_error(code), "completed_at": c.now.isoformat()},
                             detail={"error_code": code})
        else:
            res = self.db.rpc("marketing_fail_generation", {"p_tenant": c.tenant_id, "p_job": j["id"],
                                                            "p_worker": self.worker_id, "p_error_code": jd.public_error(code),
                                                            "p_usage": usage or []}) or {}
            if res.get("status") != "failed":
                self._discard(c, j, files or [])
                raise WorkerError("lease_lost")                  # vencido: no puede marcar failed
            out = res["job"]
        self._discard(c, j, files or [])
        return out

    def run(self, tenant_id: str, job_id: str) -> Dict[str, Any]:
        """Un trabajo concreto de ESTE tenant (petición HTTP de un trabajo simulado)."""
        j = (self.db.select("marketing_generation_jobs", {"tenant_id": f"eq.{tenant_id}", "id": f"eq.{job_id}",
                                                          "select": JOB_COLS, "limit": "1"}) or [None])[0]
        if not j or j["status"] != "queued" or not j.get("approved_at") or not j.get("approved_by"):
            raise WorkerError("not_runnable")
        why = recheck_inputs(self.db, tenant_id, j["id"], self._c(tenant_id).now)
        if why:                                                    # motivo preciso (la base solo sabe "no permitido")
            return self._fail(self._c(tenant_id), j, why)
        res = self.claim(j["id"])
        if res.get("status") != "claimed" or res["job"]["tenant_id"] != tenant_id:
            raise WorkerError("already_claimed" if res.get("status") == "empty" else "not_runnable")
        return self._execute(res["job"])

    def _execute(self, j: Dict[str, Any]) -> Dict[str, Any]:
        """Todo el trabajo con el lease renovado en segundo plano. SIGTERM (stop_event) detiene las
        renovaciones al instante aunque la subtarea siga bloqueada; el hilo se une con tiempo acotado."""
        files: List[Dict[str, Any]] = []
        with LeaseKeeper(lambda t: self._renew(j, t), self.heartbeat_seconds, name=str(j["id"]),
                         cancel=self.stop_event, renew_timeout=min(self.heartbeat_seconds, 10.0)) as keeper:
            try:
                return self._work(j, keeper, files)
            except WorkerError as e:
                if e.code in ("lease_lost", "interrupted"):     # nada más que borrar lo propio
                    self._discard(self._c(j["tenant_id"]), j, files)
                raise

    def _work(self, j: Dict[str, Any], keeper: LeaseKeeper, files: List[Dict[str, Any]]) -> Dict[str, Any]:
        tenant_id = j["tenant_id"]
        c = self._c(tenant_id)
        self._check(keeper)
        est = (j["request_metadata"] or {}).get("estimate") or {}
        st = (self.db.select("marketing_settings", {"tenant_id": f"eq.{tenant_id}", "limit": "1",
                                                    "select": "ai_generation_enabled,monthly_ai_cost_limit"}) or [{}])[0]
        if j.get("reserved_cost") is None or st.get("ai_generation_enabled") is not True or jd.ai_budget(st) <= 0:
            return self._fail(c, j, "budget_exceeded")          # sin reserva, generación cerrada o presupuesto 0: nada
        why = recheck_inputs(self.db, tenant_id, j["id"], c.now)
        if why:
            return self._fail(c, j, why)
        usage: List[Dict[str, Any]] = []                        # costos de ESTE intento (aún sin publicar)
        total, n, first = 0.0, 0, None
        for st in est.get("subtasks") or []:
            adapter = self.adapters.get(st["provider"])
            model = next((m for m in self.router.catalog.models if m.model_id == st["model_id"]), None)
            if adapter is None or model is None:
                return self._fail(c, j, "provider_unavailable", usage, files)
            if st["external"] and not pv.can_go_external(st["privacy_class"]):
                return self._fail(c, j, "privacy_blocked", usage, files)   # nunca restricted hacia fuera
            req = RouteRequest(tenant_id=tenant_id, task_type=st["task_type"], quality_tier=j["quality_tier"],
                               maximum_cost=float(j["maximum_cost"]), privacy_class=st["privacy_class"],
                               idempotency_key=st["idempotency_key"], units=float(st["units"]),
                               output_requirements={"temp_prefix": self.temp_prefix})
            self._check(keeper)
            self._heartbeat(j)                                    # sigue siendo nuestro antes de cada envío
            done = self.db.select("marketing_model_usage", {"tenant_id": f"eq.{tenant_id}",
                                                            "idempotency_key": f"eq.{req.idempotency_key}",
                                                            "select": "actual_cost,provider_job_id", "limit": "1"})
            if done:                                     # reintento: ya publicado, no se vuelve a enviar
                total += float(done[0].get("actual_cost") or 0)
                continue
            res = adapter.submit(req, model)                      # puede durar más que el lease: el keeper renueva
            files += list((res.output or {}).get("files") or [])         # temporales que dejó el proveedor
            self._check(keeper)                                   # lease perdido o SIGTERM: nada se escribe
            self._heartbeat(j)                                    # la base confirma que seguimos siendo dueños
            cost = float(res.actual_cost or 0)
            if res.status == "succeeded" or cost:
                usage.append({"provider": st["provider"], "model_id": st["model_id"], "task_type": st["task_type"],
                              "catalog_version": est.get("catalog_version", "unknown"),
                              "billing_unit": st["billing_unit"], "units": st["units"],
                              "estimated_cost": st["estimated_cost"], "actual_cost": cost, "currency": j["currency"],
                              "idempotency_key": req.idempotency_key, "provider_job_id": res.provider_job_id,
                              "status": "charged" if cost else "not_charged"})
            if res.status != "succeeded":
                return self._fail(c, j, jd.public_error(res.error_code), usage, files)
            if total + cost > min(float(j["maximum_cost"]), float(j.get("reserved_cost") or 0)):
                return self._fail(c, j, "budget_exceeded", usage, files)   # nunca más que lo aprobado y reservado
            total += cost
            n += 1
            first = first or (st["provider"], st["model_id"], res.provider_job_id)
        mock = all_mock(est)
        if not self._files_ok(c, j, files):
            return self._fail(c, j, "internal_error", usage, files)
        self._check(keeper)
        prov, model_id, pjid = first or ("mock", None, None)
        try:
            res = self.db.rpc("marketing_finalize_generation", {
                "p_tenant": tenant_id, "p_job": j["id"], "p_worker": self.worker_id, "p_usage": usage,
                "p_outputs": self._outputs(c, j, est, mock, files),
                "p_result": {"mock": mock, "scenes": est.get("plan", {}).get("scenes"), "provider_jobs": n},
                "p_selected": {"provider": prov, "model": model_id, "provider_job_id": pjid}}) or {}
        except Exception:                                         # noqa: BLE001 — red/timeout: resultado desconocido
            # La transacción se confirmó entera o no dejó nada; no se sabe cuál. No se escribe ni se borra nada
            # (los temporales podrían estar ya publicados); si no se confirmó, el lease vence y otro reintenta.
            raise WorkerError("finalize_unknown")
        if res.get("status") == "succeeded":
            return res["job"]
        reason = res.get("reason") or "internal_error"
        if reason == "lease_lost":
            raise WorkerError("lease_lost")
        code = {"reservation_expired": "timeout", "invalid_output": "internal_error"}.get(reason, reason)
        return self._fail(c, j, code, usage, files)              # nada quedó publicado

    def _outputs(self, c, j, est: Dict[str, Any], mock: bool, files: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Resultados a publicar en la transacción final (nada se inserta antes)."""
        brief = (j["request_metadata"] or {}).get("brief") or {}
        # Resultados temporales: simulados 7 días, reales 30 (después los purga el RetentionRunner).
        base = {"expires_at": (c.now + timedelta(days=7 if mock else 30)).isoformat()}
        out = [{**base, "kind": "script", "metadata": {"script": brief.get("script"), "mock": mock}}]
        for p in (est.get("plan") or {}).get("plan") or []:
            out.append({**base, "kind": "scene", "scene_index": p["index"], "origin": p["origin"],
                        "duration_ms": p["seconds"] * 1000,
                        "metadata": {"mock": mock, "cover": p["index"] == brief.get("cover_scene", 0)}})
        out.append({**base, "kind": "render", "mime_type": "video/mp4",
                    "metadata": {"mock": mock, "aspect_ratio": "9:16", "safe_subtitles": brief.get("subtitles", True)}})
        seen = set()
        for f in files:                                       # cuentan por su tamaño real (validado en la base)
            if f["temp_path"] in seen:
                continue                                      # el mismo archivo devuelto dos veces: un resultado
            seen.add(f["temp_path"])
            out.append({**base, "kind": f.get("kind") or "render", "storage_path": f["temp_path"],
                        "mime_type": f.get("mime_type"), "byte_size": int(f["byte_size"]), "metadata": {"mock": mock}})
        return out

    def run_pending(self, limit: int = 5) -> List[Dict[str, Any]]:
        """Bucle del worker (service role): reclama y ejecuta hasta `limit` trabajos. Lo invoca
        services/marketing_runtime.py; no está programado en producción."""
        out: List[Dict[str, Any]] = []
        for _ in range(max(int(limit), 0)):
            if self.stop_event.is_set():
                break                                             # SIGTERM: no se reclama nada más
            res = self.claim()
            if res.get("status") == "empty":
                break
            if res.get("status") != "claimed":
                out.append({"id": res.get("job_id"), "error": "media_not_ready"})
                continue
            try:
                out.append(self._execute(res["job"]))
            except WorkerError as e:
                out.append({"id": res["job"]["id"], "error": e.code})
        return out
