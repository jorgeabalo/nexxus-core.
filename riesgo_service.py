"""
Alertas de riesgo: qué socios están por irse (para actuar ANTES de que
cancelen) y qué socios ya se fueron y podrían reactivarse.

Reglas simples y explicables a propósito — no es un modelo de predicción ni
una caja negra: cada alerta trae el/los motivo(s) exactos ("Debe 2 meses",
"No viene hace 24 días"), mismo principio que el resto de NEXXUS: nunca
mostrar un número que el negocio no pueda auditar contra un dato real.

Separado de progreso_service.py (que es el historial/perfil de UN socio)
porque esto es una vista agregada de TODO el negocio — mismo tipo de
separación que ya existe entre generic_service.py y billing_service.py.
"""

from datetime import datetime, timedelta

from models import SessionLocal, ClienteNegocio, Checkin

VENTANA_DIAS = 30
DIAS_SIN_VENIR_ALERTA = 10
DIAS_SIN_VENIR_CRITICO = 21
CAIDA_ASISTENCIA_ALERTA = 0.5  # 50% o más de caída vs. el período anterior


def _dias_desde_ultima_visita(checkins, ahora):
    if not checkins:
        return None
    return (ahora - max(c.fecha for c in checkins)).days


def calcular_riesgo_cliente(cliente: ClienteNegocio, checkins: list, ahora: datetime = None) -> dict:
    """cliente: un ClienteNegocio activo. checkins: TODO el historial de
    check-ins de ese cliente (no solo los recientes — acá se recorta la
    ventana que hace falta). Devuelve None si no hay ninguna señal de
    riesgo, o {"nivel": "alerta"|"critico", "motivos": [...]}."""
    ahora = ahora or datetime.utcnow()

    motivos_criticos = []
    motivos_alerta = []

    if cliente.meses_adeudados >= 2:
        motivos_criticos.append(f"Debe {cliente.meses_adeudados} meses")
    elif cliente.meses_adeudados == 1:
        motivos_alerta.append("Debe 1 mes")

    dias_sin_venir = _dias_desde_ultima_visita(checkins, ahora)
    antiguedad_dias = (ahora - cliente.fecha_ingreso).days if cliente.fecha_ingreso else None

    if dias_sin_venir is None:
        # Nunca registró una visita — solo es señal si ya lleva tiempo
        # como socio (un socio nuevo de una semana sin check-in es normal).
        if antiguedad_dias is not None and antiguedad_dias >= DIAS_SIN_VENIR_CRITICO:
            motivos_criticos.append("Nunca registró una visita")
    elif dias_sin_venir >= DIAS_SIN_VENIR_CRITICO:
        motivos_criticos.append(f"No viene hace {dias_sin_venir} días")
    elif dias_sin_venir >= DIAS_SIN_VENIR_ALERTA:
        motivos_alerta.append(f"No viene hace {dias_sin_venir} días")

    ventana_actual = ahora - timedelta(days=VENTANA_DIAS)
    ventana_previa = ahora - timedelta(days=VENTANA_DIAS * 2)
    visitas_actual = len([c for c in checkins if c.fecha >= ventana_actual])
    visitas_previa = len([c for c in checkins if ventana_previa <= c.fecha < ventana_actual])
    # Solo cuenta la caída si venía con un mínimo de actividad antes —
    # si no, un socio con 1 sola visita hace 40 días dispararía un "100%"
    # que no dice nada real.
    if visitas_previa >= 2:
        caida = 1 - (visitas_actual / visitas_previa)
        if caida >= CAIDA_ASISTENCIA_ALERTA:
            porcentaje = round(caida * 100)
            motivos_alerta.append(f"Bajó su asistencia {porcentaje}% este mes")

    if motivos_criticos:
        return {"nivel": "critico", "motivos": motivos_criticos + motivos_alerta}
    if motivos_alerta:
        return {"nivel": "alerta", "motivos": motivos_alerta}
    return None


def calcular_riesgo_por_id(cliente_negocio_id: str) -> dict:
    """Mismo cálculo que calcular_riesgo_cliente, pero abriendo su propia
    sesión — pensado para usarse desde un endpoint que ya tiene el cliente
    resuelto por otro lado (ej. el perfil del socio en el dashboard)."""
    db = SessionLocal()
    try:
        cliente = db.query(ClienteNegocio).filter(ClienteNegocio.id == cliente_negocio_id).first()
        if not cliente or cliente.estado_membresia != "activo":
            return None
        checkins = db.query(Checkin).filter(Checkin.cliente_negocio_id == cliente_negocio_id).all()
        return calcular_riesgo_cliente(cliente, checkins)
    finally:
        db.close()


def obtener_alertas_negocio(negocio_id: str) -> dict:
    """Vista agregada para el panel del negocio: quién está en riesgo de
    irse (socios activos) y quién ya se fue y podría reactivarse (pausados
    o cancelados)."""
    db = SessionLocal()
    try:
        clientes = db.query(ClienteNegocio).filter(ClienteNegocio.negocio_id == negocio_id).all()
        checkins_todos = db.query(Checkin).filter(Checkin.negocio_id == negocio_id).all()
        checkins_por_cliente = {}
        for c in checkins_todos:
            checkins_por_cliente.setdefault(c.cliente_negocio_id, []).append(c)

        ahora = datetime.utcnow()
        en_riesgo = []
        reactivacion = []

        for cliente in clientes:
            checkins_cliente = checkins_por_cliente.get(cliente.id, [])
            if cliente.estado_membresia == "activo":
                riesgo = calcular_riesgo_cliente(cliente, checkins_cliente, ahora)
                if riesgo:
                    en_riesgo.append({
                        "cliente_id": cliente.id,
                        "nombre": cliente.nombre,
                        "telefono": cliente.telefono,
                        "nivel": riesgo["nivel"],
                        "motivos": riesgo["motivos"],
                    })
            elif cliente.estado_membresia in ("pausado", "cancelado"):
                ultima_visita = max((c.fecha for c in checkins_cliente), default=None)
                reactivacion.append({
                    "cliente_id": cliente.id,
                    "nombre": cliente.nombre,
                    "telefono": cliente.telefono,
                    "estado_membresia": cliente.estado_membresia,
                    "plan_membresia": cliente.plan_membresia,
                    "ultima_visita": ultima_visita.isoformat() if ultima_visita else None,
                })

        orden_nivel = {"critico": 0, "alerta": 1}
        en_riesgo.sort(key=lambda r: orden_nivel[r["nivel"]])

        return {
            "en_riesgo": en_riesgo,
            "reactivacion": reactivacion,
            "total_en_riesgo": len(en_riesgo),
            "total_criticos": len([r for r in en_riesgo if r["nivel"] == "critico"]),
            "total_reactivacion": len(reactivacion),
        }
    finally:
        db.close()
