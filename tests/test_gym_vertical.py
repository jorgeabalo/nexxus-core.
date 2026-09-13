"""
Tests del vertical Gym (verticals/gym.py) contra datos reales de la BD.

Esto es la prueba central de la promesa que Jorge pidió desde el principio:
la IA NUNCA inventa un monto de pago — o encuentra el dato real en la base,
o dice explícitamente que no lo encontró. Estos tests prueban `ejecutar_tool`
directamente (sin pasar por Claude), que es donde vive esa garantía.
"""

from verticals import gym
from verticals.gym import ejecutar_tool
from conftest import crear_negocio_gym, crear_socio, crear_medicion


def test_consultar_estado_cuenta_por_nombre_encuentra_dato_real(db):
    negocio = crear_negocio_gym(db)
    crear_socio(db, negocio, "María Torres", telefono="+17135550142", meses_adeudados=3, proximo_vencimiento_en_dias=1)

    resultado = ejecutar_tool(db, negocio, "consultar_estado_cuenta", {"identificador": "María Torres"})

    assert resultado["encontrado"] is True
    assert resultado["nombre"] == "María Torres"
    assert resultado["meses_adeudados"] == 3
    assert resultado["proximo_vencimiento"] is not None


def test_consultar_estado_cuenta_por_telefono(db):
    negocio = crear_negocio_gym(db)
    crear_socio(db, negocio, "Carlos Núñez", telefono="+12815550177", meses_adeudados=1)

    resultado = ejecutar_tool(db, negocio, "consultar_estado_cuenta", {"identificador": "+12815550177"})

    assert resultado["encontrado"] is True
    assert resultado["nombre"] == "Carlos Núñez"


def test_consultar_estado_cuenta_socio_inexistente_nunca_inventa_datos(db):
    negocio = crear_negocio_gym(db)
    crear_socio(db, negocio, "María Torres", telefono="+17135550142")

    resultado = ejecutar_tool(db, negocio, "consultar_estado_cuenta", {"identificador": "Alguien Que No Existe"})

    assert resultado["encontrado"] is False
    assert "meses_adeudados" not in resultado
    assert "mensaje" in resultado


def test_consultar_estado_cuenta_identificador_vacio_no_rompe(db):
    negocio = crear_negocio_gym(db)

    resultado = ejecutar_tool(db, negocio, "consultar_estado_cuenta", {"identificador": ""})

    assert resultado["encontrado"] is False


def test_consultar_horario_clases_devuelve_horario_estatico(db):
    negocio = crear_negocio_gym(db)

    resultado = ejecutar_tool(db, negocio, "consultar_horario_clases", {})

    assert resultado["clases"] == gym.CLASES_DEMO
    assert len(resultado["clases"]) > 0


def test_registrar_checkin_socio_activo_se_confirma(db):
    negocio = crear_negocio_gym(db)
    crear_socio(db, negocio, "Diana Reyes", telefono="+17135550120", estado_membresia="activo")

    resultado = ejecutar_tool(db, negocio, "registrar_checkin", {"identificador": "Diana Reyes"})

    assert resultado["encontrado"] is True
    assert resultado["checkin_confirmado"] is True


def test_registrar_checkin_socio_pausado_se_rechaza(db):
    negocio = crear_negocio_gym(db)
    crear_socio(db, negocio, "Luis Fernández", telefono="+17135550188", estado_membresia="pausado")

    resultado = ejecutar_tool(db, negocio, "registrar_checkin", {"identificador": "Luis Fernández"})

    assert resultado["encontrado"] is True
    assert resultado["checkin_confirmado"] is False
    assert "motivo" in resultado


def test_registrar_checkin_socio_inexistente(db):
    negocio = crear_negocio_gym(db)

    resultado = ejecutar_tool(db, negocio, "registrar_checkin", {"identificador": "Nadie"})

    assert resultado["encontrado"] is False


def test_herramienta_desconocida_devuelve_error_explicito(db):
    negocio = crear_negocio_gym(db)

    resultado = ejecutar_tool(db, negocio, "herramienta_que_no_existe", {})

    assert "error" in resultado


def test_registrar_checkin_deja_un_registro_persistente(db):
    """Antes registrar_checkin solo confirmaba en la conversación y no
    guardaba nada — esto prueba que ahora sí queda un Checkin en la base."""
    negocio = crear_negocio_gym(db)
    crear_socio(db, negocio, "Diana Reyes", telefono="+17135550120", estado_membresia="activo")

    ejecutar_tool(db, negocio, "registrar_checkin", {"identificador": "Diana Reyes"})

    from models import Checkin, ClienteNegocio
    cliente = db.query(ClienteNegocio).filter(ClienteNegocio.nombre == "Diana Reyes").first()
    checkins = db.query(Checkin).filter(Checkin.cliente_negocio_id == cliente.id).all()
    assert len(checkins) == 1


def test_registrar_checkin_socio_pausado_no_persiste_nada(db):
    negocio = crear_negocio_gym(db)
    socio = crear_socio(db, negocio, "Luis Fernández", telefono="+17135550188", estado_membresia="pausado")

    ejecutar_tool(db, negocio, "registrar_checkin", {"identificador": "Luis Fernández"})

    from models import Checkin
    checkins = db.query(Checkin).filter(Checkin.cliente_negocio_id == socio.id).all()
    assert len(checkins) == 0


def test_consultar_progreso_devuelve_perfil_real(db):
    negocio = crear_negocio_gym(db)
    socio = crear_socio(db, negocio, "María Torres", telefono="+17135550142", altura_cm=162)
    crear_medicion(db, socio, peso_kg=78.5, hace_dias=30)
    crear_medicion(db, socio, peso_kg=74.0, hace_dias=0)

    resultado = ejecutar_tool(db, negocio, "consultar_progreso", {"identificador": "María Torres"})

    assert resultado["encontrado"] is True
    assert resultado["ultima_medicion"]["peso_kg"] == 74.0
    assert resultado["progreso"]["delta_peso_kg"] == -4.5


def test_consultar_progreso_socio_inexistente_nunca_inventa_datos(db):
    negocio = crear_negocio_gym(db)

    resultado = ejecutar_tool(db, negocio, "consultar_progreso", {"identificador": "Nadie"})

    assert resultado["encontrado"] is False
    assert "ultima_medicion" not in resultado
