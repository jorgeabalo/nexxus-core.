from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, Response
import uuid
import logging

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
    print(f"Error al inicializar RecepcionistaIAService: {e}")
    recepcionista = None

@app.get("/")
async def root():
    return {"status": "ok"}

@app.post("/api/iniciar")
async def iniciar(request: Request):
    try:
        data = await request.json()
        sesion_id = data.get("sesion_id", str(uuid.uuid4()))
        return recepcionista.iniciar_sesion(sesion_id)
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
        
        respuesta = recepcionista.procesar_mensaje(sesion_id, mensaje_texto)
        return {"respuesta": respuesta}
    except Exception as e:
        return {"error": str(e)}

@app.post("/api/finalizar")
async def finalizar(request: Request):
    try:
        data = await request.json()
        sesion_id = data.get("sesion_id")
        return recepcionista.finalizar_sesion(sesion_id)
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
        # Leer datos POST (form-urlencoded)
        form_data = await request.form()
        call_data = dict(form_data)
        
        # Obtener firma de Twilio
        signature = request.headers.get("X-Twilio-Signature", "")
        
        # Construir URL exacta
        # Construir URL correcta (forzar https en Railway)
        scheme = request.headers.get("X-Forwarded-Proto", "https")
        host = request.headers.get("X-Forwarded-Host") or request.url.netloc
        request_url = f"{scheme}://{host}{request.url.path}"
        
        logger.info(f"Webhook Twilio recibido: {request_url}")
        logger.debug(f"CallSid={call_data.get('CallSid')}, From={call_data.get('From')}, To={call_data.get('To')}")
        
        # Procesar con el manejador de Twilio
        if not twilio_handler:
            logger.error("TwilioWebhookHandler no está inicializado")
            error_twiml = """<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="alice" language="es-ES">Error: Sistema no configurado correctamente.</Say>
    <Hangup/>
</Response>"""
            return Response(content=error_twiml, status_code=500, media_type="application/xml")
        
        twiml_response, status_code, content_type = twilio_handler.handle_incoming_call(
            call_data=call_data,
            request_url=request_url,
            signature=signature,
            session_creator_callback=None
        )
        
        return Response(
            content=twiml_response,
            status_code=status_code,
            media_type=content_type
        )
    
    except Exception as e:
        logger.error(f"Error en webhook Twilio: {e}", exc_info=True)
        error_twiml = """<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="alice" language="es-ES">Error procesando su llamada. Por favor intente más tarde.</Say>
    <Hangup/>
</Response>"""
        return Response(
            content=error_twiml,
            status_code=500,
            media_type="application/xml"
        )

@app.post("/api/twilio/mensaje")
async def twilio_mensaje_callback(request: Request):
    """
    Callback que Twilio invoca después de grabar/transcribir.
    Procesa la entrada del usuario y devuelve TwiML con la respuesta.
    """
    try:
        # Leer datos POST (form-urlencoded de Twilio)
        form_data = await request.form()
        call_data = dict(form_data)
        
        call_sid = call_data.get("CallSid", "UNKNOWN")
        speech_result = call_data.get("SpeechResult", "")
        recording_url = call_data.get("RecordingUrl", "")
        
        logger.info(f"[{call_sid}] Callback Twilio: speech='{speech_result[:50]}'")
        logger.info(f"[{call_sid}] PARÁMETROS RECIBIDOS: {list(call_data.keys())}")
        
        if not speech_result and not recording_url:
            # No hay entrada del usuario
            no_input_twiml = """<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="alice" language="es-ES">No escuché tu pregunta. Por favor intenta de nuevo.</Say>
    <Record action="/api/twilio/mensaje" method="POST" maxLength="600" transcribe="true" transcribeCallback="/api/twilio/mensaje" playBeep="true"/>
    <Hangup/>
</Response>"""
            return Response(content=no_input_twiml, status_code=200, media_type="application/xml")
        
        # Procesar con Claudia
        if not recepcionista:
            logger.error(f"[{call_sid}] RecepcionistaIAService no inicializado")
            return Response(content="""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="alice" language="es-ES">Sistema no disponible.</Say>
    <Hangup/>
</Response>""", status_code=200, media_type="application/xml")
        session_id = f"{call_sid}"
        respuesta = recepcionista.procesar_mensaje(session_id, speech_result)
        
        logger.info(f"[{call_sid}] Respuesta Claudia: {respuesta[:100]}")
        
        # Generar TwiML con la respuesta
        safe_response = respuesta.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        response_twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="alice" language="es-ES">{safe_response}</Say>
    <Record action="/api/twilio/mensaje" method="POST" maxLength="600" transcribe="true" transcribeCallback="/api/twilio/mensaje" playBeep="true"/>
    <Hangup/>
</Response>"""
        
        return Response(content=response_twiml, status_code=200, media_type="application/xml")
        
    except Exception as e:
        logger.error(f"Error en callback Twilio: {e}", exc_info=True)
        error_twiml = """<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="alice" language="es-ES">Error procesando tu mensaje. Adiós.</Say>
    <Hangup/>
</Response>"""
        return Response(content=error_twiml, status_code=500, media_type="application/xml")
