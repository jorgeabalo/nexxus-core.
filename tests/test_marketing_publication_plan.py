"""Plan simple: 8 publicaciones y ampliaciones mensuales de 1, 4 u 8."""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import main
from services.marketing_plan import MarketingPlanService
from services.marketing_publication_plan import quote
from services.member_portal import PortalError
from test_marketing import T1, T2, OWNER, MANAGER, STAFF, NOW, FakeDB, draft, jwt


@pytest.fixture
def db():
    d = FakeDB()
    d.tables["marketing_publication_addons"] = []
    return d


@pytest.fixture
def svc(db):
    return MarketingPlanService(db, now=NOW)


@pytest.mark.parametrize("additional,amount,blocks", [
    (1, 6, [(1, 1)]), (4, 20, [(4, 1)]), (8, 35, [(8, 1)]),
    (22, 102, [(8, 2), (4, 1), (1, 2)]), (37, 166, [(8, 4), (4, 1), (1, 1)]),
])
def test_quote_uses_cheapest_exact_block_combination(additional, amount, blocks):
    q = quote(additional)
    assert q["amount"] == amount
    assert [(b["size"], b["quantity"]) for b in q["blocks"]] == blocks


@pytest.mark.parametrize("bad", [None, 0, -1, 501, 1.5, True, "x"])
def test_quote_rejects_invalid_amounts(bad):
    with pytest.raises(PortalError) as e:
        quote(bad)
    assert e.value.code == "invalid_publication_addon"


def test_dashboard_has_one_public_quota(svc):
    d = svc.dashboard(jwt(OWNER), T1)
    assert d["publication_plan"] == {"included": 8, "added": 0, "total": 8, "used": 0, "available": 8,
                                      "offers": [{"size": 1, "price": 6.0, "currency": "USD"},
                                                 {"size": 4, "price": 20.0, "currency": "USD"},
                                                 {"size": 8, "price": 35.0, "currency": "USD"}]}
    assert d["limits"] == {"monthly_post_limit": 8}


def test_owner_and_manager_authorize_and_double_click_is_idempotent(svc, db):
    a = svc.authorize_addon(jwt(OWNER), T1, {"additional_publications": 22, "confirm": True,
                                             "idempotency_key": "addon-monthly-0001"})
    again = svc.authorize_addon(jwt(OWNER), T1, {"additional_publications": 22, "confirm": True,
                                                 "idempotency_key": "addon-monthly-0001"})
    svc.authorize_addon(jwt(MANAGER), T1, {"additional_publications": 15, "confirm": True,
                                           "idempotency_key": "addon-monthly-0002"})
    assert a["id"] == again["id"] and a["amount"] == 102
    assert len(db.tables["marketing_publication_addons"]) == 2
    assert svc.dashboard(jwt(OWNER), T1)["publication_plan"]["total"] == 45


def test_same_idempotency_key_cannot_authorize_a_different_total(svc, db):
    body = {"additional_publications": 1, "confirm": True, "idempotency_key": "addon-monthly-conflict"}
    svc.authorize_addon(jwt(OWNER), T1, body)
    with pytest.raises(PortalError) as e:
        svc.authorize_addon(jwt(OWNER), T1, {**body, "additional_publications": 8})
    assert e.value.code == "idempotency_conflict" and e.value.status == 409
    assert len(db.tables["marketing_publication_addons"]) == 1


def test_authorization_requires_confirmation_and_role(svc):
    body = {"additional_publications": 1, "idempotency_key": "addon-monthly-0003"}
    with pytest.raises(PortalError) as e:
        svc.authorize_addon(jwt(OWNER), T1, body)
    assert e.value.code == "confirmation_required"
    with pytest.raises(PortalError) as e:
        svc.authorize_addon(jwt(STAFF), T1, {**body, "confirm": True})
    assert e.value.code == "forbidden"


def test_addon_is_monthly_and_isolated_by_tenant(svc, db):
    svc.authorize_addon(jwt(OWNER), T1, {"additional_publications": 4, "confirm": True,
                                         "idempotency_key": "addon-monthly-0004"})
    assert svc.dashboard(jwt(OWNER), T1)["publication_plan"]["added"] == 4
    assert svc.dashboard(jwt(OWNER), T2)["publication_plan"]["added"] == 0
    db.tables["marketing_publication_addons"][0]["cycle_start"] = "2026-09-01"
    assert svc.dashboard(jwt(OWNER), T1)["publication_plan"]["added"] == 0


def test_addon_extends_the_single_scheduling_limit(svc, db):
    for i in range(8):
        db.tables["marketing_content"].append({
            "id": f"used-{i}", "tenant_id": T1, "status": "scheduled", "format": "reel",
            "scheduled_at": f"2026-10-{20 + i:02d}T14:00:00Z"})
    item = draft(svc)
    svc.transition(jwt(OWNER), T1, item["id"], "review")
    svc.transition(jwt(OWNER), T1, item["id"], "approved")
    with pytest.raises(PortalError) as e:
        svc.transition(jwt(OWNER), T1, item["id"], "scheduled", scheduled_at="2026-10-30T09:00")
    assert e.value.code == "limit_monthly_post_limit"
    svc.authorize_addon(jwt(OWNER), T1, {"additional_publications": 1, "confirm": True,
                                         "idempotency_key": "addon-monthly-0005"})
    assert svc.transition(jwt(OWNER), T1, item["id"], "scheduled", scheduled_at="2026-10-30T09:00")["status"] == "scheduled"


def test_http_authorization_records_only_confirmed_request(monkeypatch, db):
    monkeypatch.setattr(main, "member_portal", SimpleNamespace(db=db))
    c = TestClient(main.app)
    headers = {"Authorization": f"Bearer jwt-{OWNER}"}
    body = {"tenant_id": T1, "additional_publications": 37, "confirm": True,
            "idempotency_key": "addon-http-month-01"}
    r = c.post("/api/manager/marketing/publication-addons/authorize", json=body, headers=headers)
    assert r.status_code == 200 and r.json()["amount"] == 166 and r.json()["additional_publications"] == 37
    assert len(db.tables["marketing_publication_addons"]) == 1
