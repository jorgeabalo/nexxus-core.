"""
Plan simple de Marketing (PR #21), revisión: las dos modalidades, la cuota única, la combinación más económica
para cualquier total exacto, el doble clic simultáneo y lo que la API del tenant NO debe devolver.
"""
from functools import lru_cache
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import main
from services.marketing_ai_router import MarketingAIRouter, MockProvider, OmniRouteAdapter, DisabledLocalTools
from services.marketing_library import LibraryService
from services.marketing_plan import MarketingPlanService
from services.marketing_publication_plan import quote
from services.marketing_studio import StudioService
from services.member_portal import PortalError
from test_marketing import NOW, OWNER, T1
from test_marketing_studio import StudioDB, jwt, no_people, png, up

H = {"Authorization": f"Bearer jwt-{OWNER}"}
BRIEF = {"objective": "brand", "audience": "general", "style": "calm", "duration_seconds": 15}
ADAPTERS = {"mock": MockProvider(), "omniroute": OmniRouteAdapter(env={}), "local_ffmpeg": DisabledLocalTools()}


@pytest.fixture
def db():
    d = StudioDB()
    d.tables.setdefault("marketing_publication_addons", [])
    return d


@pytest.fixture
def studio(db):
    return StudioService(db, now=NOW, router=MarketingAIRouter(env={}), adapters=ADAPTERS)


@pytest.fixture
def plan(db):
    return MarketingPlanService(db, now=NOW)


@lru_cache(None)
def cheapest(n):
    return 0 if n == 0 else min(p + cheapest(n - s) for s, p in ((8, 3500), (4, 2000), (1, 600)) if s <= n)


# ------------------------------------------------------------------ precio
def test_every_exact_total_uses_the_cheapest_combination():
    for n in range(1, 501):                                       # +1 permite llegar a cualquier cantidad exacta
        q = quote(n)
        assert sum(b["size"] * b["quantity"] for b in q["blocks"]) == n
        assert round(q["amount"] * 100) == cheapest(n), n
    assert quote(22)["amount"] == 102 and quote(37)["amount"] == 166


def test_browser_cannot_send_the_price(monkeypatch, db):
    monkeypatch.setattr(main, "member_portal", SimpleNamespace(db=db))
    r = TestClient(main.app).post("/api/manager/marketing/publication-addons/authorize", headers=H, json={
        "tenant_id": T1, "additional_publications": 22, "confirm": True, "idempotency_key": "addon-price-000001",
        "amount": 0.01, "amount_cents": 1, "blocks_1": 22, "currency": "EUR"})
    assert r.status_code == 200 and r.json()["amount"] == 102 and r.json()["currency"] == "USD"
    row = db.tables["marketing_publication_addons"][0]
    assert not {"amount", "amount_cents", "blocks_1", "currency"} & set(row)       # lo calcula la base


# ------------------------------------------------------------------ doble clic simultáneo
def race(db):
    """Dos peticiones con la misma clave leen a la vez "no existe"; la base (índice único) deja entrar a una."""
    real_select, real_insert = db.select, db.insert
    state = {"blind": True}

    def select(table, params):
        if table == "marketing_publication_addons" and "idempotency_key" in params and state["blind"]:
            state["blind"] = False
            return []
        return real_select(table, params)

    def insert(table, row):
        if table == "marketing_publication_addons" and any(
                r["tenant_id"] == row["tenant_id"] and r["idempotency_key"] == row["idempotency_key"] for r in db.tables[table]):
            raise RuntimeError("Supabase POST -> 409: duplicate key value violates unique constraint")
        return real_insert(table, row)
    db.select, db.insert = select, insert


@pytest.mark.parametrize("second,expect", [(22, "same"), (8, "conflict")])
def test_simultaneous_double_click_never_creates_two_or_fails(plan, db, second, expect):
    body = {"additional_publications": 22, "confirm": True, "idempotency_key": "addon-race-0000001"}
    first = plan.authorize_addon(jwt(OWNER), T1, body)
    race(db)
    if expect == "same":
        assert plan.authorize_addon(jwt(OWNER), T1, body)["id"] == first["id"]
    else:
        with pytest.raises(PortalError) as e:
            plan.authorize_addon(jwt(OWNER), T1, {**body, "additional_publications": second})
        assert e.value.code == "idempotency_conflict" and e.value.status == 409
    assert len(db.tables["marketing_publication_addons"]) == 1


# ------------------------------------------------------------------ las dos modalidades
def media(db):
    lib = LibraryService(db, now=NOW)
    m = up(lib, name="real.png", data=png(w=41))
    no_people(lib, m)
    return m


def test_fully_ai_never_uses_real_files(studio, db):
    m = media(db)
    for extra in ({"media_ids": [m["id"]]}, {"derivative_ids": ["d1"]}):
        with pytest.raises(PortalError) as e:
            studio.create_job(jwt(OWNER), T1, {"brief": BRIEF, "creation_mode": "fully_ai", "quality_tier": "draft", **extra})
        assert e.value.code == "fully_ai_uses_no_media"
    j = studio.create_job(jwt(OWNER), T1, {"brief": BRIEF, "creation_mode": "fully_ai", "quality_tier": "draft"})
    assert [i for i in db.tables["marketing_generation_inputs"] if i["job_id"] == j["id"]] == []


def test_library_ai_requires_allowed_material(studio, db):
    with pytest.raises(PortalError) as e:
        studio.create_job(jwt(OWNER), T1, {"brief": BRIEF, "creation_mode": "library_ai", "quality_tier": "draft"})
    assert e.value.code == "real_media_required"
    m = media(db)
    j = studio.create_job(jwt(OWNER), T1, {"brief": BRIEF, "creation_mode": "library_ai", "quality_tier": "draft",
                                           "media_ids": [m["id"]]})
    assert [i["media_id"] for i in db.tables["marketing_generation_inputs"] if i["job_id"] == j["id"]] == [m["id"]]


def test_both_modes_count_the_same_against_the_monthly_quota(studio, plan, db):
    m = media(db)
    formats = []
    for extra in ({"creation_mode": "library_ai", "media_ids": [m["id"]]}, {"creation_mode": "fully_ai"}):
        j = studio.create_job(jwt(OWNER), T1, {"brief": BRIEF, "quality_tier": "draft", **extra})
        studio.estimate_job(jwt(OWNER), T1, j["id"])
        studio.approve_job(jwt(OWNER), T1, j["id"], True)
        studio.process_job(jwt(OWNER), T1, j["id"])
        formats.append(studio.send_to_approval(jwt(OWNER), T1, j["id"], "Reel")["content"]["format"])
    assert formats == ["reel", "reel"]                                # mismo tipo de publicación en las dos
    # La cuota cuenta publicaciones programadas, 1 cada una, sea cual sea el formato o la modalidad.
    for i, fmt in enumerate(("reel", "image", "carousel", "reel")):
        db.tables["marketing_content"].append({"id": f"s{i}", "tenant_id": T1, "status": "scheduled", "format": fmt,
                                               "scheduled_at": f"2026-10-2{i}T09:00:00Z"})
    p = plan.dashboard(jwt(OWNER), T1)["publication_plan"]
    assert p["used"] == 4 and p["available"] == p["total"] - 4


# ------------------------------------------------------------------ lo que el tenant no ve
def test_tenant_api_has_no_percentages_ai_budget_costs_or_per_format_limits(monkeypatch, db):
    monkeypatch.setattr(main, "member_portal", SimpleNamespace(db=db))
    c = TestClient(main.app)
    ov = c.get(f"/api/manager/marketing/studio?tenant_id={T1}", headers=H).json()
    assert set(ov) == {"generation_enabled", "publication_plan"}
    d = c.get(f"/api/manager/marketing/dashboard?tenant_id={T1}", headers=H).json()
    assert d["usage"] == {"posts": 0} and d["limits"] == {"monthly_post_limit": 8}
    assert not {"monthly_image_limit", "monthly_reel_limit", "monthly_ai_cost_limit"} & set(d["settings"])
    j = c.post("/api/manager/marketing/jobs", headers=H, json={"tenant_id": T1, "brief": BRIEF, "creation_mode": "fully_ai",
                                                                "quality_tier": "draft"}).json()
    c.post(f"/api/manager/marketing/jobs/{j['id']}/estimate", headers=H, json={"tenant_id": T1})
    for r in (c.get(f"/api/manager/marketing/jobs/{j['id']}?tenant_id={T1}", headers=H),
              c.get(f"/api/manager/marketing/jobs?tenant_id={T1}", headers=H)):
        assert "percent" not in r.text and "mix_confirmed" not in r.text and "cost" not in r.text, r.text[:300]
    assert c.get(f"/api/manager/marketing/jobs/{j['id']}?tenant_id={T1}", headers=H).json()["creation_mode"] == "fully_ai"


# ------------------------------------------------------------------ aislamiento entre tenants (con T2 configurado)
def test_addons_and_keys_never_cross_tenants(db):
    from test_marketing import T2
    t2 = next((s for s in db.tables["marketing_settings"] if s["tenant_id"] == T2), None)
    if t2 is None:
        t2 = {**db.tables["marketing_settings"][0], "tenant_id": T2}
        db.tables["marketing_settings"].append(t2)
    t2["monthly_post_limit"] = 8                                  # T2 con plan: la cuota sí lee ampliaciones
    plan = MarketingPlanService(db, now=NOW)
    key = "addon-shared-key-0001"
    a = plan.authorize_addon(jwt(OWNER), T1, {"additional_publications": 4, "confirm": True, "idempotency_key": key})
    assert plan.dashboard(jwt(OWNER), T2)["publication_plan"]["added"] == 0
    b = plan.authorize_addon(jwt(OWNER), T2, {"additional_publications": 8, "confirm": True, "idempotency_key": key})
    assert a["id"] != b["id"] and b["amount"] == 35                # misma clave en otro tenant: autorización propia
    assert plan.dashboard(jwt(OWNER), T1)["publication_plan"]["added"] == 4
    assert plan.dashboard(jwt(OWNER), T2)["publication_plan"]["added"] == 8
