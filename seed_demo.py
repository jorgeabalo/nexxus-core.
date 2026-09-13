"""
Siembra un negocio de prueba ("Fuerza Total", vertical gym) con socios
ficticios — los mismos datos que se usaron en el boceto visual del
dashboard, para que ambos coincidan. Sirve para probar el motor de punta a
punta mientras Jorge consigue un gym real (dijo que ya está en camino).

Uso: python3 seed_demo.py
"""

import uuid
from datetime import datetime, timedelta

from models import SessionLocal, Negocio, ClienteNegocio, Suscripcion, MedicionCliente, Checkin


def sembrar():
    db = SessionLocal()
    try:
        existente = db.query(Negocio).filter(Negocio.slug == "fuerza-total").first()
        if existente:
            print("Ya existe 'fuerza-total' — no se vuelve a crear. Borrá nexxus_core.sqlite si querés reiniciar.")
            return existente

        negocio = Negocio(
            id=str(uuid.uuid4())[:8],
            slug="fuerza-total",
            nombre="Fuerza Total",
            vertical="gym",
            idioma_principal="es",
            idioma_secundario="en",
            nombre_asistente="María",
            plan="professional",
            activo=True,
        )
        db.add(negocio)
        db.flush()  # para tener negocio.id disponible

        db.add(Suscripcion(
            id=str(uuid.uuid4())[:8],
            negocio_id=negocio.id,
            plan="professional",
            precio_mensual=199.0,
            estado="activa",
            fecha_proximo_cobro=datetime.utcnow() + timedelta(days=18),
        ))

        socios = [
            {"nombre": "María Torres", "telefono": "+17135550142", "estado_membresia": "activo",
             "meses_adeudados": 3, "proximo_vencimiento": datetime.utcnow() + timedelta(days=1), "plan_membresia": "Mensual",
             "altura_cm": 162, "fecha_ingreso": datetime.utcnow() - timedelta(days=210),
             "pesos": [78.5, 76.2, 74.0], "visitas_ultimos_30_dias": 9},
            {"nombre": "Carlos Núñez", "telefono": "+12815550177", "estado_membresia": "activo",
             "meses_adeudados": 1, "proximo_vencimiento": datetime.utcnow() + timedelta(days=5), "plan_membresia": "Mensual",
             "altura_cm": 178, "fecha_ingreso": datetime.utcnow() - timedelta(days=60),
             "pesos": [92.0, 90.5], "visitas_ultimos_30_dias": 2, "visitas_periodo_anterior": 7},
            {"nombre": "Jorge Paredes", "telefono": "+17135550199", "estado_membresia": "activo",
             "meses_adeudados": 1, "proximo_vencimiento": datetime.utcnow() + timedelta(days=9), "plan_membresia": "Mensual",
             "altura_cm": 174, "fecha_ingreso": datetime.utcnow() - timedelta(days=35),
             "pesos": [88.0], "visitas_ultimos_30_dias": 2},
            {"nombre": "Diana Reyes", "telefono": "+17135550120", "estado_membresia": "activo",
             "meses_adeudados": 0, "proximo_vencimiento": datetime.utcnow() + timedelta(days=22), "plan_membresia": "Anual",
             "altura_cm": 168, "fecha_ingreso": datetime.utcnow() - timedelta(days=400),
             "pesos": [70.0, 68.5, 67.0, 66.2], "visitas_ultimos_30_dias": 14},
            {"nombre": "Luis Fernández", "telefono": "+17135550188", "estado_membresia": "pausado",
             "meses_adeudados": 0, "proximo_vencimiento": None, "plan_membresia": "Mensual",
             "altura_cm": 180, "fecha_ingreso": datetime.utcnow() - timedelta(days=500),
             "pesos": [95.0], "visitas_ultimos_30_dias": 0},
            {"nombre": "Sofía Delgado", "telefono": "+17135550133", "estado_membresia": "cancelado",
             "meses_adeudados": 0, "proximo_vencimiento": None, "plan_membresia": "Mensual",
             "altura_cm": 165, "fecha_ingreso": datetime.utcnow() - timedelta(days=300),
             "pesos": [80.0, 79.0], "visitas_ultimos_30_dias": 0, "visitas_periodo_anterior": 0,
             "ultima_visita_hace_dias": 95},
        ]
        for s in socios:
            pesos = s.pop("pesos")
            visitas = s.pop("visitas_ultimos_30_dias")
            visitas_previas = s.pop("visitas_periodo_anterior", 0)
            ultima_visita_hace_dias = s.pop("ultima_visita_hace_dias", None)
            cliente = ClienteNegocio(id=str(uuid.uuid4())[:8], negocio_id=negocio.id, **s)
            db.add(cliente)
            db.flush()

            # Historial de mediciones — la más vieja hace tantos días como
            # mediciones haya, espaciadas cada ~30 días, terminando "hoy".
            total = len(pesos)
            for i, peso in enumerate(pesos):
                dias_atras = (total - 1 - i) * 30
                db.add(MedicionCliente(
                    id=str(uuid.uuid4())[:8],
                    cliente_negocio_id=cliente.id,
                    fecha=datetime.utcnow() - timedelta(days=dias_atras),
                    peso_kg=peso,
                    altura_cm=s["altura_cm"],
                ))

            # Check-ins de los últimos 30 días, repartidos.
            for i in range(visitas):
                db.add(Checkin(
                    id=str(uuid.uuid4())[:8],
                    negocio_id=negocio.id,
                    cliente_negocio_id=cliente.id,
                    fecha=datetime.utcnow() - timedelta(days=(i * 3) % 30, hours=i),
                ))

            # Check-ins del período anterior (30-60 días atrás) — sirve para
            # demostrar la señal de "bajó su asistencia" (ver riesgo_service.py).
            for i in range(visitas_previas):
                db.add(Checkin(
                    id=str(uuid.uuid4())[:8],
                    negocio_id=negocio.id,
                    cliente_negocio_id=cliente.id,
                    fecha=datetime.utcnow() - timedelta(days=31 + (i * 4) % 29, hours=i),
                ))

            # Para un socio pausado/cancelado, una última visita puntual hace
            # rato (en vez de nada) — así "Para reactivar" muestra una fecha
            # real en lugar de "sin visitas registradas" para todos.
            if ultima_visita_hace_dias is not None:
                db.add(Checkin(
                    id=str(uuid.uuid4())[:8],
                    negocio_id=negocio.id,
                    cliente_negocio_id=cliente.id,
                    fecha=datetime.utcnow() - timedelta(days=ultima_visita_hace_dias),
                ))

        db.commit()
        print(f"Negocio demo creado: {negocio.nombre} (slug='{negocio.slug}', id={negocio.id}) con {len(socios)} socios.")
        return negocio
    finally:
        db.close()


if __name__ == "__main__":
    sembrar()
