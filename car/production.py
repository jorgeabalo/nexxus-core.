"""HTTPS-only private pilot. Supabase authenticates approved testers; no payments."""
import os, time, hashlib
from pathlib import Path
from uuid import UUID
from urllib.parse import urlsplit
import httpx
from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import FileResponse, JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ConfigDict
from .store import Store
from .api import router
from .domain import Plan

ROOT=Path(__file__).resolve().parent
COOKIE='__Host-aita-car'
class Credentials(BaseModel):
    model_config=ConfigDict(extra='forbid')
    email: str=Field(min_length=3,max_length=254)
    password: str=Field(min_length=8,max_length=256)

def create_production(*, origin=None, auth_url=None, auth_key=None, path=None, allowed_users=None, transport=None):
    origin=origin or os.getenv('AITA_CAR_ORIGIN','')
    auth_url=auth_url or os.getenv('SUPABASE_URL','')
    auth_key=auth_key or os.getenv('SUPABASE_ANON_KEY','')
    path=path or os.getenv('AITA_CAR_DATABASE','')
    allowed_users=allowed_users or os.getenv('AITA_CAR_TEST_USERS','').split(',')
    if not origin.startswith('https://') or urlsplit(origin).path not in ('','/'):
        raise RuntimeError('Configure an HTTPS AITA_CAR_ORIGIN')
    if not auth_url.startswith('https://') or not auth_key or not path:
        raise RuntimeError('Configure Supabase Auth and a persistent database path')
    allowed={str(UUID(u.strip())) for u in allowed_users if u.strip()}
    if not allowed: raise RuntimeError('Explicit tester UUID allowlist required')
    origin=origin.rstrip('/'); host=urlsplit(origin).netloc
    store=Store(path); app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None)
    auth=httpx.Client(base_url=auth_url.rstrip('/')+'/auth/v1',headers={'apikey':auth_key},timeout=8,transport=transport)
    app.state.store=store
    alexa_enabled=os.getenv('AITA_CAR_ALEXA_ENABLED','').lower()=='true'
    alexa_server_paths={'/api/car/alexa','/alexa/token'} if alexa_enabled else set()
    store.db.execute('CREATE TABLE IF NOT EXISTS login_limits (key TEXT PRIMARY KEY, window INTEGER, attempts INTEGER)');store.db.commit()
    @app.on_event('shutdown')
    def close(): auth.close(); store.db.close()
    @app.exception_handler(RequestValidationError)
    async def invalid(request,exc): return JSONResponse({'detail':'Revisa los campos'},422)
    @app.middleware('http')
    async def protect(request,call_next):
        if request.url.path!='/health' and request.headers.get('host')!=host: return JSONResponse({'detail':'Host no autorizado'},403)
        if request.method not in ('GET','HEAD','OPTIONS'):
            if request.url.path not in alexa_server_paths and request.headers.get('origin')!=origin: return JSONResponse({'detail':'Origen no autorizado'},403)
            chunks=[]; size=0
            async for chunk in request.stream():
                size+=len(chunk)
                if size>(32768 if request.url.path=='/api/car/alexa' else 16384): return JSONResponse({'detail':'Solicitud demasiado grande'},413)
                chunks.append(chunk)
            request._body=b''.join(chunks)
            if request.url.path in alexa_server_paths:
                window=int(time.time()//60);key='alexa:'+request.url.path
                with store.transaction():
                    row=store.db.execute('SELECT attempts FROM login_limits WHERE key=? AND window=?',(key,window)).fetchone()
                    if row and row[0]>=120:return JSONResponse({'detail':'Límite de solicitudes'},429)
                    store.db.execute('INSERT INTO login_limits VALUES(?,?,1) ON CONFLICT(key) DO UPDATE SET window=excluded.window,attempts=CASE WHEN window=excluded.window THEN attempts+1 ELSE 1 END',(key,window))
        response=await call_next(request)
        # Native consent forms need their real Origin for the strict CSRF check.
        # Keep OAuth URLs private when navigating to Amazon or any other origin.
        response.headers.update({'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Referrer-Policy':'same-origin' if request.url.path in ('/alexa/authorize','/alexa/manage') else 'no-referrer','Strict-Transport-Security':'max-age=31536000',
          'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-src https://www.openstreetmap.org; object-src 'none'; frame-ancestors 'none'; base-uri 'self'"})
        return response
    def auth_request(method,path,**kwargs):
        try: return auth.request(method,path,**kwargs)
        except httpx.HTTPError: raise HTTPException(503,'Servicio de identidad no disponible') from None
    def checked_user(token):
        if not token: raise HTTPException(401,'Inicia sesión')
        res=auth_request('GET','/user',headers={'Authorization':'Bearer '+token})
        if res.status_code!=200: raise HTTPException(401,'Inicia sesión nuevamente')
        user=res.json()
        if user.get('id') not in allowed: raise HTTPException(403,'Cuenta no habilitada para esta prueba')
        return 'local-'+user['id']
    def identity(request:Request): return checked_user(request.cookies.get(COOKIE))
    if alexa_enabled:
        from .alexa_link import AlexaLink
        from .alexa_transport import alexa_router
        skill_id=os.getenv('AITA_CAR_ALEXA_SKILL_ID','')
        if not skill_id.startswith('amzn1.ask.skill.'): raise RuntimeError('Alexa skill ID required')
        link=AlexaLink(store,os.getenv('AITA_CAR_ALEXA_CLIENT_ID',''),os.getenv('AITA_CAR_ALEXA_CLIENT_SECRET',''),
          [u.strip() for u in os.getenv('AITA_CAR_ALEXA_REDIRECTS','').split(',') if u.strip()],{'local-'+u for u in allowed})
        app.include_router(alexa_router(store,identity,link,skill_id))
    @app.post('/api/car/start')
    def login(b:Credentials,request:Request):
        # Persist limits across restarts; never retain passwords or raw email addresses.
        window=int(time.time()//900)
        email_key=hashlib.sha256(b.email.strip().lower().encode()).hexdigest()
        with store.transaction():
            store.db.execute('DELETE FROM login_limits WHERE window<?',(window,))
            for key,limit in [(email_key,8),('global',120)]:
                row=store.db.execute('SELECT attempts FROM login_limits WHERE key=? AND window=?',(key,window)).fetchone()
                if row and row[0]>=limit: raise HTTPException(429,'Espera antes de intentar nuevamente')
            for key in (email_key,'global'):
                store.db.execute('INSERT INTO login_limits VALUES(?,?,1) ON CONFLICT(key) DO UPDATE SET attempts=attempts+1',(key,window))
        res=auth_request('POST','/token?grant_type=password',json=b.model_dump())
        if res.status_code!=200: raise HTTPException(401,'No se pudo iniciar sesión')
        data=res.json(); token=data.get('access_token'); tenant=checked_user(token)
        with store.transaction():
            if not store.profile(tenant): store.save_profile(tenant,{'name':'','plan':'basic','onboarding_completed':False,'trial_access':True})
        response=JSONResponse({'ok':True,'charges_enabled':False})
        response.set_cookie(COOKIE,token,secure=True,httponly=True,samesite='strict',path='/',max_age=min(int(data.get('expires_in',3600)),3600))
        return response
    @app.post('/api/car/logout')
    def logout():
        response=JSONResponse({'ok':True});response.delete_cookie(COOKIE,secure=True,httponly=True,samesite='strict');return response
    @app.put('/api/car/demo-plan')
    def trial_plan(b:Plan,t=Depends(identity)):
        with store.transaction():
            p=store.profile(t);p['plan']=b.plan;store.save_profile(t,p);return p
    app.include_router(router(store,identity,demo=False))
    app.mount('/car/assets',StaticFiles(directory=ROOT/'assets'),name='assets')
    @app.get('/health')
    def health(): return {'ok':True,'payments_enabled':False}
    @app.get('/')
    @app.get('/car')
    def index(): return FileResponse(ROOT/'index.html')
    @app.get('/api/car/runtime')
    def runtime(): return {'requires_login':True}
    return app
