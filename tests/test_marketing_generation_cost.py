"""
AITA Marketing (Fase 2): costo de los trabajos de generación (solo Marketing; el presupuesto global
de voz/facturación irá en otro PR). Costo máximo estimado, reservado y real; proveedor/modelo;
idempotencia; bloqueo por el límite del operador; concurrencia; mocks con costo real 0 y sin red.
"""
import inspect
import socket
import threading
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import main
from services import marketing_jobs_domain as jd
from services.marketing_ai_router import MarketingAIRouter, MockProvider, OmniRouteAdapter, DisabledLocalTools
from services.marketing_library import LibraryService
from services.marketing_studio import StudioService
from test_marketing import NOW, T1, OWNER
from test_marketing_studio import StudioDB, err, jwt, new_job, approved_job


@pytest.fixture
def db():
    return StudioDB()


@pytest.fixture
def lib(db):
    return LibraryService(db, now=NOW)


@pytest.fixture
def studio(db):
    return StudioService(db, now=NOW, router=MarketingAIRouter(env={}),
                         adapters={"mock": MockProvider(), "omniroute": OmniRouteAdapter(env={}),
                                   "local_ffmpeg": DisabledLocalTools()})


def awaiting(studio, db, estimated_usd):
    j = studio.estimate_job(jwt(OWNER), T1, new_job(studio, [], 0, 100)["id"])
    next(r for r in db.tables["marketing_generation_jobs"] if r["id"] == j["id"])["estimated_cost"] = estimated_usd
    return j


def row(db, jid):
    return next(r for r in db.tables["marketing_generation_jobs"] if r["id"] == jid)


@pytest.mark.parametrize("usd,out", [(0, 0.0), ("0.00001", 0.0001), (0.3, 0.3), ("1.23456", 1.2346)])
def test_to_cost_rounds_up(usd, out):
    assert jd.to_cost(usd) == out


@pytest.mark.parametrize("bad", [None, -1, "abc", float("nan"), True])
def test_not_estimable(bad):
    assert jd.to_cost(bad) is None


@pytest.mark.parametrize("avail,limit,level", [(20, 20, None), (5, 20, "low"), (2, 20, "critical"), (0, 20, "exhausted"),
                                               (0, 0, "not_enabled"), (0, None, None)])
def test_warning_levels(avail, limit, level):
    assert jd.warning_level(avail, limit) == level


def test_job_keeps_estimated_reserved_actual_provider_and_idempotency(lib, studio, db):
    j = approved_job(studio)
    r = row(db, j["id"])
    assert r["estimated_cost"] == 0 and r["reserved_cost"] == 0 and r["status"] == "queued"
    studio.process_job(jwt(OWNER), T1, j["id"])
    r = row(db, j["id"])
    assert r["actual_cost"] == 0 and r["selected_provider"] == "mock" and r["selected_model"]       # mock: costo 0
    usage = [u for u in db.tables["marketing_model_usage"] if u["job_id"] == j["id"]]
    assert usage and all(u["actual_cost"] == 0 and u["provider"] == "mock" and u["model_id"] for u in usage)
    assert len({u["idempotency_key"] for u in usage}) == len(usage)
    assert all(u["idempotency_key"].startswith(r["idempotency_key"]) for u in usage)


def test_mock_jobs_cost_nothing_and_use_no_network(lib, studio, db, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network used")
    monkeypatch.setattr(socket, "create_connection", boom)
    monkeypatch.setattr(socket.socket, "connect", boom)
    for _ in range(3):
        j = approved_job(studio)
        studio.process_job(jwt(OWNER), T1, j["id"])
    b = studio.overview(jwt(OWNER), T1)["budget"]
    assert b["consumed"] == 0 and b["reserved"] == 0 and b["available"] == 20 and b["warning"] is None


def test_cost_not_estimable_never_runs(lib, studio, db):
    j = awaiting(studio, db, None)
    assert err(studio.approve_job, jwt(OWNER), T1, j["id"], True) == "cost_not_estimable"
    assert row(db, j["id"])["status"] == "awaiting_generation_approval" and "marketing_approve_generation" not in db.rpc_calls


def test_operator_limit_blocks(lib, studio, db):
    j = awaiting(studio, db, 25.0)                                      # 25 > 20 USD del mes
    assert err(studio.approve_job, jwt(OWNER), T1, j["id"], True) == "budget_exceeded"
    assert row(db, j["id"])["status"] == "awaiting_generation_approval" and row(db, j["id"]).get("reserved_cost") is None


def test_reserved_counts_until_finished_then_only_real_cost(lib, studio, db):
    j1 = awaiting(studio, db, 15.0)
    studio.approve_job(jwt(OWNER), T1, j1["id"], True)
    assert studio.overview(jwt(OWNER), T1)["budget"]["reserved"] == 15.0
    j2 = awaiting(studio, db, 10.0)
    assert err(studio.approve_job, jwt(OWNER), T1, j2["id"], True) == "budget_exceeded"      # 15 + 10 > 20
    studio.cancel_job(jwt(OWNER), T1, j1["id"])                         # cancelado sin gasto: deja de contar
    assert studio.approve_job(jwt(OWNER), T1, j2["id"], True)["status"] == "queued"


def test_concurrent_approvals_cannot_use_same_balance(lib, studio, db):
    db.rpc_delay = 0.02
    jobs = [awaiting(studio, db, 15.0) for _ in range(2)]               # 15 + 15 > 20
    results = []

    def go(j):
        try:
            results.append(studio.approve_job(jwt(OWNER), T1, j["id"], True)["status"])
        except Exception as e:                                          # noqa: BLE001
            results.append(getattr(e, "code", "error"))
    ts = [threading.Thread(target=go, args=(j,)) for j in jobs]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(results) == ["budget_exceeded", "queued"]
    assert sum(float(r.get("reserved_cost") or 0) for r in db.tables["marketing_generation_jobs"]
               if r["status"] == "queued") == 15.0


def test_worker_never_spends_more_than_reserved(lib, studio, db):
    j = approved_job(studio)
    row(db, j["id"])["reserved_cost"] = 0.0

    class Pricey(MockProvider):
        def submit(self, req, model):
            res = super().submit(req, model)
            return res.__class__(**{**res.__dict__, "actual_cost": 0.5})
    studio.adapters["mock"] = Pricey()
    out = studio.process_job(jwt(OWNER), T1, j["id"])
    assert out["status"] == "failed" and out["error_code"] == "budget_exceeded"


def test_full_ai_reel_shows_cost_and_needs_approval(lib, studio, db):
    j = studio.estimate_job(jwt(OWNER), T1, new_job(studio, [], 0, 100)["id"])
    est = j["request_metadata"]["estimate"]
    assert est["full_ai"] is True and "estimated_cost" in est and j["status"] == "awaiting_generation_approval"
    assert est["production_methods"][-1] == "text_to_video"
    assert err(studio.process_job, jwt(OWNER), T1, j["id"]) == "generation_approval_required"
    assert err(studio.approve_job, jwt(OWNER), T1, j["id"], False) == "confirmation_required"


def test_zero_limit_blocks_even_mock(lib, studio, db):
    j = studio.estimate_job(jwt(OWNER), T1, new_job(studio, [], 0, 100)["id"])
    db.tables["marketing_settings"][0]["monthly_ai_cost_limit"] = 0
    assert err(studio.approve_job, jwt(OWNER), T1, j["id"], True) == "generation_disabled"


def test_defaults_are_closed():
    assert jd.GEN_DEFAULTS["monthly_ai_cost_limit"] == 0 and jd.GEN_DEFAULTS["ai_generation_enabled"] is False
    assert all(jd.GEN_DEFAULTS[k] == 0 for k in jd.GEN_LIMIT_KEYS)


def test_tenant_cannot_change_limits():
    import services.marketing_studio as m1
    import services.marketing_studio_routes as m2
    import services.marketing_library as m3
    for mod in (m1, m2, m3):
        src = inspect.getsource(mod)
        for verb in ("insert", "update", "upsert", "delete"):
            assert f'{verb}("marketing_settings"' not in src, mod.__name__


def test_client_material_preferred_by_default():
    from services.marketing_mix import suggest_mix
    assert suggest_mix(5, 3)["real_media_percent"] == 100 and suggest_mix(0, 0)["real_media_percent"] == 0


def test_http_never_exposes_internal_costs_or_providers(monkeypatch, db):
    monkeypatch.setattr(main, "member_portal", SimpleNamespace(db=db))
    c = TestClient(main.app)
    H = {"Authorization": f"Bearer jwt-{OWNER}"}
    body = {"tenant_id": T1, "brief": {"objective": "brand", "audience": "general", "style": "calm", "duration_seconds": 15},
            "real_media_percent": 0, "ai_media_percent": 100, "quality_tier": "draft", "maximum_cost": 1}
    jid = c.post("/api/manager/marketing/jobs", json=body, headers=H).json()["id"]
    for action, extra in (("estimate", {}), ("approve", {"confirm": True}), ("process", {})):
        assert c.post(f"/api/manager/marketing/jobs/{jid}/{action}", json={"tenant_id": T1, **extra}, headers=H).status_code == 200
    texts = [c.get(f"/api/manager/marketing/jobs/{jid}?tenant_id={T1}", headers=H).text,
             c.get(f"/api/manager/marketing/jobs?tenant_id={T1}", headers=H).text,
             c.get(f"/api/manager/marketing/studio?tenant_id={T1}", headers=H).text]
    for t in texts:
        for leak in ("mock-media-v1", "selected_provider", "provider_job_id", "price_source", "billing_unit", "model_id"):
            assert leak not in t, leak
    job = c.get(f"/api/manager/marketing/jobs/{jid}?tenant_id={T1}", headers=H).json()
    assert job["cost"]["category"] == "marketing_ai_budget"
    assert set(job["cost"]) == {"category", "currency", "estimated", "reserved", "actual"}
    assert job["cost"]["reserved"] == 0 and job["cost"]["actual"] == 0
    ov = c.get(f"/api/manager/marketing/studio?tenant_id={T1}", headers=H).json()
    assert set(ov["budget"]) == {"scope", "limit", "consumed", "reserved", "available", "currency", "warning", "available_pct"}
    assert ov["budget"]["scope"] == "marketing_ai_budget"                      # NO es el presupuesto global
