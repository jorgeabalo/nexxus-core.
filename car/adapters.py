from typing import Protocol

class OBDAdapter(Protocol):
    def read(self, vehicle: dict) -> dict: ...

class MockOBD:
    def read(self, vehicle):
        return {'source': 'simulated', 'connected': False, 'rpm': 820,
                'coolant_c': 89, 'battery_v': 12.6, 'codes': ['P0442'],
                'notice': 'Lectura simulada. P0442: posible fuga EVAP; requiere revisión, no es diagnóstico confirmado.'}

class FutureOBDBLE:
    def read(self, vehicle):
        raise NotImplementedError('BLE pendiente: validar dispositivo, PIDs, permisos y reconexión nativa.')

class ProjectionAdapter(Protocol):
    def capabilities(self) -> dict: ...

class FutureProjection:
    def capabilities(self):
        return {'ottocast': 'pending_hardware_validation', 'carplay': 'not_integrated',
                'android_auto': 'not_integrated', 'universal_compatibility': False,
                'automatic_background_connection': False}

class NexxusAdapter(Protocol):
    def answer(self, question: str, context: dict) -> str: ...

class LocalNexxus:
    """Deterministic context-backed demo; no paid provider calls."""
    def answer(self, question, context):
        if context['vehicle'].get('distance_unit') == 'km':
            v = dict(context['vehicle'])
            v['odometer'] *= 1.609344
            v['oil_due'] *= 1.609344
            context = {**context, 'vehicle': v}
            return self._answer(question, context).replace(' miles', ' kilometers').replace(' millas', ' kilómetros')
        return self._answer(question, context)

    def _answer(self, question, context):
        v = context['vehicle']; q = question.lower()
        if context.get('language') == 'en':
            if any(w in q for w in ('fuel', 'gas', 'expense', 'cost', 'spent', 'gasolina', 'gast', 'costo')):
                return f"Your recorded expenses total ${context['expense_total']:.2f}. Trip fuel costs are estimated using {v['mpg']} MPG and ${v['fuel_price']}/gallon; they are not added to your actual expenses."
            if any(w in q for w in ('park', 'estacion', 'aparqué')):
                p = context['parking']
                return f"Last parking location: {p['lat']}, {p['lon']}, recorded {p['at']}." if p else 'You have not saved a parking location yet.'
            if any(w in q for w in ('diagn', 'obd', 'engine', 'fault', 'motor', 'falla')):
                return 'OBD disconnected. Configure your adapter in Profile; no real diagnosis is available yet.'
            if any(w in q for w in ('oil', 'mainten', 'aceite', 'manten')):
                return f"For your {v['make']} {v['model']} {v['year']}, your custom oil reminder is due in {max(0, v['oil_due']-v['odometer']):.0f} miles. Check the interval in the manual; we do not have an official manufacturer catalog."
            return f"I am Nexxus. Your {v['make']} {v['model']} has {v['odometer']:.1f} miles. I can look up trips, fuel, maintenance and parking."
        if any(w in q for w in ('gasolina', 'gast', 'costo', 'fuel', 'expense', 'cost')):
            return f"Tus gastos registrados suman ${context['expense_total']:.2f}. El combustible de los viajes es estimado con {v['mpg']} MPG y ${v['fuel_price']}/galón; no se agrega a tus gastos reales."
        if any(w in q for w in ('estacion', 'aparqué', 'parque', 'park')):
            p = context['parking']
            return f"Último estacionamiento: {p['lat']}, {p['lon']}, registrado {p['at']}." if p else 'Todavía no has guardado un estacionamiento.'
        if any(w in q for w in ('diagn', 'obd', 'motor', 'falla', 'engine')):
            return 'OBD sin conexión. Configura tu adaptador en Perfil; todavía no hay un diagnóstico real.'
        if any(w in q for w in ('aceite', 'manten', 'oil', 'mainten')):
            return f"Para {v['make']} {v['model']} {v['year']}, tu recordatorio personalizado de aceite vence en {max(0, v['oil_due']-v['odometer']):.0f} millas. Confirma el intervalo en el manual; no contamos con un catálogo oficial del fabricante."
        return f"Soy Nexxus. Tu {v['make']} {v['model']} tiene {v['odometer']:.1f} millas. Puedo consultar viajes, gasolina, mantenimiento y estacionamiento."
