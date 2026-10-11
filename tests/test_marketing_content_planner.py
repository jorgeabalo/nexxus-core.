"""
Contenido → Planear contenido (PR #21): objetivo, modo (Biblioteca + IA o todo con IA), formato, instrucciones,
propuesta y revisión. La preparación interna nunca aparece en la API del tenant: solo estados comerciales.
"""
import json
import re

import pytest
from marketing_addon_fake import install
from marketing_planner_client import plan_body, planner_client
from services import marketing_content_plan as cp
from services.marketing_library import LibraryService
from test_marketing import MANAGER, NOW, OWNER, T1
from test_marketing_studio import StudioDB, no_people, png, up

P = "/api/manager/marketing"
H = {"Authorization": f"Bearer jwt-{OWNER}"}
HM = {"Authorization": f"Bearer jwt-{MANAGER}"}
TECH = re.compile(r"queued|\blease|worker|generation_job|reserved_cost|provider|\bmodel\b|strategy|attempts|"
                  r"estimated_cost|actual_cost|maximum_cost|error_code|last_error|awaiting_generation_approval|"
                  r"\bgenerating\b|traceback|exception", re.I)
COMMERCIAL = {"draft", "preparing", "ready_for_review", "changes_requested", "approved", "scheduled", "published",
              "could_not_prepare", "rejected", "archived"}


@pytest.fixture
def db():
    d = install(StudioDB())
    d.tables["marketing_settings"][0].update(ai_generation_enabled=True, monthly_ai_cost_limit=20)
    return d


@pytest.fixture
def client(monkeypatch, db):
    return planner_client(monkeypatch, db)


body = plan_body


def media(db):
    lib = LibraryService(db, now=NOW)
    m = up(lib, name="real.png", data=png(w=43))
    no_people(lib, m)
    return m


def clean(text):
    return TECH.findall(text)


# ------------------------------------------------------------------ asistente
def test_plan_creates_content_and_proposal_ready_for_review(client, db):
    r = client.post(f"{P}/content/plan", json=body(), headers=H)
    assert r.status_code == 200, r.text
    out = r.json()
    item = out["content"]
    assert item["display_status"] == "ready_for_review" and item["status"] == "review"      # nunca publicado solo
    assert item["format"] == "reel" and item["channels"] == ["instagram", "facebook"] and item["cta"] == "Llámanos hoy"
    assert "Clase de prueba gratis" in item["caption"] and item["planned_at"]
    assert out["preparation"] == "preparing" and clean(r.text) == []
    assert not [x for x in db.tables["marketing_publications"]]                                # nada programado
    assert db.tables["marketing_generation_jobs"][0]["content_id"] == item["id"]               # trabajo interno ligado


def test_same_submission_twice_returns_the_same_content(client, db):
    b = body(idempotency_key="plan-idem-0000000001")
    a = client.post(f"{P}/content/plan", json=b, headers=H).json()
    again = client.post(f"{P}/content/plan", json=b, headers=H).json()
    assert again["content"]["id"] == a["content"]["id"] and again["preparation"] == "existing"
    assert len(db.tables["marketing_content"]) == 1 and len(db.tables["marketing_generation_jobs"]) == 1


@pytest.mark.parametrize("change,code", [
    ({"objective": "brand"}, "invalid_objective"), ({"mode": "mix_50"}, "invalid_mode"),
    ({"format": "story"}, "invalid_format"), ({"format": "text"}, "invalid_format"),
    ({"mode": "library_ai"}, "real_media_required"), ({"media_ids": ["x"]}, "fully_ai_uses_no_media"),
    ({"instructions": {"platforms": []}}, "channels_required"), ({"instructions": {"tone": "angry"}}, "invalid_tone")])
def test_plan_validation(client, db, change, code):
    r = client.post(f"{P}/content/plan", json=body(**change), headers=H)
    assert r.status_code == 400 and r.json() == {"error": code}
    assert db.tables["marketing_content"] == []


def test_only_two_modes_and_four_formats():
    assert cp.MODES == ("library_ai", "fully_ai")
    assert cp.FORMATS == ("image", "carousel", "reel", "video")
    assert cp.OBJECTIVES == ("calls", "messages", "trial_visit", "memberships", "reactivation", "reviews")


def test_library_mode_uses_current_files_only(client, db):
    m = media(db)
    r = client.post(f"{P}/content/plan", json=body(mode="library_ai", media_ids=[m["id"]]), headers=H)
    assert r.status_code == 200, r.text
    job = db.tables["marketing_generation_jobs"][0]
    assert [i["media_id"] for i in db.tables["marketing_generation_inputs"] if i["job_id"] == job["id"]] == [m["id"]]
    next(x for x in db.tables["marketing_media"] if x["id"] == m["id"])["expires_at"] = "2026-01-01T00:00:00Z"
    r2 = client.post(f"{P}/content/plan", json=body(mode="library_ai", media_ids=[m["id"]]), headers=H)
    assert r2.status_code >= 400 and clean(r2.text) == []


def test_without_ai_generation_the_content_stays_as_draft(client, db):
    db.tables["marketing_settings"][0].update(ai_generation_enabled=False, monthly_ai_cost_limit=0)
    r = client.post(f"{P}/content/plan", json=body(), headers=H)
    assert r.status_code == 200 and r.json()["preparation"] == "unavailable"
    assert r.json()["content"]["display_status"] == "draft" and clean(r.text) == []


# ------------------------------------------------------------------ estados comerciales y reintento
def test_failed_preparation_shows_could_not_prepare_and_can_retry(client, db, monkeypatch):
    from services.marketing_studio import StudioService
    with monkeypatch.context() as m:
        m.setattr(StudioService, "process_job", lambda *a, **k: None)                 # queda en preparación
        item = client.post(f"{P}/content/plan", json=body(), headers=H).json()["content"]
    assert item["display_status"] == "preparing" and 0 < item["progress"] < 100
    job = db.tables["marketing_generation_jobs"][0]
    job.update(status="failed", error_code="provider_timeout")
    d = client.get(f"{P}/content/{item['id']}?tenant_id={T1}", headers=H)
    assert d.json()["content"]["display_status"] == "could_not_prepare" and d.json()["can_retry"] is True
    assert "provider_timeout" not in d.text and clean(d.text) == []
    r = client.post(f"{P}/content/{item['id']}/retry", json={"tenant_id": T1}, headers=H)
    assert r.status_code == 200, r.text
    assert r.json()["content"]["display_status"] == "ready_for_review"
    jobs = db.tables["marketing_generation_jobs"]
    assert len(jobs) == 2 and jobs[1]["regeneration_of"] == job["id"]                # el anterior se conserva
    again = client.post(f"{P}/content/{item['id']}/retry", json={"tenant_id": T1}, headers=H)
    assert again.status_code == 409 and again.json() == {"error": "not_retryable"}


def test_review_actions_need_comments_and_scheduling_needs_approval(client, db):
    cid = client.post(f"{P}/content/plan", json=body(), headers=H).json()["content"]["id"]
    tr = lambda to, **kw: client.post(f"{P}/content/{cid}/transition", json={"tenant_id": T1, "to": to, **kw}, headers=H)  # noqa: E731
    assert tr("scheduled", scheduled_at="2026-10-25T10:00").status_code == 409          # sin aprobar: no se programa
    for to in ("draft", "rejected"):
        r = tr(to)
        assert r.status_code == 400 and r.json() == {"error": "comment_required"}
    assert tr("draft", comment="Cambia la foto").status_code == 200
    d = client.get(f"{P}/content/{cid}?tenant_id={T1}", headers=H).json()
    assert d["content"]["display_status"] == "changes_requested"
    assert [h["comment"] for h in d["history"] if h["to_status"] == "draft"][-1] == "Cambia la foto"   # historial


def test_every_display_status_is_commercial(client, db):
    client.post(f"{P}/content/plan", json=body(), headers=H)
    for path in ("content", "dashboard", "calendar?start=2026-10-01&end=2026-10-31"):
        sep = "&" if "?" in path else "?"
        r = client.get(f"{P}/{path}{sep}tenant_id={T1}", headers=H)
        assert r.status_code == 200 and clean(r.text) == [], (path, clean(r.text))
        found = set(re.findall(r'"display_status":\s*"([a-z_]+)"', r.text))
        assert found <= COMMERCIAL and (found or path == "dashboard"), (path, found)
        statuses = set(re.findall(r'"(?:status|from_status|to_status)":\s*"([a-z_]+)"', r.text))
        assert "generating" not in statuses, (path, statuses)


def test_dashboard_summary_is_commercial(client, db):
    client.post(f"{P}/content/plan", json=body(), headers=H)
    d = client.get(f"{P}/dashboard?tenant_id={T1}", headers=H).json()
    assert d["counts"]["ready_for_review"] == 1 and d["counts"]["waiting_approval"] == 1
    assert {"used", "included", "total"} <= set(d["publication_plan"])
    assert [x["code"] for x in d["recommendations"]][0] == "review_ready"
    assert clean(json.dumps(d)) == []


# ------------------------------------------------------------------ roles y aislamiento
@pytest.mark.parametrize("who", ["staff", "member", "other"])
def test_staff_members_and_other_tenants_cannot_plan_or_open(client, db, who):
    from test_marketing import STAFF, MEMBER, OTHER
    uid = {"staff": STAFF, "member": MEMBER, "other": OTHER}[who]
    hx = {"Authorization": f"Bearer jwt-{uid}"}
    cid = client.post(f"{P}/content/plan", json=body(), headers=H).json()["content"]["id"]
    assert client.post(f"{P}/content/plan", json=body(), headers=hx).status_code == 403
    assert client.get(f"{P}/content/{cid}?tenant_id={T1}", headers=hx).status_code == 403
    assert client.post(f"{P}/content/{cid}/retry", json={"tenant_id": T1}, headers=hx).status_code == 403
    assert len(db.tables["marketing_content"]) == 1


def test_manager_can_plan_like_the_owner(client, db):
    r = client.post(f"{P}/content/plan", json=body(), headers=HM)
    assert r.status_code == 200 and r.json()["content"]["display_status"] == "ready_for_review"


# ------------------------------------------------------------------ rutas técnicas retiradas
@pytest.mark.parametrize("method,path", [("GET", "studio"), ("POST", "studio/mix-preview"), ("GET", "jobs"),
                                         ("POST", "jobs"), ("GET", "jobs/00000000-0000-0000-0000-000000000001"),
                                         ("POST", "jobs/00000000-0000-0000-0000-000000000001/approve")])
def test_technical_routes_are_gone(client, method, path):
    r = client.request(method, f"{P}/{path}?tenant_id={T1}", json={"tenant_id": T1}, headers=H)
    assert r.status_code in (404, 405)


def test_publication_queue_shows_only_commercial_states(client, db):
    """Programado manualmente (sin preparación con IA): la cola nunca muestra estados ni datos de envío internos."""
    cid = client.post(f"{P}/content", json={"tenant_id": T1, "title": "Post", "format": "image",
                                            "channels": ["instagram", "facebook"]}, headers=H).json()["id"]
    tr = lambda to, **kw: client.post(f"{P}/content/{cid}/transition", json={"tenant_id": T1, "to": to, **kw}, headers=H)  # noqa: E731
    assert tr("review").status_code == 200 and tr("approved").status_code == 200
    assert tr("scheduled", scheduled_at="2030-01-10T10:00").status_code == 200
    for p in db.tables["marketing_publications"]:
        p.update(status="queued", attempts=2, last_error="TimeoutError", provider="postiz")    # como lo dejaría el envío
    d = client.get(f"{P}/content/{cid}?tenant_id={T1}", headers=H)
    pubs = d.json()["publications"]
    assert pubs and all(set(p) == {"id", "channel", "status", "scheduled_at", "published_at"} for p in pubs)
    assert {p["status"] for p in pubs} == {"scheduled"}
    assert d.json()["content"]["display_status"] == "scheduled" and clean(d.text) == []


def test_disabled_marketing_cannot_plan_or_retry(client, db):
    cid = client.post(f"{P}/content/plan", json=body(), headers=H).json()["content"]["id"]
    db.tables["marketing_settings"][0]["marketing_enabled"] = False
    r = client.post(f"{P}/content/plan", json=body(), headers=H)
    assert r.status_code == 403 and r.json() == {"error": "marketing_disabled"}
    assert client.post(f"{P}/content/{cid}/retry", json={"tenant_id": T1}, headers=H).status_code == 403
    assert len(db.tables["marketing_content"]) == 1 and len(db.tables["marketing_generation_jobs"]) == 1
