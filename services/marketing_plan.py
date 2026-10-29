"""Marketing Fase 1 con una cuota pública única de publicaciones y estados comerciales de contenido.

El tenant ve contenido con estados comerciales (Borrador, Preparando, Listo para revisar, Cambios solicitados,
Aprobado, Programado, Publicado, No se pudo preparar). La preparación con IA es interna (marketing_content_plan).
"""
from datetime import datetime
from typing import Any, Dict

from services.marketing import MarketingService
from services import marketing_copywriter, marketing_text
from services.marketing_autopilot import AutopilotService, channels_connected
from services.marketing_content_plan import ContentPlanner, public_history, public_publication
from services.member_portal import PortalError
from services import marketing_publication_plan as pp


class MarketingPlanService(MarketingService):
    text_client = None                      # cliente de Claude inyectable (tests); None = el real si hay clave

    def planner(self) -> ContentPlanner:
        from services.marketing_studio import StudioService
        raw = getattr(self.db, "_db", self.db)
        return ContentPlanner(self, lambda: StudioService(raw, now=self._now))

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
        rows = self.db.select("marketing_content", {"tenant_id": f"eq.{c.tenant_id}", "status": "neq.archived",
                                                    "select": "id,status", "limit": "10000"}) or []
        pl = self.planner()
        pl.sync(c, rows)
        shown = pl.present_rows(c, rows)
        by = {}
        for r in shown:
            by[r["display_status"]] = by.get(r["display_status"], 0) + 1
        counts = {k: by.get(k, 0) for k in ("draft", "preparing", "ready_for_review", "changes_requested", "approved",
                                             "scheduled", "published", "could_not_prepare")}
        out["counts"] = {**counts, "waiting_approval": counts["ready_for_review"]}
        # Sin fuente real de métricas no hay cifras: solo el estado (nunca un 0 que parezca una métrica).
        out["results"] = {"state": "none" if channels_connected(self) else "not_connected"}
        out["upcoming"] = pl.present_rows(c, out["upcoming"])
        out["recommendations"] = recommendations(counts, out["publication_plan"], len(out["upcoming"]))
        out["text_ai"] = self.text_client is not None or marketing_copywriter.enabled()
        return out

    # ---------- contenido con estados comerciales ----------
    def content_list(self, jwt: str, tenant_id: str, status: str = "", campaign_id: str = "") -> Dict[str, Any]:
        out = super().content_list(jwt, tenant_id, status, campaign_id)
        return {"content": self.planner().present_rows(self.ctx(jwt, tenant_id), out["content"])}

    def content(self, jwt: str, tenant_id: str, content_id: str) -> Dict[str, Any]:
        c = self.ctx(jwt, tenant_id)
        pl = self.planner()
        pl.sync(c, [self._content(c, content_id)])
        out = super().content(jwt, tenant_id, content_id)
        job = pl._jobs(c, [out["content"]["id"]]).get(out["content"]["id"]) if out["content"]["status"] == "generating" else None
        out["content"] = pl.present(c, out["content"], out["history"], job)
        out["history"] = public_history(out["history"])
        out["publications"] = [public_publication(p) for p in out["publications"]]
        out["allowed"] = [a for a in out["allowed"] if a != "idea"]
        out["can_retry"] = out["content"]["display_status"] == "could_not_prepare"
        return out

    def calendar(self, jwt: str, tenant_id: str, start: str, end: str) -> Dict[str, Any]:
        out = super().calendar(jwt, tenant_id, start, end)
        out["items"] = self.planner().present_rows(self.ctx(jwt, tenant_id), out["items"])
        return out

    def write_copy(self, jwt: str, tenant_id: str, content_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        return marketing_text.write_copy(self, jwt, tenant_id, content_id, body)

    def update_content(self, jwt: str, tenant_id: str, content_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        c = self.ctx(jwt, tenant_id)
        before = self._content(c, content_id)
        row = super().update_content(jwt, tenant_id, content_id, body)
        return marketing_text.refresh_variants(self, c, before, body, row)

    def plan_content(self, jwt: str, tenant_id: str, body: Dict[str, Any], idempotency_key: Any = None) -> Dict[str, Any]:
        return self.planner().plan(jwt, tenant_id, body, idempotency_key)

    def autopilot(self) -> AutopilotService:
        return AutopilotService(self)

    def retry_content(self, jwt: str, tenant_id: str, content_id: str) -> Dict[str, Any]:
        return self.planner().retry(jwt, tenant_id, content_id)

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
        """Una sola cuota: el límite efectivo DEL MES DE PUBLICACIÓN (incluidas + ampliación vigente en ese ciclo,
        zona del tenant). Los límites técnicos por formato no forman parte del plan."""
        original = c.settings
        at = self._when(c, when) or cur.get("planned_at")
        limit = None
        if at:
            at_dt = datetime.fromisoformat(str(at).replace("Z", "+00:00"))
            limit = pp.effective_limit(self.db, c, pp.cycle_of(c, at_dt))
        c.settings = {**original, "monthly_post_limit": limit, "monthly_image_limit": None, "monthly_reel_limit": None}
        try:
            return super()._schedule_checks(c, cur, when)
        finally:
            c.settings = original

    def transition(self, jwt: str, tenant_id: str, content_id: str, target: str, comment: Any = None,
                   scheduled_at: Any = None) -> Dict[str, Any]:
        if target == "draft" and not str(comment or "").strip():
            c = self.ctx(jwt, tenant_id)
            if self._content(c, content_id)["status"] == "review":
                raise PortalError("comment_required", 400)            # Solicitar cambios exige un comentario
        try:
            return super().transition(jwt, tenant_id, content_id, target, comment, scheduled_at)
        except PortalError:
            raise
        except Exception as e:
            # La base rechazó por cuota (dos programaciones a la vez): mismo error público que el backend.
            if "limit_monthly_post_limit" in str(e):
                raise PortalError("limit_monthly_post_limit", 409)
            raise


def recommendations(counts: Dict[str, int], plan: Dict[str, Any], upcoming: int):
    """Recomendaciones simples de AITA (códigos; el panel los traduce). Nunca datos técnicos."""
    out = []
    if counts.get("ready_for_review"):
        out.append({"code": "review_ready", "count": counts["ready_for_review"]})
    if counts.get("changes_requested"):
        out.append({"code": "changes_pending", "count": counts["changes_requested"]})
    if counts.get("could_not_prepare"):
        out.append({"code": "retry_failed", "count": counts["could_not_prepare"]})
    available = plan.get("available")
    if available is not None and available <= 0:
        out.append({"code": "plan_full"})
    elif not upcoming:
        out.append({"code": "plan_week"})
    return out[:4]
