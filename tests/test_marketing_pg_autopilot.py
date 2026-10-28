"""
AITA Marketing: idempotencia de «Planear contenido» y aprobación automática con concurrencia REAL en PostgreSQL 17.

* Varias peticiones simultáneas con la misma Idempotency-Key: un solo contenido, una sola clave, un solo alta.
* La misma clave con otro cuerpo, a la vez: una crea y la otra recibe idempotency_conflict.
* Dos aprobaciones automáticas simultáneas del mismo contenido: se aprueba una vez.
* Pausa y aprobación a la vez: o se aprueba antes de la pausa (y vuelve a revisión) o falla por la pausa.
Mismo comando que tests/test_marketing_pg_concurrency.py (MARKETING_PG_BIN, --noconftest).
"""
import json
import os
import shutil
import socket
import subprocess
import threading
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")
PG_BIN = os.getenv("MARKETING_PG_BIN", "")
if not PG_BIN or not (Path(PG_BIN) / "initdb").exists():
    pytest.skip("MARKETING_PG_BIN no apunta a binarios de PostgreSQL", allow_module_level=True)

ROOT = Path(__file__).resolve().parents[1]
MIGS = ("20261009120000_aita_marketing.sql", "20261013120000_marketing_publication_addons.sql",
        "20261014120000_marketing_autopilot.sql")
O, M = "00000000-0000-0000-0000-00000000000a", "00000000-0000-0000-0000-00000000000b"
TA = "10000000-0000-0000-0000-00000000000a"
RULES = json.dumps({"objective": "calls", "platforms": ["instagram"], "formats": ["reel"], "days": [0, 3],
                    "hours": {"from": "10:00", "to": "12:00"}, "mode": "fully_ai"})
NEXT = "(date_trunc('month', now() at time zone 'America/Chicago') + interval '1 month')::date"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def pg(tmp_path_factory):
    base = tmp_path_factory.mktemp("marketing_pg_autopilot")
    data, port = base / "data", free_port()
    env = {**os.environ, "LC_ALL": "C", "LANG": "C"}
    run = lambda *a: subprocess.run([str(Path(PG_BIN) / a[0]), *a[1:]], check=True, capture_output=True, text=True, env=env)  # noqa: E731
    run("initdb", "-D", str(data), "-U", "postgres", "--auth=trust", "-E", "UTF8", "--no-locale")
    run("pg_ctl", "-D", str(data), "-l", str(base / "pg.log"), "-w", "start",
        "-o", f"-p {port} -c listen_addresses=127.0.0.1 -c unix_socket_directories=''")
    dsn = f"host=127.0.0.1 port={port} user=postgres dbname=postgres"
    try:
        with psycopg.connect(dsn, autocommit=True) as c:
            c.execute((ROOT / "tests/sql/bootstrap_local.sql").read_text())
            c.execute(f"""insert into auth.users (id) values ('{O}'), ('{M}');
                insert into tenants (id, slug, name, timezone) values ('{TA}','ta','A','America/Chicago');
                insert into tenant_users (tenant_id, user_id, role) values ('{TA}','{O}','owner'), ('{TA}','{M}','manager');""")
            for m in MIGS:
                c.execute((ROOT / "supabase/migrations" / m).read_text())
            c.execute(f"""insert into marketing_settings (tenant_id, marketing_enabled, monthly_post_limit) values ('{TA}', true, 8)
                on conflict (tenant_id) do update set marketing_enabled = true, monthly_post_limit = 8""")
        yield dsn
    finally:
        subprocess.run([str(Path(PG_BIN) / "pg_ctl"), "-D", str(data), "-m", "fast", "-w", "stop"], capture_output=True, env=env)
        shutil.rmtree(base, ignore_errors=True)


def race(dsn, sqls):
    barrier, out = threading.Barrier(len(sqls)), [None] * len(sqls)

    def go(i, sql):
        with psycopg.connect(dsn) as c:
            barrier.wait()
            try:
                row = c.execute(sql).fetchone()
                c.commit()
                out[i] = row[0] if row else "ok"
            except psycopg.Error as e:
                c.rollback()
                out[i] = e.sqlstate
    ts = [threading.Thread(target=go, args=(i, s)) for i, s in enumerate(sqls)]
    [t.start() for t in ts]
    [t.join(30) for t in ts]
    return out


def q(dsn, sql):
    with psycopg.connect(dsn, autocommit=True) as c:
        return c.execute(sql).fetchone()[0]


def plan(key, digest="a" * 64, title="Post"):
    content = json.dumps({"title": title, "format": "reel", "channels": ["instagram"], "language": "es"})
    return f"select public.marketing_plan_content('{TA}','{O}','owner','{key}','{digest}','{content}'::jsonb)"


@pytest.mark.parametrize("rnd", range(3))
def test_simultaneous_plans_with_the_same_key_create_one_content(pg, rnd):
    key = f"plan-pg-same-key-{rnd:06d}"
    before = q(pg, "select count(*) from marketing_content")
    res = race(pg, [plan(key)] * 6)
    assert all(isinstance(r, dict) for r in res), res
    assert sorted(r["result"] for r in res) == ["created"] + ["existing"] * 5, res
    assert len({r["content_id"] for r in res}) == 1
    assert q(pg, "select count(*) from marketing_content") == before + 1
    assert q(pg, f"select count(*) from marketing_plan_requests where idempotency_key = '{key}'") == 1
    assert q(pg, f"select count(*) from marketing_approval_events where action = 'create' and content_id = '{res[0]['content_id']}'") == 1


def test_same_key_with_another_body_at_the_same_time_conflicts(pg):
    key = "plan-pg-conflict-0001"
    res = race(pg, [plan(key, "a" * 64), plan(key, "b" * 64)])
    assert sorted(r.get("result") or r.get("error") for r in res) == ["created", "idempotency_conflict"], res
    assert q(pg, f"select count(*) from marketing_plan_requests where idempotency_key = '{key}'") == 1


def _reviewable(pg, day):
    with psycopg.connect(pg, autocommit=True) as c:
        c.execute(f"select public.marketing_autopilot_set('{TA}','{O}','activate','{RULES}'::jsonb, current_date + 1)")
        cid = c.execute(f"""insert into marketing_content (tenant_id, title, format, channels, created_by)
            values ('{TA}', 'auto', 'reel', array['instagram'], '{O}') returning id""").fetchone()[0]
        c.execute(f"update marketing_content set status = 'generating' where id = '{cid}'")
        c.execute(f"update marketing_content set status = 'review' where id = '{cid}'")
        ver = c.execute(f"select rules_version from marketing_autopilot_settings where tenant_id = '{TA}'").fetchone()[0]
        r = c.execute(f"""select public.marketing_autopilot_claim('{TA}','{cid}',
            (({NEXT} + {day})::timestamp + interval '10 hour') at time zone 'America/Chicago', {NEXT} + {day}, {ver})""").fetchone()[0]
        assert r["result"] == "claimed", r
    return cid, ver


def test_two_simultaneous_auto_approvals_approve_once(pg):
    cid, ver = _reviewable(pg, 3)
    sql = f"select public.marketing_autopilot_approve('{TA}','{cid}',{ver})"
    res = race(pg, [sql, sql])
    assert sorted(r.get("result") or r.get("error") for r in res) == ["approved", "invalid_transition"], res
    assert q(pg, f"select count(*) from marketing_approval_events where content_id = '{cid}' and action = 'approve'") == 1
    assert q(pg, f"select actor_role from marketing_approval_events where content_id = '{cid}' and action = 'approve'") == "system"


def test_pause_and_auto_approval_at_the_same_time_never_leave_it_approved(pg):
    cid, ver = _reviewable(pg, 5)
    res = race(pg, [f"select public.marketing_autopilot_approve('{TA}','{cid}',{ver})",
                    f"select public.marketing_autopilot_set('{TA}','{M}','pause',null,null)"])
    assert res[1]["result"] == "paused", res
    assert q(pg, f"select status from marketing_content where id = '{cid}'") == "review"   # espera revisión humana
    assert q(pg, f"select mode from marketing_autopilot_settings where tenant_id = '{TA}'") == "manual"
