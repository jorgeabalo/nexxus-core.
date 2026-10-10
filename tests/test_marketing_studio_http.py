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
    assert r.status_code == 403 and r.json() == {"error": "library_disabled"}
    db.tables["marketing_settings"][0]["library_storage_limit_bytes"] = 10
    r = upload(client, PNG)
    assert r.status_code == 413 and db.storage == {}


def test_upload_errors_are_codes(client):
    r = upload(client, b"<svg><script>x</script></svg>", name="a.svg", mime="image/svg+xml")
    assert r.status_code == 415 and r.json() == {"error": "forbidden_file_type"}


def _directive(csp, name):
    m = re.search(rf"(?:^|;)\s*{name}\s+([^;]*)", csp)
    return m.group(1).split() if m else None


def test_manager_csp_not_weakened(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://abc.supabase.co")
    csp = main._manager_csp()
    assert _directive(csp, "media-src") is None                    # el vídeo usa default-src 'self' (sin blob:)
    assert _directive(csp, "default-src") == ["'self'"]
    assert _directive(csp, "img-src") == ["'self'", "data:", "blob:"]
    assert _directive(csp, "script-src") == ["'self'"]
    assert "*" not in csp and "https:" not in _directive(csp, "default-src")
    assert not any("supabase" in x for x in _directive(csp, "default-src") + _directive(csp, "img-src"))


def test_preview_served_with_manager_csp(client, db):
    m = upload(client, PNG).json()
    p = client.get(f"/api/manager/marketing/library/{m['id']}/content?tenant_id={T1}", headers=H(OWNER))
    assert _directive(p.headers["content-security-policy"], "media-src") is None
    assert _directive(p.headers["content-security-policy"], "default-src") == ["'self'"]


def test_phase2_stylesheet_is_linked_and_served(client):
    html = (ROOT / "manager/index.html").read_text()
    assert html.index("css/manager.css") < html.index("css/marketing-studio.css")
    r = client.get("/manager/assets/css/marketing-studio.css")
    assert r.status_code == 200 and ".mk-der-mock" in r.text


# ---------------------------------------------------------------- vídeo: HEAD y Range (seeking)
from test_marketing import MANAGER, STAFF, MEMBER  # noqa: E402
from test_marketing_studio import mp4_with_duration  # noqa: E402

VIDEO = mp4_with_duration(2, pad=200_000)                     # ~200 KB, 2 s (creíble)


@pytest.fixture
def video(client):
    r = upload(client, VIDEO, name="clase.mp4", mime="video/mp4")
    assert r.status_code == 200, r.text
    return r.json()


def get(client, m, rng=None, user=OWNER, tenant=T1, method="GET"):
    h = H(user, **({"Range": rng} if rng else {}))
    return client.request(method, f"/api/manager/marketing/library/{m['id']}/content?tenant_id={tenant}", headers=h)


def test_full_video_get(client, video):
    r = get(client, video)
    assert r.status_code == 200 and r.content == VIDEO
    assert r.headers["accept-ranges"] == "bytes" and r.headers["content-length"] == str(len(VIDEO))
    assert r.headers["content-type"] == "video/mp4" and "content-range" not in r.headers


def test_head_has_headers_and_no_body(client, db, video):
    db.streamed.clear()
    r = get(client, video, method="HEAD")
    assert r.status_code == 200 and r.content == b"" and r.headers["content-length"] == str(len(VIDEO))
    assert r.headers["accept-ranges"] == "bytes" and db.streamed == []          # HEAD no toca Storage
    r = get(client, video, "bytes=0-99", method="HEAD")
    assert r.status_code == 206 and r.headers["content-range"] == f"bytes 0-99/{len(VIDEO)}" and r.content == b""


@pytest.mark.parametrize("rng,start,end", [
    ("bytes=0-1", 0, 1),                                       # primer fragmento (Safari pide 0-1 al empezar)
    ("bytes=0-65535", 0, 65535),
    ("bytes=100000-150000", 100000, 150000),                   # fragmento intermedio
    ("bytes=150000-", 150000, None),                           # rango abierto
    ("bytes=-1000", None, None),                               # últimos 1000 bytes
    ("bytes=190000-999999999", 190000, None),                  # fin más allá del archivo: se recorta
])
def test_partial_content(client, db, video, rng, start, end):
    size = len(VIDEO)
    if start is None:
        start, end = size - 1000, size - 1
    end = size - 1 if end is None else end
    db.streamed.clear()
    r = get(client, video, rng)
    assert r.status_code == 206 and r.content == VIDEO[start:end + 1]
    assert r.headers["content-range"] == f"bytes {start}-{end}/{size}"
    assert r.headers["content-length"] == str(end - start + 1) and r.headers["accept-ranges"] == "bytes"
    assert db.streamed == [(video["storage_path"], (start, end))]          # solo se pide ese rango a Storage


@pytest.mark.parametrize("rng", ["bytes=999999999-", "bytes=500-100", "bytes=-0", "bytes=abc", "items=0-1", "bytes=-"])
def test_invalid_range_416(client, db, video, rng):
    db.streamed.clear()
    r = get(client, video, rng)
    assert r.status_code == 416 and r.headers["content-range"] == f"bytes */{len(VIDEO)}" and db.streamed == []


def test_safari_seeking_sequence(client, video):
    size = len(VIDEO)
    assert get(client, video, "bytes=0-1").content == VIDEO[:2]                # sondeo inicial
    assert get(client, video, f"bytes=0-{size - 1}").status_code == 206
    mid = get(client, video, "bytes=120000-")                                  # avanzar
    assert mid.status_code == 206 and mid.content == VIDEO[120000:]
    back = get(client, video, "bytes=4000-")                                   # retroceder
    assert back.status_code == 206 and back.content == VIDEO[4000:]


@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_every_range_request_revalidates_access(client, db, video, method):
    assert get(client, video, "bytes=0-1", user=OTHER, tenant=T2, method=method).status_code == 404
    assert get(client, video, "bytes=0-1", user=STAFF, method=method).status_code == 403
    assert get(client, video, "bytes=0-1", user=MEMBER, method=method).status_code == 403
    assert get(client, video, "bytes=0-1", user=MANAGER, method=method).status_code == 206
    assert client.request(method, f"/api/manager/marketing/library/{video['id']}/content?tenant_id={T1}",
                          headers={"Range": "bytes=0-1"}).status_code == 401
    db.tables["tenants"][0]["modules"] = {"marketing": False}
    assert get(client, video, "bytes=0-1", method=method).status_code == 403
    db.tables["tenants"][0]["modules"] = {"marketing": True}
    row = db.tables["marketing_media"][0]
    row["storage_path"] = f"{T2}/originals/{video['id']}/clase.mp4"            # ruta manipulada
    assert get(client, video, "bytes=0-1", method=method).status_code == 404
    row["storage_path"] = video["storage_path"]
    row["processing_status"] = "deleted"
    assert get(client, video, "bytes=0-1", method=method).status_code == 404


def test_deleted_video_cannot_be_previewed(client, video):
    assert client.post(f"/api/manager/marketing/library/{video['id']}/delete",
                       json={"tenant_id": T1, "confirm": True}, headers=H(OWNER)).status_code == 200
    assert get(client, video).status_code == 404 and get(client, video, "bytes=0-1", method="HEAD").status_code == 404


def test_supabase_range_is_forwarded_and_trimmed_if_ignored(monkeypatch):
    import httpx
    from services.supabase_admin import SupabaseAdmin
    seen, body = [], bytes(range(256)) * 4

    class Resp:
        def __init__(self, code):
            self.status_code = code

        def iter_bytes(self, n):
            for i in range(0, len(body), 100):
                yield body[i:i + 100]

        def close(self):
            pass

    class Client:
        def __init__(self, *a, **k):
            pass

        def build_request(self, method, url, headers=None):
            seen.append(headers.get("Range"))
            return url

        def send(self, req, stream=False):
            return Resp(200)                                     # Storage ignora el Range

        def close(self):
            pass
    monkeypatch.setattr(httpx, "Client", Client)
    sa = SupabaseAdmin(url="https://proj.supabase.co", service_key="k" * 20)
    assert b"".join(sa.storage_stream("marketing-assets", "a/b.mp4", byte_range=(150, 449))) == body[150:450]
    assert seen == ["bytes=150-449"]


def test_supabase_storage_stream_never_leaks(monkeypatch):
    import httpx
    from services.supabase_admin import SupabaseAdmin
    sent = []

    class Resp:
        def __init__(self, code):
            self.status_code = code

        def iter_bytes(self, n):
            yield b"abc"

        def close(self):
            sent.append("closed")

    class Client:
        def __init__(self, *a, **k):
            pass

        def build_request(self, method, url, headers=None):
            sent.append((method, url))
            return url

        def send(self, req, stream=False):
            return Resp(200 if "/ok/" in req else 404)

        def close(self):
            pass
    monkeypatch.setattr(httpx, "Client", Client)
    sa = SupabaseAdmin(url="https://proj.supabase.co", service_key="service-key-123")
    assert b"".join(sa.storage_stream("marketing-assets", "ok/b.png")) == b"abc"
    assert sent[0] == ("GET", "https://proj.supabase.co/storage/v1/object/authenticated/marketing-assets/ok/b.png")
    with pytest.raises(RuntimeError) as e:
        sa.storage_stream("marketing-assets", "a/b.png")
    assert "service-key-123" not in str(e.value) and "a/b.png" not in str(e.value)
