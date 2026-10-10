"""
AITA Marketing (Fase 2): reproducción con HTTP Range real mediante autorización temporal same-origin.
El <video> pide HEAD y rangos con la URL del token (sin cabecera Authorization). Cada petición revalida
token, usuario, rol, tenant, módulo, estado del archivo y ruta. Caducidad, revocación y aislamiento.
"""
import logging
from datetime import datetime, timedelta, timezone
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


@pytest.fixture
def db():
    return StudioDB()


@pytest.fixture
def client(monkeypatch, db):
    monkeypatch.setattr(main, "member_portal", SimpleNamespace(db=db))
    return TestClient(main.app)


def H(u):
    return {"Authorization": f"Bearer jwt-{u}"}


@pytest.fixture
def video(client):
    r = client.post(f"/api/manager/marketing/library?tenant_id={T1}", content=VIDEO,
                    headers={**H(OWNER), "Content-Type": "video/mp4", "X-File-Name": "clase.mp4"})
    assert r.status_code == 200, r.text
    return r.json()


def token_url(client, media, user=OWNER, tenant=T1):
    r = client.post(f"/api/manager/marketing/library/{media['id']}/stream-token", json={"tenant_id": tenant}, headers=H(user))
    return r


def url_of(client, media, user=OWNER):
    r = token_url(client, media, user)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["url"].startswith(mstream.STREAM_PATH) and 60 <= body["expires_in"] <= 900
    return body["url"]


# ------------------------------------------------------------------ lo que hace el reproductor
def test_player_head_and_ranges_without_authorization_header(client, video):
    url = url_of(client, video)
    size = len(VIDEO)
    h = client.head(url)                                                   # sin Authorization: solo el token
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
    for r in (h, first, full):
        assert r.headers["cache-control"] == "no-store" and r.headers["referrer-policy"] == "no-referrer"
        assert r.headers["x-content-type-options"] == "nosniff"


def test_ranges_are_requested_from_storage_not_whole_file(client, db, video):
    url = url_of(client, video)
    db.streamed.clear()
    client.get(url, headers={"Range": "bytes=150000-150099"})
    client.head(url)
    assert db.streamed == [(video["storage_path"], (150000, 150099))]      # HEAD no toca Storage


def test_token_is_only_issued_to_owner_manager_of_tenant(client, video):
    assert token_url(client, video, MANAGER).status_code == 200
    assert token_url(client, video, STAFF).status_code == 403
    assert token_url(client, video, OTHER, T2).status_code == 404
    assert client.post(f"/api/manager/marketing/library/{video['id']}/stream-token", json={"tenant_id": T1}).status_code == 401


def test_only_hash_is_stored(client, db, video):
    url = url_of(client, video)
    token = url.rsplit("/", 1)[1]
    row = db.tables["marketing_stream_tokens"][-1]
    assert token not in str(row) and row["token_hash"] == mstream.token_hash(token)
    assert row["user_id"] == OWNER and row["tenant_id"] == T1 and row["media_id"] == video["id"]


# ------------------------------------------------------------------ caducidad y revocación
def test_expired_token_stops_working(client, db, video):
    url = url_of(client, video)
    assert client.head(url).status_code == 200
    db.tables["marketing_stream_tokens"][-1]["expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    assert client.head(url).status_code == 404 and client.get(url, headers={"Range": "bytes=0-1"}).status_code == 404


def test_expiry_with_controlled_clock(db):
    lib = LibraryService(db, now=NOW)
    m = lib.upload(f"jwt-{OWNER}", T1, "c.mp4", "video/mp4", VIDEO)
    tok = MediaStreamService(db, now=NOW).issue_token(f"jwt-{OWNER}", T1, m["id"])
    t = tok["url"].rsplit("/", 1)[1]
    assert MediaStreamService(db, now=NOW + timedelta(seconds=tok["expires_in"] - 1)).stream(t, "bytes=0-1", False)["status"] == 206
    with pytest.raises(Exception) as e:
        MediaStreamService(db, now=NOW + timedelta(seconds=tok["expires_in"])).stream(t, "bytes=0-1", False)
    assert getattr(e.value, "code", "") == "not_found"


def test_ttl_is_short(monkeypatch):
    monkeypatch.setenv("MARKETING_STREAM_TTL", "99999")
    assert mstream.stream_ttl() == 900
    monkeypatch.setenv("MARKETING_STREAM_TTL", "1")
    assert mstream.stream_ttl() == 60


def test_explicit_revocation(client, video):
    url = url_of(client, video)
    r = client.post(f"/api/manager/marketing/library/{video['id']}/stream-revoke", json={"tenant_id": T1}, headers=H(OWNER))
    assert r.status_code == 200 and r.json()["revoked"] == 1
    assert client.head(url).status_code == 404


@pytest.mark.parametrize("how", ["role_removed", "module_off", "deleted", "consent_revoked", "purged", "path_tampered"])
def test_access_changes_invalidate_existing_tokens(client, db, video, how):
    url = url_of(client, video)
    assert client.get(url, headers={"Range": "bytes=0-1"}).status_code == 206
    if how == "role_removed":
        db.tables["tenant_users"][0]["active"] = False
    elif how == "module_off":
        db.tables["tenants"][0]["modules"] = {"marketing": False}
    elif how == "deleted":
        assert client.post(f"/api/manager/marketing/library/{video['id']}/delete", json={"tenant_id": T1, "confirm": True},
                           headers=H(OWNER)).status_code == 200
    elif how == "consent_revoked":
        lib = LibraryService(db)
        consented(lib, video)
        url = url_of(client, video)
        lib.revoke_consent(f"jwt-{OWNER}", T1, video["id"])
    elif how == "purged":
        m = next(x for x in db.tables["marketing_media"] if x["id"] == video["id"])
        RetentionRunner(db, now=datetime.now(timezone.utc) + timedelta(days=60)).purge(dict(m), "expired")
    elif how == "path_tampered":
        next(x for x in db.tables["marketing_media"] if x["id"] == video["id"])["storage_path"] = \
            f"{T2}/originals/{video['id']}/clase.mp4"
    assert client.head(url).status_code == 404
    assert client.get(url, headers={"Range": "bytes=0-1"}).status_code == 404


# ------------------------------------------------------------------ aislamiento y formato
def test_token_never_serves_another_tenants_file(client, db, video):
    url = url_of(client, video)
    row = db.tables["marketing_stream_tokens"][-1]
    row["tenant_id"] = T2                                                  # alguien manipula la fila
    assert client.head(url).status_code == 404                            # OWNER es manager de T2, pero el archivo es de T1


@pytest.mark.parametrize("bad", ["", "x", "../../etc/passwd", "A" * 200, "abc$%", "0" * 31])
def test_malformed_tokens(client, bad):
    assert client.get(mstream.STREAM_PATH + bad).status_code in (404, 405)


def test_tokens_redacted_from_access_logs():
    f = mstream.RedactStreamTokens()
    rec = logging.LogRecord("uvicorn.access", logging.INFO, "x", 1, '%s - "%s %s HTTP/%s" %d',
                            ("1.2.3.4", "GET", "/api/manager/marketing/library/stream/SECRETtoken_1234567890abcdef1234567890", "1.1", 206), None)
    f.filter(rec)
    assert "SECRET" not in rec.getMessage() and "/library/stream/***" in rec.getMessage()
    assert any(isinstance(x, mstream.RedactStreamTokens) for x in logging.getLogger("uvicorn.access").filters)


def test_frontend_player_uses_range_url_not_blob():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    lib = (root / "manager/assets/js/modules/marketing-library.js").read_text()
    api = (root / "manager/assets/js/api.js").read_text()
    assert "streamToken" in lib and "src: tok.url" in lib and "streamRevoke" in lib
    assert "createObjectURL" not in lib and "previewBlob" not in api
    assert "/stream-token" in api and "supabase" not in api[api.index("streamToken"):api.index("streamRevoke")].lower()


def test_pending_purge_blocks_even_unrevoked_tokens(client, db, video):
    url = url_of(client, video)
    next(x for x in db.tables["marketing_media"] if x["id"] == video["id"])["retention_status"] = "purge_pending"
    assert client.head(url).status_code == 404                             # aunque el token no se haya revocado


def test_consent_revocation_and_purge_revoke_token_rows(client, db, video):
    lib = LibraryService(db)
    consented(lib, video)
    url_of(client, video)
    lib.revoke_consent(f"jwt-{OWNER}", T1, video["id"])
    assert all(t["revoked_at"] for t in db.tables["marketing_stream_tokens"] if t["media_id"] == video["id"])
    other = client.post(f"/api/manager/marketing/library?tenant_id={T1}", content=VIDEO[:-1] + b"\x01",
                        headers={**H(OWNER), "Content-Type": "video/mp4", "X-File-Name": "b.mp4"}).json()
    url_of(client, other)
    m = next(x for x in db.tables["marketing_media"] if x["id"] == other["id"])
    RetentionRunner(db, now=datetime.now(timezone.utc) + timedelta(days=60)).purge(dict(m), "expired")
    assert all(t["revoked_at"] for t in db.tables["marketing_stream_tokens"] if t["media_id"] == other["id"])


def test_token_never_serves_another_file_of_same_tenant(client, db, video):
    other = client.post(f"/api/manager/marketing/library?tenant_id={T1}", content=VIDEO[:-1] + b"\x02",
                        headers={**H(OWNER), "Content-Type": "video/mp4", "X-File-Name": "c.mp4"}).json()
    url = url_of(client, video)
    next(x for x in db.tables["marketing_media"] if x["id"] == video["id"])["storage_path"] = other["storage_path"]
    assert client.head(url).status_code == 404
