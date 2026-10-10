"""
Doble de pruebas de public.marketing_approve_generation (20261011140000_marketing_generation_budget.sql):
aprueba y reserva el costo máximo bajo un candado, como hace SELECT … FOR UPDATE en PostgreSQL.
La función SQL real se prueba en tests/sql/marketing_generation_budget.mjs.
"""
import threading
import time


class MarketingRpcMixin:
    """Requiere self.tables (FakeDB) y self.rpc_now (fecha ISO de "ahora")."""
    rpc_delay = 0.0                                   # agranda la ventana de carrera en las pruebas de concurrencia

    def _rpc_init(self, now_iso):
        self._rpc_lock = threading.Lock()
        self.rpc_now = now_iso
        self.rpc_calls = []

    def rpc(self, fn, a):
        self.rpc_calls.append(fn)
        with self._rpc_lock:
            return getattr(self, f"_rpc_{fn}")(a)

    def _rpc_marketing_approve_generation(self, a):
        reserved = a["p_reserved"]
        if reserved is None or reserved < 0:
            return {"status": "rejected", "reason": "cost_not_estimable"}
        s = next((x for x in self.tables["marketing_settings"] if x["tenant_id"] == a["p_tenant"]), None)
        limit = (s or {}).get("monthly_ai_cost_limit", 0)
        if not s or s.get("ai_generation_enabled") is not True or limit == 0:
            return {"status": "rejected", "reason": "generation_disabled"}
        job = next((j for j in self.tables["marketing_generation_jobs"]
                    if j["id"] == a["p_job"] and j["tenant_id"] == a["p_tenant"]
                    and j["status"] == "awaiting_generation_approval"), None)
        if not job:
            return {"status": "rejected", "reason": "conflict"}
        used = sum(float(j.get("reserved_cost") or 0) if j["status"] in ("queued", "processing")
                   else float(j.get("actual_cost") or 0)
                   for j in self.tables["marketing_generation_jobs"]
                   if j["tenant_id"] == a["p_tenant"] and j.get("approved_at"))
        if self.rpc_delay:
            time.sleep(self.rpc_delay)
        if limit is not None and used + reserved > float(limit):
            return {"status": "rejected", "reason": "budget_exceeded", "available": max(float(limit) - used, 0)}
        job.update({"status": "queued", "approved_at": self.rpc_now, "approved_by": a["p_user"], "reserved_cost": reserved})
        self.tables["marketing_generation_job_events"].append({
            "tenant_id": a["p_tenant"], "job_id": job["id"], "action": "approve", "from_status": "awaiting_generation_approval",
            "to_status": "queued", "detail": {"reserved_cost": reserved}, "actor_id": a["p_user"], "actor_role": "owner",
            "created_at": self.rpc_now})
        return {"status": "approved", "job": dict(job)}
