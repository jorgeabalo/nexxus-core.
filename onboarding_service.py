"""Altas compartidas por la API y el adaptador AITA; sin llamadas a un LLM."""

import uuid
from datetime import datetime, timedelta
from typing import Optional, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.exc import IntegrityError

from models import SessionLocal, Negocio, ClienteNegocio, OnboardingAction
from verticals import VERTICALES
from aita_orchestrator import orchestrator
from business_profile import profile_from_negocio


class OnboardingError(ValueError):
    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.status_code = status_code


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class BusinessInput(InputModel):
    slug: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    nombre: str = Field(min_length=1, max_length=200)
    vertical: str = "gym"
    idioma_principal: str = Field(default="es", min_length=2, max_length=12)
    idioma_secundario: str = Field(default="en", min_length=2, max_length=12)
    nombre_asistente: str = Field(default="María", min_length=1, max_length=100)
    plan: Literal["starter", "professional", "enterprise"] = "starter"


class CustomerInput(InputModel):
    nombre: str = Field(min_length=1, max_length=200)
    telefono: Optional[str] = Field(default=None, max_length=50)
    email: Optional[str] = Field(default=None, max_length=254)
    estado_membresia: Literal["activo", "pausado", "cancelado"] = "activo"
    meses_adeudados: int = Field(default=0, ge=0)
    proximo_vencimiento: Optional[str] = None
    plan_membresia: Optional[str] = Field(default=None, max_length=100)
    altura_cm: Optional[float] = Field(default=None, gt=0, le=300, allow_inf_nan=False)
    fecha_ingreso: Optional[str] = None

    @field_validator("proximo_vencimiento", "fecha_ingreso")
    @classmethod
    def valid_date(cls, value):
        if value:
            datetime.fromisoformat(value)
        return value


class ConfigurationInput(InputModel):
    slug: str = Field(min_length=1, max_length=80)
    nombre_asistente: Optional[str] = Field(default=None, min_length=1, max_length=100)
    idioma_principal: Optional[str] = Field(default=None, min_length=2, max_length=12)
    idioma_secundario: Optional[str] = Field(default=None, min_length=2, max_length=12)
    activo: Optional[bool] = None


class CustomerToolInput(CustomerInput):
    negocio_slug: str = Field(min_length=1, max_length=80)


class CustomerBatchInput(InputModel):
    negocio_slug: str = Field(min_length=1, max_length=80)
    socios: list[CustomerInput] = Field(min_length=1, max_length=100)


WRITE_TOOLS = {
    "crear_negocio": ("create_business", BusinessInput),
    "cargar_socio": ("import_customers", CustomerToolInput),
    "cargar_socios_multiples": ("import_customers", CustomerBatchInput),
    "actualizar_configuracion_negocio": ("change_configuration", ConfigurationInput),
}


def create_business(db, data: BusinessInput, negocio_id=None):
    if data.vertical not in VERTICALES:
        raise OnboardingError("Vertical desconocido")
    if data.slug in {"admin", "api", "health", "webhooks"}:
        raise OnboardingError("Slug reservado")
    if db.query(Negocio).filter_by(slug=data.slug).first():
        raise OnboardingError("Ya existe un negocio con ese slug", 409)
    negocio = Negocio(id=negocio_id or str(uuid.uuid4()), activo=True, **data.model_dump())
    db.add(negocio)
    db.flush()
    return {"negocio_id": negocio.id, "slug": negocio.slug, "suscripcion": None}


def active_business(db, slug):
    negocio = db.query(Negocio).filter_by(slug=slug).first()
    if not negocio:
        raise OnboardingError("Negocio no encontrado", 404)
    return negocio


def create_customer(db, negocio, data: CustomerInput):
    if not negocio.activo:
        raise OnboardingError("El negocio no tiene el servicio activo", 403)
    values = data.model_dump()
    for key in ("proximo_vencimiento", "fecha_ingreso"):
        values[key] = datetime.fromisoformat(values[key]) if values[key] else None
    values["fecha_ingreso"] = values["fecha_ingreso"] or datetime.utcnow()
    cliente = ClienteNegocio(id=str(uuid.uuid4()), negocio_id=negocio.id, **values)
    db.add(cliente)
    db.flush()
    return {"cliente_id": cliente.id, "nombre": cliente.nombre}


def read_business(slug):
    with SessionLocal() as db:
        negocio = active_business(db, slug)
        orchestrator.decide("onboarding", "business_profile", negocio.id)
        return {
            "encontrado": True,
            "slug": negocio.slug,
            "perfil": profile_from_negocio(negocio).to_agent_context(),
            "nombre_asistente": negocio.nombre_asistente,
            "activo": negocio.activo,
            "plan": negocio.plan,
            "cantidad_socios": db.query(ClienteNegocio).filter_by(negocio_id=negocio.id).count(),
        }


def list_businesses():
    # Sólo disponible detrás de la autenticación del operador de plataforma.
    orchestrator.decide("onboarding", "business_directory", "aita-operator")
    with SessionLocal() as db:
        return {"negocios": [
            {"slug": n.slug, "nombre": n.nombre, "vertical": n.vertical, "activo": n.activo}
            for n in db.query(Negocio).order_by(Negocio.slug).all()
        ]}


def propose_action(tool, payload, operador):
    if not operador or tool not in WRITE_TOOLS:
        raise OnboardingError("Acción no permitida", 403)
    capability, schema = WRITE_TOOLS[tool]
    data = schema.model_validate(payload)
    clean = data.model_dump(mode="json", exclude_none=True)
    with SessionLocal() as db:
        if tool == "crear_negocio":
            if data.vertical not in VERTICALES:
                raise OnboardingError("Vertical desconocido")
            if db.query(Negocio).filter_by(slug=data.slug).first():
                raise OnboardingError("Ya existe un negocio con ese slug", 409)
            negocio_id = str(uuid.uuid4())
        else:
            negocio = active_business(db, clean.get("negocio_slug") or clean["slug"])
            negocio_id = negocio.id
        decision = orchestrator.decide("onboarding", capability, negocio_id)
        if not decision.requires_human_approval:
            raise OnboardingError("La acción de escritura debe exigir aprobación", 403)
        # Reintentar la misma propuesta no crea una segunda operación.
        for previous in db.query(OnboardingAction).filter_by(operador=operador, tool=tool, estado="pendiente").all():
            if previous.payload == clean and previous.fecha_creacion >= datetime.utcnow() - timedelta(minutes=30):
                return proposal_view(previous)
        action = OnboardingAction(id=str(uuid.uuid4()), operador=operador, negocio_id=negocio_id,
                                  tool=tool, payload=clean, estado="pendiente")
        db.add(action)
        db.commit()
        return proposal_view(action)


def proposal_view(action):
    return {"id": action.id, "tool": action.tool, "datos": action.payload,
            "negocio_id": action.negocio_id, "requiere_confirmacion": True}


def confirm_action(action_id, operador):
    """Revalida la propuesta guardada, escribe y audita en una sola transacción.

    El LLM nunca llama a este método. Sólo el endpoint de confirmación humana.
    Un reintento devuelve el resultado guardado y no repite las altas.
    """
    with SessionLocal() as db:
        try:
            # UPDATE condicional obtiene el bloqueo antes de leer o escribir.
            claimed = db.query(OnboardingAction).filter_by(
                id=action_id, operador=operador, estado="pendiente"
            ).update({"estado": "ejecutando"}, synchronize_session=False)
            action = db.query(OnboardingAction).filter_by(id=action_id, operador=operador).first()
            if not action:
                raise OnboardingError("Propuesta no encontrada", 404)
            if not claimed:
                if action.estado == "confirmada":
                    return action.resultado
                raise OnboardingError("Propuesta no disponible", 409)
            if action.fecha_creacion < datetime.utcnow() - timedelta(minutes=30):
                raise OnboardingError("Propuesta caducada; solicita una nueva", 409)
            capability, schema = WRITE_TOOLS[action.tool]
            data = schema.model_validate(action.payload)
            decision = orchestrator.decide("onboarding", capability, action.negocio_id)
            if not decision.requires_human_approval:
                raise OnboardingError("Política de aprobación inválida", 403)
            if action.tool == "crear_negocio":
                result = create_business(db, data, negocio_id=action.negocio_id)
            else:
                negocio = active_business(db, action.payload.get("negocio_slug") or action.payload["slug"])
                if negocio.id != action.negocio_id:
                    raise OnboardingError("El negocio de la propuesta cambió", 409)
                if action.tool == "actualizar_configuracion_negocio":
                    for key, value in data.model_dump(exclude_none=True, exclude={"slug"}).items():
                        setattr(negocio, key, value)
                    result = {"actualizado": True, "slug": negocio.slug}
                else:
                    customers = data.socios if action.tool == "cargar_socios_multiples" else [
                        CustomerInput.model_validate(data.model_dump(exclude={"negocio_slug"}))
                    ]
                    results = [create_customer(db, negocio, customer) for customer in customers]
                    result = {"cargados": len(results), "socios_cargados": results}
            action.estado = "confirmada"
            action.resultado = result
            action.fecha_confirmacion = datetime.utcnow()
            db.commit()
            return result
        except IntegrityError:
            db.rollback()
            raise OnboardingError("Conflicto al guardar; vuelve a consultar el negocio", 409) from None
