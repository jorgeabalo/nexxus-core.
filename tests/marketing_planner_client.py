"""Cliente HTTP de pruebas para Contenido → Planear contenido con proveedores simulados (sin red ni gasto)."""
from types import SimpleNamespace

from fastapi.testclient import TestClient

import main
from services import marketing_content_plan as cp
from services.marketing_ai_router import DisabledLocalTools, MarketingAIRouter, MockProvider, OmniRouteAdapter
from services.marketing_plan import MarketingPlanService
from services.marketing_studio import StudioService
from test_marketing import T1


def planner_client(monkeypatch, db):
    adapters = {"mock": MockProvider(), "omniroute": OmniRouteAdapter(env={}), "local_ffmpeg": DisabledLocalTools()}
    monkeypatch.setattr(MarketingPlanService, "planner", lambda self: cp.ContentPlanner(
        self, lambda: StudioService(db, now=self._now, router=MarketingAIRouter(env={}), adapters=adapters)))
    monkeypatch.setattr(main, "member_portal", SimpleNamespace(db=db))
    return TestClient(main.app)


def plan_body(**kw):
    ins = {"topic": "Clase de prueba gratis", "audience": "Adultos que empiezan", "offer": "Primera semana gratis",
           "tone": "energetic", "cta": "Llámanos hoy", "platforms": ["instagram", "facebook"],
           "preferred_date": "2026-10-20T10:00", **kw.pop("instructions", {})}
    return {"tenant_id": T1, "objective": "calls", "mode": "fully_ai", "format": "reel", "instructions": ins,
            "language": "es", **kw}
