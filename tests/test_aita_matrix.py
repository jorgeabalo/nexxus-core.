"""Real service adapters, tenant isolation and a closed execution surface."""
from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

import main
from test_marketing import FakeDB, T1, T2, OWNER, MANAGER, STAFF, OTHER, MEMBER, jwt


@pytest.fixture
def database():
    db = FakeDB()
    for tenant in db.tables["tenants"]:
        tenant["modules"]["accounting"] = True
    return db


@pytest.fixture
def client(monkeypatch, database):
    monkeypatch.setattr(main, "member_portal", SimpleNamespace(db=database))
    return TestClient(main.app)


def headers(user=OWNER):
    return {"Authorization": f"Bearer {jwt(user)}"}


def command(agent="marketing", capability="brand", tenant=T1):
    return {"tenant_id": tenant, "agent": agent, "capability": capability}


def test_catalog_is_tenant_scoped_and_honest(client, database):
    database.tables["tenants"][0]["settings"]["secret"] = "never expose"
    result = client.get(f"/api/manager/aita/agents?tenant_id={T1}", headers=headers())
    assert result.status_code == 200
    agents = {a["key"]: a for a in result.json()["agents"]}
    assert result.json()["tenant"]["id"] == T1
    assert agents["marketing"]["status"] == "available"
    assert agents["finance"]["capabilities"] == ["summary"]
    for key in ("coordination", "web", "inventory", "onboarding", "integrations", "quality", "cost_control"):
        assert agents[key]["status"] == "planned"
        assert agents[key]["capabilities"] == []
        assert agents[key]["responsibility"]
    assert "never expose" not in result.text


@pytest.mark.parametrize("user,tenant,status", [
    (STAFF, T1, 403), (MEMBER, T1, 403), (OTHER, T1, 403), (OWNER, "bad", 403),
    (OWNER, T1, 200), (MANAGER, T1, 200), (OWNER, T2, 200),
])
def test_permissions_on_catalog_and_execution(client, user, tenant, status):
    assert client.get(f"/api/manager/aita/agents?tenant_id={tenant}", headers=headers(user)).status_code == status
    assert client.post("/api/manager/aita/read", json=command(tenant=tenant), headers=headers(user)).status_code == status


def test_auth_inactive_and_deleted_tenant(client, database):
    assert client.post("/api/manager/aita/read", json=command()).status_code == 401
    database.tables["tenant_users"][0]["active"] = False
    assert client.post("/api/manager/aita/read", json=command(), headers=headers()).status_code == 403
    database.tables["tenant_users"][0]["active"] = True
    database.tables["tenants"].pop(0)
    assert client.post("/api/manager/aita/read", json=command(), headers=headers()).status_code == 403


@pytest.mark.parametrize("modules", [{}, None, [], "marketing", {"marketing": False},
                                     {"marketing": "true"}, {"marketing": 1}])
def test_module_closed_before_target_table_read(client, database, modules):
    database.tables["tenants"][0]["modules"] = modules
    original = database.select

    def select(table, params):
        assert not table.startswith("marketing_")
        return original(table, params)

    database.select = select
    assert client.post("/api/manager/aita/read", json=command(), headers=headers()).status_code == 403


@pytest.mark.parametrize("override", [{"manager": []}, {"manager": ["accounting"]},
                                      {"manager": "marketing"}, []])
def test_manager_restrictions_cannot_be_bypassed(client, database, override):
    database.tables["tenants"][0]["settings"]["role_modules"] = override
    assert client.post("/api/manager/aita/read", json=command(), headers=headers(MANAGER)).status_code == 403
    assert client.post("/api/manager/aita/read", json=command(), headers=headers(OWNER)).status_code == 200


def test_real_services_are_read_only_and_do_not_mix_tenants(client, database):
    database.tables["marketing_brand_profiles"] = [
        {"tenant_id": T1, "business_name": "First"}, {"tenant_id": T2, "business_name": "Second"}]
    before = deepcopy(database.tables)
    for tenant, name in ((T1, "First"), (T2, "Second"), (T1, "First")):
        result = client.post("/api/manager/aita/read", json=command(tenant=tenant), headers=headers())
        assert result.json()["data"]["profile"]["business_name"] == name
        assert result.json()["tenant_id"] == tenant
    for agent, capability in (("marketing", "dashboard"), ("marketing", "campaigns"), ("finance", "summary")):
        result = client.post("/api/manager/aita/read", json=command(agent, capability), headers=headers())
        assert result.status_code == 200, result.text
        assert result.json()["read_only"] is True
    assert database.tables == before


def test_finance_adapter_keeps_the_same_users_companies_separate(client, database):
    today = datetime.now(ZoneInfo("America/Chicago")).date().isoformat()
    database.tables["accounting_transactions"] = [
        {"id": tenant, "tenant_id": tenant, "transaction_date": today,
         "type": "income", "status": "paid", "description": "tenant income",
         "amount": amount, "payment_method": "cash"}
        for tenant, amount in ((T1, 100), (T2, 900))
    ]
    for tenant, expected in ((T1, 100), (T2, 900), (T1, 100)):
        result = client.post("/api/manager/aita/read", json=command("finance", "summary", tenant), headers=headers())
        assert result.status_code == 200, result.text
        assert result.json()["data"]["income"] == expected


def test_dashboard_omits_internal_settings(client):
    result = client.post("/api/manager/aita/read", json=command(capability="dashboard"), headers=headers())
    assert result.status_code == 200
    assert set(result.json()["data"]) == {"counts", "upcoming"}


@pytest.mark.parametrize("agent,capability", [("marketing", "publish_content"), ("finance", "initiate_payment"),
    ("onboarding", "create_tenant"), ("web", "deploy"), ("magic", "summary"), ("marketing", "save_brand"),
    ("marketing", "__dict__"), ("finance", "brand")])
def test_unregistered_or_write_operations_never_execute(client, database, agent, capability):
    before = deepcopy(database.tables)
    result = client.post("/api/manager/aita/read", json=command(agent, capability), headers=headers())
    assert result.status_code == 409
    assert database.tables == before


def test_context_and_approval_cannot_be_supplied_by_caller(client):
    for extra in ({"context": {"tenant_id": T2}}, {"approved": True}, {"role": "owner"}):
        assert client.post("/api/manager/aita/read", json={**command(), **extra}, headers=headers()).status_code == 422


def test_target_service_retains_its_gates_and_safe_errors(client, database, monkeypatch):
    original = database.select

    def missing(table, params):
        if table.startswith("marketing_"):
            raise RuntimeError("Supabase GET -> 404: PGRST205 secret-value")
        return original(table, params)

    monkeypatch.setattr(database, "select", missing)
    result = client.post("/api/manager/aita/read", json=command(), headers=headers())
    assert result.status_code == 503 and result.json() == {"error": "marketing_unavailable"}

    def broken(*args):
        raise RuntimeError("secret-value")

    monkeypatch.setattr(database, "select", broken)
    result = client.post("/api/manager/aita/read", json=command(), headers=headers())
    assert result.status_code == 500 and result.json() == {"error": "server_error"}


def test_not_configured(client, monkeypatch):
    monkeypatch.setattr(main, "member_portal", None)
    assert client.get(f"/api/manager/aita/agents?tenant_id={T1}", headers=headers()).status_code == 503
