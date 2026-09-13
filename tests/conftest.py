"""
Configuración compartida de los tests automatizados.

Lo más importante de este archivo: setea NEXXUS_DB_PATH ANTES de importar
cualquier módulo de la app (models, main, generic_service, billing_service,
verticals) — así los tests corren contra su propio archivo SQLite, separado
por completo de nexxus_core.sqlite (la base de desarrollo/demo). Nunca se
toca esa base al correr la suite.

También se limpian/fijan las variables de entorno sensibles (API key de
Anthropic, claves de Stripe) para que los tests sean deterministas: sin key
de Anthropic el motor cae a su fallback pre-grabado (no llama a la red, no
gasta cuota real), y sin claves de Stripe la facturación corre en modo
"solo local" — exactamente el mismo comportamiento que ya se probó a mano
durante la construcción de Fase 2, ahora fijado en una suite repetible.
"""

import os
import sys
from pathlib import Path

TEST_DB_PATH = "test_nexxus.sqlite"
os.environ["NEXXUS_DB_PATH"] = TEST_DB_PATH

os.environ.setdefault("ADMIN_USER", "admin_test")
os.environ.setdefault("ADMIN_PASSWORD", "admin_test_pass")
os.environ.setdefault("OPERADOR_USER", "operador_test")
os.environ.setdefault("OPERADOR_PASSWORD", "operador_test_pass")

# Vacías a propósito (nunca None) para que load_dotenv() de cada módulo no
# las pise con valores reales si algún día hay un .env real en este entorno.
os.environ["ANTHROPIC_API_KEY"] = ""
os.environ["STRIPE_SECRET_KEY"] = ""
os.environ["STRIPE_WEBHOOK_SECRET"] = ""
os.environ["STRIPE_PRICE_STARTER"] = ""
os.environ["STRIPE_PRICE_PROFESSIONAL"] = ""
os.environ["STRIPE_PRICE_ENTERPRISE"] = ""

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
import uuid
from datetime import datetime, timedelta

from models import Base, engine, SessionLocal  # noqa: E402


@pytest.fixture(autouse=True)
def _base_de_datos_limpia():
    """Cada test arranca con las tablas vacías — evita que un test dependa
    (a propósito o por accidente) de datos dejados por otro."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture
def db():
    sesion = SessionLocal()
    try:
        yield sesion
    finally:
        sesion.close()


def crear_negocio_gym(db, slug="fuerza-total", nombre="Fuerza Total", activo=True, **kwargs):
    """Helper reusado por varios archivos de test: crea un negocio del
    vertical gym con valores razonables por default (mismo patrón que
    seed_demo.py, pero pensado para aislarse por test)."""
    from models import Negocio

    negocio = Negocio(
        id=str(uuid.uuid4())[:8],
        slug=slug,
        nombre=nombre,
        vertical="gym",
        idioma_principal="es",
        idioma_secundario="en",
        nombre_asistente="María",
        plan=kwargs.pop("plan", "starter"),
        activo=activo,
    )
    db.add(negocio)
    db.commit()
    db.refresh(negocio)
    return negocio


def crear_socio(db, negocio, nombre, telefono=None, meses_adeudados=0,
                 proximo_vencimiento_en_dias=None, estado_membresia="activo", plan_membresia="Mensual",
                 altura_cm=None, fecha_ingreso_hace_dias=None):
    from models import ClienteNegocio

    vencimiento = None
    if proximo_vencimiento_en_dias is not None:
        vencimiento = datetime.utcnow() + timedelta(days=proximo_vencimiento_en_dias)

    fecha_ingreso = datetime.utcnow()
    if fecha_ingreso_hace_dias is not None:
        fecha_ingreso = datetime.utcnow() - timedelta(days=fecha_ingreso_hace_dias)

    socio = ClienteNegocio(
        id=str(uuid.uuid4())[:8],
        negocio_id=negocio.id,
        nombre=nombre,
        telefono=telefono,
        estado_membresia=estado_membresia,
        meses_adeudados=meses_adeudados,
        proximo_vencimiento=vencimiento,
        plan_membresia=plan_membresia,
        altura_cm=altura_cm,
        fecha_ingreso=fecha_ingreso,
    )
    db.add(socio)
    db.commit()
    db.refresh(socio)
    return socio


def crear_medicion(db, cliente, peso_kg=None, altura_cm=None, hace_dias=0, **kwargs):
    from models import MedicionCliente

    medicion = MedicionCliente(
        id=str(uuid.uuid4())[:8],
        cliente_negocio_id=cliente.id,
        fecha=datetime.utcnow() - timedelta(days=hace_dias),
        peso_kg=peso_kg,
        altura_cm=altura_cm if altura_cm is not None else cliente.altura_cm,
        **kwargs,
    )
    db.add(medicion)
    db.commit()
    db.refresh(medicion)
    return medicion


def crear_checkin(db, negocio, cliente, hace_dias=0):
    from models import Checkin

    checkin = Checkin(
        id=str(uuid.uuid4())[:8],
        negocio_id=negocio.id,
        cliente_negocio_id=cliente.id,
        fecha=datetime.utcnow() - timedelta(days=hace_dias),
    )
    db.add(checkin)
    db.commit()
    db.refresh(checkin)
    return checkin
