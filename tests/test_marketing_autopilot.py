"""
Modo de control de las publicaciones (PR #21): «Revisar antes de publicar» (predeterminado) y «Piloto automático
de AITA». Autorización del owner o de un manager con permiso delegado propio, pausa inmediata, reparto en cuatro
semanas (máximo una por día), cuota del plan con ampliaciones ya autorizadas (nunca compra), aprobación automática
atribuida al sistema con versión de reglas y sus motivos de rechazo. Sin canales conectados no se crea ni envía nada.
"""
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from marketing_addon_fake import install as install_addons
from marketing_autopilot_fake import install as install_autopilot
from marketing_planner_client import planner_client
from services import marketing_autopilot as ap
from services import marketing_content_plan as cp
from services.marketing_ai_router import DisabledLocalTools, MarketingAIRouter, MockProvider, OmniRouteAdapter
from services.marketing_library import LibraryService
from services.marketing_plan import MarketingPlanService
from services.marketing_providers import MockPublisher, default_providers
from services.marketing_studio import StudioService
from services.member_portal import PortalError
from test_marketing import MANAGER, MEMBER, NOW, OTHER, OWNER, STAFF, T1, T2
from test_marketing_studio import StudioDB, jwt, no_people, png, up

P = "/api/manager/marketing"
H, HM = {"Authorization": f"Bearer jwt-{OWNER}"}, {"Authorization": f"Bearer jwt-{MANAGER}"}
CHI = ZoneInfo("America/Chicago")
RULES = {"objective": "calls", "platforms": ["instagram"], "days": [0, 3], "hours": {"from": "10:00", "to": "18:00"},
         "formats": ["reel", "image"], "mode": "fully_ai", "cta": "Llámanos", "banned_topics": ["descuento"],
         "starts_on": "2026-11-02"}
ADAPTERS = {"mock": MockProvider(), "omniroute": OmniRouteAdapter(env={}), "local_ffmpeg": DisabledLocalTools()}


@pytest.fixture
def db():
    d = install_autopilot(install_addons(StudioDB(), now=NOW))
    d.tables["marketing_settings"][0].update(ai_generation_enabled=True, monthly_ai_cost_limit=20,
                                             monthly_generation_job_limit=50, monthly_generated_image_limit=200,
                                             monthly_generated_video_seconds_limit=5000)
    return d


@pytest.fixture
def client(monkeypatch, db):
    return planner_client(monkeypatch, db)


def service(monkeypatch, db, connected=True):
    """Servicio del sistema con proveedores simulados; `connected` simula canales conectados y autorizados."""
    monkeypatch.setattr(MarketingPlanService, "planner", lambda self: cp.ContentPlanner(
        self, lambda: StudioService(db, now=self._now, router=MarketingAIRouter(env={}), adapters=ADAPTERS)))
    providers = {**default_providers(), **({"publisher": MockPublisher()} if connected else {})}
    return MarketingPlanService(db, now=NOW, providers=providers)


def activate(client, rules=None, headers=H, confirm=True):
    return client.post(f"{P}/autopilot/activate", json={"tenant_id": T1, "rules": rules or RULES, "confirm": confirm},
                       headers=headers)


def contents(db, tenant=T1):
    return [c for c in db.tables["marketing_content"] if c["tenant_id"] == tenant]


# ------------------------------------------------------------------ modo manual y autorización
def test_manual_mode_is_the_default(client, db):
    st = client.get(f"{P}/autopilot?tenant_id={T1}", headers=H).json()
    assert st["mode"] == "manual" and st["rules_version"] == 0 and st["channels_connected"] is False
    assert st["can_manage"] is True and st["can_pause"] is True and st["can_delegate"] is True
    m = client.get(f"{P}/autopilot?tenant_id={T1}", headers=HM).json()
    assert m["can_manage"] is False and m["can_pause"] is True and m["can_delegate"] is False
    assert db.tables["marketing_autopilot_settings"] == [] and db.tables["marketing_autopilot_events"] == []


def test_activation_requires_explicit_confirmation(client, db):
    r = activate(client, confirm=False)
    assert r.status_code == 400 and r.json() == {"error": "confirmation_required"}
    r = activate(client)
    assert r.status_code == 200 and r.json()["mode"] == "autopilot" and r.json()["rules_version"] == 1
    assert r.json()["message"] == "waiting_for_channels"               # no afirma que publica
    ev = db.tables["marketing_autopilot_events"]
    assert [e["event"] for e in ev] == ["activated"] and ev[0]["actor_id"] == OWNER and ev[0]["rules"]["days"] == [0, 3]


def test_manager_needs_the_autopilot_delegation_not_the_purchase_one(client, db):
    client.put(f"{P}/publication-addons/permissions", json={"tenant_id": T1, "managers_can_authorize": True}, headers=H)
    r = activate(client, headers=HM)
    assert r.status_code == 403 and r.json() == {"error": "autopilot_authorization_required"}
    assert client.put(f"{P}/autopilot/permissions", json={"tenant_id": T1, "managers_can_manage": True},
                      headers=HM).status_code == 403                    # el manager no se delega a sí mismo
    assert client.put(f"{P}/autopilot/permissions", json={"tenant_id": T1, "managers_can_manage": True},
                      headers=H).json()["managers_can_manage"] is True
    r = activate(client, headers=HM)
    assert r.status_code == 200 and r.json()["authorized_role"] == "manager"
    assert [e["event"] for e in db.tables["marketing_autopilot_events"]] == ["delegation_granted", "activated"]


def test_any_manager_pauses_immediately_and_it_returns_to_manual(client, db):
    activate(client)
    r = client.post(f"{P}/autopilot/pause", json={"tenant_id": T1}, headers=HM)
    assert r.status_code == 200 and r.json()["mode"] == "manual" and r.json()["paused_at"]
    again = client.post(f"{P}/autopilot/pause", json={"tenant_id": T1}, headers=HM)
    assert again.json()["result"] == "unchanged"
    assert [e["event"] for e in db.tables["marketing_autopilot_events"]] == ["activated", "paused"]
    assert activate(client).json()["result"] == "reactivated"


def test_rule_change_creates_a_new_version(client, db):
    activate(client)
    assert activate(client).json()["result"] == "unchanged"
    r = activate(client, {**RULES, "days": [1, 4]})
    assert r.json()["result"] == "rules_changed" and r.json()["rules_version"] == 2
    assert [e["rules_version"] for e in db.tables["marketing_autopilot_events"]] == [1, 2]


@pytest.mark.parametrize("change,code", [
    ({"objective": "brand"}, "invalid_objective"), ({"platforms": []}, "channels_required"),
    ({"days": []}, "invalid_days"), ({"days": [7]}, "invalid_days"), ({"hours": {"from": "18:00", "to": "10:00"}}, "invalid_hours"),
    ({"formats": ["story"]}, "invalid_format"), ({"mode": "mix"}, "invalid_mode"), ({"starts_on": "2026-01-01"}, "invalid_date")])
def test_rules_are_validated(client, db, change, code):
    r = activate(client, {**RULES, **change})
    assert r.status_code == 400 and r.json() == {"error": code}
    assert db.tables["marketing_autopilot_events"] == []


@pytest.mark.parametrize("who", [STAFF, MEMBER, OTHER])
def test_staff_members_and_other_tenants_are_blocked(client, db, who):
    hx = {"Authorization": f"Bearer jwt-{who}"}
    for method, path, body in (("GET", f"autopilot?tenant_id={T1}", None), ("POST", "autopilot/preview", RULES),
                               ("POST", "autopilot/activate", {"rules": RULES, "confirm": True}),
                               ("POST", "autopilot/pause", {}), ("PUT", "autopilot/permissions", {"managers_can_manage": True})):
        r = client.request(method, f"{P}/{path}", json={"tenant_id": T1, **(body or {})}, headers=hx)
        assert r.status_code == 403, (who, path)
    assert db.tables["marketing_autopilot_settings"] == []


def test_each_tenant_has_its_own_configuration(client, db):
    activate(client)
    other = client.get(f"{P}/autopilot?tenant_id={T2}", headers={"Authorization": f"Bearer jwt-{OTHER}"}).json()
    assert other["mode"] == "manual" and other["rules"] is None


# ------------------------------------------------------------------ calendario preliminar
def test_preview_spreads_8_posts_over_4_weeks_one_per_day(client, db):
    r = client.post(f"{P}/autopilot/preview", json={"tenant_id": T1, **RULES}, headers=H).json()
    days = [s["day"] for s in r["slots"]]
    assert r["planned"] == 8 and r["available"] == 8 and len(set(days)) == 8
    assert days == ["2026-11-02", "2026-11-05", "2026-11-09", "2026-11-12", "2026-11-16", "2026-11-19",
                    "2026-11-23", "2026-11-26"]                         # 2 por semana, sin acumular al final
    assert all(datetime.fromisoformat(s["at"].replace("Z", "+00:00")).astimezone(CHI).strftime("%H:%M") == "10:00"
               for s in r["slots"])
    assert db.tables["marketing_content"] == [] and db.tables["marketing_autopilot_settings"] == []   # no escribe


def test_distribution_rules():
    start = date(2026, 11, 2)
    for n in range(1, 30):
        slots = ap.distribute(n, start, [0, 1, 2, 3, 4, 5, 6], "09:00", CHI)
        days = [s.astimezone(CHI).date() for s in slots]
        assert len(days) == len(set(days)) == min(n, 28)               # máximo una por día
        per_week = [sum(1 for d in days if (d - start).days // 7 == w) for w in range(4)]
        assert max(per_week) - min(per_week) <= 1 and per_week == sorted(per_week, reverse=True)   # nunca al final
    assert len(ap.distribute(8, start, [0], "09:00", CHI)) == 4         # un solo día por semana: 4, no 8 apiladas


def test_an_authorized_addon_raises_the_total_but_nothing_is_ever_bought(client, db):
    client.post(f"{P}/publication-addons/authorize", json={"tenant_id": T1, "additional_publications": 4, "confirm": True,
                                                          "idempotency_key": "addon-autopilot-0001"}, headers=H)
    r = client.post(f"{P}/autopilot/preview", json={"tenant_id": T1, **RULES, "days": [0, 2, 4]}, headers=H).json()
    assert r["available"] == 12 and r["planned"] == 12                  # 3 por semana con +4
    n = len(db.tables["marketing_publication_addons"])
    activate(client, {**RULES, "days": [0, 2, 4]})
    assert len(db.tables["marketing_publication_addons"]) == n          # el piloto no compra ni solicita


# ------------------------------------------------------------------ ciclo del sistema
def test_without_connected_channels_nothing_is_created_or_sent(monkeypatch, client, db):
    activate(client)
    out = service(monkeypatch, db, connected=False).autopilot().tick(T1)
    assert out == {"state": "waiting_for_channels"} and contents(db) == []
    assert db.tables["marketing_publications"] == []


def test_tick_creates_reviews_and_auto_approves_attributed_to_the_system(monkeypatch, client, db):
    activate(client)
    svc = service(monkeypatch, db)
    out = svc.autopilot().tick(T1)
    assert out["created"] == 8 and out["approved"] == 8, out
    days = [i["slot_day"] for i in db.tables["marketing_autopilot_items"]]
    assert len(days) == len(set(days)) == 8
    for c in contents(db):
        assert c["status"] == "approved" and c["approved_by"] is None
    auto = [e for e in db.tables["marketing_approval_events"] if e["action"] == "approve"]
    assert len(auto) == 8 and all(e["actor_role"] == "system" and e["actor_id"] == OWNER
                                  and e["comment"] == "Aprobado automáticamente bajo la autorización del owner." for e in auto)
    assert all(i["approved_rules_version"] == 1 and i["authorized_by"] == OWNER for i in db.tables["marketing_autopilot_items"])
    again = svc.autopilot().tick(T1)
    assert again["created"] == 0 and len(contents(db)) == 8            # repetir no duplica


def test_scheduling_respects_the_quota_and_stops_when_exhausted(monkeypatch, client, db):
    activate(client, {**RULES, "days": [0, 1, 2, 3, 4]})
    svc = service(monkeypatch, db)
    monkeypatch.setattr(MarketingPlanService, "_mock_generated", lambda self, c, cid: False)
    out = svc.autopilot().tick(T1)
    assert out["scheduled"] == 8 and out["state"] == "quota_exhausted", out
    sched = [c for c in contents(db) if c["status"] == "scheduled"]
    assert len(sched) == 8 and len({c["scheduled_at"][:10] for c in sched}) == 8
    assert {p["status"] for p in db.tables["marketing_publications"]} == {"queued"}
    st = svc.autopilot().status(jwt(OWNER), T1)
    assert st["message"] == "quota_exhausted" and st["available"] == 0
    assert svc.autopilot().tick(T1)["created"] == 0                     # no crea más


def test_pause_keeps_published_and_drafts_and_holds_the_future(monkeypatch, client, db):
    activate(client)
    svc = service(monkeypatch, db)
    monkeypatch.setattr(MarketingPlanService, "_mock_generated", lambda self, c, cid: False)
    svc.autopilot().tick(T1)
    items = contents(db)
    items[0]["status"] = "published"
    draft = client.post(f"{P}/content", json={"tenant_id": T1, "title": "Borrador", "format": "image"}, headers=H).json()
    r = client.post(f"{P}/autopilot/pause", json={"tenant_id": T1}, headers=HM)
    assert r.status_code == 200
    by = {c["id"]: c["status"] for c in contents(db)}
    assert by[items[0]["id"]] == "published" and by[draft["id"]] == "draft"
    assert all(by[c["id"]] == "review" for c in items[1:])               # lo futuro espera revisión
    assert not [p for p in db.tables["marketing_publications"] if p["status"] in ("pending", "queued")
                and p["content_id"] != items[0]["id"]]
    assert svc.autopilot().tick(T1) == {"state": "manual"}              # sin nuevas publicaciones automáticas


# ------------------------------------------------------------------ motivos para no aprobar automáticamente
def prepared(monkeypatch, client, db, **rules):
    activate(client, {**RULES, **rules})
    svc = service(monkeypatch, db)
    with monkeypatch.context() as m:                     # el primer ciclo deja todo esperando revisión
        m.setattr(ap.AutopilotService, "auto_approve", lambda self, c, item: "held_for_test")
        svc.autopilot().tick(T1)
    item = db.tables["marketing_autopilot_items"][0]
    c = svc._ctx_for({"id": OWNER}, T1)
    return svc, c, item


def test_auto_approval_failures(monkeypatch, client, db):
    svc, c, item = prepared(monkeypatch, client, db)
    a = svc.autopilot()
    content = next(x for x in contents(db) if x["id"] == item["content_id"])
    assert content["status"] == "review"
    content["format"] = "video"
    assert a.auto_approve(c, item) == "format_not_authorized"
    content["format"], content["channels"] = "reel", ["tiktok"]
    assert a.auto_approve(c, item) == "platform_not_authorized"
    content["channels"] = ["instagram"]
    content["caption"] = "Gran descuento hoy"
    assert a.auto_approve(c, item) == "banned_topic"
    content["caption"] = "Clase de prueba"
    db.tables["marketing_settings"][0]["monthly_ai_cost_limit"] = 0
    assert a.auto_approve(c, item) == "budget_unavailable"
    db.tables["marketing_settings"][0]["monthly_ai_cost_limit"] = 20
    assert service(monkeypatch, db, connected=False).autopilot().auto_approve(c, item) == "channels_not_connected"
    db.tables["marketing_autopilot_settings"][0]["rules_version"] = 9
    assert a.auto_approve(c, item) == "rules_changed"
    db.tables["marketing_autopilot_settings"][0]["rules_version"] = 1
    assert a.auto_approve(c, {**item, "content_id": item["content_id"]}) is None      # todo en regla: aprueba
    assert content["status"] == "approved"


def test_auto_approval_fails_when_paused_or_authorization_withdrawn(monkeypatch, client, db):
    client.put(f"{P}/autopilot/permissions", json={"tenant_id": T1, "managers_can_manage": True}, headers=H)
    activate(client, headers=HM)
    svc = service(monkeypatch, db)
    with monkeypatch.context() as m:
        m.setattr(ap.AutopilotService, "auto_approve", lambda self, c, item: "held_for_test")
        svc.autopilot().tick(T1)
    a, c, item = svc.autopilot(), svc._ctx_for({"id": OWNER}, T1), db.tables["marketing_autopilot_items"][0]
    client.put(f"{P}/autopilot/permissions", json={"tenant_id": T1, "managers_can_manage": False}, headers=H)
    assert a.auto_approve(c, item) == "authorization_withdrawn"
    client.put(f"{P}/autopilot/permissions", json={"tenant_id": T1, "managers_can_manage": True}, headers=H)
    client.post(f"{P}/autopilot/pause", json={"tenant_id": T1}, headers=H)
    assert a.auto_approve(c, item) == "autopilot_paused"


def test_auto_approval_fails_for_other_tenants_content(monkeypatch, client, db):
    svc, c, item = prepared(monkeypatch, client, db)
    other = {**item, "content_id": item["content_id"]}
    db.tables["marketing_autopilot_items"][0]["tenant_id"] = T2           # el hueco ya no es de este tenant
    assert svc.autopilot().auto_approve(c, other) == "tenant_mismatch"


def test_auto_approval_fails_when_the_quota_is_used(monkeypatch, client, db):
    svc, c, item = prepared(monkeypatch, client, db)
    db.tables["marketing_settings"][0]["monthly_post_limit"] = 0
    assert svc.autopilot().auto_approve(c, item) == "quota_exhausted"


@pytest.mark.parametrize("change,code", [
    ({"expires_at": "2026-10-01T00:00:00+00:00"}, "media_expired"),
    ({"consent_status": "revoked"}, "consent_revoked"),
    ({"contains_people": True, "contains_minors": True, "people_policy": "consented", "consent_status": "granted"}, None),
    ({"people_policy": "exclude", "contains_people": True}, None)])
def test_auto_approval_rechecks_library_material(monkeypatch, client, db, change, code):
    lib = LibraryService(db, now=NOW)
    m = up(lib, name="real.png", data=png(w=47))
    no_people(lib, m)
    svc, c, item = prepared(monkeypatch, client, db, mode="library_ai")
    media = next(x for x in db.tables["marketing_media"] if x["id"] == m["id"])
    media.update(change)
    why = svc.autopilot().auto_approve(c, item)
    assert why and why != "held_for_test" and (code is None or why == code), why
    assert next(x for x in contents(db) if x["id"] == item["content_id"])["status"] == "review"


# ------------------------------------------------------------------ resultados sin métricas
def test_results_are_never_a_fake_zero(monkeypatch, client, db):
    d = client.get(f"{P}/dashboard?tenant_id={T1}", headers=H).json()
    assert d["results"] == {"state": "not_connected"} and "results_available" not in d["counts"]
    svc = service(monkeypatch, db)
    assert svc.dashboard(jwt(OWNER), T1)["results"] == {"state": "none"}


def test_system_service_rejects_an_authorizer_that_left_the_tenant(monkeypatch, client, db):
    activate(client)
    next(u for u in db.tables["tenant_users"] if u["user_id"] == OWNER and u["tenant_id"] == T1)["active"] = False
    assert service(monkeypatch, db).autopilot().tick(T1) == {"state": "authorization_withdrawn"}
    assert contents(db) == []


def test_validate_rules_is_canonical():
    today = date(2026, 10, 15)
    a = ap.validate_rules({**RULES, "platforms": ["instagram"], "days": [3, 0, 3]}, today)
    b = ap.validate_rules({**RULES, "days": [0, 3]}, today)
    assert a == b and a["starts_on"] == "2026-11-02"
    with pytest.raises(PortalError):
        ap.validate_rules({**RULES, "starts_on": "2027-06-01"}, today)       # más de 90 días
    assert datetime(2026, 10, 15, tzinfo=timezone.utc) == NOW.replace(hour=0)
