"""
Tests de aislamiento multi-tenant: dos negocios distintos nunca deben
mezclar datos entre sí, ni siquiera cuando comparten un mismo número de
teléfono de cliente (caso límite ya probado a mano durante la construcción
de Fase 2 — acá queda fijado como test repetible).
"""

from generic_service import NexxusIAService
from conftest import crear_negocio_gym, crear_socio


def test_mismo_telefono_en_dos_negocios_se_identifica_por_separado(db):
    servicio = NexxusIAService(api_key=None)

    negocio_a = crear_negocio_gym(db, slug="fuerza-total", nombre="Fuerza Total")
    negocio_b = crear_negocio_gym(db, slug="gym-dos", nombre="Gym Dos")

    telefono_compartido = "+17135550142"
    crear_socio(db, negocio_a, "María Torres", telefono=telefono_compartido)
    crear_socio(db, negocio_b, "Otro Socio", telefono=telefono_compartido)

    resultado_a = servicio.iniciar_llamada(negocio_a.id, telefono_compartido)
    resultado_b = servicio.iniciar_llamada(negocio_b.id, telefono_compartido)

    assert resultado_a["cliente_identificado"] is True
    assert resultado_b["cliente_identificado"] is True
    assert resultado_a["llamada_id"] != resultado_b["llamada_id"]

    from models import Llamada
    llamada_a = db.query(Llamada).filter(Llamada.id == resultado_a["llamada_id"]).first()
    llamada_b = db.query(Llamada).filter(Llamada.id == resultado_b["llamada_id"]).first()
    assert llamada_a.negocio_id == negocio_a.id
    assert llamada_b.negocio_id == negocio_b.id
    assert llamada_a.cliente_negocio_id != llamada_b.cliente_negocio_id


def test_telefono_desconocido_no_identifica_cliente(db):
    servicio = NexxusIAService(api_key=None)
    negocio = crear_negocio_gym(db)

    resultado = servicio.iniciar_llamada(negocio.id, "+10000000000")

    assert resultado["cliente_identificado"] is False


def test_iniciar_llamada_negocio_inexistente(db):
    servicio = NexxusIAService(api_key=None)

    resultado = servicio.iniciar_llamada("negocio-que-no-existe", "+17135550142")

    assert "error" in resultado


def test_iniciar_llamada_negocio_inactivo_rechaza(db):
    servicio = NexxusIAService(api_key=None)
    negocio = crear_negocio_gym(db, slug="suspendido", activo=False)

    resultado = servicio.iniciar_llamada(negocio.id, "+17135550142")

    assert "error" in resultado


def test_estadisticas_no_se_mezclan_entre_negocios(db):
    servicio = NexxusIAService(api_key=None)

    negocio_a = crear_negocio_gym(db, slug="fuerza-total", nombre="Fuerza Total")
    negocio_b = crear_negocio_gym(db, slug="gym-dos", nombre="Gym Dos")

    r1 = servicio.iniciar_llamada(negocio_a.id, "+17135550001")
    servicio.finalizar_llamada(r1["llamada_id"], "completada")
    r2 = servicio.iniciar_llamada(negocio_a.id, "+17135550002")
    servicio.finalizar_llamada(r2["llamada_id"], "completada")

    r3 = servicio.iniciar_llamada(negocio_b.id, "+17135550003")
    servicio.finalizar_llamada(r3["llamada_id"], "completada")

    stats_a = servicio.obtener_estadisticas(negocio_a.id)
    stats_b = servicio.obtener_estadisticas(negocio_b.id)
    stats_globales = servicio.obtener_estadisticas_globales()

    assert stats_a["total_llamadas"] == 2
    assert stats_b["total_llamadas"] == 1
    assert stats_globales["total_llamadas"] == 3
    assert stats_globales["negocios_totales"] == 2
    assert stats_globales["negocios_activos"] == 2


def test_detectar_idioma_sin_api_key_usa_primer_candidato(db):
    servicio = NexxusIAService(api_key=None)

    idioma = servicio.detectar_idioma("hola, ¿cuánto debo?", ["es", "en"])

    assert idioma == "es"


def test_detectar_idioma_un_solo_candidato_no_llama_a_claude(db):
    servicio = NexxusIAService(api_key=None)

    idioma = servicio.detectar_idioma("cualquier texto", ["en"])

    assert idioma == "en"
