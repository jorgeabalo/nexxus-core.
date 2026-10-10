"""
AITA — control global de costos internos por tenant (objetivo comercial: < 80 USD/mes).

Incluye voz (Twilio + IA de Claudia), IA de Marketing, almacenamiento, procesamiento,
infraestructura y publicación. NO incluye el presupuesto publicitario del cliente (Google Ads,
Meta Ads…): ese dinero va completamente aparte y nunca pasa por aquí.

Toda operación facturable sigue este orden:
  1. calcular el costo máximo estimado (si no se puede, NO se ejecuta);
  2-4. reservar de forma atómica en la base de datos (aita_cost_reserve bloquea el presupuesto del
       tenant: dos operaciones simultáneas nunca reservan el mismo saldo); se rechaza si no cabe;
  5. aprobación de owner/manager (la pide quien llama, antes de reservar);
  6. ejecutar con idempotencia (la misma clave nunca reserva ni cobra dos veces);
  7-8. conciliar UNA vez con el costo real: la diferencia con la reserva queda libre.

Los límites los fija solo el operador; aquí no hay ninguna función que los modifique.
"""
import math
from decimal import ROUND_CEILING, Decimal
from typing import Any, Dict, Optional

from services.marketing_domain import DomainError

CATEGORIES = ("voice", "claudia_ai", "marketing_ai", "storage", "processing", "infrastructure", "publishing")
# Prioridad de producción económica (de más barata a más cara). El router y el Estudio la siguen.
PRODUCTION_PRIORITY = ("client_material", "local_processing", "templates_ffmpeg", "small_models",
                       "image_generation", "image_to_video", "text_to_video")
PILOT_PROPOSAL_CENTS = {"monthly_total_cost_limit_cents": 8000, "monthly_voice_cost_limit_cents": 3500,
                        "monthly_marketing_ai_cost_limit_cents": 2000, "monthly_storage_cost_limit_cents": 750,
                        "monthly_infrastructure_allocation_cents": 750, "monthly_reserve_cents": 1000}
_REASONS = {"category_budget_exceeded": "budget_exceeded", "total_budget_exceeded": "budget_exceeded",
            "budget_not_configured": "budget_not_configured", "cost_not_estimable": "cost_not_estimable"}


def to_cents(usd: Any) -> Optional[int]:
    """USD → centavos redondeando hacia arriba (nunca se reserva de menos). None si no es estimable."""
    if usd is None or isinstance(usd, bool):
        return None
    try:
        d = Decimal(str(usd))
    except Exception:
        return None
    if not d.is_finite() or d < 0:
        return None
    return int((d * 100).to_integral_value(rounding=ROUND_CEILING))


def warning_level(available: int, limit: int) -> Optional[str]:
    """Avisos al quedar 25 %, 10 % o 0 % del presupuesto."""
    if limit <= 0:
        return "not_enabled"
    pct = 100 * max(available, 0) / limit
    if pct <= 0:
        return "exhausted"
    if pct <= 10:
        return "critical"
    if pct <= 25:
        return "low"
    return None


class CostBudget:
    def __init__(self, db):
        self.db = db

    def reserve(self, tenant_id: str, category: str, provider: str, model: Optional[str], operation: str,
                estimated_cents: Optional[int], idempotency_key: str) -> Dict[str, Any]:
        if category not in CATEGORIES:
            raise DomainError("invalid_cost_category", 400)
        if estimated_cents is None or not isinstance(estimated_cents, int) or estimated_cents < 0:
            raise DomainError("cost_not_estimable", 409)               # sin costo máximo no se ejecuta
        res = self.db.rpc("aita_cost_reserve", {
            "p_tenant": tenant_id, "p_category": category, "p_provider": provider, "p_model": model,
            "p_operation": operation[:80], "p_estimated_cents": estimated_cents,
            "p_idempotency_key": idempotency_key}) or {}
        if res.get("status") == "rejected":
            raise DomainError(_REASONS.get(res.get("reason"), "budget_exceeded"), 409)
        if res.get("status") not in ("reserved", "duplicate"):
            raise DomainError("budget_unavailable", 503)
        return res

    def reconcile(self, tenant_id: str, idempotency_key: str, actual_cents: int, release: bool = False) -> Dict[str, Any]:
        return self.db.rpc("aita_cost_reconcile", {"p_tenant": tenant_id, "p_idempotency_key": idempotency_key,
                                                   "p_actual_cents": int(actual_cents), "p_release": bool(release)}) or {}

    def release(self, tenant_id: str, idempotency_key: str) -> Dict[str, Any]:
        return self.reconcile(tenant_id, idempotency_key, 0, release=True)

    def summary(self, tenant_id: str) -> Dict[str, Any]:
        """Solo cifras del propio tenant: sin proveedores, modelos, márgenes ni otros tenants."""
        s = self.db.rpc("aita_cost_summary", {"p_tenant": tenant_id}) or {}
        out = {"configured": bool(s.get("configured")), "currency": "USD"}
        for scope, limit_key, used_key, res_key in (
                ("total", "total_limit_cents", "consumed_cents", "reserved_cents"),
                ("marketing_ai", "marketing_ai_limit_cents", "marketing_ai_consumed_cents", "marketing_ai_reserved_cents")):
            limit = int(s.get(limit_key) or 0)
            used, held = int(s.get(used_key) or 0), int(s.get(res_key) or 0)
            avail = max(limit - used - held, 0)
            out[scope] = {"limit_cents": limit, "consumed_cents": used, "reserved_cents": held, "available_cents": avail,
                          "warning": warning_level(avail, limit),
                          "available_pct": None if limit <= 0 else math.floor(100 * avail / limit)}
        return out
