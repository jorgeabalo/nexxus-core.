"""Fail-closed signed Alexa endpoint and explicit account-link consent."""
import asyncio
import base64
import html
import json
import secrets
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlencode
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from .alexa import CarAlexa


def verify_signature(headers, raw):
    from ask_sdk_webservice_support.verifier import RequestVerifier
    class BoundedVerifier(RequestVerifier):
        def _load_cert_chain(self, cert_url):
            import httpx
            with httpx.Client(timeout=1.5, follow_redirects=False, trust_env=False) as client:
                with client.stream('GET', cert_url) as response:
                    response.raise_for_status()
                    certificate = bytearray()
                    for chunk in response.iter_bytes():
                        certificate.extend(chunk)
                        if len(certificate)>65536: raise ValueError('Certificate too large')
                    return bytes(certificate)
    BoundedVerifier().verify(headers,raw,None)


def validate_envelope(envelope, skill_id):
    try:
        system=envelope['context']['System']
        if not secrets.compare_digest(system['application']['applicationId'],skill_id): raise ValueError()
        uid=system['user']['userId']
        if not isinstance(uid,str) or not uid: raise ValueError()
        session=envelope.get('session')
        if session and (session['application']['applicationId']!=skill_id or session['user']['userId']!=uid): raise ValueError()
        req=envelope['request']
        stamp=datetime.fromisoformat(req['timestamp'].replace('Z','+00:00'))
        if stamp.tzinfo is None or abs((datetime.now(timezone.utc)-stamp).total_seconds())>150: raise ValueError()
        if not isinstance(req['requestId'],str) or not 1<=len(req['requestId'])<=512: raise ValueError()
        if req['type'] not in ('LaunchRequest','IntentRequest','SessionEndedRequest'): raise ValueError()
    except (KeyError,TypeError,ValueError,AttributeError):
        raise HTTPException(400,'Solicitud Alexa inválida') from None


def alexa_router(store,identity,link,skill_id,assistant=None):
    router=APIRouter()
    skill=CarAlexa(store,link.verify,assistant=assistant)
    @router.post('/api/car/alexa')
    async def alexa(request:Request):
        raw=await request.body()
        try:
            envelope=json.loads(raw)
            validate_envelope(envelope,skill_id)
            await asyncio.wait_for(asyncio.to_thread(verify_signature,dict(request.headers),raw.decode('utf-8')),2.5)
        except HTTPException: raise
        except ImportError: raise HTTPException(503,'Verificador Alexa no disponible') from None
        except Exception: raise HTTPException(400,'Firma Alexa inválida') from None
        if envelope['request']['type']=='SessionEndedRequest': return {'version':'1.0','response':{}}
        return skill.handle(envelope)

    def parameters(params):
        try:
            link.validate(params.get('client_id'),params.get('redirect_uri'),params.get('scope'),params.get('response_type'))
            state=params.get('state','')
            if not 1<=len(state)<=2048: raise ValueError()
        except ValueError: raise HTTPException(400,'Solicitud de vinculación inválida') from None
        return {key:params[key] for key in ('client_id','redirect_uri','scope','response_type','state')}

    @router.get('/alexa/authorize')
    def consent(request:Request):
        params=parameters(request.query_params)
        inputs=''.join('<input type="hidden" name="'+key+'" value="'+html.escape(value,quote=True)+'">' for key,value in params.items())
        return HTMLResponse('<!doctype html><html lang="es"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Vincular Alexa — AITA Car</title><h1>Vincular Alexa con AITA Car</h1><p>Alexa podrá consultar mantenimiento, gastos y ubicación de estacionamiento de tu vehículo principal y registrar gastos con tu confirmación. Amazon recibirá las consultas y respuestas de voz. No permite cobros ni controlar el carro.</p><p>Primero <a href="/car" target="_blank" rel="noopener">inicia sesión en AITA Car</a>; después vuelve aquí.</p><form method="post" action="/alexa/authorize">'+inputs+'<button type="submit">Autorizar Alexa durante 30 días</button></form><p>Puedes cancelar cerrando esta página. Para revocar el acceso, abre <a href="/alexa/manage">conexión Alexa</a>.</p></html>')

    @router.post('/alexa/authorize')
    async def authorize(request:Request,tenant=Depends(identity)):
        try: parsed=parse_qs((await request.body()).decode(),strict_parsing=True)
        except (ValueError,UnicodeError): raise HTTPException(400,'Parámetros inválidos') from None
        if any(len(values)!=1 for values in parsed.values()): raise HTTPException(400,'Parámetros inválidos')
        params=parameters({key:value[0] for key,value in parsed.items()})
        code=link.authorize(tenant,params['redirect_uri'])
        uri=params['redirect_uri']+('&' if '?' in params['redirect_uri'] else '?')+urlencode({'code':code,'state':params['state']})
        return RedirectResponse(uri,status_code=303)

    @router.post('/alexa/token')
    async def token(request:Request):
        try:
            auth=request.headers.get('authorization','')
            if not auth.startswith('Basic '): raise ValueError()
            client,secret=base64.b64decode(auth[6:],validate=True).decode().split(':',1)
            parsed=parse_qs((await request.body()).decode(),strict_parsing=True)
            if any(len(values)!=1 for values in parsed.values()): raise ValueError()
            params={key:value[0] for key,value in parsed.items()}
            return link.exchange(client,secret,params.get('grant_type'),params.get('code',params.get('refresh_token','')),params.get('redirect_uri'))
        except (ValueError,UnicodeError,KeyError): raise HTTPException(400,'invalid_grant') from None

    @router.get('/alexa/manage')
    def manage(tenant=Depends(identity)):
        return HTMLResponse('<!doctype html><html lang="es"><title>Conexión Alexa</title><h1>Conexión Alexa</h1><form method="post" action="/alexa/revoke"><button>Revocar acceso de Alexa</button></form><a href="/car">Volver a AITA Car</a></html>')

    @router.post('/alexa/revoke')
    def revoke(tenant=Depends(identity)):
        link.revoke(tenant)
        return HTMLResponse('<h1>Acceso de Alexa revocado</h1><a href="/car">Volver a AITA Car</a>')
    return router
