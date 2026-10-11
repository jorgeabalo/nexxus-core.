"""
AITA Marketing (Fase 2): reservas de almacenamiento abandonadas y resultados que no caben.

* Una reserva se cierra UNA sola vez (consumed / released / expired): subida interrumpida, trabajo
  cancelado, fallido, timeout, consentimiento retirado o purga. Una reserva cerrada no libera bytes otra vez.
* Antes de registrar un resultado se compara su tamaño real con lo reservado y la cuota disponible.
  Si no cabe: no se registra, se borra el temporal, se libera la reserva y el trabajo falla con
  'storage_quota_exceeded'.
* La validación de rutas se mantiene como defensa en profundidad y se prueba directamente.
"""
from datetime import timedelta
from types import SimpleNamespace

import pytest

from services import marketing_media_files as mf
from services import marketing_retention as rt
from services import marketing_storage as ms
from services import marketing_worker as mw
from services.marketing_ai_router import (MarketingAIRouter, MockProvider, OmniRouteAdapter, DisabledLocalTools,
                                         ProviderJob)
from services.marketing_library import LibraryService
from services.marketing_media_stream import MediaStreamService, resolve
from services.marketing_studio import StudioService
from services.member_portal import PortalError
from test_marketing import NOW, T1, T2, OWNER
from test_marketing_studio import StudioDB, err, jwt, png, PNG, approved_job, consented, MockFaceDetector

NEED = ms.estimated_output_bytes(30)


@pytest.fixture
def db():
    return StudioDB()


@pytest.fixture
def lib(db):
    return LibraryService(db, now=NOW, detector=MockFaceDetector(0.95))


@pytest.fixture
def studio(db):
    return StudioService(db, now=NOW, router=MarketingAIRouter(env={}),
                         adapters={"mock": MockProvider(), "omniroute": OmniRouteAdapter(env={}),
                                   "local_ffmpeg": DisabledLocalTools()})


def res(db, key):
    return next(r for r in db.tables["marketing_storage_reservations"] if r["reservation_key"] == key)


def job(db, jid):
    return next(r for r in db.tables["marketing_generation_jobs"] if r["id"] == jid)


def setlimit(db, v):
    db.tables["marketing_settings"][0]["library_storage_limit_bytes"] = v


def assert_closed_once(db, key, status):
    """Cerrada con `status`; ni liberar ni consumir otra vez cambia nada ni devuelve bytes."""
    before = ms.used_bytes(db, T1)
    assert res(db, key)["status"] == status
    ms.release(db, T1, key, consumed=False)
    ms.release(db, T1, key, consumed=True)
    assert res(db, key)["status"] == status and ms.used_bytes(db, T1) == before
    with pytest.raises(RuntimeError):                                      # el guardián de la base lo impide
        db.update("marketing_storage_reservations", {"reservation_key": f"eq.{key}"}, {"status": "reserved"})


# ================================================================== reservas abandonadas
def test_interrupted_upload_reservation_expires_once(db):
    key = ms.reserve(db, T1, "upload", 5000, "upload:interrupted-1")       # el proceso muere aquí
    assert ms.used_bytes(db, T1) == 5000
    db.rpc_now = (NOW + timedelta(minutes=16)).isoformat()                # pasan los 15 minutos
    assert ms.used_bytes(db, T1) == 0                                      # caducada: ya no cuenta
    assert ms.release(db, T1, key, consumed=False) is None and res(db, key)["status"] == "reserved"
    out = rt.RetentionRunner(db, now=NOW + timedelta(minutes=16)).run()
    assert out["reservations_expired"] == 1
    assert rt.RetentionRunner(db, now=NOW + timedelta(minutes=17)).run()["reservations_expired"] == 0   # idempotente
    assert_closed_once(db, key, "expired")


def test_upload_failure_releases_and_success_consumes(lib, db, monkeypatch):
    lib.upload(jwt(OWNER), T1, "a.png", "image/png", PNG)
    assert_closed_once(db, db.tables["marketing_storage_reservations"][-1]["reservation_key"], "consumed")
    monkeypatch.setattr(db, "storage_upload", lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    assert err(lib.upload, jwt(OWNER), T1, "b.png", "image/png", png(w=9)) == "storage_unavailable"
    assert_closed_once(db, db.tables["marketing_storage_reservations"][-1]["reservation_key"], "released")


def test_cancelled_job_releases_its_reservation(studio, db):
    j = approved_job(studio)
    assert res(db, f"job:{j['id']}")["bytes"] == NEED and ms.used_bytes(db, T1) == NEED
    studio.cancel_job(jwt(OWNER), T1, j["id"])
    assert ms.used_bytes(db, T1) == 0
    assert_closed_once(db, f"job:{j['id']}", "released")


def test_failed_job_releases_its_reservation(studio, db):
    j = approved_job(studio)

    class Failing(MockProvider):
        def submit(self, req, model):
            return ProviderJob(status="failed", provider_job_id=None, error_code="render_failed")
    studio.adapters["mock"] = Failing()
    assert studio.process_job(jwt(OWNER), T1, j["id"])["status"] == "failed"
    assert ms.used_bytes(db, T1) == 0
    assert_closed_once(db, f"job:{j['id']}", "released")


def test_timed_out_job_releases_its_reservation(studio, db):
    j = approved_job(studio)
    db.rpc_now = (NOW + timedelta(hours=23)).isoformat()                  # el timeout lo decide la base
    assert rt.RetentionRunner(db, now=NOW + timedelta(hours=23)).run()["timed_out"] == 0
    db.rpc_now = (NOW + timedelta(hours=25)).isoformat()
    out = rt.RetentionRunner(db, now=NOW + timedelta(hours=25)).run()
    assert out["timed_out"] == 1 and job(db, j["id"])["status"] == "failed" and job(db, j["id"])["error_code"] == "timeout"
    assert ms.used_bytes(db, T1) == 0
    assert_closed_once(db, f"job:{j['id']}", "released")


def test_consent_revocation_releases_reservation_of_jobs_using_media(lib, studio, db):
    m = lib.upload(jwt(OWNER), T1, "a.png", "image/png", PNG)
    consented(lib, m)
    j = approved_job(studio, [m["id"]], real=50, ai=50)
    assert res(db, f"job:{j['id']}")["status"] == "reserved"
    lib.revoke_consent(jwt(OWNER), T1, m["id"])
    assert job(db, j["id"])["status"] == "cancelled"
    assert_closed_once(db, f"job:{j['id']}", "released")


def test_purge_releases_reservation_of_jobs_and_pending_upload(lib, studio, db):
    m = lib.upload(jwt(OWNER), T1, "a.png", "image/png", PNG)
    lib.classify(jwt(OWNER), T1, m["id"], {"contains_people": False, "contains_minors": False, "people_policy": "no_people"})
    j = approved_job(studio, [m["id"]], real=50, ai=50)
    row = next(x for x in db.tables["marketing_media"] if x["id"] == m["id"])
    assert rt.RetentionRunner(db, now=NOW + timedelta(days=40)).purge(dict(row), "expired") == "purged"
    assert job(db, j["id"])["status"] == "cancelled" and job(db, j["id"])["error_code"] == "media_pending_deletion"
    assert_closed_once(db, f"job:{j['id']}", "released")
    assert ms.used_bytes(db, T1) == 0


def test_expired_job_reservation_cannot_be_released_twice(studio, db):
    j = approved_job(studio)
    db.rpc_now = (NOW + timedelta(hours=24, seconds=1)).isoformat()
    assert ms.expire_abandoned(db) == 1 and ms.used_bytes(db, T1) == 0
    studio.cancel_job(jwt(OWNER), T1, j["id"])                              # el trabajo termina después
    assert_closed_once(db, f"job:{j['id']}", "expired")                    # sigue 'expired': no se libera dos veces


# ================================================================== tamaño real del resultado
class WithFiles(MockProvider):
    """Simula un proveedor que deja temporales en {tenant}/derivatives/{job}/…"""
    def __init__(self, db, job_id, sizes, path=None):
        super().__init__()
        self.db, self.job_id, self.sizes, self.path = db, job_id, list(sizes), path

    def submit(self, req, model):
        r = super().submit(req, model)
        if not self.sizes:
            return r
        size = self.sizes.pop(0)
        prefix = (req.output_requirements or {}).get("temp_prefix", "")          # temporales de ESTE intento
        path = self.path or f"{req.tenant_id}/derivatives/{self.job_id}/{prefix}render-{len(self.sizes)}.mp4"
        self.db.storage[("marketing-assets", path)] = (b"x", "video/mp4")
        return r.__class__(**{**r.__dict__, "output": {**r.output, "files": [
            {"kind": "render", "temp_path": path, "byte_size": size, "mime_type": "video/mp4"}]}})


def run_with(studio, db, sizes, path=None):
    jid = approved_job(studio)["id"]
    studio.adapters["mock"] = WithFiles(db, jid, sizes, path)
    return jid, studio.process_job(jwt(OWNER), T1, jid)


def files_of(db, jid):
    return [o for o in db.tables["marketing_generation_outputs"] if o["job_id"] == jid and o.get("storage_path")]


def test_result_that_fits_is_recorded_with_real_size(studio, db):
    jid, out = run_with(studio, db, [3_000_000, 1_000_000])
    assert out["status"] == "succeeded"
    assert sorted(o["byte_size"] for o in files_of(db, jid)) == [1_000_000, 3_000_000]
    assert ms.used_bytes(db, T1) == 4_000_000                              # cuenta el tamaño real, no lo estimado
    assert_closed_once(db, f"job:{jid}", "consumed")


def test_result_bigger_than_reserved_but_within_quota_is_accepted(studio, db):
    jid, out = run_with(studio, db, [NEED + 1_000_000])
    assert out["status"] == "succeeded" and ms.used_bytes(db, T1) == NEED + 1_000_000


def test_result_that_does_not_fit_is_rejected_cleanly(studio, db):
    setlimit(db, NEED + 100)                                               # la aprobación cabe…
    jid, out = run_with(studio, db, [NEED + 50, 60])                       # …pero el resultado real no
    assert out["status"] == "failed" and out["error_code"] == "storage_quota_exceeded"
    assert job(db, jid)["status"] == "failed" and job(db, jid)["error_code"] == "storage_quota_exceeded"
    assert [o for o in db.tables["marketing_generation_outputs"] if o["job_id"] == jid] == []      # nada registrado
    assert not [k for k in db.storage if k[1].startswith(f"{T1}/derivatives/{jid}/")]             # temporales borrados
    assert ms.used_bytes(db, T1) == 0
    assert_closed_once(db, f"job:{jid}", "released")


def test_result_after_reservation_expired_fails_as_timeout(studio, db):
    j = approved_job(studio)
    res(db, f"job:{j['id']}")["expires_at"] = (NOW - timedelta(seconds=1)).isoformat()   # reserva abandonada
    assert ms.expire_abandoned(db) == 1 and job(db, j["id"])["status"] == "queued"      # el trabajo sigue en cola
    studio.adapters["mock"] = WithFiles(db, j["id"], [10])
    out = studio.process_job(jwt(OWNER), T1, j["id"])
    assert out["status"] == "failed" and out["error_code"] == "timeout" and files_of(db, j["id"]) == []


@pytest.mark.parametrize("bad", [f"{T2}/derivatives/JOB/x.mp4", "{T1}/originals/JOB/x.mp4", "{T1}/derivatives/OTHER/x.mp4",
                                 "{T1}/derivatives/JOB/../x.mp4", "../../etc/passwd"])
def test_result_with_foreign_temp_path_is_never_recorded_nor_deleted(studio, db, bad):
    j = approved_job(studio)
    path = bad.replace("{T1}", T1).replace("JOB", j["id"])
    db.storage[("marketing-assets", path)] = (b"ajeno", "video/mp4")
    studio.adapters["mock"] = WithFiles(db, j["id"], [10], path)
    out = studio.process_job(jwt(OWNER), T1, j["id"])
    assert out["status"] == "failed" and out["error_code"] == "internal_error" and files_of(db, j["id"]) == []
    assert ("marketing-assets", path) in db.storage                       # no se borra nada que no sea suyo


# ================================================================== validación de rutas (defensa en profundidad)
OWN = f"{T1}/originals/11111111-1111-1111-1111-111111111111/a.png"


@pytest.mark.parametrize("path,ok", [
    (OWN, True),
    (f"{T1}/derivatives/11111111-1111-1111-1111-111111111111/a.png", True),
    (f"{T2}/originals/11111111-1111-1111-1111-111111111111/a.png", False),       # otro tenant
    (f"{T1}/originals/../a.png", False), (f"{T1}/originals/x/../../a.png", False),
    (f"{T1}/tmp/x/a.png", False), (f"{T1}/originals/x/y/a.png", False), (f"/{OWN}", False),
    (f"{T1}/originals/x/a b.png", False), ("", False), (None, False)])
def test_library_path_validation_directly(db, path, ok):
    c = SimpleNamespace(tenant_id=T1)
    lib = LibraryService(db, now=NOW)
    if ok:
        assert lib._path(c, path) == path
    else:
        with pytest.raises(PortalError) as e:
            lib._path(c, path)
        assert e.value.code == "not_found"


def test_upload_rejects_bad_path_even_if_builder_is_wrong(lib, db, monkeypatch):
    """Si otra capa fallara y construyera una ruta de otro tenant, _path la detiene antes de escribir."""
    monkeypatch.setattr(mf, "original_path", lambda t, m, n: f"{T2}/originals/{m}/{n}")
    assert err(lib.upload, jwt(OWNER), T1, "a.png", "image/png", PNG) == "not_found"
    assert db.storage == {} and db.tables["marketing_media"] == []
    assert not [r for r in db.tables["marketing_storage_reservations"] if r["status"] == "reserved"]


def test_stream_resolve_path_validation_directly(lib, db):
    m = lib.upload(jwt(OWNER), T1, "a.png", "image/png", PNG)
    svc = MediaStreamService(db, now=NOW)
    c = svc.ctx(jwt(OWNER), T1)
    assert resolve(svc, c, m["id"])[1] == m["storage_path"]
    row = next(x for x in db.tables["marketing_media"] if x["id"] == m["id"])
    for bad in (f"{T2}/originals/{m['id']}/a.png", f"{T1}/originals/{'2' * 8}-2222-2222-2222-{'2' * 12}/a.png",
                f"{T1}/originals/{m['id']}/../a.png", f"{T1}/tmp/{m['id']}/a.png"):
        row["storage_path"] = bad
        with pytest.raises(PortalError):
            resolve(svc, c, m["id"])


def test_purge_path_validation_directly(db):
    r = rt.RetentionRunner(db, now=NOW)
    mid = "11111111-1111-1111-1111-111111111111"
    for bad in (f"{T2}/originals/{mid}/a.png", f"{T1}/originals/{'3' * 8}-3333-3333-3333-{'3' * 12}/a.png",
                f"{T1}/originals/{mid}/../../x"):
        with pytest.raises(PermissionError):
            r._remove(T1, mid, bad)
    assert db.removed == []


@pytest.mark.parametrize("path,ok", [("{T1}/derivatives/J/a.mp4", True), ("{T2}/derivatives/J/a.mp4", False),
                                     ("{T1}/derivatives/K/a.mp4", False), ("{T1}/originals/J/a.mp4", False),
                                     ("{T1}/derivatives/J/..", False), ("{T1}/derivatives/J/a/b.mp4", False)])
def test_worker_temp_path_validation_directly(path, ok):
    p = path.replace("{T1}", T1).replace("{T2}", T2)
    assert mw.temp_file_ok(p, T1, "J") is ok
