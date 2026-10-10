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
    r = subprocess.run(["node", "--test", str(ROOT / "tests" / "js" / "nav.test.mjs"),
                        str(ROOT / "tests" / "js" / "i18n.test.mjs")],
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
    """Ningún cliente concreto (Golden Age) aparece en la infraestructura multitenant del panel."""
    hits = [p.name for p in JS.rglob("*.js") if "Golden Age" in p.read_text() or "golden_age" in p.read_text()]
    assert hits == []
    nav = (JS / "nav.js").read_text()
    assert "PILOT_NAME" not in nav and "FALLBACK_NAME = 'Nexxus'" in nav


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


def test_mobile_tenant_switcher():
    """Con más de una empresa hay selector también en el menú lateral (móvil); al cambiar se cierra el menú."""
    app = (JS / "app.js").read_text()
    assert "state.memberships.length > 1" in app                      # con una sola empresa no hay selector
    assert "tenantPicker('tenant-select')" in app and "tenantPicker('sb-tenant')" in app
    body = app[app.index("function switchTenant(tenantId) {"):app.index("function toggleNav()")]
    assert body.index("closeNav()") < body.index("setTenant(m)") < body.index("renderShell()") < body.index("route()")
    css = (ROOT / "manager" / "assets" / "css" / "manager.css").read_text()
    assert ".sb-tenant { display: none;" in css
    assert css.index(".sb-tenant { display: none;") < css.index("@media (max-width: 720px) { .sb-tenant { display: block; } }")


def test_back_to_website_link_in_sidebar_and_login():
    """'Volver al sitio web' / 'Back to website': enlace a / en la misma pestaña, fuera de los módulos."""
    app = (JS / "app.js").read_text()
    fn = app[app.index("function websiteLink("):app.index("function renderLogin(")]
    assert "href: '/'" in fn and "target" not in fn            # misma pestaña
    assert "tr('websiteTitle')" in fn and "icon('back')" in fn
    shell = app[app.index("function renderShell()"):app.index("// Indicador discreto")]
    # en el menú lateral, justo después de la lista de módulos y antes del pie (escritorio y móvil)
    assert shell.index("    nav,\n") < shell.index("websiteLink('sb-link sb-website', closeNav)") < shell.index("class: 'sb-foot'")
    # pantalla de acceso: traducido, ya no fijo en inglés
    assert "websiteLink('auth-site')" in app and "'← Back to website'" not in app
    # no es un módulo: no está en el catálogo ni depende de permisos
    nav = (JS / "nav.js").read_text()
    assert "website" not in nav


def test_back_to_website_link_visible_on_mobile():
    css = (ROOT / "manager" / "assets" / "css" / "manager.css").read_text()
    assert ".sb-exit {" in css and ".sb-website" in css
    import re
    for block in re.findall(r"@media[^{]*\{(.*?)\n\}", css, re.S):
        assert not re.search(r"\.sb-(exit|website)[^{]*\{[^}]*display:\s*none", block)


def test_back_to_website_target_exists(client):
    assert client.get("/").status_code == 200
