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
