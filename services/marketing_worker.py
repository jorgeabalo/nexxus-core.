"""
AITA Marketing (Fase 2) — ejecución de trabajos de generación (preparada para worker / cola).

* Un trabajo solo se ejecuta si está 'queued' y aprobado. Se "reclama" con una actualización
  condicional queued → processing: si dos workers lo intentan a la vez, solo uno gana.
* Antes de enviar nada se vuelven a comprobar las entradas: consentimiento retirado, menores,
  archivo excluido o eliminado → el trabajo falla con un código público y no se envía nada.
* Cada subtarea lleva su idempotency_key: un reintento nunca crea un segundo cargo.
* Vídeo, render, anonimización y proveedores reales son trabajo pesado y deben ejecutarse en un
  proceso aparte (run_pending, llamado por un worker programado). En esta fase NO hay worker
  desplegado: la petición HTTP solo puede ejecutar trabajos 100 % simulados (rápidos, sin red).
"""
from types import SimpleNamespace
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from services import marketing_jobs_domain as jd
from services import marketing_privacy as pv
from services.marketing_ai_router import RouteRequest

JOB_COLS = ("id,tenant_id,content_id,status,quality_tier,maximum_cost,currency,approved_at,approved_by,"
            "request_metadata,result_metadata,real_media_percent,ai_media_percent")
MEDIA_CHECK = "id,processing_status,validation_status,contains_people,contains_minors,people_policy,consent_status"


class WorkerError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def recheck_inputs(db, tenant_id: str, job_id: str) -> Optional[str]:
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
    return None


def all_mock(est: Dict[str, Any]) -> bool:
    subs = est.get("subtasks") or []
    return bool(subs) and all(s.get("provider") == "mock" and not s.get("external") for s in subs)


class JobRunner:
    def __init__(self, db, router, adapters: Dict[str, Any], now: Optional[datetime] = None):
        self.db, self.router, self.adapters, self._now = db, router, adapters, now

    def _c(self, tenant_id: str):
        return SimpleNamespace(tenant_id=tenant_id, now=self._now or datetime.now(timezone.utc))

    def _event(self, c, job_id, action, frm, to, detail=None):
        self.db.insert("marketing_generation_job_events", {
            "tenant_id": c.tenant_id, "job_id": job_id, "action": action, "from_status": frm, "to_status": to,
            "detail": detail or {}, "actor_id": None, "actor_role": "system"})

    def _move(self, c, job, to, action, values=None, detail=None):
        jd.check_job_transition(job["status"], to, approved=bool(job.get("approved_at")))
        rows = self.db.update("marketing_generation_jobs",
                              {"id": f"eq.{job['id']}", "tenant_id": f"eq.{c.tenant_id}", "status": f"eq.{job['status']}"},
                              {**(values or {}), "status": to})
        if not rows:
            raise WorkerError("already_claimed")                 # otro worker se adelantó
        self._event(c, job["id"], action, job["status"], to, detail)
        return rows[0]

    def _fail(self, c, j, code: str, cost: float):
        return self._move(c, j, "failed", "fail", {"error_code": jd.public_error(code), "actual_cost": round(cost, 4),
                                                   "completed_at": c.now.isoformat()}, detail={"error_code": code})

    def run(self, tenant_id: str, job_id: str) -> Dict[str, Any]:
        c = self._c(tenant_id)
        j = (self.db.select("marketing_generation_jobs", {"tenant_id": f"eq.{tenant_id}", "id": f"eq.{job_id}",
                                                          "select": JOB_COLS, "limit": "1"}) or [None])[0]
        if not j or j["status"] != "queued" or not j.get("approved_at") or not j.get("approved_by"):
            raise WorkerError("not_runnable")
        est = (j["request_metadata"] or {}).get("estimate") or {}
        j = self._move(c, j, "processing", "start")               # reclamar el trabajo
        why = recheck_inputs(self.db, tenant_id, j["id"])
        if why:
            return self._fail(c, j, why, 0.0)
        total, n, first = 0.0, 0, None
        for st in est.get("subtasks") or []:
            adapter = self.adapters.get(st["provider"])
            model = next((m for m in self.router.catalog.models if m.model_id == st["model_id"]), None)
            if adapter is None or model is None:
                return self._fail(c, j, "provider_unavailable", total)
            if st["external"] and not pv.can_go_external(st["privacy_class"]):
                return self._fail(c, j, "privacy_blocked", total)          # nunca restricted hacia fuera
            req = RouteRequest(tenant_id=tenant_id, task_type=st["task_type"], quality_tier=j["quality_tier"],
                               maximum_cost=float(j["maximum_cost"]), privacy_class=st["privacy_class"],
                               idempotency_key=st["idempotency_key"], units=float(st["units"]))
            done = self.db.select("marketing_model_usage", {"tenant_id": f"eq.{tenant_id}",
                                                            "idempotency_key": f"eq.{req.idempotency_key}",
                                                            "select": "actual_cost,provider_job_id", "limit": "1"})
            if done:                                     # reintento: ya facturado, no se vuelve a enviar
                total += float(done[0].get("actual_cost") or 0)
                continue
            res = adapter.submit(req, model)
            if res.status != "succeeded":
                return self._fail(c, j, jd.public_error(res.error_code), total)
            cost = float(res.actual_cost or 0)
            if total + cost > float(j["maximum_cost"]):
                return self._fail(c, j, "budget_exceeded", total)
            total += cost
            n += 1
            first = first or (st["provider"], st["model_id"], res.provider_job_id)
            self.db.insert("marketing_model_usage", {
                "tenant_id": tenant_id, "job_id": j["id"], "provider": st["provider"], "model_id": st["model_id"],
                "task_type": st["task_type"], "catalog_version": est.get("catalog_version", "unknown"),
                "billing_unit": st["billing_unit"], "units": st["units"], "estimated_cost": st["estimated_cost"],
                "actual_cost": cost, "currency": j["currency"], "idempotency_key": req.idempotency_key,
                "provider_job_id": res.provider_job_id, "status": "charged" if cost else "not_charged"})
        mock = all_mock(est)
        self._outputs(c, j, est, mock)
        prov, model_id, pjid = first or ("mock", None, None)
        return self._move(c, j, "succeeded", "succeed", {
            "actual_cost": round(total, 4), "selected_provider": prov, "selected_model": model_id,
            "provider_job_id": pjid, "completed_at": c.now.isoformat(),
            "result_metadata": {"mock": mock, "scenes": est.get("plan", {}).get("scenes"), "provider_jobs": n}})

    def _outputs(self, c, j, est: Dict[str, Any], mock: bool) -> None:
        brief = (j["request_metadata"] or {}).get("brief") or {}
        base = {"tenant_id": c.tenant_id, "job_id": j["id"]}
        self.db.insert("marketing_generation_outputs", {**base, "kind": "script",
                                                        "metadata": {"script": brief.get("script"), "mock": mock}})
        for p in (est.get("plan") or {}).get("plan") or []:
            self.db.insert("marketing_generation_outputs", {
                **base, "kind": "scene", "scene_index": p["index"], "origin": p["origin"],
                "duration_ms": p["seconds"] * 1000, "review_status": "generated",
                "metadata": {"mock": mock, "cover": p["index"] == brief.get("cover_scene", 0)}})
        self.db.insert("marketing_generation_outputs", {**base, "kind": "render", "mime_type": "video/mp4",
                                                        "review_status": "generated",
                                                        "metadata": {"mock": mock, "aspect_ratio": "9:16",
                                                                     "safe_subtitles": brief.get("subtitles", True)}})

    def run_pending(self, limit: int = 5) -> List[Dict[str, Any]]:
        """Punto de entrada de un worker futuro (service role): procesa trabajos en cola, más antiguos
        primero. No está programado en ningún sitio en esta fase."""
        out = []
        for row in self.db.select("marketing_generation_jobs", {"status": "eq.queued", "select": "id,tenant_id",
                                                                "order": "approved_at.asc", "limit": str(int(limit))}) or []:
            try:
                out.append(self.run(row["tenant_id"], row["id"]))
            except WorkerError as e:
                out.append({"id": row["id"], "error": e.code})
        return out
