"""Alexa skill domain handler. Transport signature verification and OAuth are required before mounting."""
import time, uuid
from datetime import datetime, timezone
from .adapters import LocalNexxus
from .domain import Expense

def speech(text,end=False,link=False):
    response={'outputSpeech':{'type':'PlainText','text':text[:2000]},'shouldEndSession':end}
    if link: response['card']={'type':'LinkAccount'}
    if not end: response['reprompt']={'outputSpeech':{'type':'PlainText','text':'Puedes consultar mantenimiento o registrar un gasto.'}}
    return {'version':'1.0','response':response}

class CarAlexa:
    def __init__(self,store,verify_linked_token):
        self.store=store;self.verify_linked_token=verify_linked_token
        with store.transaction():
            store.db.execute('CREATE TABLE IF NOT EXISTS alexa_pending (tenant TEXT, session TEXT, vehicle TEXT, data TEXT, expires REAL, PRIMARY KEY(tenant,session))')
            store.db.execute('CREATE TABLE IF NOT EXISTS alexa_requests (tenant TEXT, request TEXT, response TEXT, expires REAL, PRIMARY KEY(tenant,request))')
    def handle(self,envelope):
        import json
        system=envelope.get('context',{}).get('System',{})
        token=system.get('user',{}).get('accessToken')
        tenant=self.verify_linked_token(token) if token else None
        if not tenant: return speech('Vincula tu cuenta de AITA Car desde la app Alexa.',True,True)
        req=envelope['request'];rid=req['requestId'];session=envelope.get('session',{}).get('sessionId','')
        with self.store.transaction():
            self.store.db.execute('DELETE FROM alexa_requests WHERE expires<?',(time.time(),))
            self.store.db.execute('DELETE FROM alexa_pending WHERE expires<?',(time.time(),))
            old=self.store.db.execute('SELECT response FROM alexa_requests WHERE tenant=? AND request=?',(tenant,rid)).fetchone()
            if old:return json.loads(old[0])
            answer=self._dispatch(tenant,session,req)
            self.store.db.execute('INSERT INTO alexa_requests VALUES(?,?,?,?)',(tenant,rid,json.dumps(answer),time.time()+300))
            return answer
    def _dispatch(self,tenant,session,req):
        import json
        p=self.store.profile(tenant) or {};vehicle=self.store.vehicle(tenant,p.get('primary_vehicle_id',''))
        if not vehicle:return speech('Completa tu vehículo en AITA Car primero.',True)
        intent=req.get('intent',{});name=intent.get('name');slots=intent.get('slots',{})
        value=lambda key: slots.get(key,{}).get('value','')
        if name in ('AMAZON.CancelIntent','AMAZON.NoIntent','AMAZON.StopIntent'):
            self.store.db.execute('DELETE FROM alexa_pending WHERE tenant=? AND session=?',(tenant,session))
            return speech('De acuerdo. No guardaré ningún gasto.',True)
        if name=='AMAZON.YesIntent':
            pending=self.store.db.execute('SELECT vehicle,data FROM alexa_pending WHERE tenant=? AND session=?',(tenant,session)).fetchone()
            if not pending or pending[0]!=vehicle['id']:return speech('No hay un gasto pendiente para este carro.',True)
            data=json.loads(pending[1]);self.store.save_record(tenant,vehicle['id'],'expense',{'id':str(uuid.uuid4()),'at':datetime.now(timezone.utc).isoformat(),**data})
            self.store.db.execute('DELETE FROM alexa_pending WHERE tenant=? AND session=?',(tenant,session))
            return speech('Gasto guardado.',True)
        if name=='RecordExpenseIntent':
            if not session:return speech('Inicia una sesión de Alexa para confirmar el gasto.',True)
            categories={'gasolina':'gasolina','aceite':'mantenimiento','mantenimiento':'mantenimiento','peajes':'peajes'}
            try:data=Expense(category=categories[value('category').lower()],amount=float(value('amount').replace(',','.')),note='Registrado con Alexa').model_dump()
            except (ValueError,KeyError):return speech('Dime la categoría y el importe en dólares, por ejemplo gasolina por cuarenta dólares.')
            self.store.db.execute('INSERT OR REPLACE INTO alexa_pending VALUES(?,?,?,?,?)',(tenant,session,vehicle['id'],json.dumps(data),time.time()+120))
            return speech(f"¿Confirmas registrar {data['amount']:.2f} dólares de {data['category']} para tu {vehicle['make']} {vehicle['model']}?")
        if name=='VehicleQueryIntent':
            expenses=self.store.records(tenant,vehicle['id'],'expense');parks=self.store.records(tenant,vehicle['id'],'parking')
            return speech(LocalNexxus().answer(value('question'),{'vehicle':vehicle,'language':'es','expense_total':sum(x['amount'] for x in expenses),'parking':parks[0] if parks else None}))
        return speech('Soy Nexxus de AITA Car. Puedes preguntar por mantenimiento y gastos, o registrar gasolina por un importe en dólares.')
