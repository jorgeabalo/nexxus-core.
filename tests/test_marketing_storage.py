"""
AITA Marketing (Fase 2): cuota de almacenamiento de la Biblioteca. Cuenta originales, derivados
(anonimizaciones, vistas previas), resultados/temporales, subidas en curso y bytes estimados de trabajos
pendientes; la reserva es atómica para que operaciones simultáneas no superen el límite.
"""
import threading
from datetime import timedelta

import pytest

from services import marketing_storage as ms
from services.marketing_ai_router import MarketingAIRouter, MockProvider, OmniRouteAdapter, DisabledLocalTools
from services.marketing_library import LibraryService
from services.marketing_studio import StudioService
from test_marketing import NOW, T1, T2, OWNER, OTHER
from test_marketing_studio import StudioDB, err, jwt, png, PNG, new_job

MB = 1024 * 1024
LATER, EARLIER = (NOW + timedelta(hours=1)).isoformat(), (NOW - timedelta(seconds=1)).isoformat()


@pytest.fixture
def db():
    return StudioDB()                       # tenant de prueba con 1 GiB explícito


@pytest.fixture
def lib(db):
    return LibraryService(db, now=NOW)


@pytest.fixture
def studio(db):
    return StudioService(db, now=NOW, router=MarketingAIRouter(env={}),
                         adapters={"mock": MockProvider(), "omniroute": OmniRouteAdapter(env={}),
                                   "local_ffmpeg": DisabledLocalTools()})


def setlimit(db, v):
    db.tables["marketing_settings"][0]["library_storage_limit_bytes"] = v


def test_test_tenant_has_explicit_1gib_and_default_is_closed(db):
    from services import marketing_jobs_domain as jd
    assert db.tables["marketing_settings"][0]["library_storage_limit_bytes"] == 1073741824
    assert jd.GEN_DEFAULTS["library_storage_limit_bytes"] == 0


def test_quota_counts_originals_derivatives_outputs_and_pending(lib, db):
    m = lib.upload(jwt(OWNER), T1, "a.png", "image/png", PNG)
    base = len(PNG)
    db.tables["marketing_media_derivatives"].append({"id": "d1", "tenant_id": T1, "media_id": m["id"], "status": "ready",
                                                     "byte_size": 1000, "kind": "anonymized"})            # anonimización
    db.tables["marketing_media_derivatives"].append({"id": "d2", "tenant_id": T1, "media_id": m["id"], "status": "ready",
                                                     "byte_size": 300, "kind": "thumbnail"})              # vista previa
    db.tables["marketing_generation_outputs"].append({"id": "o1", "tenant_id": T1, "job_id": "j", "byte_size": 5000})
    db.tables["marketing_storage_reservations"].append({"tenant_id": T1, "reservation_key": "upload:x", "kind": "upload",
                                                        "bytes": 700, "status": "reserved", "expires_at": LATER})  # subida
    db.tables["marketing_storage_reservations"].append({"tenant_id": T1, "reservation_key": "job:jq", "kind": "generation",
                                                        "bytes": 20000, "status": "reserved", "expires_at": LATER})  # trabajo
    # lo que NO cuenta: eliminados, purgados, reservas liberadas, trabajos terminados, otros tenants
    db.tables["marketing_media_derivatives"].append({"id": "d3", "tenant_id": T1, "media_id": m["id"], "status": "deleted", "byte_size": 9})
    db.tables["marketing_generation_outputs"].append({"id": "o2", "tenant_id": T1, "job_id": "j", "byte_size": 9, "purged_at": "x"})
    db.tables["marketing_storage_reservations"].append({"tenant_id": T1, "reservation_key": "upload:y", "kind": "upload",
                                                        "bytes": 9, "status": "released", "expires_at": LATER})
    for st, exp in (("consumed", LATER), ("expired", EARLIER), ("reserved", EARLIER)):        # cerradas o caducadas
        db.tables["marketing_storage_reservations"].append({"tenant_id": T1, "reservation_key": f"job:{st}{exp}",
                                                            "kind": "generation", "bytes": 9, "status": st, "expires_at": exp})
    db.tables["marketing_generation_outputs"].append({"id": "o3", "tenant_id": T2, "job_id": "j", "byte_size": 10 ** 9})
    expected = base + 1000 + 300 + 5000 + 700 + 20000
    assert ms.used_bytes(db, T1) == expected
    assert lib.library(jwt(OWNER), T1)["storage"]["used_bytes"] == expected


def test_derivatives_can_fill_the_quota(lib, db):
    m = lib.upload(jwt(OWNER), T1, "a.png", "image/png", PNG)
    setlimit(db, len(PNG) + 2000)
    db.tables["marketing_media_derivatives"].append({"id": "d1", "tenant_id": T1, "media_id": m["id"], "status": "ready",
                                                     "byte_size": 1990, "kind": "anonymized"})
    assert lib.library(jwt(OWNER), T1)["storage"]["state"] == "enabled"
    assert lib.upload_limit(jwt(OWNER), T1) == 10                           # cuenta también los derivados
    assert err(lib.upload, jwt(OWNER), T1, "b.png", "image/png", png(w=8)) in ("file_too_large", "limit_library_storage")


def test_upload_reservation_is_consumed_or_released(lib, db, monkeypatch):
    lib.upload(jwt(OWNER), T1, "a.png", "image/png", PNG)
    assert [r["status"] for r in db.tables["marketing_storage_reservations"]] == ["consumed"]
    monkeypatch.setattr(db, "storage_upload", lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    assert err(lib.upload, jwt(OWNER), T1, "b.png", "image/png", png(w=9)) == "storage_unavailable"
    assert db.tables["marketing_storage_reservations"][-1]["status"] == "released"      # sin fugas de cuota


def test_library_disabled_rejects_reservation(lib, db):
    setlimit(db, 0)
    assert err(lib.upload, jwt(OWNER), T1, "a.png", "image/png", PNG) == "library_disabled"


def test_concurrent_uploads_cannot_exceed_quota(lib, db):
    files = [png(w=100 + i) for i in range(6)]
    setlimit(db, len(files[0]) * 2 + 10)                                    # caben exactamente dos
    db.rpc_delay = 0.02
    db.tables["marketing_settings"][0]["max_upload_bytes"] = 10_000_000
    ok, errors = [], []

    def go(i):
        try:
            LibraryService(db, now=NOW).upload(jwt(OWNER), T1, f"f{i}.png", "image/png", files[i])
            ok.append(i)
        except Exception as e:                                              # noqa: BLE001
            errors.append(getattr(e, "code", "error"))
    ts = [threading.Thread(target=go, args=(i,)) for i in range(6)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(ok) == 2 and set(errors) <= {"limit_library_storage", "file_too_large"}
    assert ms.used_bytes(db, T1) <= db.tables["marketing_settings"][0]["library_storage_limit_bytes"]


def test_job_approval_reserves_output_bytes_and_respects_quota(lib, studio, db):
    j = studio.estimate_job(jwt(OWNER), T1, new_job(studio, [], 0, 100)["id"])
    need = j["request_metadata"]["estimate"]["estimated_output_bytes"]
    assert need == ms.estimated_output_bytes(30)
    setlimit(db, need - 1)
    assert err(studio.approve_job, jwt(OWNER), T1, j["id"], True) == "limit_library_storage"
    setlimit(db, need + 10)
    out = studio.approve_job(jwt(OWNER), T1, j["id"], True)
    assert out["reserved_storage_bytes"] == need and ms.used_bytes(db, T1) == need       # pendiente: cuenta
    j2 = studio.estimate_job(jwt(OWNER), T1, new_job(studio, [], 0, 100)["id"])
    assert err(studio.approve_job, jwt(OWNER), T1, j2["id"], True) == "limit_library_storage"
    studio.process_job(jwt(OWNER), T1, j["id"])                            # terminado (mock sin archivos): libera
    assert ms.used_bytes(db, T1) == 0
    assert studio.approve_job(jwt(OWNER), T1, j2["id"], True)["status"] == "queued"


def test_concurrent_job_approvals_share_storage_safely(lib, studio, db):
    need = ms.estimated_output_bytes(30)
    setlimit(db, need + need // 2)                                          # cabe uno
    db.rpc_delay = 0.02
    jobs = [studio.estimate_job(jwt(OWNER), T1, new_job(studio, [], 0, 100)["id"]) for _ in range(3)]
    res = []

    def go(j):
        try:
            res.append(studio.approve_job(jwt(OWNER), T1, j["id"], True)["status"])
        except Exception as e:                                              # noqa: BLE001
            res.append(getattr(e, "code", "error"))
    ts = [threading.Thread(target=go, args=(j,)) for j in jobs]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(res) == ["limit_library_storage", "limit_library_storage", "queued"]


def test_other_tenant_usage_never_counts(lib, db):
    db.tables["marketing_settings"][1].update({"marketing_enabled": True, "library_storage_limit_bytes": 10 ** 9})
    LibraryService(db, now=NOW).upload(jwt(OTHER), T2, "b.png", "image/png", png(w=33))
    assert ms.used_bytes(db, T1) == 0
