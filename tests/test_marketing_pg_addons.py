"""
AITA Marketing: ampliaciones y límite efectivo con concurrencia REAL en PostgreSQL 17 (clúster desechable).

* Dos programaciones simultáneas del último hueco del ciclo: solo una entra (bloqueo por tenant en la base).
* Dos autorizaciones simultáneas con la misma clave: una sola fila y un solo cargo.
* Dos autorizaciones simultáneas con claves distintas: nunca dos vigentes en el mismo ciclo.
* Dos aprobaciones simultáneas de la misma solicitud: se decide una vez.
Mismo comando que tests/test_marketing_pg_concurrency.py (MARKETING_PG_BIN, --noconftest).
"""
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
MIGS = ("20261009120000_aita_marketing.sql", "20261013120000_marketing_publication_addons.sql")
O, M = "00000000-0000-0000-0000-00000000000a", "00000000-0000-0000-0000-00000000000b"
TA = "10000000-0000-0000-0000-00000000000a"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def pg(tmp_path_factory):
    base = tmp_path_factory.mktemp("marketing_pg_addons")
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


_k = [0]


def key():
    _k[0] += 1
    return f"addon-pg-real-{_k[0]:06d}"


def race(dsn, sqls):
    """Cada SQL en su conexión y transacción, a la vez (barrera). Devuelve el resultado o el código de error."""
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


def submit(n, k, auth=True, who=O):
    return f"select public.marketing_submit_publication_addon('{TA}','{who}','monthly',null,{n},{auth},'{k}')"


def reset(dsn):
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute("alter table marketing_publication_addons disable trigger user")   # solo en el clúster desechable
        c.execute("alter table marketing_publication_addon_events disable trigger user")
        c.execute("delete from marketing_publication_addon_events; delete from marketing_publication_addons; delete from marketing_content")
        c.execute("alter table marketing_publication_addons enable trigger user")
        c.execute("alter table marketing_publication_addon_events enable trigger user")


def approved_content(dsn, n, when):
    ids = []
    with psycopg.connect(dsn, autocommit=True) as c:
        for i in range(n):
            cid = c.execute(f"""insert into marketing_content (tenant_id, title, format, channels, status, scheduled_at, created_by)
                values ('{TA}', 'p{i}', 'reel', array['instagram'], 'draft', {when}, '{O}') returning id""").fetchone()[0]
            c.execute(f"update marketing_content set status = 'review' where id = '{cid}'")
            c.execute(f"update marketing_content set status = 'approved' where id = '{cid}'")
            ids.append(cid)
    return ids


WHEN = "(private.marketing_cycle('10000000-0000-0000-0000-00000000000a', now()) + interval '25 day')::timestamptz"


@pytest.mark.parametrize("rnd", range(3))
def test_two_simultaneous_schedulings_never_exceed_the_effective_limit(pg, rnd):
    reset(pg)
    assert q(pg, submit(1, key()))["result"] == "authorized"                   # 8 + 1 = 9
    ids = approved_content(pg, 10, WHEN)
    with psycopg.connect(pg, autocommit=True) as c:
        for cid in ids[:8]:
            c.execute(f"update marketing_content set status = 'scheduled' where id = '{cid}'")
    res = race(pg, [f"update marketing_content set status = 'scheduled' where id = '{ids[8]}' returning id",
                    f"update marketing_content set status = 'scheduled' where id = '{ids[9]}' returning id"])
    assert sorted(map(str, res)).count("23514") == 1, res                     # uno entra, el otro choca con el límite
    assert q(pg, f"select count(*) from marketing_content where tenant_id = '{TA}' and status = 'scheduled'") == 9


def test_simultaneous_authorizations_with_the_same_key_create_one(pg):
    reset(pg)
    k = key()
    res = race(pg, [submit(12, k), submit(12, k)])
    assert sorted(r["result"] for r in res) == ["authorized", "existing"], res
    assert q(pg, "select count(*) from marketing_publication_addons") == 1
    assert q(pg, "select count(*) from marketing_publication_addon_events where event = 'authorized'") == 1


def test_simultaneous_different_authorizations_never_accumulate(pg):
    reset(pg)
    res = race(pg, [submit(4, key()), submit(12, key())])
    assert all(r["result"] == "authorized" for r in res), res
    cur = q(pg, f"select private.marketing_post_limit('{TA}', private.marketing_cycle('{TA}', now()))")
    nxt = q(pg, f"select private.marketing_post_limit('{TA}', (private.marketing_cycle('{TA}', now()) + interval '1 month')::date)")
    assert cur in (12, 20) and nxt in (12, 20) and cur != 8 + 4 + 12          # nunca 24: una vigente por ciclo
    assert q(pg, "select count(*) from marketing_publication_addons where status = 'authorized_uninvoiced'") == 1


def test_simultaneous_approvals_decide_once(pg):
    reset(pg)
    rid = q(pg, submit(4, key(), auth=False, who=M))["addon"]["id"]
    sql = f"select public.marketing_decide_publication_addon('{TA}','{rid}','{O}', true)"
    res = race(pg, [sql, sql])
    assert sorted(r["result"] for r in res) == ["authorized", "existing"], res
    assert q(pg, "select count(*) from marketing_publication_addon_events where event = 'authorized'") == 1
