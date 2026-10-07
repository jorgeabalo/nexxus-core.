"""
Rutas HTTP de Contabilidad (/api/manager/accounting/*).

Mismo patrón que el resto del Manager Panel: el navegador envía su JWT de
Supabase (Authorization: Bearer) y el tenant_id; AccountingService comprueba
rol y tenant antes de tocar nada. Los errores devuelven solo un código
(nunca detalles internos ni secretos).
"""
import logging
import re
from typing import Callable

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from services.accounting import MAX_RECEIPT, AccountingService
from services.member_portal import PortalError

logger = logging.getLogger(__name__)
P = "/api/manager/accounting"


def build_router(get_db: Callable) -> APIRouter:
    r = APIRouter()

    def svc() -> AccountingService:
        db = get_db()
        if db is None:
            raise PortalError("portal_not_configured", 503)
        return AccountingService(db)

    def bearer(request: Request) -> str:
        a = request.headers.get("authorization", "")
        return a[7:].strip() if a.lower().startswith("bearer ") else ""

    async def call(fn, *args):
        try:
            return await run_in_threadpool(fn, *args)
        except PortalError as e:
            return JSONResponse({"error": e.code}, status_code=e.status)
        except Exception as e:
            logger.error(f"ACCOUNTING_ERROR {type(e).__name__}")
            return JSONResponse({"error": "server_error"}, status_code=500)

    async def body(request: Request) -> dict:
        try:
            data = await request.json()
        except Exception:
            data = None
        return data if isinstance(data, dict) else {}

    # ---------- resumen y movimientos ----------
    @r.get(f"{P}/summary")
    async def summary(request: Request, tenant_id: str = "", start: str = "", end: str = ""):
        return await call(lambda: svc().summary(bearer(request), tenant_id, start or None, end or None))

    @r.get(f"{P}/transactions")
    async def transactions(request: Request, tenant_id: str = "", start: str = "", end: str = "",
                           type: str = "", status: str = ""):
        return await call(lambda: svc().transactions(bearer(request), tenant_id, start or None, end or None, type, status))

    @r.post(f"{P}/transactions")
    async def create_transaction(request: Request):
        b = await body(request)
        return await call(lambda: svc().create_transaction(bearer(request), str(b.get("tenant_id") or ""), b))

    @r.patch(f"{P}/transactions/{{txn_id}}")
    async def update_transaction(request: Request, txn_id: str):
        b = await body(request)
        return await call(lambda: svc().update_transaction(bearer(request), str(b.get("tenant_id") or ""), txn_id, b))

    @r.post(f"{P}/transactions/{{txn_id}}/cancel")
    async def cancel_transaction(request: Request, txn_id: str):
        b = await body(request)
        return await call(lambda: svc().cancel_transaction(bearer(request), str(b.get("tenant_id") or ""), txn_id))

    # ---------- recibos ----------
    @r.post(f"{P}/transactions/{{txn_id}}/receipt")
    async def upload_receipt(request: Request, txn_id: str):
        if int(request.headers.get("content-length") or 0) > MAX_RECEIPT + 200_000:
            return JSONResponse({"error": "file_too_large"}, status_code=413)
        try:
            form = await request.form()
            f = form.get("file")
            data = await f.read(MAX_RECEIPT + 1) if hasattr(f, "read") else b""
            name, tenant = getattr(f, "filename", "") or "", str(form.get("tenant_id") or "")
        except Exception:
            return JSONResponse({"error": "invalid_upload"}, status_code=400)
        return await call(lambda: svc().upload_receipt(bearer(request), tenant, txn_id, name, data))

    @r.get(f"{P}/transactions/{{txn_id}}/receipt")
    async def get_receipt(request: Request, txn_id: str, tenant_id: str = ""):
        res = await call(lambda: svc().receipt(bearer(request), tenant_id, txn_id))
        if isinstance(res, Response):
            return res
        data, mime, name = res
        safe = re.sub(r"[^\w.\-]+", "_", name)
        return Response(data, media_type=mime, headers={
            "Content-Disposition": f'inline; filename="{safe}"',
            "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox"})

    # ---------- categorías ----------
    @r.get(f"{P}/categories")
    async def categories(request: Request, tenant_id: str = ""):
        return await call(lambda: svc().categories(bearer(request), tenant_id))

    @r.post(f"{P}/categories")
    async def create_category(request: Request):
        b = await body(request)
        return await call(lambda: svc().create_category(bearer(request), str(b.get("tenant_id") or ""), b))

    @r.patch(f"{P}/categories/{{cat_id}}")
    async def update_category(request: Request, cat_id: str):
        b = await body(request)
        return await call(lambda: svc().update_category(bearer(request), str(b.get("tenant_id") or ""), cat_id, b))

    # ---------- pendientes ----------
    @r.get(f"{P}/obligations")
    async def obligations(request: Request, tenant_id: str = "", status: str = "open"):
        return await call(lambda: svc().obligations(bearer(request), tenant_id, "all" if status == "all" else "open"))

    @r.post(f"{P}/obligations")
    async def create_obligation(request: Request):
        b = await body(request)
        return await call(lambda: svc().create_obligation(bearer(request), str(b.get("tenant_id") or ""), b))

    @r.patch(f"{P}/obligations/{{ob_id}}")
    async def update_obligation(request: Request, ob_id: str):
        b = await body(request)
        return await call(lambda: svc().update_obligation(bearer(request), str(b.get("tenant_id") or ""), ob_id, b))

    @r.post(f"{P}/obligations/{{ob_id}}/pay")
    async def pay_obligation(request: Request, ob_id: str):
        b = await body(request)
        return await call(lambda: svc().pay_obligation(bearer(request), str(b.get("tenant_id") or ""), ob_id, b))

    # ---------- exportación ----------
    @r.get(f"{P}/export")
    async def export(request: Request, tenant_id: str = "", start: str = "", end: str = "",
                     format: str = "csv", lang: str = "es"):
        from services.accounting_report import to_csv, to_pdf
        lang = lang if lang in ("es", "en") else "es"
        fmt = "pdf" if format == "pdf" else "csv"

        def build():
            rep = svc().report(bearer(request), tenant_id, start or None, end or None)
            return rep, (to_pdf(rep, lang) if fmt == "pdf" else to_csv(rep, lang))

        res = await call(build)
        if isinstance(res, Response):
            return res
        rep, data = res
        p = rep["summary"]["period"]
        fname = f"contabilidad-{p['from']}_{p['to']}.{fmt}"
        return Response(data, media_type="application/pdf" if fmt == "pdf" else "text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{fname}"', "Cache-Control": "no-store",
                                 "X-Content-Type-Options": "nosniff"})

    return r
