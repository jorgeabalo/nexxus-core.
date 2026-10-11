"""Plan simple: 8 publicaciones incluidas y ampliaciones mensuales recurrentes de +1, +4 u +8 (o personalizadas).

El manager solicita; el owner autoriza (o un manager con permiso de compras delegado). Una ampliación cuenta
desde el mes en que se autoriza y se repite cada mes hasta que se cancela (con efecto el mes siguiente).
"""
from datetime import timedelta
from functools import lru_cache
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import main
from marketing_addon_fake import install
from services.marketing_plan import MarketingPlanService
from services.marketing_publication_plan import quote, spec
from services.member_portal import PortalError
from test_marketing import T1, T2, OWNER, MANAGER, STAFF, OTHER, NOW, FakeDB, draft, jwt

K = iter(f"addon-key-{i:010d}" for i in range(10_000))


@pytest.fixture
def db():
    return install(FakeDB())


@pytest.fixture
def svc(db):
    return MarketingPlanService(db, now=NOW)


def body(n=None, key=None, **kw):
    b = {"confirm": True, "idempotency_key": key or next(K), **kw}
    if n is not None:
        b["additional_publications"] = n
    return b


def plan(svc, who=OWNER, tenant=T1):
    return svc.dashboard(jwt(who), tenant)["publication_plan"]


def err(fn, *a):
    with pytest.raises(PortalError) as e:
        fn(*a)
    return e.value.code, e.value.status


@lru_cache(None)
def cheapest(n):
    return 0 if n == 0 else min(p + cheapest(n - s) for s, p in ((8, 3500), (4, 2000), (1, 600)) if s <= n)


# ------------------------------------------------------------------ precio y frecuencia
@pytest.mark.parametrize("additional,amount,blocks", [
    (1, 6, [(1, 1)]), (4, 20, [(4, 1)]), (8, 35, [(8, 1)]),
    (12, 55, [(8, 1), (4, 1)]), (22, 102, [(8, 2), (4, 1), (1, 2)]), (37, 166, [(8, 4), (4, 1), (1, 1)]),
])
def test_quote_uses_cheapest_exact_block_combination(additional, amount, blocks):
    q = quote(additional)
    assert q["amount"] == amount and [(b["size"], b["quantity"]) for b in q["blocks"]] == blocks


def test_every_exact_amount_is_reachable_at_the_cheapest_price():
    for n in range(1, 501):
        q = quote(n)
        assert sum(b["size"] * b["quantity"] for b in q["blocks"]) == n and round(q["amount"] * 100) == cheapest(n), n


@pytest.mark.parametrize("per_week,monthly,amount", [(1, 4, 20), (2, 8, 35), (3, 12, 55), (5, 20, 90)])
def test_weekly_frequency_is_a_monthly_quota(per_week, monthly, amount):
    s = spec({"cadence": "weekly", "per_week": per_week})
    assert (s["cadence"], s["per_week"], s["additional_publications"], s["amount"]) == ("weekly", per_week, monthly, amount)


@pytest.mark.parametrize("bad", [None, 0, -1, 501, 1.5, True, "x", "4.0"])
def test_quote_rejects_invalid_amounts(bad):
    with pytest.raises(PortalError) as e:
        quote(bad)
    assert e.value.code == "invalid_publication_addon"


@pytest.mark.parametrize("bad", [{"cadence": "weekly", "per_week": 0}, {"cadence": "weekly", "per_week": 126},
                                 {"cadence": "daily", "additional_publications": 4}, {"cadence": "weekly"}])
def test_invalid_frequency_is_rejected(bad):
    with pytest.raises(PortalError):
        spec(bad)


# ------------------------------------------------------------------ cuota única
def test_dashboard_has_one_public_quota(svc):
    p = plan(svc)
    assert {k: p[k] for k in ("included", "added", "total", "used", "available")} == \
        {"included": 8, "added": 0, "total": 8, "used": 0, "available": 8}
    assert p["offers"] == [{"size": 1, "price": 6.0, "currency": "USD"}, {"size": 4, "price": 20.0, "currency": "USD"},
                           {"size": 8, "price": 35.0, "currency": "USD"}]
    assert svc.dashboard(jwt(OWNER), T1)["limits"] == {"monthly_post_limit": 8}


def test_authorized_addon_extends_the_single_scheduling_limit(svc, db):
    for i in range(8):
        db.tables["marketing_content"].append({"id": f"used-{i}", "tenant_id": T1, "status": "scheduled",
                                               "format": ("reel", "image", "carousel", "video")[i % 4],
                                               "scheduled_at": f"2026-10-{20 + i:02d}T14:00:00Z"})
    item = draft(svc)
    svc.transition(jwt(OWNER), T1, item["id"], "review")
    svc.transition(jwt(OWNER), T1, item["id"], "approved")
    assert err(svc.transition, jwt(OWNER), T1, item["id"], "scheduled", None, "2026-10-30T09:00")[0] == "limit_monthly_post_limit"
    svc.request_addon(jwt(MANAGER), T1, body(1))                       # una solicitud aún no amplía nada
    assert err(svc.transition, jwt(OWNER), T1, item["id"], "scheduled", None, "2026-10-30T09:00")[0] == "limit_monthly_post_limit"
    svc.authorize_addon(jwt(OWNER), T1, body(1))
    assert svc.transition(jwt(OWNER), T1, item["id"], "scheduled", scheduled_at="2026-10-30T09:00")["status"] == "scheduled"


# ------------------------------------------------------------------ owner versus manager
def test_manager_requests_and_owner_authorizes(svc, db):
    assert err(svc.authorize_addon, jwt(MANAGER), T1, body(12)) == ("owner_authorization_required", 403)
    r = svc.request_addon(jwt(MANAGER), T1, body(12))
    assert r["status"] == "requested" and r["amount"] == 55 and plan(svc)["added"] == 0
    assert plan(svc, MANAGER)["can_authorize"] is False and plan(svc)["can_authorize"] is True
    assert err(svc.decide_addon, jwt(MANAGER), T1, r["id"], True, {"confirm": True}) == ("owner_authorization_required", 403)
    assert err(svc.decide_addon, jwt(OWNER), T1, r["id"], True, {}) == ("confirmation_required", 400)
    ok = svc.decide_addon(jwt(OWNER), T1, r["id"], True, {"confirm": True})
    assert ok["status"] == "authorized" and plan(svc)["added"] == 12 and plan(svc)["total"] == 20


def test_owner_can_decline_a_request(svc):
    r = svc.request_addon(jwt(MANAGER), T1, body(4))
    assert svc.decide_addon(jwt(OWNER), T1, r["id"], False, {"confirm": True})["status"] == "declined"
    assert plan(svc)["added"] == 0 and plan(svc)["pending"] == []
    assert err(svc.decide_addon, jwt(OWNER), T1, r["id"], True, {"confirm": True}) == ("addon_not_pending", 409)


def test_delegated_manager_can_authorize_until_the_owner_revokes(svc):
    assert err(svc.set_purchase_permission, jwt(MANAGER), T1, {"managers_can_authorize": True}) == ("owner_only", 403)
    svc.set_purchase_permission(jwt(OWNER), T1, {"managers_can_authorize": True})
    assert plan(svc, MANAGER)["can_authorize"] is True
    assert svc.authorize_addon(jwt(MANAGER), T1, body(4))["status"] == "authorized"
    svc.set_purchase_permission(jwt(OWNER), T1, {"managers_can_authorize": False})
    assert err(svc.authorize_addon, jwt(MANAGER), T1, body(4)) == ("owner_authorization_required", 403)


@pytest.mark.parametrize("fn", ["request_addon", "authorize_addon"])
def test_staff_is_blocked(svc, fn):
    assert err(getattr(svc, fn), jwt(STAFF), T1, body(1))[0] == "forbidden"


def test_explicit_confirmation_is_required(svc, db):
    for fn in (svc.request_addon, svc.authorize_addon):
        assert err(fn, jwt(OWNER), T1, {**body(1), "confirm": "true"}) == ("confirmation_required", 400)
    assert db.tables["marketing_publication_addons"] == []


# ------------------------------------------------------------------ recurrente hasta cancelación
def test_addon_recurs_monthly_until_cancelled_from_next_month(svc, db):
    a = svc.authorize_addon(jwt(OWNER), T1, body(cadence="weekly", per_week=2))
    assert (a["additional_publications"], a["amount"], a["cadence"], a["per_week"]) == (8, 35, "weekly", 2)
    later = MarketingPlanService(db, now=NOW + timedelta(days=62))            # dos meses después: sigue vigente
    assert later.dashboard(jwt(OWNER), T1)["publication_plan"]["added"] == 8
    c = svc.cancel_addon(jwt(OWNER), T1, a["id"], {"confirm": True})
    assert c["status"] == "cancelled" and c["ends_cycle"] == "2026-11-01"
    assert plan(svc)["added"] == 8                                             # el mes en curso se respeta
    assert later.dashboard(jwt(OWNER), T1)["publication_plan"]["added"] == 0
    assert err(svc.cancel_addon, jwt(MANAGER), T1, a["id"], {"confirm": True})[0] in ("owner_authorization_required",)


# ------------------------------------------------------------------ idempotencia y concurrencia
def test_double_click_is_idempotent_and_other_amount_conflicts(svc, db):
    b = body(22, key="addon-monthly-dbl-0001")
    a, again = svc.authorize_addon(jwt(OWNER), T1, b), svc.authorize_addon(jwt(OWNER), T1, b)
    assert a["id"] == again["id"] and a["amount"] == 102 and len(db.tables["marketing_publication_addons"]) == 1
    assert err(svc.authorize_addon, jwt(OWNER), T1, {**b, "additional_publications": 8}) == ("idempotency_conflict", 409)
    assert err(svc.authorize_addon, jwt(OWNER), T1, {**b, "cadence": "weekly", "per_week": 5}) == ("idempotency_conflict", 409)


def blind_race(db):
    """Las dos peticiones leen a la vez "no existe": solo el índice único de la base decide."""
    real, state = db.select, {"blind": True}

    def select(table, params):
        if table == "marketing_publication_addons" and "idempotency_key" in params and state["blind"]:
            state["blind"] = False
            return []
        return real(table, params)
    db.select = select


@pytest.mark.parametrize("second,expect", [(22, "same"), (8, "conflict")])
def test_concurrent_submissions_never_create_two(svc, db, second, expect):
    b = body(22, key="addon-race-00000001")
    first = svc.authorize_addon(jwt(OWNER), T1, b)
    blind_race(db)
    if expect == "same":
        assert svc.authorize_addon(jwt(OWNER), T1, b)["id"] == first["id"]
    else:
        assert err(svc.authorize_addon, jwt(OWNER), T1, {**b, "additional_publications": second}) == ("idempotency_conflict", 409)
    assert len(db.tables["marketing_publication_addons"]) == 1


def test_concurrent_approvals_decide_once(svc, db):
    r = svc.request_addon(jwt(MANAGER), T1, body(4))
    real = db.select

    def stale(table, params):                                                  # ambas leen "requested"
        rows = real(table, params)
        return [{**x, "status": "requested"} for x in rows] if table == "marketing_publication_addons" and "id" in params else rows
    db.select = stale
    one = svc.decide_addon(jwt(OWNER), T1, r["id"], True, {"confirm": True})
    db.select = real
    two = svc.decide_addon(jwt(OWNER), T1, r["id"], True, {"confirm": True})
    assert one["status"] == two["status"] == "authorized" and plan(svc)["added"] == 4


# ------------------------------------------------------------------ aislamiento entre tenants
def test_addons_and_keys_never_cross_tenants(svc, db):
    db.tables["marketing_settings"][1]["monthly_post_limit"] = 8              # T2 con plan: la cuota lee ampliaciones
    key = "addon-shared-key-0001"
    a = svc.authorize_addon(jwt(OWNER), T1, body(4, key=key))
    assert plan(svc, OTHER, T2)["added"] == 0
    b = svc.authorize_addon(jwt(OTHER), T2, body(8, key=key))
    assert a["id"] != b["id"] and b["amount"] == 35
    assert plan(svc)["added"] == 4 and plan(svc, OTHER, T2)["added"] == 8
    assert err(svc.decide_addon, jwt(OTHER), T2, a["id"], False, {"confirm": True})[0] == "not_found"
    assert err(svc.cancel_addon, jwt(OTHER), T2, a["id"], {"confirm": True})[0] == "not_found"
    # OWNER solo es manager en T2: no autoriza allí.
    assert err(svc.authorize_addon, jwt(OWNER), T2, body(1)) == ("owner_authorization_required", 403)


# ------------------------------------------------------------------ HTTP: el navegador nunca fija el precio
def test_http_flow_ignores_browser_price(monkeypatch, db):
    monkeypatch.setattr(main, "member_portal", SimpleNamespace(db=db))
    c = TestClient(main.app)
    H = lambda who: {"Authorization": f"Bearer jwt-{who}"}  # noqa: E731
    r = c.post("/api/manager/marketing/publication-addons/request", headers=H(MANAGER), json={
        "tenant_id": T1, "additional_publications": 12, "confirm": True, "idempotency_key": "addon-http-month-01",
        "amount": 0.01, "amount_cents": 1, "currency": "EUR", "status": "authorized", "decided_by": MANAGER})
    assert r.status_code == 200 and r.json()["amount"] == 55 and r.json()["status"] == "requested"
    row = db.tables["marketing_publication_addons"][0]
    assert row["amount_cents"] == 5500 and row["currency"] == "USD" and row.get("decided_by") is None
    a = c.post(f"/api/manager/marketing/publication-addons/{r.json()['id']}/approve", headers=H(OWNER),
               json={"tenant_id": T1, "confirm": True})
    assert a.status_code == 200 and a.json()["status"] == "authorized"
    assert c.post("/api/manager/marketing/publication-addons/x/approve", headers=H(OWNER),
                  json={"tenant_id": T1, "confirm": True}).status_code == 404
    assert c.post(f"/api/manager/marketing/publication-addons/{r.json()['id']}/delete", headers=H(OWNER),
                  json={"tenant_id": T1}).status_code == 404
