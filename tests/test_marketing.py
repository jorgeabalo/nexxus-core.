"""
AITA Marketing (Fase 1): permisos, aislamiento entre tenants, Brand Kit,
borradores, revisión/aprobación, programación, historial, límites del plan,
adaptadores sin red y endpoints. Sin red: Supabase (PostgREST) en memoria.
Golden Age aparece solo como datos de prueba.
"""
import socket
import uuid
from datetime import datetime, timezone

import pytest

import main
from services import marketing_domain as d
from services import marketing_providers as prov
from services.marketing import MarketingService
from services.member_portal import PortalError

T1, T2 = str(uuid.uuid4()), str(uuid.uuid4())
OWNER, MANAGER, STAFF, OTHER, MEMBER = (str(uuid.uuid4()) for _ in range(5))
NOW = datetime(2026, 10, 15, 15, 0, tzinfo=timezone.utc)
GOLDEN_COLORS = {"color_primary": "#0B1F3A", "color_accent": "#C9A227"}


def _split(s):
    """Divide 'a,and(b,c),d' por las comas de primer nivel."""
    out, depth, cur = [], 0, ""
    for ch in s:
        if ch == "," and depth == 0:
            out.append(cur); cur = ""; continue
        depth += ch == "("; depth -= ch == ")"
        cur += ch
    return out + ([cur] if cur else [])


class FakeDB:
    enabled = True

    def __init__(self):
        self.tables = {
            "tenants": [
                {"id": T1, "name": "Golden Age Fitness & Training", "timezone": "America/Chicago",
                 "branding": {**GOLDEN_COLORS, "locale": "en-US", "logo_url": "/media/golden.png"},
                 "settings": {"public_phone": "(346) 245-7940"}},
                {"id": T2, "name": "Other Studio", "timezone": "America/New_York",
                 "branding": {"color_primary": "#112233", "logo_url": "https://evil.example/x.svg"}, "settings": {}}],
            "tenant_users": [
                {"tenant_id": T1, "user_id": OWNER, "role": "owner", "active": True, "staff_id": "s1"},
                {"tenant_id": T1, "user_id": MANAGER, "role": "manager", "active": True, "staff_id": None},
                {"tenant_id": T1, "user_id": STAFF, "role": "staff", "active": True, "staff_id": None},
                {"tenant_id": T2, "user_id": OTHER, "role": "owner", "active": True, "staff_id": None},
                # el owner de T1 también es manager de T2 (cambio de empresa)
                {"tenant_id": T2, "user_id": OWNER, "role": "manager", "active": True, "staff_id": None},
            ],
            "staff": [{"id": "s1", "tenant_id": T1, "first_name": "Roberto", "last_name": "G"}],
            "marketing_settings": [
                {"tenant_id": T1, "marketing_enabled": True, "approval_required": True, "plan_code": "starter",
                 "monthly_post_limit": 8, "monthly_image_limit": None, "monthly_reel_limit": 2,
                 "connected_channel_limit": 1, "competitor_limit": 3},
                {"tenant_id": T2, "marketing_enabled": True, "approval_required": True, "plan_code": "none"}],
            "marketing_brand_profiles": [], "marketing_campaigns": [], "marketing_content": [],
            "marketing_approval_events": [], "marketing_publications": [],
        }

    @classmethod
    def _cond(cls, row, expr):
        if expr.startswith(("and(", "or(")):
            kind, inner = expr.split("(", 1)
            parts = [cls._cond(row, p) for p in _split(inner[:-1])]
            return all(parts) if kind == "and" else any(parts)
        col, _, rest = expr.partition(".")
        return cls._op(row.get(col), rest)

    @staticmethod
    def _op(cur, v):
        op, _, val = v.partition(".")
        if op == "not":
            return not FakeDB._op(cur, val)
        if op == "is":
            return cur is None
        s = "" if cur is None else str(cur)
        if op == "eq":
            return s.lower() == val.lower()
        if op == "neq":
            return s.lower() != val.lower()
        if op == "in":
            return s in val.strip("()").split(",")
        if cur is None:
            return False
        return {"gte": s >= val, "lte": s <= val, "lt": s < val, "gt": s > val}[op]

    def _match(self, row, params):
        for k, v in params.items():
            if k in ("select", "limit", "order"):
                continue
            if k in ("and", "or"):
                if not self._cond(row, f"{k}{v}"):
                    return False
            elif not self._op(row.get(k), v):
                return False
        return True

    def select(self, table, params):
        rows = [dict(r) for r in self.tables.get(table, []) if self._match(r, params)]
        for spec in reversed((params.get("order") or "").split(",")):
            if spec:
                col, _, dr = spec.partition(".")
                rows.sort(key=lambda r: str(r.get(col) or "~"), reverse=dr.startswith("desc"))
        return rows[: int(params.get("limit", 1000))]

    def insert(self, table, row):
        row = dict(row, id=row.get("id") or str(uuid.uuid4()), created_at=NOW.isoformat(), updated_at=NOW.isoformat())
        self.tables.setdefault(table, []).append(row)
        return dict(row)

    def upsert(self, table, row, on_conflict):
        keys = on_conflict.split(",")
        for r in self.tables.setdefault(table, []):
            if all(r.get(k) == row.get(k) for k in keys):
                r.update(row)
                return dict(r)
        return self.insert(table, row)

    def update(self, table, filters, values):
        if table == "marketing_approval_events":
            raise RuntimeError("immutable")
        out = []
        for r in self.tables.get(table, []):
            if self._match(r, filters):
                r.update(values)
                out.append(dict(r))
        return out

    def user_from_jwt(self, jwt):
        return {"id": jwt[4:]} if jwt.startswith("jwt-") else None


@pytest.fixture
def db():
    return FakeDB()


@pytest.fixture
def svc(db):
    return MarketingService(db, now=NOW)


def jwt(u):
    return f"jwt-{u}"


def draft(svc, who=OWNER, tenant=T1, **kw):
    body = {"title": "Movilidad después de los 60", "format": "reel", "channels": ["instagram", "facebook"],
            "language": "es", "caption": "Muévete con confianza", "hashtags": "#fuerza longevidad",
            "planned_at": "2026-10-20T09:00"}
    body.update(kw)
    return svc.create_content(jwt(who), tenant, body)


def err(fn, *a, **kw):
    with pytest.raises(PortalError) as e:
        fn(*a, **kw)
    return e.value.code, e.value.status


# ---------------------------------------------------------------- acceso
def test_owner_and_manager_can_enter(svc):
    assert svc.dashboard(jwt(OWNER), T1)["role"] == "owner"
    assert svc.dashboard(jwt(MANAGER), T1)["role"] == "manager"


@pytest.mark.parametrize("who", [STAFF, MEMBER, OTHER])
def test_staff_member_and_other_tenant_cannot_enter(svc, who):
    assert err(svc.dashboard, jwt(who), T1) == ("forbidden", 403)
    assert err(svc.create_content, jwt(who), T1, {"title": "x", "format": "text"}) == ("forbidden", 403)


def test_invalid_session_and_tenant(svc):
    assert err(svc.dashboard, "bad", T1) == ("unauthorized", 401)
    assert err(svc.dashboard, jwt(OWNER), "not-a-uuid") == ("forbidden", 403)


def test_disabled_tenant_is_read_only(svc, db):
    db.tables["marketing_settings"][0]["marketing_enabled"] = False
    assert svc.dashboard(jwt(OWNER), T1)["settings"]["marketing_enabled"] is False
    assert err(draft, svc) == ("marketing_disabled", 403)


def test_missing_settings_default_to_disabled_with_approval(svc, db):
    db.tables["marketing_settings"] = []
    s = svc.dashboard(jwt(OWNER), T1)["settings"]
    assert s["marketing_enabled"] is False and s["approval_required"] is True and s["monthly_post_limit"] is None


# ---------------------------------------------------------------- aislamiento
def test_tenant_isolation(svc):
    mine = draft(svc)
    theirs = draft(svc, who=OTHER, tenant=T2, title="Otro estudio")
    assert [x["id"] for x in svc.content_list(jwt(OWNER), T1)["content"]] == [mine["id"]]
    assert [x["id"] for x in svc.content_list(jwt(OTHER), T2)["content"]] == [theirs["id"]]
    # no se puede leer, editar ni aprobar contenido de otro tenant por id
    assert err(svc.content, jwt(OTHER), T2, mine["id"]) == ("not_found", 404)
    assert err(svc.update_content, jwt(OTHER), T2, mine["id"], {"title": "hack"}) == ("not_found", 404)
    assert err(svc.transition, jwt(OTHER), T2, mine["id"], "review") == ("not_found", 404)
    # ni enlazar una campaña ajena
    camp = svc.save_campaign(jwt(OTHER), T2, {"name": "Ajena"})
    assert err(draft, svc, campaign_id=camp["id"]) == ("invalid_campaign", 400)


# ---------------------------------------------------------------- Brand Kit
def test_brand_kit_inherits_active_tenant(svc, db):
    b = svc.brand(jwt(OWNER), T1)
    assert b["inherited"] is True
    p = b["profile"]
    assert p["business_name"] == "Golden Age Fitness & Training"
    assert (p["color_primary"], p["color_accent"]) == ("#0B1F3A", "#C9A227")
    assert p["phone"] == "(346) 245-7940" and p["languages"] == ["en"]
    assert b["tenant_logo_url"] == "/media/golden.png" and p["logo_url"] is None   # por referencia, no copiado
    assert db.tables["marketing_brand_profiles"] == []                             # leer no duplica nada


def test_brand_kit_never_leaks_between_tenants(svc):
    svc.save_brand(jwt(OWNER), T1, {"business_name": "Golden Age", "color_primary": "#0B1F3A",
                                    "color_accent": "#C9A227", "languages": ["en", "es"]})
    other = svc.brand(jwt(OWNER), T2)              # misma persona, otra empresa
    assert other["inherited"] is True
    assert other["profile"]["color_primary"] == "#112233" and other["profile"]["color_accent"] is None
    assert other["profile"]["business_name"] == "Other Studio"
    assert other["tenant_logo_url"] is None        # URL externa/SVG del tenant: rechazada
    assert svc.brand(jwt(OWNER), T1)["profile"]["languages"] == ["en", "es"]


def test_golden_age_fixture_profile(svc):
    """Perfil esperado de Golden Age (solo datos de prueba, nada hardcoded en el código)."""
    body = {"business_name": "Golden Age Fitness & Training",
            "target_audience": "Adultos mayores: fuerza, movilidad, independencia y longevidad",
            "tone": "Humano, motivador, respetuoso y claro", "languages": ["en", "es"],
            "banned_topics": ["promesas médicas", "resultados garantizados"],
            "compliance_notes": "No prometer curas ni resultados garantizados.", **GOLDEN_COLORS}
    p = svc.save_brand(jwt(MANAGER), T1, body)["profile"]
    assert p["languages"] == ["en", "es"] and p["color_accent"] == "#C9A227"
    assert svc.dashboard(jwt(OWNER), T1)["settings"]["approval_required"] is True


@pytest.mark.parametrize("logo", ["//evil.example/x.png", "/\\evil", "https://evil.example/logo.png",
                                  "data:image/svg+xml;base64,PHN2Zz4=", "data:image/png;base64,iVBORw0KGgo=",
                                  "javascript:alert(1)"])
def test_brand_logo_policy_not_weakened(svc, logo):
    assert err(svc.save_brand, jwt(OWNER), T1, {"logo_url": logo}) == ("invalid_logo", 400)


@pytest.mark.parametrize("field,value,code", [
    ("color_primary", "red", "invalid_color"), ("languages", ["fr"], "invalid_languages"),
    ("website", "ftp://x", "invalid_website"), ("phone", "call me", "invalid_phone"),
    ("description", "x" * 2001, "invalid_description")])
def test_brand_validation(svc, field, value, code):
    assert err(svc.save_brand, jwt(OWNER), T1, {field: value}) == (code, 400)


def test_brand_local_logo_ok(svc):
    assert svc.save_brand(jwt(OWNER), T1, {"logo_url": "/media/brand/logo.png"})["profile"]["logo_url"] == "/media/brand/logo.png"


# ---------------------------------------------------------------- contenido
def test_create_and_edit_draft(svc, db):
    c = draft(svc)
    assert c["status"] == "draft" and c["hashtags"] == ["#fuerza", "#longevidad"]
    assert c["planned_at"] == "2026-10-20T14:00:00Z"          # 09:00 en Chicago (CDT)
    assert c["assigned_to"] == OWNER
    u = svc.update_content(jwt(MANAGER), T1, c["id"], {"caption": "Nuevo texto", "format": "carousel"})
    assert u["caption"] == "Nuevo texto" and u["format"] == "carousel" and u["updated_by"] == MANAGER
    ev = db.tables["marketing_approval_events"]
    assert [(e["action"], e["to_status"]) for e in ev] == [("create", "draft")]


@pytest.mark.parametrize("field,value,code", [
    ("format", "podcast", "invalid_format"), ("channels", ["myspace"], "invalid_channels"),
    ("language", "fr", "invalid_language"), ("title", "", "invalid_title"),
    ("hashtags", "#ok #no-válido!", "invalid_hashtags"), ("planned_at", "mañana", "invalid_date"),
    ("status", "approved", "invalid_status")])
def test_content_validation(svc, field, value, code):
    assert err(draft, svc, **{field: value})[0] == code


def test_formats_and_statuses_complete():
    assert d.FORMATS == ("image", "carousel", "reel", "story", "video", "text")
    assert set(d.TRANSITIONS) == set(d.STATUSES)


# ---------------------------------------------------------------- aprobación
def test_review_approve_and_history(svc, db):
    c = draft(svc)
    svc.transition(jwt(MANAGER), T1, c["id"], "review", "Lista para revisar")
    assert err(svc.update_content, jwt(OWNER), T1, c["id"], {"title": "x"}) == ("not_editable", 409)
    a = svc.transition(jwt(OWNER), T1, c["id"], "approved", "Perfecto")
    assert a["status"] == "approved" and a["approved_by"] == OWNER
    h = svc.content(jwt(OWNER), T1, c["id"])["history"]
    assert [(e["action"], e["from_status"], e["to_status"], e["actor_id"], e["comment"]) for e in h] == [
        ("create", None, "draft", OWNER, None),
        ("submit", "draft", "review", MANAGER, "Lista para revisar"),
        ("approve", "review", "approved", OWNER, "Perfecto")]
    assert all(e["created_at"] and e["actor_role"] in ("owner", "manager") for e in h)


def test_reject_requires_comment_and_returns_to_draft(svc):
    c = draft(svc)
    svc.transition(jwt(OWNER), T1, c["id"], "review")
    assert err(svc.transition, jwt(OWNER), T1, c["id"], "rejected") == ("comment_required", 400)
    r = svc.transition(jwt(OWNER), T1, c["id"], "rejected", "Evitar promesas médicas")
    assert r["status"] == "rejected"
    assert svc.update_content(jwt(OWNER), T1, c["id"], {"caption": "Corregido"})["caption"] == "Corregido"
    assert svc.transition(jwt(OWNER), T1, c["id"], "draft")["status"] == "draft"


def test_cannot_skip_review_when_approval_required(svc, db):
    c = draft(svc)
    assert err(svc.transition, jwt(OWNER), T1, c["id"], "approved") == ("invalid_transition", 409)
    db.tables["marketing_settings"][0]["approval_required"] = False
    assert svc.transition(jwt(OWNER), T1, c["id"], "approved")["status"] == "approved"


def test_staff_cannot_approve(svc):
    c = draft(svc)
    svc.transition(jwt(OWNER), T1, c["id"], "review")
    assert err(svc.transition, jwt(STAFF), T1, c["id"], "approved") == ("forbidden", 403)


# ---------------------------------------------------------------- programación
def approved(svc, **kw):
    c = draft(svc, **kw)
    svc.transition(jwt(OWNER), T1, c["id"], "review")
    return svc.transition(jwt(OWNER), T1, c["id"], "approved")


@pytest.mark.parametrize("status", ["draft", "review", "rejected"])
def test_schedule_requires_approved(svc, status):
    c = draft(svc)
    if status != "draft":
        svc.transition(jwt(OWNER), T1, c["id"], "review")
    if status == "rejected":
        svc.transition(jwt(OWNER), T1, c["id"], "rejected", "No")
    assert err(svc.transition, jwt(OWNER), T1, c["id"], "scheduled", None, "2026-10-21T10:00") == ("not_approved", 409)


def test_publish_states_not_reachable_by_users(svc):
    c = approved(svc)
    for target in ("publishing", "published", "failed"):
        assert err(svc.transition, jwt(OWNER), T1, c["id"], target)[0] == "invalid_transition"


def test_schedule_queues_without_publishing(svc, db):
    c = approved(svc)
    s = svc.transition(jwt(OWNER), T1, c["id"], "scheduled", None, "2026-10-21T10:00")
    assert s["status"] == "scheduled" and s["scheduled_at"] == "2026-10-21T15:00:00Z"
    pubs = db.tables["marketing_publications"]
    assert sorted(p["channel"] for p in pubs) == ["facebook", "instagram"]
    assert all(p["provider"] == "disabled" and p["status"] == "pending" and p["idempotency_key"] for p in pubs)
    assert len({p["idempotency_key"] for p in pubs}) == 2
    # desprogramar cancela la cola y vuelve a aprobado
    back = svc.transition(jwt(OWNER), T1, c["id"], "approved")
    assert back["status"] == "approved" and back["scheduled_at"] is None
    assert {p["status"] for p in pubs} == {"cancelled"}


def test_schedule_rules(svc):
    c = approved(svc, channels=[])
    assert err(svc.transition, jwt(OWNER), T1, c["id"], "scheduled", None, "2026-10-21T10:00") == ("channels_required", 400)
    c2 = approved(svc)
    assert err(svc.transition, jwt(OWNER), T1, c2["id"], "scheduled", None, "2026-10-01T10:00") == ("scheduled_in_past", 400)


# ---------------------------------------------------------------- límites del plan
def schedule_n(svc, n, fmt="image", day=21):
    for i in range(n):
        c = approved(svc, format=fmt, title=f"Post {i}")
        svc.transition(jwt(OWNER), T1, c["id"], "scheduled", None, f"2026-10-{day}T{8 + i % 10:02d}:00")


def test_monthly_post_limit_is_configurable(svc, db):
    schedule_n(svc, 8)
    c = approved(svc, format="text")
    assert err(svc.transition, jwt(OWNER), T1, c["id"], "scheduled", None, "2026-10-28T10:00") == (
        "limit_monthly_post_limit", 409)
    # el mes siguiente tiene su propio cupo
    assert svc.transition(jwt(OWNER), T1, c["id"], "scheduled", None, "2026-11-03T10:00")["status"] == "scheduled"
    assert svc.dashboard(jwt(OWNER), T1)["usage"] == {"posts": 8, "images": 8, "reels": 0}
    db.tables["marketing_settings"][0]["monthly_post_limit"] = None           # sin límite
    c2 = approved(svc, format="text")
    assert svc.transition(jwt(OWNER), T1, c2["id"], "scheduled", None, "2026-10-29T10:00")["status"] == "scheduled"


def test_reel_limit(svc):
    schedule_n(svc, 2, fmt="reel")
    c = approved(svc, format="video")
    assert err(svc.transition, jwt(OWNER), T1, c["id"], "scheduled", None, "2026-10-28T10:00")[0] == "limit_monthly_reel_limit"


def test_limit_logic_pure():
    s = {**d.DEFAULT_SETTINGS, "monthly_post_limit": 0}
    assert d.limit_reached(s, {"posts": 0, "images": 0, "reels": 0}, "text") == "monthly_post_limit"
    assert d.limit_reached(d.DEFAULT_SETTINGS, {"posts": 999, "images": 999, "reels": 999}, "reel") is None


@pytest.mark.parametrize("key,fmt", [("monthly_post_limit", "text"), ("monthly_image_limit", "image"),
                                     ("monthly_reel_limit", "reel")])
def test_null_limit_means_unlimited_and_zero_blocks(key, fmt):
    huge = {"posts": 10**6, "images": 10**6, "reels": 10**6}
    assert d.limit_reached({**d.DEFAULT_SETTINGS, key: None}, huge, fmt) is None
    assert d.limit_reached({**d.DEFAULT_SETTINGS, key: 0}, {"posts": 0, "images": 0, "reels": 0}, fmt) == key


def test_settings_not_editable_by_tenant():
    """Plan y límites los fija el operador: no hay endpoint para que un tenant los cambie."""
    paths = {getattr(r, "path", "") for r in main.app.routes}
    assert not any("marketing/settings" in p for p in paths)


# ---------------------------------------------------------------- campañas y calendario
def test_campaigns(svc):
    camp = svc.save_campaign(jwt(OWNER), T1, {"name": "Octubre activo", "objective": "Nuevos socios",
                                              "start_date": "2026-10-01", "end_date": "2026-10-31", "budget": "250"})
    assert camp["budget"] == 250.0 and camp["status"] == "planned"
    draft(svc, campaign_id=camp["id"])
    assert svc.campaigns(jwt(MANAGER), T1)["campaigns"][0]["content_count"] == 1
    assert err(svc.save_campaign, jwt(OWNER), T1, {"name": "x", "start_date": "2026-10-10",
                                                   "end_date": "2026-10-01"}) == ("invalid_dates", 400)
    assert err(svc.save_campaign, jwt(OWNER), T1, {"name": "x", "budget": "-1"}) == ("invalid_budget", 400)
    u = svc.save_campaign(jwt(OWNER), T1, {"name": "Octubre", "status": "active"}, camp["id"])
    assert u["status"] == "active"


def test_calendar(svc):
    camp = svc.save_campaign(jwt(OWNER), T1, {"name": "Octubre"})
    c = draft(svc, campaign_id=camp["id"])
    draft(svc, title="Noviembre", planned_at="2026-11-20T09:00")
    items = svc.calendar(jwt(OWNER), T1, "2026-10-01", "2026-10-31")["items"]
    assert [i["id"] for i in items] == [c["id"]]
    i = items[0]
    assert (i["campaign_name"], i["assignee_name"], i["format"], i["status"]) == ("Octubre", "Roberto G", "reel", "draft")
    assert i["channels"] == ["instagram", "facebook"]
    assert err(svc.calendar, jwt(OWNER), T1, "2026-10-01", "2027-10-01") == ("invalid_range", 400)
    assert svc.calendar(jwt(OTHER), T2, "2026-10-01", "2026-10-31")["items"] == []


def test_dashboard_counts_and_upcoming(svc):
    draft(svc)
    r = draft(svc, title="En revisión")
    svc.transition(jwt(OWNER), T1, r["id"], "review")
    a = approved(svc, title="Aprobado")
    s = approved(svc, title="Programado")
    svc.transition(jwt(OWNER), T1, s["id"], "scheduled", None, "2026-10-22T10:00")
    dash = svc.dashboard(jwt(OWNER), T1)
    assert {k: dash["counts"][k] for k in ("drafts", "review", "approved", "scheduled", "published")} == {
        "drafts": 1, "review": 1, "approved": 1, "scheduled": 1, "published": 0}
    assert {u["id"] for u in dash["upcoming"]} == {a["id"], s["id"]}
    assert dash["limits"]["monthly_post_limit"] == 8


# ---------------------------------------------------------------- adaptadores
def test_disabled_adapters_make_no_network_calls(monkeypatch):
    def boom(*a, **kw):
        raise AssertionError("network call")
    monkeypatch.setattr(socket, "create_connection", boom)
    monkeypatch.setattr(socket.socket, "connect", boom)
    p = prov.default_providers()
    req = prov.PublishRequest("t", "c", ["instagram"], None, "hola", "k" * 20)
    results = [p["publisher"].schedule(req), p["publisher"].publish(req), p["publisher"].cancel("t", "x"),
               p["publisher"].fetch_metrics("t", "x"), p["creative"].generate_image("t", "p", {}),
               p["creative"].generate_video("t", "p", {}), p["reels"].render("t", {}),
               p["competitors"].collect_snapshot("t", {})]
    assert {r.status for r in results} == {"not_connected"}


def test_mock_publisher_receives_idempotency_key(db):
    mock = prov.MockPublisher()
    s = MarketingService(db, now=NOW, providers={**prov.default_providers(), "publisher": mock})
    c = approved(s, channels=["instagram"])
    s.transition(jwt(OWNER), T1, c["id"], "scheduled", None, "2026-10-21T10:00")
    assert mock.calls == [("schedule", f"{c['id']}:instagram:2026-10-21T15:00:00Z")]
    assert db.tables["marketing_publications"][0]["status"] == "queued"


def test_redact_hides_tokens():
    out = prov.redact("401 Bearer abc.def token=SECRET123 key: k-999 eyJa.eyJb.sig")
    assert "SECRET123" not in out and "abc.def" not in out and "k-999" not in out and "eyJa" not in out


def test_transitions_match_migration():
    """La tabla de transiciones de Python y la del trigger SQL deben ser idénticas."""
    import re
    from pathlib import Path
    sql = (Path(__file__).resolve().parent.parent / "supabase/migrations/20261009120000_aita_marketing.sql").read_text()
    found = {m.group(1): tuple(re.findall(r"'(\w+)'", m.group(2)))
             for m in re.finditer(r"when '(\w+)'\s+then array\[([^\]]*)\]", sql)}
    assert found == d.TRANSITIONS
