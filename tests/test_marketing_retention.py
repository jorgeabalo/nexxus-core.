"""
AITA Marketing (Fase 2): retención y purga de la Biblioteca con reloj controlado.
Sin red ni Storage real: el "almacenamiento" está en memoria. Ninguna tarea programada.
"""
import inspect
from datetime import timedelta
from pathlib import Path

import pytest

from services import marketing_retention as rt
from services.marketing_ai_router import MarketingAIRouter
from services.marketing_library import LibraryService
from services.marketing_privacy import MockFaceDetector
from services.marketing_studio import StudioService
from test_marketing import NOW, T1, T2, OWNER, STAFF, MEMBER, OTHER
from test_marketing_studio import StudioDB, PNG, png, err, jwt, no_people, consented, new_job, approved_job

ROOT = Path(__file__).resolve().parents[1]
DAY = timedelta(days=1)


@pytest.fixture
def db():
    d = StudioDB()
    for s in d.tables["marketing_settings"]:
        s["library_storage_limit_bytes"] = 1073741824
    return d


@pytest.fixture
def lib(db):
    return LibraryService(db, now=NOW, detector=MockFaceDetector())


@pytest.fixture
def studio(db):
    return StudioService(db, now=NOW, router=MarketingAIRouter(env={}))


def up(lib, data=PNG, tenant=T1, who=OWNER):
    return lib.upload(jwt(who), tenant, "foto.png", "image/png", data)


def run(db, days):
    return rt.RetentionRunner(db, now=NOW + timedelta(days=days)).run()


def row(db, mid):
    return next(r for r in db.tables["marketing_media"] if r["id"] == mid)


def actions(db, mid):
    return [e["action"] for e in db.tables["marketing_media_events"] if e["media_id"] == mid]


# ------------------------------------------------------------------ política
def test_default_and_choices_never_permanent():
    assert rt.default_days({}) == 30 and rt.allowed_choices({}) == [7, 30]
    assert rt.allowed_choices({"max_retention_days": 90}) == [7, 30, 60, 90]
    assert rt.plan_max({"max_retention_days": 10_000}) == 90                 # el máximo absoluto es 90
    for bad in (0, None, "permanent", -1, 365, 45):
        with pytest.raises(Exception):
            rt.check_choice({"max_retention_days": 90}, bad)


def test_upload_sets_expiry(lib, db):
    m = up(lib)
    assert m["retention_days"] == 30 and m["expires_at"] == (NOW + 30 * DAY).isoformat()
    assert m["retention_status"] == "active"


# ------------------------------------------------------------------ vencimiento y aviso
def test_normal_expiry_and_warning(lib, db):
    m = up(lib)
    assert run(db, 10)["warned"] == 0 and row(db, m["id"])["retention_status"] == "active"
    run(db, 24)                                                       # faltan 6 días: aviso
    r = row(db, m["id"])
    assert r["retention_status"] == "expiring" and actions(db, m["id"]).count("expiry_warning") == 1
    info = LibraryService(db, now=NOW + 24 * DAY).library(jwt(OWNER), T1)["items"][0]["retention"]
    assert info["warning"] and info["days_left"] == 6 and info["expires_at"] == (NOW + 30 * DAY).isoformat()
    assert lib.library(jwt(OWNER), T1)["items"][0]["retention"]["warning"] is False
    run(db, 25)
    assert actions(db, m["id"]).count("expiry_warning") == 1                # sin avisos repetidos
    assert row(db, m["id"])["expires_at"] == (NOW + 30 * DAY).isoformat()    # nunca se extiende solo
    out = run(db, 31)
    r = row(db, m["id"])
    assert out["purged"] == 1 and r["retention_status"] == "purged" and r["processing_status"] == "deleted"
    assert r["purge_reason"] == "expired" and db.storage == {}
    assert r["original_filename"] is None and r["metadata"] == {} and r["checksum"] == m["checksum"]
    assert r["storage_path"].endswith("/purged")
    assert err(lib.content, jwt(OWNER), T1, m["id"]) == "not_found"


def test_purge_is_idempotent(lib, db):
    m = up(lib)
    run(db, 31)
    removed, events = list(db.removed), list(db.tables["marketing_media_events"])
    run(db, 32)
    assert db.removed == removed and db.tables["marketing_media_events"] == events
    assert rt.RetentionRunner(db, now=NOW).purge(row(db, m["id"]), "expired") == "purged"


# ------------------------------------------------------------------ protección
def test_active_job_protects_until_safety_limit(lib, studio, db):
    m = up(lib)
    no_people(lib, m)
    j = new_job(studio, [m["id"]])                                            # trabajo activo (borrador)
    assert run(db, 31)["protected"] == 1
    r = row(db, m["id"])
    assert r["retention_status"] == "protected_by_workflow" and db.storage
    assert r["protected_until"] == (NOW + 45 * DAY).isoformat()               # 14 días desde que empieza la protección
    run(db, 40)
    assert row(db, m["id"])["retention_status"] == "protected_by_workflow"
    run(db, 45)                                                               # trabajo atascado: se corta
    r = row(db, m["id"])
    assert r["retention_status"] == "purged" and r["purge_reason"] == "workflow_timeout"
    job = studio.job(jwt(OWNER), T1, j["id"])
    assert job["status"] == "cancelled" and job["error_code"] == "timeout"
    ev = [e for e in db.tables["marketing_generation_job_events"] if e["job_id"] == j["id"] and e["to_status"] == "cancelled"]
    run(db, 46)
    run(db, 47)                                                               # idempotente: nada se repite
    assert len([e for e in db.tables["marketing_generation_job_events"]
                if e["job_id"] == j["id"] and e["to_status"] == "cancelled"]) == len(ev) == 1
    assert actions(db, m["id"]).count("purged") == 1


def test_timeout_requests_purge_even_if_storage_fails(lib, studio, db, monkeypatch):
    m = up(lib)
    no_people(lib, m)
    new_job(studio, [m["id"]])
    run(db, 31)                                                               # empieza la protección (14 días)
    monkeypatch.setattr(db, "storage_remove", lambda b, k: (_ for _ in ()).throw(RuntimeError("-> 500")))
    run(db, 46)
    r = row(db, m["id"])
    assert r["purge_reason"] == "workflow_timeout" and r["purge_requested_at"] and r["retention_status"] == "purge_failed"


def test_timeout_blocks_access_before_removing(lib, studio, db, monkeypatch):
    m = up(lib)
    no_people(lib, m)
    new_job(studio, [m["id"]])
    run(db, 31)
    seen = []
    monkeypatch.setattr(db, "storage_remove", lambda b, k: seen.append(row(db, m["id"])["retention_status"]))
    run(db, 46)
    assert seen == ["purge_pending"]


def test_finished_job_does_not_protect(lib, studio, db):
    m = up(lib)
    no_people(lib, m)
    j = new_job(studio, [m["id"]])
    studio.cancel_job(jwt(OWNER), T1, j["id"])
    run(db, 31)
    assert row(db, m["id"])["retention_status"] == "purged"


def test_scheduled_publication_protects_and_published_sets_30_days(lib, studio, db):
    db.tables["marketing_settings"][0]["max_retention_days"] = 90
    m = up(lib)
    no_people(lib, m)
    lib.set_retention(jwt(OWNER), T1, m["id"], 90)
    j = approved_job(studio, [m["id"]], 100, 0)
    studio.process_job(jwt(OWNER), T1, j["id"])
    cid = studio.send_to_approval(jwt(OWNER), T1, j["id"], "Reel")["content"]["id"]
    content = next(c for c in db.tables["marketing_content"] if c["id"] == cid)
    content["status"] = "scheduled"                                           # programado (simulado)
    run(db, 91)
    assert row(db, m["id"])["retention_status"] == "protected_by_workflow"
    content["status"] = "published"                                           # confirmación del proveedor
    db.tables["marketing_publications"].append({"id": "p1", "tenant_id": T1, "content_id": cid, "status": "published",
                                                "published_at": (NOW + 60 * DAY).isoformat()})
    run(db, 92)                                                               # ya publicado: deja de protegerse
    r = row(db, m["id"])
    assert r["retention_status"] == "purged" and r["purge_reason"] == "publication_done"


@pytest.mark.parametrize("pub_day,plan,expected", [(5, 90, 35), (75, 90, 90), (20, 30, 50), (88, 90, 90)])
def test_publication_expiry_is_min_of_pub_plus_30_and_upload_plus_90(lib, studio, db, pub_day, plan, expected):
    db.tables["marketing_settings"][0]["max_retention_days"] = plan
    m = up(lib)
    no_people(lib, m)
    j = approved_job(studio, [m["id"]], 100, 0)
    studio.process_job(jwt(OWNER), T1, j["id"])
    cid = studio.send_to_approval(jwt(OWNER), T1, j["id"], "Reel")["content"]["id"]
    next(c for c in db.tables["marketing_content"] if c["id"] == cid)["status"] = "published"
    db.tables["marketing_publications"].append({"id": "p", "tenant_id": T1, "content_id": cid, "status": "published",
                                                "published_at": (NOW + pub_day * DAY).isoformat()})
    rt.RetentionRunner(db, now=NOW + pub_day * DAY).run()
    assert row(db, m["id"])["expires_at"] == (NOW + expected * DAY).isoformat()
    assert rt._dt(row(db, m["id"])["expires_at"]) <= NOW + 90 * DAY                  # nunca más de 90 días


def test_published_file_expires_30_days_after_publication(lib, studio, db):
    db.tables["marketing_settings"][0]["max_retention_days"] = 90
    m = up(lib)
    no_people(lib, m)
    lib.set_retention(jwt(OWNER), T1, m["id"], 90)
    j = approved_job(studio, [m["id"]], 100, 0)
    studio.process_job(jwt(OWNER), T1, j["id"])
    cid = studio.send_to_approval(jwt(OWNER), T1, j["id"], "Reel")["content"]["id"]
    next(c for c in db.tables["marketing_content"] if c["id"] == cid)["status"] = "published"
    db.tables["marketing_publications"].append({"id": "p1", "tenant_id": T1, "content_id": cid, "status": "published",
                                                "published_at": (NOW + 5 * DAY).isoformat()})
    run(db, 6)
    r = row(db, m["id"])
    assert r["published_at"] == (NOW + 5 * DAY).isoformat() and r["expires_at"] == (NOW + 35 * DAY).isoformat()
    run(db, 34)
    assert row(db, m["id"])["retention_status"] != "purged"                   # se conserva hasta 30 días tras publicar
    run(db, 36)
    r = row(db, m["id"])
    assert r["retention_status"] == "purged" and r["purge_reason"] == "publication_done"


# ------------------------------------------------------------------ extensión
def test_extension_only_within_plan(lib, db):
    m = up(lib)
    assert err(lib.set_retention, jwt(OWNER), T1, m["id"], 60) == "retention_exceeds_plan"
    db.tables["marketing_settings"][0]["max_retention_days"] = 60
    r = lib.set_retention(jwt(OWNER), T1, m["id"], 60)
    assert r["expires_at"] == (NOW + 60 * DAY).isoformat() and r["retention"]["days_left"] == 60
    assert err(lib.set_retention, jwt(OWNER), T1, m["id"], 90) == "retention_exceeds_plan"
    for bad in (0, None, "permanent", 365):
        assert err(lib.set_retention, jwt(OWNER), T1, m["id"], bad) == "invalid_retention"
    late = LibraryService(db, now=NOW + 10 * DAY)
    assert err(late.set_retention, jwt(OWNER), T1, m["id"], 7) == "retention_too_short"
    assert actions(db, m["id"]).count("retention_change") == 1


def test_staff_member_and_other_tenant_cannot_change_retention_or_delete(lib):
    m = up(lib)
    for who, tenant, code in ((STAFF, T1, "forbidden"), (MEMBER, T1, "forbidden"), (OTHER, T2, "not_found")):
        assert err(lib.set_retention, jwt(who), tenant, m["id"], 7) == code
        assert err(lib.delete, jwt(who), tenant, m["id"], "", True) == code


# ------------------------------------------------------------------ consentimiento retirado
def test_consent_revoked_is_priority_purge(lib, studio, db):
    m = up(lib)
    consented(lib, m)
    j = new_job(studio, [m["id"]])
    lib.revoke_consent(jwt(OWNER), T1, m["id"])
    assert studio.job(jwt(OWNER), T1, j["id"])["status"] == "cancelled"
    out = run(db, 1)                                                          # aunque no haya vencido
    r = row(db, m["id"])
    assert out["purged"] == 1 and r["retention_status"] == "purged" and r["purge_reason"] == "consent_revoked"
    assert db.storage == {}


def test_results_using_revoked_media_cannot_be_reused(lib, studio, db):
    m = up(lib)
    consented(lib, m)
    j = approved_job(studio, [m["id"]], 50, 50)
    studio.process_job(jwt(OWNER), T1, j["id"])
    lib.revoke_consent(jwt(OWNER), T1, m["id"])
    assert err(studio.send_to_approval, jwt(OWNER), T1, j["id"], "Reel") == "consent_revoked"
    outs = [o for o in db.tables["marketing_generation_outputs"] if o["job_id"] == j["id"]]
    assert outs and all(o["review_status"] == "rejected" and o["metadata"] == {"blocked": "consent_revoked"} for o in outs)


# ------------------------------------------------------------------ derivados y resultados
def test_mock_derivatives_expire_in_7_days_and_media_purge_removes_derivative_files(lib, db):
    m = up(lib)
    lib.classify(jwt(OWNER), T1, m["id"], {"contains_people": True, "contains_minors": False, "people_policy": "anonymize"})
    d = lib.anonymize(jwt(OWNER), T1, m["id"], "blur_faces")
    der = db.tables["marketing_media_derivatives"][0]
    der["expires_at"] = (NOW + 7 * DAY).isoformat()
    assert run(db, 8)["derivatives"] == 1 and der["status"] == "deleted" and der["metadata"] == {}
    # un derivado real con archivo se borra junto al original
    m2 = up(lib, png(w=7))
    path = f"{T1}/derivatives/{m2['id']}/{d['id']}.png"
    db.storage[("marketing-assets", path)] = (b"x", "image/png")
    db.tables["marketing_media_derivatives"].append({"id": "d2", "tenant_id": T1, "media_id": m2["id"], "status": "ready",
                                                     "storage_path": path, "expires_at": (NOW + 60 * DAY).isoformat()})
    lib.delete(jwt(OWNER), T1, m2["id"], "", True)
    assert ("marketing-assets", path) not in db.storage and db.tables["marketing_media_derivatives"][-1]["status"] == "deleted"


def test_outputs_of_jobs_expire(lib, studio, db):
    j = approved_job(studio)
    studio.process_job(jwt(OWNER), T1, j["id"])
    outs = [o for o in db.tables["marketing_generation_outputs"] if o["job_id"] == j["id"]]
    assert all(o["expires_at"] == (NOW + 7 * DAY).isoformat() for o in outs)        # simulados: 7 días
    assert run(db, 8)["outputs"] == len(outs) and all(o["purged_at"] and o["metadata"] == {} for o in outs)


# ------------------------------------------------------------------ fallos, reintentos y aislamiento
def test_failure_retry_and_attempt_limit(lib, db, monkeypatch):
    m = up(lib)
    calls = []

    def broken(bucket, key):
        calls.append(key)
        raise RuntimeError("Supabase storage DELETE -> 500: boom")
    monkeypatch.setattr(db, "storage_remove", broken)
    run(db, 31)
    r = row(db, m["id"])
    assert r["retention_status"] == "purge_failed" and r["purge_attempts"] == 1 and r["last_purge_error"] == "storage_error"
    assert err(lib.content, jwt(OWNER), T1, m["id"]) == "not_found"           # acceso bloqueado igualmente
    for k in range(2, 6):
        run(db, 31 + k)
    assert row(db, m["id"])["purge_attempts"] == 5
    run(db, 40)
    assert len(calls) == 5                                                    # sin reintentos infinitos
    monkeypatch.undo()
    row(db, m["id"])["purge_attempts"] = 0                                    # el operador lo reactiva
    run(db, 41)
    assert row(db, m["id"])["retention_status"] == "purged"


def test_access_is_blocked_before_any_object_is_removed(lib, db, monkeypatch):
    m = up(lib)
    seen = []

    def check(bucket, key):
        seen.append(row(db, m["id"])["retention_status"])          # estado en el momento de borrar
    monkeypatch.setattr(db, "storage_remove", check)
    run(db, 31)
    assert seen == ["purge_pending"]


def test_missing_object_is_safe(lib, db, monkeypatch):
    up(lib)
    monkeypatch.setattr(db, "storage_remove", lambda b, k: (_ for _ in ()).throw(RuntimeError("Supabase storage DELETE -> 404: not found")))
    assert run(db, 31)["purged"] == 1


def test_tenant_isolation_and_unvalidated_paths(lib, db):
    m1 = up(lib)
    lib2 = LibraryService(db, now=NOW + 20 * DAY)
    db.tables["marketing_settings"][1].update({"marketing_enabled": True, "library_storage_limit_bytes": 1073741824,
                                               "max_upload_bytes": 10_000_000})
    m2 = lib2.upload(jwt(OTHER), T2, "x.png", "image/png", png(w=9))
    run(db, 31)
    assert row(db, m1["id"])["retention_status"] == "purged" and row(db, m2["id"])["retention_status"] == "active"
    assert ("marketing-assets", m2["storage_path"]) in db.storage
    # una fila con una ruta que no es de su tenant nunca se borra
    m3 = up(lib, png(w=11))
    victim = m2["storage_path"]
    row(db, m3["id"])["storage_path"] = victim
    before = list(db.removed)
    run(db, 32)
    r = row(db, m3["id"])
    assert r["retention_status"] == "purge_failed" and r["last_purge_error"] == "invalid_path"
    assert db.removed == before and ("marketing-assets", victim) in db.storage


def test_no_scheduled_purge_in_production():
    """La purga solo existe como servicio: nada la programa ni la expone por HTTP en este PR."""
    for f in ("main.py", "services/marketing_studio_routes.py", "services/marketing_routes.py"):
        src = (ROOT / f).read_text()
        assert "RetentionRunner" not in src and "run_pending" not in src, f
    assert "def run(" in inspect.getsource(rt.RetentionRunner)
