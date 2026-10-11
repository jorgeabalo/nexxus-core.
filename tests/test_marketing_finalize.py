"""
AITA Marketing (Fase 2), PR #20: cierre seguro del worker y del purgador.
1. SIGTERM con el adaptador BLOQUEADO más que el lease: las renovaciones se detienen al instante, el lease
   vence, B reclama y, cuando A regresa, no escribe nada. Sin hilos huérfanos.
2. Finalización atómica (marketing_finalize_generation): un fallo a mitad (tras el almacenamiento, tras el primer
   resultado, antes de succeeded) no deja nada; B reintenta con las mismas idempotency_key sin duplicar.
3. Transiciones terminales: un worker vencido no puede marcar failed ni cancelled; el timeout es otro camino.
4. RetentionRunner: pierde el lease en cada punto de control → se detiene sin cerrar nada ni liberar el lease nuevo.
"""
import os
import signal
import threading
import time
from datetime import timedelta

import pytest

from services import marketing_runtime as runtime
from services.marketing_ai_router import MarketingAIRouter, MockProvider
from services.marketing_lease import LeaseKeeper
from services.marketing_library import LibraryService
from services.marketing_retention import RetentionRunner
from services.marketing_studio import StudioService
from services.marketing_worker import JobRunner, WorkerError
from test_marketing import NOW, T1
from test_marketing_lease_keeper import ADAPTERS, Long, at, job, keeper_threads, wait_for
from test_marketing_studio import StudioDB, approved_job, up, png

A, B = "00000000-0000-4000-8000-0000000000aa", "00000000-0000-4000-8000-0000000000bb"


@pytest.fixture
def db():
    return StudioDB()


@pytest.fixture
def studio(db):
    return StudioService(db, now=NOW, router=MarketingAIRouter(env={}), adapters=ADAPTERS())


def runner(db, wid, adapters=None, hb=0.005):
    return JobRunner(db, MarketingAIRouter(env={}), adapters or ADAPTERS(), now=NOW, worker_id=wid,
                     lease_seconds=30, heartbeat_seconds=hb)


def rows(db, table, jid):
    return [r for r in db.tables[table] if r.get("job_id") == jid]


def reservation(db, jid):
    return dict(next(r for r in db.tables["marketing_storage_reservations"] if r["reservation_key"] == f"job:{jid}"))


# ------------------------------------------------------------------ 1. SIGTERM con el adaptador bloqueado
class Blocked(MockProvider):
    def __init__(self):
        super().__init__()
        self.entered, self.release = threading.Event(), threading.Event()

    def submit(self, req, model):
        if not self.entered.is_set():
            self.entered.set()
            self.release.wait(10)                                  # bloqueado (más que el lease, en hora de la base)
        return super().submit(req, model)


def test_sigterm_while_adapter_blocked_stops_renewals_and_writes_nothing(studio, db):
    j = approved_job(studio)
    res_before = reservation(db, j["id"])
    beats, real = [], db._rpc_marketing_job_heartbeat

    def counted(a):
        if threading.current_thread().name.startswith("lease-keeper"):
            beats.append(1)
        return real(a)
    db._rpc_marketing_job_heartbeat = counted
    blocked, stop, out = Blocked(), threading.Event(), []
    a = runner(db, A, {**ADAPTERS(), "mock": blocked})
    a.stop_event = stop
    previous = signal.getsignal(signal.SIGTERM)
    try:
        runtime.install_sigterm(stop)
        t = threading.Thread(target=lambda: out.extend(a.run_pending(limit=3)), name="worker-a")
        t.start()
        assert blocked.entered.wait(2) and wait_for(lambda: len(beats) >= 3)      # A renueva mientras está bloqueado
        os.kill(os.getpid(), signal.SIGTERM)
        assert wait_for(stop.is_set) and wait_for(lambda: keeper_threads() == [])  # el keeper se detiene YA
        n = len(beats)
        time.sleep(0.1)
        assert len(beats) == n and t.is_alive()                   # ni una renovación más; A sigue bloqueado
        at(db, seconds=31)                                        # el lease vence (hora de la base)
        b = runner(db, B)
        claimed = b.claim()
        assert claimed["status"] == "claimed" and claimed["job"]["id"] == j["id"]
        blocked.release.set()                                     # A por fin regresa
        t.join(2)
        assert not t.is_alive()
    finally:
        signal.signal(signal.SIGTERM, previous)
        blocked.release.set()
    assert out == [{"id": j["id"], "error": "interrupted"}]       # no reclamó nada más
    assert rows(db, "marketing_model_usage", j["id"]) == [] and rows(db, "marketing_generation_outputs", j["id"]) == []
    r = job(db, j["id"])
    assert r["status"] == "processing" and r["lease_owner"] == B and reservation(db, j["id"]) == res_before
    assert not [e for e in rows(db, "marketing_generation_job_events", j["id"]) if e.get("action") in ("succeed", "fail")]
    assert len(beats) == n and keeper_threads() == []
    db._rpc_marketing_job_heartbeat = real
    assert b._execute(job(db, j["id"]))["status"] == "succeeded"  # B termina normalmente


# ------------------------------------------------------------------ 2. finalización atómica
@pytest.mark.parametrize("step", ["after_storage", "after_first_output", "before_succeeded"])
def test_failure_inside_finalize_leaves_nothing_and_b_retries_without_duplicates(studio, db, step):
    j = approved_job(studio)
    res_before = reservation(db, j["id"])
    longp = Long(db, steps=0, step_seconds=0, temps=True)
    longp.job_id = j["id"]

    def boom(s):
        if s == step:
            raise RuntimeError("conexión perdida")               # la transacción se aborta a mitad
    db.finalize_hook = boom
    a = runner(db, A, {**ADAPTERS(), "mock": longp})
    assert a.run_pending(limit=1) == [{"id": j["id"], "error": "finalize_unknown"}]
    assert rows(db, "marketing_model_usage", j["id"]) == [] and rows(db, "marketing_generation_outputs", j["id"]) == []
    assert job(db, j["id"])["status"] == "processing" and reservation(db, j["id"]) == res_before
    db.finalize_hook = None
    at(db, seconds=31)                                            # el lease de A vence
    retry = Long(db, steps=0, step_seconds=0, temps=True)
    retry.job_id = j["id"]
    out = runner(db, B, {**ADAPTERS(), "mock": retry}).run_pending(limit=1)
    assert out[0].get("status") == "succeeded", out
    usage = rows(db, "marketing_model_usage", j["id"])
    assert usage and len({u["idempotency_key"] for u in usage}) == len(usage) and {u["worker_id"] for u in usage} == {B}
    outs = rows(db, "marketing_generation_outputs", j["id"])
    keys = [(o["kind"], o.get("scene_index", -1), o.get("storage_path") or "") for o in outs]
    assert len(keys) == len(set(keys)) and {o["worker_id"] for o in outs} == {B}
    assert not [o for o in outs if (o.get("storage_path") or "").split("/")[-1].startswith(f"{A}-")]
    assert job(db, j["id"])["actual_cost"] == round(sum(float(u["actual_cost"] or 0) for u in usage), 4)
    assert reservation(db, j["id"])["status"] == "consumed"       # consumida una sola vez
    assert len([e for e in rows(db, "marketing_generation_job_events", j["id"]) if e.get("action") == "succeed"]) == 1


def test_lease_expired_before_finalize_is_rejected(studio, db):
    j = approved_job(studio)
    a = runner(db, A)
    real_fin, seen = db._rpc_marketing_finalize_generation, []

    def expire_then_finalize(p):                                  # vence justo al llegar (bajo el bloqueo de la base)
        at(db, seconds=31)
        seen.append(real_fin(p))
        return seen[-1]
    db._rpc_marketing_finalize_generation = expire_then_finalize
    db.rpc_calls.clear()
    assert a.run_pending(limit=1)[0]["error"] == "lease_lost"
    assert rows(db, "marketing_generation_outputs", j["id"]) == [] and job(db, j["id"])["status"] == "processing"
    assert seen == [{"status": "rejected", "reason": "lease_lost"}]
    assert "marketing_fail_generation" not in db.rpc_calls          # sin lease ni siquiera lo intenta


def test_finalize_twice_or_with_duplicate_outputs_publishes_nothing_more(studio, db):
    j = approved_job(studio)
    a = runner(db, A)
    claimed = a.claim()["job"]
    dup = [{"kind": "script", "metadata": {}}, {"kind": "script", "metadata": {}}]
    args = {"p_tenant": T1, "p_job": j["id"], "p_worker": A, "p_usage": [], "p_outputs": dup, "p_result": {},
            "p_selected": {}}
    with pytest.raises(RuntimeError):
        db.rpc("marketing_finalize_generation", args)             # índice único: toda la transacción se revierte
    assert rows(db, "marketing_generation_outputs", j["id"]) == [] and job(db, j["id"])["status"] == "processing"
    ok = db.rpc("marketing_finalize_generation", {**args, "p_outputs": dup[:1]})
    assert ok["status"] == "succeeded" and claimed["id"] == j["id"]
    again = db.rpc("marketing_finalize_generation", {**args, "p_outputs": dup[:1]})
    assert again == {"status": "rejected", "reason": "lease_lost"}
    assert len(rows(db, "marketing_generation_outputs", j["id"])) == 1


# ------------------------------------------------------------------ 3. transiciones terminales
@pytest.mark.parametrize("reclaimed", [False, True])
def test_expired_worker_cannot_fail_or_cancel(studio, db, reclaimed):
    j = approved_job(studio)
    a = runner(db, A)
    claimed = a.claim()["job"]
    at(db, seconds=31)
    if reclaimed:
        assert runner(db, B).claim()["status"] == "claimed"
    with pytest.raises(WorkerError) as e:
        a._fail(a._c(T1), claimed, "render_failed")
    assert e.value.code == "lease_lost"
    res = db.rpc("marketing_fail_generation", {"p_tenant": T1, "p_job": j["id"], "p_worker": A,
                                               "p_error_code": "cancelled_by_user", "p_usage": []})
    assert res["status"] == "rejected" and job(db, j["id"])["status"] == "processing"
    with pytest.raises(Exception):                               # actualización directa processing → terminal
        db.update("marketing_generation_jobs", {"id": f"eq.{j['id']}"}, {"status": "cancelled"})
    assert job(db, j["id"])["status"] == "processing"


def test_timeout_path_only_with_expired_lease(studio, db):
    j = approved_job(studio)
    runner(db, A).claim()
    at(db, seconds=10)
    db.rpc("marketing_timeout_jobs", {"p_max_attempts": 1})
    assert job(db, j["id"])["status"] == "processing"            # lease vigente: el timeout no lo toca
    at(db, seconds=31)
    assert db.rpc("marketing_timeout_jobs", {"p_max_attempts": 1}) == 1
    assert job(db, j["id"])["status"] in ("timed_out", "failed")


# ------------------------------------------------------------------ 4. purgador: pérdida en cada punto
def media_with_derivative(db):
    lib = LibraryService(db, now=NOW)
    m = up(lib, name="e.png", data=png(w=61))
    path = f"{T1}/derivatives/{m['id']}/thumb.png"
    db.storage[("marketing-assets", path)] = (b"x", "image/png")
    db.tables["marketing_media_derivatives"].append({"id": "dx", "tenant_id": T1, "media_id": m["id"], "status": "ready",
                                                     "storage_path": path, "expires_at": (NOW + timedelta(days=90)).isoformat()})
    return m, path


def media(db, mid):
    return next(x for x in db.tables["marketing_media"] if x["id"] == mid)


@pytest.mark.parametrize("point", ["purge:start", "purge:after_pending", "purge:after_original", "purge:before_derivative",
                                   "purge:before_close", "batch:before_timeouts", "batch:before_expire"])
def test_retention_lease_lost_at_each_checkpoint(db, monkeypatch, point):
    m, deriv = media_with_derivative(db)
    original = media(db, m["id"])["storage_path"]
    at(db, days=40)
    a = RetentionRunner(db, now=NOW + timedelta(days=40), holder=A, renew_seconds=0.005)
    real = a._revalidate

    def steal_at(p):
        if p == point:                                            # B toma el lease justo aquí
            db.tables["marketing_runtime_leases"][0].update({"holder": B, "expires_at": (NOW + timedelta(days=41)).isoformat()})
        return real(p)
    monkeypatch.setattr(a, "_revalidate", steal_at)
    db.rpc_calls.clear()
    out = a.run()
    assert out["lease_lost"] is True and out["stopped_at"] == point
    assert db.tables["marketing_runtime_leases"][0]["holder"] == B      # el lease nuevo no se libera
    assert keeper_threads() == []
    purged = [e for e in db.tables["marketing_media_events"] if e["action"] == "purged"]
    status = media(db, m["id"])["retention_status"]
    if point == "purge:start":
        assert status not in ("purge_pending", "purged") and ("marketing-assets", original) in db.storage
    elif point == "purge:after_pending":
        assert status == "purge_pending" and ("marketing-assets", original) in db.storage
    elif point == "purge:after_original":
        assert status == "purge_pending" and ("marketing-assets", original) not in db.storage
        assert ("marketing-assets", deriv) in db.storage                  # el derivado no se tocó
    elif point == "purge:before_close":
        assert status == "purge_pending" and ("marketing-assets", deriv) not in db.storage   # borrado, sin cerrar
    if point.startswith("purge:"):
        assert purged == [] and "marketing_close_purge" not in db.rpc_calls
    if point == "batch:before_timeouts":
        assert "marketing_timeout_jobs" not in db.rpc_calls
    if point == "batch:before_expire":
        assert "marketing_timeout_jobs" in db.rpc_calls and "marketing_expire_storage_reservations" not in db.rpc_calls
    # B (el purgador vigente) retoma y termina: un único cierre y un único evento por archivo.
    out_b = RetentionRunner(db, now=NOW + timedelta(days=40), holder=B, renew_seconds=0.005).run()
    assert "lease_lost" not in out_b and media(db, m["id"])["retention_status"] == "purged"
    assert ("marketing-assets", deriv) not in db.storage
    assert len([e for e in db.tables["marketing_media_events"] if e["action"] == "purged"]) == 1


def test_two_purgers_in_parallel_close_once(db):
    lib = LibraryService(db, now=NOW)
    ms = [up(lib, name=f"p{i}.png", data=png(w=70 + i)) for i in range(4)]
    at(db, days=40)
    outs = []
    runners = [RetentionRunner(db, now=NOW + timedelta(days=40), holder=h, renew_seconds=0.005) for h in (A, B)]
    ts = [threading.Thread(target=lambda r=r: outs.append(r.run())) for r in runners]
    [t.start() for t in ts]
    [t.join(5) for t in ts]
    assert len(outs) == 2 and sum(o.get("purged", 0) for o in outs) == 4    # uno purga; el otro espera o no halla nada
    events = [e for e in db.tables["marketing_media_events"] if e["action"] == "purged"]
    assert sorted(e["media_id"] for e in events) == sorted(m["id"] for m in ms)
    assert keeper_threads() == [] and db.tables["marketing_runtime_leases"] == []


def test_close_purge_requires_live_lease_or_owner(db):
    m, _ = media_with_derivative(db)
    media(db, m["id"]).update({"retention_status": "purge_pending"})
    args = {"p_tenant": T1, "p_media": m["id"], "p_reason": "expired", "p_actor": None, "p_role": "system"}
    assert db.rpc("marketing_close_purge", {**args, "p_holder": None})["status"] == "rejected"
    assert db.rpc("marketing_close_purge", {**args, "p_holder": A})["status"] == "lease_lost"
    assert media(db, m["id"])["retention_status"] == "purge_pending"


# ------------------------------------------------------------------ detalles del keeper, del worker y del cierre
def test_cancel_stops_the_keeper_at_once_even_with_a_long_interval():
    cancel, calls = threading.Event(), []
    with LeaseKeeper(lambda t: calls.append(t) or True, 5.0, name="long", cancel=cancel) as k:
        t0 = time.monotonic()
        cancel.set()
        assert wait_for(lambda: keeper_threads() == [], timeout=1)  # no espera al final del intervalo
        assert time.monotonic() - t0 < 1 and k.cancelled and calls == []


def test_cancel_set_before_start_means_no_renewal_at_all():
    cancel, calls = threading.Event(), []
    cancel.set()
    with LeaseKeeper(lambda t: calls.append(t) or True, 1e-9, name="pre", cancel=cancel) as k:
        assert wait_for(lambda: keeper_threads() == [], timeout=1)
    assert calls == [] and k.cancelled


def test_late_renewal_counts_as_lost():
    with LeaseKeeper(lambda t: time.sleep(t + 0.05) or True, 0.01, name="late", renew_timeout=0.05) as k:
        assert wait_for(lambda: k.lost)                          # llegó después de su timeout: no vale
    assert keeper_threads() == []


def test_exit_is_bounded_even_if_renewal_hangs():
    started, release = threading.Event(), threading.Event()

    def hang(t):
        started.set()
        release.wait(5)
        return True
    k = LeaseKeeper(hang, 0.01, name="hang", renew_timeout=0.05)
    t0 = time.monotonic()
    with k:
        assert started.wait(2)
    assert time.monotonic() - t0 < 2 and k.joined is False       # no bloquea indefinidamente
    release.set()
    assert wait_for(lambda: keeper_threads() == [])


def test_finalize_response_lost_after_commit_deletes_nothing(studio, db):
    """La base confirmó, pero la respuesta se perdió: el worker no borra temporales (ya están publicados)."""
    j = approved_job(studio)
    longp = Long(db, steps=0, step_seconds=0, temps=True)
    longp.job_id = j["id"]
    real = db._rpc_marketing_finalize_generation

    def committed_then_lost(a):
        real(a)
        raise RuntimeError("timeout de red")
    db._rpc_marketing_finalize_generation = committed_then_lost
    assert runner(db, A, {**ADAPTERS(), "mock": longp}).run_pending(limit=1) == [{"id": j["id"], "error": "finalize_unknown"}]
    assert job(db, j["id"])["status"] == "succeeded"
    paths = [o["storage_path"] for o in rows(db, "marketing_generation_outputs", j["id"]) if o.get("storage_path")]
    assert paths and all(("marketing-assets", p) in db.storage for p in paths)


def test_close_rejected_for_lost_lease_stops_the_runner(db, monkeypatch):
    m, _ = media_with_derivative(db)
    at(db, days=40)
    real = db._rpc_marketing_close_purge

    def steal_then_close(a):                                     # B lo toma entre la revalidación y el cierre
        db.tables["marketing_runtime_leases"][0].update({"holder": B, "expires_at": (NOW + timedelta(days=41)).isoformat()})
        return real(a)
    monkeypatch.setattr(db, "_rpc_marketing_close_purge", steal_then_close)
    out = RetentionRunner(db, now=NOW + timedelta(days=40), holder=A, renew_seconds=0.005).run()
    assert out["lease_lost"] is True and out["stopped_at"] == "purge:close" and out["purged"] == 0
    assert media(db, m["id"])["retention_status"] == "purge_pending"
    assert db.tables["marketing_runtime_leases"][0]["holder"] == B


def test_lease_lost_before_the_first_send_spends_nothing(studio, db, monkeypatch):
    """Sin lease no se gasta: el heartbeat síncrono antes de cada envío lo detecta aunque el keeper aún no."""
    j = approved_job(studio)
    real = db.select

    def select(table, params):
        if table == "marketing_settings" and params.get("select") == "ai_generation_enabled,monthly_ai_cost_limit":
            at(db, seconds=31)                                    # el lease vence entre el reclamo y el envío
        return real(table, params)
    monkeypatch.setattr(db, "select", select)
    p = MockProvider()
    a = runner(db, A, {**ADAPTERS(), "mock": p}, hb=10)           # el keeper no renueva a tiempo
    assert a.run_pending(limit=1) == [{"id": j["id"], "error": "lease_lost"}]
    assert p.submissions == 0 and rows(db, "marketing_model_usage", j["id"]) == []
