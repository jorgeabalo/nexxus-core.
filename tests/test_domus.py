import asyncio
import json
from datetime import datetime, timezone, timedelta
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from domus import alexa
from domus.service import DomusService, Command, JarvisAdapter

@pytest.fixture
def client(monkeypatch):
    for k,v in {'DOMUS_ENABLED':'true','DOMUS_ALEXA_SKILL_ID':'test-skill','DOMUS_ALEXA_USER_IDS':'test-user','DOMUS_HOME_ID':'jorge-home'}.items():
        monkeypatch.setenv(k,v)
    app=FastAPI(); app.include_router(alexa.router)
    return TestClient(app)


def envelope(kind='LaunchRequest', intent=None):
    req={'type':kind,'timestamp':datetime.now(timezone.utc).isoformat(),'requestId':'test-request'}
    if intent: req['intent']=intent
    return {'context':{'System':{'application':{'applicationId':'test-skill'},'user':{'userId':'test-user'}}},'request':req}


def sign_ok(monkeypatch):
    monkeypatch.setattr(alexa,'verify_signature',lambda headers,raw: None)


def test_closed_by_default(client, monkeypatch):
    monkeypatch.delenv('DOMUS_ENABLED')
    assert client.post('/api/domus/alexa',json=envelope()).status_code==503


def test_launch_stop_end(client,monkeypatch):
    sign_ok(monkeypatch)
    assert 'Bienvenido' in client.post('/api/domus/alexa',json=envelope()).json()['response']['outputSpeech']['text']
    r=client.post('/api/domus/alexa',json=envelope('IntentRequest',{'name':'AMAZON.StopIntent'})).json()
    assert r['response']['shouldEndSession']
    assert client.post('/api/domus/alexa',json=envelope('SessionEndedRequest')).json()['response']=={}

@pytest.mark.parametrize('change',['skill','user','old','future','session','missing'])
def test_authorization(client,monkeypatch,change):
    def should_not_run(*a): raise AssertionError('must reject before signature network')
    monkeypatch.setattr(alexa,'verify_signature',should_not_run)
    e=envelope()
    if change=='skill': e['context']['System']['application']['applicationId']='wrong'
    if change=='user': e['context']['System']['user']['userId']='wrong'
    if change in ('old','future'): e['request']['timestamp']=(datetime.now(timezone.utc)+timedelta(seconds=-151 if change=='old' else 151)).isoformat()
    if change=='session': e['session']={'application':{'applicationId':'wrong'},'user':{'userId':'test-user'}}
    if change=='missing': e={}
    assert client.post('/api/domus/alexa',json=e).status_code==(400 if change in ('old','future') else 403)


def test_signature_rejection(client,monkeypatch):
    def reject(*a): raise ValueError('synthetic invalid signature')
    monkeypatch.setattr(alexa,'verify_signature',reject)
    assert client.post('/api/domus/alexa',json=envelope()).status_code==400


def test_real_verifier_missing_signature(client):
    assert client.post('/api/domus/alexa',json=envelope()).status_code==400


def test_body_limit(client):
    assert client.post('/api/domus/alexa',content=b'x'*32769).status_code==413
    assert client.post('/api/domus/alexa',content=b'{broken').status_code==400


def test_jarvis_dispatch_home_from_server(client,monkeypatch):
    sign_ok(monkeypatch)
    class Jarvis:
        async def execute(self,c):
            assert c.home_id=='jorge-home'; assert c.text=='qué tengo pendiente'
            return 'No tienes tareas pendientes.'
    monkeypatch.setattr(alexa,'service',DomusService(Jarvis()))
    e=envelope('IntentRequest',{'name':'JarvisCommandIntent','slots':{'command':{'value':'qué tengo pendiente'}}})
    e['sessionAttributes']={'home_id':'golden_age'}
    assert 'No tienes' in client.post('/api/domus/alexa',json=e).json()['response']['outputSpeech']['text']


def test_domestic_does_not_call_jarvis():
    class NeverJarvis:
        async def execute(self,c): raise AssertionError('Domestic must bypass Jarvis')
    service=DomusService(NeverJarvis())
    result=asyncio.run(service.dispatch('DomesticCommandIntent',Command('home','apagar','req'),'tv'))
    assert 'No he ejecutado' in result


def test_jarvis_missing_configuration(monkeypatch):
    monkeypatch.delenv('DOMUS_JARVIS_URL',raising=False)
    assert 'todavía no está conectado' in asyncio.run(JarvisAdapter().execute(Command('home','consulta','req')))


def test_sdk_signature_and_tamper(monkeypatch):
    import base64
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa, padding
    from ask_sdk_webservice_support.verifier import RequestVerifier
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'echo-api.amazon.com')])
    now=datetime.now(timezone.utc)
    cert=(x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
          .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=1))
          .not_valid_after(now+timedelta(minutes=5))
          .add_extension(x509.SubjectAlternativeName([x509.DNSName('echo-api.amazon.com')]),critical=False)
          .sign(key,hashes.SHA256()))
    # Synthetic cert: replace trust chain only, keep SDK SAN/date/signature checks.
    monkeypatch.setattr(RequestVerifier,'_load_cert_chain',lambda *a: cert.public_bytes(serialization.Encoding.PEM))
    monkeypatch.setattr(RequestVerifier,'_validate_cert_chain',lambda *a: None)
    raw=json.dumps(envelope())
    signature=base64.b64encode(key.sign(raw.encode(),padding.PKCS1v15(),hashes.SHA256())).decode()
    headers={'SignatureCertChainUrl':'https://s3.amazonaws.com/echo.api/test.pem','Signature-256':signature}
    verifier=RequestVerifier(); verifier.verify(headers,raw,None)
    with pytest.raises(Exception): verifier.verify(headers,raw+' ',None)
    for url in ['http://s3.amazonaws.com/echo.api/x','https://evil.example/echo.api/x','https://s3.amazonaws.com/private/x']:
        with pytest.raises(Exception): verifier.verify({**headers,'SignatureCertChainUrl':url},raw,None)


def test_jarvis_http_contract_and_error(monkeypatch):
    import httpx
    monkeypatch.setenv('DOMUS_JARVIS_URL','https://jarvis.example/command')
    monkeypatch.setenv('DOMUS_JARVIS_TOKEN','synthetic-test-token')
    real_client=httpx.AsyncClient
    seen=[]
    def handler(request):
        seen.append(json.loads(request.content))
        assert request.headers['Authorization']=='Bearer synthetic-test-token'
        return httpx.Response(200,json={'speech':'Respuesta de Jarvis.'})
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw: real_client(**kw,transport=httpx.MockTransport(handler)))
    assert asyncio.run(JarvisAdapter().execute(Command('home','consulta','request')))=='Respuesta de Jarvis.'
    assert seen[0]['mode']=='read_only' and seen[0]['home_id']=='home'
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw: real_client(**kw,transport=httpx.MockTransport(lambda req: httpx.Response(500))))
    assert 'no está disponible' in asyncio.run(JarvisAdapter().execute(Command('home','consulta','request')))


def test_domestic_alexa_action_slot(client,monkeypatch):
    sign_ok(monkeypatch)
    e=envelope('IntentRequest',{'name':'DomesticCommandIntent','slots':{
        'action':{'value':'apagar'},
        'device':{'value':'televisión','resolutions':{'resolutionsPerAuthority':[
            {'status':{'code':'ER_SUCCESS_MATCH'},'values':[{'value':{'id':'tv'}}]}
        ]}}
    }})
    r=client.post('/api/domus/alexa',json=e)
    assert r.status_code==200
    assert 'No he ejecutado' in r.json()['response']['outputSpeech']['text']
