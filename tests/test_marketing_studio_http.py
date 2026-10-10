"""
AITA Marketing (Fase 2) por HTTP: subida con límite ANTES de leer el cuerpo, vista previa entregada por
el backend (mismo origen) y CSP del Manager sin abrir media-src / img-src a dominios externos.
"""
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import main
from services.marketing_library import LibraryService
from services.marketing_privacy import MockFaceDetector
from test_marketing import NOW, T1, T2, OWNER, OTHER
from test_marketing_studio import StudioDB, PNG

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def db():
    return StudioDB()


@pytest.fixture
def client(monkeypatch, db):
    monkeypatch.setattr(main, "member_portal", SimpleNamespace(db=db))
    monkeypatch.setattr("services.marketing_studio_routes.LibraryService",
                        lambda d: LibraryService(d, now=NOW, detector=MockFaceDetector()))
    return TestClient(main.app)


def H(u, **extra):
    return {"Authorization": f"Bearer jwt-{u}", **extra}


def upload(client, data, user=OWNER, tenant=T1, name="foto.png", mime="image/png", headers=None):
    return client.post(f"/api/manager/marketing/library?tenant_id={tenant}", content=data,
                       headers={**H(user, **{"Content-Type": mime, "X-File-Name": name}), **(headers or {})})


def test_upload_and_preview_same_origin(client, db):
    r = upload(client, PNG, name="mi%20foto.png")
    assert r.status_code == 200, r.text
    m = r.json()
    assert m["original_filename"] == "mi foto.png" and m["malware_scan_status"] == "unavailable"
    p = client.get(f"/api/manager/marketing/library/{m['id']}/content?tenant_id={T1}", headers=H(OWNER))
    assert p.status_code == 200 and p.content == PNG
    assert p.headers["content-type"] == "image/png" and p.headers["x-content-type-options"] == "nosniff"
    assert p.headers["cache-control"] == "no-store"
    assert "supabase" not in p.text.lower() and "token" not in str(p.headers).lower()
    assert client.get(f"/api/manager/marketing/library/{m['id']}/content?tenant_id={T2}", headers=H(OTHER)).status_code == 404


def test_declared_size_over_limit_rejected_before_reading(client, db):
    db.tables["marketing_settings"][0]["max_upload_bytes"] = 100
    r = upload(client, PNG + b"\x00" * 200)
    assert r.status_code == 413 and r.json() == {"error": "file_too_large"}
    assert db.storage == {} and db.tables["marketing_media"] == []


def test_streamed_body_without_length_is_cut_at_limit(client, db):
    """A nivel ASGI: el servidor deja de pedir trozos en cuanto supera el límite (nunca lee el resto)."""
    import asyncio
    db.tables["marketing_settings"][0]["max_upload_bytes"] = 100
    pulled, sent = [], []

    async def receive():
        pulled.append(1)
        return {"type": "http.request", "body": b"\x00" * 64, "more_body": len(pulled) < 500}   # 32 000 bytes

    async def send(msg):
        sent.append(msg)
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST", "scheme": "http",
             "path": "/api/manager/marketing/library", "raw_path": b"/api/manager/marketing/library",
             "query_string": f"tenant_id={T1}".encode(), "root_path": "", "server": ("test", 80), "client": ("c", 1),
             "headers": [(b"authorization", f"Bearer jwt-{OWNER}".encode()), (b"content-type", b"image/png"),
                         (b"x-file-name", b"a.png")]}
    asyncio.run(main.app(scope, receive, send))
    assert sent[0]["status"] == 413 and len(pulled) < 10 and db.storage == {}


def test_storage_limit_blocks_before_reading(client, db):
    db.tables["marketing_settings"][0]["library_storage_limit_bytes"] = 0
    r = upload(client, PNG)
    assert r.status_code == 409 and r.json() == {"error": "limit_library_storage"}


def test_upload_errors_are_codes(client):
    r = upload(client, b"<svg><script>x</script></svg>", name="a.svg", mime="image/svg+xml")
    assert r.status_code == 415 and r.json() == {"error": "forbidden_file_type"}


def _directive(csp, name):
    m = re.search(rf"(?:^|;)\s*{name}\s+([^;]*)", csp)
    return m.group(1).split() if m else None


def test_manager_csp_not_weakened(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://abc.supabase.co")
    csp = main._manager_csp()
    assert _directive(csp, "media-src") == ["'self'", "blob:"]
    assert _directive(csp, "img-src") == ["'self'", "data:", "blob:"]
    assert _directive(csp, "script-src") == ["'self'"]
    assert "*" not in csp and "https:" not in _directive(csp, "media-src")
    assert not any("supabase" in x for x in _directive(csp, "media-src") + _directive(csp, "img-src"))


def test_preview_served_with_manager_csp(client, db):
    m = upload(client, PNG).json()
    p = client.get(f"/api/manager/marketing/library/{m['id']}/content?tenant_id={T1}", headers=H(OWNER))
    assert _directive(p.headers["content-security-policy"], "media-src") == ["'self'", "blob:"]


def test_frontend_preview_uses_same_origin_blob_only():
    api = (ROOT / "manager/assets/js/api.js").read_text()
    block = api[api.index("async previewBlob"):api.index("classify(tenantId")]
    assert "/api/manager/marketing/library/" in block and "/content?" in block
    assert "signed" not in block.lower() and "supabase" not in block.lower()
    lib = (ROOT / "manager/assets/js/modules/marketing-library.js").read_text()
    assert "URL.createObjectURL" in lib and "URL.revokeObjectURL" in lib


def test_phase2_stylesheet_is_linked_and_served(client):
    html = (ROOT / "manager/index.html").read_text()
    assert html.index("css/manager.css") < html.index("css/marketing-studio.css")
    r = client.get("/manager/assets/css/marketing-studio.css")
    assert r.status_code == 200 and ".mk-der-mock" in r.text
