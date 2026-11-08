"""Nexxus integration gate. Not wired into the local demo."""
from uuid import UUID
from fastapi import Request, HTTPException

def supabase_identity(db):
    def identity(request:Request):
        a=request.headers.get('authorization','')
        if not a.lower().startswith('bearer '): raise HTTPException(401,'Sesión requerida')
        if not db.enabled: raise HTTPException(503,'Identidad no configurada')
        user=db.user_from_jwt(a[7:].strip())
        if not user: raise HTTPException(401,'Sesión inválida')
        try: tenant=str(UUID(request.headers.get('x-aita-tenant','')))
        except ValueError: raise HTTPException(403,'Cuenta no autorizada')
        membership=db.select('tenant_users',{'tenant_id':f'eq.{tenant}','user_id':f"eq.{user['id']}",
                    'active':'eq.true','role':'in.(owner,manager)','select':'role','limit':'1'})
        if not membership: raise HTTPException(403,'Cuenta no autorizada')
        tenants=db.select('tenants',{'id':f'eq.{tenant}','select':'modules','limit':'1'})
        modules=(tenants[0].get('modules') if tenants else None)
        if not isinstance(modules,dict) or modules.get('car') is not True:
            raise HTTPException(403,'Vertical Car deshabilitada')
        return tenant
    return identity
