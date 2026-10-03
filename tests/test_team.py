"""
Tests del equipo del Manager Panel: invitar gerentes, permisos por rol,
cuentas que ya existían y quitar accesos. Sin red: Supabase en memoria.
"""
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import main
from services.member_portal import PortalError
from services.team import TeamService

T1, T2 = "t-1", "t-2"
OWNER, MANAGER, STAFF, OTHER = "u-owner", "u-manager", "u-staff", "u-other"


class FakeDB:
    def __init__(self):
        self.tables = {
            "tenant_users": [
                {"tenant_id": T1, "user_id": OWNER, "role": "owner", "active": True, "created_at": "2026-09-01"},
                {"tenant_id": T1, "user_id": MANAGER, "role": "manager", "active": True, "created_at": "2026-09-02"},
                {"tenant_id": T1, "user_id": STAFF, "role": "staff", "active": True, "created_at": "2026-09-03"},
                {"tenant_id": T2, "user_id": OTHER, "role": "owner", "active": True, "created_at": "2026-09-04"},
            ],
            "tenant_invites": [],
        }
        self.users = {u: {"id": u, "email": f"{u}@x.com"} for u in (OWNER, MANAGER, STAFF, OTHER)}
        self.jwts = {f"jwt-{u}": u for u in self.users}
        self.calls = []
        self.invite_status = 200

    @staticmethod
    def _match(row, filters):
        for k, v in filters.items():
            if k in ("select", "limit", "order"):
                continue
            op, _, val = v.partition(".")
            cur = row.get(k)
            if op == "eq" and str(cur).lower() != val.lower():
                return False
            if op == "in" and str(cur) not in val.strip("()").split(","):
                return False
            if op == "is" and cur is not None:
                return False
        return True

    def select(self, table, params):
        return [dict(r) for r in self.tables.get(table, []) if self._match(r, params)][: int(params.get("limit", 1000))]

    def insert(self, table, row):
        row = dict(row, id=str(uuid.uuid4()), created_at="2026-10-03", accepted_at=None)
        self.tables.setdefault(table, []).append(row)
        return dict(row)

    def upsert(self, table, row, on_conflict):
        keys = on_conflict.split(",")
        for r in self.tables.setdefault(table, []):
            if all(r.get(k) == row[k] for k in keys):
                r.update(row)
                return dict(r)
        return self.insert(table, row)

    def update(self, table, filters, values):
        out = []
        for r in self.tables.get(table, []):
            if self._match(r, filters):
                r.update(values)
                out.append(dict(r))
        return out

    def delete(self, table, filters):
        keep = [r for r in self.tables.get(table, []) if not self._match(r, filters)]
        gone = len(self.tables.get(table, [])) - len(keep)
        self.tables[table] = keep
        return [{}] * gone

    def user_from_jwt(self, jwt):
        uid = self.jwts.get(jwt)
        return {"id": uid} if uid else None

    def auth(self, method, path, *, json=None, params=None, anon=False, bearer=None):
        self.calls.append((method, path, json, params, anon))
        if method == "GET" and path.startswith("/admin/users/"):
            u = self.users.get(path.rsplit("/", 1)[1])
            return (200, u) if u else (404, None)
        if path == "/invite":
            if any(u["email"] == json["email"] for u in self.users.values()):
                return 422, {"error_code": "email_exists"}
            return self.invite_status, {}
        if path == "/admin/generate_link":
            u = next((u for u in self.users.values() if u["email"] == json["email"]), None)
            return (200, {"id": u["id"]}) if u else (404, None)
        if path == "/recover":
            return 200, {}
        return 404, None


@pytest.fixture
def db():
    return FakeDB()


@pytest.fixture
def team(db):
    return TeamService(db)


def test_owner_invites_new_manager(team, db):
    r = team.invite(f"jwt-{OWNER}", T1, " New@Gym.com ", "manager", "https://portal.example")
    assert r == {"status": "invited", "email": "new@gym.com", "role": "manager"}
    inv = db.tables["tenant_invites"]
    assert len(inv) == 1 and inv[0]["email"] == "new@gym.com" and inv[0]["role"] == "manager"
    invite_call = next(c for c in db.calls if c[1] == "/invite")
    assert invite_call[3] == {"redirect_to": "https://portal.example/manager"}


def test_reinvite_replaces_pending_invite(team, db):
    team.invite(f"jwt-{OWNER}", T1, "new@gym.com", "staff", "https://p.example")
    team.invite(f"jwt-{OWNER}", T1, "new@gym.com", "manager", "https://p.example")
    inv = db.tables["tenant_invites"]
    assert len(inv) == 1 and inv[0]["role"] == "manager"


def test_existing_account_gets_access_and_recovery_email(team, db):
    db.users["u-x"] = {"id": "u-x", "email": "known@gym.com"}
    r = team.invite(f"jwt-{MANAGER}", T1, "known@gym.com", "staff", "https://p.example")
    assert r["status"] == "existing_account" and r["email_sent"]
    link = [u for u in db.tables["tenant_users"] if u["user_id"] == "u-x"]
    assert link == [dict(link[0], tenant_id=T1, role="staff", active=True)]
    assert db.tables["tenant_invites"][0]["accepted_at"]
    assert any(c[1] == "/recover" and c[4] for c in db.calls)


def test_reinviting_removed_person_reactivates(team, db):
    team.revoke(f"jwt-{OWNER}", T1, MANAGER)
    team.invite(f"jwt-{OWNER}", T1, f"{MANAGER}@x.com", "manager", "https://p.example")
    row = next(u for u in db.tables["tenant_users"] if u["user_id"] == MANAGER)
    assert row["active"] is True


@pytest.mark.parametrize("who,role,code", [
    (MANAGER, "owner", "role_not_allowed"),   # un manager no crea owners
    (STAFF, "staff", "forbidden"),            # staff no invita
    (OTHER, "manager", "forbidden"),          # owner de otro gimnasio
])
def test_invite_permissions(team, who, role, code):
    with pytest.raises(PortalError) as e:
        team.invite(f"jwt-{who}", T1, "a@b.com", role, "https://p.example")
    assert e.value.code == code


def test_invalid_email_and_bad_token(team):
    with pytest.raises(PortalError) as e:
        team.invite(f"jwt-{OWNER}", T1, "not-an-email", "manager", "https://p.example")
    assert e.value.code == "invalid_email"
    with pytest.raises(PortalError) as e:
        team.list("bad-token", T1)
    assert e.value.code == "unauthorized"


def test_invite_email_failure_is_reported(team, db):
    db.invite_status = 429
    with pytest.raises(PortalError) as e:
        team.invite(f"jwt-{OWNER}", T1, "new@gym.com", "manager", "https://p.example")
    assert e.value.code == "rate_limited"


def test_list_shows_people_and_pending(team):
    team.invite(f"jwt-{OWNER}", T1, "new@gym.com", "manager", "https://p.example")
    d = team.list(f"jwt-{MANAGER}", T1)
    assert d["me"] == {"role": "manager"}
    assert [p["email"] for p in d["members"]] == [f"{OWNER}@x.com", f"{MANAGER}@x.com", f"{STAFF}@x.com"]
    assert [p["is_me"] for p in d["members"]] == [False, True, False]
    assert [i["email"] for i in d["pending"]] == ["new@gym.com"]


def test_revoke_rules(team, db):
    with pytest.raises(PortalError) as e:
        team.revoke(f"jwt-{MANAGER}", T1, STAFF)          # solo owners quitan acceso
    assert e.value.code == "forbidden"
    with pytest.raises(PortalError) as e:
        team.revoke(f"jwt-{OWNER}", T1, OWNER)            # nunca a uno mismo
    assert e.value.code == "cannot_remove_self"
    assert team.revoke(f"jwt-{OWNER}", T1, STAFF) == {"ok": True}
    assert next(u for u in db.tables["tenant_users"] if u["user_id"] == STAFF)["active"] is False
    with pytest.raises(PortalError) as e:
        team.list(f"jwt-{STAFF}", T1)                     # ya no entra
    assert e.value.code == "forbidden"


def test_cancel_invite(team, db):
    team.invite(f"jwt-{OWNER}", T1, "new@gym.com", "manager", "https://p.example")
    inv_id = db.tables["tenant_invites"][0]["id"]
    assert team.cancel_invite(f"jwt-{MANAGER}", T1, inv_id) == {"ok": True}
    assert db.tables["tenant_invites"] == []
    with pytest.raises(PortalError):
        team.cancel_invite(f"jwt-{OWNER}", T1, inv_id)


def test_endpoints(monkeypatch, db):
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://portal.example")
    monkeypatch.setattr(main, "member_portal", SimpleNamespace(db=db))
    c = TestClient(main.app)
    h = {"Authorization": f"Bearer jwt-{OWNER}"}
    r = c.post("/api/manager/team/invite", json={"tenant_id": T1, "email": "new@gym.com", "role": "manager"}, headers=h)
    assert r.status_code == 200 and r.json()["status"] == "invited"
    r = c.get(f"/api/manager/team?tenant_id={T1}", headers=h)
    assert r.status_code == 200 and len(r.json()["pending"]) == 1
    r = c.post(f"/api/manager/team/{STAFF}/revoke", json={"tenant_id": T1}, headers=h)
    assert r.status_code == 200
    r = c.get(f"/api/manager/team?tenant_id={T1}", headers={"Authorization": f"Bearer jwt-{STAFF}"})
    assert r.status_code == 403 and r.json() == {"error": "forbidden"}
    r = c.get(f"/api/manager/team?tenant_id={T1}")
    assert r.status_code == 401
