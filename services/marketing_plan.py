"""Marketing Fase 1 con una cuota pública única de publicaciones."""
from typing import Any, Dict

from services.marketing import MarketingService
from services.marketing_publication_plan import authorize, summary


class MarketingPlanService(MarketingService):
    def publication_plan(self, c, usage=None) -> Dict[str, Any]:
        return summary(self.db, c, (usage or self._usage(c))["posts"])

    def dashboard(self, jwt: str, tenant_id: str) -> Dict[str, Any]:
        out = super().dashboard(jwt, tenant_id)
        c = self.ctx(jwt, tenant_id)
        out["publication_plan"] = self.publication_plan(c, out["usage"])
        # Una sola cuota pública. Los límites técnicos por formato no forman parte del plan mostrado.
        out["limits"] = {"monthly_post_limit": out["publication_plan"]["total"]}
        return out

    def authorize_addon(self, jwt: str, tenant_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        c = self.ctx(jwt, tenant_id)
        self._enabled(c)
        return authorize(self.db, c, body.get("additional_publications"), body.get("confirm") is True,
                         body.get("idempotency_key"))

    def _schedule_checks(self, c, cur: Dict[str, Any], when: Any) -> str:
        original = c.settings
        plan = self.publication_plan(c)
        c.settings = {**original, "monthly_post_limit": plan["total"],
                      "monthly_image_limit": None, "monthly_reel_limit": None}
        try:
            return super()._schedule_checks(c, cur, when)
        finally:
            c.settings = original
