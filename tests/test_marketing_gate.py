"""
AITA Marketing — puerta única de acceso, simulando producción:

* Marketing solo se abre con tenants.modules.marketing === true. Ausente, null, "true", 1 o
  modules mal formado => 403 marketing_disabled y CERO consultas a tablas marketing_*.
* Habilitado pero sin tablas (migración sin aplicar) => 503 marketing_unavailable, sin detalles.
* Solo "tabla inexistente" se convierte en 503: RLS/permisos/conexión siguen siendo 500.
* Habilitado y con tablas => funcionamiento normal. Staff y socio siempre rechazados.
"""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import main
from services.marketing import MarketingService
from services.marketing_gate import marketing_module_enabled
from test_marketing import FakeDB, NOW, T1, OWNER, MANAGER, STAFF, MEMBER

MISSING = 'Supabase GET {t} -> 404: {{"code":"PGRST205","message":"Could not find the table \'public.{t}\'"}}'


class ProdLikeDB(FakeDB):
    """FakeDB que registra cada acceso a marketing_* y puede simular tablas inexistentes u otros fallos."""

    def __init__(self, modules=None, tables_exist=True, failure=None, missing_only=None):
        super().__init__()
        self.tables["tenants"][0]["modules"] = modules
        self.tables_exist, self.failure, self.missing_only = tables_exist, failure, missing_only
        self.marketing_calls = []

    def _guard(self, table):
        if not str(table).startswith("marketing_"):
            return
        self.marketing_calls.append(table)
        if self.failure:
            raise RuntimeError(self.failure.format(t=table))
        if not self.tables_exist or table == self.missing_only:
            raise RuntimeError(MISSING.format(t=table))

    def select(self, t, p):
        self._guard(t); return super().select(t, p)

    def insert(self, t, r):
        self._guard(t); return super().insert(t, r)

    def upsert(self, t, r, c):
        self._guard(t); return super().upsert(t, r, c)

    def update(self, t, f, v):
        self._guard(t); return super().update(t, f, v)


CID = "00000000-0000-0000-0000-000000000001"
ENDPOINTS = [
    ("GET", f"/api/manager/marketing/dashboard?tenant_id={T1}", None),
    ("GET", f"/api/manager/marketing/brand?tenant_id={T1}", None),
    ("PUT", "/api/manager/marketing/brand", {"tenant_id": T1, "tone": "Claro"}),
    ("GET", f"/api/manager/marketing/campaigns?tenant_id={T1}", None),
    ("POST", "/api/manager/marketing/campaigns", {"tenant_id": T1, "name": "Octubre"}),
    ("PATCH", f"/api/manager/marketing/campaigns/{CID}", {"tenant_id": T1, "name": "x"}),
    ("GET", f"/api/manager/marketing/content?tenant_id={T1}", None),
    ("POST", "/api/manager/marketing/content", {"tenant_id": T1, "title": "Post", "format": "text"}),
    ("GET", f"/api/manager/marketing/content/{CID}?tenant_id={T1}", None),
    ("PATCH", f"/api/manager/marketing/content/{CID}", {"tenant_id": T1, "title": "x"}),
    ("POST", f"/api/manager/marketing/content/{CID}/transition", {"tenant_id": T1, "to": "review"}),
    ("GET", f"/api/manager/marketing/calendar?tenant_id={T1}&start=2026-10-01&end=2026-10-31", None),
]


def client_for(monkeypatch, db):
    monkeypatch.setattr(main, "member_portal", SimpleNamespace(db=db))
    monkeypatch.setattr("services.marketing_routes.MarketingService", lambda d: MarketingService(d, now=NOW))
    return TestClient(main.app)


def call(client, method, path, body, user=OWNER):
    return client.request(method, path, json=body, headers={"Authorization": f"Bearer jwt-{user}"})


# ---------------------------------------------------------------- regla pura
@pytest.mark.parametrize("tenant,expected", [
    ({"modules": {"marketing": True}}, True),
    ({"modules": {"marketing": False}}, False),
    ({"modules": {}}, False), ({"modules": None}, False), ({}, False), (None, False),
    ({"modules": {"marketing": None}}, False), ({"modules": {"marketing": "true"}}, False),
    ({"modules": {"marketing": 1}}, False), ({"modules": {"marketing": "yes"}}, False),
    ({"modules": ["marketing"]}, False), ({"modules": "marketing"}, False), ({"modules": True}, False),
])
def test_marketing_enabled_only_when_exactly_true(tenant, expected):
    assert marketing_module_enabled(tenant) is expected


# ---------------------------------------------------------------- desactivado: 403 y cero consultas
@pytest.mark.parametrize("method,path,body", ENDPOINTS)
@pytest.mark.parametrize("user", [OWNER, MANAGER])
def test_disabled_and_tables_missing_is_403_without_touching_marketing_tables(monkeypatch, method, path, body, user):
    db = ProdLikeDB(modules={"marketing": False}, tables_exist=False)   # como Golden Age hoy en producción
    r = call(client_for(monkeypatch, db), method, path, body, user)
    assert r.status_code == 403 and r.json() == {"error": "marketing_disabled"}
    assert db.marketing_calls == []


@pytest.mark.parametrize("modules", [None, {}, {"marketing": None}, {"marketing": "true"}, {"marketing": 1},
                                     ["marketing"], "marketing", {"accounting": True}])
def test_missing_null_or_malformed_flag_is_403(monkeypatch, modules):
    db = ProdLikeDB(modules=modules, tables_exist=False)
    client = client_for(monkeypatch, db)
    for method, path, body in ENDPOINTS:
        r = call(client, method, path, body)
        assert r.status_code == 403 and r.json() == {"error": "marketing_disabled"}, (modules, path)
    assert db.marketing_calls == []


# ---------------------------------------------------------------- habilitado sin tablas: 503 controlado
@pytest.mark.parametrize("method,path,body", ENDPOINTS)
def test_enabled_but_tables_missing_is_controlled_503(monkeypatch, method, path, body):
    db = ProdLikeDB(modules={"marketing": True}, tables_exist=False)
    r = call(client_for(monkeypatch, db), method, path, body)
    assert r.status_code == 503 and r.json() == {"error": "marketing_unavailable"}
    assert "PGRST205" not in r.text and "public." not in r.text            # sin detalles internos


def test_partial_migration_is_also_503(monkeypatch):
    db = ProdLikeDB(modules={"marketing": True}, missing_only="marketing_content")
    r = call(client_for(monkeypatch, db), "GET", f"/api/manager/marketing/content?tenant_id={T1}", None)
    assert r.status_code == 503 and r.json() == {"error": "marketing_unavailable"}


@pytest.mark.parametrize("failure", [
    "Supabase GET {t} -> 403: permission denied for table {t} (42501)",          # RLS / permisos
    "Supabase GET {t} -> 401: JWT expired",
    "Supabase GET {t}: ConnectError: connection refused",                           # conexión
    "Supabase GET {t} -> 500: internal error",
    "Supabase GET {t} -> 404: no rows",                                             # 404 que no es tabla inexistente
])
def test_other_errors_are_not_disguised_as_missing_migration(monkeypatch, failure):
    db = ProdLikeDB(modules={"marketing": True}, failure=failure)
    r = call(client_for(monkeypatch, db), "GET", f"/api/manager/marketing/dashboard?tenant_id={T1}", None)
    assert r.status_code == 500 and r.json() == {"error": "server_error"}


# ---------------------------------------------------------------- habilitado con tablas: normal
def test_enabled_with_tables_works_normally(monkeypatch):
    db = ProdLikeDB(modules={"marketing": True})
    client = client_for(monkeypatch, db)
    assert call(client, "GET", f"/api/manager/marketing/dashboard?tenant_id={T1}", None).status_code == 200
    r = call(client, "POST", "/api/manager/marketing/content", {"tenant_id": T1, "title": "Post", "format": "text"})
    assert r.status_code == 200 and r.json()["status"] == "draft"
    cid = r.json()["id"]
    assert call(client, "POST", f"/api/manager/marketing/content/{cid}/transition",
                {"tenant_id": T1, "to": "review"}).json()["status"] == "review"
    assert call(client, "GET", f"/api/manager/marketing/brand?tenant_id={T1}", None).status_code == 200
    assert db.marketing_calls                                                    # sí usa las tablas


# ---------------------------------------------------------------- staff y socio: siempre fuera
@pytest.mark.parametrize("modules", [{"marketing": True}, {"marketing": False}])
@pytest.mark.parametrize("user", [STAFF, MEMBER])
def test_staff_and_member_rejected_without_touching_marketing_tables(monkeypatch, modules, user):
    db = ProdLikeDB(modules=modules, tables_exist=False)
    client = client_for(monkeypatch, db)
    for method, path, body in ENDPOINTS:
        r = call(client, method, path, body, user)
        assert r.status_code == 403 and r.json() == {"error": "forbidden"}, path
    assert db.marketing_calls == []


def test_every_public_service_method_goes_through_the_gate():
    """Todos los endpoints pasan por ctx(), donde está la puerta."""
    import inspect
    from services import marketing as mk
    public = [n for n, f in inspect.getmembers(mk.MarketingService, inspect.isfunction)
              if not n.startswith("_") and n != "ctx"]
    assert len(public) == 11
    for name in public:
        src = inspect.getsource(inspect.unwrap(getattr(mk.MarketingService, name)))
        assert "self.ctx(jwt, tenant_id)" in src, name
