import os, time
import pytest
from fastapi.testclient import TestClient
from fastapi import FastAPI
from car.demo import create_demo
from car.api import router
from car.auth import supabase_identity
from car.store import Store
from car.domain import miles

H={'Origin':'http://testserver'}
@pytest.fixture
def c(monkeypatch):
    monkeypatch.setenv('AITA_CAR_DEMO','1')
    with TestClient(create_demo(':memory:')) as c:
        c.headers.update(H)
        c.post('/api/car/demo-login',json={'account':'demo-a'})
        yield c

def vid(c): return c.get('/api/car/dashboard').json()['vehicles'][0]['id']
def path(c): return '/api/car/vehicles/'+vid(c)
def trip(c,source='simulated'):
    p=path(c);r=c.post(p+'/trips',json={'source':source}); assert r.status_code==200
    rid=r.json()['id'];start=time.time()-1200
    for i in range(3):
        r=c.post(p+f'/trips/{rid}/points',json={'lat':41.88+i*.01,'lon':-87.63,'accuracy':5,'timestamp':start+i*300})
        assert r.status_code==200
    return p,rid,r.json()

def test_explicit_demo_required(monkeypatch):
    monkeypatch.delenv('AITA_CAR_DEMO',raising=False)
    with pytest.raises(RuntimeError): create_demo(':memory:')

def test_login_cookie_and_expiry(c):
    r=c.post('/api/car/demo-login',json={'account':'demo-a'})
    assert 'HttpOnly' in r.headers['set-cookie'] and 'SameSite=strict' in r.headers['set-cookie']
    c.post('/api/car/logout',json={});assert c.get('/api/car/dashboard').status_code==401

def test_csrf_rebinding_and_body_limit(c):
    assert c.post('/api/car/logout',json={},headers={'Origin':'https://evil.example'}).status_code==403
    assert c.get('/api/car/dashboard',headers={'Host':'evil.example'}).status_code==403
    assert c.post('/api/car/logout',content='x'*17000).status_code==413

def test_tenants_isolated(c):
    p,rid,_=trip(c);c.post(p+'/expenses',json={'category':'gasolina','amount':20,'note':'privado'})
    c.post('/api/car/demo-login',json={'account':'demo-b'})
    assert vid(c)!=p.split('/')[-1]
    for endpoint in ['', '/obd']:
        assert c.get(p+endpoint).status_code==404
    for endpoint,body in [('/assistant',{'question':'gastos'}),('/parking',{'lat':1,'lon':1,'accuracy':5,'timestamp':time.time()}),('/expenses',{'category':'otros','amount':2,'note':''}),('/trips',{'source':'gps'}),('/maintenance',{'interval':5000}),('/security/simulate',{}), (f'/trips/{rid}/finish',{'tolls':0}), (f'/trips/{rid}/cancel',{})]:
        assert c.post(p+endpoint,json=body).status_code==404
    assert c.get(path(c)).json()['expenses']==[]

def test_simulation_does_not_change_odometer(c):
    p,rid,x=trip(c);before=c.get(p).json()['vehicle']['odometer']
    r=c.post(p+f'/trips/{rid}/finish',json={'tolls':2.5});assert r.status_code==200
    assert r.json()['miles']==pytest.approx(1.38187,rel=.001)
    assert r.json()['fuel']==round(x['miles']/26*3.45,2)
    assert r.json()['total_estimated']==r.json()['fuel']+2.5
    d=c.get(p).json();assert d['vehicle']['odometer']==before and d['parking']['source']=='simulated'
    assert d['expenses']==[]

def test_gps_finish_idempotent(c):
    p,rid,x=trip(c,'gps');before=c.get(p).json()['vehicle']['odometer']
    for _ in range(2): assert c.post(p+f'/trips/{rid}/finish',json={'tolls':1}).status_code==200
    d=c.get(p).json();assert d['vehicle']['odometer']==pytest.approx(before+x['miles'])
    assert len(c.app.state.store.records('demo-a',vid(c),'parking'))==1

@pytest.mark.parametrize('point',[
    {'lat':91,'lon':0,'accuracy':5,'timestamp':1},
    {'lat':0,'lon':0,'accuracy':500,'timestamp':1},
    {'lat':0,'lon':0,'accuracy':5,'timestamp':float('inf')},
    {'lat':0,'lon':0,'accuracy':5,'timestamp':time.time()+3600},
])
def test_bad_points(c,point):
    rid=c.post(path(c)+'/trips',json={'source':'gps'}).json()['id']
    if point['timestamp']==float('inf'):
        r=c.post(path(c)+f'/trips/{rid}/points',content='{"lat":0,"lon":0,"accuracy":5,"timestamp":Infinity}',headers={'Content-Type':'application/json'})
        # Pydantic rejects this; response encoder must also handle non-finite error input.
    else: r=c.post(path(c)+f'/trips/{rid}/points',json=point)
    assert r.status_code==422

def test_duplicates_jumps_and_stationary_noise(c):
    p,rid,x=trip(c);last=x['points'][-1]
    assert c.post(p+f'/trips/{rid}/points',json=last).status_code==422
    jump={**last,'lat':1,'timestamp':last['timestamp']+1}
    assert c.post(p+f'/trips/{rid}/points',json=jump).status_code==422
    noise={**last,'lat':last['lat']+.000001,'timestamp':last['timestamp']+60}
    assert c.post(p+f'/trips/{rid}/points',json=noise).json()['miles']==x['miles']

def test_active_trip_and_cancel(c):
    p=path(c);rid=c.post(p+'/trips',json={'source':'gps'}).json()['id']
    assert c.post(p+'/trips',json={'source':'gps'}).status_code==409
    assert c.post(p+f'/trips/{rid}/finish',json={'tolls':0}).status_code==422
    assert c.post(p+f'/trips/{rid}/cancel',json={}).status_code==200
    assert c.post(p+f'/trips/{rid}/finish',json={'tolls':0}).status_code==409
    assert c.post(p+'/trips',json={'source':'gps'}).status_code==200

def test_pro_entitlement(c):
    assert c.post(path(c)+'/security/simulate',json={}).status_code==403
    c.put('/api/car/demo-plan',json={'plan':'pro'})
    assert c.post(path(c)+'/security/simulate',json={}).json()['source']=='simulated'
    c.put('/api/car/demo-plan',json={'plan':'basic'})
    assert c.post(path(c)+'/security/simulate',json={}).status_code==403

def test_profile_vehicle_expense_maintenance_assistant(c):
    assert c.put('/api/car/profile',json={'name':'Jorge A.'}).status_code==200
    v=c.get(path(c)).json()['vehicle']; b={k:val for k,val in v.items() if k!='id'}
    b.update(make='Toyota',model='Corolla',year=2022)
    new=c.post('/api/car/vehicles',json=b).json();p='/api/car/vehicles/'+new['id']
    assert c.post(p+'/expenses',json={'category':'gasolina','amount':40,'note':'Recarga'}).status_code==200
    assert c.post(p+'/maintenance',json={'interval':7000}).status_code==200
    answer=c.post(p+'/assistant',json={'question':'mantenimiento'}).json()['answer']; assert 'Toyota Corolla 2022' in answer and '7000' in answer
    assert '$40.00' in c.post(p+'/assistant',json={'question':'gastos'}).json()['answer']
    assert c.get(p+'/obd').json()['connected'] is False
    assert c.post(p+'/expenses',json={'category':'otros','amount':-1,'note':''}).status_code==422
    assert c.post(p+'/expenses',json={'tenant':'demo-b','category':'otros','amount':1,'note':''}).status_code==422

def test_persistent_restart(tmp_path,monkeypatch):
    monkeypatch.setenv('AITA_CAR_DEMO','1');p=tmp_path/'car.db'
    a=create_demo(p);b=create_demo(p)
    assert a.state.store.vehicles('demo-a')==b.state.store.vehicles('demo-a')

def test_production_router_has_no_demo_plan():
    s=Store(':memory:');app=FastAPI();app.include_router(router(s,lambda:'tenant'))
    assert not any(x.path.endswith('/demo-plan') for x in app.routes)

class AuthDB:
    enabled=True
    def user_from_jwt(self,jwt): return {'id':'user'} if jwt=='valid' else None
    def select(self,table,filters):
        if table=='tenant_users': return [{'role':'owner'}] if filters['tenant_id']=='eq.00000000-0000-0000-0000-000000000001' else []
        return [{'modules':{'car':True}}]

@pytest.mark.parametrize('token,tenant,expected',[
 ('','00000000-0000-0000-0000-000000000001',401),('bad','00000000-0000-0000-0000-000000000001',401),
 ('valid','00000000-0000-0000-0000-000000000002',403),('valid','invalid',403),
 ('valid','00000000-0000-0000-0000-000000000001',200)])
def test_supabase_gate(token,tenant,expected):
    db=AuthDB();s=Store(':memory:');s.seed('00000000-0000-0000-0000-000000000001')
    app=FastAPI();app.include_router(router(s,supabase_identity(db)))
    r=TestClient(app).get('/api/car/dashboard',headers={'Authorization':'Bearer '+token,'x-aita-tenant':tenant})
    assert r.status_code==expected

def test_module_gate_is_strict():
    for value in (False,1,'true',None):
        db=AuthDB();old=db.select
        db.select=lambda table,f: [{'modules':{'car':value}}] if table=='tenants' else old(table,f)
        app=FastAPI();app.include_router(router(Store(':memory:'),supabase_identity(db)))
        assert TestClient(app).get('/api/car/dashboard',headers={'Authorization':'Bearer valid','x-aita-tenant':'00000000-0000-0000-0000-000000000001'}).status_code==403

def test_haversine():
    assert miles({'lat':0,'lon':0},{'lat':0,'lon':1})==pytest.approx(69.0934,rel=.0001)

def test_concurrent_finish_is_atomic(c):
    from concurrent.futures import ThreadPoolExecutor
    p,rid,x=trip(c,'gps');before=c.get(p).json()['vehicle']['odometer']
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:c.post(p+f'/trips/{rid}/finish',json={'tolls':0}).status_code,range(2)))
    assert results==[200,200]
    assert c.get(p).json()['vehicle']['odometer']==pytest.approx(before+x['miles'])
    assert len(c.app.state.store.records('demo-a',vid(c),'parking'))==1

def test_profile_cannot_escalate_plan(c):
    assert c.put('/api/car/profile',json={'name':'Jorge','plan':'pro'}).status_code==422
    assert c.get('/api/car/dashboard').json()['profile']['plan']=='basic'

def test_vehicle_edits_do_not_change_active_trip(c):
    p,rid,x=trip(c);v=c.get(p).json()['vehicle'];v.pop('id');v['mpg']=100
    assert c.put(p,json=v).status_code==409
    c.post(p+f'/trips/{rid}/finish',json={'tolls':0})
    assert c.put(p,json=v).status_code==200
    assert c.get(p).json()['trips'][0]['mpg']==26

@pytest.mark.parametrize('language,question,expected',[
 ('en','How much have I spent on fuel?','Your recorded expenses total'),
 ('en','When is my oil change due?','custom oil reminder'),
 ('en','Where did I park?','parking location'),
 ('en','Check engine diagnostics','OBD disconnected'),
 ('es','¿Cuánto gasté?','Tus gastos registrados suman'),
])
def test_bilingual_nexxus(c,language,question,expected):
    response=c.post(path(c)+'/assistant',json={'question':question,'language':language})
    assert response.status_code==200 and expected in response.json()['answer']

def test_unknown_assistant_language_rejected(c):
    assert c.post(path(c)+'/assistant',json={'question':'hello','language':'fr'}).status_code==422

ONBOARD={'name':'Demo Owner','address':'100 Example Street','phone':'+1 555 010 2345','make':'Toyota','model':'Corolla','year':2022,'initial_odometer':80000,'distance_unit':'km'}

def test_onboarding_km_and_profile_persist(c):
    before=vid(c)
    r=c.post('/api/car/onboarding',json=ONBOARD);assert r.status_code==200
    v=r.json()['vehicle'];assert v['odometer']==pytest.approx(80000/1.609344)
    assert v['initial_odometer']==80000 and v['distance_unit']=='km'
    p=c.get('/api/car/dashboard').json()['profile']
    assert p['onboarding_completed'] and p['address']==ONBOARD['address'] and p['phone']==ONBOARD['phone']
    assert p['primary_vehicle_id']==v['id'] and before!=v['id']
    answer=c.post('/api/car/vehicles/'+v['id']+'/assistant',json={'question':'hello','language':'en'}).json()['answer']
    assert '80000.0 kilometers' in answer and ONBOARD['address'] not in answer and ONBOARD['phone'] not in answer
    n=len(c.get('/api/car/dashboard').json()['vehicles'])
    assert c.post('/api/car/onboarding',json=ONBOARD).status_code==409
    assert len(c.get('/api/car/dashboard').json()['vehicles'])==n

@pytest.mark.parametrize('change',[{'phone':'-------'},{'name':'   '},{'address':'x'},{'initial_odometer':-1},{'distance_unit':'yards'},{'year':1800},{'plan':'invalid'}])
def test_invalid_onboarding_is_atomic(c,change):
    count=len(c.get('/api/car/dashboard').json()['vehicles'])
    assert c.post('/api/car/onboarding',json={**ONBOARD,**change}).status_code==422
    d=c.get('/api/car/dashboard').json()
    assert not d['profile'].get('onboarding_completed') and len(d['vehicles'])==count

def test_onboarding_miles_and_isolation(c):
    r=c.post('/api/car/onboarding',json={**ONBOARD,'distance_unit':'mi','initial_odometer':45000})
    assert r.json()['vehicle']['odometer']==45000
    c.post('/api/car/demo-login',json={'account':'demo-b'})
    p=c.get('/api/car/dashboard').json()['profile']
    assert not p.get('onboarding_completed') and 'address' not in p and 'phone' not in p


def test_new_user_starts_empty_and_isolated(c):
    previous=vid(c)
    r=c.post('/api/car/start',json={})
    assert r.status_code==200
    assert 'HttpOnly' in r.headers['set-cookie']
    first=r.json()['account']
    d=c.get('/api/car/dashboard').json()
    assert d['vehicles']==[] and d['profile']['onboarding_completed'] is False
    assert d['profile']['trial_access'] is True
    assert c.get('/api/car/vehicles/'+previous).status_code==404
    assert c.post('/api/car/start',json={}).json()['account']!=first


def test_connector_settings_are_isolated_and_not_connectivity(c):
    settings={'maps':'waze','music':'amazon','obd_name':'vLinker MC+','projection':'carplay','assistant':'openai'}
    r=c.put('/api/car/connectors',json=settings)
    assert r.status_code==200 and r.json()['settings']==settings
    assert r.json()['status']['obd']=='disconnected'
    assert r.json()['status']['assistant']=='provider_setup_required'
    assert c.put('/api/car/connectors',json={**settings,'api_key':'secret'}).status_code==422
    c.post('/api/car/start',json={})
    assert c.get('/api/car/connectors').json()['settings']['maps']=='apple'
    assert c.get('/api/car/vehicles/missing/obd').status_code==404


def test_all_plans_remain_without_charges(c):
    for plan in ['basic','pro']:
        c.put('/api/car/demo-plan',json={'plan':plan})
        billing=c.get('/api/car/dashboard').json()['billing']
        assert billing['mode']=='trial'
        assert billing['charges_enabled'] is False
        assert billing['payment_collection_enabled'] is False
        assert billing['activation_requires_owner_authorization'] is True


def test_preferred_name_and_separate_address_fields(c):
    data={'name':'Jorge Abalo','preferred_name':'Jorge','address':'123 Main Street','city':'Miami','state':'Florida','country':'United States','phone':'5551234567'}
    r=c.put('/api/car/profile',json=data)
    assert r.status_code==200
    for key,value in data.items(): assert c.get('/api/car/dashboard').json()['profile'][key]==value
    assert c.put('/api/car/profile',json={**data,'preferred_name':' '*3}).status_code==422


def test_onboarding_registers_family_cars_and_plan(c):
    c.post('/api/car/start',json={})
    car={'make':'Kia','model':'Rio','year':2023,'initial_odometer':2000,'distance_unit':'km'}
    r=c.post('/api/car/onboarding',json={'name':'Jorge Abalo','preferred_name':'Jorge','address':'123 Main Street','phone':'5551234567','make':'Kia','model':'Sportage','year':2021,'initial_odometer':100,'distance_unit':'mi','plan':'pro','additional_vehicles':[car]})
    assert r.status_code==200
    d=c.get('/api/car/dashboard').json()
    assert len(d['vehicles'])==2 and d['profile']['plan']=='pro'
    rio=next(v for v in d['vehicles'] if v['model']=='Rio')
    assert rio['odometer']==pytest.approx(2000/1.609344)
    assert d['profile']['primary_vehicle_id']==r.json()['vehicle']['id']
