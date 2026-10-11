"""
AITA Marketing (Fase 2), PR #20: cierre seguro en PostgreSQL REAL (clúster desechable, nunca Supabase).
  * SIGTERM con la subtarea bloqueada más que el lease: las renovaciones paran al instante, el lease vence en
    la hora de PostgreSQL, B reclama y A no puede escribir nada. Sin hilos ni conexiones huérfanas.
  * Finalización atómica: un fallo tras el almacenamiento, tras el primer resultado o antes de succeeded no deja
    nada; B reintenta con la misma idempotency_key sin duplicar. Un lease que vence DURANTE la finalización no
    permite que otro reclame (la fila está bloqueada).
  * Transiciones terminales solo por los caminos explícitos; el timeout solo con el lease vencido.
  * Cierre de purga ligado al lease vigente del purgador.
Mismo comando que tests/test_marketing_pg_concurrency.py (MARKETING_PG_BIN, --noconftest).
"""
import json
import os
import signal
import threading
import time
import uuid

import pytest

from test_marketing_pg_concurrency import (LEASE, OWNER, TA, W1, W2, _approved_with_storage, fail, finalize, pg,
                                           psycopg, usage)

FIXTURES = (pg,)                                  # el clúster desechable de test_marketing_pg_concurrency

FAULTS = """
create or replace function public.test_finalize_fault() returns trigger language plpgsql as $$
begin
  if current_setting('test.fault', true) = tg_argv[0] then
    raise exception 'fallo inyectado: %', tg_argv[0];
  end if;
  return coalesce(new, old);
end $$;
drop trigger if exists t_after_storage on public.marketing_storage_reservations;
create trigger t_after_storage after update on public.marketing_storage_reservations
  for each row execute function public.test_finalize_fault('after_storage');
drop trigger if exists t_after_first_output on public.marketing_generation_outputs;
create trigger t_after_first_output after insert on public.marketing_generation_outputs
  for each row execute function public.test_finalize_fault('after_first_output');
drop trigger if exists t_before_succeeded on public.marketing_generation_jobs;
create trigger t_before_succeeded before update on public.marketing_generation_jobs
  for each row when (new.status = 'succeeded') execute function public.test_finalize_fault('before_succeeded');
"""


@pytest.fixture(scope="module")
def db(pg):
    with psycopg.connect(pg, autocommit=True) as c:
        c.execute(FAULTS)                                         # solo en el clúster de pruebas
    return pg


def one(c, sql, *args):
    return c.execute(sql, args or None).fetchone()[0]


def expire_lease(dsn, j):
    """Simula el paso del tiempo: el lease de A vence ya (en la hora de PostgreSQL)."""
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute("update marketing_generation_jobs set lease_expires_at = now() - interval '1 second' where id = %s", (j,))


def outputs_for(j, worker):
    return [{"kind": "script", "metadata": {}}, {"kind": "scene", "scene_index": 0, "metadata": {}},
            {"kind": "render", "storage_path": f"{TA}/derivatives/{j}/{worker}-render.mp4", "byte_size": 5,
             "mime_type": "video/mp4", "metadata": {}}]


def state(c, j):
    return c.execute("""select (select status from marketing_generation_jobs where id = %(j)s),
        (select count(*) from marketing_generation_outputs where job_id = %(j)s),
        (select count(*) from marketing_model_usage where job_id = %(j)s),
        (select status || ':' || bytes from marketing_storage_reservations where reservation_key = 'job:' || %(j)s),
        (select count(*) from marketing_generation_job_events where job_id = %(j)s and action = 'succeed')""",
                     {"j": str(j)}).fetchone()


def client_backends(dsn):
    with psycopg.connect(dsn, autocommit=True) as c:
        return one(c, "select count(*) from pg_stat_activity where backend_type = 'client backend' and pid <> pg_backend_pid()")


# ------------------------------------------------------------------ SIGTERM con la subtarea bloqueada
def test_sigterm_while_blocked_stops_renewals_and_b_takes_over(db):
    from services.marketing_lease import LeaseKeeper
    from services.marketing_runtime import install_sigterm
    j = _approved_with_storage(db)
    stop, previous = threading.Event(), signal.getsignal(signal.SIGTERM)
    try:
        install_sigterm(stop)
        with psycopg.connect(db, autocommit=True) as a, psycopg.connect(db, autocommit=True) as b, \
                psycopg.connect(db, autocommit=True) as ka:
            assert one(a, f"select public.marketing_claim_job('{W1}', {LEASE})")["status"] == "claimed"

            def renew(t):                                         # cada renovación con timeout en la base
                ka.execute(f"set statement_timeout = {int(t * 1000)}")
                return one(ka, f"select public.marketing_job_heartbeat('{j}','{W1}', {LEASE})")
            with LeaseKeeper(renew, 0.2, name="pg-sigterm", cancel=stop, renew_timeout=1.0) as k:
                # La subtarea de A está bloqueada aquí (este hilo no hace nada); el keeper renueva.
                end = time.monotonic() + 5
                while k.renewals < 3 and time.monotonic() < end:
                    time.sleep(0.05)
                assert k.renewals >= 3
                os.kill(os.getpid(), signal.SIGTERM)
                end = time.monotonic() + 2
                while (not stop.is_set() or any(t.name == "lease-keeper-pg-sigterm" for t in threading.enumerate())) \
                        and time.monotonic() < end:
                    time.sleep(0.01)
                assert stop.is_set() and k.cancelled                 # sigue "bloqueada", pero ya no renueva
                assert not any(t.name == "lease-keeper-pg-sigterm" for t in threading.enumerate())
                hb = one(b, f"select heartbeat_at from marketing_generation_jobs where id = '{j}'")
                time.sleep(0.6)
                assert one(b, f"select heartbeat_at from marketing_generation_jobs where id = '{j}'") == hb
                deadline = time.monotonic() + LEASE + 10            # vence en la hora de PostgreSQL
                while not one(b, f"select lease_expires_at <= now() from marketing_generation_jobs where id = '{j}'"):
                    assert time.monotonic() < deadline
                    time.sleep(0.5)
                got = one(b, f"select public.marketing_claim_job('{W2}', {LEASE})")
                assert got["status"] == "claimed" and got["job"]["attempts"] == 2
                # A por fin regresa: aunque lo intentara, la base no le deja escribir nada.
                assert finalize(a, j, W1, outputs_for(j, W1))["reason"] == "lease_lost"
                assert fail(a, j, W1)["reason"] == "lease_lost"
                assert one(a, f"select public.marketing_job_heartbeat('{j}','{W1}', {LEASE})") is None
            assert k.joined
            assert state(b, j) == ("processing", 0, 0, "reserved:10", 0)
            assert one(b, f"select lease_owner::text from marketing_generation_jobs where id = '{j}'") == W2
            assert finalize(b, j, W2, outputs_for(j, W2))["status"] == "succeeded"
    finally:
        signal.signal(signal.SIGTERM, previous)
    assert client_backends(db) == 0                                  # sin conexiones huérfanas


# ------------------------------------------------------------------ finalización atómica
@pytest.mark.parametrize("step", ["after_storage", "after_first_output", "before_succeeded"])
def test_fault_inside_finalize_rolls_back_everything_and_b_retries(db, step):
    j = _approved_with_storage(db)
    with psycopg.connect(db, autocommit=True) as a, psycopg.connect(db, autocommit=True) as b:
        assert one(a, f"select public.marketing_claim_job('{W1}', {LEASE})")["status"] == "claimed"
        a.execute("select set_config('test.fault', %s, false)", (step,))
        with pytest.raises(psycopg.errors.RaiseException):
            finalize(a, j, W1, outputs_for(j, W1))
        a.execute("select set_config('test.fault', '', false)")
        assert state(a, j) == ("processing", 0, 0, "reserved:10", 0)    # ni resultados, ni costos, ni reserva
        expire_lease(db, j)
        assert one(b, f"select public.marketing_claim_job('{W2}', {LEASE})")["status"] == "claimed"
        assert finalize(b, j, W2, outputs_for(j, W2), use=usage(j))["status"] == "succeeded"   # misma clave
        assert finalize(b, j, W2, outputs_for(j, W2), use=usage(j))["reason"] == "lease_lost"
        assert state(b, j) == ("succeeded", 3, 1, "consumed:5", 1)       # una vez cada cosa
        assert one(b, f"select count(*) from marketing_generation_outputs where job_id = '{j}' and worker_id <> '{W2}'") == 0


def test_duplicate_outputs_roll_back_the_whole_finalize(db):
    j = _approved_with_storage(db)
    with psycopg.connect(db, autocommit=True) as a:
        assert one(a, f"select public.marketing_claim_job('{W1}', {LEASE})")["status"] == "claimed"
        dup = [{"kind": "script", "metadata": {}}, {"kind": "script", "metadata": {}}]
        with pytest.raises(psycopg.errors.UniqueViolation):
            finalize(a, j, W1, dup)
        assert state(a, j) == ("processing", 0, 0, "reserved:10", 0)


def test_lease_expiring_during_finalize_never_lets_b_in(db):
    j = _approved_with_storage(db)
    with psycopg.connect(db, autocommit=True) as a0, psycopg.connect(db) as a, psycopg.connect(db, autocommit=True) as b:
        assert one(a0, f"select public.marketing_claim_job('{W1}', {LEASE})")["status"] == "claimed"
        a0.execute("update marketing_generation_jobs set lease_expires_at = now() + interval '1 second' where id = %s", (j,))
        assert finalize(a, j, W1, outputs_for(j, W1))["status"] == "succeeded"   # transacción aún abierta
        time.sleep(1.5)                                              # el lease vence con la fila bloqueada
        assert one(b, f"select public.marketing_claim_job('{W2}', {LEASE})")["status"] == "empty"   # SKIP LOCKED
        a.commit()
        assert one(b, f"select public.marketing_claim_job('{W2}', {LEASE})")["status"] == "empty"
        assert state(b, j) == ("succeeded", 3, 1, "consumed:5", 1)


# ------------------------------------------------------------------ transiciones terminales
@pytest.mark.parametrize("reclaimed", [False, True])
def test_expired_worker_cannot_end_the_job(db, reclaimed):
    j = _approved_with_storage(db)
    with psycopg.connect(db, autocommit=True) as a, psycopg.connect(db, autocommit=True) as b:
        assert one(a, f"select public.marketing_claim_job('{W1}', {LEASE})")["status"] == "claimed"
        expire_lease(db, j)
        if reclaimed:
            assert one(b, f"select public.marketing_claim_job('{W2}', {LEASE})")["status"] == "claimed"
        assert fail(a, j, W1)["reason"] == "lease_lost" and finalize(a, j, W1)["reason"] == "lease_lost"
        for to in ("failed", "cancelled", "succeeded"):
            for flag in ("", "worker", f"worker:{W1}", f"worker:{W2}", "expired"):
                if (flag == "expired" and to == "failed" and not reclaimed) or (flag == f"worker:{W2}" and reclaimed
                                                                                 and to != "cancelled"):
                    continue    # timeout administrativo (lease vencido) o el dueño vigente B: caminos legítimos
                with pytest.raises(psycopg.errors.CheckViolation):
                    a.execute(f"""begin; select set_config('marketing.transition', '{flag}', true);
                                  update marketing_generation_jobs set status = '{to}' where id = '{j}'; commit;""")
                a.execute("rollback")
        assert one(a, f"select status from marketing_generation_jobs where id = '{j}'") == "processing"


def test_timeout_only_after_lease_expired(db):
    j = _approved_with_storage(db)
    with psycopg.connect(db, autocommit=True) as a:
        assert one(a, f"select public.marketing_claim_job('{W1}', {LEASE})")["status"] == "claimed"
        assert one(a, "select public.marketing_timeout_jobs(1)") == 0                # lease vigente
        assert one(a, f"select status from marketing_generation_jobs where id = '{j}'") == "processing"
        expire_lease(db, j)
        assert one(a, "select public.marketing_timeout_jobs(1)") >= 1
        assert one(a, f"select status from marketing_generation_jobs where id = '{j}'") in ("timed_out", "failed")


# ------------------------------------------------------------------ cierre de purga ligado al lease
def test_close_purge_bound_to_live_holder(db):
    H1, H2 = W1, W2
    with psycopg.connect(db, autocommit=True) as c:
        c.execute("delete from marketing_runtime_leases")
        mid = str(uuid.uuid4())
        c.execute(f"""insert into marketing_media (id, tenant_id, storage_path, original_filename, media_type, mime_type,
            byte_size, checksum, uploaded_by, contains_people, contains_minors, people_policy)
            values ('{mid}', '{TA}', '{TA}/originals/{mid}/a.png', 'a.png', 'image', 'image/png', 10, repeat('7', 64),
                    '{OWNER}', false, false, 'no_people')""")
        c.execute("update marketing_media set retention_status = 'purge_pending', purge_reason = 'expired' where id = %s", (mid,))
        close = "select public.marketing_close_purge(%s, %s, %s, 'expired', null, 'system')"
        assert one(c, close, TA, mid, None)["status"] == "rejected"                   # sin lease ni owner/manager
        assert one(c, "select public.marketing_acquire_runtime_lease('retention_runner', %s, 60)", H1) is True
        assert one(c, close, TA, mid, H2)["status"] == "lease_lost"                   # no es el holder
        assert one(c, f"select retention_status from marketing_media where id = '{mid}'") == "purge_pending"
        assert one(c, close, TA, mid, H1)["status"] == "closed"
        assert one(c, close, TA, mid, H1)["status"] == "already_closed"
        assert one(c, f"select count(*) from marketing_media_events where media_id = '{mid}' and action = 'purged'") == 1
        c.execute("update marketing_runtime_leases set expires_at = now() - interval '1 second'")
        assert one(c, close, TA, mid, H1)["status"] == "lease_lost"                   # vencido: ya no cierra nada
        assert json.loads(json.dumps(one(c, "select public.marketing_release_runtime_lease('retention_runner', %s)", H2))) \
            in (None, False)
