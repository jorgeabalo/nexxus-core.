"""
Tests del ciclo de facturación SaaS (billing_service.py): lo que Jorge le
cobra a cada negocio por usar NEXXUS. Corren sin claves reales de Stripe
(ver conftest.py) — mismo enfoque "modo local" que se usó y probó a mano
durante la construcción de Fase 2, ahora como suite repetible.
"""

from datetime import datetime, timedelta

import pytest

import billing_service
from conftest import crear_negocio_gym


def test_crear_suscripcion_sin_claves_stripe_crea_registro_local(db):
    negocio = crear_negocio_gym(db)

    resultado = billing_service.crear_suscripcion(negocio.id, "professional", "dueno@fuerzatotal.com")

    assert resultado["plan"] == "professional"
    assert resultado["precio_mensual"] == 199.0
    assert resultado["stripe_conectado"] is False

    from models import Suscripcion
    suscripcion = db.query(Suscripcion).filter(Suscripcion.negocio_id == negocio.id).first()
    assert suscripcion is not None
    assert suscripcion.estado == "activa"
    assert suscripcion.stripe_customer_id is None


def test_crear_suscripcion_plan_invalido_rechaza(db):
    negocio = crear_negocio_gym(db)

    with pytest.raises(billing_service.PlanInvalido):
        billing_service.crear_suscripcion(negocio.id, "plan-que-no-existe", "dueno@fuerzatotal.com")


def test_crear_suscripcion_negocio_inexistente(db):
    resultado = billing_service.crear_suscripcion("negocio-fantasma", "starter", "x@x.com")
    assert "error" in resultado


def test_ciclo_completo_pago_fallido_gracia_suspension_reactivacion(db):
    negocio = crear_negocio_gym(db)
    billing_service.crear_suscripcion(negocio.id, "starter", "dueno@fuerzatotal.com")

    from models import Suscripcion, Negocio
    suscripcion = db.query(Suscripcion).filter(Suscripcion.negocio_id == negocio.id).first()
    # Simulamos que Stripe ya le asignó un subscription_id real (en modo
    # local queda None — para probar el flujo del webhook lo fijamos acá).
    suscripcion.stripe_subscription_id = "sub_test_123"
    db.commit()

    # 1) Pago fallido -> entra en período de gracia, el negocio sigue activo.
    r_fallo = billing_service.manejar_pago_fallido("sub_test_123")
    assert r_fallo["estado"] == "periodo_gracia"
    db.refresh(negocio)
    assert negocio.activo is True

    # 2) Todavía no pasaron los 3 días de gracia -> no se suspende nadie.
    suspendidos = billing_service.chequear_suspensiones_vencidas()
    assert negocio.id not in suspendidos
    db.refresh(negocio)
    assert negocio.activo is True

    # 3) Forzamos que la fecha de pago fallido sea de hace 4 días (vencida).
    suscripcion = db.query(Suscripcion).filter(Suscripcion.negocio_id == negocio.id).first()
    suscripcion.fecha_pago_fallido = datetime.utcnow() - timedelta(days=4)
    db.commit()

    suspendidos = billing_service.chequear_suspensiones_vencidas()
    assert negocio.id in suspendidos
    db.refresh(negocio)
    assert negocio.activo is False
    db.expire_all()
    suscripcion = db.query(Suscripcion).filter(Suscripcion.negocio_id == negocio.id).first()
    assert suscripcion.estado == "suspendida"

    # 4) Pago exitoso -> reactiva el negocio y limpia el contador de gracia.
    r_exito = billing_service.manejar_pago_exitoso("sub_test_123")
    assert r_exito["reactivado"] is True
    db.refresh(negocio)
    assert negocio.activo is True
    # billing_service usa su PROPIA sesión (SessionLocal separado) para
    # escribir estos cambios — la sesión de este test los ve recién cuando
    # se le pide refrescar/expirar (si no, devuelve el objeto que ya tenía
    # en su identity map, no lo último confirmado en la base).
    db.expire_all()
    suscripcion = db.query(Suscripcion).filter(Suscripcion.negocio_id == negocio.id).first()
    assert suscripcion.estado == "activa"
    assert suscripcion.fecha_pago_fallido is None


def test_manejar_pago_fallido_suscripcion_inexistente(db):
    resultado = billing_service.manejar_pago_fallido("sub_que_no_existe")
    assert "error" in resultado


def test_webhook_sin_secret_configurado_procesa_json_crudo(db):
    import json

    negocio = crear_negocio_gym(db)
    billing_service.crear_suscripcion(negocio.id, "starter", "dueno@fuerzatotal.com")

    from models import Suscripcion
    suscripcion = db.query(Suscripcion).filter(Suscripcion.negocio_id == negocio.id).first()
    suscripcion.stripe_subscription_id = "sub_webhook_test"
    db.commit()

    payload = json.dumps({
        "type": "invoice.payment_failed",
        "data": {"object": {"subscription": "sub_webhook_test"}},
    }).encode("utf-8")

    resultado = billing_service.verificar_y_procesar_webhook(payload, sig_header="")

    assert resultado["estado"] == "periodo_gracia"


def test_webhook_evento_no_manejado(db):
    import json

    payload = json.dumps({
        "type": "algun.evento.desconocido",
        "data": {"object": {"id": "algo"}},
    }).encode("utf-8")

    resultado = billing_service.verificar_y_procesar_webhook(payload, sig_header="")

    assert resultado["procesado"] is False
