from fastapi import FastAPI, Request
from fastapi.responses import Response
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
    logger.error(f"Error al inicializar RecepcionistaIAService: {e}")
    recepcionista = None

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
    <Say voice="Polly.Conchita" language="es-ES">Error: Sistema no configurado correctamente.</Say>
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
    <Say voice="Polly.Conchita" language="es-ES">Error procesando su llamada. Por favor intente más tarde.</Say>
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
            no_input_twiml = """<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="Polly.Conchita" language="es-ES">No escuché tu pregunta. Por favor intenta de nuevo.</Say>
    <Gather
        input="speech"
        action="/api/twilio/mensaje"
        method="POST"
        language="es-US"
        speechTimeout="auto"
        numDigits="1"
    >
        <Say voice="Polly.Conchita" language="es-ES">Adelante, te escucho.</Say>
    </Gather>
    <Say voice="Polly.Conchita" language="es-ES">No recibimos respuesta. Adiós.</Say>
    <Hangup/>
</Response>"""
            return Response(content=no_input_twiml, status_code=200, media_type="application/xml")

        if not recepcionista:
            logger.error(f"[{call_sid}] RecepcionistaIAService no inicializado")
            error_twiml = """<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="Polly.Conchita" language="es-ES">Sistema no disponible. Adiós.</Say>
    <Hangup/>
</Response>"""
            return Response(content=error_twiml, status_code=200, media_type="application/xml")

        session_id = f"{call_sid}"
        try:
            respuesta = recepcionista.procesar_mensaje(session_id, speech_result)
            logger.info(f"[{call_sid}] Respuesta de Claude: {respuesta[:100]}")
        except Exception as e:
            logger.error(f"[{call_sid}] Error procesando mensaje con Claude: {e}")
            respuesta = "Disculpa, hubo un error procesando tu mensaje. Por favor intenta de nuevo."

        safe_response = respuesta.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        response_twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="Polly.Conchita" language="es-ES">{safe_response}</Say>
    <Gather
        input="speech"
        action="/api/twilio/mensaje"
        method="POST"
        language="es-US"
        speechTimeout="auto"
        numDigits="1"
    >
        <Say voice="Polly.Conchita" language="es-ES">¿Hay algo más en lo que pueda ayudarte?</Say>
    </Gather>
    <Say voice="Polly.Conchita" language="es-ES">No recibimos respuesta. Adiós.</Say>
    <Hangup/>
</Response>"""

        logger.info(f"[{call_sid}] TwiML de continuación generado correctamente")
        return Response(content=response_twiml, status_code=200, media_type="application/xml")

    except Exception as e:
        logger.error(f"Error en callback Twilio /api/twilio/mensaje: {e}", exc_info=True)
        error_twiml = """<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="Polly.Conchita" language="es-ES">Error procesando tu mensaje. Adiós.</Say>
    <Hangup/>
</Response>"""
        return Response(content=error_twiml, status_code=500, media_type="application/xml")
