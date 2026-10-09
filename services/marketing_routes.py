"""
Rutas HTTP de AITA Marketing (/api/manager/marketing/*).

Mismo patrón que Contabilidad: el navegador envía su JWT de Supabase
(Authorization: Bearer) y el tenant_id; MarketingService comprueba rol
(owner/manager), tenant y reglas de dominio antes de tocar nada. Los errores
devuelven solo un código (nunca detalles internos ni secretos).
"""
import logging
from typing import Callable

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from services.marketing import MarketingService
from services.member_portal import PortalError

logger = logging.getLogger(__name__)
P = "/api/manager/marketing"


def build_router(get_db: Callable) -> APIRouter:
    r = APIRouter()

    def svc() -> MarketingService:
        db = get_db()
        if db is None:
            raise PortalError("portal_not_configured", 503)
        return MarketingService(db)

    def bearer(request: Request) -> str:
        a = request.headers.get("authorization", "")
        return a[7:].strip() if a.lower().startswith("bearer ") else ""

    async def call(fn):
        try:
            return await run_in_threadpool(fn)
        except PortalError as e:
            return JSONResponse({"error": e.code}, status_code=e.status)
        except Exception as e:
            logger.error(f"MARKETING_ERROR {type(e).__name__}")
            return JSONResponse({"error": "server_error"}, status_code=500)

    async def body(request: Request) -> dict:
        try:
            data = await request.json()
        except Exception:
            data = None
        return data if isinstance(data, dict) else {}

    def tid(b: dict) -> str:
        return str(b.get("tenant_id") or "")

    @r.get(f"{P}/dashboard")
    async def dashboard(request: Request, tenant_id: str = ""):
        return await call(lambda: svc().dashboard(bearer(request), tenant_id))

    # ---------- Brand Kit ----------
    @r.get(f"{P}/brand")
    async def brand(request: Request, tenant_id: str = ""):
        return await call(lambda: svc().brand(bearer(request), tenant_id))

    @r.put(f"{P}/brand")
    async def save_brand(request: Request):
        b = await body(request)
        return await call(lambda: svc().save_brand(bearer(request), tid(b), b))

    # ---------- campañas ----------
    @r.get(f"{P}/campaigns")
    async def campaigns(request: Request, tenant_id: str = ""):
        return await call(lambda: svc().campaigns(bearer(request), tenant_id))

    @r.post(f"{P}/campaigns")
    async def create_campaign(request: Request):
        b = await body(request)
        return await call(lambda: svc().save_campaign(bearer(request), tid(b), b))

    @r.patch(f"{P}/campaigns/{{campaign_id}}")
    async def update_campaign(request: Request, campaign_id: str):
        b = await body(request)
        return await call(lambda: svc().save_campaign(bearer(request), tid(b), b, campaign_id))

    # ---------- contenido ----------
    @r.get(f"{P}/content")
    async def content_list(request: Request, tenant_id: str = "", status: str = "", campaign_id: str = ""):
        return await call(lambda: svc().content_list(bearer(request), tenant_id, status, campaign_id))

    @r.post(f"{P}/content")
    async def create_content(request: Request):
        b = await body(request)
        return await call(lambda: svc().create_content(bearer(request), tid(b), b))

    @r.get(f"{P}/content/{{content_id}}")
    async def content(request: Request, content_id: str, tenant_id: str = ""):
        return await call(lambda: svc().content(bearer(request), tenant_id, content_id))

    @r.patch(f"{P}/content/{{content_id}}")
    async def update_content(request: Request, content_id: str):
        b = await body(request)
        return await call(lambda: svc().update_content(bearer(request), tid(b), content_id, b))

    @r.post(f"{P}/content/{{content_id}}/transition")
    async def transition(request: Request, content_id: str):
        b = await body(request)
        return await call(lambda: svc().transition(bearer(request), tid(b), content_id, str(b.get("to") or ""),
                                                   b.get("comment"), b.get("scheduled_at")))

    # ---------- calendario ----------
    @r.get(f"{P}/calendar")
    async def calendar(request: Request, tenant_id: str = "", start: str = "", end: str = ""):
        return await call(lambda: svc().calendar(bearer(request), tenant_id, start, end))

    return r
