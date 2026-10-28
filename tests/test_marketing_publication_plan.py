"""Plan simple: 8 publicaciones incluidas y UNA ampliación mensual recurrente (+1, +4, +8 o personalizada).

* Límite efectivo del ciclo = incluidas + ampliación vigente en ese ciclo; se aplica al programar según el mes de
  la publicación (zona del tenant). Un cambio reemplaza la cantidad del próximo ciclo, nunca se acumula.
* El manager solicita; el owner autoriza, rechaza o cancela (o un manager con permiso delegado).
* La autorización deja un registro `authorized_uninvoiced` con su línea de factura e historial; no se cobra.
"""
from datetime import datetime, timedelta, timezone
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


def at(db, when):
    """Mueve el reloj del servicio y de la base a la vez."""
    db.addon_now = when
    return MarketingPlanService(db, now=when)


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


def scheduled(db, n, when="2026-10-{d:02d}T14:00:00Z", start=16, tenant=T1, fmt=("reel", "image", "carousel", "video")):
    for i in range(n):
        db.tables["marketing_content"].append({"id": f"s-{tenant[:4]}-{when[:7]}-{i}", "tenant_id": tenant, "status": "scheduled",
                                               "format": fmt[i % len(fmt)], "scheduled_at": when.format(d=start + i % 12)})


def schedule_one(svc, when="2026-10-30T09:00"):
    item = draft(svc)
    svc.transition(jwt(OWNER), T1, item["id"], "review")
    svc.transition(jwt(OWNER), T1, item["id"], "approved")
    return lambda: svc.transition(jwt(OWNER), T1, item["id"], "scheduled", scheduled_at=when)


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
def test_weekly_is_only_a_distribution_of_a_monthly_quota(per_week, monthly, amount):
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


# ------------------------------------------------------------------ límite efectivo al programar
@pytest.mark.parametrize("extra,limit", [(None, 8), (1, 9), (4, 12), (8, 16), (12, 20)])
def test_effective_limit_is_what_scheduling_enforces(svc, db, extra, limit):
    if extra:
        svc.authorize_addon(jwt(OWNER), T1, body(extra))
    assert plan(svc)["total"] == limit
    scheduled(db, limit - 1)
    assert schedule_one(svc)()["status"] == "scheduled"                      # el último que cabe
    assert err(schedule_one(svc))[0] == "limit_monthly_post_limit"            # uno más, nunca


def test_a_weekly_addon_never_gives_more_than_its_monthly_quota(svc, db):
    svc.authorize_addon(jwt(OWNER), T1, body(cadence="weekly", per_week=2))   # 2/sem = 8 al mes, aunque haya 5 semanas
    assert plan(svc)["total"] == 16
    scheduled(db, 16, when="2026-10-{d:02d}T14:00:00Z", start=1)
    assert err(schedule_one(svc, "2026-10-31T09:00"))[0] == "limit_monthly_post_limit"


def test_scheduling_uses_the_cycle_of_the_publication_date(svc, db):
    svc.authorize_addon(jwt(OWNER), T1, body(4))                               # octubre: 12
    later = at(db, NOW + timedelta(days=1))
    later.authorize_addon(jwt(OWNER), T1, body(12))                            # cambio: noviembre 20
    assert plan(later)["total"] == 12 and plan(later)["next_total"] == 20
    scheduled(db, 12)                                                          # octubre lleno
    assert err(schedule_one(later, "2026-10-31T09:00"))[0] == "limit_monthly_post_limit"
    scheduled(db, 19, when="2026-11-{d:02d}T14:00:00Z", start=1)
    assert schedule_one(later, "2026-11-20T09:00")()["status"] == "scheduled"  # noviembre: 20 publicaciones


def test_month_boundary_in_the_tenant_timezone(svc, db):
    # 31 oct 22:00 en Chicago = 1 nov 03:00 UTC: sigue siendo el ciclo de octubre para el tenant.
    late = at(db, datetime(2026, 11, 1, 3, 0, tzinfo=timezone.utc))
    a = late.authorize_addon(jwt(OWNER), T1, body(4))
    assert a["starts_cycle"] == "2026-10-01" and plan(late)["cycle_start"] == "2026-10-01"
    nov = at(db, datetime(2026, 11, 1, 6, 0, tzinfo=timezone.utc))            # 1 nov 01:00 en Chicago
    assert plan(nov)["cycle_start"] == "2026-11-01" and plan(nov)["total"] == 12   # recurrente: sigue en noviembre


# ------------------------------------------------------------------ una vigente: cambio, cancelación, repetición
def test_a_change_replaces_the_next_cycle_and_never_accumulates(svc, db):
    a = svc.authorize_addon(jwt(OWNER), T1, body(4))
    assert a["state"] == "active" and plan(svc)["total"] == 12
    b = svc.authorize_addon(jwt(OWNER), T1, body(12))
    assert b["state"] == "upcoming" and b["starts_cycle"] == "2026-11-01"
    p = plan(svc)
    assert p["total"] == 12 and p["next_total"] == 20 and p["current"]["state"] == "changing"
    c = svc.authorize_addon(jwt(OWNER), T1, body(8))                           # otro cambio en el mismo mes
    p = plan(svc)
    assert p["total"] == 12 and p["next_total"] == 16 and p["upcoming"]["id"] == c["id"]
    nov = at(db, NOW + timedelta(days=20))
    assert plan(nov)["total"] == 16                                            # nunca 8 + 4 + 12 + 8
    effective = [r for r in db.tables["marketing_publication_addons"] if r["status"] == "authorized_uninvoiced"]
    assert len(effective) == 1


def test_same_amount_again_creates_nothing(svc, db):
    svc.authorize_addon(jwt(OWNER), T1, body(12))
    again = svc.authorize_addon(jwt(OWNER), T1, body(12))                      # otra clave, misma cantidad
    assert again["result"] == "unchanged" and len(db.tables["marketing_publication_addons"]) == 1


def test_cancellation_keeps_the_cycle_and_returns_to_8_next_month(svc, db):
    a = svc.authorize_addon(jwt(OWNER), T1, body(12))
    assert err(svc.cancel_addon, jwt(MANAGER), T1, a["id"], {"confirm": True})[0] == "owner_authorization_required"
    assert err(svc.cancel_addon, jwt(OWNER), T1, a["id"], {})[0] == "confirmation_required"
    c = svc.cancel_addon(jwt(OWNER), T1, a["id"], {"confirm": True})
    assert c["state"] == "cancel_scheduled" and c["ends_cycle"] == "2026-11-01" and plan(svc)["total"] == 20
    assert plan(svc)["next_total"] == 8
    assert plan(at(db, NOW + timedelta(days=20)))["total"] == 8
    assert svc.cancel_addon(jwt(OWNER), T1, a["id"], {"confirm": True})["result"] == "existing"   # doble clic


# ------------------------------------------------------------------ owner versus manager
def test_manager_requests_and_owner_authorizes_or_declines(svc, db):
    assert err(svc.authorize_addon, jwt(MANAGER), T1, body(12)) == ("owner_authorization_required", 403)
    r = svc.request_addon(jwt(MANAGER), T1, body(12))
    assert r["state"] == "pending" and r["amount"] == 55 and plan(svc)["total"] == 8
    assert plan(svc, MANAGER)["can_authorize"] is False and plan(svc)["can_authorize"] is True
    assert err(svc.decide_addon, jwt(MANAGER), T1, r["id"], True, {"confirm": True}) == ("owner_authorization_required", 403)
    assert err(svc.decide_addon, jwt(OWNER), T1, r["id"], True, {}) == ("confirmation_required", 400)
    assert svc.decide_addon(jwt(OWNER), T1, r["id"], True, {"confirm": True})["state"] == "active"
    assert plan(svc)["total"] == 20
    d = svc.request_addon(jwt(MANAGER), T1, body(4))
    assert svc.decide_addon(jwt(OWNER), T1, d["id"], False, {"confirm": True})["state"] == "declined"
    assert err(svc.decide_addon, jwt(OWNER), T1, d["id"], True, {"confirm": True}) == ("addon_not_pending", 409)
    assert plan(svc)["total"] == 20 and plan(svc)["pending"] == []


def test_delegated_manager_can_authorize_until_the_owner_revokes(svc):
    assert err(svc.set_purchase_permission, jwt(MANAGER), T1, {"managers_can_authorize": True}) == ("owner_only", 403)
    svc.set_purchase_permission(jwt(OWNER), T1, {"managers_can_authorize": True})
    assert plan(svc, MANAGER)["can_authorize"] is True
    assert svc.authorize_addon(jwt(MANAGER), T1, body(4))["state"] == "active"
    svc.set_purchase_permission(jwt(OWNER), T1, {"managers_can_authorize": False})
    assert err(svc.authorize_addon, jwt(MANAGER), T1, body(4)) == ("owner_authorization_required", 403)


@pytest.mark.parametrize("fn", ["request_addon", "authorize_addon"])
def test_staff_is_blocked(svc, fn):
    assert err(getattr(svc, fn), jwt(STAFF), T1, body(1))[0] == "forbidden"


def test_staff_cannot_read_the_plan(svc):
    assert err(svc.dashboard, jwt(STAFF), T1)[0] == "forbidden"


def test_explicit_confirmation_is_required(svc, db):
    for fn in (svc.request_addon, svc.authorize_addon):
        assert err(fn, jwt(OWNER), T1, {**body(1), "confirm": "true"}) == ("confirmation_required", 400)
    assert db.tables["marketing_publication_addons"] == []


# ------------------------------------------------------------------ facturación: registro inequívoco, sin cobro
def test_authorization_creates_an_uninvoiced_billing_record_with_history(svc, db):
    r = svc.request_addon(jwt(MANAGER), T1, body(12, key="addon-billing-000001"))
    svc.decide_addon(jwt(OWNER), T1, r["id"], True, {"confirm": True})
    row = db.tables["marketing_publication_addons"][0]
    assert row["status"] == "authorized_uninvoiced"                          # autorizado, sin factura ni pago
    assert (row["tenant_id"], row["authorized_by"], row["additional_publications"], row["amount_cents"]) == (T1, OWNER, 12, 5500)
    assert (row["blocks_8"], row["blocks_4"], row["blocks_1"]) == (1, 1, 0) and row["idempotency_key"] == "addon-billing-000001"
    assert row["authorized_at"] and row["starts_cycle"] == "2026-10-01" and row["ends_cycle"] is None
    assert row["billing_line"] == "Publicaciones adicionales de Marketing: 12 por mes — $55"
    events = [(e["event"], e["actor_id"]) for e in db.tables["marketing_publication_addon_events"]]
    assert events == [("requested", MANAGER), ("authorized", OWNER)]
    out = svc.dashboard(jwt(OWNER), T1)["publication_plan"]
    assert "authorized_uninvoiced" not in str(out) and "billing_line" not in str(out) and "amount_cents" not in str(out)


# ------------------------------------------------------------------ idempotencia y concurrencia
def test_double_click_is_idempotent_and_other_amount_conflicts(svc, db):
    b = body(22, key="addon-monthly-dbl-0001")
    a, again = svc.authorize_addon(jwt(OWNER), T1, b), svc.authorize_addon(jwt(OWNER), T1, b)
    assert a["id"] == again["id"] and again["result"] == "existing" and a["amount"] == 102
    assert len(db.tables["marketing_publication_addons"]) == 1
    assert err(svc.authorize_addon, jwt(OWNER), T1, {**b, "additional_publications": 8}) == ("idempotency_conflict", 409)
    assert err(svc.authorize_addon, jwt(OWNER), T1, {**b, "cadence": "weekly", "per_week": 5}) == ("idempotency_conflict", 409)
    assert len(db.tables["marketing_publication_addon_events"]) == 1         # un solo cargo registrado


def test_repeated_approval_decides_once(svc, db):
    r = svc.request_addon(jwt(MANAGER), T1, body(4))
    one = svc.decide_addon(jwt(OWNER), T1, r["id"], True, {"confirm": True})
    two = svc.decide_addon(jwt(OWNER), T1, r["id"], True, {"confirm": True})
    assert one["id"] == two["id"] and two["result"] == "existing" and plan(svc)["total"] == 12
    assert [e["event"] for e in db.tables["marketing_publication_addon_events"]] == ["requested", "authorized"]


def test_the_backend_never_writes_addon_rows_directly(svc, db):
    with pytest.raises(RuntimeError):
        db.insert("marketing_publication_addons", {"tenant_id": T1})
    with pytest.raises(RuntimeError):
        db.update("marketing_publication_addons", {"tenant_id": f"eq.{T1}"}, {"status": "authorized_uninvoiced"})


# ------------------------------------------------------------------ aislamiento entre tenants
def test_addons_and_keys_never_cross_tenants(svc, db):
    db.tables["marketing_settings"][1]["monthly_post_limit"] = 8              # T2 con plan
    key = "addon-shared-key-0001"
    a = svc.authorize_addon(jwt(OWNER), T1, body(4, key=key))
    assert plan(svc, OTHER, T2)["added"] == 0
    b = svc.authorize_addon(jwt(OTHER), T2, body(8, key=key))
    assert a["id"] != b["id"] and b["amount"] == 35
    assert plan(svc)["total"] == 12 and plan(svc, OTHER, T2)["total"] == 16
    assert err(svc.decide_addon, jwt(OTHER), T2, a["id"], False, {"confirm": True})[0] == "not_found"
    assert err(svc.cancel_addon, jwt(OTHER), T2, a["id"], {"confirm": True})[0] == "not_found"
    assert err(svc.authorize_addon, jwt(OWNER), T2, body(1)) == ("owner_authorization_required", 403)   # solo manager en T2


# ------------------------------------------------------------------ HTTP: el navegador nunca fija el precio
def test_http_flow_ignores_browser_price(monkeypatch, db):
    monkeypatch.setattr(main, "member_portal", SimpleNamespace(db=db))
    c = TestClient(main.app)
    H = lambda who: {"Authorization": f"Bearer jwt-{who}"}  # noqa: E731
    r = c.post("/api/manager/marketing/publication-addons/request", headers=H(MANAGER), json={
        "tenant_id": T1, "additional_publications": 12, "confirm": True, "idempotency_key": "addon-http-month-01",
        "amount": 0.01, "amount_cents": 1, "currency": "EUR", "status": "authorized_uninvoiced", "authorized_by": MANAGER})
    assert r.status_code == 200 and r.json()["amount"] == 55 and r.json()["state"] == "pending"
    row = db.tables["marketing_publication_addons"][0]
    assert row["amount_cents"] == 5500 and row["currency"] == "USD" and row["authorized_by"] is None
    a = c.post(f"/api/manager/marketing/publication-addons/{r.json()['id']}/approve", headers=H(OWNER),
               json={"tenant_id": T1, "confirm": True})
    assert a.status_code == 200 and a.json()["state"] == "active"
    assert c.post("/api/manager/marketing/publication-addons/x/approve", headers=H(OWNER),
                  json={"tenant_id": T1, "confirm": True}).status_code == 404
    assert c.post(f"/api/manager/marketing/publication-addons/{r.json()['id']}/delete", headers=H(OWNER),
                  json={"tenant_id": T1}).status_code == 404


def test_a_database_quota_rejection_is_the_same_public_error(svc, db):
    """Dos programaciones a la vez: el backend ve hueco, pero la base (bloqueo por tenant) rechaza a la segunda."""
    go = schedule_one(svc)
    real = db.update

    def racing(table, filters, values):
        if table == "marketing_content" and values.get("status") == "scheduled":
            raise RuntimeError('Supabase PATCH marketing_content -> 400: {"code":"23514","message":"limit_monthly_post_limit"}')
        return real(table, filters, values)
    db.update = racing
    assert err(go) == ("limit_monthly_post_limit", 409)


def test_manager_sees_commercial_states_without_billing_terms(svc, db):
    r = svc.request_addon(jwt(MANAGER), T1, body(12))
    assert plan(svc, MANAGER)["pending"][0]["state"] == "pending"                 # "Pendiente de autorización"
    svc.decide_addon(jwt(OWNER), T1, r["id"], False, {"confirm": True})
    states = {a["id"]: a["state"] for a in plan(svc, MANAGER)["recent"]}
    assert states[r["id"]] == "declined"                                          # "Rechazada"
    a = svc.authorize_addon(jwt(OWNER), T1, body(4))
    p = plan(svc, MANAGER)
    assert p["current"]["state"] == "active" and p["current"]["starts_cycle"] == "2026-10-01"   # "Autorizada", se renueva
    svc.cancel_addon(jwt(OWNER), T1, a["id"], {"confirm": True})
    p = plan(svc, MANAGER)
    assert p["current"]["state"] == "cancel_scheduled" and p["current"]["ends_cycle"] == "2026-11-01"
    text = str(p)
    for internal in ("authorized_uninvoiced", "uninvoiced", "billing", "amount_cents", "replaced", "cancelled", "invoice"):
        assert internal not in text, internal
