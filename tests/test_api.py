"""
Tests a nivel HTTP usando el TestClient de FastAPI (no un servidor real) —
evita a propósito el patrón de "levantar el server con nohup y pegarle con
curl en otro Bash", que resultó poco confiable en este entorno durante la
construcción de Fase 2. TestClient corre la app in-process, sin red real.
"""

import pytest
from fastapi.testclient import TestClient

from main import app

ADMIN_AUTH = ("admin_test", "admin_test_pass")
OPERADOR_AUTH = ("operador_test", "operador_test_pass")


@pytest.fixture
def client():
    return TestClient(app)


def _crear_negocio(client, slug="fuerza-total", nombre="Fuerza Total", vertical="gym"):
    return client.post(
        "/api/admin/negocios",
        json={"slug": slug, "nombre": nombre, "vertical": vertical},
        auth=OPERADOR_AUTH,
    )


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_negocio_inexistente_404(client):
    r = client.post("/api/no-existe/iniciar", json={})
    assert r.status_code == 404


def test_estadisticas_sin_auth_401(client):
    _crear_negocio(client)
    r = client.get("/api/fuerza-total/estadisticas")
    assert r.status_code == 401


def test_estadisticas_con_auth_200(client):
    _crear_negocio(client)
    r = client.get("/api/fuerza-total/estadisticas", auth=ADMIN_AUTH)
    assert r.status_code == 200
    assert "total_llamadas" in r.json()


def test_estadisticas_globales_requiere_auth_de_operador(client):
    _crear_negocio(client)
    sin_auth = client.get("/api/admin/estadisticas-globales")
    assert sin_auth.status_code == 401

    # La auth del negocio (admin) NO sirve para el panel de operador.
    con_auth_equivocada = client.get("/api/admin/estadisticas-globales", auth=ADMIN_AUTH)
    assert con_auth_equivocada.status_code == 401

    con_auth_correcta = client.get("/api/admin/estadisticas-globales", auth=OPERADOR_AUTH)
    assert con_auth_correcta.status_code == 200
    assert "negocios_totales" in con_auth_correcta.json()


def test_crear_negocio_slug_duplicado_409(client):
    r1 = _crear_negocio(client, slug="dup")
    assert r1.status_code == 200
    r2 = _crear_negocio(client, slug="dup")
    assert r2.status_code == 409


def test_crear_negocio_vertical_desconocido_400(client):
    r = _crear_negocio(client, slug="algo", vertical="clinica-que-no-existe")
    assert r.status_code == 400


def test_crear_negocio_requiere_auth_operador(client):
    r = client.post("/api/admin/negocios", json={"slug": "sin-auth", "nombre": "X"})
    assert r.status_code == 401


def test_flujo_completo_alta_de_socio_y_cobro(client):
    _crear_negocio(client, slug="fuerza-total")

    r_crear_cliente = client.post(
        "/api/fuerza-total/clientes",
        json={"nombre": "María Torres", "telefono": "+17135550142", "meses_adeudados": 3},
        auth=ADMIN_AUTH,
    )
    assert r_crear_cliente.status_code == 200
    cliente_id = r_crear_cliente.json()["cliente_id"]

    r_listar = client.get("/api/fuerza-total/clientes", auth=ADMIN_AUTH)
    assert r_listar.status_code == 200
    nombres = [c["nombre"] for c in r_listar.json()]
    assert "María Torres" in nombres

    # Registrar que pagó: baja meses_adeudados a 0.
    r_actualizar = client.patch(
        f"/api/fuerza-total/clientes/{cliente_id}",
        json={"meses_adeudados": 0, "proximo_vencimiento": "2026-11-01"},
        auth=ADMIN_AUTH,
    )
    assert r_actualizar.status_code == 200

    r_listar_2 = client.get("/api/fuerza-total/clientes", auth=ADMIN_AUTH)
    cliente_actualizado = next(c for c in r_listar_2.json() if c["id"] == cliente_id)
    assert cliente_actualizado["meses_adeudados"] == 0


def test_actualizar_cliente_inexistente_404(client):
    _crear_negocio(client, slug="fuerza-total")
    r = client.patch(
        "/api/fuerza-total/clientes/no-existe",
        json={"meses_adeudados": 0},
        auth=ADMIN_AUTH,
    )
    assert r.status_code == 404


def test_flujo_completo_llamada_iniciar_mensaje_finalizar(client):
    _crear_negocio(client, slug="fuerza-total")

    r_iniciar = client.post("/api/fuerza-total/iniciar", json={"numero_cliente": "+17135550142"})
    assert r_iniciar.status_code == 200
    llamada_id = r_iniciar.json()["llamada_id"]

    # Sin ANTHROPIC_API_KEY en el entorno de test, el motor cae al fallback
    # pre-grabado — comportamiento determinista, perfecto para probar que el
    # endpoint funciona de punta a punta sin depender de la red ni gastar
    # cuota real de la API.
    r_mensaje = client.post(
        "/api/fuerza-total/mensaje",
        json={"llamada_id": llamada_id, "mensaje": "hola, ¿cuánto debo?"},
    )
    assert r_mensaje.status_code == 200
    assert r_mensaje.json()["fallback"] is True

    r_finalizar = client.post(
        "/api/fuerza-total/finalizar",
        json={"llamada_id": llamada_id, "resultado": "completada"},
    )
    assert r_finalizar.status_code == 200
    assert r_finalizar.json()["resultado"] == "completada"

    r_llamadas = client.get("/api/fuerza-total/llamadas-recientes", auth=ADMIN_AUTH)
    assert r_llamadas.status_code == 200
    assert len(r_llamadas.json()) == 1


def test_mensaje_llamada_inexistente(client):
    _crear_negocio(client, slug="fuerza-total")
    r = client.post(
        "/api/fuerza-total/mensaje",
        json={"llamada_id": "no-existe", "mensaje": "hola"},
    )
    assert r.status_code == 400


def test_negocio_suspendido_rechaza_llamadas(client):
    _crear_negocio(client, slug="fuerza-total")
    from models import SessionLocal, Negocio
    dbs = SessionLocal()
    try:
        negocio = dbs.query(Negocio).filter(Negocio.slug == "fuerza-total").first()
        negocio.activo = False
        dbs.commit()
    finally:
        dbs.close()

    r = client.post("/api/fuerza-total/iniciar", json={})
    assert r.status_code == 403


def test_listar_negocios_operador(client):
    _crear_negocio(client, slug="fuerza-total", nombre="Fuerza Total")
    _crear_negocio(client, slug="gym-dos", nombre="Gym Dos")

    r = client.get("/api/admin/negocios", auth=OPERADOR_AUTH)
    assert r.status_code == 200
    slugs = [n["slug"] for n in r.json()]
    assert "fuerza-total" in slugs
    assert "gym-dos" in slugs


def test_paginas_html_cargan(client):
    _crear_negocio(client, slug="fuerza-total")

    assert client.get("/admin").status_code == 200
    assert client.get("/fuerza-total/admin").status_code == 200
    assert client.get("/fuerza-total").status_code == 200
    assert client.get("/no-existe/admin").status_code == 404


def _crear_cliente(client, slug="fuerza-total", nombre="María Torres", altura_cm=162):
    return client.post(
        f"/api/{slug}/clientes",
        json={"nombre": nombre, "telefono": "+17135550142", "altura_cm": altura_cm},
        auth=ADMIN_AUTH,
    )


def test_registrar_y_listar_mediciones(client):
    _crear_negocio(client, slug="fuerza-total")
    cliente_id = _crear_cliente(client).json()["cliente_id"]

    r1 = client.post(
        f"/api/fuerza-total/clientes/{cliente_id}/mediciones",
        json={"peso_kg": 78.5},
        auth=ADMIN_AUTH,
    )
    assert r1.status_code == 200
    assert r1.json()["bmi"] is not None

    r2 = client.post(
        f"/api/fuerza-total/clientes/{cliente_id}/mediciones",
        json={"peso_kg": 76.0, "notas": "buena semana"},
        auth=ADMIN_AUTH,
    )
    assert r2.status_code == 200

    r_listar = client.get(f"/api/fuerza-total/clientes/{cliente_id}/mediciones", auth=ADMIN_AUTH)
    assert r_listar.status_code == 200
    pesos = [m["peso_kg"] for m in r_listar.json()]
    assert pesos == [78.5, 76.0]


def test_mediciones_requiere_auth_admin(client):
    _crear_negocio(client, slug="fuerza-total")
    cliente_id = _crear_cliente(client).json()["cliente_id"]

    r = client.post(f"/api/fuerza-total/clientes/{cliente_id}/mediciones", json={"peso_kg": 70})
    assert r.status_code == 401


def test_mediciones_cliente_de_otro_negocio_404(client):
    _crear_negocio(client, slug="fuerza-total")
    _crear_negocio(client, slug="gym-dos")
    cliente_id = _crear_cliente(client, slug="fuerza-total").json()["cliente_id"]

    # El cliente es de fuerza-total, no de gym-dos.
    r = client.get(f"/api/gym-dos/clientes/{cliente_id}/mediciones", auth=ADMIN_AUTH)
    assert r.status_code == 404


def test_perfil_cliente_completo(client):
    _crear_negocio(client, slug="fuerza-total")
    cliente_id = _crear_cliente(client).json()["cliente_id"]
    client.post(f"/api/fuerza-total/clientes/{cliente_id}/mediciones", json={"peso_kg": 78.5}, auth=ADMIN_AUTH)

    r = client.get(f"/api/fuerza-total/clientes/{cliente_id}/perfil", auth=ADMIN_AUTH)
    assert r.status_code == 200
    perfil = r.json()
    assert perfil["nombre"] == "María Torres"
    assert perfil["ultima_medicion"]["peso_kg"] == 78.5
    assert "asistencia" in perfil
    assert "meses_como_socio" in perfil


def test_perfil_cliente_inexistente_404(client):
    _crear_negocio(client, slug="fuerza-total")
    r = client.get("/api/fuerza-total/clientes/no-existe/perfil", auth=ADMIN_AUTH)
    assert r.status_code == 404


def test_panel_cliente_html_carga(client):
    _crear_negocio(client, slug="fuerza-total")
    cliente_id = _crear_cliente(client).json()["cliente_id"]

    r = client.get(f"/fuerza-total/admin/clientes/{cliente_id}")
    assert r.status_code == 200

    r_404 = client.get("/fuerza-total/admin/clientes/no-existe")
    assert r_404.status_code == 404


def test_alertas_separa_en_riesgo_y_reactivacion(client):
    _crear_negocio(client, slug="fuerza-total")

    r_critico = client.post(
        "/api/fuerza-total/clientes",
        json={"nombre": "María Torres", "meses_adeudados": 3},
        auth=ADMIN_AUTH,
    )
    cliente_critico = r_critico.json()["cliente_id"]

    client.post(
        "/api/fuerza-total/clientes",
        json={"nombre": "Luis Fernández", "estado_membresia": "pausado"},
        auth=ADMIN_AUTH,
    )

    r = client.get("/api/fuerza-total/alertas", auth=ADMIN_AUTH)
    assert r.status_code == 200
    data = r.json()
    assert data["total_criticos"] == 1
    assert data["en_riesgo"][0]["cliente_id"] == cliente_critico
    assert data["en_riesgo"][0]["nivel"] == "critico"
    assert data["total_reactivacion"] == 1
    assert data["reactivacion"][0]["nombre"] == "Luis Fernández"


def test_alertas_requiere_auth_admin(client):
    _crear_negocio(client, slug="fuerza-total")
    r = client.get("/api/fuerza-total/alertas")
    assert r.status_code == 401


def test_perfil_incluye_riesgo(client):
    _crear_negocio(client, slug="fuerza-total")
    r_cliente = client.post(
        "/api/fuerza-total/clientes",
        json={"nombre": "María Torres", "meses_adeudados": 2},
        auth=ADMIN_AUTH,
    )
    cliente_id = r_cliente.json()["cliente_id"]

    r = client.get(f"/api/fuerza-total/clientes/{cliente_id}/perfil", auth=ADMIN_AUTH)
    perfil = r.json()
    assert perfil["riesgo"]["nivel"] == "critico"
    assert "Debe 2 meses" in perfil["riesgo"]["motivos"]


def test_checkin_por_voz_se_refleja_en_el_perfil(client):
    """Flujo de punta a punta: registrar_checkin (tool de voz) -> el perfil
    del cliente (dashboard) refleja la visita."""
    _crear_negocio(client, slug="fuerza-total")
    cliente_id = _crear_cliente(client).json()["cliente_id"]

    r_iniciar = client.post("/api/fuerza-total/iniciar", json={"numero_cliente": "+17135550142"})
    llamada_id = r_iniciar.json()["llamada_id"]

    from verticals.gym import ejecutar_tool
    from models import SessionLocal, Negocio
    dbs = SessionLocal()
    try:
        negocio = dbs.query(Negocio).filter(Negocio.slug == "fuerza-total").first()
        resultado_tool = ejecutar_tool(dbs, negocio, "registrar_checkin", {"identificador": "María Torres"})
    finally:
        dbs.close()
    assert resultado_tool["checkin_confirmado"] is True

    r_perfil = client.get(f"/api/fuerza-total/clientes/{cliente_id}/perfil", auth=ADMIN_AUTH)
    assert r_perfil.json()["asistencia"]["total_checkins_historico"] == 1



def test_llamada_no_puede_cruzar_de_negocio(client, db):
    """Un ID de llamada de un tenant no sirve bajo la URL de otro."""
    from conftest import crear_negocio_gym

    crear_negocio_gym(db, slug="gym-a", nombre="Gym A")
    crear_negocio_gym(db, slug="gym-b", nombre="Gym B")

    inicio = client.post("/api/gym-a/iniciar", json={})
    assert inicio.status_code == 200
    llamada_id = inicio.json()["llamada_id"]
    assert len(llamada_id) == 36

    mensaje_cruzado = client.post(
        "/api/gym-b/mensaje",
        json={"llamada_id": llamada_id, "mensaje": "hola"},
    )
    assert mensaje_cruzado.status_code == 400

    finalizar_cruzado = client.post(
        "/api/gym-b/finalizar",
        json={"llamada_id": llamada_id, "resultado": "completada"},
    )
    assert finalizar_cruzado.status_code == 400


def test_webhook_stripe_rechaza_evento_sin_secreto(client):
    """Nunca se aceptan eventos de pago sin verificar la firma de Stripe."""
    r = client.post(
        "/webhooks/stripe",
        content=b'{"type":"invoice.payment_succeeded","data":{"object":{"subscription":"sub_fake"}}}',
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 400
    assert r.json()["detail"] == "Webhook inválido"
