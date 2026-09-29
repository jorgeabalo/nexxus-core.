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
            if k in ("select", "limit"):
                continue
            op, _, val = v.partition(".")
            cur = row.get(k)
            if op == "eq":
                if str(cur).lower() != val.lower() if isinstance(cur, bool) else str(cur) != val:
                    return False
            elif op == "in":
                if str(cur) not in val.strip("()").split(","):
                    return False
        return True

    def select(self, table, params):
        rows = [dict(r) for r in self.tables.get(table, []) if self._match(r, params)]
        return rows[: int(params.get("limit", 1000))]

    def update(self, table, filters, values):
        out = []
        for r in self.tables[table]:
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
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    db = FakeDB()
    sent = []
    portal = MemberPortal(db, sms_sender=lambda to, frm, body: sent.append((to, frm, body)) or {"ok": True, "detail": "queued"})
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
def test_qr_login_creates_user_and_redirects(env):
    db, portal, _ = env
    c = TestClient(main.app)
    r = c.get(f"/m/q/{make_token(M1, 1)}", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/m/#login=hash-")
    m = db.member(M1)
    assert m["user_id"] and db.users[m["user_id"]]["email"] == "ana@x.com"
    assert m["portal_activated_at"] and m["portal_last_used_at"]
    assert r.headers["Cache-Control"] == "no-store"
    # segundo uso: reutiliza el mismo usuario
    uid = m["user_id"]
    c.get(f"/m/q/{make_token(M1, 1)}", follow_redirects=False)
    assert db.member(M1)["user_id"] == uid


def test_qr_invalid_and_revoked_redirect_to_error(env):
    db, portal, _ = env
    c = TestClient(main.app)
    r = c.get("/m/q/not-a-token", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/m/#error=invalid_link"
    old = make_token(M1, 1)
    portal.regenerate(db.member(M1))
    r = c.get(f"/m/q/{old}", follow_redirects=False)
    assert r.headers["location"] == "/m/#error=invalid_link"


def test_qr_rate_limited(env):
    c = TestClient(main.app)
    for _ in range(20):
        c.get("/m/q/bad", follow_redirects=False)
    r = c.get("/m/q/bad", follow_redirects=False)
    assert r.headers["location"] == "/m/#error=rate_limited"


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
    assert r.json()["link"] == f"http://testserver/m/q/{make_token(M1, 1)}"
    assert c.get("/api/manager/members/nope/portal-link", headers=_hdr("jwt-owner")).status_code == 404


def test_send_access_sms_and_email(env):
    db, _, sent = env
    c = TestClient(main.app)
    r = c.post(f"/api/manager/members/{M1}/portal-access", json={"sms": True, "email": True},
               headers={**_hdr("jwt-owner"), "X-Forwarded-Proto": "https", "X-Forwarded-Host": "gym.example"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["sms"]["ok"] and body["email"]["ok"]
    link = f"https://gym.example/m/q/{make_token(M1, 1)}"
    assert body["link"] == link
    to, frm, text = sent[0]
    assert to == "+18325551234" and frm == "+13462457940" and link in text
    otp = [a for a in db.auth_calls if a[1] == "/otp"][0]
    assert otp[2] == {"email": "ana@x.com", "create_user": False} and otp[4] is True
    assert otp[3]["redirect_to"] == "https://gym.example/m"
    assert db.member(M1)["portal_invited_at"]


def test_send_access_without_contact(env):
    db, _, sent = env
    db.member(M1)["phone"] = None
    db.member(M1)["email"] = None
    r = TestClient(main.app).post(f"/api/manager/members/{M1}/portal-access", json={"sms": True, "email": True},
                                  headers=_hdr("jwt-owner"))
    assert r.json()["sms"]["detail"] == "no_phone" and r.json()["email"]["detail"] == "no_email"
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


def test_public_base_url_override(env, monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://portal.example/")
    r = TestClient(main.app).get(f"/api/manager/members/{M1}/portal-link", headers=_hdr("jwt-owner"))
    assert r.json()["link"].startswith("https://portal.example/m/q/")


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
    r = c.get(f"/m/q/{'a' * 32}.{'b' * 32}", follow_redirects=False)
    assert r.headers["location"] == "/m/#error=portal_not_configured"


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
