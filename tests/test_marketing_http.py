"""
AITA Marketing — endpoints HTTP (/api/manager/marketing/*), pruebas de JS (Node)
y pruebas SQL de RLS (PGlite, si hay una instalación de desarrollo disponible).
"""
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import main
from services.marketing import MarketingService
from test_marketing import FakeDB, NOW, T1, OWNER, MANAGER, STAFF, MEMBER

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def db():
    return FakeDB()


@pytest.fixture
def client(monkeypatch, db):
    monkeypatch.setattr(main, "member_portal", SimpleNamespace(db=db))
    monkeypatch.setattr("services.marketing_routes.MarketingService",
                        lambda database: MarketingService(database, now=NOW))
    return TestClient(main.app)


def H(u):
    return {"Authorization": f"Bearer jwt-{u}"}


def test_endpoints_flow(client):
    r = client.post("/api/manager/marketing/content", headers=H(OWNER),
                    json={"tenant_id": T1, "title": "Fuerza", "format": "image", "channels": ["instagram"]})
    assert r.status_code == 200, r.text
    cid = r.json()["id"]
    for to in ("review", "approved"):
        assert client.post(f"/api/manager/marketing/content/{cid}/transition", headers=H(MANAGER),
                           json={"tenant_id": T1, "to": to}).status_code == 200
    r = client.post(f"/api/manager/marketing/content/{cid}/transition", headers=H(OWNER),
                    json={"tenant_id": T1, "to": "scheduled", "scheduled_at": "2026-10-25T10:00"})
    assert r.status_code == 200 and r.json()["status"] == "scheduled"
    assert client.get(f"/api/manager/marketing/dashboard?tenant_id={T1}", headers=H(OWNER)).json()["counts"]["scheduled"] == 1
    assert client.get(f"/api/manager/marketing/brand?tenant_id={T1}", headers=H(OWNER)).status_code == 200
    assert client.put("/api/manager/marketing/brand", headers=H(OWNER),
                      json={"tenant_id": T1, "tone": "Claro"}).json()["profile"]["tone"] == "Claro"
    assert client.get(f"/api/manager/marketing/calendar?tenant_id={T1}&start=2026-10-01&end=2026-10-31",
                      headers=H(OWNER)).json()["items"][0]["id"] == cid


def test_endpoints_deny_member_and_staff(client):
    for u in (MEMBER, STAFF):
        r = client.get(f"/api/manager/marketing/dashboard?tenant_id={T1}", headers=H(u))
        assert r.status_code == 403 and r.json() == {"error": "forbidden"}
    assert client.get(f"/api/manager/marketing/dashboard?tenant_id={T1}").status_code == 401


def test_endpoint_errors_hide_details(client, db, monkeypatch):
    def broken(*a, **kw):
        raise RuntimeError("Supabase GET -> 500: secret sb_secret_abc")
    monkeypatch.setattr(db, "select", broken)
    r = client.get(f"/api/manager/marketing/dashboard?tenant_id={T1}", headers=H(OWNER))
    assert r.status_code == 500 and r.json() == {"error": "server_error"}


def test_not_configured(monkeypatch):
    monkeypatch.setattr(main, "member_portal", None)
    r = TestClient(main.app).get(f"/api/manager/marketing/dashboard?tenant_id={T1}")
    assert r.status_code == 503


def test_marketing_assets_served():
    c = TestClient(main.app)
    for name in ("marketing.js", "marketing-forms.js", "marketing-i18n.js", "marketing-state.js", "marketing-library.js",
                 "marketing-studio.js", "marketing-studio-i18n.js", "marketing-studio-state.js"):
        assert c.get(f"/manager/assets/js/modules/{name}").status_code == 200, name


def test_no_provider_secrets_or_brand_hardcoded_in_frontend():
    js = "\n".join(p.read_text() for p in (ROOT / "manager/assets/js").rglob("marketing*.js"))
    for word in ("Golden Age", "postiz", "comfy", "api_key", "apiKey", "SERVICE_ROLE", "#0B1F3A", "#C9A227"):
        assert word.lower() not in js.lower(), word


@pytest.mark.skipif(not shutil.which("node"), reason="Node.js no disponible")
def test_marketing_js():
    r = subprocess.run(["node", "--test", str(ROOT / "tests/js/marketing.test.mjs"),
                        str(ROOT / "tests/js/marketing-studio.test.mjs")], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]


@pytest.mark.skipif(not (shutil.which("node") and os.getenv("PGLITE_NODE_PATH")),
                    reason="PGlite no disponible (PGLITE_NODE_PATH=/ruta/node_modules)")
def test_marketing_sql_rls():
    r = subprocess.run(["node", str(ROOT / "tests/sql/marketing.mjs")], capture_output=True, text=True, timeout=120,
                       env={**os.environ, "NODE_PATH": os.environ["PGLITE_NODE_PATH"]})
    assert r.returncode == 0 and "ALL MARKETING SQL TESTS PASSED" in r.stdout, r.stdout[-2000:] + r.stderr[-2000:]


@pytest.mark.skipif(not (shutil.which("node") and os.getenv("PGLITE_NODE_PATH")),
                    reason="PGlite no disponible (PGLITE_NODE_PATH=/ruta/node_modules)")
def test_marketing_sql_least_privilege():
    r = subprocess.run(["node", str(ROOT / "tests/sql/marketing_privileges.mjs")], capture_output=True, text=True,
                       timeout=180, env={**os.environ, "NODE_PATH": os.environ["PGLITE_NODE_PATH"]})
    assert r.returncode == 0 and "ALL MARKETING PRIVILEGE TESTS PASSED" in r.stdout, r.stdout[-2000:] + r.stderr[-2000:]


@pytest.mark.skipif(not (shutil.which("node") and os.getenv("PGLITE_NODE_PATH")),
                    reason="PGlite no disponible (PGLITE_NODE_PATH=/ruta/node_modules)")
def test_marketing_studio_sql():
    r = subprocess.run(["node", str(ROOT / "tests/sql/marketing_studio.mjs")], capture_output=True, text=True,
                       timeout=180, env={**os.environ, "NODE_PATH": os.environ["PGLITE_NODE_PATH"]})
    assert r.returncode == 0 and "ALL MARKETING STUDIO SQL TESTS PASSED" in r.stdout, r.stdout[-2000:] + r.stderr[-2000:]


@pytest.mark.skipif(not (shutil.which("node") and os.getenv("PGLITE_NODE_PATH")),
                    reason="PGlite no disponible (PGLITE_NODE_PATH=/ruta/node_modules)")
def test_marketing_retention_sql():
    r = subprocess.run(["node", str(ROOT / "tests/sql/marketing_retention.mjs")], capture_output=True, text=True,
                       timeout=180, env={**os.environ, "NODE_PATH": os.environ["PGLITE_NODE_PATH"]})
    assert r.returncode == 0 and "ALL MARKETING RETENTION SQL TESTS PASSED" in r.stdout, r.stdout[-2000:] + r.stderr[-2000:]



@pytest.mark.skipif(not (shutil.which("node") and os.getenv("PGLITE_NODE_PATH")),
                    reason="PGlite no disponible (PGLITE_NODE_PATH=/ruta/node_modules)")
def test_marketing_generation_budget_sql():
    r = subprocess.run(["node", str(ROOT / "tests/sql/marketing_generation_budget.mjs")], capture_output=True, text=True,
                       timeout=180, env={**os.environ, "NODE_PATH": os.environ["PGLITE_NODE_PATH"]})
    assert r.returncode == 0 and "ALL MARKETING GENERATION BUDGET SQL TESTS PASSED" in r.stdout, r.stdout[-2000:] + r.stderr[-2000:]
