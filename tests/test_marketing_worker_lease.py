"""
AITA Marketing (Fase 2): worker con lease. Reclamo atómico (dos workers nunca toman el mismo trabajo),
heartbeat antes de cada envío, lease perdido = se detiene sin escribir nada, reintento con la MISMA
idempotency_key (nada se cobra dos veces), timeout que libera reservas una sola vez y bloquea resultados,
y presupuesto 0 o generación cerrada = falla cerrado. Sin proveedores reales ni red.
"""
import threading
from datetime import timedelta

import pytest

from services import marketing_storage as ms
from services.marketing_ai_router import MarketingAIRouter, MockProvider, OmniRouteAdapter, DisabledLocalTools
from services.marketing_retention import RetentionRunner
from services.marketing_studio import StudioService
from services.marketing_worker import JobRunner, WorkerError, MAX_ATTEMPTS
from test_marketing import NOW, T1, OWNER
from test_marketing_studio import StudioDB, jwt, approved_job

ADAPTERS = lambda: {"mock": MockProvider(), "omniroute": OmniRouteAdapter(env={}), "local_ffmpeg": DisabledLocalTools()}  # noqa: E731


@pytest.fixture
def db():
    return StudioDB()


@pytest.fixture
def studio(db):
    return StudioService(db, now=NOW, router=MarketingAIRouter(env={}), adapters=ADAPTERS())


def runner(db, wid=None, adapters=None):
    return JobRunner(db, MarketingAIRouter(env={}), adapters or ADAPTERS(), now=NOW, worker_id=wid)


def job(db, jid):
    return next(r for r in db.tables["marketing_generation_jobs"] if r["id"] == jid)


def at(db, **kw):
    db.rpc_now = (NOW + timedelta(**kw)).isoformat()


# ------------------------------------------------------------------ reclamo
def test_two_workers_never_claim_the_same_job(studio, db):
    j = approved_job(studio)
    db.rpc_delay = 0.02
    got = []

    def go(w):
        got.append((w, runner(db, w).claim()))
    ts = [threading.Thread(target=go, args=(f"00000000-0000-4000-8000-00000000000{i}",)) for i in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    claimed = [(w, r) for w, r in got if r["status"] == "claimed"]
    assert len(claimed) == 1 and claimed[0][1]["job"]["id"] == j["id"]
    assert sorted(r["status"] for _, r in got) == ["claimed", "empty", "empty", "empty"]
    assert job(db, j["id"])["lease_owner"] == claimed[0][0] and job(db, j["id"])["attempts"] == 1


def test_run_pending_processes_each_job_once(studio, db):
    ids = [approved_job(studio)["id"] for _ in range(3)]
    out = runner(db).run_pending(limit=10)
    assert sorted(o["id"] for o in out) == sorted(ids) and all(o["status"] == "succeeded" for o in out)
    assert runner(db).run_pending(limit=10) == []                       # nada más que reclamar


def test_unapproved_or_unreserved_jobs_are_never_claimed(studio, db):
    j = approved_job(studio)
    job(db, j["id"])["reserved_cost"] = None
    assert runner(db).claim()["status"] == "empty"


# ------------------------------------------------------------------ heartbeat, lease perdido y reintento
class Slow(MockProvider):
    """El reloj de la base avanza durante el envío: el lease vence antes del siguiente heartbeat."""
    def __init__(self, db, seconds):
        super().__init__()
        self.db, self.seconds = db, seconds

    def submit(self, req, model):
        res = super().submit(req, model)
        at(self.db, seconds=self.seconds)
        return res


def test_lost_lease_stops_without_outputs_and_retry_keeps_idempotency(studio, db):
    j = approved_job(studio)
    first = runner(db, "00000000-0000-4000-8000-0000000000a1", {**ADAPTERS(), "mock": Slow(db, 500)})
    assert first.run_pending(limit=1) == [{"id": j["id"], "error": "lease_lost"}]
    usage_after_first = [u["idempotency_key"] for u in db.tables["marketing_model_usage"]]
    assert job(db, j["id"])["status"] == "processing"
    assert [o for o in db.tables["marketing_generation_outputs"] if o["job_id"] == j["id"]] == []   # nada utilizable
    # otro worker reintenta tras vencer el lease: misma idempotency_key, sin cobros repetidos
    second = runner(db, "00000000-0000-4000-8000-0000000000b2")
    out = second.run_pending(limit=1)
    assert out[0]["status"] == "succeeded" and job(db, j["id"])["attempts"] == 2
    keys = [u["idempotency_key"] for u in db.tables["marketing_model_usage"]]
    assert len(keys) == len(set(keys)) and set(usage_after_first) <= set(keys)
    assert all(k.startswith(job(db, j["id"])["idempotency_key"]) for k in keys)


def test_heartbeat_extends_only_own_live_lease(studio, db):
    j = approved_job(studio)
    w = runner(db, "00000000-0000-4000-8000-0000000000c1")
    w.claim()
    w._heartbeat(job(db, j["id"]))                                      # propio y vigente
    with pytest.raises(WorkerError):
        runner(db, "00000000-0000-4000-8000-0000000000c2")._heartbeat(job(db, j["id"]))   # ajeno
    at(db, seconds=1000)
    with pytest.raises(WorkerError):
        w._heartbeat(job(db, j["id"]))                                  # vencido


def test_success_requires_live_lease_and_outputs_require_processing(studio, db):
    j = approved_job(studio)
    w = runner(db, "00000000-0000-4000-8000-0000000000d1")
    w.claim()
    at(db, seconds=1000)
    with pytest.raises(RuntimeError):                                   # guardián de la base
        db.update("marketing_generation_jobs", {"id": f"eq.{j['id']}"}, {"status": "succeeded"})
    with pytest.raises(RuntimeError):
        db.insert("marketing_generation_outputs", {"tenant_id": T1, "job_id": j["id"], "kind": "script"})


# ------------------------------------------------------------------ timeout y liberación única
def test_exhausted_retries_time_out_once_and_release_everything(studio, db):
    j = approved_job(studio)
    key = f"job:{j['id']}"
    assert next(r for r in db.tables["marketing_storage_reservations"] if r["reservation_key"] == key)["status"] == "reserved"
    for n in range(MAX_ATTEMPTS):
        assert runner(db, f"00000000-0000-4000-8000-00000000000{n}").claim()["status"] == "claimed"
        at(db, seconds=200 * (n + 1))                                   # el worker muere: el lease vence
    assert runner(db).claim()["status"] == "empty"                      # sin más reintentos
    out = RetentionRunner(db, now=NOW).run()
    assert out["timed_out"] == 1
    r = job(db, j["id"])
    assert r["status"] == "failed" and r["error_code"] == "timeout" and r["lease_owner"] is None
    assert next(x for x in db.tables["marketing_storage_reservations"] if x["reservation_key"] == key)["status"] == "released"
    assert RetentionRunner(db, now=NOW).run()["timed_out"] == 0          # una sola vez
    assert ms.used_bytes(db, T1) == 0
    b = studio.overview(jwt(OWNER), T1)["budget"]
    assert b["reserved"] == 0 and b["consumed"] == 0                    # el costo reservado deja de contar


def test_queued_too_long_times_out_and_blocks_outputs(studio, db):
    j = approved_job(studio)
    db.tables["marketing_generation_outputs"].append({"id": "o1", "tenant_id": T1, "job_id": j["id"], "kind": "render",
                                                      "review_status": "generated", "metadata": {}})
    at(db, hours=25)
    assert RetentionRunner(db, now=NOW + timedelta(hours=25)).run()["timed_out"] == 1
    o = db.tables["marketing_generation_outputs"][0]
    assert o["review_status"] == "rejected" and o["metadata"]["blocked"] == "timeout"


# ------------------------------------------------------------------ presupuesto y entradas
@pytest.mark.parametrize("change", [{"monthly_ai_cost_limit": 0}, {"monthly_ai_cost_limit": None},
                                    {"monthly_ai_cost_limit": 500}, {"ai_generation_enabled": False}])
def test_budget_closed_after_approval_fails_without_spending(studio, db, change):
    j = approved_job(studio)
    db.tables["marketing_settings"][0].update(change)
    out = runner(db).run_pending(limit=1)
    assert out[0]["id"] == j["id"] and out[0]["status"] == "failed" and out[0]["error_code"] == "budget_exceeded"
    assert db.tables["marketing_model_usage"] == []


def test_claim_skips_job_whose_inputs_are_no_longer_allowed(studio, db):
    from services.marketing_library import LibraryService
    from test_marketing_studio import up, consented
    lib = LibraryService(db, now=NOW)
    m = up(lib)
    consented(lib, m)
    j = approved_job(studio, [m["id"]], 50, 50)
    db.tables["marketing_media"][0].update({"consent_status": "revoked", "people_policy": "exclude"})
    res = runner(db).claim()
    assert res["status"] == "skipped" and job(db, j["id"])["status"] == "failed"
    assert job(db, j["id"])["error_code"] == "media_not_ready"
    assert runner(db).claim()["status"] == "empty"                      # no bloquea la cola


def test_stale_worker_cannot_finish_or_fail_a_reclaimed_job(studio, db):
    j = approved_job(studio)
    a = runner(db, "00000000-0000-4000-8000-0000000000e1")
    claimed = a.claim()["job"]
    at(db, seconds=200)                                                 # A se queda colgado: su lease vence
    b = runner(db, "00000000-0000-4000-8000-0000000000e2")
    assert b.claim()["status"] == "claimed"                            # B lo retoma
    c = a._c(T1)
    with pytest.raises(WorkerError) as e:
        a._fail(c, claimed, "render_failed", 0.0)                       # A despierta e intenta cerrarlo
    assert e.value.code == "already_claimed"
    assert next(x for x in db.tables["marketing_storage_reservations"]
                if x["reservation_key"] == f"job:{j['id']}")["status"] == "reserved"     # A no libera nada ajeno
    r = job(db, j["id"])
    assert r["status"] == "processing" and r["lease_owner"] == "00000000-0000-4000-8000-0000000000e2"
    assert b.run_pending(limit=0) == [] and b._execute(job(db, j["id"]))["status"] == "succeeded"


class SlowLast(MockProvider):
    """El lease vence durante la ÚLTIMA subtarea: solo el heartbeat previo a los resultados lo detecta."""
    def __init__(self, db, total):
        super().__init__()
        self.db, self.total = db, total

    def submit(self, req, model):
        res = super().submit(req, model)
        if self.submissions == self.total:
            at(self.db, seconds=500)
        return res


def test_lease_lost_in_last_subtask_writes_no_outputs(studio, db):
    j = approved_job(studio)
    total = len(job(db, j["id"])["request_metadata"]["estimate"]["subtasks"])
    w = runner(db, "00000000-0000-4000-8000-0000000000f1", {**ADAPTERS(), "mock": SlowLast(db, total)})
    assert w.run_pending(limit=1) == [{"id": j["id"], "error": "lease_lost"}]
    assert [o for o in db.tables["marketing_generation_outputs"] if o["job_id"] == j["id"]] == []
    assert job(db, j["id"])["status"] == "processing"                  # lo retomará otro worker o el timeout
