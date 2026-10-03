"""
Tests del portal del socio (Member Panel): enlace/QR firmado, revocación al
regenerar, autorización del staff, envío de acceso y endpoints. Sin red: se
usa un Supabase (PostgREST + Auth) en memoria.
"""
import uuid

import pytest
from fastapi.testclient import TestClient

import main
from services import member_portal as mp
from services.member_portal import MemberPortal, PortalError, make_token, parse_token

SECRET = "x" * 40
T1, T2 = str(uuid.uuid4()), str(uuid.uuid4())
M1, M2, M3 = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
OWNER, STAFF, OTHER_OWNER, MEMBER_USER = "u-owner", "u-staff", "u-other", "u-member"


class FakeDB:
    enabled = True
    anon_key = "anon"

    def __init__(self):
        self.tables = {
            "tenants": [{"id": T1, "name": "Golden Age", "twilio_phone": "+13462457940",
                         "branding": {"display_name": "GOLDEN AGE"}, "settings": {}},
                        {"id": T2, "name": "Other", "twilio_phone": None, "branding": {}, "settings": {}}],
            "members": [
                {"id": M1, "tenant_id": T1, "first_name": "Ana", "last_name": "Diaz", "email": "ana@x.com",
                 "phone": "(832) 555-1234", "user_id": None, "membership_status": "active", "portal_token_version": 1,
                 "portal_invited_at": None, "portal_activated_at": None, "portal_last_used_at": None},
                {"id": M2, "tenant_id": T1, "first_name": "Luis", "last_name": None, "email": None,
                 "phone": None, "user_id": None, "membership_status": "cancelled", "portal_token_version": 1,
                 "portal_invited_at": None, "portal_activated_at": None, "portal_last_used_at": None},
                {"id": M3, "tenant_id": T2, "first_name": "Zoe", "last_name": None, "email": None,
                 "phone": "8325550000", "user_id": MEMBER_USER, "membership_status": "active", "portal_token_version": 1,
                 "portal_invited_at": None, "portal_activated_at": None, "portal_last_used_at": None},
            ],
            "tenant_users": [
                {"user_id": OWNER, "tenant_id": T1, "role": "owner", "active": True},
                {"user_id": STAFF, "tenant_id": T1, "role": "staff", "active": True},
                {"user_id": OTHER_OWNER, "tenant_id": T2, "role": "owner", "active": True},
            ],
        }
        self.users = {MEMBER_USER: {"id": MEMBER_USER, "email": "m-x@members.aita-nexxus.app"}}
        self.jwts = {"jwt-owner": OWNER, "jwt-staff": STAFF, "jwt-other": OTHER_OWNER, "jwt-member": MEMBER_USER}
        self.auth_calls = []

    @staticmethod
    def _match(row, filters):
        for k, v in filters.items():
            if k in ("select", "limit", "order"):
                continue
            op, _, val = v.partition(".")
            cur = row.get(k)
            if op == "eq":
                if str(cur).lower() != val.lower() if isinstance(cur, bool) else str(cur) != val:
                    return False
            elif op == "lte":
                if cur is None or str(cur) > val:
                    return False
            elif op == "not":
                if cur is None:
                    return False
            elif op == "in":
                if str(cur) not in val.strip("()").split(","):
                    return False
        return True

    def select(self, table, params):
        rows = [dict(r) for r in self.tables.get(table, []) if self._match(r, params)]
        return rows[: int(params.get("limit", 1000))]

    def insert(self, table, row):
        rows = self.tables.setdefault(table, [])
        if table == "evaluation_invitations" and row.get("auto") and any(
                r.get("auto") and r["member_id"] == row["member_id"] and r["due_date"] == row["due_date"] for r in rows):
            raise RuntimeError("duplicate key value violates unique constraint")
        row = dict(row, id=row.get("id") or str(uuid.uuid4()), created_at="2026-09-29T12:00:00+00:00")
        rows.append(row)
        return dict(row)

    def update(self, table, filters, values):
        out = []
        for r in self.tables.setdefault(table, []):
            if self._match(r, filters):
                r.update(values)
                out.append(dict(r))
        return out

    def user_from_jwt(self, jwt):
        uid = self.jwts.get(jwt)
        return {"id": uid} if uid else None

    def auth(self, method, path, *, json=None, params=None, anon=False, bearer=None):
        self.auth_calls.append((method, path, json, params, anon))
        if method == "POST" and path == "/admin/users":
            if any(u["email"] == json["email"] for u in self.users.values()):
                return 422, {"msg": "exists"}
            uid = f"u-{len(self.users) + 1}"
            self.users[uid] = {"id": uid, "email": json["email"]}
            return 200, self.users[uid]
        if method == "GET" and path.startswith("/admin/users/"):
            u = self.users.get(path.rsplit("/", 1)[1])
            return (200, u) if u else (404, None)
        if method == "PUT" and path.startswith("/admin/users/"):
            self.users[path.rsplit("/", 1)[1]]["email"] = json["email"]
            return 200, {}
        if method == "POST" and path == "/admin/generate_link":
            u = next((u for u in self.users.values() if u["email"] == json["email"]), None)
            if not u:
                return 404, None
            return 200, {"id": u["id"], "properties": {"hashed_token": f"hash-{u['id']}"}}
        if method == "POST" and path == "/otp":
            return 200, {}
        return 404, None

    def member(self, mid):
        return next(r for r in self.tables["members"] if r["id"] == mid)


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("PORTAL_TOKEN_SECRET", SECRET)
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://portal.example")
    db = FakeDB()
    sent = []
    portal = MemberPortal(db, sms_sender=lambda to, frm, body: sent.append((to, frm, body)) or {"ok": True, "sid": f"SM{len(sent):032d}", "twilio_status": "queued"})
    monkeypatch.setattr(main, "member_portal", portal)
    main._portal_hits.clear()
    return db, portal, sent


# ---------------------------------------------------------------------------
# Token
# ---------------------------------------------------------------------------
def test_token_roundtrip_and_tamper(env):
    tok = make_token(M1, 1)
    mid, sig = parse_token(tok)
    assert mid == M1 and len(sig) == 32
    assert M1 not in tok  # el id va sin guiones; la firma no revela el secreto
    with pytest.raises(PortalError):
        parse_token(tok[:-1] + ("A" if tok[-1] != "A" else "B") + "x")
    with pytest.raises(PortalError):
        parse_token("../../etc")


def test_token_requires_secret(monkeypatch):
    monkeypatch.delenv("PORTAL_TOKEN_SECRET", raising=False)
    with pytest.raises(PortalError) as e:
        make_token(M1, 1)
    assert e.value.status == 503
    monkeypatch.setenv("PORTAL_TOKEN_SECRET", "short")
    with pytest.raises(PortalError):
        make_token(M1, 1)


def test_signature_from_other_secret_rejected(env, monkeypatch):
    db, portal, _ = env
    monkeypatch.setenv("PORTAL_TOKEN_SECRET", "y" * 40)
    forged = make_token(M1, 1)
    monkeypatch.setenv("PORTAL_TOKEN_SECRET", SECRET)
    with pytest.raises(PortalError):
        portal.verify_token(forged)


def test_regenerate_revokes_old_link(env):
    db, portal, _ = env
    old = make_token(M1, 1)
    assert portal.verify_token(old)["id"] == M1
    portal.regenerate(db.member(M1))
    assert db.member(M1)["portal_token_version"] == 2
    with pytest.raises(PortalError):
        portal.verify_token(old)
    assert portal.verify_token(make_token(M1, 2))["id"] == M1


def test_cancelled_member_link_rejected(env):
    _, portal, _ = env
    with pytest.raises(PortalError) as e:
        portal.verify_token(make_token(M2, 1))
    assert e.value.code == "membership_inactive"


# ---------------------------------------------------------------------------
# Login por QR
# ---------------------------------------------------------------------------
def test_qr_get_has_no_side_effects(env):
    """El GET (lo que hacen los escáneres/vistas previas) solo sirve la página."""
    db, portal, _ = env
    c = TestClient(main.app)
    r = c.get(f"/m/q/{make_token(M1, 1)}", follow_redirects=False)
    assert r.status_code == 200 and "Portal del socio" in r.text
    assert r.headers["Cache-Control"] == "no-store"
    m = db.member(M1)
    assert m["user_id"] is None and m["portal_activated_at"] is None and not db.auth_calls


def test_qr_login_post_creates_user_and_returns_token(env):
    db, portal, _ = env
    c = TestClient(main.app)
    r = c.post("/api/member/qr-login", json={"token": make_token(M1, 1)})
    assert r.status_code == 200 and r.json()["token_hash"].startswith("hash-")
    m = db.member(M1)
    assert m["user_id"] and db.users[m["user_id"]]["email"] == "ana@x.com"
    assert m["portal_activated_at"] and m["portal_last_used_at"]
    uid = m["user_id"]
    c.post("/api/member/qr-login", json={"token": make_token(M1, 1)})
    assert db.member(M1)["user_id"] == uid


def test_qr_invalid_and_revoked(env):
    db, portal, _ = env
    c = TestClient(main.app)
    assert c.post("/api/member/qr-login", json={"token": "not-a-token"}).json() == {"error": "invalid_link"}
    old = make_token(M1, 1)
    portal.regenerate(db.member(M1))
    r = c.post("/api/member/qr-login", json={"token": old})
    assert r.status_code == 404 and r.json()["error"] == "invalid_link"


def test_qr_rate_limited(env):
    c = TestClient(main.app)
    for _ in range(20):
        c.post("/api/member/qr-login", json={"token": "bad"})
    r = c.post("/api/member/qr-login", json={"token": "bad"})
    assert r.status_code == 429


def test_member_without_email_gets_alias(env):
    db, portal, _ = env
    db.member(M1)["email"] = None
    portal.login_token_for(make_token(M1, 1))
    uid = db.member(M1)["user_id"]
    assert db.users[uid]["email"].endswith("@members.aita-nexxus.app")
    # luego añade su email real: se actualiza el usuario de Auth
    db.member(M1)["email"] = "ana@x.com"
    portal.login_token_for(make_token(M1, 1))
    assert db.users[uid]["email"] == "ana@x.com"


# ---------------------------------------------------------------------------
# Staff
# ---------------------------------------------------------------------------
def _hdr(jwt):
    return {"Authorization": f"Bearer {jwt}"}


def test_staff_endpoints_authz(env):
    c = TestClient(main.app)
    url = f"/api/manager/members/{M1}/portal-link"
    assert c.get(url).status_code == 401
    assert c.get(url, headers=_hdr("jwt-staff")).status_code == 403        # rol staff: no
    assert c.get(url, headers=_hdr("jwt-other")).status_code == 403        # otro tenant: no
    assert c.get(url, headers=_hdr("jwt-member")).status_code == 403       # un socio: no
    r = c.get(url, headers=_hdr("jwt-owner"))
    assert r.status_code == 200
    assert r.json()["link"] == f"https://portal.example/m/q/{make_token(M1, 1)}"
    assert r.json()["last_sms"] is None
    assert c.get("/api/manager/members/nope/portal-link", headers=_hdr("jwt-owner")).status_code == 404


def test_send_access_sms_and_email(env):
    db, _, sent = env
    c = TestClient(main.app)
    r = c.post(f"/api/manager/members/{M1}/portal-access", json={"sms": True, "email": True}, headers=_hdr("jwt-owner"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["sms"]["ok"] and body["sms"]["status"] == "queued" and body["email"]["ok"]
    link = f"https://portal.example/m/q/{make_token(M1, 1)}"
    assert body["link"] == link
    to, frm, text = sent[0]
    assert to == "+18325551234" and frm == "+13462457940" and link in text
    otp = [a for a in db.auth_calls if a[1] == "/otp"][0]
    assert otp[2] == {"email": "ana@x.com", "create_user": False} and otp[4] is True
    assert otp[3]["redirect_to"] == "https://portal.example/m/"
    log = db.tables["sms_messages"][0]
    assert log["status"] == "queued" and log["to_masked"].endswith("1234") and "+18325551234" not in str(log)
    assert "body" not in log and link not in str(log), "nunca se guarda el enlace ni el cuerpo"
    assert db.member(M1)["portal_invited_at"]


def test_send_access_without_contact(env):
    db, _, sent = env
    db.member(M1)["phone"] = None
    db.member(M1)["email"] = None
    r = TestClient(main.app).post(f"/api/manager/members/{M1}/portal-access", json={"sms": True, "email": True},
                                  headers=_hdr("jwt-owner"))
    assert r.json()["sms"]["detail"] == "no_phone" and r.json()["email"]["detail"] == "no_email"
    assert r.json()["sms"]["status"] == "failed"
    assert not sent and db.member(M1)["portal_invited_at"] is None


def test_regenerate_endpoint(env):
    db, _, _ = env
    c = TestClient(main.app)
    r = c.post(f"/api/manager/members/{M1}/portal-access", json={"regenerate": True}, headers=_hdr("jwt-owner"))
    assert r.status_code == 200 and r.json()["regenerated"]
    assert db.member(M1)["portal_token_version"] == 2
    assert r.json()["link"].endswith(make_token(M1, 2))
    assert c.post(f"/api/manager/members/{M1}/portal-access", json={"regenerate": True},
                  headers=_hdr("jwt-staff")).status_code == 403
    assert db.member(M1)["portal_token_version"] == 2


def test_public_url_must_be_https_production(env, monkeypatch):
    for bad in ("http://portal.example", "https://localhost:8000", "https://127.0.0.1"):
        monkeypatch.setenv("PUBLIC_BASE_URL", bad)
        r = TestClient(main.app).post(f"/api/manager/members/{M1}/portal-access", json={"sms": True}, headers=_hdr("jwt-owner"))
        assert r.status_code == 500 and r.json()["error"] == "invalid_public_url", bad


# ---------------------------------------------------------------------------
# Socio
# ---------------------------------------------------------------------------
def test_member_own_qr(env):
    c = TestClient(main.app)
    assert c.get("/api/member/qr").status_code == 401
    assert c.get("/api/member/qr", headers=_hdr("jwt-owner")).status_code == 403  # staff sin ficha de socio
    r = c.get("/api/member/qr", headers=_hdr("jwt-member"))
    assert r.status_code == 200 and r.json()["link"].endswith(make_token(M3, 1))
    assert r.headers["Cache-Control"] == "no-store"


def test_portal_not_configured_returns_503(env, monkeypatch):
    monkeypatch.delenv("PORTAL_TOKEN_SECRET")
    c = TestClient(main.app)
    assert c.get("/api/member/qr", headers=_hdr("jwt-member")).status_code == 503
    r = c.post("/api/member/qr-login", json={"token": f"{'a' * 32}.{'b' * 32}"})
    assert r.status_code == 503 and r.json()["error"] == "portal_not_configured"


def test_member_page_served_with_security_headers(env):
    c = TestClient(main.app)
    r = c.get("/m/")
    assert r.status_code == 200 and "Portal del socio" in r.text
    assert "script-src 'self'" in r.headers["Content-Security-Policy"]
    assert r.headers["X-Frame-Options"] == "DENY"
    for path in ("/m/assets/js/app.js", "/m/assets/js/i18n.js", "/m/assets/vendor/qrcode.js",
                 "/m/assets/vendor/supabase.js", "/m/assets/css/member.css"):
        assert c.get(path).status_code == 200, path
    # la página no lleva secretos ni datos
    assert "service_role" not in r.text and "sb_secret" not in r.text
    assert "service_role" not in c.get("/m/assets/js/app.js").text


# ---------------------------------------------------------------------------
# SMS: formato, estados de entrega y callback firmado
# ---------------------------------------------------------------------------
def test_e164():
    assert mp.e164("(832) 555-1234") == "+18325551234"
    assert mp.e164("1 832 555 1234") == "+18325551234"
    assert mp.e164("+52 55 1234 5678") == "+525512345678"
    for bad in ("555-1234", "(123) 555-1234", "832 155 1234", "", None, "+1 832 555 12"):
        assert mp.e164(bad) is None, bad


def test_sms_states_and_safe_errors():
    assert [mp.sms_state(x) for x in ("accepted", "queued", "sending", "sent", "delivered", "undelivered", "failed")] == \
           ["queued", "queued", "sent", "sent", "delivered", "failed", "failed"]
    err = mp._safe_err("Unable to create record: https://portal.example/m/q/abc.def token SMabcdefabcdefabcdefabcdef0123")
    assert "https://" not in err and "SMabcdef" not in err


def _signed(url, params):
    from twilio.request_validator import RequestValidator
    return RequestValidator("tok123").compute_signature(url, params)


def test_status_callback_requires_valid_signature(env, monkeypatch):
    db, portal, _ = env
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "tok123")
    db.tables["sms_messages"] = [{"id": "s1", "tenant_id": T1, "member_id": M1, "message_sid": "SMx1", "status": "queued"}]
    c = TestClient(main.app)
    url = "https://portal.example/webhooks/twilio/sms-status"
    params = {"MessageSid": "SMx1", "MessageStatus": "undelivered", "ErrorCode": "30034"}
    assert c.post("/webhooks/twilio/sms-status", data=params, headers={"X-Twilio-Signature": "bad"}).status_code == 403
    assert db.tables["sms_messages"][0]["status"] == "queued"
    r = c.post("/webhooks/twilio/sms-status", data=params, headers={"X-Twilio-Signature": _signed(url, params)})
    assert r.status_code == 204
    row = db.tables["sms_messages"][0]
    assert row["status"] == "failed" and row["error_code"] == 30034 and row["twilio_status"] == "undelivered"
    params2 = {"MessageSid": "SMx1", "MessageStatus": "delivered"}
    c.post("/webhooks/twilio/sms-status", data=params2, headers={"X-Twilio-Signature": _signed(url, params2)})
    assert db.tables["sms_messages"][0]["status"] == "delivered" and db.tables["sms_messages"][0]["delivered_at"]
    # el manager ve el último estado (sin SID completo)
    last = TestClient(main.app).get(f"/api/manager/members/{M1}/portal-link", headers=_hdr("jwt-owner")).json()["last_sms"]
    assert last["status"] == "delivered" and last["sid_tail"] == "SMx1"[-6:] and "message_sid" not in last


def test_twilio_rejection_is_failed_not_ok(env):
    db, portal, _ = env
    portal._sms_sender = lambda to, frm, body: {"ok": False, "twilio_status": "failed", "error_code": 21608, "detail": "twilio_error"}
    r = TestClient(main.app).post(f"/api/manager/members/{M1}/portal-access", json={"sms": True}, headers=_hdr("jwt-owner")).json()
    assert r["sms"]["ok"] is False and r["sms"]["status"] == "failed" and r["sms"]["error_code"] == 21608
    assert db.tables["sms_messages"][-1]["error_code"] == 21608


# ---------------------------------------------------------------------------
# Evaluaciones: invitación, invitación automática y PDF protegido
# ---------------------------------------------------------------------------
def test_evaluation_invite_link_and_log(env):
    db, portal, sent = env
    r = TestClient(main.app).post(f"/api/manager/members/{M1}/evaluation-invite", json={"sms": True, "email": True}, headers=_hdr("jwt-owner"))
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == "initial"
    assert f"https://portal.example/m/q/{make_token(M1, 1)}?next=evaluation" in sent[0][2]
    otp = [a for a in db.auth_calls if a[1] == "/otp"][0]
    assert otp[3]["redirect_to"] == "https://portal.example/m/?next=evaluation"
    inv = db.tables["evaluation_invitations"][0]
    assert inv["member_id"] == M1 and inv["channel"] == "sms+email" and inv["auto"] is False
    assert db.member(M1)["portal_invited_at"] is None, "la invitación a evaluar no cambia el estado de acceso"
    assert TestClient(main.app).post(f"/api/manager/members/{M1}/evaluation-invite", json={"sms": True},
                                     headers=_hdr("jwt-staff")).status_code == 403


def test_auto_invitations_once_per_due_date(env):
    db, portal, sent = env
    db.member(M1)["next_evaluation_due"] = "2026-09-01"
    db.member(M2)["next_evaluation_due"] = "2026-09-01"   # cancelado: no se invita
    assert portal.run_due_invitations("2026-09-29") == {"sent": 1, "skipped": 1}
    assert portal.run_due_invitations("2026-09-30") == {"sent": 0, "skipped": 2}
    assert len([i for i in db.tables["evaluation_invitations"] if i["auto"]]) == 1
    assert len(sent) == 1


def _add_eval(db, member_id, tenant_id):
    ev = {"id": str(uuid.uuid4()), "tenant_id": tenant_id, "member_id": member_id, "kind": "initial", "status": "submitted",
          "questionnaire_id": None, "questionnaire_version": None, "answers": {"q": {}, "body": {"weight_lb": 180}, "progress": []},
          "measurement_id": "meas1", "submitted_at": "2026-09-29T17:00:00+00:00", "next_due_date": "2026-12-28"}
    db.tables.setdefault("member_evaluations", []).append(ev)
    db.tables.setdefault("measurements", []).append({"id": "meas1", "member_id": member_id, "tenant_id": tenant_id, "weight_lb": 180,
        "waist_in": 38, "height_in": 69, "left_arm_in": None, "right_arm_in": None, "left_leg_in": None, "right_leg_in": None,
        "is_baseline": True, "source": "member", "recorded_by_name": "Zoe", "measurement_date": "2026-09-29"})
    db.tables.setdefault("progress_entries", []).append({"id": "p1", "evaluation_id": ev["id"], "category": "strength", "metric": "exercise",
        "exercise": "Leg press", "value": 90, "reps": 10, "conditions": "Máquina 2", "source": "member"})
    return ev


def test_evaluation_pdf_permissions(env):
    db, portal, _ = env
    ev = _add_eval(db, M3, T2)          # evaluación de Zoe (tenant B, usuario jwt-member)
    c = TestClient(main.app)
    r = c.get(f"/api/member/evaluations/{ev['id']}/pdf", headers=_hdr("jwt-member"))
    assert r.status_code == 200 and r.content.startswith(b"%PDF") and r.headers["Cache-Control"] == "no-store"
    assert "attachment" in r.headers["Content-Disposition"]
    assert c.get(f"/api/member/evaluations/{ev['id']}/pdf").status_code == 401
    assert c.get(f"/api/member/evaluations/{ev['id']}/pdf", headers=_hdr("jwt-owner")).status_code == 403   # no es socio
    assert c.get(f"/api/manager/evaluations/{ev['id']}/pdf", headers=_hdr("jwt-owner")).status_code == 403  # otro gym
    assert c.get(f"/api/manager/evaluations/{ev['id']}/pdf", headers=_hdr("jwt-other")).status_code == 200  # owner de ese gym
    ev1 = _add_eval(db, M1, T1)
    assert c.get(f"/api/member/evaluations/{ev1['id']}/pdf", headers=_hdr("jwt-member")).status_code == 404  # de otra socia
    assert c.get("/api/member/evaluations/nope/pdf", headers=_hdr("jwt-member")).status_code == 404


def test_evaluation_pdf_content():
    from services.evaluation_pdf import build_evaluation_pdf
    bundle = {"evaluation": {"id": "e" * 8, "kind": "reevaluation", "submitted_at": "2026-12-28T10:00:00", "next_due_date": "2027-03-28",
                             "questionnaire_version": 1, "answers": {"q": {"q1": "a"}}},
              "member": {"first_name": "Ana", "last_name": "Díaz", "member_id": "GA-1"},
              "tenant": {"name": "Golden Age", "branding": {"display_name": "GOLDEN AGE"}},
              "questionnaire": {"version": 1, "title": {"es": "Prueba"}, "definition": {"sections": [{"title": {"es": "S"}, "questions": [
                  {"id": "q1", "type": "single", "label": {"es": "¿Pregunta?"}, "options": [{"value": "a", "label": {"es": "Opción A"}}]}]}]}},
              "measurement": {"id": "m2", "weight_lb": 176, "waist_in": 36, "source": "staff", "recorded_by_name": "Edgar", "measurement_date": "2026-12-28"},
              "baseline_measurement": {"id": "m1", "weight_lb": 180, "waist_in": 38},
              "progress": [{"category": "endurance", "metric": "walk", "value": 25, "source": "member"}]}
    pdf = build_evaluation_pdf(bundle, "es")
    assert pdf.startswith(b"%PDF") and len(pdf) > 1500
