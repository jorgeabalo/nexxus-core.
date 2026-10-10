"""
AITA Marketing (Fase 2): reproducción con HTTP Range real mediante una sesión opaca en cookie.
La petición autenticada (JWT) crea la sesión y la coloca en una cookie HttpOnly, SameSite=Strict,
Secure en producción, con Path = ruta EXACTA del archivo y Max-Age ≤ 600. El <video> usa una URL limpia
(sin token) y el navegador envía la cookie en GET, HEAD y cada Range. Cada petición revalida sesión,
usuario, rol, tenant, módulo, estado del archivo y ruta. Caducidad, revocación y aislamiento.
"""
from datetime import datetime, timedelta, timezone
from http.cookies import SimpleCookie
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import main
from services import marketing_media_stream as mstream
from services.marketing_library import LibraryService
from services.marketing_media_stream import MediaStreamService
from services.marketing_retention import RetentionRunner
from test_marketing import NOW, T1, T2, OWNER, MANAGER, STAFF, OTHER
from test_marketing_studio import StudioDB, mp4_with_duration, consented

VIDEO = mp4_with_duration(2, pad=200_000)
LIB = "/api/manager/marketing/library"


@pytest.fixture
def db():
    return StudioDB()


@pytest.fixture
def client(monkeypatch, db):
    # Configuración EXPLÍCITA de test: el cliente de pruebas habla HTTP, así que la cookie va sin Secure.
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv(mstream.INSECURE_FLAG, "1")
    monkeypatch.setattr(main, "member_portal", SimpleNamespace(db=db))
    return TestClient(main.app)


def H(u):
    return {"Authorization": f"Bearer jwt-{u}"}


def upload(client, data=VIDEO, name="clase.mp4"):
    r = client.post(f"{LIB}?tenant_id={T1}", content=data,
                    headers={**H(OWNER), "Content-Type": "video/mp4", "X-File-Name": name})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture
def video(client):
    return upload(client)


def session(client, media, user=OWNER, tenant=T1):
    return client.post(f"{LIB}/{media['id']}/stream-session", json={"tenant_id": tenant}, headers=H(user))


def cookie_of(r):
    """(valor, atributos en minúsculas) de la cookie de sesión de una respuesta."""
    raw = next(h for h in r.headers.get_list("set-cookie") if h.startswith(f"{mstream.COOKIE}="))
    c = SimpleCookie()
    c.load(raw)
    return c[mstream.COOKIE].value, raw.lower()


def open_session(client, media, user=OWNER):
    """Como el navegador: POST autenticado → la cookie queda en el tarro del cliente. Devuelve (url, token)."""
    r = session(client, media, user)
    assert r.status_code == 200, r.text
    body = r.json()
    token, _ = cookie_of(r)
    assert body["url"] == mstream.stream_path(media["id"]) and token not in r.text
    return body["url"], token


def bare(token=None):
    """Cliente sin tarro de cookies: solo envía lo que se le pasa."""
    return lambda method, url, **kw: TestClient(main.app).request(
        method, url, headers={**kw.pop("headers", {}), **({"Cookie": f"{mstream.COOKIE}={token}"} if token else {})}, **kw)


# ------------------------------------------------------------------ lo que hace el reproductor
def test_player_head_and_ranges_with_cookie_on_clean_url(client, video):
    url, token = open_session(client, video)
    assert "?" not in url and token not in url and url.endswith(f"/{video['id']}/stream")
    size = len(VIDEO)
    h = client.head(url)                                                   # sin Authorization: la cookie
    assert h.status_code == 200 and h.headers["content-length"] == str(size) and h.headers["accept-ranges"] == "bytes"
    assert h.content == b""
    first = client.get(url, headers={"Range": "bytes=0-1"})               # sondeo inicial (Safari)
    assert first.status_code == 206 and first.content == VIDEO[:2]
    assert first.headers["content-range"] == f"bytes 0-1/{size}"
    whole = client.get(url, headers={"Range": "bytes=0-"})                 # Chrome: rango abierto
    assert whole.status_code == 206 and whole.content == VIDEO
    fwd = client.get(url, headers={"Range": "bytes=150000-"})              # avanzar
    assert fwd.status_code == 206 and fwd.content == VIDEO[150000:]
    back = client.get(url, headers={"Range": "bytes=1000-50999"})          # retroceder
    assert back.status_code == 206 and back.content == VIDEO[1000:51000] and back.headers["content-length"] == "50000"
    tail = client.get(url, headers={"Range": "bytes=-500"})
    assert tail.status_code == 206 and tail.content == VIDEO[-500:]
    bad = client.get(url, headers={"Range": f"bytes={size}-"})
    assert bad.status_code == 416 and bad.headers["content-range"] == f"bytes */{size}"
    full = client.get(url)
    assert full.status_code == 200 and full.content == VIDEO
    for r in (h, first, full, bad):
        assert r.headers["cache-control"] == "private, no-store" and r.headers["referrer-policy"] == "no-referrer"
        assert r.headers["x-content-type-options"] == "nosniff"


def test_without_cookie_nothing_is_served(client, video):
    url, _ = open_session(client, video)
    call = bare()
    assert call("HEAD", url).status_code == 404 and call("GET", url, headers={"Range": "bytes=0-1"}).status_code == 404
    assert call("GET", url, headers=H(OWNER)).status_code == 404           # ni siquiera con el JWT: solo la sesión


def test_ranges_are_requested_from_storage_not_whole_file(client, db, video):
    url, _ = open_session(client, video)
    db.streamed.clear()
    client.get(url, headers={"Range": "bytes=150000-150099"})
    client.head(url)
    assert db.streamed == [(video["storage_path"], (150000, 150099))]      # HEAD no toca Storage


# ------------------------------------------------------------------ la cookie
def test_cookie_attributes_with_explicit_test_config(client, video):
    r = session(client, video)
    _, attrs = cookie_of(r)
    assert "httponly" in attrs and "samesite=strict" in attrs
    assert f"path={mstream.stream_path(video['id'])}".lower() in attrs     # ruta EXACTA del archivo
    assert "max-age=600" in attrs and "; secure" not in attrs             # solo porque APP_ENV=test lo pide
    assert r.headers["cache-control"] == "private, no-store" and r.headers["referrer-policy"] == "no-referrer"
    assert set(r.json()) == {"url", "max_age", "mime"} and r.json()["max_age"] <= 600


@pytest.mark.parametrize("headers", [{}, {"Host": "localhost"}, {"Host": "127.0.0.1:8000"},
                                     {"X-Forwarded-Host": "localhost"}, {"X-Forwarded-Proto": "http"}])
def test_cookie_is_always_secure_without_explicit_test_config(monkeypatch, client, video, headers):
    """Sin configuración explícita = producción: Secure siempre, diga lo que diga Host o X-Forwarded-Host."""
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv(mstream.INSECURE_FLAG, raising=False)
    r = client.post(f"{LIB}/{video['id']}/stream-session", json={"tenant_id": T1}, headers={**H(OWNER), **headers})
    _, attrs = cookie_of(r)
    assert "; secure" in attrs and "httponly" in attrs and "samesite=strict" in attrs and "max-age=600" in attrs
    rv = client.post(f"{LIB}/{video['id']}/stream-revoke", json={"tenant_id": T1}, headers={**H(OWNER), **headers})
    assert "; secure" in cookie_of(rv)[1] and "max-age=0" in cookie_of(rv)[1]


@pytest.mark.parametrize("app_env,flag,secure", [(None, None, True), ("production", None, True), ("test", None, True),
                                                 ("local", "0", True), ("test", "1", False), ("development", "true", False)])
def test_cookie_secure_only_disabled_by_explicit_local_config(monkeypatch, app_env, flag, secure):
    for k, v in (("APP_ENV", app_env), (mstream.INSECURE_FLAG, flag)):
        monkeypatch.delenv(k, raising=False) if v is None else monkeypatch.setenv(k, v)
    assert mstream.cookie_secure() is secure


@pytest.mark.parametrize("app_env", [None, "production", "staging", "prod", ""])
def test_production_refuses_to_start_if_secure_is_disabled(monkeypatch, app_env):
    monkeypatch.setenv(mstream.INSECURE_FLAG, "1")
    monkeypatch.delenv("APP_ENV", raising=False) if app_env is None else monkeypatch.setenv("APP_ENV", app_env)
    from services.marketing_studio_routes import build_router
    with pytest.raises(RuntimeError, match="Secure"):
        build_router(lambda: None)
    with pytest.raises(RuntimeError):
        mstream.cookie_secure()                                         # ni siquiera por petición


def test_app_process_does_not_start_in_production_with_insecure_cookie():
    import os
    import subprocess
    import sys
    from pathlib import Path
    env = {k: v for k, v in os.environ.items() if k != "APP_ENV"}
    env.update({mstream.INSECURE_FLAG: "1", "PYTHONDONTWRITEBYTECODE": "1"})
    p = subprocess.run([sys.executable, "-c", "import main"], cwd=Path(__file__).resolve().parents[1], env=env,
                       capture_output=True, text=True, timeout=120)
    assert p.returncode != 0 and "MARKETING_STREAM_COOKIE_INSECURE" in p.stderr
    env["APP_ENV"] = "test"
    assert subprocess.run([sys.executable, "-c", "import main"], cwd=Path(__file__).resolve().parents[1], env=env,
                          capture_output=True, text=True, timeout=120).returncode == 0


def test_session_is_only_issued_to_owner_manager_of_tenant(client, video):
    assert session(client, video, MANAGER).status_code == 200
    r = session(client, video, STAFF)
    assert r.status_code == 403 and not r.headers.get_list("set-cookie")
    assert session(client, video, OTHER, T2).status_code == 404
    assert client.post(f"{LIB}/{video['id']}/stream-session", json={"tenant_id": T1}).status_code == 401


def test_only_hash_is_stored(client, db, video):
    _, token = open_session(client, video)
    row = db.tables["marketing_stream_tokens"][-1]
    assert token not in str(row) and row["token_hash"] == mstream.token_hash(token)
    assert row["user_id"] == OWNER and row["tenant_id"] == T1 and row["media_id"] == video["id"]


def test_session_only_serves_its_own_file(client, db, video):
    other = upload(client, VIDEO[:-1] + b"\x02", "c.mp4")
    _, token = open_session(client, video)
    call = bare(token)
    assert call("HEAD", mstream.stream_path(video["id"])).status_code == 200
    assert call("HEAD", mstream.stream_path(other["id"])).status_code == 404   # aunque se envíe a mano


# ------------------------------------------------------------------ caducidad y revocación
def test_expired_session_stops_working(client, db, video):
    url, _ = open_session(client, video)
    assert client.head(url).status_code == 200
    db.tables["marketing_stream_tokens"][-1]["expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    assert client.head(url).status_code == 404 and client.get(url, headers={"Range": "bytes=0-1"}).status_code == 404


def test_expiry_with_controlled_clock(db):
    lib = LibraryService(db, now=NOW)
    m = lib.upload(f"jwt-{OWNER}", T1, "c.mp4", "video/mp4", VIDEO)
    s = MediaStreamService(db, now=NOW).issue_session(f"jwt-{OWNER}", T1, m["id"])
    assert s["max_age"] == 600 and s["path"] == s["url"] == mstream.stream_path(m["id"])
    assert MediaStreamService(db, now=NOW + timedelta(seconds=599)).stream(m["id"], s["token"], "bytes=0-1", False)["status"] == 206
    with pytest.raises(Exception) as e:
        MediaStreamService(db, now=NOW + timedelta(seconds=600)).stream(m["id"], s["token"], "bytes=0-1", False)
    assert getattr(e.value, "code", "") == "not_found"


def test_ttl_is_at_most_600(monkeypatch):
    monkeypatch.setenv("MARKETING_STREAM_TTL", "99999")
    assert mstream.stream_ttl() == 600
    monkeypatch.setenv("MARKETING_STREAM_TTL", "1")
    assert mstream.stream_ttl() == 60
    monkeypatch.setenv("MARKETING_STREAM_TTL", "abc")
    assert mstream.stream_ttl() == 600


def test_close_revokes_session_and_expires_cookie(client, video):
    url, token = open_session(client, video)
    r = client.post(f"{LIB}/{video['id']}/stream-revoke", json={"tenant_id": T1}, headers=H(OWNER))
    assert r.status_code == 200 and r.json()["revoked"] == 1
    _, attrs = cookie_of(r)
    assert "max-age=0" in attrs and f"path={mstream.stream_path(video['id'])}".lower() in attrs and "httponly" in attrs
    assert client.head(url).status_code == 404
    assert bare(token)("HEAD", url).status_code == 404                      # el valor antiguo ya no sirve


@pytest.mark.parametrize("how", ["deleted", "consent_revoked"])
def test_delete_and_consent_responses_expire_cookie(client, db, video, how):
    if how == "consent_revoked":
        consented(LibraryService(db), video)
    _, token = open_session(client, video)
    if how == "deleted":
        r = client.post(f"{LIB}/{video['id']}/delete", json={"tenant_id": T1, "confirm": True}, headers=H(OWNER))
    else:
        r = client.post(f"{LIB}/{video['id']}/revoke-consent", json={"tenant_id": T1}, headers=H(OWNER))
    assert r.status_code == 200, r.text
    assert "max-age=0" in cookie_of(r)[1]
    assert bare(token)("HEAD", mstream.stream_path(video["id"])).status_code == 404


@pytest.mark.parametrize("how", ["role_removed", "module_off", "deleted", "consent_revoked", "purged", "path_tampered"])
def test_access_changes_invalidate_existing_sessions(client, db, video, how):
    if how == "consent_revoked":
        consented(LibraryService(db), video)
    url, token = open_session(client, video)
    assert client.get(url, headers={"Range": "bytes=0-1"}).status_code == 206
    if how == "role_removed":
        db.tables["tenant_users"][0]["active"] = False
    elif how == "module_off":
        db.tables["tenants"][0]["modules"] = {"marketing": False}
    elif how == "deleted":
        LibraryService(db).delete(f"jwt-{OWNER}", T1, video["id"], "", True)
    elif how == "consent_revoked":
        LibraryService(db).revoke_consent(f"jwt-{OWNER}", T1, video["id"])
    elif how == "purged":
        m = next(x for x in db.tables["marketing_media"] if x["id"] == video["id"])
        RetentionRunner(db, now=datetime.now(timezone.utc) + timedelta(days=60)).purge(dict(m), "expired")
    elif how == "path_tampered":
        next(x for x in db.tables["marketing_media"] if x["id"] == video["id"])["storage_path"] = \
            f"{T2}/originals/{video['id']}/clase.mp4"
    call = bare(token)                                                      # aunque el navegador aún tenga la cookie
    assert call("HEAD", url).status_code == 404
    assert call("GET", url, headers={"Range": "bytes=0-1"}).status_code == 404


def test_pending_purge_blocks_even_unrevoked_sessions(client, db, video):
    url, _ = open_session(client, video)
    next(x for x in db.tables["marketing_media"] if x["id"] == video["id"])["retention_status"] = "purge_pending"
    assert client.head(url).status_code == 404


def test_consent_revocation_and_purge_revoke_session_rows(client, db, video):
    lib = LibraryService(db)
    consented(lib, video)
    open_session(client, video)
    lib.revoke_consent(f"jwt-{OWNER}", T1, video["id"])
    assert all(t["revoked_at"] for t in db.tables["marketing_stream_tokens"] if t["media_id"] == video["id"])
    other = upload(client, VIDEO[:-1] + b"\x01", "b.mp4")
    open_session(client, other)
    m = next(x for x in db.tables["marketing_media"] if x["id"] == other["id"])
    RetentionRunner(db, now=datetime.now(timezone.utc) + timedelta(days=60)).purge(dict(m), "expired")
    assert all(t["revoked_at"] for t in db.tables["marketing_stream_tokens"] if t["media_id"] == other["id"])


# ------------------------------------------------------------------ aislamiento y formato
def test_session_never_serves_another_tenants_file(client, db, video):
    url, _ = open_session(client, video)
    db.tables["marketing_stream_tokens"][-1]["tenant_id"] = T2             # alguien manipula la fila
    assert client.head(url).status_code == 404                            # OWNER es manager de T2, pero el archivo es de T1


def test_session_never_serves_another_file_of_same_tenant(client, db, video):
    other = upload(client, VIDEO[:-1] + b"\x02", "c.mp4")
    url, _ = open_session(client, video)
    next(x for x in db.tables["marketing_media"] if x["id"] == video["id"])["storage_path"] = other["storage_path"]
    assert client.head(url).status_code == 404


@pytest.mark.parametrize("bad", ["", "x", "../../etc/passwd", "A" * 200, "abc$%", "0" * 31])
def test_malformed_session_values(client, video, bad):
    assert bare(bad)("GET", mstream.stream_path(video["id"])).status_code == 404


def test_old_token_url_routes_are_gone(client, video):
    open_session(client, video)
    assert client.post(f"{LIB}/{video['id']}/stream-token", json={"tenant_id": T1}, headers=H(OWNER)).status_code in (404, 405)
    assert client.get(f"{LIB}/stream/{'a' * 43}").status_code in (404, 405)


def test_frontend_player_uses_clean_url_and_cookie_session():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    lib = (root / "manager/assets/js/modules/marketing-library.js").read_text()
    api = (root / "manager/assets/js/api.js").read_text()
    assert "streamSession" in lib and "src: session.url" in lib and "streamRevoke" in lib
    assert "media.src = fresh.url;" in lib and "?r=" not in lib and "token" not in lib.lower()
    assert "createObjectURL" not in lib and "previewBlob" not in api and "streamToken" not in api
    assert "/stream-session" in api and "supabase" not in api[api.index("streamSession"):api.index("streamRevoke")].lower()
