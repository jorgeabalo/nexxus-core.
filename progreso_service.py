"""
Seguimiento de progreso de un socio del negocio: peso/medidas históricas,
BMI, tiempo como socio ("tiempo en el gym"), cambios a lo largo del tiempo,
y qué tan habitual viene (frecuencia de check-ins).

Separado del motor de IA (generic_service.py) y del vertical Gym
(verticals/gym.py) por la misma razón que billing_service.py está
separado: es lógica de negocio sobre datos reales, no algo específico de
cómo Claude conversa. El vertical Gym solo LLAMA a estas funciones (desde
ejecutar_tool, para el check-in por voz, y desde el tool de progreso) — el
cálculo en sí vive acá para poder reusarse también desde la API HTTP
(dashboard del cliente) sin duplicar nada.

Estos modelos (Checkin, MedicionCliente) son genéricos, no del vertical Gym
— igual que ClienteNegocio, viven en models.py porque cualquier vertical
basado en seguimiento físico (ej. una clínica de nutrición) los puede
reusar tal cual, sin tocar este archivo ni el motor genérico.
"""

import uuid
from datetime import datetime, timedelta

from models import SessionLocal, ClienteNegocio, MedicionCliente, Checkin

VENTANA_FRECUENCIA_DIAS = 30


def calcular_bmi(peso_kg, altura_cm):
    """BMI = peso(kg) / altura(m)^2. Nunca se guarda como columna — se
    calcula siempre a partir del peso y la altura de cada medición, para
    que nunca quede desactualizado si se corrige un dato."""
    if not peso_kg or not altura_cm:
        return None
    altura_m = altura_cm / 100
    if altura_m <= 0:
        return None
    return round(peso_kg / (altura_m ** 2), 1)


def clasificar_bmi(bmi):
    """Categorías estándar de la OMS — solo informativo para el panel, no
    es un diagnóstico médico ni se usa así en ningún lado del sistema."""
    if bmi is None:
        return None
    if bmi < 18.5:
        return "bajo_peso"
    if bmi < 25:
        return "normal"
    if bmi < 30:
        return "sobrepeso"
    return "obesidad"


def registrar_checkin(negocio_id: str, cliente_negocio_id: str) -> dict:
    """Guarda UNA visita. Antes de esto, registrar_checkin en
    verticals/gym.py solo confirmaba en la conversación pero no dejaba
    ningún registro persistente — sin esto era imposible calcular qué tan
    seguido viene un socio."""
    db = SessionLocal()
    try:
        checkin = Checkin(
            id=str(uuid.uuid4())[:8],
            negocio_id=negocio_id,
            cliente_negocio_id=cliente_negocio_id,
            fecha=datetime.utcnow(),
        )
        db.add(checkin)
        db.commit()
        return {"checkin_id": checkin.id, "fecha": checkin.fecha.isoformat()}
    finally:
        db.close()


def registrar_medicion(cliente_negocio_id: str, peso_kg: float = None, altura_cm: float = None,
                        cintura_cm: float = None, cadera_cm: float = None, pecho_cm: float = None,
                        brazo_cm: float = None, notas: str = None, fecha: datetime = None) -> dict:
    """Agrega una medición nueva al historial del socio. Pensado para
    cargarse desde el panel del negocio (no por voz — a diferencia del
    check-in, esto lo carga el staff con una cinta métrica/balanza en
    mano, no algo que el socio dicte por teléfono)."""
    db = SessionLocal()
    try:
        cliente = db.query(ClienteNegocio).filter(ClienteNegocio.id == cliente_negocio_id).first()
        if not cliente:
            return {"error": "Cliente no encontrado"}

        altura_usar = altura_cm if altura_cm is not None else cliente.altura_cm

        medicion = MedicionCliente(
            id=str(uuid.uuid4())[:8],
            cliente_negocio_id=cliente_negocio_id,
            fecha=fecha or datetime.utcnow(),
            peso_kg=peso_kg,
            altura_cm=altura_usar,
            cintura_cm=cintura_cm,
            cadera_cm=cadera_cm,
            pecho_cm=pecho_cm,
            brazo_cm=brazo_cm,
            notas=notas,
        )
        db.add(medicion)

        # La altura cambia poco pero puede corregirse — si viene una nueva,
        # actualizamos también el valor "actual" del socio.
        if altura_cm is not None:
            cliente.altura_cm = altura_cm

        db.commit()
        return {
            "medicion_id": medicion.id,
            "fecha": medicion.fecha.isoformat(),
            "peso_kg": medicion.peso_kg,
            "bmi": calcular_bmi(medicion.peso_kg, altura_usar),
        }
    finally:
        db.close()


def _serializar_medicion(m: MedicionCliente) -> dict:
    bmi = calcular_bmi(m.peso_kg, m.altura_cm)
    return {
        "medicion_id": m.id,
        "fecha": m.fecha.isoformat(),
        "peso_kg": m.peso_kg,
        "altura_cm": m.altura_cm,
        "bmi": bmi,
        "clasificacion_bmi": clasificar_bmi(bmi),
        "cintura_cm": m.cintura_cm,
        "cadera_cm": m.cadera_cm,
        "pecho_cm": m.pecho_cm,
        "brazo_cm": m.brazo_cm,
        "notas": m.notas,
    }


def obtener_mediciones(cliente_negocio_id: str) -> list:
    """Historial completo de mediciones de un socio, más viejo -> más
    nuevo (lo que alimenta el gráfico de evolución del dashboard)."""
    db = SessionLocal()
    try:
        mediciones = (
            db.query(MedicionCliente)
            .filter(MedicionCliente.cliente_negocio_id == cliente_negocio_id)
            .order_by(MedicionCliente.fecha.asc())
            .all()
        )
        return [_serializar_medicion(m) for m in mediciones]
    finally:
        db.close()


def _frecuencia_asistencia(checkins: list) -> dict:
    """Qué tan habitual viene un socio: visitas en los últimos 30 días,
    promedio semanal, y días desde la última visita."""
    ahora = datetime.utcnow()
    ventana = ahora - timedelta(days=VENTANA_FRECUENCIA_DIAS)
    visitas_recientes = len([c for c in checkins if c.fecha >= ventana])
    promedio_semanal = round(visitas_recientes / (VENTANA_FRECUENCIA_DIAS / 7), 1)
    dias_desde_ultima_visita = None
    if checkins:
        ultima = max(c.fecha for c in checkins)
        dias_desde_ultima_visita = (ahora - ultima).days
    return {
        "visitas_ultimos_30_dias": visitas_recientes,
        "promedio_semanal": promedio_semanal,
        "dias_desde_ultima_visita": dias_desde_ultima_visita,
        "total_checkins_historico": len(checkins),
    }


def obtener_perfil_cliente(cliente_negocio_id: str) -> dict:
    """Arma el perfil completo de UN socio: datos básicos + tiempo como
    socio + última medición/BMI + cambios desde la primera medición +
    frecuencia de asistencia. Esto es lo que alimenta tanto el dashboard
    del cliente (panel del negocio) como la herramienta de voz
    consultar_progreso del vertical Gym."""
    db = SessionLocal()
    try:
        cliente = db.query(ClienteNegocio).filter(ClienteNegocio.id == cliente_negocio_id).first()
        if not cliente:
            return {"error": "Cliente no encontrado"}

        mediciones = (
            db.query(MedicionCliente)
            .filter(MedicionCliente.cliente_negocio_id == cliente_negocio_id)
            .order_by(MedicionCliente.fecha.asc())
            .all()
        )
        checkins = db.query(Checkin).filter(Checkin.cliente_negocio_id == cliente_negocio_id).all()

        primera, ultima = (mediciones[0], mediciones[-1]) if mediciones else (None, None)

        progreso = None
        if primera and ultima and primera.id != ultima.id:
            bmi_primera = calcular_bmi(primera.peso_kg, primera.altura_cm)
            bmi_ultima = calcular_bmi(ultima.peso_kg, ultima.altura_cm)
            progreso = {
                "delta_peso_kg": (
                    round(ultima.peso_kg - primera.peso_kg, 1)
                    if ultima.peso_kg is not None and primera.peso_kg is not None else None
                ),
                "delta_bmi": (
                    round(bmi_ultima - bmi_primera, 1)
                    if bmi_ultima is not None and bmi_primera is not None else None
                ),
                "desde": primera.fecha.isoformat(),
                "hasta": ultima.fecha.isoformat(),
            }

        dias_como_socio = (datetime.utcnow() - cliente.fecha_ingreso).days if cliente.fecha_ingreso else None

        return {
            "cliente_id": cliente.id,
            "nombre": cliente.nombre,
            "telefono": cliente.telefono,
            "estado_membresia": cliente.estado_membresia,
            "meses_adeudados": cliente.meses_adeudados,
            "proximo_vencimiento": cliente.proximo_vencimiento.isoformat() if cliente.proximo_vencimiento else None,
            "plan_membresia": cliente.plan_membresia,
            "fecha_ingreso": cliente.fecha_ingreso.isoformat() if cliente.fecha_ingreso else None,
            "dias_como_socio": dias_como_socio,
            "meses_como_socio": round(dias_como_socio / 30, 1) if dias_como_socio is not None else None,
            "altura_cm": cliente.altura_cm,
            "ultima_medicion": _serializar_medicion(ultima) if ultima else None,
            "progreso": progreso,
            "total_mediciones": len(mediciones),
            "asistencia": _frecuencia_asistencia(checkins),
        }
    finally:
        db.close()
