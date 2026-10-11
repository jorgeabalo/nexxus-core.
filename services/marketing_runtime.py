"""
AITA Marketing (Fase 2) — procesos de fondo, fuera del proceso web.

    python -m services.marketing_runtime retention [--dry-run | --apply] [--batch-size N] [--max-batches N]
    python -m services.marketing_runtime worker [--once | --loop] [--max-jobs N] [--lease-seconds S]
                                                [--poll-seconds S] [--max-runtime-seconds S]

* retention: RetentionRunner por lotes. Por defecto es DRY-RUN (no escribe nada: lecturas reales,
  escrituras solo registradas). Con --apply purga. Un solo purgador a la vez (lease en la base); si otro
  ya corre, termina sin hacer nada. Repetible: cada lote vuelve a leer el estado.
* worker: por defecto UNA ejecución (--once): reclama hasta --max-jobs trabajos con lease, los ejecuta y
  termina. --loop (continuo) NUNCA es el predeterminado: repite rondas con --poll-seconds de espera hasta
  --max-runtime-seconds o SIGTERM; un worker permanente aumenta costos y no debe crearse todavía. Solo arranca con
  MARKETING_WORKER_ENABLED=true y se niega a correr si hay proveedores reales o herramientas locales
  activados (este PR no conecta ninguno). Sin red salvo la propia base (Supabase).

Pensado para Railway Cron (ver docs/AITA_MARKETING_PHASE2.md, §4g). NO está programado en producción.
Salida: una línea JSON por lote, sin secretos. Códigos: 0 ok · 2 deshabilitado · 3 sin base de datos.
"""
import argparse
import json
import os
import signal
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

READ_ONLY_RPCS = ("marketing_storage_used",)


class DryRunDB:
    """Lecturas reales; escrituras (insert/update/upsert/delete, RPC de escritura y Storage) solo se
    registran. El flujo continúa como si hubieran tenido éxito, para contar lo que se haría."""

    def __init__(self, db):
        self._db = db
        self.writes: List[Dict[str, Any]] = []

    def select(self, table, params):
        return self._db.select(table, params)

    def _record(self, op, target, **kw):
        self.writes.append({"op": op, "target": target, **kw})

    def insert(self, table, row):
        self._record("insert", table)
        return dict(row)

    def upsert(self, table, row, on_conflict):
        self._record("upsert", table)
        return dict(row)

    def update(self, table, filters, values):
        self._record("update", table, fields=sorted(values))
        return [dict(r, **values) for r in (self._db.select(table, {**filters, "select": "*", "limit": "500"}) or [])]

    def delete(self, table, filters):
        self._record("delete", table)
        return []

    def rpc(self, fn, args):
        if fn in READ_ONLY_RPCS:
            return self._db.rpc(fn, args)
        self._record("rpc", fn)
        return True if fn == "marketing_acquire_runtime_lease" else 0

    def storage_remove(self, bucket, key):
        self._record("storage_remove", bucket)              # nunca la ruta (puede identificar al tenant)


def _db():
    from services.supabase_admin import SupabaseAdmin
    db = SupabaseAdmin()
    return db if getattr(db, "enabled", False) else None


def run_retention(db, apply: bool, batch_size: int, max_batches: int, now: Optional[datetime] = None) -> List[Dict]:
    from services.marketing_retention import RetentionRunner
    target = db if apply else DryRunDB(db)
    holder = str(uuid.uuid4())                     # el mismo dueño del lease en todos los lotes de esta ejecución
    out = []
    for i in range(max(int(max_batches), 1)):
        res = RetentionRunner(target, now=now or datetime.now(timezone.utc), holder=holder).run(limit=int(batch_size))
        res = {"batch": i + 1, "mode": "apply" if apply else "dry_run", **res}
        if not apply:
            res["would_write"] = len(target.writes)
        out.append(res)
        busy = sum(int(res.get(k) or 0) for k in ("warned", "purged", "failed", "protected", "derivatives", "outputs"))
        if res.get("locked") or busy == 0 or not apply:       # bloqueado, nada más que hacer, o dry-run (un lote)
            break
    return out


def providers_disabled(env: Optional[Dict[str, str]] = None) -> bool:
    from services.marketing_ai_router import provider_enabled
    env = os.environ if env is None else env
    return not provider_enabled("omniroute", env) and not provider_enabled("local_ffmpeg", env)


def run_worker(db, max_jobs: int, lease_seconds: int, env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    from services.marketing_ai_router import MarketingAIRouter, default_adapters
    from services.marketing_worker import JobRunner
    env = os.environ if env is None else env
    if str(env.get("MARKETING_WORKER_ENABLED", "")).lower() != "true":
        return {"status": "disabled", "reason": "MARKETING_WORKER_ENABLED"}
    if not providers_disabled(env):
        return {"status": "disabled", "reason": "real_providers_not_allowed"}
    runner = JobRunner(db, MarketingAIRouter(env=dict(env)), default_adapters(), lease_seconds=int(lease_seconds))
    results = runner.run_pending(limit=int(max_jobs))
    return {"status": "ok", "worker": runner.worker_id, "processed": len(results),
            "succeeded": sum(1 for r in results if r.get("status") == "succeeded"),
            "failed": sum(1 for r in results if r.get("status") == "failed" or r.get("error"))}


def run_worker_loop(db, max_jobs: int, lease_seconds: int, poll_seconds: int, max_runtime_seconds: int,
                    env: Optional[Dict[str, str]] = None, sleep=time.sleep, clock=time.monotonic,
                    stop=lambda: False) -> Dict[str, Any]:
    """Modo continuo (solo con --loop). Rondas de run_worker con espera; se detiene al agotar el tiempo
    máximo, con SIGTERM (stop) o si el worker está deshabilitado."""
    poll, limit = max(int(poll_seconds), 5), max(int(max_runtime_seconds), 1)
    start, rounds, processed = clock(), 0, 0
    while not stop() and clock() - start < limit:
        res = run_worker(db, max_jobs, lease_seconds, env)
        if res["status"] == "disabled":
            return res
        rounds, processed = rounds + 1, processed + res["processed"]
        if stop() or clock() - start + poll >= limit:
            break
        sleep(poll)
    return {"status": "ok", "mode": "loop", "rounds": rounds, "processed": processed}


def main(argv: Optional[List[str]] = None, db=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m services.marketing_runtime")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("retention", help="purga y retención (dry-run por defecto)")
    rm = r.add_mutually_exclusive_group()
    rm.add_argument("--dry-run", dest="apply", action="store_false", help="no escribe nada (predeterminado)")
    rm.add_argument("--apply", dest="apply", action="store_true", help="escribe de verdad")
    r.set_defaults(apply=False)
    r.add_argument("--batch-size", type=int, default=200)
    r.add_argument("--max-batches", type=int, default=10)
    w = sub.add_parser("worker", help="ejecuta trabajos en cola con lease (una ejecución por defecto)")
    wm = w.add_mutually_exclusive_group()
    wm.add_argument("--once", dest="loop", action="store_false", help="una ejecución y termina (predeterminado)")
    wm.add_argument("--loop", dest="loop", action="store_true", help="continuo (NO predeterminado; aumenta costos)")
    w.set_defaults(loop=False)
    w.add_argument("--max-jobs", type=int, default=20)
    w.add_argument("--lease-seconds", type=int, default=120)
    w.add_argument("--poll-seconds", type=int, default=30)
    w.add_argument("--max-runtime-seconds", type=int, default=3300)
    a = ap.parse_args(argv)
    db = db or _db()
    if db is None:
        print(json.dumps({"status": "error", "reason": "database_not_configured"}))
        return 3
    if a.cmd == "retention":
        for line in run_retention(db, a.apply, a.batch_size, a.max_batches):
            print(json.dumps(line, default=str))
        return 0
    if a.loop:
        stopping = []
        signal.signal(signal.SIGTERM, lambda *_: stopping.append(1))
        res = run_worker_loop(db, a.max_jobs, a.lease_seconds, a.poll_seconds, a.max_runtime_seconds,
                              stop=lambda: bool(stopping))
    else:
        res = {**run_worker(db, a.max_jobs, a.lease_seconds), "mode": "once"}
    print(json.dumps(res))
    return 2 if res["status"] == "disabled" else 0


if __name__ == "__main__":
    sys.exit(main())
