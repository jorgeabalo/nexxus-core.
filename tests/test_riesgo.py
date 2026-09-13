"""
Tests de riesgo_service.py: detectar socios activos que muestran señales
de irse (deuda, asistencia que cae o desaparece) y armar la lista de
socios pausados/cancelados para reactivar. Reglas simples y explicables a
propósito — cada test verifica también que el MOTIVO expuesto sea el
correcto, no solo el nivel.
"""

from datetime import datetime, timedelta

import riesgo_service
from conftest import crear_negocio_gym, crear_socio, crear_checkin


def test_sin_senales_no_hay_riesgo(db):
    negocio = crear_negocio_gym(db)
    socio = crear_socio(db, negocio, "Diana Reyes", meses_adeudados=0, fecha_ingreso_hace_dias=400)
    checkin = crear_checkin(db, negocio, socio, hace_dias=1)

    riesgo = riesgo_service.calcular_riesgo_cliente(socio, [checkin])
    assert riesgo is None


def test_debe_un_mes_es_alerta(db):
    negocio = crear_negocio_gym(db)
    socio = crear_socio(db, negocio, "Carlos Núñez", meses_adeudados=1)

    riesgo = riesgo_service.calcular_riesgo_cliente(socio, [])

    assert riesgo["nivel"] == "alerta"
    assert "Debe 1 mes" in riesgo["motivos"]


def test_debe_dos_o_mas_meses_es_critico(db):
    negocio = crear_negocio_gym(db)
    socio = crear_socio(db, negocio, "María Torres", meses_adeudados=3)

    riesgo = riesgo_service.calcular_riesgo_cliente(socio, [])

    assert riesgo["nivel"] == "critico"
    assert "Debe 3 meses" in riesgo["motivos"]


def test_no_viene_hace_poco_no_es_riesgo(db):
    negocio = crear_negocio_gym(db)
    socio = crear_socio(db, negocio, "Jorge Paredes", meses_adeudados=0, fecha_ingreso_hace_dias=60)
    checkin = crear_checkin(db, negocio, socio, hace_dias=5)

    riesgo = riesgo_service.calcular_riesgo_cliente(socio, [checkin])

    assert riesgo is None


def test_no_viene_hace_10_a_20_dias_es_alerta(db):
    negocio = crear_negocio_gym(db)
    socio = crear_socio(db, negocio, "Jorge Paredes", meses_adeudados=0, fecha_ingreso_hace_dias=60)
    checkin = crear_checkin(db, negocio, socio, hace_dias=15)

    riesgo = riesgo_service.calcular_riesgo_cliente(socio, [checkin])

    assert riesgo["nivel"] == "alerta"
    assert "No viene hace 15 días" in riesgo["motivos"]


def test_no_viene_hace_21_o_mas_es_critico(db):
    negocio = crear_negocio_gym(db)
    socio = crear_socio(db, negocio, "Jorge Paredes", meses_adeudados=0, fecha_ingreso_hace_dias=60)
    checkin = crear_checkin(db, negocio, socio, hace_dias=25)

    riesgo = riesgo_service.calcular_riesgo_cliente(socio, [checkin])

    assert riesgo["nivel"] == "critico"
    assert "No viene hace 25 días" in riesgo["motivos"]


def test_nunca_vino_pero_es_socio_nuevo_no_es_riesgo(db):
    negocio = crear_negocio_gym(db)
    socio = crear_socio(db, negocio, "Recién Llegado", meses_adeudados=0, fecha_ingreso_hace_dias=3)

    riesgo = riesgo_service.calcular_riesgo_cliente(socio, [])

    assert riesgo is None


def test_nunca_vino_y_ya_es_socio_antiguo_es_critico(db):
    negocio = crear_negocio_gym(db)
    socio = crear_socio(db, negocio, "Fantasma", meses_adeudados=0, fecha_ingreso_hace_dias=40)

    riesgo = riesgo_service.calcular_riesgo_cliente(socio, [])

    assert riesgo["nivel"] == "critico"
    assert "Nunca registró una visita" in riesgo["motivos"]


def test_caida_de_asistencia_es_alerta(db):
    negocio = crear_negocio_gym(db)
    socio = crear_socio(db, negocio, "Carlos Núñez", meses_adeudados=0, fecha_ingreso_hace_dias=90)
    ahora = datetime.utcnow()
    # Período anterior (30-60 días atrás): 6 visitas. Período actual: 2.
    checkins = [crear_checkin(db, negocio, socio, hace_dias=d) for d in [35, 38, 42, 45, 50, 55]]
    checkins += [crear_checkin(db, negocio, socio, hace_dias=d) for d in [2, 20]]

    riesgo = riesgo_service.calcular_riesgo_cliente(socio, checkins, ahora)

    assert riesgo is not None
    assert any("Bajó su asistencia" in m for m in riesgo["motivos"])


def test_caida_de_asistencia_sin_historial_previo_no_cuenta(db):
    """Un socio con 1 sola visita hace 40 días y ninguna reciente no debe
    disparar un '100% de caída' falso — no tenía actividad real antes."""
    negocio = crear_negocio_gym(db)
    socio = crear_socio(db, negocio, "Nuevo Con Una Visita", meses_adeudados=0, fecha_ingreso_hace_dias=45)
    checkin = crear_checkin(db, negocio, socio, hace_dias=40)

    riesgo = riesgo_service.calcular_riesgo_cliente(socio, [checkin])

    # Sin deuda, sin ausencia larga (40 días > 21 activa "no viene hace"),
    # así que en este caso puntual sí dispara por inactividad, pero NO por
    # la caída de asistencia (visitas_previa < 2 no cuenta como señal).
    assert riesgo is not None
    assert not any("Bajó su asistencia" in m for m in riesgo["motivos"])


def test_debe_dos_meses_y_no_viene_combina_motivos_en_critico(db):
    negocio = crear_negocio_gym(db)
    socio = crear_socio(db, negocio, "Doble Señal", meses_adeudados=2, fecha_ingreso_hace_dias=100)
    checkin = crear_checkin(db, negocio, socio, hace_dias=12)

    riesgo = riesgo_service.calcular_riesgo_cliente(socio, [checkin])

    assert riesgo["nivel"] == "critico"
    assert "Debe 2 meses" in riesgo["motivos"]
    assert "No viene hace 12 días" in riesgo["motivos"]


def test_calcular_riesgo_por_id_socio_inactivo_no_aplica(db):
    negocio = crear_negocio_gym(db)
    socio = crear_socio(db, negocio, "Pausado", meses_adeudados=5, estado_membresia="pausado")

    riesgo = riesgo_service.calcular_riesgo_por_id(socio.id)

    assert riesgo is None


def test_calcular_riesgo_por_id_socio_inexistente(db):
    assert riesgo_service.calcular_riesgo_por_id("no-existe") is None


def test_obtener_alertas_negocio_separa_en_riesgo_y_reactivacion(db):
    negocio = crear_negocio_gym(db)
    critico = crear_socio(db, negocio, "María Torres", meses_adeudados=3, estado_membresia="activo")
    alerta = crear_socio(db, negocio, "Carlos Núñez", meses_adeudados=1, estado_membresia="activo")
    normal = crear_socio(db, negocio, "Diana Reyes", meses_adeudados=0, estado_membresia="activo", fecha_ingreso_hace_dias=400)
    crear_checkin(db, negocio, normal, hace_dias=1)
    pausado = crear_socio(db, negocio, "Luis Fernández", meses_adeudados=0, estado_membresia="pausado")
    cancelado = crear_socio(db, negocio, "Sofía Delgado", meses_adeudados=0, estado_membresia="cancelado")

    alertas = riesgo_service.obtener_alertas_negocio(negocio.id)

    nombres_en_riesgo = [r["nombre"] for r in alertas["en_riesgo"]]
    assert "María Torres" in nombres_en_riesgo
    assert "Carlos Núñez" in nombres_en_riesgo
    assert "Diana Reyes" not in nombres_en_riesgo
    # Crítico va primero.
    assert alertas["en_riesgo"][0]["nombre"] == "María Torres"
    assert alertas["total_criticos"] == 1
    assert alertas["total_en_riesgo"] == 2

    nombres_reactivacion = [r["nombre"] for r in alertas["reactivacion"]]
    assert "Luis Fernández" in nombres_reactivacion
    assert "Sofía Delgado" in nombres_reactivacion
    assert alertas["total_reactivacion"] == 2


def test_obtener_alertas_negocio_aislado_por_negocio(db):
    negocio_a = crear_negocio_gym(db, slug="fuerza-total")
    negocio_b = crear_negocio_gym(db, slug="gym-dos")
    crear_socio(db, negocio_a, "Socio A", meses_adeudados=3)
    crear_socio(db, negocio_b, "Socio B", meses_adeudados=3)

    alertas_a = riesgo_service.obtener_alertas_negocio(negocio_a.id)

    nombres = [r["nombre"] for r in alertas_a["en_riesgo"]]
    assert "Socio A" in nombres
    assert "Socio B" not in nombres
