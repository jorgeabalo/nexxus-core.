"""
Doble de pruebas del presupuesto global (aita_cost_*): emula las funciones SQL de reserva y conciliación
con un candado, como hace SELECT … FOR UPDATE en PostgreSQL. La lógica real vive en
supabase/migrations/20261011140000_aita_cost_control.sql y se prueba en tests/sql/aita_cost_control.mjs.
"""
import threading
import time
import uuid

GROUPS = {"voice": ("voice", "claudia_ai"), "claudia_ai": ("voice", "claudia_ai"), "marketing_ai": ("marketing_ai",),
          "storage": ("storage",)}
LIMIT_COL = {"voice": "monthly_voice_cost_limit_cents", "claudia_ai": "monthly_voice_cost_limit_cents",
             "marketing_ai": "monthly_marketing_ai_cost_limit_cents", "storage": "monthly_storage_cost_limit_cents"}
PILOT = {"monthly_total_cost_limit_cents": 8000, "monthly_voice_cost_limit_cents": 3500,
         "monthly_marketing_ai_cost_limit_cents": 2000, "monthly_storage_cost_limit_cents": 750,
         "monthly_infrastructure_allocation_cents": 750, "monthly_reserve_cents": 1000}


class CostRpcMixin:
    """Requiere self.tables (FakeDB). Añade aita_cost_budgets / aita_cost_ledger y rpc()."""
    rpc_delay = 0.0                                   # para forzar carreras en las pruebas de concurrencia

    def _cost_init(self, tenants):
        self._cost_lock = threading.Lock()
        self.tables["aita_cost_budgets"] = [{"tenant_id": t, **PILOT} for t in tenants]
        self.tables["aita_cost_ledger"] = []
        self.rpc_calls = []

    def _used(self, tenant, cats=None):
        return sum((e["actual_cost_cents"] if e["actual_cost_cents"] is not None else e["reserved_cost_cents"])
                   for e in self.tables["aita_cost_ledger"]
                   if e["tenant_id"] == tenant and e["status"] != "released" and (cats is None or e["service_category"] in cats))

    def rpc(self, fn, a):
        self.rpc_calls.append(fn)
        with self._cost_lock:                          # = FOR UPDATE sobre el presupuesto del tenant
            return getattr(self, f"_rpc_{fn}")(a)

    def _rpc_aita_cost_reserve(self, a):
        est = a["p_estimated_cents"]
        if est is None or est < 0:
            return {"status": "rejected", "reason": "cost_not_estimable"}
        b = next((x for x in self.tables["aita_cost_budgets"] if x["tenant_id"] == a["p_tenant"]), None)
        if not b:
            return {"status": "rejected", "reason": "budget_not_configured"}
        dup = next((e for e in self.tables["aita_cost_ledger"]
                    if e["tenant_id"] == a["p_tenant"] and e["idempotency_key"] == a["p_idempotency_key"]), None)
        if dup:
            return {"status": "duplicate", "ledger_id": dup["id"], "ledger_status": dup["status"]}
        cat = a["p_category"]
        cats = GROUPS.get(cat, ("processing", "infrastructure", "publishing"))
        limit = b[LIMIT_COL.get(cat, "monthly_infrastructure_allocation_cents")]
        used_cat, used = self._used(a["p_tenant"], cats), self._used(a["p_tenant"])
        if self.rpc_delay:
            time.sleep(self.rpc_delay)
        if used_cat + est > limit:
            return {"status": "rejected", "reason": "category_budget_exceeded"}
        if used + est > b["monthly_total_cost_limit_cents"] - b["monthly_reserve_cents"]:
            return {"status": "rejected", "reason": "total_budget_exceeded"}
        e = {"id": str(uuid.uuid4()), "tenant_id": a["p_tenant"], "service_category": cat, "provider": a["p_provider"],
             "model": a["p_model"], "operation": a["p_operation"], "estimated_cost_cents": est, "reserved_cost_cents": est,
             "actual_cost_cents": None, "currency": "USD", "idempotency_key": a["p_idempotency_key"],
             "status": "reserved", "reconciled_at": None}
        self.tables["aita_cost_ledger"].append(e)
        return {"status": "reserved", "ledger_id": e["id"]}

    def _rpc_aita_cost_reconcile(self, a):
        e = next((x for x in self.tables["aita_cost_ledger"]
                  if x["tenant_id"] == a["p_tenant"] and x["idempotency_key"] == a["p_idempotency_key"]), None)
        if not e:
            return {"status": "not_found"}
        if e["status"] != "reserved":
            return {"status": e["status"], "duplicate": True}
        e["status"] = "released" if a.get("p_release") and a["p_actual_cents"] == 0 else "committed"
        e["actual_cost_cents"], e["reconciled_at"] = a["p_actual_cents"], "now"
        return {"status": e["status"]}

    def _rpc_aita_cost_summary(self, a):
        b = next((x for x in self.tables["aita_cost_budgets"] if x["tenant_id"] == a["p_tenant"]), None)
        led = [e for e in self.tables["aita_cost_ledger"] if e["tenant_id"] == a["p_tenant"]]

        def s(status, field, cat=None):
            return sum(e[field] or 0 for e in led if e["status"] == status and (cat is None or e["service_category"] == cat))
        return {"configured": bool(b),
                "total_limit_cents": b["monthly_total_cost_limit_cents"] - b["monthly_reserve_cents"] if b else None,
                "marketing_ai_limit_cents": b["monthly_marketing_ai_cost_limit_cents"] if b else None,
                "consumed_cents": s("committed", "actual_cost_cents"), "reserved_cents": s("reserved", "reserved_cost_cents"),
                "marketing_ai_consumed_cents": s("committed", "actual_cost_cents", "marketing_ai"),
                "marketing_ai_reserved_cents": s("reserved", "reserved_cost_cents", "marketing_ai")}
