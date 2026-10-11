"""
AITA Marketing (Fase 2): concurrencia REAL en PostgreSQL (no en el doble de pruebas).

Crea un clúster PostgreSQL desechable en un directorio temporal (initdb), lo arranca solo en
127.0.0.1 con un puerto libre, carga tests/sql/bootstrap_local.sql + las migraciones de Marketing,
ejecuta las pruebas y lo detiene y borra. Nunca usa Supabase ni producción.

Comprueba con conexiones y transacciones separadas, abiertas a la vez:
  * dos subidas simultáneas cerca del límite → solo una reserva entra;
  * dos aprobaciones simultáneas cerca del límite de bytes → solo una;
  * dos aprobaciones simultáneas cerca del límite de costo → solo una;
  * una subida y una aprobación simultáneas compitiendo por el mismo espacio → solo una;
  * dos workers reclamando el mismo trabajo (lease + FOR UPDATE SKIP LOCKED) → solo uno;
  * dos RetentionRunner tomando el bloqueo del purgador a la vez → solo uno;
  * heartbeat continuo: una subtarea de más de 2 × lease conserva la propiedad frente a otro worker, y
    un worker que pierde el heartbeat no puede escribir resultados, costos ni tocar reservas.

Se omite si no hay binarios de PostgreSQL. Comando exacto (ver docs/AITA_MARKETING_PHASE2.md, §4f):
  python3 -m venv /tmp/mk-pg-venv && /tmp/mk-pg-venv/bin/pip install pytest "psycopg[binary]==3.2.3"
  MARKETING_PG_BIN=/opt/homebrew/opt/postgresql@17/bin /tmp/mk-pg-venv/bin/python -m pytest -v -p no:cacheprovider \
      --noconftest tests/test_marketing_pg_concurrency.py
"""
import os
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")
PG_BIN = os.getenv("MARKETING_PG_BIN", "")
if not PG_BIN or not (Path(PG_BIN) / "initdb").exists():
    pytest.skip("MARKETING_PG_BIN no apunta a binarios de PostgreSQL", allow_module_level=True)

ROOT = Path(__file__).resolve().parents[1]
MIGS = ("20261009120000_aita_marketing.sql", "20261011120000_marketing_reel_studio.sql",
        "20261011130000_marketing_library_retention.sql", "20261011140000_marketing_generation_budget.sql",
        "20261012120000_marketing_runtime_safety.sql")
OWNER = "00000000-0000-0000-0000-00000000000a"
TA = "10000000-0000-0000-0000-00000000000a"
HOLD = 0.4                       # cada transacción retiene su bloqueo este tiempo antes de confirmar
ROUNDS = 5


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def pg(tmp_path_factory):
    base = tmp_path_factory.mktemp("marketing_pg")
    data, port = base / "data", free_port()
    env = {**os.environ, "LC_ALL": "C", "LANG": "C"}               # macOS: el postmaster exige un locale válido
    run = lambda *a: subprocess.run([str(Path(PG_BIN) / a[0]), *a[1:]], check=True, capture_output=True, text=True, env=env)
    run("initdb", "-D", str(data), "-U", "postgres", "--auth=trust", "-E", "UTF8", "--no-locale")
    run("pg_ctl", "-D", str(data), "-l", str(base / "pg.log"), "-w", "start",
        "-o", f"-p {port} -c listen_addresses=127.0.0.1 -c unix_socket_directories=''")
    dsn = f"host=127.0.0.1 port={port} user=postgres dbname=postgres"
    try:
        with psycopg.connect(dsn, autocommit=True) as c:
            c.execute((ROOT / "tests/sql/bootstrap_local.sql").read_text())
            c.execute(f"""insert into auth.users (id) values ('{OWNER}');
                insert into tenants (id, slug, name) values ('{TA}','ta','A');
                insert into tenant_users (tenant_id, user_id, role) values ('{TA}','{OWNER}','owner');""")
            for m in MIGS:
                c.execute((ROOT / "supabase/migrations" / m).read_text())
            c.execute(f"""update marketing_settings set ai_generation_enabled = true, monthly_ai_cost_limit = 20,
                library_storage_limit_bytes = 100 where tenant_id = '{TA}'""")
            assert c.execute("select version()").fetchone()[0].startswith("PostgreSQL")
        yield dsn
    finally:
        subprocess.run([str(Path(PG_BIN) / "pg_ctl"), "-D", str(data), "-m", "fast", "-w", "stop"], capture_output=True, env=env)
        shutil.rmtree(base, ignore_errors=True)


def reset(dsn, storage_limit=100, cost_limit=20):
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute("update marketing_storage_reservations set status = 'released' where status = 'reserved'")
        c.execute("update marketing_generation_jobs set status = 'cancelled' where status in ('queued','processing')")
        c.execute("delete from marketing_media")
        c.execute("delete from marketing_runtime_leases")
        lim = "null" if storage_limit is None else int(storage_limit)
        c.execute(f"""update marketing_settings set library_storage_limit_bytes = {lim},
            monthly_ai_cost_limit = {cost_limit} where tenant_id = '{TA}'""")


_n = [0]


def awaiting_job(dsn, cost):
    _n[0] += 1
    with psycopg.connect(dsn, autocommit=True) as c:
        jid = c.execute(f"""insert into marketing_generation_jobs (tenant_id, created_by, task_type, real_media_percent,
            ai_media_percent, maximum_cost, idempotency_key, status) values ('{TA}','{OWNER}','reel',0,100,50,
            'job-pg-concurrency-{_n[0]:05d}','draft') returning id""").fetchone()[0]
        c.execute(f"update marketing_generation_jobs set status = 'awaiting_generation_approval', estimated_cost = {cost} "
                  f"where id = '{jid}'")
    return jid


def race(dsn, calls, must_wait=True):
    """Ejecuta cada SQL en su propia conexión y transacción, a la vez; cada una retiene su bloqueo HOLD s."""
    barrier, out = threading.Barrier(len(calls)), [None] * len(calls)

    def go(i, sql):
        with psycopg.connect(dsn) as c:                                 # transacción explícita (no autocommit)
            barrier.wait()
            t0 = time.monotonic()
            res = c.execute(sql).fetchone()[0]
            t1 = time.monotonic()
            time.sleep(HOLD)
            c.commit()
            out[i] = (res, t0, t1)
    ts = [threading.Thread(target=go, args=(i, s)) for i, s in enumerate(calls)]
    [t.start() for t in ts]
    [t.join(30) for t in ts]
    results = [o[0] for o in out]
    waited = max(o[2] for o in out) - min(o[1] for o in out)
    if must_wait:
        assert waited >= HOLD * 0.8, "las transacciones no llegaron a competir"   # una esperó el bloqueo de la otra
    return results


def used(dsn):
    with psycopg.connect(dsn, autocommit=True) as c:
        return c.execute(f"select public.marketing_storage_used('{TA}')").fetchone()[0]


@pytest.mark.parametrize("rnd", range(ROUNDS))
def test_two_simultaneous_uploads_near_limit(pg, rnd):
    reset(pg, storage_limit=100)
    sql = "select public.marketing_reserve_storage('%s','upload:pg-%d-%s','upload',60)"
    res = race(pg, [sql % (TA, rnd, "a"), sql % (TA, rnd, "b")])
    assert sorted(r["status"] for r in res) == ["rejected", "reserved"]
    assert next(r for r in res if r["status"] == "rejected")["reason"] == "limit_library_storage"
    assert used(pg) == 60 <= 100


@pytest.mark.parametrize("rnd", range(ROUNDS))
def test_two_simultaneous_approvals_near_storage_limit(pg, rnd):
    reset(pg, storage_limit=100)
    a, b = awaiting_job(pg, 1), awaiting_job(pg, 1)
    sql = f"select public.marketing_approve_generation('{TA}','%s','{OWNER}', 1, 60)"
    res = race(pg, [sql % a, sql % b])
    assert sorted(r["status"] for r in res) == ["approved", "rejected"]
    assert next(r for r in res if r["status"] == "rejected")["reason"] == "limit_library_storage"
    assert used(pg) == 60


@pytest.mark.parametrize("rnd", range(ROUNDS))
def test_two_simultaneous_approvals_near_cost_limit(pg, rnd):
    reset(pg, storage_limit=None, cost_limit=20)
    with psycopg.connect(pg, autocommit=True) as c:                      # el mes ya tiene gasto: quedan 20
        spent = c.execute(f"""select coalesce(sum(case when status in ('queued','processing') then reserved_cost
            else coalesce(actual_cost, 0) end), 0) from marketing_generation_jobs where tenant_id = '{TA}'""").fetchone()[0]
        c.execute(f"update marketing_settings set monthly_ai_cost_limit = {float(spent) + 20} where tenant_id = '{TA}'")
    a, b = awaiting_job(pg, 15), awaiting_job(pg, 15)
    sql = f"select public.marketing_approve_generation('{TA}','%s','{OWNER}', 15, 0)"
    res = race(pg, [sql % a, sql % b])
    assert sorted(r["status"] for r in res) == ["approved", "rejected"]
    assert next(r for r in res if r["status"] == "rejected")["reason"] == "budget_exceeded"


def test_upload_and_approval_compete_for_same_space(pg):
    reset(pg, storage_limit=100)
    j = awaiting_job(pg, 1)
    res = race(pg, [f"select public.marketing_reserve_storage('{TA}','upload:pg-mixed','upload',60)",
                    f"select public.marketing_approve_generation('{TA}','{j}','{OWNER}', 1, 60)"])
    assert sorted(r["status"] for r in res) in (["approved", "rejected"], ["rejected", "reserved"])
    assert used(pg) == 60


def test_expired_reservation_is_closed_once_under_concurrency(pg):
    reset(pg, storage_limit=100)
    with psycopg.connect(pg, autocommit=True) as c:
        c.execute(f"select public.marketing_reserve_storage('{TA}','upload:pg-expire','upload',40)")
        c.execute("update marketing_storage_reservations set expires_at = now() - interval '1 second' "
                  "where reservation_key = 'upload:pg-expire'")
    res = race(pg, ["select public.marketing_expire_storage_reservations()",
                    f"select public.marketing_release_storage('{TA}','upload:pg-expire', false)"], must_wait=False)
    with psycopg.connect(pg, autocommit=True) as c:
        st = c.execute("select status from marketing_storage_reservations where reservation_key = 'upload:pg-expire'").fetchone()[0]
    assert st == "expired" and res[0] == 1 and res[1] is None              # cerrada una vez; la liberación no hace nada
    assert used(pg) == 0


W1, W2 = "30000000-0000-4000-8000-000000000001", "30000000-0000-4000-8000-000000000002"


@pytest.mark.parametrize("rnd", range(ROUNDS))
def test_two_workers_claim_the_same_job(pg, rnd):
    reset(pg, storage_limit=None)
    j = awaiting_job(pg, 1)
    with psycopg.connect(pg, autocommit=True) as c:
        assert c.execute(f"select public.marketing_approve_generation('{TA}','{j}','{OWNER}', 1, 0)").fetchone()[0]["status"] == "approved"
    sql = "select public.marketing_claim_job('%s', 60)"
    res = race(pg, [sql % W1, sql % W2], must_wait=False)              # SKIP LOCKED: el segundo no espera
    assert sorted(r["status"] for r in res) == ["claimed", "empty"]
    with psycopg.connect(pg, autocommit=True) as c:
        owner, attempts = c.execute(f"select lease_owner::text, attempts from marketing_generation_jobs where id = '{j}'").fetchone()
    assert owner in (W1, W2) and attempts == 1


@pytest.mark.parametrize("rnd", range(ROUNDS))
def test_two_retention_runners_take_the_lock(pg, rnd):
    reset(pg)
    sql = "select coalesce(public.marketing_acquire_runtime_lease('retention_runner', '%s', 60), false)"
    res = race(pg, [sql % W1, sql % W2])
    assert sorted(res) == [False, True]


# ------------------------------------------------------------------ heartbeat continuo con dos workers reales
LEASE = 30                       # el mínimo que acepta la base; la subtarea dura más de 2 × LEASE


def _approved_with_storage(pg):
    reset(pg, storage_limit=1000)
    j = awaiting_job(pg, 1)
    with psycopg.connect(pg, autocommit=True) as c:
        assert c.execute(f"select public.marketing_approve_generation('{TA}','{j}','{OWNER}', 1, 10)").fetchone()[0]["status"] == "approved"
    return j


def _write_result(c, j, worker):
    c.execute(f"""insert into marketing_generation_outputs (tenant_id, job_id, kind, worker_id)
                  values ('{TA}','{j}','render','{worker}')""")
    c.execute(f"""insert into marketing_model_usage (tenant_id, job_id, provider, model_id, task_type, catalog_version,
                  billing_unit, units, estimated_cost, actual_cost, idempotency_key, status, worker_id)
                  values ('{TA}','{j}','mock','m','reel','v1','request',1,0,0,'usage-{j}-subtask-0','not_charged','{worker}')""")


def test_heartbeat_keeps_long_subtask_against_second_worker(pg):
    from services.marketing_lease import LeaseKeeper, validate
    j = _approved_with_storage(pg)
    with psycopg.connect(pg, autocommit=True) as a, psycopg.connect(pg, autocommit=True) as b:
        assert a.execute(f"select public.marketing_claim_job('{W1}', {LEASE})").fetchone()[0]["status"] == "claimed"
        renew = lambda: a.execute(f"select public.marketing_job_heartbeat('{j}','{W1}', {LEASE})").fetchone()[0]  # noqa: E731
        rival, t0 = [], time.monotonic()
        with LeaseKeeper(renew, validate(LEASE), name="pg-a") as keeper:
            while time.monotonic() - t0 < 2 * LEASE + 5:             # la subtarea dura > 2 × lease
                rival.append(b.execute(f"select public.marketing_claim_job('{W2}', {LEASE})").fetchone()[0]["status"])
                time.sleep(3)
            assert not keeper.lost and keeper.renewals >= 5
            _write_result(a, j, W1)                                   # sigue siendo el dueño
            assert a.execute(f"""update marketing_generation_jobs set status = 'succeeded', actual_cost = 0, completed_at = now()
                                 where id = '{j}' and lease_owner = '{W1}' and status = 'processing'""").rowcount == 1
        assert rival and set(rival) == {"empty"}                     # B nunca pudo reclamarlo
        counts = a.execute(f"""select (select count(*) from marketing_generation_outputs where job_id = '{j}'),
                                      (select count(*) from marketing_model_usage where job_id = '{j}'),
                                      (select attempts from marketing_generation_jobs where id = '{j}'),
                                      (select status from marketing_storage_reservations where reservation_key = 'job:{j}')""").fetchone()
        assert counts == (1, 1, 1, "consumed")                        # un resultado, un costo, un intento
    assert not [t for t in threading.enumerate() if t.name.startswith("lease-keeper")]


def test_lost_heartbeat_second_worker_takes_over_and_first_cannot_write(pg):
    j = _approved_with_storage(pg)
    with psycopg.connect(pg, autocommit=True) as a, psycopg.connect(pg, autocommit=True) as b:
        assert a.execute(f"select public.marketing_claim_job('{W1}', {LEASE})").fetchone()[0]["status"] == "claimed"
        # A pierde el heartbeat (no renueva). Esperar al vencimiento según la hora de PostgreSQL.
        deadline = time.monotonic() + LEASE + 20
        while not b.execute(f"select lease_expires_at <= now() from marketing_generation_jobs where id = '{j}'").fetchone()[0]:
            assert time.monotonic() < deadline
            time.sleep(1)
        got = b.execute(f"select public.marketing_claim_job('{W2}', {LEASE})").fetchone()[0]
        assert got["status"] == "claimed" and got["retry"] is True and got["job"]["attempts"] == 2
        assert a.execute(f"select public.marketing_job_heartbeat('{j}','{W1}', {LEASE})").fetchone()[0] is None
        for sql in (f"insert into marketing_generation_outputs (tenant_id, job_id, kind, worker_id) values ('{TA}','{j}','render','{W1}')",
                    f"""insert into marketing_model_usage (tenant_id, job_id, provider, model_id, task_type, catalog_version,
                        billing_unit, units, estimated_cost, actual_cost, idempotency_key, status, worker_id)
                        values ('{TA}','{j}','mock','m','reel','v1','request',1,0,0,'usage-a-late-0000','not_charged','{W1}')"""):
            with pytest.raises(psycopg.errors.CheckViolation):
                a.execute(sql)                                       # A no escribe resultados ni costos
        assert a.execute(f"select public.marketing_confirm_output_storage('{TA}','{j}', 5, '{W1}')").fetchone()[0]["reason"] == "lease_lost"
        assert a.execute(f"select public.marketing_release_storage('{TA}','job:{j}', false)").fetchone()[0] is None
        assert a.execute(f"""update marketing_generation_jobs set status = 'failed', error_code = 'render_failed', completed_at = now()
                             where id = '{j}' and lease_owner = '{W1}' and status = 'processing'""").rowcount == 0
        assert b.execute(f"select status from marketing_storage_reservations where reservation_key = 'job:{j}'").fetchone()[0] == "reserved"
        _write_result(b, j, W2)                                       # B termina una sola vez
        assert b.execute(f"""update marketing_generation_jobs set status = 'succeeded', actual_cost = 0, completed_at = now()
                             where id = '{j}' and lease_owner = '{W2}' and status = 'processing'""").rowcount == 1
        assert b.execute(f"""update marketing_generation_jobs set status = 'succeeded'
                             where id = '{j}' and lease_owner = '{W2}' and status = 'processing'""").rowcount == 0
        final = b.execute(f"""select (select status from marketing_generation_jobs where id = '{j}'),
                                     (select count(*) from marketing_generation_outputs where job_id = '{j}'),
                                     (select count(*) from marketing_model_usage where job_id = '{j}'),
                                     (select status from marketing_storage_reservations where reservation_key = 'job:{j}')""").fetchone()
        assert final == ("succeeded", 1, 1, "consumed")
