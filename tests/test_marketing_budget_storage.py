"""
AITA Marketing (Fase 2): "Marketing AI budget" (solo IA de Marketing; máximo temporal 20 USD/mes; null,
desconocido, 0 o más de 20 = cerrado; nunca el techo global de AITA de 80 USD como presupuesto propio),
desglose del almacenamiento (activo · vencido pendiente de eliminación · reservado · disponible) y modos
explícitos de los comandos (dry-run/apply, once/loop).
"""
import json
from datetime import timedelta

import pytest

from services import marketing_jobs_domain as jd
from services import marketing_runtime as runtime
from services import marketing_storage as ms
from services.marketing_ai_router import MarketingAIRouter, MockProvider, OmniRouteAdapter, DisabledLocalTools
from services.marketing_library import LibraryService
from services.marketing_retention import RetentionRunner
from services.marketing_studio import StudioService
from test_marketing import NOW, T1, OWNER
from test_marketing_studio import StudioDB, err, jwt, up, png, new_job

LATER = NOW + timedelta(days=40)


@pytest.fixture
def db():
    return StudioDB()


@pytest.fixture
def studio(db):
    return StudioService(db, now=NOW, router=MarketingAIRouter(env={}),
                         adapters={"mock": MockProvider(), "omniroute": OmniRouteAdapter(env={}),
                                   "local_ffmpeg": DisabledLocalTools()})


# ------------------------------------------------------------------ presupuesto
@pytest.mark.parametrize("value,effective", [(0, 0.0), (None, 0.0), ("desconocido", 0.0), (-1, 0.0), (10, 10.0),
                                             (20, 20.0), (20.01, 0.0), (21, 0.0), (80, 0.0), (1000, 0.0)])
def test_marketing_ai_budget_values(value, effective):
    assert jd.ai_budget({"monthly_ai_cost_limit": value}) == effective
    assert jd.generation_enabled({"ai_generation_enabled": True, "monthly_generation_job_limit": 5,
                                  "monthly_ai_cost_limit": value}) is (effective > 0)


def test_cap_is_marketing_only_and_below_global_target():
    assert jd.AI_BUDGET_CAP_USD == 20.0 < 80.0                          # nunca el techo global de AITA


@pytest.mark.parametrize("value", [0, None, 21, 80])
def test_approval_fails_closed_without_calling_the_database(studio, db, value):
    j = studio.estimate_job(jwt(OWNER), T1, new_job(studio, [], 0, 100)["id"])
    db.tables["marketing_settings"][0]["monthly_ai_cost_limit"] = value
    calls = len(db.rpc_calls)
    assert err(studio.approve_job, jwt(OWNER), T1, j["id"], True) == "generation_disabled"
    assert "marketing_approve_generation" not in db.rpc_calls[calls:]


@pytest.mark.parametrize("value,limit,warning", [(20, 20.0, None), (80, 0.0, "not_enabled"), (None, 0.0, "not_enabled"),
                                                 (0, 0.0, "not_enabled")])
def test_budget_summary_never_shows_global_ceiling(value, limit, warning):
    b = jd.budget_summary(value, {"consumed": 0, "reserved": 0})
    assert b["limit"] == limit and b["warning"] == warning and b["scope"] == "marketing_ai_budget"


def test_overview_reports_marketing_only_budget(studio, db):
    b = studio.overview(jwt(OWNER), T1)["budget"]
    assert b["scope"] == "marketing_ai_budget" and b["limit"] == 20.0


# ------------------------------------------------------------------ almacenamiento
def test_storage_breakdown_shows_expired_pending_and_purge_reduces_it(db):
    lib = LibraryService(db, now=NOW)
    a = up(lib, name="a.png", data=png(w=30))
    b = up(lib, name="b.png", data=png(w=31))
    db.tables["marketing_media_derivatives"].append({"id": "d1", "tenant_id": T1, "media_id": b["id"], "status": "ready",
                                                     "byte_size": 300, "kind": "thumbnail"})
    ms.reserve(db, T1, "upload", 700, "upload:en-curso")
    next(x for x in db.tables["marketing_media"] if x["id"] == b["id"])["expires_at"] = (NOW - timedelta(seconds=1)).isoformat()
    st = lib.library(jwt(OWNER), T1)["storage"]
    used = a["byte_size"] + b["byte_size"] + 300 + 700
    assert st["used_bytes"] == used
    assert st["expired_pending_bytes"] == b["byte_size"] + 300           # original vencido + su derivado
    assert st["reserved_bytes"] == 700 and st["active_bytes"] == a["byte_size"]
    assert st["available_bytes"] == db.tables["marketing_settings"][0]["library_storage_limit_bytes"] - used
    assert [m["id"] for m in lib.library(jwt(OWNER), T1)["items"]] == [a["id"]]   # invisible, pero ocupa espacio
    RetentionRunner(db, now=NOW).purge(next(x for x in db.tables["marketing_media"] if x["id"] == b["id"]), "expired")
    st2 = LibraryService(db, now=NOW).library(jwt(OWNER), T1)["storage"]
    assert st2["expired_pending_bytes"] == 0 and st2["used_bytes"] == a["byte_size"] + 700


def test_storage_breakdown_unlimited_has_no_available_figure(db):
    db.tables["marketing_settings"][0]["library_storage_limit_bytes"] = None
    st = ms.summary(db, T1, db.tables["marketing_settings"][0], NOW)
    assert st["available_bytes"] is None and st["state"] == "unlimited"


def test_expired_cannot_be_recovered_or_extended(db):
    lib = LibraryService(db, now=NOW)
    m = up(lib)
    next(x for x in db.tables["marketing_media"] if x["id"] == m["id"])["expires_at"] = (NOW - timedelta(seconds=1)).isoformat()
    assert err(lib.set_retention, jwt(OWNER), T1, m["id"], 90) == "media_expired"
    assert err(lib.set_archived, jwt(OWNER), T1, m["id"], False) == "media_expired"


# ------------------------------------------------------------------ comandos sin ambigüedad
def test_retention_mode_flags(db, capsys):
    for argv, mode in ((["retention"], "dry_run"), (["retention", "--dry-run"], "dry_run"), (["retention", "--apply"], "apply")):
        assert runtime.main(argv + ["--max-batches", "1"], db=db) == 0
        assert json.loads(capsys.readouterr().out.strip().splitlines()[0])["mode"] == mode
    with pytest.raises(SystemExit) as e:
        runtime.main(["retention", "--dry-run", "--apply"], db=db)
    assert e.value.code == 2


def test_worker_default_is_once_never_loop(db, capsys, monkeypatch):
    monkeypatch.setenv("MARKETING_WORKER_ENABLED", "true")
    monkeypatch.setattr(runtime, "run_worker_loop", lambda *a, **k: (_ for _ in ()).throw(AssertionError("loop")))
    for argv in (["worker"], ["worker", "--once"]):
        assert runtime.main(argv, db=db) == 0
        assert json.loads(capsys.readouterr().out)["mode"] == "once"
    with pytest.raises(SystemExit):
        runtime.main(["worker", "--once", "--loop"], db=db)


def test_loop_only_when_asked_and_bounded(db):
    t = [0.0]
    slept = []
    res = runtime.run_worker_loop(db, 5, 120, poll_seconds=30, max_runtime_seconds=100,
                                  env={"MARKETING_WORKER_ENABLED": "true"},
                                  sleep=lambda s: (slept.append(s), t.__setitem__(0, t[0] + s)), clock=lambda: t[0])
    assert res["mode"] == "loop" and res["rounds"] == 4 and slept == [30, 30, 30]   # rondas a 0, 30, 60 y 90 s; nunca > 100
    stopped = runtime.run_worker_loop(db, 5, 120, 30, 100, env={"MARKETING_WORKER_ENABLED": "true"},
                                      sleep=lambda s: None, clock=lambda: 0.0, stop=lambda: True)
    assert stopped["rounds"] == 0                                       # SIGTERM: no empieza otra ronda
    assert runtime.run_worker_loop(db, 5, 120, 30, 100, env={})["status"] == "disabled"


def test_open_upload_reservation_of_registered_file_is_not_counted_twice(db):
    lib = LibraryService(db, now=NOW)
    m = up(lib, name="c.png", data=png(w=40))
    db.tables["marketing_storage_reservations"].append({"tenant_id": T1, "reservation_key": f"upload:{m['id']}",
                                                        "kind": "upload", "bytes": m["byte_size"], "status": "reserved",
                                                        "expires_at": (NOW + timedelta(minutes=10)).isoformat()})
    st = ms.summary(db, T1, db.tables["marketing_settings"][0], NOW)    # subida a medio cerrar: el archivo ya cuenta
    assert st["used_bytes"] == m["byte_size"] and st["reserved_bytes"] == 0 and st["active_bytes"] == m["byte_size"]
