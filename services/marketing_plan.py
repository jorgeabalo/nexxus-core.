"""Marketing Fase 1 con una cuota pública única de publicaciones."""
from typing import Any, Dict

from services.marketing import MarketingService
from services import marketing_publication_plan as pp


class MarketingPlanService(MarketingService):
    def publication_plan(self, c, usage=None) -> Dict[str, Any]:
        return pp.summary(self.db, c, (usage or self._usage(c))["posts"])

    def dashboard(self, jwt: str, tenant_id: str) -> Dict[str, Any]:
        out = super().dashboard(jwt, tenant_id)
        c = self.ctx(jwt, tenant_id)
        out["publication_plan"] = self.publication_plan(c, out["usage"])
        # Una sola cuota pública. Los límites técnicos por formato no forman parte del plan mostrado.
        out["limits"] = {"monthly_post_limit": out["publication_plan"]["total"]}
        out["usage"] = {"posts": out["usage"]["posts"]}
        out["settings"] = {k: v for k, v in out["settings"].items() if k not in ("monthly_image_limit", "monthly_reel_limit")}
        return out

    # ---------- ampliaciones: el manager solicita; autoriza el owner (o un manager con permiso delegado) ----------
    def _addon_ctx(self, jwt: str, tenant_id: str):
        c = self.ctx(jwt, tenant_id)
        self._enabled(c)
        return c

    def request_addon(self, jwt: str, tenant_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        return pp.submit(self.db, self._addon_ctx(jwt, tenant_id), body, authorize=False)

    def authorize_addon(self, jwt: str, tenant_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        return pp.submit(self.db, self._addon_ctx(jwt, tenant_id), body, authorize=True)

    def decide_addon(self, jwt: str, tenant_id: str, addon_id: str, approve: bool, body: Dict[str, Any]) -> Dict[str, Any]:
        return pp.decide(self.db, self._addon_ctx(jwt, tenant_id), addon_id, approve, body.get("confirm") is True)

    def cancel_addon(self, jwt: str, tenant_id: str, addon_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        return pp.cancel(self.db, self._addon_ctx(jwt, tenant_id), addon_id, body.get("confirm") is True)

    def set_purchase_permission(self, jwt: str, tenant_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        return pp.set_permission(self.db, self._addon_ctx(jwt, tenant_id), body.get("managers_can_authorize"))

    def _schedule_checks(self, c, cur: Dict[str, Any], when: Any) -> str:
        original = c.settings
        plan = self.publication_plan(c)
        c.settings = {**original, "monthly_post_limit": plan["total"],
                      "monthly_image_limit": None, "monthly_reel_limit": None}
        try:
            return super()._schedule_checks(c, cur, when)
        finally:
            c.settings = original
