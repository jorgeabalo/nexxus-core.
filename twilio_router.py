"""Webhooks de voz de Twilio para NEXXUS.

Las credenciales nunca se guardan en el repositorio. Se requieren:
TWILIO_AUTH_TOKEN, y opcionalmente PUBLIC_BASE_URL y SUPERVISOR_PHONE_NUMBER.
"""

import os
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from twilio.request_validator import RequestValidator
from twilio.twiml.voice_response import VoiceResponse, Gather

from generic_service import NexxusIAService
from models import SessionLocal, Negocio

router = APIRouter(prefix="/webhooks/twilio", tags=["twilio"])
servicio_voz = NexxusIAService()

IDIOMAS_TWILIO = {
    "es": "es-MX",
    "en": "en-US",
    "pt": "pt-BR",
}


def _url_publica(request: Request) -> str:
    base = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
    if base:
        url = f"{base}{request.url.path}"
        if request.url.query:
            url += f"?{request.url.query}"
        return url
    return str(request.url)


def _validar_firma(request: Request, datos: dict) -> None:
    token = os.getenv("TWILIO_AUTH_TOKEN")
    if not token:
        raise HTTPException(status_code=503, detail="Twilio no configurado")
    firma = request.headers.get("X-Twilio-Signature", "")
    if not firma or not RequestValidator(token).validate(_url_publica(request), datos, firma):
        raise HTTPException(status_code=403, detail="Firma de Twilio inválida")


def _obtener_negocio(slug: str) -> Negocio:
    db = SessionLocal()
    try:
        negocio = db.query(Negocio).filter(Negocio.slug == slug, Negocio.activo.is_(True)).first()
        if not negocio:
            raise HTTPException(status_code=404, detail="Negocio no encontrado")
        db.expunge(negocio)
        return negocio
    finally:
        db.close()


def _xml(respuesta: VoiceResponse) -> Response:
    return Response(content=str(respuesta), media_type="application/xml")


def _escuchar(respuesta: VoiceResponse, slug: str, llamada_id: str, idioma: str, saludo: str = None) -> None:
    action = f"/webhooks/twilio/speech/{quote(slug, safe='')}?llamada_id={quote(llamada_id, safe='')}"
    gather = Gather(
        input="speech",
        action=action,
        method="POST",
        speech_timeout="auto",
        language=IDIOMAS_TWILIO.get(idioma, "es-MX"),
    )
    if saludo:
        gather.say(saludo, language=IDIOMAS_TWILIO.get(idioma, "es-MX"), voice="alice")
    respuesta.append(gather)
    respuesta.redirect(action, method="POST")


@router.post("/voice/{negocio_slug}")
async def llamada_entrante(negocio_slug: str, request: Request):
    form = await request.form()
    datos = dict(form)
    _validar_firma(request, datos)
    negocio = _obtener_negocio(negocio_slug)

    inicio = servicio_voz.iniciar_llamada(negocio.id, datos.get("From"))
    if "error" in inicio:
        raise HTTPException(status_code=400, detail=inicio["error"])

    respuesta = VoiceResponse()
    idioma = negocio.idioma_principal or "es"
    saludo = (
        f"Hola, gracias por llamar a {negocio.nombre}. "
        f"Soy {negocio.nombre_asistente}, su asistente virtual. ¿En qué puedo ayudarle?"
        if idioma == "es"
        else f"Hello, thank you for calling {negocio.nombre}. How may I help you?"
    )
    _escuchar(respuesta, negocio.slug, inicio["llamada_id"], idioma, saludo)
    return _xml(respuesta)


@router.post("/speech/{negocio_slug}")
async def procesar_voz(negocio_slug: str, llamada_id: str, request: Request):
    form = await request.form()
    datos = dict(form)
    _validar_firma(request, datos)
    negocio = _obtener_negocio(negocio_slug)
    texto = (datos.get("SpeechResult") or "").strip()

    respuesta = VoiceResponse()
    if not texto:
        _escuchar(
            respuesta,
            negocio.slug,
            llamada_id,
            negocio.idioma_principal or "es",
            "No pude escucharle. Por favor, repita.",
        )
        return _xml(respuesta)

    resultado = servicio_voz.procesar_mensaje(llamada_id, texto, negocio_id=negocio.id)
    if "error" in resultado:
        raise HTTPException(status_code=400, detail=resultado["error"])

    idioma = resultado.get("idioma") or negocio.idioma_principal or "es"
    respuesta.say(resultado["respuesta"], language=IDIOMAS_TWILIO.get(idioma, "es-MX"), voice="alice")

    if resultado.get("requiere_supervisor"):
        supervisor = os.getenv("SUPERVISOR_PHONE_NUMBER")
        if supervisor:
            respuesta.say("Le comunico con una persona.", language=IDIOMAS_TWILIO.get(idioma, "es-MX"), voice="alice")
            respuesta.dial(supervisor)
        else:
            respuesta.say("Un representante le devolverá la llamada.", language=IDIOMAS_TWILIO.get(idioma, "es-MX"), voice="alice")
            respuesta.hangup()
    else:
        _escuchar(respuesta, negocio.slug, llamada_id, idioma)

    return _xml(respuesta)


@router.post("/status/{negocio_slug}")
async def estado_llamada(negocio_slug: str, llamada_id: str, request: Request):
    form = await request.form()
    datos = dict(form)
    _validar_firma(request, datos)
    negocio = _obtener_negocio(negocio_slug)
    estado = datos.get("CallStatus", "")
    if estado in {"completed", "busy", "failed", "no-answer", "canceled"}:
        servicio_voz.finalizar_llamada(llamada_id, estado, negocio_id=negocio.id)
    return Response(status_code=204)
