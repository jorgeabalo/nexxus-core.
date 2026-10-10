"""
AITA Marketing (Fase 2) — base común de Biblioteca y Estudio.

Reutiliza la puerta única de Fase 1 (MarketingService.ctx): sesión válida → owner/manager del
tenant → tenants.modules.marketing === true → configuración. Staff, socios, anónimos y otros
tenants nunca llegan a tocar una tabla. Además, todo id que llega del cliente se vuelve a buscar
filtrando por tenant_id: nunca se confía en un id del navegador.
"""
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from services import marketing_domain as d
from services import marketing_jobs_domain as jd
from services.marketing import MarketingService, _iso
from services.member_portal import PortalError

BUCKET = "marketing-assets"


def uid(v: Any, code: str = "not_found", status: int = 404) -> str:
    try:
        return str(uuid.UUID(str(v)))
    except (ValueError, TypeError, AttributeError):
        raise PortalError(code, status)


class StudioBase(MarketingService):
    def _settings(self, tenant_id: str) -> Dict[str, Any]:
        row = (self.db.select("marketing_settings", {"tenant_id": f"eq.{tenant_id}", "select": "*", "limit": "1"}) or [None])[0]
        out = {**d.DEFAULT_SETTINGS, **jd.GEN_DEFAULTS, "monthly_marketing_ai_cost_limit_cents": 0}
        if row:
            out.update({k: row.get(k) for k in out if k in row})
        # Presupuesto global de costos (lo fija el operador). Sin fila → 0: generación no habilitada.
        budget = (self.db.select("aita_cost_budgets", {"tenant_id": f"eq.{tenant_id}",
                                                       "select": "monthly_marketing_ai_cost_limit_cents",
                                                       "limit": "1"}) or [{}])[0]
        out["monthly_marketing_ai_cost_limit_cents"] = int(budget.get("monthly_marketing_ai_cost_limit_cents") or 0)
        return out

    def _writable(self, c) -> None:
        """Escribir exige el módulo activo para el tenant (marketing_enabled)."""
        self._enabled(c)

    def _one(self, c, table: str, row_id: Any, cols: str = "*") -> Dict[str, Any]:
        rows = self.db.select(table, {"id": f"eq.{uid(row_id)}", "tenant_id": f"eq.{c.tenant_id}",
                                      "select": cols, "limit": "1"})
        if not rows:
            raise PortalError("not_found", 404)
        return rows[0]

    def _month_bounds(self, c, ref: Optional[datetime] = None):
        start, end = self._month(c, ref)
        return _iso(start), _iso(end)
