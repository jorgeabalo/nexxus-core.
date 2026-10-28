"""Cliente HTTP de pruebas para Contenido → Planear contenido con proveedores simulados (sin red ni gasto)."""
import uuid
from types import SimpleNamespace

from fastapi.testclient import TestClient

import main
from services import marketing_content_plan as cp
from services.marketing_ai_router import DisabledLocalTools, MarketingAIRouter, MockProvider, OmniRouteAdapter
from services.marketing_plan import MarketingPlanService
from services.marketing_studio import StudioService
from test_marketing import T1


class PlanClient(TestClient):
    """Cada POST /content/plan lleva su propia Idempotency-Key (como el navegador), salvo que la prueba la fije
    o pida explícitamente omitirla (no_key=True)."""

    def post(self, url, *a, no_key=False, **kw):
        if str(url).endswith("/content/plan") and not no_key:
            headers = dict(kw.pop("headers", None) or {})
            headers.setdefault("Idempotency-Key", f"plan-test-{uuid.uuid4().hex}")
            kw["headers"] = headers
        return super().post(url, *a, **kw)


def planner_client(monkeypatch, db, providers=None):
    from marketing_autopilot_fake import install
    if "marketing_plan_requests" not in db.tables:
        install(db)
    if providers is not None:
        real_init = MarketingPlanService.__init__
        monkeypatch.setattr(MarketingPlanService, "__init__",
                            lambda self, d, now=None, providers_=None: real_init(self, d, now=now, providers=providers))
    adapters = {"mock": MockProvider(), "omniroute": OmniRouteAdapter(env={}), "local_ffmpeg": DisabledLocalTools()}
    monkeypatch.setattr(MarketingPlanService, "planner", lambda self: cp.ContentPlanner(
        self, lambda: StudioService(db, now=self._now, router=MarketingAIRouter(env={}), adapters=adapters)))
    monkeypatch.setattr(main, "member_portal", SimpleNamespace(db=db))
    return PlanClient(main.app)


def plan_body(**kw):
    ins = {"topic": "Clase de prueba gratis", "audience": "Adultos que empiezan", "offer": "Primera semana gratis",
           "tone": "energetic", "cta": "Llámanos hoy", "platforms": ["instagram", "facebook"],
           "preferred_date": "2026-10-20T10:00", **kw.pop("instructions", {})}
    return {"tenant_id": T1, "objective": "calls", "mode": "fully_ai", "format": "reel", "instructions": ins,
            "language": "es", **kw}
