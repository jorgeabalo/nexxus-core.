"""
Nexxus Manager (Fase 1): identidad de plataforma, menú por tenant y rol, y
rutas existentes sin regresiones (web pública, /manager, /m, Contabilidad).
La lógica de permisos del menú se prueba en tests/js/nav.test.mjs (Node).
"""
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import main

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "manager" / "assets" / "js"


@pytest.fixture
def client():
    return TestClient(main.app)


@pytest.mark.skipif(not shutil.which("node"), reason="Node.js no disponible")
def test_navigation_logic_js():
    r = subprocess.run(["node", "--test", str(ROOT / "tests" / "js" / "nav.test.mjs")],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]


def test_manager_page_branding_and_headers(client):
    r = client.get("/manager")
    assert r.status_code == 200 and "<title>Nexxus Manager</title>" in r.text
    assert r.headers["cache-control"] == "no-store" and r.headers["x-frame-options"] == "DENY"
    assert "script-src 'self'" in r.headers["content-security-policy"]


def test_all_manager_assets_served(client):
    for path in ("js/app.js", "js/nav.js", "js/i18n.js", "js/ui.js", "js/api.js", "css/manager.css",
                 "js/modules/accounting.js", "js/modules/coming-soon.js"):
        assert client.get(f"/manager/assets/{path}").status_code == 200, path


def test_routes_unchanged():
    """Las claves de ruta (#/clave) existentes se conservan; Contabilidad sigue en #/accounting."""
    nav = (JS / "nav.js").read_text()
    for key in ("dashboard", "members", "schedule", "claudia", "payments", "team", "accounting",
                "marketing", "inventory", "agents", "settings"):
        assert f"key: '{key}'" in nav, key
    app = (JS / "app.js").read_text()
    assert "const VIEWS = { dashboard, members, schedule, claudia, payments, team, accounting }" in app
    assert "canOpen(tenant, role, mod.key)" in app            # rutas no permitidas no se renderizan


def test_branding_not_hardcoded():
    """Golden Age solo aparece como fallback del piloto, no repartido por el panel."""
    hits = [p.name for p in JS.rglob("*.js") if "Golden Age" in p.read_text() and p.name != "nav.js"]
    assert hits == []
    assert "PILOT_NAME = 'Golden Age Fitness'" in (JS / "nav.js").read_text()


def test_only_safe_tenant_settings_requested():
    api = (JS / "api.js").read_text()
    assert "public_phone:settings->>public_phone" in api and "address:settings->address" in api
    assert "tenants(id, slug, name, vertical, timezone, branding, modules, settings)" not in api


def test_single_language_source():
    acc = (JS / "modules" / "accounting-i18n.js").read_text()
    assert "export { getLang, setLang } from '../i18n.js'" in acc and "localStorage" not in acc


def test_public_site_and_member_portal_unchanged(client):
    home = client.get("/")
    assert home.status_code == 200
    assert 'href="/manager"' in home.text and 'href="/m"' in home.text
    assert client.get("/m").status_code == 200


def test_config_exposes_no_secrets(client, monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service-secret")
    r = client.get("/api/manager/config")
    assert r.status_code == 200 and "service-secret" not in r.text


def test_set_tenant_resets_brand_colors():
    """setTenant usa applyBrandColors (que quita los colores del tenant anterior), no setProperty suelto."""
    app = (JS / "app.js").read_text()
    body = app[app.index("function setTenant(m) {"):app.index("let shellRefs = null;")]
    assert "applyBrandColors(document.documentElement.style, profile)" in body
    assert "setProperty('--brand-" not in body
