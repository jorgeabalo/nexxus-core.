"""
AITA Marketing (Fase 2): heartbeat continuo. El lease se renueva en segundo plano (≤ lease/3) mientras dura
una subtarea larga; si se pierde (o llega SIGTERM) el worker no registra resultados ni costos, no toca
reservas y solo borra sus temporales. El purgador renueva su lease durante borrados lentos y, si lo pierde,
se detiene. Sin hilos huérfanos. La hora la decide la base (aquí, el reloj del doble: rpc_now).
"""
import os
import signal
import threading
import time
from datetime import timedelta

import pytest

from services import marketing_runtime as runtime
from services.marketing_ai_router import MarketingAIRouter, MockProvider, OmniRouteAdapter, DisabledLocalTools
from services.marketing_lease import LeaseKeeper, LeaseLost, validate
from services.marketing_library import LibraryService
from services.marketing_retention import RetentionRunner
from services.marketing_studio import StudioService
from services.marketing_worker import JobRunner
from test_marketing import NOW, T1
from test_marketing_studio import StudioDB, approved_job, up, png

A, B = "00000000-0000-4000-8000-0000000000aa", "00000000-0000-4000-8000-0000000000bb"
ADAPTERS = lambda: {"mock": MockProvider(), "omniroute": OmniRouteAdapter(env={}), "local_ffmpeg": DisabledLocalTools()}  # noqa: E731


@pytest.fixture
def db():
    return StudioDB()


@pytest.fixture
def studio(db):
    return StudioService(db, now=NOW, router=MarketingAIRouter(env={}), adapters=ADAPTERS())


def at(db, **kw):
    db.rpc_now = (NOW + timedelta(**kw)).isoformat()


def job(db, jid):
    return next(r for r in db.tables["marketing_generation_jobs"] if r["id"] == jid)


def wait_for(cond, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end and not cond():
        time.sleep(0.005)
    return cond()


def keeper_threads():
    return [t for t in threading.enumerate() if t.name.startswith("lease-keeper")]


# ------------------------------------------------------------------ LeaseKeeper y parámetros
@pytest.mark.parametrize("lease,hb", [(10, None), (29, None), (901, None), (120, 0), (120, -1), (120, 40.01),
                                      (120, 120), (120, 200)])
def test_unsafe_parameters_are_rejected(lease, hb):
    with pytest.raises(ValueError):
        validate(lease, hb)


@pytest.mark.parametrize("lease,hb,expected", [(120, None, 40.0), (30, None, 10.0), (120, 40, 40.0), (120, 5, 5.0)])
def test_heartbeat_is_at_most_a_third_of_the_lease(lease, hb, expected):
    assert validate(lease, hb) == expected and expected < lease


def test_keeper_renews_and_leaves_no_thread():
    calls = []
    with LeaseKeeper(lambda: calls.append(1) or True, 0.01, name="t") as k:
        assert wait_for(lambda: len(calls) >= 3)
        assert not k.lost and keeper_threads()
    assert keeper_threads() == [] and k.renewals >= 3


@pytest.mark.parametrize("renew", [lambda: False, lambda: None, lambda: (_ for _ in ()).throw(RuntimeError("red"))])
def test_failed_or_erroring_renewal_means_lost(renew):
    with LeaseKeeper(renew, 0.01, name="t") as k:
        assert wait_for(lambda: k.lost)
        with pytest.raises(LeaseLost):
            k.check()
    assert keeper_threads() == []


def test_stop_ends_the_thread_immediately():
    with LeaseKeeper(lambda: True, 30, name="t") as k:
        t0 = time.monotonic()
        k.stop()
    assert time.monotonic() - t0 < 1 and keeper_threads() == []


# ------------------------------------------------------------------ worker: subtarea larga
class Long(MockProvider):
    """Una subtarea que dura más de 2×lease (en hora de la base) mientras otro worker intenta reclamar."""
    def __init__(self, db, steps, step_seconds, rival=None, temps=False, fail_renew_after=None):
        super().__init__()
        self.db, self.steps, self.step, self.rival, self.temps = db, steps, step_seconds, rival, temps
        self.rival_claims, self.elapsed = [], 0
        self.fail_renew_after = fail_renew_after

    def submit(self, req, model):
        res = super().submit(req, model)
        for i in range(self.steps):
            if self.fail_renew_after is not None and i == self.fail_renew_after:
                self.db._rpc_marketing_job_heartbeat = lambda a: None          # el heartbeat empieza a fallar
            self.elapsed += self.step
            at(self.db, seconds=self.elapsed)
            time.sleep(0.03)                                                   # el keeper renueva mientras tanto
            if self.rival:
                self.rival_claims.append(self.rival.claim()["status"])
        if not self.temps:
            return res
        prefix = req.output_requirements["temp_prefix"]
        mine = f"{T1}/derivatives/{self.job_id}/{prefix}render.mp4"
        self.db.storage[("marketing-assets", mine)] = (b"x", "video/mp4")
        extra = [{"kind": "render", "temp_path": p, "byte_size": 1, "mime_type": "video/mp4"} for p in getattr(self, "extra", [])]
        return res.__class__(**{**res.__dict__, "output": {**res.output, "files": [
            {"kind": "render", "temp_path": mine, "byte_size": 1, "mime_type": "video/mp4"}, *extra]}})


def test_long_subtask_keeps_ownership_against_a_second_worker(studio, db):
    j = approved_job(studio)
    rival = JobRunner(db, MarketingAIRouter(env={}), ADAPTERS(), now=NOW, worker_id=B, lease_seconds=30)
    longp = Long(db, steps=6, step_seconds=20, rival=rival)                       # 120 s > 2 × 30 s
    a = JobRunner(db, MarketingAIRouter(env={}), {**ADAPTERS(), "mock": longp}, now=NOW, worker_id=A,
                  lease_seconds=30, heartbeat_seconds=0.005)
    out = a.run_pending(limit=1)
    assert out[0]["status"] == "succeeded" and job(db, j["id"])["lease_owner"] == A
    assert longp.rival_claims and set(longp.rival_claims) == {"empty"}             # B nunca pudo
    usage = [u for u in db.tables["marketing_model_usage"] if u["job_id"] == j["id"]]
    assert len({u["idempotency_key"] for u in usage}) == len(usage) and all(u["worker_id"] == A for u in usage)
    renders = [o for o in db.tables["marketing_generation_outputs"] if o["job_id"] == j["id"] and o["kind"] == "render"]
    assert len(renders) == 1                                                        # un solo resultado
    assert keeper_threads() == []


def test_lease_lost_during_subtask_writes_nothing_and_cleans_only_own_temps(studio, db):
    j = approved_job(studio)
    longp = Long(db, steps=4, step_seconds=20, temps=True, fail_renew_after=0)
    longp.job_id = j["id"]
    foreign = f"{T1}/derivatives/{j['id']}/{B}-render.mp4"                          # temporal de OTRO intento
    db.storage[("marketing-assets", foreign)] = (b"y", "video/mp4")
    longp.extra = [foreign]                                       # aunque el proveedor lo devuelva, no es de este intento
    a = JobRunner(db, MarketingAIRouter(env={}), {**ADAPTERS(), "mock": longp}, now=NOW, worker_id=A,
                  lease_seconds=30, heartbeat_seconds=0.005)
    assert a.run_pending(limit=1) == [{"id": j["id"], "error": "lease_lost"}]
    assert [u for u in db.tables["marketing_model_usage"] if u["job_id"] == j["id"]] == []    # ningún costo
    assert [o for o in db.tables["marketing_generation_outputs"] if o["job_id"] == j["id"]] == []
    assert next(r for r in db.tables["marketing_storage_reservations"]
                if r["reservation_key"] == f"job:{j['id']}")["status"] == "reserved"    # reservas intactas
    assert ("marketing-assets", foreign) in db.storage                                 # lo ajeno no se toca
    assert not [k for k in db.storage if k[1].startswith(f"{T1}/derivatives/{j['id']}/{A}-")]   # lo propio sí
    assert job(db, j["id"])["status"] == "processing" and keeper_threads() == []


def test_sigterm_stops_mid_subtask_without_writing(studio, db):
    ids = {approved_job(studio)["id"], approved_job(studio)["id"]}
    stop = threading.Event()

    class Interrupted(MockProvider):
        def submit(self, req, model):
            stop.set()                                                              # SIGTERM llega a mitad
            return super().submit(req, model)
    a = JobRunner(db, MarketingAIRouter(env={}), {**ADAPTERS(), "mock": Interrupted()}, now=NOW, worker_id=A,
                  lease_seconds=30, heartbeat_seconds=0.005)
    a.stop_event = stop
    out = a.run_pending(limit=5)
    assert len(out) == 1 and out[0]["error"] == "interrupted"                       # no reclama el segundo
    assert db.tables["marketing_model_usage"] == [] and db.tables["marketing_generation_outputs"] == []
    other = (ids - {out[0]["id"]}).pop()
    assert job(db, other)["status"] == "queued" and job(db, other).get("attempts", 0) == 0 and keeper_threads() == []


def test_sigterm_handler_sets_the_stop_event():
    stop = threading.Event()
    previous = signal.getsignal(signal.SIGTERM)
    try:
        runtime.install_sigterm(stop)
        os.kill(os.getpid(), signal.SIGTERM)
        assert wait_for(stop.is_set)
    finally:
        signal.signal(signal.SIGTERM, previous)


@pytest.mark.parametrize("argv", [["worker", "--lease-seconds", "10"], ["worker", "--lease-seconds", "120",
                                                                         "--heartbeat-seconds", "120"]])
def test_cli_rejects_unsafe_lease(db, argv, capsys):
    assert runtime.main(argv, db=db) == 2
    assert '"invalid_lease"' in capsys.readouterr().out


# ------------------------------------------------------------------ purgador
def expired(db, n):
    lib = LibraryService(db, now=NOW)
    return [up(lib, name=f"e{i}.png", data=png(w=50 + i)) for i in range(n)]


def test_retention_renews_during_slow_deletions(db, monkeypatch):
    ms = expired(db, 3)
    at(db, days=40)
    orig = db.storage_remove
    holders = []

    def slow(bucket, key):
        at(db, seconds=40 * 86400 + 600 * (len(holders) + 1))                     # cada borrado: +10 min (TTL 15)
        time.sleep(0.03)
        holders.append(db._rpc_marketing_acquire_runtime_lease({"p_name": "retention_runner", "p_holder": B,
                                                                "p_ttl_seconds": 900}))
        orig(bucket, key)
    monkeypatch.setattr(db, "storage_remove", slow)
    out = RetentionRunner(db, now=NOW + timedelta(days=40), holder=A, renew_seconds=0.005).run()
    assert out["purged"] == 3 and "lease_lost" not in out
    assert holders and set(holders) == {None}                                       # B nunca tomó el lease
    assert all(next(x for x in db.tables["marketing_media"] if x["id"] == m["id"])["retention_status"] == "purged"
               for m in ms)


def test_retention_stops_when_lease_lost_and_nothing_is_deleted_or_closed_twice(db, monkeypatch):
    ms = expired(db, 3)
    at(db, days=40)
    orig = db.storage_remove
    removed = []

    def steal_on_first(bucket, key):
        removed.append(key)
        orig(bucket, key)
        if len(removed) == 1:                                                       # B toma el lease tras vencer
            lease = db.tables["marketing_runtime_leases"][0]
            lease.update({"holder": B, "expires_at": (NOW + timedelta(days=41)).isoformat()})
            assert wait_for(lambda: a._keeper is not None and a._keeper.lost)
    monkeypatch.setattr(db, "storage_remove", steal_on_first)
    a = RetentionRunner(db, now=NOW + timedelta(days=40), holder=A, renew_seconds=0.005)
    out = a.run()
    assert out.get("lease_lost") is True and out["purged"] == 1 and len(removed) == 1   # se detuvo tras el primero
    assert db.tables["marketing_runtime_leases"][0]["holder"] == B                     # no liberó el lease ajeno
    db.tables["marketing_runtime_leases"].clear()
    out_b = RetentionRunner(db, now=NOW + timedelta(days=40), holder=B, renew_seconds=0.005).run()
    assert out_b["purged"] == 2
    assert sorted(removed) == sorted(set(removed)) and len(removed) == 3            # cada objeto, una sola vez
    purged_events = [e for e in db.tables["marketing_media_events"] if e["action"] == "purged"]
    assert sorted(e["media_id"] for e in purged_events) == sorted(m["id"] for m in ms)   # cerrado una vez cada uno
    assert keeper_threads() == []


def test_two_purgers_never_close_the_same_file_twice(db):
    m = expired(db, 1)[0]
    stale = dict(next(x for x in db.tables["marketing_media"] if x["id"] == m["id"]))   # ambos leyeron lo mismo
    first = RetentionRunner(db, now=NOW + timedelta(days=40)).purge(dict(stale), "expired")
    second = RetentionRunner(db, now=NOW + timedelta(days=40)).purge(dict(stale), "expired")
    assert first == "purged" and second == "skipped"
    assert len([e for e in db.tables["marketing_media_events"] if e["action"] == "purged"]) == 1


def test_background_renewal_failure_stops_even_if_lease_still_valid(studio, db):
    """Una renovación en segundo plano que falla (p. ej. error de red) = lease_lost, aunque la base aún lo dé por vigente."""
    j = approved_job(studio)
    real = db._rpc_marketing_job_heartbeat

    def flaky(a):
        if threading.current_thread().name.startswith("lease-keeper"):
            raise RuntimeError("red")
        return real(a)
    db._rpc_marketing_job_heartbeat = flaky
    a = JobRunner(db, MarketingAIRouter(env={}), {**ADAPTERS(), "mock": Long(db, steps=2, step_seconds=1)}, now=NOW,
                  worker_id=A, lease_seconds=30, heartbeat_seconds=0.005)
    assert a.run_pending(limit=1) == [{"id": j["id"], "error": "lease_lost"}]
    assert [u for u in db.tables["marketing_model_usage"] if u["job_id"] == j["id"]] == []
    assert keeper_threads() == []


def test_lease_lost_right_before_confirming_storage(studio, db):
    """El lease cambia de dueño entre el último heartbeat y la confirmación del tamaño: no se registra nada."""
    j = approved_job(studio)
    longp = Long(db, steps=0, step_seconds=0, temps=True)
    longp.job_id = j["id"]
    real = db._rpc_marketing_confirm_output_storage

    def steal_then_confirm(a):
        job(db, j["id"]).update({"lease_owner": B})                      # B lo retomó justo ahora
        return real(a)
    db._rpc_marketing_confirm_output_storage = steal_then_confirm
    a = JobRunner(db, MarketingAIRouter(env={}), {**ADAPTERS(), "mock": longp}, now=NOW, worker_id=A,
                  lease_seconds=30, heartbeat_seconds=0.005)
    assert a.run_pending(limit=1) == [{"id": j["id"], "error": "lease_lost"}]
    assert [o for o in db.tables["marketing_generation_outputs"] if o["job_id"] == j["id"]] == []
    assert not [k for k in db.storage if k[1].startswith(f"{T1}/derivatives/{j['id']}/{A}-")]   # temporal propio borrado
    assert next(r for r in db.tables["marketing_storage_reservations"]
                if r["reservation_key"] == f"job:{j['id']}")["status"] == "reserved"
