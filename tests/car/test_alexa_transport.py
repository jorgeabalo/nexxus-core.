import base64
from datetime import datetime,timezone,timedelta
import httpx
from fastapi.testclient import TestClient
from car.production import create_production
from car.alexa_transport import validate_envelope
from car.alexa_transport import verify_signature
from fastapi import HTTPException
import pytest

USER='11111111-1111-4111-8111-111111111111'
URI='https://pitangui.amazon.com/api/skill/link/test'
SKILL='amzn1.ask.skill.test'
def test_real_sdk_rejects_missing_and_foreign_certificate(monkeypatch):
    pytest.importorskip('ask_sdk_webservice_support')
    def no_network(*args,**kwargs):raise AssertionError('Untrusted URL must not be fetched')
    monkeypatch.setattr('httpx.Client',no_network)
    with pytest.raises(Exception,match='Missing Signature'):
        verify_signature({},'{}')
    with pytest.raises(Exception,match='hostname'):
        verify_signature({'signaturecertchainurl':'https://evil.test/cert','signature-256':'fake'},'{}')
def envelope():
    return {'context':{'System':{'application':{'applicationId':SKILL},'user':{'userId':'amazon-user'}}},'request':{'type':'LaunchRequest','requestId':'1','timestamp':datetime.now(timezone.utc).isoformat()}}

def test_envelope_skill_timestamp_and_session():
    e=envelope();validate_envelope(e,SKILL)
    with pytest.raises(HTTPException):validate_envelope(e,SKILL+'wrong')
    e['request']['timestamp']=(datetime.now(timezone.utc)-timedelta(minutes=3)).isoformat()
    with pytest.raises(HTTPException):validate_envelope(e,SKILL)
    e=envelope();e['session']={'application':{'applicationId':SKILL},'user':{'userId':'other'}}
    with pytest.raises(HTTPException):validate_envelope(e,SKILL)

def test_oauth_and_signed_endpoint(tmp_path,monkeypatch):
    for key,val in {'ENABLED':'true','SKILL_ID':SKILL,'CLIENT_ID':'client','CLIENT_SECRET':'x'*32,'REDIRECTS':URI}.items():
        monkeypatch.setenv('AITA_CAR_ALEXA_'+key,val)
    def provider(req):
        return httpx.Response(200,json={'id':USER})
    app=create_production(origin='https://car.test',auth_url='https://auth.test',auth_key='public',path=tmp_path/'db',allowed_users=[USER],transport=httpx.MockTransport(provider))
    with TestClient(app,base_url='https://car.test') as c:
        params={'client_id':'client','redirect_uri':URI,'scope':'car','response_type':'code','state':'original'}
        assert c.get('/alexa/authorize',params={**params,'redirect_uri':'https://evil.test'}).status_code==400
        consent=c.get('/alexa/authorize',params=params)
        assert consent.status_code==200 and 'Amazon' in consent.text
        assert c.post('/alexa/authorize',data=params).status_code==403
        assert c.post('/alexa/authorize',data=params,headers={'Origin':'https://car.test'}).status_code==401
        c.cookies.set('__Host-aita-car','test',domain='car.test',path='/')
        r=c.post('/alexa/authorize',data=params,headers={'Origin':'https://car.test'},follow_redirects=False)
        assert r.status_code==303
        from urllib.parse import parse_qs,urlsplit
        query=parse_qs(urlsplit(r.headers['location']).query)
        assert query['state']==['original']
        body={'grant_type':'authorization_code','code':query['code'][0],'redirect_uri':URI}
        assert c.post('/alexa/token',data=body).status_code==400
        auth={'Authorization':'Basic '+base64.b64encode(('client:'+('x'*32)).encode()).decode()}
        data=c.post('/alexa/token',data=body,headers=auth).json()
        e=envelope();e['context']['System']['user']['accessToken']=data['access_token']
        # Signature failure never dispatches even with a valid linked token.
        def invalid(*args):raise ValueError()
        monkeypatch.setattr('car.alexa_transport.verify_signature',invalid)
        assert c.post('/api/car/alexa',json=e).status_code==400
        monkeypatch.setattr('car.alexa_transport.verify_signature',lambda *args:None)
        assert c.post('/api/car/alexa',json=e).status_code==200
        assert c.post('/api/car/alexa',content='x'*32769).status_code==413
        assert c.post('/alexa/revoke',headers={'Origin':'https://car.test'}).status_code==200
        assert c.post('/api/car/alexa',json=e).json()['response']['card']['type']=='LinkAccount'
        assert c.post('/api/car/logout',headers={'Origin':'https://evil.test'}).status_code==403
