"""
Modelos de datos de NEXXUS AI Support — versión multi-tenant (Fase 2).

Diferencia clave con el MVP anterior (recepcionista-ia-proyecto): ahí solo
existían Llamada/Conversacion para UN negocio. Acá se agrega:

- Negocio: cada cliente de Jorge (un gym, una clínica, un restaurante...) es
  una fila acá. Todo lo demás cuelga de negocio_id — así un mismo servidor
  atiende a todos los negocios sin mezclar datos entre ellos.
- ClienteNegocio: el cliente/socio/paciente DEL negocio (ej. un socio del
  gym). Acá vive la data real que la IA tiene que consultar (meses
  adeudados, próximo vencimiento) — la IA nunca inventa estos datos, los
  lee de acá.
- Suscripcion: la facturación de Jorge AL negocio (lo que le cobra NEXXUS
  al gym por usar la plataforma) — separado de los pagos que el gym le
  cobra a SUS socios.
- Checkin: registro histórico de cada visita de un ClienteNegocio (antes
  registrar_checkin solo confirmaba, no guardaba nada — esto es lo que
  permite calcular "qué tan habitual" viene un socio).
- MedicionCliente: historial de peso/medidas corporales de un ClienteNegocio
  (pensado para el vertical Gym, pero es un modelo genérico — no vive en
  verticals/gym.py — porque cualquier vertical basado en seguimiento físico,
  ej. una clínica de nutrición, lo puede reusar sin tocarlo).

Se mantiene SQLite para desarrollo/pruebas (igual que el MVP anterior). La
especificación original define PostgreSQL para producción multi-tenant real
— migrar el engine de acá es un cambio de una línea (create_engine) una vez
que haya un Postgres real disponible; el resto del código no depende del
motor de base de datos.
"""

from sqlalchemy import (
    create_engine, Column, String, DateTime, Float, Integer, Boolean,
    ForeignKey, Text, JSON
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, sessionmaker
from datetime import datetime

Base = declarative_base()


class Negocio(Base):
    """Un tenant: un cliente de NEXXUS (ej. 'Fuerza Total', un gym)."""
    __tablename__ = "negocios"

    id = Column(String, primary_key=True)
    slug = Column(String, unique=True, nullable=False)  # usado en la URL, ej. "fuerza-total"
    nombre = Column(String, nullable=False)
    vertical = Column(String, nullable=False, default="generico")  # "gym", "clinica", "restaurante", ...
    idioma_principal = Column(String, default="es")
    idioma_secundario = Column(String, default="en")
    nombre_asistente = Column(String, default="María")
    plan = Column(String, default="starter")  # starter / professional / enterprise
    activo = Column(Boolean, default=True)
    fecha_creacion = Column(DateTime, default=datetime.utcnow)

    clientes = relationship("ClienteNegocio", back_populates="negocio", cascade="all, delete-orphan")
    llamadas = relationship("Llamada", back_populates="negocio", cascade="all, delete-orphan")
    suscripcion = relationship("Suscripcion", back_populates="negocio", uselist=False, cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Negocio {self.slug} ({self.vertical})>"


class ClienteNegocio(Base):
    """El cliente/socio/paciente DEL negocio — no confundir con el Negocio
    mismo. Ej: un socio del gym. acá vive el dato real de facturación que
    la IA debe consultar (nunca inventar) al responder preguntas de pago."""
    __tablename__ = "clientes_negocio"

    id = Column(String, primary_key=True)
    negocio_id = Column(String, ForeignKey("negocios.id"), nullable=False)
    nombre = Column(String, nullable=False)
    telefono = Column(String, nullable=True)  # usado para identificar quién llama
    email = Column(String, nullable=True)

    # Campos de facturación — genéricos para cualquier vertical basado en
    # membresía/suscripción (gym, clínica con plan, etc.)
    estado_membresia = Column(String, default="activo")  # activo / pausado / cancelado
    meses_adeudados = Column(Integer, default=0)
    proximo_vencimiento = Column(DateTime, nullable=True)
    plan_membresia = Column(String, nullable=True)  # ej. "Mensual", "Anual"

    # Campo libre para datos específicos del vertical que no ameritan
    # columna propia (ej. horario de clase preferido, alergias, etc.)
    datos_extra = Column(JSON, nullable=True)

    # Antigüedad como socio ("tiempo en el gym") — se fija sola al crear el
    # cliente si no se especifica. Altura en cm: cambia poco, así que vive
    # acá (no en cada medición) — cada MedicionCliente puede pisarla si el
    # dato se corrige, pero el valor "actual" para calcular BMI es este.
    fecha_ingreso = Column(DateTime, default=datetime.utcnow)
    altura_cm = Column(Float, nullable=True)

    negocio = relationship("Negocio", back_populates="clientes")
    mediciones = relationship("MedicionCliente", back_populates="cliente", cascade="all, delete-orphan")
    checkins = relationship("Checkin", back_populates="cliente", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<ClienteNegocio {self.nombre} ({self.negocio_id})>"


class Llamada(Base):
    __tablename__ = "llamadas"

    id = Column(String, primary_key=True)
    negocio_id = Column(String, ForeignKey("negocios.id"), nullable=False)
    cliente_negocio_id = Column(String, ForeignKey("clientes_negocio.id"), nullable=True)

    numero_cliente = Column(String, nullable=True)
    idioma_detectado = Column(String, default="es")
    duracion_segundos = Column(Float, default=0)
    fecha_inicio = Column(DateTime, default=datetime.utcnow)
    fecha_fin = Column(DateTime, nullable=True)
    fallback_usado = Column(Boolean, default=False)
    resultado = Column(String, default="en_progreso")  # completada, transferida, fallback, requiere_supervisor
    notas = Column(Text, nullable=True)

    negocio = relationship("Negocio", back_populates="llamadas")
    conversaciones = relationship("Conversacion", back_populates="llamada", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Llamada {self.id} ({self.idioma_detectado}) - {self.duracion_segundos}s>"


class Conversacion(Base):
    __tablename__ = "conversaciones"

    id = Column(String, primary_key=True)
    llamada_id = Column(String, ForeignKey("llamadas.id"), nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow)
    rol = Column(String)  # "usuario", "recepcionista", "herramienta"
    mensaje = Column(Text)
    es_fallback = Column(Boolean, default=False)

    llamada = relationship("Llamada", back_populates="conversaciones")

    def __repr__(self):
        return f"<Conversacion {self.id} - {self.rol}: {self.mensaje[:50]}>"


class Suscripcion(Base):
    """Lo que Jorge (NEXXUS) le cobra AL negocio por usar la plataforma —
    separado de lo que el negocio le cobra a sus propios clientes."""
    __tablename__ = "suscripciones"

    id = Column(String, primary_key=True)
    negocio_id = Column(String, ForeignKey("negocios.id"), nullable=False)
    plan = Column(String, default="starter")  # starter=$99 / professional=$199 / enterprise=$499
    precio_mensual = Column(Float, default=99.0)
    estado = Column(String, default="activa")  # activa / periodo_gracia / suspendida / cancelada
    stripe_customer_id = Column(String, nullable=True)
    stripe_subscription_id = Column(String, nullable=True)
    fecha_proximo_cobro = Column(DateTime, nullable=True)
    # Se setea al primer pago fallido; desde acá se cuentan los 3 días de
    # gracia. Se limpia (None) apenas un pago vuelve a ser exitoso.
    fecha_pago_fallido = Column(DateTime, nullable=True)

    negocio = relationship("Negocio", back_populates="suscripcion")

    def __repr__(self):
        return f"<Suscripcion {self.negocio_id} ({self.plan}, {self.estado})>"


class Checkin(Base):
    """Registro histórico de UNA visita de un socio al negocio. Antes de
    esto, registrar_checkin (verticals/gym.py) solo confirmaba en la
    conversación pero no guardaba nada persistente — con esta tabla, cada
    check-in queda guardado y se puede calcular qué tan seguido viene un
    socio (frecuencia semanal, días desde la última visita, etc.)."""
    __tablename__ = "checkins"

    id = Column(String, primary_key=True)
    negocio_id = Column(String, ForeignKey("negocios.id"), nullable=False)
    cliente_negocio_id = Column(String, ForeignKey("clientes_negocio.id"), nullable=False)
    fecha = Column(DateTime, default=datetime.utcnow)

    cliente = relationship("ClienteNegocio", back_populates="checkins")

    def __repr__(self):
        return f"<Checkin {self.cliente_negocio_id} @ {self.fecha}>"


class MedicionCliente(Base):
    """Una medición puntual de peso/medidas corporales de un socio — el
    historial de estas filas es lo que permite mostrar 'cambios' (progreso)
    en el dashboard del cliente, no solo el valor actual. El BMI no se
    guarda como columna: se calcula siempre a partir de peso_kg + altura_cm
    (ver progreso_service.calcular_bmi) para que nunca quede desactualizado
    si se corrige un dato."""
    __tablename__ = "mediciones_cliente"

    id = Column(String, primary_key=True)
    cliente_negocio_id = Column(String, ForeignKey("clientes_negocio.id"), nullable=False)
    fecha = Column(DateTime, default=datetime.utcnow)

    peso_kg = Column(Float, nullable=True)
    # Se guarda también acá (además de en ClienteNegocio.altura_cm) para que
    # el historial quede fiel al momento de cada medición, aunque después se
    # corrija la altura "actual" del socio.
    altura_cm = Column(Float, nullable=True)
    cintura_cm = Column(Float, nullable=True)
    cadera_cm = Column(Float, nullable=True)
    pecho_cm = Column(Float, nullable=True)
    brazo_cm = Column(Float, nullable=True)
    notas = Column(Text, nullable=True)

    cliente = relationship("ClienteNegocio", back_populates="mediciones")

    def __repr__(self):
        return f"<MedicionCliente {self.cliente_negocio_id} @ {self.fecha} ({self.peso_kg}kg)>"


# Motor de BD (SQLite para desarrollo — ver nota arriba sobre PostgreSQL en producción).
# El nombre del archivo es configurable por variable de entorno para que los
# tests automatizados (ver tests/) puedan usar un archivo aparte del de
# desarrollo/demo, sin tocarlo.
import os as _os
_DB_PATH = _os.getenv("NEXXUS_DB_PATH", "nexxus_core.sqlite")
engine = create_engine(f"sqlite:///{_DB_PATH}", connect_args={"check_same_thread": False})
Base.metadata.create_all(bind=engine)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
