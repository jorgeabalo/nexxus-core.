"""
Motor genérico de IA de NEXXUS AI Support (Fase 2).

Esto reemplaza a recepcionista_service.py del MVP anterior. La diferencia
central con el MVP: ese motor tenía UN prompt fijo para UN negocio. Este
motor es "plantilla genérica" (decisión ya tomada en la planificación): no
sabe nada de gimnasios, clínicas ni restaurantes — carga la configuración
del `Negocio` (su vertical) desde la base de datos y arma el prompt y las
herramientas disponibles dinámicamente. Agregar un vertical nuevo es
agregar un archivo en verticals/, no tocar este archivo.

Segundo cambio central: usa TOOL CALLING real de Claude en vez de dejar que
el modelo conteste de memoria. Para cualquier pregunta de datos reales (ej.
"¿cuánto debe este socio?"), el modelo está instruido a invocar una
herramienta que consulta la base de datos — así la respuesta nunca es una
alucinación del modelo, siempre viene de un dato real. Esto es justo lo que
Jorge pidió originalmente: "el usuario pedirá por voz el reporte de un
cliente... y la IA le contestará con el estado real de pago".
"""

import os
import json
import uuid
from datetime import datetime

from anthropic import Anthropic
from dotenv import load_dotenv

from models import SessionLocal, Negocio, ClienteNegocio, Llamada, Conversacion
from verticals import obtener_vertical

load_dotenv()

MODELO_CLAUDE = os.getenv("MODELO_CLAUDE", "claude-haiku-4-5-20251001")
MAX_CARACTERES_MENSAJE = 2000
UMBRAL_ESCALADO_SEGUNDOS = 20 * 60
MAX_ITERACIONES_TOOL_USE = 4  # tope de seguridad para no loopear infinito

NOMBRES_IDIOMA = {
    "es": "español",
    "en": "English",
    "fr": "français",
    "de": "Deutsch",
    "it": "italiano",
    "pt": "português",
    "ja": "日本語",
    "zh": "中文",
    "ru": "русский",
    "ar": "العربية",
    "hi": "हिन्दी",
    "ko": "한국어",
}

FALLBACKS = {
    "es": "Lo siento, en este momento no puedo procesar tu solicitud. Por favor, intenta de nuevo o espera al supervisor.",
    "en": "I'm sorry, I cannot process your request at this moment. Please try again or wait for the supervisor.",
}


class NexxusIAService:
    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        if not self.api_key:
            print(
                "⚠️  ANTHROPIC_API_KEY no configurada. Todas las respuestas usarán "
                "el fallback pre-grabado hasta que agregues la key en .env."
            )
        self.client = Anthropic(api_key=self.api_key) if self.api_key else None

    # ---------- Negocios (tenants) ----------

    def obtener_negocio(self, db, negocio_id: str = None, slug: str = None) -> Negocio:
        q = db.query(Negocio)
        if negocio_id:
            return q.filter(Negocio.id == negocio_id).first()
        if slug:
            return q.filter(Negocio.slug == slug).first()
        return None

    # ---------- Idioma ----------

    def detectar_idioma(self, texto: str, idiomas_candidatos: list) -> str:
        """Detecta el idioma del mensaje, restringido a los idiomas que el
        negocio ofrece (por decisión ya tomada: lanzamos con español +
        inglés, no los 12 del MVP anterior — pero el mecanismo soporta
        cualquier subconjunto de esos 12 si un negocio lo necesita)."""
        if not self.client or len(idiomas_candidatos) <= 1:
            return idiomas_candidatos[0] if idiomas_candidatos else "es"
        try:
            opciones = ", ".join(idiomas_candidatos)
            response = self.client.messages.create(
                model=MODELO_CLAUDE,
                max_tokens=10,
                messages=[{
                    "role": "user",
                    "content": f"¿Cuál de estos códigos de idioma corresponde a este texto: {opciones}? Responde solo con el código:\n\n{texto}"
                }]
            )
            codigo = response.content[0].text.strip().lower()
            return codigo if codigo in idiomas_candidatos else idiomas_candidatos[0]
        except Exception as e:
            print(f"Error detectando idioma: {e}")
            return idiomas_candidatos[0]

    # ---------- Llamadas ----------

    def iniciar_llamada(self, negocio_id: str, numero_cliente: str = None) -> dict:
        llamada_id = str(uuid.uuid4())[:8]
        db = SessionLocal()
        try:
            negocio = self.obtener_negocio(db, negocio_id=negocio_id)
            if not negocio:
                return {"error": "Negocio no encontrado"}
            if not negocio.activo:
                return {"error": "Este negocio no tiene el servicio activo"}

            # Si viene número de teléfono, intentamos identificar de una
            # vez a qué cliente-del-negocio corresponde (opción "por
            # número entrante" que se había planteado como una de las dos
            # formas de identificar a quién llama).
            cliente_negocio_id = None
            if numero_cliente:
                cliente = (
                    db.query(ClienteNegocio)
                    .filter(ClienteNegocio.negocio_id == negocio_id, ClienteNegocio.telefono == numero_cliente)
                    .first()
                )
                if cliente:
                    cliente_negocio_id = cliente.id

            llamada = Llamada(
                id=llamada_id,
                negocio_id=negocio_id,
                cliente_negocio_id=cliente_negocio_id,
                numero_cliente=numero_cliente,
                fecha_inicio=datetime.utcnow(),
            )
            db.add(llamada)
            db.commit()
            return {"llamada_id": llamada_id, "cliente_identificado": cliente_negocio_id is not None}
        finally:
            db.close()

    def _reconstruir_historial(self, db, llamada_id: str) -> list:
        """Reconstruye el historial de ESTA llamada desde la BD — igual
        principio que el MVP anterior (nunca guardar el historial como
        estado compartido en memoria del servicio)."""
        mensajes = (
            db.query(Conversacion)
            .filter(Conversacion.llamada_id == llamada_id, Conversacion.rol.in_(["usuario", "recepcionista"]))
            .order_by(Conversacion.timestamp.asc())
            .all()
        )
        historial = []
        for m in mensajes:
            role = "user" if m.rol == "usuario" else "assistant"
            historial.append({"role": role, "content": m.mensaje})
        return historial

    def _generar_prompt_sistema(self, negocio: Negocio, vertical_config: dict, idioma: str) -> str:
        nombre_idioma = NOMBRES_IDIOMA.get(idioma, NOMBRES_IDIOMA["es"])
        return f"""Eres {negocio.nombre_asistente}, una asistente virtual con IA, amable, profesional y eficiente, parte de NEXXUS AI Support. Atendés a {negocio.nombre}.

{vertical_config.get("instrucciones_extra", "")}

Reglas generales:
- Respondé SIEMPRE en {nombre_idioma}, sin importar en qué idioma esté este prompt.
- Nunca inventes datos de pagos, membresías ni cuentas — si la pregunta requiere un dato real, usá la herramienta correspondiente antes de responder.
- Respondé de forma concisa (máximo 2-3 oraciones), cálida pero profesional.
- Si no entendés, pedí que repitan.
- Después de 20 minutos de conversación, ofrecé transferir con un supervisor humano.

Idioma de respuesta obligatorio: {nombre_idioma}"""

    def _guardar_mensaje(self, db, llamada_id, rol, mensaje, es_fallback=False):
        c = Conversacion(id=str(uuid.uuid4())[:8], llamada_id=llamada_id, rol=rol, mensaje=mensaje, es_fallback=es_fallback)
        db.add(c)
        return c

    def procesar_mensaje(self, llamada_id: str, mensaje_usuario: str) -> dict:
        if not mensaje_usuario or not mensaje_usuario.strip():
            return {"error": "Mensaje vacío"}
        if len(mensaje_usuario) > MAX_CARACTERES_MENSAJE:
            return {"error": f"Mensaje demasiado largo (máximo {MAX_CARACTERES_MENSAJE} caracteres)"}

        db = SessionLocal()
        try:
            llamada = db.query(Llamada).filter(Llamada.id == llamada_id).first()
            if not llamada:
                return {"error": "Llamada no encontrada"}

            negocio = self.obtener_negocio(db, negocio_id=llamada.negocio_id)
            if not negocio:
                return {"error": "Negocio no encontrado"}

            vertical_mod = obtener_vertical(negocio.vertical)
            vertical_config = vertical_mod.CONFIG
            tools = vertical_mod.TOOLS

            idiomas_candidatos = [negocio.idioma_principal, negocio.idioma_secundario]
            idioma = self.detectar_idioma(mensaje_usuario, idiomas_candidatos)
            llamada.idioma_detectado = idioma

            historial_previo = self._reconstruir_historial(db, llamada_id)
            self._guardar_mensaje(db, llamada_id, "usuario", mensaje_usuario)
            db.commit()

            mensajes = historial_previo + [{"role": "user", "content": mensaje_usuario}]

            if not self.client:
                respuesta_final = FALLBACKS.get(idioma, FALLBACKS["es"])
                fallback_usado = True
            else:
                try:
                    prompt_sistema = self._generar_prompt_sistema(negocio, vertical_config, idioma)
                    respuesta_final, fallback_usado = self._loop_tool_use(
                        db, negocio, vertical_mod, llamada_id, prompt_sistema, tools, mensajes
                    )
                except Exception as e:
                    respuesta_final = FALLBACKS.get(idioma, FALLBACKS["es"])
                    fallback_usado = True
                    print(f"Error en Claude API: {e}. Usando fallback.")

            self._guardar_mensaje(db, llamada_id, "recepcionista", respuesta_final, es_fallback=fallback_usado)
            if fallback_usado:
                llamada.fallback_usado = True

            duracion_actual = (datetime.utcnow() - llamada.fecha_inicio).total_seconds()
            requiere_supervisor = duracion_actual >= UMBRAL_ESCALADO_SEGUNDOS
            if requiere_supervisor and llamada.resultado == "en_progreso":
                llamada.resultado = "requiere_supervisor"

            db.commit()

            return {
                "respuesta": respuesta_final,
                "idioma": idioma,
                "fallback": fallback_usado,
                "requiere_supervisor": requiere_supervisor,
                "duracion_segundos": round(duracion_actual, 1),
            }
        finally:
            db.close()

    def _loop_tool_use(self, db, negocio, vertical_mod, llamada_id, prompt_sistema, tools, mensajes):
        """Ciclo estándar de tool-use de Claude: si el modelo pide usar una
        herramienta, la ejecutamos contra la BD real y le devolvemos el
        resultado, hasta que responda con texto final. Tope de
        MAX_ITERACIONES_TOOL_USE para evitar loops infinitos."""
        mensajes_actuales = list(mensajes)

        for _ in range(MAX_ITERACIONES_TOOL_USE):
            response = self.client.messages.create(
                model=MODELO_CLAUDE,
                max_tokens=800,
                system=prompt_sistema,
                tools=tools,
                messages=mensajes_actuales,
            )

            if response.stop_reason != "tool_use":
                texto = "".join(b.text for b in response.content if b.type == "text")
                return texto, False

            mensajes_actuales.append({"role": "assistant", "content": response.content})

            tool_results = []
            for bloque in response.content:
                if bloque.type != "tool_use":
                    continue
                resultado = vertical_mod.ejecutar_tool(db, negocio, bloque.name, bloque.input)
                # Registramos la llamada a herramienta para auditoría/panel admin.
                self._guardar_mensaje(
                    db, llamada_id, "herramienta",
                    json.dumps({"tool": bloque.name, "input": bloque.input, "resultado": resultado}, ensure_ascii=False, default=str),
                )
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": bloque.id,
                    "content": json.dumps(resultado, ensure_ascii=False, default=str),
                })

            mensajes_actuales.append({"role": "user", "content": tool_results})

        # Si se agotaron los intentos, devolvemos lo último en texto (si hay) o fallback.
        return "Disculpá, tuve un problema consultando la información. ¿Podés reformular tu pregunta?", True

    def finalizar_llamada(self, llamada_id: str, resultado: str = "completada") -> dict:
        db = SessionLocal()
        try:
            llamada = db.query(Llamada).filter(Llamada.id == llamada_id).first()
            if not llamada:
                return {"error": "Llamada no encontrada"}
            llamada.fecha_fin = datetime.utcnow()
            llamada.resultado = resultado
            llamada.duracion_segundos = (llamada.fecha_fin - llamada.fecha_inicio).total_seconds()
            db.commit()
            return {"llamada_id": llamada_id, "duracion": llamada.duracion_segundos, "resultado": resultado}
        finally:
            db.close()

    # ---------- Estadísticas ----------

    def obtener_estadisticas(self, negocio_id: str) -> dict:
        """Estadísticas de UN negocio — lo que ve el panel del gym."""
        db = SessionLocal()
        try:
            llamadas = db.query(Llamada).filter(Llamada.negocio_id == negocio_id).all()
            return self._resumir_llamadas(llamadas)
        finally:
            db.close()

    def obtener_estadisticas_globales(self) -> dict:
        """Estadísticas de TODOS los negocios — lo que ve Jorge como operador."""
        db = SessionLocal()
        try:
            negocios = db.query(Negocio).all()
            llamadas = db.query(Llamada).all()
            resumen = self._resumir_llamadas(llamadas)
            resumen["negocios_activos"] = len([n for n in negocios if n.activo])
            resumen["negocios_totales"] = len(negocios)
            return resumen
        finally:
            db.close()

    def _resumir_llamadas(self, llamadas) -> dict:
        total = len(llamadas)
        duraciones = [l.duracion_segundos for l in llamadas if l.duracion_segundos > 0]
        duracion_promedio = sum(duraciones) / len(duraciones) if duraciones else 0
        fallbacks_usados = len([l for l in llamadas if l.fallback_usado])
        tasa_fallback = (fallbacks_usados / total * 100) if total > 0 else 0
        escaladas = len([l for l in llamadas if l.resultado == "requiere_supervisor"])
        idiomas_conteo = {}
        for l in llamadas:
            idiomas_conteo[l.idioma_detectado] = idiomas_conteo.get(l.idioma_detectado, 0) + 1
        return {
            "total_llamadas": total,
            "duracion_promedio_segundos": round(duracion_promedio, 2),
            "tasa_fallback_porcentaje": round(tasa_fallback, 2),
            "idiomas": idiomas_conteo,
            "fallbacks_usados": fallbacks_usados,
            "escaladas_supervisor": escaladas,
        }
