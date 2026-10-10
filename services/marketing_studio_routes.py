"""
Rutas HTTP de la Fase 2 de AITA Marketing: Biblioteca, Estudio de Reels y trabajos de generación.
Mismo patrón que services/marketing_routes.py: JWT de Supabase + tenant_id; la puerta de Marketing,
el rol owner/manager y el tenant se comprueban en el servicio. Errores = solo un código.
"""
import logging
from typing import Callable

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from services.marketing_domain import DomainError
from services.marketing_library import LibraryService
from services.marketing_media_files import HARD_MAX_BYTES
from services.marketing_studio import StudioService
from services.member_portal import PortalError

logger = logging.getLogger(__name__)
P = "/api/manager/marketing"


def build_router(get_db: Callable) -> APIRouter:
    r = APIRouter()

    def _db():
        db = get_db()
        if db is None:
            raise PortalError("portal_not_configured", 503)
        return db

    def lib() -> LibraryService:
        return LibraryService(_db())

    def studio() -> StudioService:
        return StudioService(_db())

    def bearer(request: Request) -> str:
        a = request.headers.get("authorization", "")
        return a[7:].strip() if a.lower().startswith("bearer ") else ""

    async def call(fn):
        try:
            return await run_in_threadpool(fn)
        except (PortalError, DomainError) as e:
            return JSONResponse({"error": e.code}, status_code=e.status)
        except Exception as e:
            logger.error(f"MARKETING_STUDIO_ERROR {type(e).__name__}")       # sin detalles ni URLs
            return JSONResponse({"error": "server_error"}, status_code=500)

    async def body(request: Request) -> dict:
        try:
            data = await request.json()
        except Exception:
            data = None
        return data if isinstance(data, dict) else {}

    def tid(b: dict) -> str:
        return str(b.get("tenant_id") or "")

    # ---------- Biblioteca ----------
    @r.get(f"{P}/library")
    async def library(request: Request, tenant_id: str = "", status: str = ""):
        return await call(lambda: lib().library(bearer(request), tenant_id, status))

    @r.post(f"{P}/library")
    async def upload(request: Request):
        if int(request.headers.get("content-length") or 0) > HARD_MAX_BYTES + 200_000:
            return JSONResponse({"error": "file_too_large"}, status_code=413)
        try:
            form = await request.form()
            f = form.get("file")
            data = await f.read(HARD_MAX_BYTES + 1) if hasattr(f, "read") else b""
            name = getattr(f, "filename", "") or ""
            mime = getattr(f, "content_type", "") or ""
            tenant = str(form.get("tenant_id") or "")
        except Exception:
            return JSONResponse({"error": "invalid_upload"}, status_code=400)
        return await call(lambda: lib().upload(bearer(request), tenant, name, mime, data))

    @r.get(f"{P}/library/{{media_id}}/preview")
    async def preview(request: Request, media_id: str, tenant_id: str = "", derivative_id: str = ""):
        res = await call(lambda: lib().preview(bearer(request), tenant_id, media_id, derivative_id))
        if isinstance(res, dict):
            return JSONResponse(res, headers={"Cache-Control": "no-store"})
        return res

    @r.patch(f"{P}/library/{{media_id}}/privacy")
    async def classify(request: Request, media_id: str):
        b = await body(request)
        return await call(lambda: lib().classify(bearer(request), tid(b), media_id, b))

    @r.post(f"{P}/library/{{media_id}}/archive")
    async def archive(request: Request, media_id: str):
        b = await body(request)
        return await call(lambda: lib().set_archived(bearer(request), tid(b), media_id, b.get("archived") is not False))

    @r.post(f"{P}/library/{{media_id}}/anonymize")
    async def anonymize(request: Request, media_id: str):
        b = await body(request)
        return await call(lambda: lib().anonymize(bearer(request), tid(b), media_id, str(b.get("method") or "")))

    @r.post(f"{P}/library/derivatives/{{derivative_id}}/review")
    async def review_derivative(request: Request, derivative_id: str):
        b = await body(request)
        return await call(lambda: lib().review_derivative(bearer(request), tid(b), derivative_id,
                                                          b.get("approve") is True, b.get("confirm_reviewed") is True))

    # ---------- Estudio de Reels ----------
    @r.get(f"{P}/studio")
    async def overview(request: Request, tenant_id: str = ""):
        return await call(lambda: studio().overview(bearer(request), tenant_id))

    @r.post(f"{P}/studio/mix-preview")
    async def mix_preview(request: Request):
        b = await body(request)
        return await call(lambda: studio().preview_mix(bearer(request), tid(b), b))

    @r.get(f"{P}/jobs")
    async def jobs(request: Request, tenant_id: str = ""):
        return await call(lambda: studio().jobs(bearer(request), tenant_id))

    @r.post(f"{P}/jobs")
    async def create_job(request: Request):
        b = await body(request)
        return await call(lambda: studio().create_job(bearer(request), tid(b), b))

    @r.get(f"{P}/jobs/{{job_id}}")
    async def job(request: Request, job_id: str, tenant_id: str = ""):
        return await call(lambda: studio().job(bearer(request), tenant_id, job_id))

    @r.post(f"{P}/jobs/{{job_id}}/{{action}}")
    async def job_action(request: Request, job_id: str, action: str):
        b = await body(request)
        t, jw = tid(b), bearer(request)
        actions = {
            "estimate": lambda s: s.estimate_job(jw, t, job_id),
            "approve": lambda s: s.approve_job(jw, t, job_id, b.get("confirm") is True),
            "reopen": lambda s: s.reopen_job(jw, t, job_id),
            "cancel": lambda s: s.cancel_job(jw, t, job_id),
            "process": lambda s: s.process_job(jw, t, job_id),
            "send-to-approval": lambda s: s.send_to_approval(jw, t, job_id, str(b.get("title") or "")),
        }
        if action not in actions:
            return JSONResponse({"error": "not_found"}, status_code=404)
        return await call(lambda: actions[action](studio()))

    return r
