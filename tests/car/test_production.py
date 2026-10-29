import httpx
import pytest
from fastapi.testclient import TestClient
from car.production import create_production

A='11111111-1111-4111-8111-111111111111'
B='22222222-2222-4222-8222-222222222222'
def provider(req):
    if req.url.path.endswith('/token'):
        import json
        email=json.loads(req.content)['email']
        return httpx.Response(200,json={'access_token': 'a' if email=='a@test.com' else 'b','expires_in':3600})
    token=req.headers.get('authorization')
    if token not in ('Bearer a','Bearer b'): return httpx.Response(401,json={})
    return httpx.Response(200,json={'id':A if token=='Bearer a' else B})

@pytest.fixture
def app(tmp_path):
    return create_production(origin='https://car.test',auth_url='https://auth.test',auth_key='public-test',path=tmp_path/'car.sqlite',allowed_users=[A,B],transport=httpx.MockTransport(provider))

def test_secure_login_and_private_data(app):
    with TestClient(app,base_url='https://car.test',headers={'Origin':'https://car.test'}) as c:
        assert c.get('/api/car/dashboard').status_code==401
        assert c.post('/api/car/demo-login',json={'account':'demo-a'}).status_code==404
        r=c.post('/api/car/start',json={'email':'a@test.com','password':'password123'})
        assert r.status_code==200 and all(x in r.headers['set-cookie'] for x in ('Secure','HttpOnly','SameSite=strict','__Host-'))
        assert c.get('/api/car/dashboard').json()['vehicles']==[]
        r=c.post('/api/car/onboarding',json={'name':'Jorge','address':'Test address','phone':'5551234567','make':'Kia','model':'Sportage','year':2021,'initial_odometer':100,'distance_unit':'mi'})
        assert r.status_code==200
        vid=r.json()['vehicle']['id']
        assert c.post('/api/car/vehicles/'+vid+'/trips',json={'source':'simulated'}).status_code==409
        assert c.get('/api/car/vehicles/'+vid+'/obd').json()['source']=='unavailable'
        assert c.put('/api/car/demo-plan',json={'plan':'pro'}).status_code==200
        assert not c.get('/api/car/dashboard').json()['billing']['charges_enabled']
        c.post('/api/car/start',json={'email':'b@test.com','password':'password123'})
        assert c.get('/api/car/vehicles/'+vid).status_code==404
        c.post('/api/car/start',json={'email':'a@test.com','password':'password123'})
        assert c.get('/api/car/dashboard').json()['profile']['name']=='Jorge'
        assert c.post('/api/car/logout',json={}).status_code==200
        assert c.get('/api/car/dashboard').status_code==401

def test_origin_host_limits_and_no_secret_echo(app):
    with TestClient(app,base_url='https://car.test',headers={'Origin':'https://car.test'}) as c:
        assert c.get('/car',headers={'Host':'evil.test'}).status_code==403
        assert c.post('/api/car/start',json={},headers={'Origin':'https://evil.test'}).status_code==403
        r=c.post('/api/car/start',json={'password':'private'})
        assert r.status_code==422 and 'private' not in r.text
        for i in range(8): assert c.post('/api/car/start',json={'email':'a@test.com','password':'password123'}).status_code==200
        assert c.post('/api/car/start',json={'email':'a@test.com','password':'password123'}).status_code==429
        assert c.get('/api/car/runtime').json()=={'requires_login':True}

def test_configuration_fails_closed():
    with pytest.raises(RuntimeError): create_production(origin='http://car.test')
