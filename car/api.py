import time, uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from .connectors import ConnectorSettings, DEFAULTS, status
from .domain import Vehicle, TripStart, Point, Finish, Expense, Question, Plan, Profile, Maintenance, Onboarding, miles
from .adapters import MockOBD, FutureProjection, LocalNexxus

def now(): return datetime.now(timezone.utc).isoformat()
def uid(): return str(uuid.uuid4())

def router(store, identity, *, demo=False, obd=None, assistant=None):
    r = APIRouter(prefix='/api/car'); obd=obd or MockOBD(); assistant=assistant or LocalNexxus()
    def vehicle(t, vid):
        v=store.vehicle(t,vid)
        if not v: raise HTTPException(404,'Vehículo no encontrado')
        return v
    def record(t,vid,rid):
        vehicle(t,vid); x=store.record(t,vid,rid,'trip')
        if not x: raise HTTPException(404,'Viaje no encontrado')
        return x

    @r.get('/dashboard')
    def dashboard(t=Depends(identity)):
        with store.lock:
            return {'profile':store.profile(t), 'vehicles':store.vehicles(t), 'demo':demo,
                    'adapters':FutureProjection().capabilities(), 'prices':{'basic':19.99,'pro':29.99},
                    'billing':{'mode':'trial','charges_enabled':False,'payment_collection_enabled':False,'activation_requires_owner_authorization':True}}

    @r.get('/connectors')
    def connectors(t=Depends(identity)):
        with store.lock:
            settings={**DEFAULTS,**store.profile(t).get('connectors',{})}
            return {'settings':settings,'status':status(settings)}

    @r.put('/connectors')
    def configure_connectors(b:ConnectorSettings,t=Depends(identity)):
        with store.transaction():
            p=store.profile(t); p['connectors']=b.model_dump(); store.save_profile(t,p)
            return {'settings':p['connectors'],'status':status(p['connectors'])}

    @r.post('/onboarding')
    def onboarding(b:Onboarding,t=Depends(identity)):
        with store.transaction():
            p=store.profile(t)
            if not p: raise HTTPException(401,'Inicia una sesión demo')
            if p.get('onboarding_completed'): raise HTTPException(409,'Onboarding ya completado; edita tu perfil')
            if len(store.vehicles(t))+1+len(b.additional_vehicles)>10: raise HTTPException(409,'Límite de 10 vehículos')
            odometer=b.initial_odometer / 1.609344 if b.distance_unit=='km' else b.initial_odometer
            v={'id':uid(),'make':b.make,'model':b.model,'year':b.year,'odometer':odometer,
               'initial_odometer':b.initial_odometer,'initial_distance_unit':b.distance_unit,'distance_unit':b.distance_unit,
               'initial_odometer_miles':odometer,'mpg':26,'fuel_price':3.45,'oil_due':odometer+5000,
               'baseline_at':now(),'maintenance_interval_source':'demo_estimate'}
            store.save_vehicle(t,v)
            for extra in b.additional_vehicles:
                odo=extra.initial_odometer/(1.609344 if extra.distance_unit=='km' else 1)
                store.save_vehicle(t,{'id':uid(),'make':extra.make,'model':extra.model,'year':extra.year,
                    'odometer':odo,'initial_odometer_miles':odo,'initial_odometer':extra.initial_odometer,
                    'initial_distance_unit':extra.distance_unit,'distance_unit':extra.distance_unit,
                    'mpg':26,'fuel_price':3.45,'oil_due':odo+5000,'baseline_at':now(),'maintenance_interval_source':'demo_estimate'})
            p.update(plan=b.plan,preferred_name=b.preferred_name,city=b.city,state=b.state,country=b.country,
                     name=b.name,address=b.address,phone=b.phone,onboarding_completed=True,
                     onboarding_at=now(),primary_vehicle_id=v['id'])
            store.save_profile(t,p)
            return {'profile':p,'vehicle':v}

    @r.put('/profile')
    def profile(b:Profile,t=Depends(identity)):
        with store.transaction():
            p=store.profile(t); p.update(b.model_dump(exclude_none=True)); store.save_profile(t,p); return p

    if demo:
        @r.put('/demo-plan')
        def plan(b:Plan,t=Depends(identity)):
            with store.transaction():
                p=store.profile(t); p['plan']=b.plan; store.save_profile(t,p); return p

    @r.post('/vehicles')
    def add(b:Vehicle,t=Depends(identity)):
        with store.transaction():
            if len(store.vehicles(t))>=10: raise HTTPException(409,'Límite de 10 vehículos de demo')
            v={'id':uid(),**b.model_dump()}; store.save_vehicle(t,v); return v

    @r.put('/vehicles/{vid}')
    def update(vid:str,b:Vehicle,t=Depends(identity)):
        with store.transaction():
            old=vehicle(t,vid)
            if any(x['status']=='active' for x in store.records(t,vid,'trip')): raise HTTPException(409,'Finaliza el viaje antes de editar')
            if b.odometer < old['odometer']-0.1: raise HTTPException(422,'El odómetro no puede disminuir')
            b.odometer=max(b.odometer,old['odometer'])
            v={**old,**b.model_dump(),'id':vid}; store.save_vehicle(t,v); return v

    @r.get('/vehicles/{vid}')
    def detail(vid:str,t=Depends(identity)):
        with store.lock:
            v=vehicle(t,vid); trips=store.records(t,vid,'trip'); expenses=store.records(t,vid,'expense')
            parks=store.records(t,vid,'parking')
            return {'vehicle':v,'trips':trips,'expenses':expenses,'parking':parks[0] if parks else None,
                    'maintenance':store.records(t,vid,'maintenance'),'alerts':store.records(t,vid,'alert'),
                    'oil_remaining':max(0,v['oil_due']-v['odometer']),
                    'expense_total':round(sum(x['amount'] for x in expenses),2)}

    @r.post('/vehicles/{vid}/trips')
    def start(vid:str,b:TripStart,t=Depends(identity)):
        with store.transaction():
            v=vehicle(t,vid)
            if b.source=='simulated' and t.startswith('local-'): raise HTTPException(409,'Usa GPS para registrar un viaje real')
            if any(x['status']=='active' for x in store.records(t,vid,'trip')): raise HTTPException(409,'Ya hay un viaje activo')
            x={'id':uid(),'status':'active','source':b.source,'started':now(),'points':[], 'miles':0,
               'mpg':v['mpg'],'fuel_price':v['fuel_price'],'tolls':0,'fuel':0}
            store.save_record(t,vid,'trip',x); return x

    @r.post('/vehicles/{vid}/trips/{rid}/points')
    def point(vid:str,rid:str,b:Point,t=Depends(identity)):
        with store.transaction():
            x=record(t,vid,rid)
            if x['status']!='active': raise HTTPException(409,'Viaje finalizado')
            p=b.model_dump(); pts=x['points']
            if len(pts)>=10000: raise HTTPException(409,'Límite de puntos alcanzado; finaliza el viaje')
            if p['accuracy']>50: raise HTTPException(422,'GPS impreciso: espera una lectura mejor')
            if p['timestamp']>time.time()+60: raise HTTPException(422,'Fecha GPS futura')
            if pts:
                elapsed=p['timestamp']-pts[-1]['timestamp']; distance=miles(pts[-1],p)
                if elapsed<=0 or distance/elapsed*3600>130: raise HTTPException(422,'Punto fuera de orden o salto GPS')
                # Ignore movement below combined uncertainty; prevents stationary jitter mileage.
                if distance*1609.344>max(10,p['accuracy']+pts[-1]['accuracy']): x['miles']+=distance
            pts.append(p); x['fuel']=round(x['miles']/x['mpg']*x['fuel_price'],2)
            store.save_record(t,vid,'trip',x); return x

    @r.post('/vehicles/{vid}/trips/{rid}/finish')
    def finish(vid:str,rid:str,b:Finish,t=Depends(identity)):
        with store.transaction():
            x=record(t,vid,rid)
            if x['status']=='finished': return x
            if x['status']!='active': raise HTTPException(409,'Viaje cancelado')
            if len(x['points'])<2: raise HTTPException(422,'Registra al menos dos puntos GPS')
            x.update(status='finished',ended=now(),tolls=b.tolls)
            x['total_estimated']=round(x['fuel']+b.tolls,2)
            v=vehicle(t,vid)
            # Synthetic mileage never changes actual vehicle odometer.
            if x['source']=='gps': v['odometer']+=x['miles']; store.save_vehicle(t,v)
            store.save_record(t,vid,'trip',x)
            p=x['points'][-1]
            store.save_record(t,vid,'parking',{'id':uid(),'lat':p['lat'],'lon':p['lon'],'at':now(),'source':x['source']})
            return x

    @r.post('/vehicles/{vid}/trips/{rid}/cancel')
    def cancel(vid:str,rid:str,t=Depends(identity)):
        with store.transaction():
            x=record(t,vid,rid)
            if x['status']=='finished': raise HTTPException(409,'Viaje ya finalizado')
            x.update(status='cancelled',ended=now()); store.save_record(t,vid,'trip',x); return x

    @r.post('/vehicles/{vid}/expenses')
    def expense(vid:str,b:Expense,t=Depends(identity)):
        with store.transaction():
            vehicle(t,vid); x={'id':uid(),**b.model_dump(),'at':now()}; store.save_record(t,vid,'expense',x); return x

    @r.post('/vehicles/{vid}/parking')
    def park(vid:str,b:Point,t=Depends(identity)):
        with store.transaction():
            vehicle(t,vid)
            if b.accuracy>50 or abs(time.time()-b.timestamp)>300: raise HTTPException(422,'Ubicación imprecisa o antigua')
            x={'id':uid(),'lat':b.lat,'lon':b.lon,'at':now(),'source':'gps'}; store.save_record(t,vid,'parking',x); return x

    @r.post('/vehicles/{vid}/maintenance')
    def maintenance(vid:str,b:Maintenance,t=Depends(identity)):
        with store.transaction():
            v=vehicle(t,vid); v['oil_due']=v['odometer']+b.interval; store.save_vehicle(t,v)
            x={'id':uid(),'kind':'aceite','at':now(),'odometer':v['odometer'],'interval':b.interval,'source':'user'}
            store.save_record(t,vid,'maintenance',x); return x

    @r.get('/vehicles/{vid}/obd')
    def diagnostic(vid:str,t=Depends(identity)):
        with store.lock:
            v=vehicle(t,vid)
            return ({'source':'unavailable','connected':False,'notice':'OBD sin conexión. Configura el adaptador en Perfil.'} if t.startswith('local-') else obd.read(v))

    @r.post('/vehicles/{vid}/security/simulate')
    def security(vid:str,t=Depends(identity)):
        with store.transaction():
            vehicle(t,vid)
            if not demo or t.startswith('local-'): raise HTTPException(501,'Proveedor de seguridad sin conexión')
            if store.profile(t)['plan']!='pro': raise HTTPException(403,'Función del plan Pro')
            x={'id':uid(),'at':now(),'source':'simulated','message':'Movimiento simulado fuera del estacionamiento. Sin monitoreo real, llamadas de emergencia ni inmovilización.'}
            store.save_record(t,vid,'alert',x); return x

    @r.post('/vehicles/{vid}/assistant')
    def ask(vid:str,b:Question,t=Depends(identity)):
        ctx=detail(vid,t)
        ctx['language']=b.language
        return {'answer':assistant.answer(b.question,ctx),'provider':'local_context_demo'}
    return r
