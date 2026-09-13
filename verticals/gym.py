"""
Vertical: Gym / centro de fitness — el piloto elegido para Fase 2.

Cubre el alcance ya decidido para el piloto: consultas de membresía/pagos
("¿este socio debe algo?"), horario de clases, y check-in. Este es
exactamente el caso de uso que Jorge pidió originalmente por voz: un
empleado pregunta por el estado de pago de un cliente y el sistema responde
con datos reales de la base — nunca un monto inventado por el modelo.
"""

from datetime import datetime
from sqlalchemy import or_

import progreso_service

CONFIG = {
    "nombre_vertical": "gym",
    "descripcion": "Centro de fitness / gimnasio",
    "instrucciones_extra": (
        "Este negocio es un gimnasio. Ayudás con: (1) estado de cuenta/membresía "
        "de un socio — SIEMPRE usá la herramienta consultar_estado_cuenta antes de "
        "afirmar cuánto debe alguien o cuándo vence su pago, nunca lo inventes ni lo "
        "calcules de memoria; (2) horario de clases — usá consultar_horario_clases; "
        "(3) confirmar el check-in de un socio que llega al gimnasio — usá "
        "registrar_checkin; (4) progreso de un socio (peso, BMI, hace cuánto es "
        "socio, qué tan seguido viene) — usá consultar_progreso, nunca inventes ni "
        "calcules estos datos de memoria. Si te preguntan por un socio y no tenés "
        "su nombre o teléfono, pedilo antes de buscar."
    ),
}

# Horario de clases — demo estático para el piloto. Se puede reemplazar por
# una consulta a una agenda real sin tocar el motor genérico.
CLASES_DEMO = [
    {"clase": "Spinning", "hora": "6:00 AM", "cupo": "18/22"},
    {"clase": "Yoga", "hora": "8:00 AM", "cupo": "11/15"},
    {"clase": "CrossFit", "hora": "6:00 PM", "cupo": "20/20"},
    {"clase": "Zumba", "hora": "7:00 PM", "cupo": "9/25"},
]

TOOLS = [
    {
        "name": "consultar_estado_cuenta",
        "description": (
            "Busca a un socio del gimnasio por nombre o teléfono y devuelve su "
            "estado real de membresía: si está al día, cuántos meses adeuda y "
            "cuándo vence su próximo pago. Usar SIEMPRE antes de responder "
            "cualquier pregunta sobre pagos o estado de cuenta de un socio."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "identificador": {
                    "type": "string",
                    "description": "Nombre completo o número de teléfono del socio.",
                }
            },
            "required": ["identificador"],
        },
    },
    {
        "name": "consultar_horario_clases",
        "description": "Devuelve el horario de clases del gimnasio para hoy.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "registrar_checkin",
        "description": (
            "Confirma el check-in de un socio que llega al gimnasio. Solo usar "
            "si el socio está activo (no lo uses para socios cancelados)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "identificador": {
                    "type": "string",
                    "description": "Nombre completo o número de teléfono del socio.",
                }
            },
            "required": ["identificador"],
        },
    },
    {
        "name": "consultar_progreso",
        "description": (
            "Busca a un socio por nombre o teléfono y devuelve su progreso real: "
            "peso y BMI de la última medición, cambio de peso/BMI desde la primera "
            "medición registrada, hace cuánto es socio, y qué tan seguido viene "
            "(visitas en los últimos 30 días). Usar SIEMPRE antes de responder "
            "cualquier pregunta sobre peso, medidas, BMI, progreso o frecuencia de "
            "asistencia — nunca inventar ni calcular estos datos de memoria."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "identificador": {
                    "type": "string",
                    "description": "Nombre completo o número de teléfono del socio.",
                }
            },
            "required": ["identificador"],
        },
    },
]


def _buscar_cliente(db, negocio_id, identificador):
    from models import ClienteNegocio

    identificador = (identificador or "").strip()
    return (
        db.query(ClienteNegocio)
        .filter(
            ClienteNegocio.negocio_id == negocio_id,
            or_(
                ClienteNegocio.nombre.ilike(f"%{identificador}%"),
                ClienteNegocio.telefono == identificador,
            ),
        )
        .first()
    )


def ejecutar_tool(db, negocio, tool_name, tool_input):
    """Ejecuta una tool del vertical Gym contra datos reales de la BD.
    Devuelve SIEMPRE un dict serializable — esto es lo que se le manda de
    vuelta a Claude como resultado de la herramienta."""

    if tool_name == "consultar_estado_cuenta":
        cliente = _buscar_cliente(db, negocio.id, tool_input.get("identificador", ""))
        if not cliente:
            return {"encontrado": False, "mensaje": "No se encontró ningún socio con ese nombre o teléfono."}
        return {
            "encontrado": True,
            "nombre": cliente.nombre,
            "estado_membresia": cliente.estado_membresia,
            "meses_adeudados": cliente.meses_adeudados,
            "proximo_vencimiento": (
                cliente.proximo_vencimiento.strftime("%Y-%m-%d") if cliente.proximo_vencimiento else None
            ),
            "plan_membresia": cliente.plan_membresia,
        }

    if tool_name == "consultar_horario_clases":
        return {"clases": CLASES_DEMO}

    if tool_name == "registrar_checkin":
        cliente = _buscar_cliente(db, negocio.id, tool_input.get("identificador", ""))
        if not cliente:
            return {"encontrado": False, "mensaje": "No se encontró ningún socio con ese nombre o teléfono."}
        if cliente.estado_membresia != "activo":
            return {
                "encontrado": True,
                "checkin_confirmado": False,
                "motivo": f"Membresía en estado '{cliente.estado_membresia}', no se puede hacer check-in.",
            }
        # Guarda el check-in de verdad (progreso_service.py, tabla Checkin)
        # — usa su propia sesión de BD, separada de `db` (mismo patrón que
        # billing_service.py), porque es una escritura de negocio, no una
        # lectura de la conversación en curso.
        progreso_service.registrar_checkin(negocio.id, cliente.id)
        return {"encontrado": True, "checkin_confirmado": True, "nombre": cliente.nombre, "hora": datetime.utcnow().strftime("%H:%M UTC")}

    if tool_name == "consultar_progreso":
        cliente = _buscar_cliente(db, negocio.id, tool_input.get("identificador", ""))
        if not cliente:
            return {"encontrado": False, "mensaje": "No se encontró ningún socio con ese nombre o teléfono."}
        perfil = progreso_service.obtener_perfil_cliente(cliente.id)
        perfil["encontrado"] = True
        return perfil

    return {"error": f"Herramienta desconocida: {tool_name}"}
