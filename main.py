from fastapi import FastAPI, Request
from fastapi.responses import Response, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import logging
import os
import re
from pathlib import Path

# Importar servicios de Twilio
from services.twilio_service import TwilioWebhookHandler

# Configurar logging
logger = logging.getLogger(__name__)

app = FastAPI()

# Inicializar el manejador de Twilio
try:
    twilio_handler = TwilioWebhookHandler()
    logger.info("TwilioWebhookHandler inicializado correctamente")
except Exception as e:
    logger.error(f"Error inicializando TwilioWebhookHandler: {e}")
    twilio_handler = None

# Importar el servicio de Recepcionista IA
try:
    from recepcionista_service import RecepcionistaIAService
    recepcionista = RecepcionistaIAService()
except Exception as e:
    logger.error(f"Error al inicializar RecepcionistaIAService: {e}")
    recepcionista = None

# ---------------------------------------------------------------------------
# Registro de llamadas en Supabase (Manager Panel). NO bloqueante: si Supabase
# falla o no está configurado, Claudia sigue funcionando exactamente igual.
# ---------------------------------------------------------------------------
try:
    from services.call_logger import CallLogger
    call_logger = CallLogger()
except Exception as e:
    logger.error(f"CallLogger no disponible: {e}")
    call_logger = None

if recepcionista and call_logger:
    recepcionista.on_lead = lambda sesion_id, lead, numero: call_logger.lead_captured(sesion_id, lead, numero)

_ESTADOS_FINALES = {"completed", "busy", "no-answer", "failed", "canceled"}
_llamadas_cerradas = set()


def _registrar_inicio_llamada(call_data: dict) -> None:
    """Nunca lanza: Claudia tiene prioridad."""
    try:
        if not call_logger:
            return
        slug = None
        if twilio_handler:
            info = twilio_handler.phone_resolver.resolve(call_data.get("To", "")) or {}
            slug = info.get("tenant")
        call_logger.call_started(call_data, slug)
    except Exception as e:
        logger.error(f"registrar_inicio_llamada falló: {e}")


def _registrar_fin_llamada(call_data: dict) -> None:
    """Nunca lanza. El resumen se genera en el hilo de fondo."""
    try:
        call_sid = call_data.get("CallSid", "")
        if (not call_logger or not call_sid or call_sid in _llamadas_cerradas
                or call_data.get("CallStatus") not in _ESTADOS_FINALES):
            return
        _llamadas_cerradas.add(call_sid)
        historial = recepcionista.historial_de(call_sid) if recepcionista else []
        resumir = (lambda: recepcionista.generar_resumen(historial)) if (recepcionista and historial) else None
        call_logger.call_ended(call_data, resumir)
    except Exception as e:
        logger.error(f"registrar_fin_llamada falló: {e}")


# ---------------------------------------------------------------------------
# Manager Panel (/manager). La página es solo la "cáscara" (login + JS): no
# contiene datos. Los datos se piden a Supabase con el JWT del usuario y RLS
# los filtra por tenant. Al navegador solo llega la URL y la clave pública
# (anon); SUPABASE_SERVICE_ROLE_KEY nunca sale del servidor.
# ---------------------------------------------------------------------------
MANAGER_DIR = Path(__file__).parent / "manager"


def _manager_csp() -> str:
    supa = (os.getenv("SUPABASE_URL") or "").rstrip("/")
    ws = supa.replace("https://", "wss://") if supa else ""
    return (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data: blob:; "
        f"connect-src 'self' {supa} {ws}; "
        "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    )


@app.middleware("http")
async def manager_security_headers(request: Request, call_next):
    response = await call_next(request)
    p = request.url.path
    if (p.startswith("/manager") or p.startswith("/api/manager")
            or p == "/m" or p.startswith("/m/") or p.startswith("/api/member")):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = _manager_csp()
    return response


@app.get("/api/manager/config")
async def manager_config():
    """Configuración PÚBLICA del panel. Nunca incluir claves privadas aquí."""
    url = os.getenv("SUPABASE_URL")
    anon = os.getenv("SUPABASE_ANON_KEY")
    if not url or not anon:
        return JSONResponse({"error": "manager_not_configured"}, status_code=503)
    return {"supabaseUrl": url.rstrip("/"), "supabaseAnonKey": anon}


if MANAGER_DIR.is_dir():
    app.mount("/manager/assets", StaticFiles(directory=MANAGER_DIR / "assets"), name="manager-assets")

    @app.get("/manager")
    @app.get("/manager/")
    async def manager_index():
        return FileResponse(MANAGER_DIR / "index.html", media_type="text/html")


# ---------------------------------------------------------------------------
# Member Panel (/m): portal del socio. Igual que /manager, la página no trae
# datos; el socio entra con su QR/enlace personal o con enlace mágico por
# email y todo lo que ve pasa por RLS con su propio JWT.
# ---------------------------------------------------------------------------
MEMBER_DIR = Path(__file__).parent / "member"

try:
    from services.member_portal import MemberPortal, PortalError, public_base_url
    member_portal = MemberPortal()
except Exception as e:
    logger.error(f"MemberPortal no disponible: {e}")
    member_portal = None
    PortalError = Exception  # type: ignore

    def public_base_url(fallback=None):  # type: ignore
        raise RuntimeError("portal_not_configured")

import time as _time
from collections import defaultdict, deque
from starlette.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse

_portal_hits = defaultdict(deque)


def _rate_limited(key: str, limit: int = 20, window: float = 60.0) -> bool:
    now = _time.monotonic()
    q = _portal_hits[key]
    while q and now - q[0] > window:
        q.popleft()
    if len(q) >= limit:
        return True
    q.append(now)
    if len(_portal_hits) > 5000:
        _portal_hits.clear()
    return False


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "?")


def _base_url(request: Request) -> str:
    env = (os.getenv("PUBLIC_BASE_URL") or "").strip().rstrip("/")
    if env:
        return env
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    return f"{proto.split(',')[0].strip()}://{host.split(',')[0].strip()}"


def _bearer(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    return auth[7:].strip() if auth.lower().startswith("bearer ") else ""


def _portal_error(e: Exception) -> JSONResponse:
    if isinstance(e, PortalError) and hasattr(e, "code"):
        return JSONResponse({"error": e.code}, status_code=e.status)
    logger.error(f"PORTAL_ERROR {type(e).__name__}")
    return JSONResponse({"error": "server_error"}, status_code=500)


def _evaluation_invite_loop():
    """Revisa cada 6 h las evaluaciones vencidas (90 días) y envía la invitación.
    Solo si EVALUATION_INVITES_AUTO=1 y PUBLIC_BASE_URL está configurada."""
    import threading

    def loop():
        while True:
            try:
                res = member_portal.run_due_invitations()
                print(f"EVAL_INVITES {res}", flush=True)
            except Exception as e:
                logger.error(f"EVAL_INVITES_ERROR {type(e).__name__}")
            _time.sleep(6 * 3600)

    threading.Thread(target=loop, name="eval-invites", daemon=True).start()


@app.on_event("startup")
async def _start_background_jobs():
    if member_portal and os.getenv("EVALUATION_INVITES_AUTO") == "1":
        _evaluation_invite_loop()


@app.get("/m/q/{token}")
async def member_qr_page(token: str):
    """Enlace/QR personal. El GET NO inicia sesión ni cambia nada: solo sirve el
    portal, que muestra un botón "Entrar". Así los escáneres y las vistas
    previas de SMS/email (que solo hacen GET) no consumen ni activan el acceso."""
    if not MEMBER_DIR.is_dir():
        return JSONResponse({"error": "not_found"}, status_code=404)
    return FileResponse(MEMBER_DIR / "index.html", media_type="text/html")


@app.post("/api/member/qr-login")
async def member_qr_login(request: Request):
    """El socio pulsó "Entrar": valida la firma del enlace y devuelve un token de
    un solo uso para canjear en el navegador (verifyOtp)."""
    if _rate_limited("q:" + _client_ip(request)):
        return JSONResponse({"error": "rate_limited"}, status_code=429)
    if not member_portal:
        return JSONResponse({"error": "portal_not_configured"}, status_code=503)
    try:
        body = await request.json()
    except Exception:
        body = {}
    token = (body or {}).get("token") if isinstance(body, dict) else None
    try:
        hashed = await run_in_threadpool(member_portal.login_token_for, str(token or ""))
        return {"token_hash": hashed}
    except Exception as e:
        return _portal_error(e)


@app.post("/api/manager/members/{member_id}/portal-access")
async def manager_portal_access(member_id: str, request: Request):
    """Owner/manager: enviar acceso (SMS y/o email) y/o regenerar el QR."""
    if not member_portal:
        return JSONResponse({"error": "portal_not_configured"}, status_code=503)
    try:
        body = await request.json()
    except Exception:
        body = {}
    body = body if isinstance(body, dict) else {}
    try:
        _, member = await run_in_threadpool(member_portal.staff_member, _bearer(request), member_id)
        if body.get("regenerate"):
            member = await run_in_threadpool(member_portal.regenerate, member)
        sms, email = bool(body.get("sms")), bool(body.get("email"))
        if sms or email:
            result = await run_in_threadpool(member_portal.send_access, member, _base_url(request), sms, email)
        else:
            result = {"link": member_portal.link_for(member, _base_url(request)), "sms": None, "email": None}
        result["regenerated"] = bool(body.get("regenerate"))
        return result
    except Exception as e:
        return _portal_error(e)


@app.get("/api/manager/members/{member_id}/portal-link")
async def manager_portal_link(member_id: str, request: Request):
    if not member_portal:
        return JSONResponse({"error": "portal_not_configured"}, status_code=503)
    try:
        _, member = await run_in_threadpool(member_portal.staff_member, _bearer(request), member_id)
        return {
            "link": member_portal.link_for(member, _base_url(request)),
            "activated_at": member.get("portal_activated_at"),
            "invited_at": member.get("portal_invited_at"),
            "last_used_at": member.get("portal_last_used_at"),
            "last_sms": await run_in_threadpool(member_portal.last_sms, member["id"]),
        }
    except Exception as e:
        return _portal_error(e)


@app.post("/api/manager/members/{member_id}/evaluation-invite")
async def manager_evaluation_invite(member_id: str, request: Request):
    """Owner/manager: envía al socio el enlace seguro para completar su evaluación."""
    if not member_portal:
        return JSONResponse({"error": "portal_not_configured"}, status_code=503)
    try:
        body = await request.json()
    except Exception:
        body = {}
    body = body if isinstance(body, dict) else {}
    try:
        user, member = await run_in_threadpool(member_portal.staff_member, _bearer(request), member_id)
        return await run_in_threadpool(member_portal.send_evaluation_invite, member, _base_url(request),
                                       bool(body.get("sms")), bool(body.get("email")), user.get("id"), False)
    except Exception as e:
        return _portal_error(e)


@app.post("/api/manager/members/{member_id}/sms-refresh")
async def manager_sms_refresh(member_id: str, request: Request):
    """Consulta a Twilio el estado real del último SMS enviado a este socio."""
    if not member_portal:
        return JSONResponse({"error": "portal_not_configured"}, status_code=503)
    try:
        _, member = await run_in_threadpool(member_portal.staff_member, _bearer(request), member_id)
        rows = await run_in_threadpool(member_portal.db.select, "sms_messages", {
            "member_id": f"eq.{member['id']}", "message_sid": "not.is.null", "order": "created_at.desc",
            "limit": "1", "select": "message_sid"})
        if rows:
            await run_in_threadpool(member_portal.refresh_sms, rows[0]["message_sid"])
        return {"last_sms": await run_in_threadpool(member_portal.last_sms, member["id"])}
    except Exception as e:
        return _portal_error(e)


@app.get("/api/manager/sms-diagnostics")
async def manager_sms_diagnostics(request: Request):
    """Diagnóstico real de Twilio (tipo de cuenta, capacidad SMS del número, errores recientes)."""
    if not member_portal:
        return JSONResponse({"error": "portal_not_configured"}, status_code=503)
    try:
        user = await run_in_threadpool(member_portal.db.user_from_jwt, _bearer(request))
        if not user:
            raise PortalError("unauthorized", 401)
        rows = await run_in_threadpool(member_portal.db.select, "tenant_users", {
            "user_id": f"eq.{user['id']}", "active": "eq.true", "role": "in.(owner,manager)",
            "select": "tenant_id", "limit": "1"})
        if not rows:
            raise PortalError("forbidden", 403)
        return await run_in_threadpool(member_portal.sms_diagnostics, rows[0]["tenant_id"])
    except Exception as e:
        return _portal_error(e)


@app.post("/webhooks/twilio/sms-status")
async def twilio_sms_status(request: Request):
    """Callback de estado de Twilio. Solo se acepta con firma válida (X-Twilio-Signature)."""
    form = dict(await request.form())
    token = os.getenv("TWILIO_AUTH_TOKEN")
    try:
        url = f"{public_base_url(_base_url(request))}/webhooks/twilio/sms-status"
    except Exception:
        return Response(status_code=503)
    from twilio.request_validator import RequestValidator
    if not token or not RequestValidator(token).validate(url, form, request.headers.get("X-Twilio-Signature", "")):
        logger.warning("SMS_STATUS_REJECTED firma inválida")
        return Response(status_code=403)
    sid, status = form.get("MessageSid") or form.get("SmsSid"), form.get("MessageStatus") or form.get("SmsStatus")
    code = form.get("ErrorCode")
    print(f"SMS_STATUS sid=…{(sid or '')[-6:]} status={status} error={code}", flush=True)
    if member_portal and sid and status:
        try:
            await run_in_threadpool(member_portal.update_sms_status, sid, status,
                                    int(code) if code and str(code).isdigit() else None, form.get("ErrorMessage"))
        except Exception as e:
            logger.error(f"SMS_STATUS_ERROR {type(e).__name__}")
    return Response(status_code=204)


def _pdf_response(pdf: bytes, bundle) -> Response:
    ev = bundle["evaluation"]
    fname = f"evaluacion-{ev['kind']}-{str(ev.get('submitted_at') or '')[:10]}.pdf"
    return Response(pdf, media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="{fname}"', "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff"})


@app.get("/api/member/evaluations/{evaluation_id}/pdf")
async def member_evaluation_pdf(evaluation_id: str, request: Request, lang: str = "es"):
    if not member_portal:
        return JSONResponse({"error": "portal_not_configured"}, status_code=503)
    try:
        from services.evaluation_pdf import build_evaluation_pdf
        bundle = await run_in_threadpool(member_portal.evaluation_for_member, _bearer(request), evaluation_id)
        return _pdf_response(await run_in_threadpool(build_evaluation_pdf, bundle, lang), bundle)
    except Exception as e:
        return _portal_error(e)


@app.get("/api/manager/evaluations/{evaluation_id}/pdf")
async def manager_evaluation_pdf(evaluation_id: str, request: Request, lang: str = "en"):
    if not member_portal:
        return JSONResponse({"error": "portal_not_configured"}, status_code=503)
    try:
        from services.evaluation_pdf import build_evaluation_pdf
        bundle = await run_in_threadpool(member_portal.evaluation_for_staff, _bearer(request), evaluation_id)
        return _pdf_response(await run_in_threadpool(build_evaluation_pdf, bundle, lang), bundle)
    except Exception as e:
        return _portal_error(e)


# ---------------- Cuestionario subido (PDF rellenable, escaneo o fotos) ----------------
evaluation_docs = None
if member_portal:
    try:
        from services.evaluation_documents import EvaluationDocuments, MAX_FILE, MAX_FILES
        evaluation_docs = EvaluationDocuments(member_portal)
    except Exception as e:
        logger.error(f"EvaluationDocuments no disponible: {type(e).__name__}")


def _file_response(data: bytes, mime: str, name: str, download: bool) -> Response:
    disp = "attachment" if download else "inline"
    ascii_name = re.sub(r"[^\w.\-]+", "_", name)[:80] or "documento"
    return Response(data, media_type=mime, headers={
        "Content-Disposition": f'{disp}; filename="{ascii_name}"', "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'; sandbox"})


@app.get("/api/member/evaluation-form.pdf")
async def member_evaluation_form(request: Request, lang: str = "es"):
    """PDF rellenable del cuestionario original cargado por el gimnasio (409 si no hay)."""
    if not evaluation_docs:
        return JSONResponse({"error": "portal_not_configured"}, status_code=503)
    try:
        pdf = await run_in_threadpool(evaluation_docs.fillable_form, _bearer(request), lang)
        return _file_response(pdf, "application/pdf", "cuestionario.pdf", True)
    except Exception as e:
        return _portal_error(e)


@app.post("/api/member/evaluation-documents")
async def member_upload_document(request: Request):
    if not evaluation_docs:
        return JSONResponse({"error": "portal_not_configured"}, status_code=503)
    try:
        if int(request.headers.get("content-length") or 0) > MAX_FILE * MAX_FILES + 1_000_000:
            raise PortalError("file_too_large", 413)
        form = await request.form()
        files = []
        for f in form.getlist("files")[:MAX_FILES + 1]:
            if hasattr(f, "read"):
                data = await f.read(MAX_FILE + 1)
                files.append((getattr(f, "filename", "") or "", data))
        return await run_in_threadpool(evaluation_docs.upload, _bearer(request), files)
    except Exception as e:
        return _portal_error(e)


@app.get("/api/member/evaluation-documents/{doc_id}")
async def member_get_document(doc_id: str, request: Request):
    if not evaluation_docs:
        return JSONResponse({"error": "portal_not_configured"}, status_code=503)
    try:
        return await run_in_threadpool(evaluation_docs.get_for_member, _bearer(request), doc_id)
    except Exception as e:
        return _portal_error(e)


@app.post("/api/member/evaluation-documents/{doc_id}/retry")
async def member_retry_document(doc_id: str, request: Request):
    if not evaluation_docs:
        return JSONResponse({"error": "portal_not_configured"}, status_code=503)
    try:
        return await run_in_threadpool(evaluation_docs.retry, _bearer(request), doc_id)
    except Exception as e:
        return _portal_error(e)


@app.get("/api/member/evaluation-documents/{doc_id}/files/{n}")
async def member_document_file(doc_id: str, n: int, request: Request, download: int = 0):
    if not evaluation_docs:
        return JSONResponse({"error": "portal_not_configured"}, status_code=503)
    try:
        data, mime, name = await run_in_threadpool(evaluation_docs.file_for_member, _bearer(request), doc_id, n)
        return _file_response(data, mime, name, bool(download))
    except Exception as e:
        return _portal_error(e)


@app.get("/api/manager/evaluation-documents/{doc_id}/files/{n}")
async def manager_document_file(doc_id: str, n: int, request: Request, download: int = 0):
    if not evaluation_docs:
        return JSONResponse({"error": "portal_not_configured"}, status_code=503)
    try:
        data, mime, name = await run_in_threadpool(evaluation_docs.file_for_staff, _bearer(request), doc_id, n)
        return _file_response(data, mime, name, bool(download))
    except Exception as e:
        return _portal_error(e)


@app.get("/api/member/qr")
async def member_own_qr(request: Request):
    """El socio obtiene su propio enlace personal (para mostrar su QR)."""
    if not member_portal:
        return JSONResponse({"error": "portal_not_configured"}, status_code=503)
    try:
        member = await run_in_threadpool(member_portal.member_from_jwt, _bearer(request))
        return {"link": member_portal.link_for(member, _base_url(request))}
    except Exception as e:
        return _portal_error(e)


if MEMBER_DIR.is_dir():
    app.mount("/m/assets", StaticFiles(directory=MEMBER_DIR / "assets"), name="member-assets")

    @app.get("/m")
    @app.get("/m/")
    async def member_index():
        return FileResponse(MEMBER_DIR / "index.html", media_type="text/html")


@app.get("/")
async def root():
    return FileResponse(Path(__file__).parent / "index.html", media_type="text/html")

@app.post("/api/iniciar")
async def iniciar(request: Request):
    try:
        data = await request.json()
        sesion_id = data.get("sesion_id", "default")
        if recepcionista:
            return recepcionista.iniciar_sesion(sesion_id)
        return {"error": "Recepcionista no disponible"}
    except Exception as e:
        return {"error": str(e)}

@app.post("/api/mensaje")
async def mensaje(request: Request):
    try:
        data = await request.json()
        sesion_id = data.get("sesion_id")
        mensaje_texto = data.get("mensaje")
        
        if not sesion_id or not mensaje_texto:
            return {"error": "sesion_id y mensaje requeridos"}
        
        if recepcionista:
            respuesta = recepcionista.procesar_mensaje(sesion_id, mensaje_texto)
            return {"respuesta": respuesta}
        return {"error": "Recepcionista no disponible"}
    except Exception as e:
        return {"error": str(e)}

@app.post("/api/finalizar")
async def finalizar(request: Request):
    try:
        data = await request.json()
        sesion_id = data.get("sesion_id")
        if recepcionista:
            return recepcionista.finalizar_sesion(sesion_id)
        return {"error": "Recepcionista no disponible"}
    except Exception as e:
        return {"error": str(e)}

@app.post("/webhooks/twilio/voice")
async def twilio_voice_webhook(request: Request):
    """
    Webhook para llamadas entrantes de Twilio.
    Twilio envía datos en formato application/x-www-form-urlencoded
    con headers que incluyen X-Twilio-Signature.
    Responde con TwiML (XML).
    """
    try:
        form_data = await request.form()
        call_data = dict(form_data)
        
        signature = request.headers.get("X-Twilio-Signature", "")
        
        scheme = request.headers.get("X-Forwarded-Proto", "https")
        host = request.headers.get("X-Forwarded-Host") or request.url.netloc
        request_url = f"{scheme}://{host}{request.url.path}"
        
        logger.info(f"Webhook Twilio recibido: {request_url}")
        logger.debug(f"CallSid={call_data.get('CallSid')}, From={call_data.get('From')}, To={call_data.get('To')}")
        
        if not twilio_handler:
            logger.error("TwilioWebhookHandler no está inicializado")
            error_twiml = """<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="Polly.Lupe-Neural" language="es-US">Error: Sistema no configurado correctamente.</Say>
    <Hangup/>
</Response>"""
            return Response(content=error_twiml, status_code=500, media_type="application/xml")
        
        twiml_response, status_code, content_type = twilio_handler.handle_incoming_call(
            call_data=call_data,
            request_url=request_url,
            signature=signature,
            session_creator_callback=None
        )
        
        if status_code == 200:
            _registrar_inicio_llamada(call_data)

        return Response(
            content=twiml_response,
            status_code=status_code,
            media_type=content_type
        )
    
    except Exception as e:
        logger.error(f"Error en webhook Twilio: {e}", exc_info=True)
        error_twiml = """<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="Polly.Lupe-Neural" language="es-US">Error procesando su llamada. Por favor intente más tarde.</Say>
    <Hangup/>
</Response>"""
        return Response(
            content=error_twiml,
            status_code=500,
            media_type="application/xml"
        )

# CallSids a los que ya se envió el SMS de despedida (evita duplicados si Twilio reintenta)
_sms_despedida_enviados = set()

@app.post("/webhooks/twilio/status")
async def twilio_status_callback(request: Request):
    """
    Status callback de Twilio. Configurarlo en el número de Twilio
    (Voice -> "Call status changes"). Cuando la llamada termina (completed),
    se envía al cliente un SMS de agradecimiento con dirección, teléfono y web.
    """
    try:
        form_data = await request.form()
        call_data = dict(form_data)
        call_sid = call_data.get("CallSid", "")
        estado = call_data.get("CallStatus", "")

        # Validar que la petición viene de Twilio
        signature = request.headers.get("X-Twilio-Signature", "")
        scheme = request.headers.get("X-Forwarded-Proto", "https")
        host = request.headers.get("X-Forwarded-Host") or request.url.netloc
        request_url = f"{scheme}://{host}{request.url.path}"
        if twilio_handler and not twilio_handler.signature_validator.validate(request_url, call_data, signature):
            logger.warning(f"[{call_sid}] Status callback con firma inválida, ignorado")
            return Response(status_code=403)

        logger.info(f"[{call_sid}] Estado de llamada: {estado}")
        _registrar_fin_llamada(call_data)
        if (estado == "completed" and recepcionista and call_sid
                and call_sid not in _sms_despedida_enviados):
            _sms_despedida_enviados.add(call_sid)
            import threading
            threading.Thread(
                target=recepcionista.enviar_sms_despedida,
                args=(call_data.get("From", ""), call_data.get("To", "")),
                daemon=True,
            ).start()
            if recepcionista:
                recepcionista.finalizar_sesion(call_sid)
        return Response(status_code=204)
    except Exception as e:
        logger.error(f"Error en status callback: {e}", exc_info=True)
        return Response(status_code=204)

# Silencios consecutivos por llamada (para colgar si nadie habla)
_silencios = {}


def _twiml_despedida(texto: str) -> str:
    seguro = (texto or "¡Gracias por llamar a Golden Age Gym! Hasta pronto.")
    seguro = seguro.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="Polly.Lupe-Neural" language="es-US">{seguro}</Say>
    <Hangup/>
</Response>"""


@app.post("/api/twilio/mensaje")
async def twilio_mensaje_callback(request: Request):
    """
    Callback que Twilio invoca después de capturar input de voz con Gather.
    Procesa la entrada del usuario, integra con Claude, y devuelve TwiML con la respuesta.
    """
    try:
        form_data = await request.form()
        call_data = dict(form_data)

        call_sid = call_data.get("CallSid", "UNKNOWN")
        logger.info(f"[{call_sid}] CALLBACK GATHER - Parámetros: {list(call_data.keys())}")
        logger.debug(f"[{call_sid}] Datos completos de Twilio: {call_data}")

        speech_result = call_data.get("SpeechResult", "").strip()
        confidence = call_data.get("Confidence", "0")

        logger.info(f"[{call_sid}] SpeechResult: '{speech_result}' (Confianza: {confidence})")

        if not speech_result:
            logger.warning(f"[{call_sid}] No hay transcripción de voz, pidiendo que repita")
            _silencios[call_sid] = _silencios.get(call_sid, 0) + 1
            if _silencios[call_sid] >= 2:
                # Dos silencios seguidos: despedirse y colgar (evita llamadas "colgadas")
                _silencios.pop(call_sid, None)
                return Response(content=_twiml_despedida("Parece que no te escucho bien. Gracias por llamar a Golden Age Gym. ¡Hasta pronto!"),
                                status_code=200, media_type="application/xml")
            no_input_twiml = """<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="Polly.Lupe-Neural" language="es-US">No escuché tu pregunta. Por favor intenta de nuevo.</Say>
    <Gather
        input="speech"
        action="/api/twilio/mensaje"
        method="POST"
        language="es-US"
        speechModel="phone_call"
        enhanced="true"
        speechTimeout="auto"
        bargeIn="true"
    >
        <Say voice="Polly.Lupe-Neural" language="es-US">Adelante, te escucho.</Say>
    </Gather>
    <Say voice="Polly.Lupe-Neural" language="es-US">No recibimos respuesta. Adiós.</Say>
    <Hangup/>
</Response>"""
            return Response(content=no_input_twiml, status_code=200, media_type="application/xml")

        if not recepcionista:
            logger.error(f"[{call_sid}] RecepcionistaIAService no inicializado")
            error_twiml = """<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="Polly.Lupe-Neural" language="es-US">Sistema no disponible. Adiós.</Say>
    <Hangup/>
</Response>"""
            return Response(content=error_twiml, status_code=200, media_type="application/xml")

        session_id = f"{call_sid}"
        try:
            respuesta = recepcionista.procesar_mensaje(session_id, speech_result, call_data.get("From"), call_data.get("To"))
            logger.info(f"[{call_sid}] Respuesta de Claude: {respuesta[:100]}")
        except Exception as e:
            logger.error(f"[{call_sid}] Error procesando mensaje con Claude: {e}")
            respuesta = "Disculpa, hubo un error procesando tu mensaje. Por favor intenta de nuevo."

        _silencios.pop(call_sid, None)
        if recepcionista and recepcionista.debe_colgar(session_id):
            logger.info(f"[{call_sid}] Despedida: se cuelga la llamada")
            return Response(content=_twiml_despedida(respuesta), status_code=200, media_type="application/xml")

        safe_response = respuesta.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        response_twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Gather
        input="speech"
        action="/api/twilio/mensaje"
        method="POST"
        language="es-US"
        speechModel="phone_call"
        enhanced="true"
        speechTimeout="auto"
        bargeIn="true"
        actionOnEmptyResult="true"
    >
        <Say voice="Polly.Lupe-Neural" language="es-US">{safe_response}</Say>
    </Gather>
    <Say voice="Polly.Lupe-Neural" language="es-US">No recibimos respuesta. Adiós.</Say>
    <Hangup/>
</Response>"""

        logger.info(f"[{call_sid}] TwiML de continuación generado correctamente")
        return Response(content=response_twiml, status_code=200, media_type="application/xml")

    except Exception as e:
        logger.error(f"Error en callback Twilio /api/twilio/mensaje: {e}", exc_info=True)
        error_twiml = """<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="Polly.Lupe-Neural" language="es-US">Error procesando tu mensaje. Adiós.</Say>
    <Hangup/>
</Response>"""
        return Response(content=error_twiml, status_code=500, media_type="application/xml")
