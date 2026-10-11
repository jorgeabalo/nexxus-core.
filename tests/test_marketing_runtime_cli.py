"""
AITA Marketing (Fase 2): comandos de fondo (services/marketing_runtime.py).
retention: dry-run por defecto (nada se escribe), --apply purga; un solo purgador a la vez; repetible;
primero se quita el acceso y después el objeto; objeto ya inexistente = purga cerrada; nunca una ruta
ajena; reintentos limitados con último error. worker: solo con MARKETING_WORKER_ENABLED=true y sin
proveedores reales activados.
"""
import copy
import json
import threading
import time
from datetime import timedelta

import pytest

from services import marketing_retention as rt
from services import marketing_runtime as runtime
from services.marketing_library import LibraryService
from services.marketing_retention import RetentionRunner
from services.marketing_studio import StudioService
from services.marketing_ai_router import MarketingAIRouter, MockProvider, OmniRouteAdapter, DisabledLocalTools
from test_marketing import NOW, T1, T2, OWNER
from test_marketing_studio import StudioDB, up, png, approved_job

LATER = NOW + timedelta(days=40)


@pytest.fixture
def db():
    d = StudioDB()
    d.rpc_now = LATER.isoformat()
    return d


def expired_media(db, n=1):
    lib = LibraryService(db, now=NOW)
    return [up(lib, name=f"f{i}.png", data=png(w=20 + i)) for i in range(n)]


def media(db, mid):
    return next(x for x in db.tables["marketing_media"] if x["id"] == mid)


# ------------------------------------------------------------------ retention: dry-run / apply
def test_dry_run_writes_nothing_but_reports(db):
    ms = expired_media(db, 2)
    before = copy.deepcopy(db.tables), dict(db.storage)
    out = runtime.run_retention(db, apply=False, batch_size=50, max_batches=5, now=LATER)
    assert out[0]["mode"] == "dry_run" and out[0]["purged"] == 2 and out[0]["would_write"] > 0
    assert db.tables == before[0] and db.storage == before[1]          # ni base ni Storage cambian
    assert all(media(db, m["id"])["retention_status"] == "active" for m in ms)


def test_apply_purges_and_is_repeatable(db):
    ms = expired_media(db, 3)
    out = runtime.run_retention(db, apply=True, batch_size=2, max_batches=5, now=LATER)
    assert sum(o["purged"] for o in out) == 3 and len(out) >= 2         # por lotes
    assert all(media(db, m["id"])["retention_status"] == "purged" for m in ms)
    again = runtime.run_retention(db, apply=True, batch_size=50, max_batches=5, now=LATER)
    assert again[0]["purged"] == 0                                      # repetible: nada que hacer
    assert db.tables["marketing_runtime_leases"] == []                  # el lease se libera


def test_two_runners_at_once_only_one_works(db, monkeypatch):
    expired_media(db, 1)
    orig = RetentionRunner._run
    monkeypatch.setattr(RetentionRunner, "_run", lambda self, limit: (time.sleep(0.2), orig(self, limit))[1])
    res, barrier = [], threading.Barrier(2)

    def go():
        barrier.wait()
        res.append(RetentionRunner(db, now=LATER).run())
    ts = [threading.Thread(target=go) for _ in range(2)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(bool(r.get("locked")) for r in res) == [False, True]
    assert sum(int(r.get("purged") or 0) for r in res) == 1


def test_crashed_runner_lease_expires_and_is_taken_over(db):
    expired_media(db, 1)
    db.tables["marketing_runtime_leases"].append({"name": "retention_runner", "holder": "dead",
                                                  "acquired_at": NOW.isoformat(), "expires_at": NOW.isoformat()})
    assert RetentionRunner(db, now=LATER).run()["purged"] == 1


def test_live_foreign_lease_blocks(db):
    expired_media(db, 1)
    db.tables["marketing_runtime_leases"].append({"name": "retention_runner", "holder": "other",
                                                  "acquired_at": LATER.isoformat(),
                                                  "expires_at": (LATER + timedelta(minutes=5)).isoformat()})
    assert RetentionRunner(db, now=LATER).run() == {"locked": True}


# ------------------------------------------------------------------ purga: orden, rutas, reintentos
def test_access_is_removed_before_the_object(db, monkeypatch):
    m = expired_media(db, 1)[0]
    db.tables["marketing_stream_tokens"].append({"tenant_id": T1, "media_id": m["id"], "user_id": OWNER,
                                                 "token_hash": "a" * 64, "revoked_at": None,
                                                 "expires_at": LATER.isoformat()})
    seen = []
    orig = db.storage_remove

    def spy(bucket, key):
        seen.append((media(db, m["id"])["retention_status"], db.tables["marketing_stream_tokens"][0]["revoked_at"]))
        orig(bucket, key)
    monkeypatch.setattr(db, "storage_remove", spy)
    RetentionRunner(db, now=LATER).run()
    assert seen and all(st == "purge_pending" and rev for st, rev in seen)


def test_missing_object_closes_purge(db, monkeypatch):
    m = expired_media(db, 1)[0]
    monkeypatch.setattr(db, "storage_remove", lambda b, k: (_ for _ in ()).throw(
        RuntimeError("Supabase storage DELETE -> 404: Object not found")))
    assert RetentionRunner(db, now=LATER).run()["purged"] == 1
    assert media(db, m["id"])["retention_status"] == "purged"


def test_foreign_path_never_deleted_and_retries_are_limited(db):
    m = expired_media(db, 1)[0]
    foreign = f"{T2}/originals/{m['id']}/f0.png"
    media(db, m["id"])["storage_path"] = foreign
    db.storage[("marketing-assets", foreign)] = (b"ajeno", "image/png")
    for _ in range(rt.MAX_ATTEMPTS + 2):
        RetentionRunner(db, now=LATER).run()
    row = media(db, m["id"])
    assert row["retention_status"] == "purge_failed" and row["last_purge_error"] == "invalid_path"
    assert row["purge_attempts"] == rt.MAX_ATTEMPTS                     # no reintenta indefinidamente
    assert ("marketing-assets", foreign) in db.storage and foreign not in db.removed


# ------------------------------------------------------------------ worker
def test_worker_requires_explicit_enable(db):
    assert runtime.run_worker(db, 5, 120, env={})["status"] == "disabled"


@pytest.mark.parametrize("flag", ["OMNIROUTE_ENABLED", "MARKETING_LOCAL_TOOLS_ENABLED"])
def test_worker_refuses_real_providers(db, flag):
    res = runtime.run_worker(db, 5, 120, env={"MARKETING_WORKER_ENABLED": "true", flag: "true"})
    assert res == {"status": "disabled", "reason": "real_providers_not_allowed"}


def test_worker_processes_mock_jobs_when_enabled(db):
    db.rpc_now = NOW.isoformat()
    studio = StudioService(db, now=NOW, router=MarketingAIRouter(env={}),
                           adapters={"mock": MockProvider(), "omniroute": OmniRouteAdapter(env={}),
                                     "local_ffmpeg": DisabledLocalTools()})
    approved_job(studio)
    res = runtime.run_worker(db, 5, 120, env={"MARKETING_WORKER_ENABLED": "true"})
    assert res["status"] == "ok" and res["processed"] == 1 and res["succeeded"] == 1


# ------------------------------------------------------------------ main()
def test_main_exit_codes_and_json(db, capsys, monkeypatch):
    expired_media(db, 1)
    assert runtime.main(["retention", "--max-batches", "1"], db=db) == 0
    line = json.loads(capsys.readouterr().out.strip().splitlines()[0])
    assert line["mode"] == "dry_run"
    monkeypatch.delenv("MARKETING_WORKER_ENABLED", raising=False)
    assert runtime.main(["worker"], db=db) == 2
    monkeypatch.setattr(runtime, "_db", lambda: None)
    assert runtime.main(["retention"]) == 3
