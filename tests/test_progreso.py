"""
Tests de progreso_service.py: peso/medidas históricas, BMI, tiempo como
socio, cambios a lo largo del tiempo, y frecuencia de asistencia — lo que
Jorge pidió agregar al registro de cada socio del gym.
"""

import progreso_service
from conftest import crear_negocio_gym, crear_socio, crear_medicion, crear_checkin


def test_calcular_bmi():
    # 70kg / 1.75m^2 = 22.857... -> redondeado a 22.9
    assert progreso_service.calcular_bmi(70, 175) == 22.9


def test_calcular_bmi_sin_datos_devuelve_none():
    assert progreso_service.calcular_bmi(None, 175) is None
    assert progreso_service.calcular_bmi(70, None) is None
    assert progreso_service.calcular_bmi(0, 175) is None


def test_clasificar_bmi():
    assert progreso_service.clasificar_bmi(17) == "bajo_peso"
    assert progreso_service.clasificar_bmi(22) == "normal"
    assert progreso_service.clasificar_bmi(27) == "sobrepeso"
    assert progreso_service.clasificar_bmi(33) == "obesidad"
    assert progreso_service.clasificar_bmi(None) is None


def test_registrar_medicion_calcula_bmi_con_altura_del_cliente(db):
    negocio = crear_negocio_gym(db)
    socio = crear_socio(db, negocio, "Diana Reyes", altura_cm=168)

    resultado = progreso_service.registrar_medicion(socio.id, peso_kg=66.0)

    assert resultado["peso_kg"] == 66.0
    assert resultado["bmi"] == progreso_service.calcular_bmi(66.0, 168)


def test_registrar_medicion_cliente_inexistente(db):
    resultado = progreso_service.registrar_medicion("no-existe", peso_kg=70)
    assert "error" in resultado


def test_registrar_medicion_actualiza_altura_actual_del_cliente(db):
    negocio = crear_negocio_gym(db)
    socio = crear_socio(db, negocio, "Carlos Núñez", altura_cm=178)

    progreso_service.registrar_medicion(socio.id, peso_kg=90, altura_cm=179)

    # progreso_service usa su propia sesión (SessionLocal separado) para
    # escribir — hay que expirar el identity map de esta sesión de test
    # para ver el cambio recién confirmado (mismo patrón que en test_billing.py).
    db.expire_all()
    from models import ClienteNegocio
    actualizado = db.query(ClienteNegocio).filter(ClienteNegocio.id == socio.id).first()
    assert actualizado.altura_cm == 179


def test_obtener_mediciones_orden_cronologico(db):
    negocio = crear_negocio_gym(db)
    socio = crear_socio(db, negocio, "María Torres", altura_cm=162)
    crear_medicion(db, socio, peso_kg=78.5, hace_dias=60)
    crear_medicion(db, socio, peso_kg=76.2, hace_dias=30)
    crear_medicion(db, socio, peso_kg=74.0, hace_dias=0)

    mediciones = progreso_service.obtener_mediciones(socio.id)

    assert [m["peso_kg"] for m in mediciones] == [78.5, 76.2, 74.0]
    assert all(m["bmi"] is not None for m in mediciones)


def test_perfil_cliente_sin_mediciones_ni_checkins(db):
    negocio = crear_negocio_gym(db)
    socio = crear_socio(db, negocio, "Jorge Paredes", altura_cm=174, fecha_ingreso_hace_dias=35)

    perfil = progreso_service.obtener_perfil_cliente(socio.id)

    assert perfil["nombre"] == "Jorge Paredes"
    assert perfil["dias_como_socio"] == 35
    assert perfil["ultima_medicion"] is None
    assert perfil["progreso"] is None
    assert perfil["asistencia"]["visitas_ultimos_30_dias"] == 0
    assert perfil["asistencia"]["dias_desde_ultima_visita"] is None


def test_perfil_cliente_inexistente(db):
    perfil = progreso_service.obtener_perfil_cliente("no-existe")
    assert "error" in perfil


def test_perfil_cliente_progreso_calcula_delta_desde_primera_medicion(db):
    negocio = crear_negocio_gym(db)
    socio = crear_socio(db, negocio, "María Torres", altura_cm=162)
    crear_medicion(db, socio, peso_kg=78.5, hace_dias=60)
    crear_medicion(db, socio, peso_kg=74.0, hace_dias=0)

    perfil = progreso_service.obtener_perfil_cliente(socio.id)

    assert perfil["ultima_medicion"]["peso_kg"] == 74.0
    assert perfil["progreso"]["delta_peso_kg"] == -4.5
    assert perfil["progreso"]["delta_bmi"] is not None
    assert perfil["progreso"]["delta_bmi"] < 0  # bajó de peso -> BMI bajó


def test_perfil_cliente_una_sola_medicion_no_calcula_progreso(db):
    negocio = crear_negocio_gym(db)
    socio = crear_socio(db, negocio, "Jorge Paredes", altura_cm=174)
    crear_medicion(db, socio, peso_kg=88.0, hace_dias=0)

    perfil = progreso_service.obtener_perfil_cliente(socio.id)

    assert perfil["progreso"] is None
    assert perfil["ultima_medicion"]["peso_kg"] == 88.0


def test_perfil_cliente_frecuencia_asistencia(db):
    negocio = crear_negocio_gym(db)
    socio = crear_socio(db, negocio, "Diana Reyes")
    for dias in [1, 4, 8, 12, 16, 20, 25, 40, 50]:  # 7 dentro de 30 días, 2 fuera
        crear_checkin(db, negocio, socio, hace_dias=dias)

    perfil = progreso_service.obtener_perfil_cliente(socio.id)

    assert perfil["asistencia"]["visitas_ultimos_30_dias"] == 7
    assert perfil["asistencia"]["total_checkins_historico"] == 9
    assert perfil["asistencia"]["dias_desde_ultima_visita"] == 1
    assert perfil["asistencia"]["promedio_semanal"] > 0


def test_registrar_checkin_queda_persistido(db):
    negocio = crear_negocio_gym(db)
    socio = crear_socio(db, negocio, "Luis Fernández")

    progreso_service.registrar_checkin(negocio.id, socio.id)

    from models import Checkin
    checkins = db.query(Checkin).filter(Checkin.cliente_negocio_id == socio.id).all()
    assert len(checkins) == 1
    assert checkins[0].negocio_id == negocio.id
