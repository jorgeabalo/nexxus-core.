"""
Rutas HTTP de la Fase 2 de AITA Marketing: Biblioteca, Estudio de Reels y trabajos de generación.
Mismo patrón que services/marketing_routes.py: JWT de Supabase + tenant_id; la puerta de Marketing,
el rol owner/manager y el tenant se comprueban en el servicio. Errores = solo un código.
"""
import logging
from typing import Callable
from urllib.parse import unquote

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.concurrency import run_in_threadpool

from services.marketing_domain import DomainError
from services.marketing_library import LibraryService
from services.marketing_media_stream import COOKIE, MediaStreamService, stream_path
from services.marketing_studio import StudioService, public_job
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

    def streamer() -> MediaStreamService:
        return MediaStreamService(_db())

    def media_response(res, extra=None):
        headers = {**res["headers"], "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
                   "Content-Disposition": "inline", "Cross-Origin-Resource-Policy": "same-origin",
                   "Referrer-Policy": "no-referrer", **(extra or {})}
        mime = headers.pop("Content-Type")
        if res["chunks"] is None:
            return Response(status_code=res["status"], headers=headers, media_type=mime)
        return StreamingResponse(res["chunks"], status_code=res["status"], headers=headers, media_type=mime)

    def bearer(request: Request) -> str:
        a = request.headers.get("authorization", "")
        return a[7:].strip() if a.lower().startswith("bearer ") else ""

    def secure_cookie(request: Request) -> bool:
        """Secure en producción (HTTPS); sin Secure solo en desarrollo local."""
        return request.url.hostname not in ("localhost", "127.0.0.1", "testserver", "::1")

    def expire_stream_cookie(response, request: Request, media_id: str):
        response.set_cookie(COOKIE, "", max_age=0, expires=0, path=stream_path(media_id), httponly=True,
                            secure=secure_cookie(request), samesite="strict")
        return response

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
    async def upload(request: Request, tenant_id: str = ""):
        """Cuerpo = bytes del archivo (no multipart). El límite se calcula ANTES de leer nada y la lectura
        se corta en cuanto lo supera: nunca se carga en memoria más de lo permitido."""
        jw = bearer(request)
        limit = await call(lambda: lib().upload_limit(jw, tenant_id))
        if not isinstance(limit, int):
            return limit                                           # error (rol, módulo, almacenamiento…)
        declared = request.headers.get("content-length")
        if declared is not None and (not declared.isdigit() or int(declared) > limit):
            return JSONResponse({"error": "file_too_large"}, status_code=413)
        buf, size = bytearray(), 0
        try:
            async for chunk in request.stream():
                size += len(chunk)
                if size > limit:
                    return JSONResponse({"error": "file_too_large"}, status_code=413)
                buf.extend(chunk)
        except Exception:
            return JSONResponse({"error": "invalid_upload"}, status_code=400)
        name = unquote(request.headers.get("x-file-name", ""))[:300]
        mime = (request.headers.get("content-type") or "").split(";")[0].strip()
        return await call(lambda: lib().upload(jw, tenant_id, name, mime, bytes(buf)))

    @r.api_route(f"{P}/library/{{media_id}}/content", methods=["GET", "HEAD"])
    async def content(request: Request, media_id: str, tenant_id: str = "", derivative_id: str = ""):
        """Vista previa entregada por el backend (mismo origen, en trozos, con HEAD y Range): el navegador
        crea un blob o reproduce con seeking, y la CSP no necesita abrir media-src/img-src a otros dominios."""
        head = request.method == "HEAD"
        res = await call(lambda: lib().content(bearer(request), tenant_id, media_id, derivative_id,
                                               request.headers.get("range"), head))
        return media_response(res) if isinstance(res, dict) else res

    # ---------- reproducción con HTTP Range (sesión opaca en cookie, URL limpia) ----------
    @r.post(f"{P}/library/{{media_id}}/stream-session")
    async def stream_session(request: Request, media_id: str):
        b = await body(request)
        res = await call(lambda: streamer().issue_session(bearer(request), tid(b), media_id))
        if not isinstance(res, dict):
            return res
        out = JSONResponse({"url": res["url"], "max_age": res["max_age"], "mime": res["mime"]},
                           headers={"Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer"})
        out.set_cookie(COOKIE, res["token"], max_age=res["max_age"], path=res["path"], httponly=True,
                       secure=secure_cookie(request), samesite="strict")
        return out

    @r.post(f"{P}/library/{{media_id}}/stream-revoke")
    async def stream_revoke(request: Request, media_id: str):
        b = await body(request)
        res = await call(lambda: streamer().revoke(bearer(request), tid(b), media_id))
        if not isinstance(res, dict):
            return res
        return expire_stream_cookie(JSONResponse({"revoked": res["revoked"]}), request, media_id)

    @r.api_route(f"{P}/library/{{media_id}}/stream", methods=["GET", "HEAD"])
    async def stream(request: Request, media_id: str):
        """El <video> pide aquí HEAD y rangos con la cookie de sesión (sin token en la URL). Cada petición
        revalida la sesión (vigencia, revocación, archivo), usuario, rol, tenant, módulo, estado y ruta."""
        res = await call(lambda: streamer().stream(media_id, request.cookies.get(COOKIE), request.headers.get("range"),
                                                   request.method == "HEAD"))
        return media_response(res) if isinstance(res, dict) else res

    @r.patch(f"{P}/library/{{media_id}}/privacy")
    async def classify(request: Request, media_id: str):
        b = await body(request)
        return await call(lambda: lib().classify(bearer(request), tid(b), media_id, b))

    @r.post(f"{P}/library/{{media_id}}/archive")
    async def archive(request: Request, media_id: str):
        b = await body(request)
        return await call(lambda: lib().set_archived(bearer(request), tid(b), media_id, b.get("archived") is not False))

    @r.post(f"{P}/library/{{media_id}}/retention")
    async def retention(request: Request, media_id: str):
        b = await body(request)
        return await call(lambda: lib().set_retention(bearer(request), tid(b), media_id, b.get("days")))

    @r.post(f"{P}/library/{{media_id}}/revoke-consent")
    async def revoke_consent(request: Request, media_id: str):
        b = await body(request)
        res = await call(lambda: lib().revoke_consent(bearer(request), tid(b), media_id))
        return expire_stream_cookie(JSONResponse(res), request, media_id) if isinstance(res, dict) else res

    @r.post(f"{P}/library/{{media_id}}/delete")
    async def delete(request: Request, media_id: str):
        b = await body(request)
        res = await call(lambda: lib().delete(bearer(request), tid(b), media_id, str(b.get("reason") or ""),
                                              b.get("confirm") is True))
        return expire_stream_cookie(JSONResponse(res), request, media_id) if isinstance(res, dict) else res

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
        res = await call(lambda: studio().jobs(bearer(request), tenant_id))
        return {"items": [public_job(j) for j in res["items"]]} if isinstance(res, dict) else res

    @r.post(f"{P}/jobs")
    async def create_job(request: Request):
        b = await body(request)
        res = await call(lambda: studio().create_job(bearer(request), tid(b), b))
        return public_job(res) if isinstance(res, dict) else res

    @r.get(f"{P}/jobs/{{job_id}}")
    async def job(request: Request, job_id: str, tenant_id: str = ""):
        res = await call(lambda: studio().job(bearer(request), tenant_id, job_id))
        if not isinstance(res, dict):
            return res
        res.pop("usage", None)                                   # costos por modelo: solo internos
        return public_job(res)

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
        res = await call(lambda: actions[action](studio()))
        return public_job(res) if isinstance(res, dict) and "status" in res else res

    return r
