"""Signed Alexa HTTPS endpoint. Closed unless explicitly configured."""
import asyncio
import json
import os
import secrets
from datetime import datetime, timezone
from fastapi import APIRouter, Request, HTTPException
from .service import Command, DomusService

router = APIRouter(prefix='/api/domus', tags=['domus'])
service = DomusService()
MAX_BODY = 32768


def speech(text: str, end=False):
    # PlainText avoids interpreting Jarvis output as SSML.
    response = {'outputSpeech': {'type': 'PlainText', 'text': text[:2000]}, 'shouldEndSession': end}
    if not end:
        response['reprompt'] = {'outputSpeech': {'type': 'PlainText', 'text': '¿Qué quieres consultar o controlar?'}}
    return {'version': '1.0', 'response': response}


def verify_signature(headers, raw):
    # Lazy import keeps existing Nexxus startup independent of optional ASK libraries.
    from ask_sdk_webservice_support.verifier import RequestVerifier
    class BoundedVerifier(RequestVerifier):
        def _load_cert_chain(self, cert_url):
            # Parent verifies the allowed Amazon URL before this method.
            # Disable redirects and proxies; bound download time and size.
            import httpx
            with httpx.Client(timeout=1.5, follow_redirects=False, trust_env=False) as client:
                with client.stream('GET', cert_url) as response:
                    response.raise_for_status()
                    certificate = bytearray()
                    for chunk in response.iter_bytes():
                        certificate.extend(chunk)
                        if len(certificate) > 65536:
                            raise ValueError('Certificate too large')
                    return bytes(certificate)
    BoundedVerifier().verify(headers, raw, None)


def authorize(envelope, skill_id, allowed_users):
    try:
        system = envelope['context']['System']
        if not secrets.compare_digest(system['application']['applicationId'], skill_id):
            raise ValueError('skill')
        if system['user']['userId'] not in allowed_users:
            raise ValueError('user')
        session = envelope.get('session')
        if session and (session['application']['applicationId'] != skill_id
                        or session['user']['userId'] != system['user']['userId']):
            raise ValueError('session')
        req = envelope['request']
        stamp = datetime.fromisoformat(req['timestamp'].replace('Z', '+00:00'))
        if stamp.tzinfo is None or abs((datetime.now(timezone.utc) - stamp).total_seconds()) > 150:
            raise ValueError('timestamp')
        if not isinstance(req['requestId'], str) or not req['requestId']:
            raise ValueError('requestId')
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        code = 400 if str(error) == 'timestamp' else 403
        raise HTTPException(code, 'Solicitud Alexa no autorizada') from None
    return req


@router.post('/alexa')
async def alexa(request: Request):
    skill_id = os.getenv('DOMUS_ALEXA_SKILL_ID', '')
    users = {u.strip() for u in os.getenv('DOMUS_ALEXA_USER_IDS', '').split(',') if u.strip()}
    home_id = os.getenv('DOMUS_HOME_ID', '')
    if os.getenv('DOMUS_ENABLED', '').lower() != 'true' or not skill_id or not users or not home_id:
        raise HTTPException(503, 'Domus no configurado')
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > MAX_BODY:
            raise HTTPException(413, 'Solicitud demasiado grande')
    try:
        serialized = raw.decode('utf-8')
        envelope = json.loads(serialized)
    except (ValueError, UnicodeError):
        raise HTTPException(400, 'JSON inválido') from None
    req = authorize(envelope, skill_id, users)
    try:
        await asyncio.wait_for(asyncio.to_thread(verify_signature, dict(request.headers), serialized), timeout=2.5)
    except ImportError:
        raise HTTPException(503, 'Verificador Alexa no instalado') from None
    except Exception:
        raise HTTPException(400, 'Firma Alexa inválida') from None
    kind = req.get('type')
    if kind == 'LaunchRequest':
        return speech('Bienvenido a AITA Domus. Soy Nexxus, tu asistente. Puedes consultarme o pedir una orden doméstica.')
    if kind == 'SessionEndedRequest':
        return {'version': '1.0', 'response': {}}
    if kind != 'IntentRequest':
        return speech('Esa solicitud todavía no está disponible.')
    intent = req.get('intent', {})
    name = intent.get('name')
    if name in ('AMAZON.StopIntent', 'AMAZON.CancelIntent'):
        return speech('Hasta luego.', True)
    if name == 'AMAZON.HelpIntent':
        return speech('Di: consulta a Nexxus qué tengo pendiente, o pide controlar un dispositivo.')
    slots = intent.get('slots', {})
    slot_name = 'action' if name == 'DomesticCommandIntent' else 'command'
    text = slots.get(slot_name, {}).get('value', '')
    if not isinstance(text, str) or not text.strip() or len(text) > 1000:
        return speech('Dime la orden que quieres enviar.')
    device_slot = slots.get('device', {})
    # Use only a successful custom slot resolution, never the spoken value as adapter key.
    device = ''
    for authority in device_slot.get('resolutions', {}).get('resolutionsPerAuthority', []):
        if authority.get('status', {}).get('code') == 'ER_SUCCESS_MATCH':
            values = authority.get('values', [])
            if len(values) == 1:
                device = values[0].get('value', {}).get('id', '')
                break
    command = Command(home_id=home_id, text=text.strip(), request_id=req['requestId'])
    try:
        answer = await asyncio.wait_for(service.dispatch(name, command, device), timeout=3.5)
    except Exception:
        answer = 'Domus no pudo completar la consulta. Inténtalo de nuevo.'
    return speech(answer)
