"""
Registro de verticales de NEXXUS.

Cada vertical (gym, clinica, restaurante, ...) es un módulo que expone:
- CONFIG: dict con las instrucciones extra del prompt y las herramientas
  (tools) que Claude puede invocar para este vertical.
- ejecutar_tool(db, negocio, tool_name, tool_input) -> resultado (dict)

La arquitectura "plantilla genérica" (decidida en la planificación) significa
que el motor (generic_service.py) NO tiene lógica de negocio de ningún
vertical adentro — solo sabe pedirle su config al vertical activo y
ejecutar las tools que ese vertical define. Agregar Restaurante/Clínica más
adelante es agregar un archivo acá, no tocar el motor.
"""

from . import gym

VERTICALES = {
    "gym": gym,
}


def obtener_vertical(nombre: str):
    return VERTICALES.get(nombre, VERTICALES["gym"])
