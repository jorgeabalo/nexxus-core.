"""
AITA — control global de costos (< 80 USD/mes por tenant): reserva atómica, idempotencia, conciliación,
liberación, concurrencia, avisos y aislamiento. Sin red ni gasto: proveedores simulados (costo real 0).
"""
import inspect
import threading
from pathlib import Path

import pytest

from services import aita_cost_budget as cb
from services.aita_cost_budget import CostBudget
from services.marketing_domain import DomainError
from test_marketing import T1, T2, OWNER
from services.marketing_ai_router import MarketingAIRouter, MockProvider, OmniRouteAdapter, DisabledLocalTools
from services.marketing_library import LibraryService
from services.marketing_studio import StudioService
from test_marketing import NOW
from test_marketing_studio import StudioDB, err, jwt, up, consented, new_job, approved_job


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

ROOT = Path(__file__).resolve().parents[1]


def ledger(db, tenant=T1):
    return [e for e in db.tables["aita_cost_ledger"] if e["tenant_id"] == tenant]


def awaiting(studio, db, estimated_usd):
    j = studio.estimate_job(jwt(OWNER), T1, new_job(studio, [], 0, 100)["id"])
    row = next(r for r in db.tables["marketing_generation_jobs"] if r["id"] == j["id"])
    row["estimated_cost"] = estimated_usd                     # simula un proveedor con precio (no hay ninguno real)
    return j


# ------------------------------------------------------------------ utilidades
@pytest.mark.parametrize("usd,cents", [(0, 0), ("0.001", 1), (0.30, 30), ("1.234", 124), (12, 1200)])
def test_to_cents_rounds_up(usd, cents):
    assert cb.to_cents(usd) == cents


@pytest.mark.parametrize("bad", [None, -1, "abc", float("nan"), float("inf"), True])
def test_not_estimable(bad):
    assert cb.to_cents(bad) is None


@pytest.mark.parametrize("avail,limit,level", [(2000, 2000, None), (500, 2000, "low"), (200, 2000, "critical"),
                                               (0, 2000, "exhausted"), (0, 0, "not_enabled")])
def test_warning_levels(avail, limit, level):
    assert cb.warning_level(avail, limit) == level


def test_pilot_proposal_fits_80_usd():
    p = cb.PILOT_PROPOSAL_CENTS
    assert p["monthly_total_cost_limit_cents"] == 8000
    assert sum(v for k, v in p.items() if k != "monthly_total_cost_limit_cents") <= 8000


@pytest.mark.parametrize("bad", [None, -1, 1.5, "10"])
def test_backend_refuses_before_calling_database_when_not_estimable(bad):
    class NoDB:
        def rpc(self, *a):
            raise AssertionError("no debía llegar a la base de datos")
    with pytest.raises(DomainError) as e:
        CostBudget(NoDB()).reserve(T1, "marketing_ai", "mock", None, "x", bad, "key-0000001")
    assert e.value.code == "cost_not_estimable"


# ------------------------------------------------------------------ ciclo completo con mock
def test_mock_job_reserves_commits_zero_and_releases(lib, studio, db):
    j = approved_job(studio)
    e = ledger(db)[0]
    assert e["status"] == "reserved" and e["service_category"] == "marketing_ai" and e["idempotency_key"] == f"job:{j['id']}"
    studio.process_job(jwt(OWNER), T1, j["id"])
    assert e["status"] == "committed" and e["actual_cost_cents"] == 0              # mock: costo real 0
    s = studio.overview(jwt(OWNER), T1)["budget"]
    assert s["marketing_ai"] == {"limit_cents": 2000, "consumed_cents": 0, "reserved_cents": 0, "available_cents": 2000,
                                 "warning": None, "available_pct": 100}


def test_cost_not_estimable_never_runs(lib, studio, db):
    j = awaiting(studio, db, None)
    assert err(studio.approve_job, jwt(OWNER), T1, j["id"], True) == "cost_not_estimable"
    assert ledger(db) == [] and studio.job(jwt(OWNER), T1, j["id"])["status"] == "awaiting_generation_approval"


def test_reservation_rejected_when_budget_would_be_exceeded(lib, studio, db):
    j = awaiting(studio, db, 25.00)                                               # 2500 > 2000 de Marketing IA
    assert err(studio.approve_job, jwt(OWNER), T1, j["id"], True) == "budget_exceeded"
    assert ledger(db) == [] and studio.job(jwt(OWNER), T1, j["id"])["status"] == "awaiting_generation_approval"


def test_total_limit_applies_even_with_big_category(lib, studio, db):
    db.tables["aita_cost_budgets"][0].update({"monthly_marketing_ai_cost_limit_cents": 7000, "monthly_voice_cost_limit_cents": 0,
                                              "monthly_reserve_cents": 1000})
    db.tables["aita_cost_ledger"].append({"id": "v", "tenant_id": T1, "service_category": "voice", "status": "committed",
                                          "reserved_cost_cents": 3000, "actual_cost_cents": 3000, "idempotency_key": "call:1"})
    j = awaiting(studio, db, 45.00)                                               # 3000 + 4500 > 8000 − 1000
    assert err(studio.approve_job, jwt(OWNER), T1, j["id"], True) == "budget_exceeded"


def test_cancel_and_consent_revocation_release_reservations(lib, studio, db):
    j = approved_job(studio)
    studio.cancel_job(jwt(OWNER), T1, j["id"])
    assert ledger(db)[0]["status"] == "released"
    m = up(lib)
    consented(lib, m)
    j2 = approved_job(studio, [m["id"]], 50, 50)
    lib.revoke_consent(jwt(OWNER), T1, m["id"])
    e = next(x for x in ledger(db) if x["idempotency_key"] == f"job:{j2['id']}")
    assert e["status"] == "released"


def test_failed_job_records_spent_and_releases_rest(lib, studio, db, monkeypatch):
    j = approved_job(studio)

    class Boom:
        def submit(self, req, model):
            from services.marketing_ai_router import ProviderJob
            return ProviderJob(status="failed", provider_job_id=None, error_code="provider_unavailable")
    studio.adapters["mock"] = Boom()
    out = studio.process_job(jwt(OWNER), T1, j["id"])
    assert out["status"] == "failed" and ledger(db)[0]["status"] == "released"


# ------------------------------------------------------------------ idempotencia
def test_retries_never_reserve_or_charge_twice(db):
    b = CostBudget(db)
    first = b.reserve(T1, "marketing_ai", "mock", None, "x", 100, "op-key-0001")
    again = b.reserve(T1, "marketing_ai", "mock", None, "x", 100, "op-key-0001")
    assert first["status"] == "reserved" and again["status"] == "duplicate" and len(ledger(db)) == 1
    assert b.reconcile(T1, "op-key-0001", 40)["status"] == "committed"
    assert b.reconcile(T1, "op-key-0001", 999).get("duplicate") and ledger(db)[0]["actual_cost_cents"] == 40
    assert b.summary(T1)["marketing_ai"]["consumed_cents"] == 40              # la diferencia quedó libre


# ------------------------------------------------------------------ concurrencia
def test_concurrent_jobs_cannot_reserve_same_balance(lib, studio, db):
    db.rpc_delay = 0.02                                                      # agranda la ventana de carrera
    jobs = [awaiting(studio, db, 15.00) for _ in range(2)]                    # 1500 + 1500 > 2000
    results = []

    def go(j):
        try:
            results.append(studio.approve_job(jwt(OWNER), T1, j["id"], True)["status"])
        except Exception as e:                                               # noqa: BLE001
            results.append(getattr(e, "code", "error"))
    ts = [threading.Thread(target=go, args=(j,)) for j in jobs]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(results) == ["budget_exceeded", "queued"]
    assert sum(e["reserved_cost_cents"] for e in ledger(db) if e["status"] == "reserved") == 1500


def test_many_parallel_reservations_never_overspend(db):
    db.rpc_delay = 0.005
    b, ok = CostBudget(db), []

    def go(i):
        try:
            b.reserve(T1, "marketing_ai", "mock", None, "x", 300, f"parallel-key-{i:04d}")
            ok.append(i)
        except DomainError:
            pass
    ts = [threading.Thread(target=go, args=(i,)) for i in range(12)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(ok) == 6 and sum(e["reserved_cost_cents"] for e in ledger(db)) == 1800      # 6 × 300 ≤ 2000


# ------------------------------------------------------------------ aislamiento y permisos
def test_summary_only_shows_own_tenant_without_internal_details(db):
    b = CostBudget(db)
    b.reserve(T2, "marketing_ai", "omniroute", "secret-model", "x", 900, "other-tenant-01")
    s = b.summary(T1)
    assert s["marketing_ai"]["reserved_cents"] == 0 and s["total"]["reserved_cents"] == 0
    assert "secret-model" not in str(s) and "omniroute" not in str(s) and "provider" not in str(s)


def test_tenant_cannot_change_budgets():
    import services.aita_cost_budget as m1
    import services.marketing_studio as m2
    import services.marketing_studio_routes as m3
    import services.marketing_library as m4
    for mod in (m1, m2, m3, m4):
        src = inspect.getsource(mod)
        for verb in ("insert", "update", "upsert", "delete"):
            assert f'{verb}("aita_cost_budgets"' not in src and f'{verb}("aita_cost_ledger"' not in src, mod.__name__


def test_full_ai_reel_is_flagged_and_needs_approval(lib, studio, db):
    j = studio.estimate_job(jwt(OWNER), T1, new_job(studio, [], 0, 100)["id"])
    est = j["request_metadata"]["estimate"]
    assert est["full_ai"] is True and est["production_methods"][-1] == "text_to_video"
    assert ledger(db) == []                                                   # estimar no reserva nada


def test_client_material_preferred_by_default(lib, studio, db):
    from services.marketing_mix import suggest_mix
    assert suggest_mix(5, 3)["real_media_percent"] == 100 and suggest_mix(0, 0)["real_media_percent"] == 0


def test_http_never_exposes_internal_costs_or_providers(monkeypatch, db):
    from types import SimpleNamespace
    from fastapi.testclient import TestClient
    import main
    monkeypatch.setattr(main, "member_portal", SimpleNamespace(db=db))
    c = TestClient(main.app)
    H = {"Authorization": f"Bearer jwt-{OWNER}"}
    body = {"tenant_id": T1, "brief": {"objective": "brand", "audience": "general", "style": "calm", "duration_seconds": 15},
            "real_media_percent": 0, "ai_media_percent": 100, "quality_tier": "draft", "maximum_cost": 1}
    jid = c.post("/api/manager/marketing/jobs", json=body, headers=H).json()["id"]
    c.post(f"/api/manager/marketing/jobs/{jid}/estimate", json={"tenant_id": T1}, headers=H)
    c.post(f"/api/manager/marketing/jobs/{jid}/approve", json={"tenant_id": T1, "confirm": True}, headers=H)
    c.post(f"/api/manager/marketing/jobs/{jid}/process", json={"tenant_id": T1}, headers=H)
    texts = [c.get(f"/api/manager/marketing/jobs/{jid}?tenant_id={T1}", headers=H).text,
             c.get(f"/api/manager/marketing/jobs?tenant_id={T1}", headers=H).text,
             c.get(f"/api/manager/marketing/studio?tenant_id={T1}", headers=H).text]
    for t in texts:
        for leak in ("mock-media-v1", "selected_provider", "provider_job_id", "price_source", "billing_unit",
                     "\"provider\"", "model_id", "\"usage\":["):
            assert leak not in t, leak
    ov = c.get(f"/api/manager/marketing/studio?tenant_id={T1}", headers=H).json()
    assert set(ov["budget"]["marketing_ai"]) == {"limit_cents", "consumed_cents", "reserved_cents", "available_cents",
                                                 "warning", "available_pct"}
