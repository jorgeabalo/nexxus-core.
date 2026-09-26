"""
NEXXUS AI Support — API multi-tenant (Fase 2).

Diferencia con el main.py del MVP anterior: cada endpoint de llamada/mensaje
recibe un `negocio_slug` en la URL (ej. /api/fuerza-total/iniciar) — así un
mismo servidor atiende a todos los negocios de Jorge, cada uno con sus
propios datos aislados (nunca se cruzan, igual principio que ya se probó
para llamadas concurrentes en el MVP anterior, ahora extendido a nivel
negocio).

Todavía NO conectado a Twilio (llamadas telefónicas reales) — estos
endpoints son el mismo patrón de chat de texto del MVP anterior, pensado
para que el motor (generic_service.py) y el modelo de datos multi-tenant
queden probados y firmes ANTES de sumarle la capa de telefonía real. Cuando
Twilio esté aprobado, se agrega un router de webhooks de voz que llama a
estos mismos métodos del servicio (iniciar_llamada/procesar_mensaje/
finalizar_llamada) — no hace falta reescribir el motor.
"""

import os
import time
from collections import defaultdict, deque
from typing import Optional
from typing import Optional as _Optional

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Depends, status, Request
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from pathlib import Path
from datetime import datetime
import secrets
import uuid

from generic_service import NexxusIAService
# from agente_configuracion import AgenteConfiguracion
# from models import SessionLocal, Negocio, ClienteNegocio
# from verticals import VERTICALES
import billing_service
import progreso_service
import riesgo_service

def _advertir_credenciales_por_defecto():
    """No podemos "negarnos a arrancar" sin riesgo de romper el uso local/
    demo (tests, desarrollo sin .env) — pero si en producción alguien borra
    o nunca puso ADMIN_PASSWORD/OPERADOR_PASSWORD en Railway, esto lo deja
    bien visible en los logs del deploy en vez de arrancar en silencio con
    una contraseña pública conocida."""
    inseguras = []
    if os.getenv("ADMIN_PASSWORD", "cambiar-esta-clave") == "cambiar-esta-clave":
        inseguras.append("ADMIN_PASSWORD")
    if os.getenv("OPERADOR_PASSWORD", "cambiar-esta-clave-tambien") == "cambiar-esta-clave-tambien":
        inseguras.append("OPERADOR_PASSWORD")
    if inseguras:
        print(
            "🚨 ATENCIÓN: " + ", ".join(inseguras) + " no está(n) configurada(s) — "
            "la app está usando la contraseña por defecto (pública, conocida). "
            "Configurá variables de entorno reales antes de usar esto con datos de verdad."
        )


@asynccontextmanager
async def _lifespan(app: FastAPI):
    _advertir_credenciales_por_defecto()
    yield


app = FastAPI(title="NEXXUS AI Support", lifespan=_lifespan)
servicio = NexxusIAService()
agente_configuracion = AgenteConfiguracion()
security = HTTPBasic()

RATE_LIMIT_MAX_REQUESTS = int(os.getenv("RATE_LIMIT_MAX_REQUESTS", "20"))
RATE_LIMIT_WINDOW_SECONDS = int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60"))
_peticiones_por_ip = defaultdict(deque)


def _chequear_rate_limit(clave: str):
    """Limitador genérico de tasa — la `clave` puede ser una IP real o
    cualquier otro identificador (ej. "negocio:llamada") según qué se quiera
    limitar; el mecanismo (ventana deslizante en memoria) es el mismo."""
    ahora = time.time()
    cola = _peticiones_por_ip[clave]
    while cola and ahora - cola[0] > RATE_LIMIT_WINDOW_SECONDS:
        cola.popleft()
    if len(cola) >= RATE_LIMIT_MAX_REQUESTS:
        raise HTTPException(status_code=429, detail="Demasiadas solicitudes")
    cola.append(ahora)


def _ip_cliente(request: Request) -> str:
    """IP real del que llama — atrás del proxy de Railway, la IP de origen
    real viaja en X-Forwarded-For (Railway la agrega), no en request.client."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "desconocida"


def _verificar_admin(credentials: HTTPBasicCredentials = Depends(security)):
    usuario_correcto = os.getenv("ADMIN_USER", "admin")
    clave_correcta = os.getenv("ADMIN_PASSWORD", "cambiar-esta-clave")
    ok_user = secrets.compare_digest(credentials.username, usuario_correcto)
    ok_pass = secrets.compare_digest(credentials.password, clave_correcta)
    if not (ok_user and ok_pass):
        raise HTTPException(status_code=401, detail="No autorizado", headers={"WWW-Authenticate": "Basic"})
    return credentials.username


def _verificar_operador(credentials: HTTPBasicCredentials = Depends(security)):
    """Auth separada para el panel de Jorge como operador de la plataforma
    (ve TODOS los negocios) — distinta de la auth de admin de cada negocio
    individual, que solo ve sus propios datos."""
    usuario_correcto = os.getenv("OPERADOR_USER", "jorge")
    clave_correcta = os.getenv("OPERADOR_PASSWORD", "cambiar-esta-clave-tambien")
    ok_user = secrets.compare_digest(credentials.username, usuario_correcto)
    ok_pass = secrets.compare_digest(credentials.password, clave_correcta)
    if not (ok_user and ok_pass):
        raise HTTPException(status_code=401, detail="No autorizado", headers={"WWW-Authenticate": "Basic"})
    return credentials.username


def _obtener_negocio_o_404(slug: str) -> Negocio:
    db = SessionLocal()
    try:
        negocio = db.query(Negocio).filter(Negocio.slug == slug).first()
        if not negocio:
            raise HTTPException(status_code=404, detail="Negocio no encontrado")
        if not negocio.activo:
            raise HTTPException(status_code=403, detail="Este negocio no tiene el servicio activo")
        return negocio
    finally:
        db.close()


class IniciarLlamadaRequest(BaseModel):
    numero_cliente: Optional[str] = None


class MensajeRequest(BaseModel):
    llamada_id: str
    mensaje: str


class FinalizarLlamadaRequest(BaseModel):
    llamada_id: str
    resultado: str = "completada"


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/api/{negocio_slug}/iniciar")
def iniciar(negocio_slug: str, req: IniciarLlamadaRequest, request: Request):
    # Rate limit por IP real acá (antes solo estaba en /mensaje, y ni
    # siquiera por IP) — este es el endpoint que de verdad crea trabajo
    # nuevo (una llamada), así que es el que hay que frenar para que nadie
    # pueda generar llamadas ilimitadas y, con eso, costo ilimitado de IA.
    _chequear_rate_limit(_ip_cliente(request))
    negocio = _obtener_negocio_o_404(negocio_slug)
    resultado = servicio.iniciar_llamada(negocio.id, req.numero_cliente)
    if "error" in resultado:
        raise HTTPException(status_code=400, detail=resultado["error"])
    return resultado


@app.post("/api/{negocio_slug}/mensaje")
def mensaje(negocio_slug: str, req: MensajeRequest):
    negocio = _obtener_negocio_o_404(negocio_slug)  # valida que el negocio existe/activo
    _chequear_rate_limit(f"{negocio_slug}:{req.llamada_id}")
    # Pasamos negocio.id para que el motor verifique que req.llamada_id
    # pertenece a ESTE negocio — sin esto, conocer un llamada_id de otro
    # negocio alcanzaba para leer/escribir su conversación.
    resultado = servicio.procesar_mensaje(req.llamada_id, req.mensaje, negocio_id=negocio.id)
    if "error" in resultado:
        raise HTTPException(status_code=400, detail=resultado["error"])
    return resultado


@app.post("/api/{negocio_slug}/finalizar")
def finalizar(negocio_slug: str, req: FinalizarLlamadaRequest):
    negocio = _obtener_negocio_o_404(negocio_slug)
    resultado = servicio.finalizar_llamada(req.llamada_id, req.resultado, negocio_id=negocio.id)
    if "error" in resultado:
        raise HTTPException(status_code=400, detail=resultado["error"])
    return resultado


@app.get("/api/{negocio_slug}/estadisticas")
def estadisticas(negocio_slug: str, admin: str = Depends(_verificar_admin)):
    negocio = _obtener_negocio_o_404(negocio_slug)
    return servicio.obtener_estadisticas(negocio.id)


@app.get("/api/admin/estadisticas-globales")
def estadisticas_globales(operador: str = Depends(_verificar_operador)):
    return servicio.obtener_estadisticas_globales()


@app.get("/api/{negocio_slug}/clientes")
def listar_clientes(negocio_slug: str, admin: str = Depends(_verificar_admin)):
    """Lista los clientes/socios del negocio con su estado de pago — lo que
    alimenta el panel de 'pagos atrasados' del dashboard de cada negocio."""
    from models import SessionLocal as _SL, ClienteNegocio as _CN
    negocio = _obtener_negocio_o_404(negocio_slug)
    db = _SL()
    try:
        clientes = db.query(_CN).filter(_CN.negocio_id == negocio.id).all()
        return [
            {
                "id": c.id,
                "nombre": c.nombre,
                "telefono": c.telefono,
                "estado_membresia": c.estado_membresia,
                "meses_adeudados": c.meses_adeudados,
                "proximo_vencimiento": c.proximo_vencimiento.isoformat() if c.proximo_vencimiento else None,
                "plan_membresia": c.plan_membresia,
            }
            for c in clientes
        ]
    finally:
        db.close()


@app.get("/api/{negocio_slug}/llamadas-recientes")
def llamadas_recientes(negocio_slug: str, admin: str = Depends(_verificar_admin), limite: int = 10):
    from models import SessionLocal as _SL, Llamada as _L
    negocio = _obtener_negocio_o_404(negocio_slug)
    db = _SL()
    try:
        llamadas = (
            db.query(_L)
            .filter(_L.negocio_id == negocio.id)
            .order_by(_L.fecha_inicio.desc())
            .limit(limite)
            .all()
        )
        return [
            {
                "id": l.id,
                "numero_cliente": l.numero_cliente,
                "idioma": l.idioma_detectado,
                "resultado": l.resultado,
                "duracion_segundos": l.duracion_segundos,
                "fecha_inicio": l.fecha_inicio.isoformat() if l.fecha_inicio else None,
            }
            for l in llamadas
        ]
    finally:
        db.close()


@app.get("/api/admin/negocios")
def listar_negocios(operador: str = Depends(_verificar_operador)):
    """Lista todos los negocios con su plan y estado de suscripción — lo que
    alimenta el panel de operador de Jorge (clientes, mezcla de planes, alertas)."""
    from models import SessionLocal as _SL, Negocio as _N, Suscripcion as _S
    db = _SL()
    try:
        negocios = db.query(_N).all()
        resultado = []
        for n in negocios:
            susc = db.query(_S).filter(_S.negocio_id == n.id).first()
            resultado.append({
                "slug": n.slug,
                "nombre": n.nombre,
                "vertical": n.vertical,
                "activo": n.activo,
                "plan": susc.plan if susc else None,
                "precio_mensual": susc.precio_mensual if susc else None,
                "estado_suscripcion": susc.estado if susc else None,
                "fecha_proximo_cobro": susc.fecha_proximo_cobro.isoformat() if susc and susc.fecha_proximo_cobro else None,
            })
        return resultado
    finally:
        db.close()


# ---------- Facturación SaaS (Stripe) ----------

class CrearSuscripcionRequest(BaseModel):
    plan: str
    email_facturacion: str


@app.post("/api/{negocio_slug}/suscripcion")
def crear_suscripcion(negocio_slug: str, req: CrearSuscripcionRequest, operador: str = Depends(_verificar_operador)):
    """Da de alta el cobro mensual de NEXXUS a un negocio. Solo el operador
    (Jorge) puede hacer esto, no el negocio mismo."""
    negocio = _obtener_negocio_o_404(negocio_slug)
    try:
        resultado = billing_service.crear_suscripcion(negocio.id, req.plan, req.email_facturacion)
    except billing_service.PlanInvalido as e:
        raise HTTPException(status_code=400, detail=str(e))
    if "error" in resultado:
        raise HTTPException(status_code=400, detail=resultado["error"])
    return resultado


@app.post("/webhooks/stripe")
async def webhook_stripe(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")
    try:
        resultado = billing_service.verificar_y_procesar_webhook(payload, sig_header)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Webhook inválido: {e}")
    return resultado


@app.post("/api/admin/chequear-suspensiones")
def chequear_suspensiones(operador: str = Depends(_verificar_operador)):
    """Corre el chequeo de períodos de gracia vencidos. Pensado para
    llamarse desde una tarea programada (ej. una vez por día) hasta que
    Jorge decida automatizarlo con un cron real."""
    suspendidos = billing_service.chequear_suspensiones_vencidas()
    return {"negocios_suspendidos": suspendidos}


@app.get("/admin", response_class=HTMLResponse)
def panel_operador():
    """Panel de Jorge como operador — ve todos los negocios."""
    html = (Path(__file__).parent / "panel.html").read_text(encoding="utf-8")
    return html


@app.get("/{negocio_slug}/admin", response_class=HTMLResponse)
def panel_negocio(negocio_slug: str):
    """Panel de UN negocio (ej. el dueño del gym) — solo ve sus propios datos."""
    _obtener_negocio_o_404(negocio_slug)
    html = (Path(__file__).parent / "panel.html").read_text(encoding="utf-8")
    return html


# ---------- Alta de negocios y socios (onboarding) ----------
# Esto resuelve el problema práctico de "cómo entran los datos reales al
# sistema" cuando Jorge consiga un gym de verdad — hasta ahora la única
# forma de cargar un negocio o un socio era editar la base a mano con un
# script (seed_demo.py). Con esto, Jorge da de alta un negocio nuevo y sus
# socios por API (o desde el panel, si se conecta un formulario más
# adelante) sin tocar código.

class CrearNegocioRequest(BaseModel):
    slug: str
    nombre: str
    vertical: str = "gym"
    idioma_principal: str = "es"
    idioma_secundario: str = "en"
    nombre_asistente: str = "María"
    plan: str = "starter"
    email_facturacion: _Optional[str] = None


@app.post("/api/admin/negocios")
def crear_negocio(req: CrearNegocioRequest, operador: str = Depends(_verificar_operador)):
    if req.vertical not in VERTICALES:
        raise HTTPException(status_code=400, detail=f"Vertical desconocido: {req.vertical}. Disponibles: {list(VERTICALES.keys())}")

    db = SessionLocal()
    try:
        if db.query(Negocio).filter(Negocio.slug == req.slug).first():
            raise HTTPException(status_code=409, detail=f"Ya existe un negocio con slug '{req.slug}'")

        negocio = Negocio(
            id=str(uuid.uuid4())[:8],
            slug=req.slug,
            nombre=req.nombre,
            vertical=req.vertical,
            idioma_principal=req.idioma_principal,
            idioma_secundario=req.idioma_secundario,
            nombre_asistente=req.nombre_asistente,
            plan=req.plan,
            activo=True,
        )
        db.add(negocio)
        db.commit()
        negocio_id = negocio.id
    finally:
        db.close()

    resultado = {"negocio_id": negocio_id, "slug": req.slug, "suscripcion": None}
    if req.email_facturacion:
        resultado["suscripcion"] = billing_service.crear_suscripcion(negocio_id, req.plan, req.email_facturacion)
    return resultado


# ---------- Agente de Configuración (Fase 3: back office interno de NEXXUS) ----------
# Copiloto de Jorge para dar de alta negocios y cargar socios charlando en
# vez de llamar a la API a mano — ver agente_configuracion.py. Gateado por
# _verificar_operador porque es SOLO para Jorge (nunca para un dueño de gym).

class AgenteConfiguracionRequest(BaseModel):
    mensaje: str
    historial: list = []


@app.post("/api/admin/agente-configuracion")
def agente_configuracion_endpoint(req: AgenteConfiguracionRequest, operador: str = Depends(_verificar_operador)):
    resultado = agente_configuracion.procesar_mensaje(req.mensaje, req.historial)
    return resultado


class CrearClienteRequest(BaseModel):
    nombre: str
    telefono: _Optional[str] = None
    email: _Optional[str] = None
    estado_membresia: str = "activo"
    meses_adeudados: int = 0
    proximo_vencimiento: _Optional[str] = None  # ISO date, ej. "2026-10-01"
    plan_membresia: _Optional[str] = None
    altura_cm: _Optional[float] = None
    fecha_ingreso: _Optional[str] = None  # ISO date; si no se manda, es "hoy"


@app.post("/api/{negocio_slug}/clientes")
def crear_cliente(negocio_slug: str, req: CrearClienteRequest, admin: str = Depends(_verificar_admin)):
    negocio = _obtener_negocio_o_404(negocio_slug)
    db = SessionLocal()
    try:
        cliente = ClienteNegocio(
            id=str(uuid.uuid4())[:8],
            negocio_id=negocio.id,
            nombre=req.nombre,
            telefono=req.telefono,
            email=req.email,
            estado_membresia=req.estado_membresia,
            meses_adeudados=req.meses_adeudados,
            proximo_vencimiento=datetime.fromisoformat(req.proximo_vencimiento) if req.proximo_vencimiento else None,
            plan_membresia=req.plan_membresia,
            altura_cm=req.altura_cm,
            fecha_ingreso=datetime.fromisoformat(req.fecha_ingreso) if req.fecha_ingreso else datetime.utcnow(),
        )
        db.add(cliente)
        db.commit()
        return {"cliente_id": cliente.id, "nombre": cliente.nombre}
    finally:
        db.close()


class ActualizarClienteRequest(BaseModel):
    estado_membresia: _Optional[str] = None
    meses_adeudados: _Optional[int] = None
    proximo_vencimiento: _Optional[str] = None
    plan_membresia: _Optional[str] = None
    altura_cm: _Optional[float] = None


@app.patch("/api/{negocio_slug}/clientes/{cliente_id}")
def actualizar_cliente(negocio_slug: str, cliente_id: str, req: ActualizarClienteRequest, admin: str = Depends(_verificar_admin)):
    """Para registrar que un socio pagó (baja meses_adeudados, corre el
    próximo vencimiento) o cambiar su estado — sin esto, una vez cargado un
    socio no había forma de mantener su información al día."""
    negocio = _obtener_negocio_o_404(negocio_slug)
    db = SessionLocal()
    try:
        cliente = db.query(ClienteNegocio).filter(
            ClienteNegocio.id == cliente_id, ClienteNegocio.negocio_id == negocio.id
        ).first()
        if not cliente:
            raise HTTPException(status_code=404, detail="Cliente no encontrado en este negocio")

        if req.estado_membresia is not None:
            cliente.estado_membresia = req.estado_membresia
        if req.meses_adeudados is not None:
            cliente.meses_adeudados = req.meses_adeudados
        if req.proximo_vencimiento is not None:
            cliente.proximo_vencimiento = datetime.fromisoformat(req.proximo_vencimiento)
        if req.plan_membresia is not None:
            cliente.plan_membresia = req.plan_membresia
        if req.altura_cm is not None:
            cliente.altura_cm = req.altura_cm

        db.commit()
        return {"cliente_id": cliente.id, "actualizado": True}
    finally:
        db.close()


def _obtener_cliente_o_404(negocio: Negocio, cliente_id: str) -> ClienteNegocio:
    db = SessionLocal()
    try:
        cliente = db.query(ClienteNegocio).filter(
            ClienteNegocio.id == cliente_id, ClienteNegocio.negocio_id == negocio.id
        ).first()
        if not cliente:
            raise HTTPException(status_code=404, detail="Cliente no encontrado en este negocio")
        return cliente
    finally:
        db.close()


# ---------- Seguimiento de progreso (peso, medidas, BMI, asistencia) ----------
# Genérico (no específico del vertical Gym) — ver progreso_service.py.

class RegistrarMedicionRequest(BaseModel):
    peso_kg: _Optional[float] = None
    altura_cm: _Optional[float] = None
    cintura_cm: _Optional[float] = None
    cadera_cm: _Optional[float] = None
    pecho_cm: _Optional[float] = None
    brazo_cm: _Optional[float] = None
    notas: _Optional[str] = None
    fecha: _Optional[str] = None  # ISO date/datetime; si no se manda, es "ahora"


@app.post("/api/{negocio_slug}/clientes/{cliente_id}/mediciones")
def crear_medicion(negocio_slug: str, cliente_id: str, req: RegistrarMedicionRequest, admin: str = Depends(_verificar_admin)):
    negocio = _obtener_negocio_o_404(negocio_slug)
    _obtener_cliente_o_404(negocio, cliente_id)  # valida que el socio existe Y es de este negocio
    resultado = progreso_service.registrar_medicion(
        cliente_id,
        peso_kg=req.peso_kg,
        altura_cm=req.altura_cm,
        cintura_cm=req.cintura_cm,
        cadera_cm=req.cadera_cm,
        pecho_cm=req.pecho_cm,
        brazo_cm=req.brazo_cm,
        notas=req.notas,
        fecha=datetime.fromisoformat(req.fecha) if req.fecha else None,
    )
    if "error" in resultado:
        raise HTTPException(status_code=400, detail=resultado["error"])
    return resultado


@app.get("/api/{negocio_slug}/clientes/{cliente_id}/mediciones")
def listar_mediciones(negocio_slug: str, cliente_id: str, admin: str = Depends(_verificar_admin)):
    negocio = _obtener_negocio_o_404(negocio_slug)
    _obtener_cliente_o_404(negocio, cliente_id)
    return progreso_service.obtener_mediciones(cliente_id)


@app.get("/api/{negocio_slug}/clientes/{cliente_id}/perfil")
def perfil_cliente(negocio_slug: str, cliente_id: str, admin: str = Depends(_verificar_admin)):
    """Perfil completo de un socio — lo que alimenta el dashboard del
    cliente: tiempo como socio, última medición + BMI, progreso desde la
    primera medición, y qué tan habitual viene."""
    negocio = _obtener_negocio_o_404(negocio_slug)
    _obtener_cliente_o_404(negocio, cliente_id)
    resultado = progreso_service.obtener_perfil_cliente(cliente_id)
    if "error" in resultado:
        raise HTTPException(status_code=404, detail=resultado["error"])
    resultado["riesgo"] = riesgo_service.calcular_riesgo_por_id(cliente_id)
    return resultado


@app.get("/api/{negocio_slug}/alertas")
def alertas(negocio_slug: str, admin: str = Depends(_verificar_admin)):
    """Socios activos con señales de que se pueden ir (deuda, asistencia
    que cae o que desapareció) y socios pausados/cancelados que podrían
    reactivarse — lo que alimenta la sección de Alertas del panel."""
    negocio = _obtener_negocio_o_404(negocio_slug)
    return riesgo_service.obtener_alertas_negocio(negocio.id)


@app.get("/{negocio_slug}/admin/clientes/{cliente_id}", response_class=HTMLResponse)
def panel_cliente(negocio_slug: str, cliente_id: str):
    """Dashboard individual de un socio (peso/BMI/progreso/asistencia)."""
    negocio = _obtener_negocio_o_404(negocio_slug)
    _obtener_cliente_o_404(negocio, cliente_id)
    html = (Path(__file__).parent / "panel_cliente.html").read_text(encoding="utf-8")
    return html


@app.get("/{negocio_slug}", response_class=HTMLResponse)
def chat_negocio(negocio_slug: str):
    _obtener_negocio_o_404(negocio_slug)
    html = (Path(__file__).parent / "recepcionista.html").read_text(encoding="utf-8")
    # El frontend original llama a /api/recepcionista/... — lo redirigimos
    # al endpoint multi-tenant de este negocio sin tocar el archivo HTML.
    html = html.replace("/api/recepcionista/", f"/api/{negocio_slug}/")
    return html


if __name__ == "__main__":
    import os
    import uvicorn
    # Railway (y otros hosts) asignan el puerto real vía la variable de
    # entorno PORT — si no está definida (desarrollo local), sigue usando
    # 8000 como antes.
    puerto = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=puerto)
