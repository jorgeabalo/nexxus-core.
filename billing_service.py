"""
Facturación SaaS — lo que Jorge (NEXXUS) le cobra a cada negocio por usar la
plataforma. Separado por completo del motor de IA (generic_service.py) y de
lo que cada negocio le cobra a SUS propios clientes (ej. la membresía de un
socio del gym) — eso es otro tema, específico de cada vertical.

Reglas ya definidas en la planificación (Fase 2, modelo SaaS):
- Planes: Starter $99, Professional $199, Enterprise $499 por mes.
- Recordatorios automáticos 7/3/1 días antes del cobro (queda como tarea de
  notificaciones — acá se deja el punto de enganche, el envío real de
  email/SMS es un paso aparte que no depende de esto).
- Período de gracia: 3 días después de un pago fallido antes de suspender.
- Suspensión automática: al vencer la gracia, negocio.activo=False y sus
  números de Twilio (cuando existan) quedan deshabilitados — hoy alcanza
  con negocio.activo, que ya es lo que usa main.py para rechazar pedidos.
- Reactivación automática al recibir un pago exitoso.

Corre en modo TEST de Stripe — no depende de que la empresa de Jorge esté
formada (Stripe test mode no pide verificación de negocio, solo bloquea
cuando se quiere activar cobros reales/modo live).
"""

import os
from datetime import datetime, timedelta

import stripe
from dotenv import load_dotenv

from models import SessionLocal, Negocio, Suscripcion

load_dotenv()

stripe.api_key = os.getenv("STRIPE_SECRET_KEY")  # clave de test (sk_test_...) hasta que la cuenta esté verificada
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")

DIAS_GRACIA = 3

PRECIOS_PLAN = {
    "starter": {"precio": 99.0, "price_id_env": "STRIPE_PRICE_STARTER"},
    "professional": {"precio": 199.0, "price_id_env": "STRIPE_PRICE_PROFESSIONAL"},
    "enterprise": {"precio": 499.0, "price_id_env": "STRIPE_PRICE_ENTERPRISE"},
}


class PlanInvalido(Exception):
    pass


def crear_suscripcion(negocio_id: str, plan: str, email_facturacion: str) -> dict:
    """Crea el customer + subscription en Stripe (test mode) para un
    negocio nuevo y guarda los IDs en la BD. Si STRIPE_SECRET_KEY no está
    configurada todavía, crea el registro local igual (estado 'activa' sin
    IDs de Stripe) para no bloquear el resto del desarrollo — se completa
    solo cuando Jorge tenga su cuenta de Stripe."""
    if plan not in PRECIOS_PLAN:
        raise PlanInvalido(f"Plan desconocido: {plan}")

    db = SessionLocal()
    try:
        negocio = db.query(Negocio).filter(Negocio.id == negocio_id).first()
        if not negocio:
            return {"error": "Negocio no encontrado"}

        info_plan = PRECIOS_PLAN[plan]
        stripe_customer_id = None
        stripe_subscription_id = None
        fecha_proximo_cobro = datetime.utcnow() + timedelta(days=30)

        price_id = os.getenv(info_plan["price_id_env"])
        if stripe.api_key and price_id:
            customer = stripe.Customer.create(email=email_facturacion, name=negocio.nombre)
            subscription = stripe.Subscription.create(
                customer=customer.id,
                items=[{"price": price_id}],
                payment_behavior="default_incomplete",
            )
            stripe_customer_id = customer.id
            stripe_subscription_id = subscription.id
            fecha_proximo_cobro = datetime.utcfromtimestamp(subscription.current_period_end)
        else:
            print(
                "⚠️  STRIPE_SECRET_KEY / price id no configurados todavía — se crea la "
                "suscripción solo en la base local (sin Stripe real) para no bloquear el desarrollo."
            )

        import uuid
        suscripcion = Suscripcion(
            id=str(uuid.uuid4())[:8],
            negocio_id=negocio_id,
            plan=plan,
            precio_mensual=info_plan["precio"],
            estado="activa",
            stripe_customer_id=stripe_customer_id,
            stripe_subscription_id=stripe_subscription_id,
            fecha_proximo_cobro=fecha_proximo_cobro,
        )
        db.add(suscripcion)
        db.commit()
        return {
            "suscripcion_id": suscripcion.id,
            "plan": plan,
            "precio_mensual": info_plan["precio"],
            "stripe_conectado": stripe_customer_id is not None,
        }
    finally:
        db.close()


def _obtener_suscripcion_por_stripe_id(db, stripe_subscription_id: str) -> Suscripcion:
    return db.query(Suscripcion).filter(Suscripcion.stripe_subscription_id == stripe_subscription_id).first()


def manejar_pago_exitoso(stripe_subscription_id: str, nueva_fecha_proximo_cobro: datetime = None) -> dict:
    """invoice.payment_succeeded — reactiva el negocio si estaba en
    gracia/suspendido, limpia el contador de gracia."""
    db = SessionLocal()
    try:
        suscripcion = _obtener_suscripcion_por_stripe_id(db, stripe_subscription_id)
        if not suscripcion:
            return {"error": "Suscripción no encontrada para ese stripe_subscription_id"}

        suscripcion.estado = "activa"
        suscripcion.fecha_pago_fallido = None
        suscripcion.fecha_proximo_cobro = nueva_fecha_proximo_cobro or (datetime.utcnow() + timedelta(days=30))

        negocio = db.query(Negocio).filter(Negocio.id == suscripcion.negocio_id).first()
        reactivado = False
        if negocio and not negocio.activo:
            negocio.activo = True
            reactivado = True

        db.commit()
        return {"negocio_id": suscripcion.negocio_id, "estado": suscripcion.estado, "reactivado": reactivado}
    finally:
        db.close()


def manejar_pago_fallido(stripe_subscription_id: str) -> dict:
    """invoice.payment_failed — arranca (o mantiene) el período de gracia.
    No suspende todavía acá — la suspensión real ocurre cuando se cumplen
    los 3 días (ver chequear_suspensiones_vencidas)."""
    db = SessionLocal()
    try:
        suscripcion = _obtener_suscripcion_por_stripe_id(db, stripe_subscription_id)
        if not suscripcion:
            return {"error": "Suscripción no encontrada para ese stripe_subscription_id"}

        if suscripcion.fecha_pago_fallido is None:
            suscripcion.fecha_pago_fallido = datetime.utcnow()
        suscripcion.estado = "periodo_gracia"
        db.commit()
        return {
            "negocio_id": suscripcion.negocio_id,
            "estado": suscripcion.estado,
            "gracia_desde": suscripcion.fecha_pago_fallido.isoformat(),
        }
    finally:
        db.close()


def chequear_suspensiones_vencidas() -> list:
    """Recorre todas las suscripciones en período de gracia y suspende las
    que ya pasaron los 3 días. Pensado para correr periódicamente (cron /
    tarea programada) — no depende de un webhook puntual."""
    db = SessionLocal()
    try:
        limite = datetime.utcnow() - timedelta(days=DIAS_GRACIA)
        vencidas = (
            db.query(Suscripcion)
            .filter(Suscripcion.estado == "periodo_gracia", Suscripcion.fecha_pago_fallido <= limite)
            .all()
        )
        suspendidas = []
        for s in vencidas:
            s.estado = "suspendida"
            negocio = db.query(Negocio).filter(Negocio.id == s.negocio_id).first()
            if negocio:
                negocio.activo = False
            suspendidas.append(s.negocio_id)
        db.commit()
        return suspendidas
    finally:
        db.close()


def verificar_y_procesar_webhook(payload: bytes, sig_header: str) -> dict:
    """Punto de entrada del webhook de Stripe. Verifica la firma (si hay
    STRIPE_WEBHOOK_SECRET configurado) y despacha al handler correspondiente."""
    if not STRIPE_WEBHOOK_SECRET:
        raise RuntimeError("STRIPE_WEBHOOK_SECRET no configurado; webhook rechazado")
    if not sig_header:
        raise ValueError("Falta Stripe-Signature")
    event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)

    tipo = event["type"] if isinstance(event, dict) else event.type
    data_obj = event["data"]["object"] if isinstance(event, dict) else event.data.object
    subscription_id = data_obj.get("subscription") or data_obj.get("id")

    if tipo == "invoice.payment_succeeded":
        return manejar_pago_exitoso(subscription_id)
    if tipo == "invoice.payment_failed":
        return manejar_pago_fallido(subscription_id)
    if tipo == "customer.subscription.deleted":
        db = SessionLocal()
        try:
            suscripcion = _obtener_suscripcion_por_stripe_id(db, subscription_id)
            if suscripcion:
                suscripcion.estado = "cancelada"
                negocio = db.query(Negocio).filter(Negocio.id == suscripcion.negocio_id).first()
                if negocio:
                    negocio.activo = False
                db.commit()
            return {"tipo": tipo, "procesado": suscripcion is not None}
        finally:
            db.close()

    return {"tipo": tipo, "procesado": False, "motivo": "evento no manejado"}
