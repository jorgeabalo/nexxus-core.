from fastapi import FastAPI, Request
from fastapi.responses import Response, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import logging
import os
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
        "img-src 'self' data:; "
        f"connect-src 'self' {supa} {ws}; "
        "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    )


@app.middleware("http")
async def manager_security_headers(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/manager") or request.url.path.startswith("/api/manager"):
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


@app.get("/")
async def root():
    return {"status": "ok"}

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
