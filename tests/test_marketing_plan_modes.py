"""
Plan simple de Marketing (PR #21): las dos modalidades de creación, la cuota única y lo que la API del tenant
NO debe devolver. Las ampliaciones se prueban en tests/test_marketing_publication_plan.py.
"""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import main
from services.marketing_ai_router import MarketingAIRouter, MockProvider, OmniRouteAdapter, DisabledLocalTools
from services.marketing_library import LibraryService
from marketing_addon_fake import install
from services.marketing_plan import MarketingPlanService
from services.marketing_studio import StudioService
from services.member_portal import PortalError
from test_marketing import NOW, OWNER, T1
from test_marketing_studio import StudioDB, jwt, no_people, png, up

H = {"Authorization": f"Bearer jwt-{OWNER}"}
BRIEF = {"objective": "brand", "audience": "general", "style": "calm", "duration_seconds": 15}
ADAPTERS = {"mock": MockProvider(), "omniroute": OmniRouteAdapter(env={}), "local_ffmpeg": DisabledLocalTools()}


@pytest.fixture
def db():
    return install(StudioDB())


@pytest.fixture
def studio(db):
    return StudioService(db, now=NOW, router=MarketingAIRouter(env={}), adapters=ADAPTERS)


@pytest.fixture
def plan(db):
    return MarketingPlanService(db, now=NOW)


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
