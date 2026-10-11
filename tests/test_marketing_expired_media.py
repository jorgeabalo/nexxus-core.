"""
AITA Marketing (Fase 2): un archivo vencido (expires_at <= ahora) es inaccesible aunque el purgador
no haya corrido. Desaparece del listado y no puede abrirse, descargarse, previsualizarse, usarse en un
trabajo ni abrir una sesión de reproducción: 410 media_expired. Otro tenant sigue viendo 404 (sin oráculo).
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import main
from services import marketing_media_stream as mstream
from services.marketing_library import LibraryService
from services.marketing_media_stream import MediaStreamService
from services.marketing_worker import recheck_inputs
from test_marketing import NOW, T1, T2, OWNER, MANAGER, STAFF, OTHER
from test_marketing_studio import StudioDB, err, jwt, up, consented, new_job, mp4_with_duration, png

LIB = "/api/manager/marketing/library"
VIDEO = mp4_with_duration(2, pad=50_000)
PAST = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()


@pytest.fixture
def db():
    return StudioDB()


@pytest.fixture
def lib(db):
    return LibraryService(db, now=NOW)


@pytest.fixture
def studio(db):
    from services.marketing_ai_router import MarketingAIRouter, MockProvider, OmniRouteAdapter, DisabledLocalTools
    from services.marketing_studio import StudioService
    return StudioService(db, now=NOW, router=MarketingAIRouter(env={}),
                         adapters={"mock": MockProvider(), "omniroute": OmniRouteAdapter(env={}),
                                   "local_ffmpeg": DisabledLocalTools()})


@pytest.fixture
def client(monkeypatch, db):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv(mstream.INSECURE_FLAG, "1")
    monkeypatch.setattr(main, "member_portal", SimpleNamespace(db=db))
    return TestClient(main.app)


def H(u):
    return {"Authorization": f"Bearer jwt-{u}"}


def row(db, mid):
    return next(x for x in db.tables["marketing_media"] if x["id"] == mid)


def expire(db, mid, when=None):
    row(db, mid)["expires_at"] = when or (NOW - timedelta(seconds=1)).isoformat()


def upload_http(client, data=VIDEO, name="clase.mp4"):
    r = client.post(f"{LIB}?tenant_id={T1}", content=data, headers={**H(OWNER), "Content-Type": "video/mp4",
                                                                      "X-File-Name": name})
    assert r.status_code == 200, r.text
    return r.json()


# ------------------------------------------------------------------ servicio
def test_expired_disappears_from_listing(lib, db):
    a = up(lib)
    expire(db, a["id"])
    assert lib.library(jwt(OWNER), T1)["items"] == []
    exactly_now = up(lib, name="b.png", data=png(w=11))
    expire(db, exactly_now["id"], NOW.isoformat())                      # expires_at == ahora: ya vencido
    assert lib.library(jwt(OWNER), T1)["items"] == []


def test_listing_query_filters_in_database(lib, db, monkeypatch):
    up(lib)
    seen = []
    orig = db.select
    monkeypatch.setattr(db, "select", lambda t, p: (seen.append((t, dict(p))), orig(t, p))[1])
    lib.library(jwt(OWNER), T1)
    q = next(p for t, p in seen if t == "marketing_media")
    assert q["expires_at"] == f"gt.{NOW.isoformat()}"                  # el filtro va a la base, no solo a la vista


@pytest.mark.parametrize("op", ["content", "classify", "archive", "retention", "anonymize", "stream_session"])
def test_expired_blocks_every_access(lib, db, op):
    m = up(lib)
    expire(db, m["id"])
    calls = {
        "content": lambda: lib.content(jwt(OWNER), T1, m["id"]),
        "classify": lambda: lib.classify(jwt(OWNER), T1, m["id"], {"contains_people": False, "contains_minors": False,
                                                                   "people_policy": "no_people"}),
        "archive": lambda: lib.set_archived(jwt(OWNER), T1, m["id"], True),
        "retention": lambda: lib.set_retention(jwt(OWNER), T1, m["id"], 30),
        "anonymize": lambda: lib.anonymize(jwt(OWNER), T1, m["id"], "blur_faces"),
        "stream_session": lambda: MediaStreamService(db, now=NOW).issue_session(jwt(OWNER), T1, m["id"]),
    }
    with pytest.raises(Exception) as e:
        calls[op]()
    assert getattr(e.value, "code", "") == "media_expired" and getattr(e.value, "status", 0) == 410


def test_expired_can_still_be_deleted_or_consent_revoked(lib, db):
    m = up(lib)
    consented(lib, m)
    expire(db, m["id"])
    assert lib.revoke_consent(jwt(OWNER), T1, m["id"])
    m2 = up(lib, name="z.png", data=png(w=12))
    expire(db, m2["id"])
    assert lib.delete(jwt(OWNER), T1, m2["id"], "", True)["retention_status"] == "purged"


def test_expired_cannot_be_used_for_generation(lib, studio, db):
    m = up(lib)
    lib.classify(jwt(OWNER), T1, m["id"], {"contains_people": False, "contains_minors": False, "people_policy": "no_people"})
    expire(db, m["id"])
    assert err(new_job, studio, [m["id"]]) == "media_expired"


def test_expiry_after_creation_blocks_approval_worker_and_reuse(lib, studio, db):
    m = up(lib)
    lib.classify(jwt(OWNER), T1, m["id"], {"contains_people": False, "contains_minors": False, "people_policy": "no_people"})
    j = new_job(studio, [m["id"]], 50, 50)
    studio.estimate_job(jwt(OWNER), T1, j["id"])
    expire(db, m["id"])
    assert err(studio.approve_job, jwt(OWNER), T1, j["id"], True) == "media_expired"
    assert recheck_inputs(db, T1, j["id"], NOW) == "media_expired"
    # aprobado antes de vencer: el worker vuelve a comprobar y falla sin enviar nada
    row(db, m["id"])["expires_at"] = (NOW + timedelta(days=1)).isoformat()
    approved = studio.approve_job(jwt(OWNER), T1, j["id"], True)
    expire(db, m["id"])
    out = studio.process_job(jwt(OWNER), T1, approved["id"])
    assert out["status"] == "failed" and out["error_code"] == "media_expired"
    assert db.tables["marketing_model_usage"] == [] and studio.adapters["mock"].submissions == 0


# ------------------------------------------------------------------ HTTP
def test_http_expired_is_410_for_owner_and_404_for_other_tenant(client, db):
    m = upload_http(client)
    expire(db, m["id"], PAST)
    for path, method in ((f"{LIB}/{m['id']}/content?tenant_id={T1}", "GET"),
                         (f"{LIB}/{m['id']}/content?tenant_id={T1}", "HEAD")):
        r = client.request(method, path, headers=H(OWNER))
        assert r.status_code == 410, (method, r.status_code)
    r = client.post(f"{LIB}/{m['id']}/stream-session", json={"tenant_id": T1}, headers=H(OWNER))
    assert r.status_code == 410 and r.json() == {"error": "media_expired"} and not r.headers.get_list("set-cookie")
    assert client.get(f"{LIB}?tenant_id={T1}", headers=H(OWNER)).json()["items"] == []
    assert client.get(f"{LIB}/{m['id']}/content?tenant_id={T2}", headers=H(OTHER)).status_code == 404   # sin oráculo
    assert client.get(f"{LIB}/{m['id']}/content?tenant_id={T1}", headers=H(STAFF)).status_code == 403


def test_expiry_between_session_and_range_request(client, db):
    m = upload_http(client)
    r = client.post(f"{LIB}/{m['id']}/stream-session", json={"tenant_id": T1}, headers=H(OWNER))
    assert r.status_code == 200
    url = r.json()["url"]
    assert client.get(url, headers={"Range": "bytes=0-1"}).status_code == 206
    expire(db, m["id"], PAST)                                           # vence con la sesión ya abierta
    assert client.get(url, headers={"Range": "bytes=2-3"}).status_code == 410
    assert client.head(url).status_code == 410


def test_session_never_outlives_the_file(db):
    lib = LibraryService(db, now=NOW)
    m = lib.upload(f"jwt-{OWNER}", T1, "c.mp4", "video/mp4", VIDEO)
    row(db, m["id"])["expires_at"] = (NOW + timedelta(seconds=90)).isoformat()
    s = MediaStreamService(db, now=NOW).issue_session(f"jwt-{OWNER}", T1, m["id"])
    assert s["max_age"] == 90                                           # no 600: el archivo vence antes
    tok = db.tables["marketing_stream_tokens"][-1]
    assert tok["expires_at"] == (NOW + timedelta(seconds=90)).isoformat()


@pytest.mark.parametrize("who,code", [(MANAGER, 200), (STAFF, 403)])
def test_roles_unchanged(client, who, code):
    assert client.get(f"{LIB}?tenant_id={T1}", headers=H(who)).status_code == code


@pytest.mark.parametrize("expires,expired", [(NOW - timedelta(seconds=1), True), (NOW, True), (None, True),
                                             (NOW + timedelta(seconds=1), False)])
def test_is_expired_boundaries(expires, expired):
    from services import marketing_retention as rt
    assert rt.is_expired({"expires_at": expires.isoformat() if expires else None}, NOW) is expired
