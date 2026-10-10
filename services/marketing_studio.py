"""
AITA Marketing (Fase 2) — Estudio de Reels y trabajos de generación asíncronos.

Flujo (demostrable de punta a punta con proveedores simulados, sin red ni gasto):
  subir → clasificar → elegir mezcla → guion → estimar → APROBAR generación → procesar (mock)
  → revisar escenas → enviar a aprobación de contenido (Fase 1). Nunca se publica nada, y un
  resultado simulado no puede programarse (MarketingService._schedule_checks lo bloquea).
La ejecución vive en services/marketing_worker.py (preparada para un worker asíncrono).

Garantías:
  * nada pasa a 'queued' sin aprobación explícita de owner/manager (aquí y en el trigger);
  * la mezcla real/IA suma 100 y queda congelada al aprobar; cambiarla exige un trabajo nuevo
    (regeneración) con confirmación explícita;
  * límites del mes comprobados al aprobar (antes de cualquier gasto); el tenant no puede cambiarlos;
  * idempotency_key por trabajo y por subtarea: un reintento nunca crea un segundo cargo;
  * cancelar o fallar conserva coste y proveedor; los errores al cliente son códigos públicos.
"""
import logging
import uuid
from typing import Any, Dict, Optional

from services import marketing_jobs_domain as jd
from services import marketing_privacy as pv
from services.aita_cost_budget import CostBudget, to_cents
from services.marketing import _guard
from services.marketing_ai_router import MarketingAIRouter, default_adapters
from services.marketing_library import media_for_job
from services.marketing_mix import confirm_mix, plan_scenes, suggest_mix, validate_mix
from services.marketing_reel_plan import estimate, validate_brief
from services.marketing_studio_base import StudioBase
from services.marketing_worker import JobRunner, WorkerError, all_mock, recheck_inputs
from services.member_portal import PortalError

logger = logging.getLogger(__name__)
JOB_COLS = ("id,tenant_id,content_id,created_by,task_type,status,real_media_percent,ai_media_percent,quality_tier,"
            "people_policy,maximum_cost,estimated_cost,actual_cost,currency,router_strategy,selected_provider,"
            "selected_model,provider_job_id,idempotency_key,regeneration_of,approved_by,request_metadata,"
            "result_metadata,error_code,created_at,updated_at,approved_at,completed_at")


def public_job(j: Dict[str, Any]) -> Dict[str, Any]:
    """Lo que ve el tenant: sin proveedores, modelos, costos unitarios ni fuentes de precio (datos internos)."""
    if not isinstance(j, dict):
        return j
    out = {k: v for k, v in j.items() if k not in ("selected_provider", "selected_model", "provider_job_id")}
    meta = dict(out.get("request_metadata") or {})
    est = meta.get("estimate")
    if isinstance(est, dict):
        meta["estimate"] = {"estimated_cost": est.get("estimated_cost"), "currency": est.get("currency"),
                            "plan": est.get("plan"), "full_ai": est.get("full_ai"),
                            "production_methods": est.get("production_methods"),
                            "catalog_version": est.get("catalog_version"),
                            "generated_video_seconds": est.get("generated_video_seconds"),
                            "subtasks": [{"task_type": t.get("task_type"), "external": t.get("external"),
                                          "privacy_class": t.get("privacy_class"), "units": t.get("units")}
                                         for t in est.get("subtasks") or []]}
    out["request_metadata"] = meta
    return out


class StudioService(StudioBase):
    def __init__(self, db, now=None, providers=None, router: Optional[MarketingAIRouter] = None,
                 adapters: Optional[Dict[str, Any]] = None):
        super().__init__(db, now=now, providers=providers)
        self.router = router or MarketingAIRouter()
        self.adapters = adapters or default_adapters()
        self.budget = CostBudget(self.db)

    # ------------------------------------------------------------------ helpers
    def _job_event(self, c, job_id: str, action: str, frm: Optional[str], to: str, detail: Optional[Dict] = None):
        self.db.insert("marketing_generation_job_events", {
            "tenant_id": c.tenant_id, "job_id": job_id, "action": action, "from_status": frm, "to_status": to,
            "detail": detail or {}, "actor_id": c.user["id"] if c else None, "actor_role": c.role if c else "system"})

    def _move(self, c, job: Dict[str, Any], to: str, action: str, values: Optional[Dict] = None,
              approved: bool = False, detail: Optional[Dict] = None) -> Dict[str, Any]:
        jd.check_job_transition(job["status"], to, approved=approved)
        rows = self.db.update("marketing_generation_jobs",
                              {"id": f"eq.{job['id']}", "tenant_id": f"eq.{c.tenant_id}", "status": f"eq.{job['status']}"},
                              {**(values or {}), "status": to})
        if not rows:
            raise PortalError("conflict", 409)                  # otro proceso cambió el estado antes
        self._job_event(c, job["id"], action, job["status"], to, detail)
        return rows[0]

    def _gen_usage(self, c) -> Dict[str, float]:
        """Consumo del mes (zona del tenant). Cuenta al aprobar; un trabajo cancelado o fallido sin
        cargo del proveedor no cuenta. El coste usa el real si existe; si no, el estimado reservado."""
        start, end = self._month_bounds(c)
        rows = self.db.select("marketing_generation_jobs", {
            "tenant_id": f"eq.{c.tenant_id}", "and": f"(approved_at.gte.{start},approved_at.lt.{end})",
            "select": "status,regeneration_of,actual_cost,estimated_cost,provider_job_id,request_metadata",
            "limit": "10000"}) or []
        u = {"jobs": 0, "regenerations": 0, "images": 0, "video_seconds": 0}
        for r in rows:
            free = r["status"] in ("cancelled", "failed") and not r.get("provider_job_id") and not r.get("actual_cost")
            if free:
                continue
            est = (r.get("request_metadata") or {}).get("estimate") or {}
            u["jobs"] += 1
            u["regenerations"] += 1 if r.get("regeneration_of") else 0
            u["images"] += int(est.get("generated_images") or 0)
            u["video_seconds"] += int(est.get("generated_video_seconds") or 0)
        return u

    def _job(self, c, job_id: Any) -> Dict[str, Any]:
        return self._one(c, "marketing_generation_jobs", job_id, JOB_COLS)

    # ------------------------------------------------------------------ catálogo y sugerencias
    def overview(self, jwt: str, tenant_id: str) -> Dict[str, Any]:
        c = self.ctx(jwt, tenant_id)
        media = self.db.select("marketing_media", {"tenant_id": f"eq.{c.tenant_id}", "processing_status": "eq.ready",
                                                   "select": "id,contains_people,contains_minors,people_policy,"
                                                             "consent_status,processing_status,validation_status",
                                                   "limit": "1000"}) or []
        usable = sum(1 for m in media if pv.usable_media(m) is None and pv.original_class(m) in pv.EXTERNAL_OK)
        return {"limits": {k: c.settings.get(k) for k in ("ai_generation_enabled", *jd.GEN_LIMIT_KEYS)},
                "budget": self.budget.summary(c.tenant_id),
                "generation_enabled": jd.generation_enabled(c.settings),
                "usage": self._gen_usage(c), "suggestion": suggest_mix(len(media), usable),
                "catalog": {"catalog_version": self.router.catalog.version}, "presets": [list(p) for p in ((100, 0), (75, 25), (50, 50),
                                                                                      (25, 75), (0, 100))],
                "providers": {"omniroute_enabled": bool(self.adapters["omniroute"].enabled()), "mock": True}}

    def preview_mix(self, jwt: str, tenant_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        self.ctx(jwt, tenant_id)
        return plan_scenes(body.get("real_media_percent"), body.get("ai_media_percent"), int(body.get("scenes") or 6),
                           int(body.get("duration_seconds") or 30), body.get("adapt_real") is True)

    # ------------------------------------------------------------------ trabajos
    def jobs(self, jwt: str, tenant_id: str) -> Dict[str, Any]:
        c = self.ctx(jwt, tenant_id)
        rows = self.db.select("marketing_generation_jobs", {"tenant_id": f"eq.{c.tenant_id}", "select": JOB_COLS,
                                                            "order": "created_at.desc", "limit": "200"}) or []
        ids = [r["content_id"] for r in rows if r.get("content_id")]
        status = {c["id"]: c["status"] for c in (self.db.select("marketing_content", {
            "tenant_id": f"eq.{c.tenant_id}", "id": f"in.({','.join(ids)})", "select": "id,status",
            "limit": "200"}) or [])} if ids else {}
        for r in rows:
            r["content_status"] = status.get(r.get("content_id"))     # para mostrar la etapa real (nunca "publicado" sin serlo)
        return {"items": rows}

    def job(self, jwt: str, tenant_id: str, job_id: str) -> Dict[str, Any]:
        c = self.ctx(jwt, tenant_id)                     # permisos revalidados en cada consulta de estado
        j = self._job(c, job_id)
        q = {"tenant_id": f"eq.{c.tenant_id}", "job_id": f"eq.{j['id']}", "select": "*", "limit": "500"}
        return {**j, "events": self.db.select("marketing_generation_job_events", {**q, "order": "created_at.asc"}) or [],
                "inputs": self.db.select("marketing_generation_inputs", q) or [],
                "outputs": self.db.select("marketing_generation_outputs", {**q, "order": "scene_index.asc"}) or [],
                "usage": self.db.select("marketing_model_usage", q) or []}

    @_guard
    def create_job(self, jwt: str, tenant_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        c = self.ctx(jwt, tenant_id)
        self._writable(c)
        brief = validate_brief(body.get("brief") or {})
        mix = confirm_mix(c.role, body.get("real_media_percent"), body.get("ai_media_percent"))
        quality = body.get("quality_tier") if body.get("quality_tier") in jd.QUALITY_TIERS else None
        if not quality:
            raise PortalError("invalid_quality_tier", 400)
        try:
            max_cost = round(float(body.get("maximum_cost")), 2)
        except (TypeError, ValueError):
            raise PortalError("invalid_maximum_cost", 400)
        if not (0 <= max_cost <= 10000):
            raise PortalError("invalid_maximum_cost", 400)
        inputs = media_for_job(self, c, body.get("media_ids"), body.get("derivative_ids"))
        if mix["real_media_percent"] and not inputs["inputs"]:
            raise PortalError("real_media_required", 400)
        regen = None
        if body.get("regeneration_of"):
            prev = self._job(c, body["regeneration_of"])
            if prev["status"] not in jd.TERMINAL:
                raise PortalError("previous_job_active", 409)
            confirm_mix(c.role, mix["real_media_percent"], mix["ai_media_percent"],
                        previous={k: prev[k] for k in ("real_media_percent", "ai_media_percent")},
                        reconfirmed=body.get("mix_reconfirmed") is True)
            regen = prev["id"]
        people = "anonymize" if any(i["privacy_class"] == "anonymized_people" for i in inputs["inputs"]) else (
            "consented" if any(i["privacy_class"] == "consented_people" for i in inputs["inputs"]) else "no_people")
        scenes = int(body.get("scenes") or 6)
        plan_scenes(mix["real_media_percent"], mix["ai_media_percent"], scenes, brief["duration_seconds"])
        key = str(body.get("idempotency_key") or "")
        if not (16 <= len(key) <= 120) or not key.replace("-", "").isalnum():
            key = f"job-{uuid.uuid4()}"
        existing = self.db.select("marketing_generation_jobs", {"tenant_id": f"eq.{c.tenant_id}",
                                                                "idempotency_key": f"eq.{key}", "select": JOB_COLS,
                                                                "limit": "1"})
        if existing:
            return existing[0]                           # mismo envío repetido: mismo trabajo
        job = self.db.insert("marketing_generation_jobs", {
            "tenant_id": c.tenant_id, "created_by": c.user["id"], "task_type": "reel", "status": "draft",
            **mix, "quality_tier": quality, "people_policy": people, "maximum_cost": max_cost, "currency":
            self.router.catalog.currency, "idempotency_key": key, "regeneration_of": regen,
            "request_metadata": {"brief": brief, "scenes": scenes, "adapt_real": body.get("adapt_real") is True,
                                 "privacy_class": inputs["privacy_class"], "real_kinds": inputs["kinds"],
                                 "malware_clean": inputs["malware_clean"],
                                 "mix_confirmed_by": c.user["id"], "mix_confirmed_at": c.now.isoformat()}})
        for i in inputs["inputs"]:
            self.db.insert("marketing_generation_inputs", {"tenant_id": c.tenant_id, "job_id": job["id"],
                                                           "media_id": i["media_id"], "derivative_id": i["derivative_id"],
                                                           "role": "source", "privacy_class": i["privacy_class"]})
        self._job_event(c, job["id"], "create", None, "draft", {"mix": mix})
        return job

    @_guard
    def estimate_job(self, jwt: str, tenant_id: str, job_id: str) -> Dict[str, Any]:
        c = self.ctx(jwt, tenant_id)
        self._writable(c)
        j = self._job(c, job_id)
        if j["status"] != "draft":
            raise PortalError("invalid_transition", 409)
        meta = j["request_metadata"] or {}
        est = estimate(self.router, tenant_id=c.tenant_id, job_key=j["idempotency_key"], brief=meta["brief"],
                       mix=validate_mix(j["real_media_percent"], j["ai_media_percent"]), scenes=int(meta["scenes"]),
                       adapt_real=bool(meta.get("adapt_real")), input_class=meta.get("privacy_class", "synthetic_only"),
                       people_policy=j["people_policy"], real_kinds=list(meta.get("real_kinds") or []),
                       quality_tier=j["quality_tier"], maximum_cost=float(j["maximum_cost"]),
                       inputs_malware_clean=bool(meta.get("malware_clean")))
        return self._move(c, j, "awaiting_generation_approval", "request_approval",
                          {"estimated_cost": est["estimated_cost"], "request_metadata": {**meta, "estimate": est}},
                          detail={"estimated_cost": est["estimated_cost"], "catalog_version": est["catalog_version"]})

    @_guard
    def approve_job(self, jwt: str, tenant_id: str, job_id: str, confirm: bool) -> Dict[str, Any]:
        c = self.ctx(jwt, tenant_id)
        self._writable(c)
        if confirm is not True:
            raise PortalError("confirmation_required", 400)
        j = self._job(c, job_id)
        if j["status"] != "awaiting_generation_approval":
            raise PortalError("invalid_transition", 409)
        why = recheck_inputs(self.db, c.tenant_id, j["id"])
        if why:
            raise PortalError(why, 409)                      # p. ej. consentimiento retirado después de crear
        est = (j["request_metadata"] or {}).get("estimate") or {}
        jd.check_generation_limits(c.settings, self._gen_usage(c), regeneration=bool(j.get("regeneration_of")),
                                   images=int(est.get("generated_images") or 0),
                                   video_seconds=int(est.get("generated_video_seconds") or 0))
        # Presupuesto global: reserva atómica del costo máximo estimado ANTES de entrar en cola.
        cents = to_cents(j.get("estimated_cost"))
        key = f"job:{j['id']}"
        self.budget.reserve(c.tenant_id, "marketing_ai", "router", None, "reel_generation", cents, key)
        try:
            return self._move(c, j, "queued", "approve", {"approved_at": c.now.isoformat(), "approved_by": c.user["id"]},
                              approved=True, detail={"estimated_cost": j["estimated_cost"], "reserved_cents": cents})
        except PortalError:
            self.budget.release(c.tenant_id, key)          # no quedó aprobado: se libera la reserva
            raise

    @_guard
    def reopen_job(self, jwt: str, tenant_id: str, job_id: str) -> Dict[str, Any]:
        c = self.ctx(jwt, tenant_id)
        self._writable(c)
        return self._move(c, self._job(c, job_id), "draft", "reopen")

    @_guard
    def cancel_job(self, jwt: str, tenant_id: str, job_id: str) -> Dict[str, Any]:
        c = self.ctx(jwt, tenant_id)
        self._writable(c)
        j = self._job(c, job_id)
        out = self._move(c, j, "cancelled", "cancel", {"error_code": "cancelled_by_user",
                                                       "completed_at": c.now.isoformat()})
        if j["status"] == "queued":
            self.budget.release(c.tenant_id, f"job:{j['id']}")   # nada se gastó: se libera todo
        return out

    @_guard
    def process_job(self, jwt: str, tenant_id: str, job_id: str) -> Dict[str, Any]:
        """Desde una petición HTTP solo se ejecutan trabajos 100 % simulados (rápidos, sin red). Cualquier
        trabajo con proveedores reales, vídeo, render o anonimización real requiere el worker asíncrono."""
        c = self.ctx(jwt, tenant_id)
        self._writable(c)
        j = self._job(c, job_id)
        if j["status"] != "queued" or not j.get("approved_at"):
            raise PortalError("generation_approval_required" if j["status"] in ("draft", "awaiting_generation_approval")
                              else "invalid_transition", 409)
        if not all_mock((j["request_metadata"] or {}).get("estimate") or {}):
            raise PortalError("requires_worker", 409)
        try:
            return JobRunner(self.db, self.router, self.adapters, now=c.now).run(c.tenant_id, j["id"])
        except WorkerError as e:
            raise PortalError("conflict" if e.code == "already_claimed" else "invalid_transition", 409)

    # ------------------------------------------------------------------ revisión → aprobación de contenido
    @_guard
    def send_to_approval(self, jwt: str, tenant_id: str, job_id: str, title: str) -> Dict[str, Any]:
        """Crea el contenido (formato reel) y lo envía a revisión de Fase 1. No programa ni publica."""
        c = self.ctx(jwt, tenant_id)
        self._writable(c)
        j = self._job(c, job_id)
        if j["status"] != "succeeded":
            raise PortalError("job_not_succeeded", 409)
        if j.get("content_id"):
            raise PortalError("already_sent", 409)
        why = recheck_inputs(self.db, c.tenant_id, j["id"])
        if why:
            raise PortalError(why, 409)            # p. ej. consentimiento retirado: el resultado no se reutiliza
        brief = (j["request_metadata"] or {}).get("brief") or {}
        script = brief.get("script") or {}
        item = self.create_content(jwt, c.tenant_id, {
            "title": (str(title or "").strip() or "Reel")[:160], "format": "reel", "language": brief.get("language", "es"),
            "script": "\n".join(x for x in (script.get("hook"), script.get("body")) if x)[:4000] or None,
            "cta": script.get("cta") or None,
            "notes": f"{'mock_generation_job' if (j.get('result_metadata') or {}).get('mock') else 'generation_job'}:{j['id']}"})
        self.db.update("marketing_generation_jobs", {"id": f"eq.{j['id']}", "tenant_id": f"eq.{c.tenant_id}"},
                       {"content_id": item["id"]})
        self.db.update("marketing_generation_outputs", {"job_id": f"eq.{j['id']}", "tenant_id": f"eq.{c.tenant_id}"},
                       {"review_status": "in_review"})
        reviewed = self.transition(jwt, c.tenant_id, item["id"], "review")
        self._job_event(c, j["id"], "review", "succeeded", "succeeded", {"content_id": item["id"]})
        return {"content": reviewed, "job_id": j["id"]}

