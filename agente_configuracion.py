"""Adaptador conversacional de onboarding para AITA.

Conserva las herramientas útiles de Claude; las escrituras son propuestas.
El Orchestrator decide capacidades y onboarding_service reutiliza las altas.
No recibe claves, datos bancarios o fiscales ni mantiene otra BusinessProfile.
"""
import json
import os
from anthropic import Anthropic
from pydantic import ValidationError
from verticals import VERTICALES
from onboarding_service import (
    WRITE_TOOLS, OnboardingError, list_businesses, read_business, propose_action,
)

MODELO_CLAUDE = os.getenv("MODELO_CLAUDE", "claude-haiku-4-5-20251001")
MAX_ITERACIONES_TOOL_USE = 6
PROMPT_SISTEMA = """Eres el asistente interno de onboarding AITA para el operador.
Responde en español. Consulta datos reales mediante las herramientas.
Las escrituras preparan propuestas: NO se ejecutan hasta que el operador
pulse Confirmar en el panel. No afirmes que una propuesta está guardada como
negocio o socio. No inventes datos faltantes. Una carga de socios no es una
actualización: nunca uses cargar_socio para modificar un socio existente.
No pidas ni incluyas credenciales, EIN, cuentas bancarias o datos de tarjetas.
BusinessProfile es la fuente de contexto empresarial. No envíes campañas,
no llames, no cobres ni crees suscripciones. Si faltan datos, pregunta.
"""

TOOLS = [
    {
        "name": "listar_negocios",
        "description": "Lista todos los negocios (gyms, clínicas, etc.) ya dados de alta en NEXXUS, con su slug, vertical y estado.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "obtener_negocio",
        "description": "Trae los datos completos de un negocio por su slug, incluyendo cuántos socios/clientes tiene cargados.",
        "input_schema": {
            "type": "object",
            "properties": {
                "slug": {"type": "string", "description": "Slug del negocio, ej. 'golden-age'."}
            },
            "required": ["slug"],
        },
    },
    {
        "name": "crear_negocio",
        "description": (
            "Da de alta un negocio nuevo (un cliente de NEXXUS, ej. un gym). Usar cuando Jorge "
            "pide crear/dar de alta un negocio nuevo."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "slug": {"type": "string", "description": "Identificador único para la URL, ej. 'golden-age'."},
                "nombre": {"type": "string", "description": "Nombre real del negocio, ej. 'Golden Age'."},
                "vertical": {
                    "type": "string",
                    "description": f"Tipo de negocio. Disponibles: {list(VERTICALES.keys())}. Si no se especifica, usar 'gym'.",
                },
                "nombre_asistente": {
                    "type": "string",
                    "description": "Nombre que va a usar la IA cuando atienda a los socios de este negocio, ej. 'María'. Si no se especifica, usar 'María'.",
                },
                "plan": {
                    "type": "string",
                    "description": "Plan de NEXXUS que va a pagar este negocio: 'starter', 'professional' o 'enterprise'. Si Jorge no lo dice, usar 'starter'.",
                },
            },
            "required": ["slug", "nombre"],
        },
    },
    {
        "name": "cargar_socio",
        "description": "Carga UN socio/cliente nuevo en un negocio ya existente.",
        "input_schema": {
            "type": "object",
            "properties": {
                "negocio_slug": {"type": "string", "description": "Slug del negocio al que pertenece este socio."},
                "nombre": {"type": "string", "description": "Nombre completo del socio."},
                "telefono": {"type": "string", "description": "Teléfono del socio (se usa para identificarlo cuando llama)."},
                "email": {"type": "string", "description": "Email del socio, si lo tiene."},
                "estado_membresia": {
                    "type": "string",
                    "description": "'activo', 'pausado' o 'cancelado'. Si no se dice, usar 'activo'.",
                },
                "meses_adeudados": {"type": "integer", "description": "Cuántos meses debe. Si no se dice, 0."},
                "proximo_vencimiento": {"type": "string", "description": "Fecha del próximo vencimiento, formato AAAA-MM-DD."},
                "plan_membresia": {"type": "string", "description": "Plan del socio dentro del negocio, ej. 'Mensual' o 'Anual'."},
                "altura_cm": {"type": "number", "description": "Altura en centímetros, si se conoce (para BMI/progreso)."},
            },
            "required": ["negocio_slug", "nombre"],
        },
    },
    {
        "name": "cargar_socios_multiples",
        "description": (
            "Carga VARIOS socios/clientes de una sola vez en un negocio ya existente — usar "
            "siempre que Jorge pase una lista o pegue datos de varios socios en el mismo mensaje, "
            "en vez de llamar a cargar_socio una vez por cada uno."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "negocio_slug": {"type": "string", "description": "Slug del negocio al que pertenecen estos socios."},
                "socios": {
                    "type": "array",
                    "description": "Lista de socios a cargar.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "nombre": {"type": "string"},
                            "telefono": {"type": "string"},
                            "email": {"type": "string"},
                            "estado_membresia": {"type": "string"},
                            "meses_adeudados": {"type": "integer"},
                            "proximo_vencimiento": {"type": "string"},
                            "plan_membresia": {"type": "string"},
                            "altura_cm": {"type": "number"},
                        },
                        "required": ["nombre"],
                    },
                },
            },
            "required": ["negocio_slug", "socios"],
        },
    },
    {
        "name": "actualizar_configuracion_negocio",
        "description": (
            "Cambia datos de configuración de un negocio ya existente (nombre del asistente de "
            "IA, idiomas, si está activo o no). No modifica socios existentes. cargar_socio siempre crea uno nuevo."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "slug": {"type": "string", "description": "Slug del negocio a modificar."},
                "nombre_asistente": {"type": "string", "description": "Nuevo nombre para la IA que atiende a los socios."},
                "idioma_principal": {"type": "string", "description": "Código de idioma principal, ej. 'es'."},
                "idioma_secundario": {"type": "string", "description": "Código de idioma secundario, ej. 'en'."},
                "activo": {"type": "boolean", "description": "true para activar el servicio de este negocio, false para desactivarlo."},
            },
            "required": ["slug"],
        },
    },
]




def ejecutar_tool(tool_name, tool_input, operador):
    if not operador:
        raise OnboardingError("Operador requerido", 403)
    if tool_name == "listar_negocios":
        return list_businesses()
    if tool_name == "obtener_negocio":
        return read_business(tool_input.get("slug", ""))
    if tool_name in WRITE_TOOLS:
        return propose_action(tool_name, tool_input, operador)
    raise OnboardingError("Herramienta no permitida", 403)


class AgenteConfiguracion:
    def __init__(self, api_key=None):
        key = api_key or os.getenv("ANTHROPIC_API_KEY")
        self.client = Anthropic(api_key=key) if key else None

    def procesar_mensaje(self, mensaje, historial=None, *, operador):
        history = list(historial or [])
        if not self.client:
            return {"respuesta": "El asistente no está disponible todavía.",
                    "historial": history, "acciones_realizadas": [], "propuestas": []}
        messages = history + [{"role": "user", "content": mensaje}]
        proposals = []
        reads = []
        response_text = "Se alcanzó el límite del turno. Revisa las propuestas antes de continuar."
        try:
            for _ in range(MAX_ITERACIONES_TOOL_USE):
                response = self.client.messages.create(
                    model=MODELO_CLAUDE, max_tokens=1600, system=PROMPT_SISTEMA,
                    tools=TOOLS, messages=messages,
                )
                if response.stop_reason != "tool_use":
                    response_text = "".join(b.text for b in response.content if b.type == "text")
                    break
                messages.append({"role": "assistant", "content": [
                    b.model_dump(mode="json", exclude_none=True) for b in response.content
                ]})
                tool_results = []
                for block in response.content:
                    if block.type != "tool_use":
                        continue
                    try:
                        result = ejecutar_tool(block.name, block.input, operador)
                    except (OnboardingError, ValidationError, ValueError, TypeError):
                        result = {"error": "Acción no válida o no permitida. Revisa los datos y el negocio."}
                    if result.get("requiere_confirmacion"):
                        if not any(p["id"] == result["id"] for p in proposals):
                            proposals.append(result)
                    else:
                        reads.append({"tool": block.name, "resultado": result})
                    tool_results.append({"type": "tool_result", "tool_use_id": block.id,
                                         "content": json.dumps(result, ensure_ascii=False)})
                messages.append({"role": "user", "content": tool_results})
        except Exception:
            # No incluir errores del proveedor, inputs, claves o datos de socios en logs.
            response_text = "No pude terminar el turno. Revisa las propuestas disponibles antes de reintentar."
        # El navegador recibe sólo diálogo en texto, nunca bloques SDK/tool_use
        # que pueda reenviar como nuevas órdenes o afirmaciones de ejecución.
        history += [{"role": "user", "content": mensaje}, {"role": "assistant", "content": response_text[:8000]}]
        return {"respuesta": response_text, "historial": history[-20:],
                "acciones_realizadas": reads, "propuestas": proposals}
