import os
import json
from anthropic import Anthropic
from datetime import datetime
from dotenv import load_dotenv
from models import SessionLocal, Llamada, Conversacion
import uuid

# Carga .env (si existe) ANTES de leer ANTHROPIC_API_KEY más abajo.
load_dotenv()

# Modelo de Claude a usar. Configurable por variable de entorno para poder
# subir de categoría (ej. claude-sonnet-5) sin tocar código.
# IMPORTANTE: "claude-opus-4-1" NO es un modelo válido de la API actual —
# usarlo hacía que TODAS las llamadas fallaran y cayeran siempre al fallback.
MODELO_CLAUDE = os.getenv("MODELO_CLAUDE", "claude-haiku-4-5-20251001")

# Límite de caracteres por mensaje entrante, para evitar abuso/costos
# descontrolados de API por mensajes gigantes.
MAX_CARACTERES_MENSAJE = 2000

# Umbral de escalado a supervisor humano (en segundos). 20 minutos.
UMBRAL_ESCALADO_SEGUNDOS = 20 * 60

# Nombres de idioma en su propio idioma, usados para armar el prompt del
# sistema dinámicamente (antes solo existían prompts para es/en/fr; el resto
# de los 12 idiomas "soportados" caían al prompt en español, que fuerza
# "Idioma: Español" y sesgaba las respuestas al español sin importar el
# idioma real del cliente).
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

# Fallbacks en 12 idiomas
FALLBACKS = {
    "es": "Lo siento, en este momento no puedo procesar tu solicitud. Por favor, intenta de nuevo o espera al supervisor.",
    "en": "I'm sorry, I cannot process your request at this moment. Please try again or wait for the supervisor.",
    "fr": "Je suis désolé, je ne peux pas traiter votre demande en ce moment. Veuillez réessayer ou attendre le superviseur.",
    "de": "Es tut mir leid, ich kann Ihre Anfrage momentan nicht bearbeiten. Bitte versuchen Sie es später oder warten Sie auf den Supervisor.",
    "it": "Mi dispiace, non posso elaborare la tua richiesta in questo momento. Per favore riprova o attendi il supervisore.",
    "pt": "Desculpe, não consigo processar sua solicitação neste momento. Tente novamente ou aguarde o supervisor.",
    "ja": "申し訳ございません。現在、リクエストを処理できません。もう一度お試しいただくか、スーパーバイザーをお待ちください。",
    "zh": "抱歉，我目前无法处理您的请求。请重试或等待主管。",
    "ru": "Извините, я не могу обработать ваш запрос в данный момент. Пожалуйста, повторите попытку или дождитесь супервайзера.",
    "ar": "أعتذر، لا يمكنني معالجة طلبك في الوقت الحالي. يرجى المحاولة مرة أخرى أو انتظار المشرف.",
    "hi": "क्षमा करें, मैं इस समय आपके अनुरोध को संसाधित नहीं कर सकता। कृपया पुनः प्रयास करें या पर्यवेक्षक की प्रतीक्षा करें।",
    "ko": "죄송합니다. 현재 요청을 처리할 수 없습니다. 다시 시도하거나 감독자를 기다려주세요.",
}

IDIOMAS_DISPONIBLES = list(FALLBACKS.keys())


class RecepcionistaIAService:
    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        if not self.api_key:
            print(
                "⚠️  ANTHROPIC_API_KEY no configurada. Todas las respuestas usarán "
                "el fallback pre-grabado hasta que agregues la key en .env."
            )
        self.client = Anthropic(api_key=self.api_key) if self.api_key else None

        # NOTA IMPORTANTE (fix de concurrencia):
        # Antes, el historial de conversación se guardaba como estado del
        # propio servicio (self.conversacion_historia / self.llamada_actual),
        # compartido por TODAS las llamadas activas. Con dos clientes
        # chateando al mismo tiempo, sus mensajes se mezclaban, y cada nueva
        # llamada borraba el historial de cualquier otra en curso.
        # Ahora el historial se reconstruye desde la base de datos por
        # llamada_id en cada mensaje (ver _reconstruir_historial), así que
        # el servicio ya no guarda estado de conversación entre llamadas.

    def detectar_idioma(self, texto: str) -> str:
        """Detecta idioma usando Claude"""
        if not self.client:
            return "es"
        try:
            response = self.client.messages.create(
                model=MODELO_CLAUDE,
                max_tokens=10,
                messages=[{
                    "role": "user",
                    "content": f"Identifica el idioma de este texto en un código ISO-639-1 (es, en, fr, de, it, pt, ja, zh, ru, ar, hi, ko). Solo responde con el código:\n\n{texto}"
                }]
            )
            codigo = response.content[0].text.strip().lower()
            return codigo if codigo in IDIOMAS_DISPONIBLES else "es"
        except Exception as e:
            print(f"Error detectando idioma: {e}")
            return "es"

    def iniciar_llamada(self, numero_cliente: str = None) -> str:
        """Inicia una nueva llamada"""
        llamada_id = str(uuid.uuid4())[:8]

        db = SessionLocal()
        try:
            llamada = Llamada(
                id=llamada_id,
                numero_cliente=numero_cliente,
                fecha_inicio=datetime.utcnow()
            )
            db.add(llamada)
            db.commit()
        finally:
            db.close()

        return llamada_id

    def _reconstruir_historial(self, db, llamada_id: str) -> list:
        """Reconstruye el historial de mensajes de ESTA llamada desde la BD,
        en el formato que espera la API de Claude. Reemplaza el viejo estado
        compartido en memoria."""
        mensajes = (
            db.query(Conversacion)
            .filter(Conversacion.llamada_id == llamada_id)
            .order_by(Conversacion.timestamp.asc())
            .all()
        )
        historial = []
        for m in mensajes:
            role = "user" if m.rol == "usuario" else "assistant"
            historial.append({"role": role, "content": m.mensaje})
        return historial

    def procesar_mensaje(self, llamada_id: str, mensaje_usuario: str) -> dict:
        """Procesa mensaje del usuario y genera respuesta de recepcionista"""

        if not mensaje_usuario or not mensaje_usuario.strip():
            return {"error": "Mensaje vacío"}

        if len(mensaje_usuario) > MAX_CARACTERES_MENSAJE:
            return {"error": f"Mensaje demasiado largo (máximo {MAX_CARACTERES_MENSAJE} caracteres)"}

        db = SessionLocal()
        try:
            llamada = db.query(Llamada).filter(Llamada.id == llamada_id).first()

            if not llamada:
                return {"error": "Llamada no encontrada"}

            # Detectar idioma
            idioma = self.detectar_idioma(mensaje_usuario)
            llamada.idioma_detectado = idioma

            # Historial ANTES de agregar el mensaje nuevo (para el contexto de Claude)
            historial_previo = self._reconstruir_historial(db, llamada_id)

            # Guardar mensaje del usuario
            msg_usuario_id = str(uuid.uuid4())[:8]
            conversacion_usuario = Conversacion(
                id=msg_usuario_id,
                llamada_id=llamada_id,
                rol="usuario",
                mensaje=mensaje_usuario
            )
            db.add(conversacion_usuario)
            db.commit()

            mensajes_para_claude = historial_previo + [{"role": "user", "content": mensaje_usuario}]

            # Generar respuesta con Claude
            if not self.client:
                respuesta = FALLBACKS.get(idioma, FALLBACKS["es"])
                fallback_usado = True
            else:
                try:
                    prompt_sistema = self._generar_prompt_sistema(idioma)

                    response = self.client.messages.create(
                        model=MODELO_CLAUDE,
                        max_tokens=500,
                        system=prompt_sistema,
                        messages=mensajes_para_claude
                    )

                    respuesta = response.content[0].text
                    fallback_usado = False

                except Exception as e:
                    respuesta = FALLBACKS.get(idioma, FALLBACKS["es"])
                    fallback_usado = True
                    print(f"Error en Claude API: {e}. Usando fallback.")

            # Guardar respuesta
            msg_recepcionista_id = str(uuid.uuid4())[:8]
            conversacion_recepcionista = Conversacion(
                id=msg_recepcionista_id,
                llamada_id=llamada_id,
                rol="recepcionista",
                mensaje=respuesta,
                es_fallback=fallback_usado
            )
            db.add(conversacion_recepcionista)

            if fallback_usado:
                llamada.fallback_usado = True

            # Chequeo real de duración -> señal de escalado a supervisor.
            # Antes esto era solo una instrucción "esperanzada" al LLM
            # ("después de 20 minutos, ofrece transferir"), sin ninguna
            # verificación real en el backend.
            duracion_actual = (datetime.utcnow() - llamada.fecha_inicio).total_seconds()
            requiere_supervisor = duracion_actual >= UMBRAL_ESCALADO_SEGUNDOS
            if requiere_supervisor and llamada.resultado == "en_progreso":
                llamada.resultado = "requiere_supervisor"

            db.commit()

            return {
                "respuesta": respuesta,
                "idioma": idioma,
                "fallback": fallback_usado,
                "requiere_supervisor": requiere_supervisor,
                "duracion_segundos": round(duracion_actual, 1),
            }
        finally:
            db.close()

    def finalizar_llamada(self, llamada_id: str, resultado: str = "completada") -> dict:
        """Finaliza una llamada"""
        db = SessionLocal()
        try:
            llamada = db.query(Llamada).filter(Llamada.id == llamada_id).first()

            if llamada:
                llamada.fecha_fin = datetime.utcnow()
                llamada.resultado = resultado
                duracion = (llamada.fecha_fin - llamada.fecha_inicio).total_seconds()
                llamada.duracion_segundos = duracion
                db.commit()

                return {"llamada_id": llamada_id, "duracion": duracion, "resultado": resultado}

            return {"error": "Llamada no encontrada"}
        finally:
            db.close()

    def _generar_prompt_sistema(self, idioma: str) -> str:
        """Genera el prompt del sistema para CUALQUIERA de los 12 idiomas
        soportados (antes solo existían prompts hardcodeados para es/en/fr;
        el resto caía al prompt en español, que decía explícitamente
        "Idioma: Español" y sesgaba las respuestas a ese idioma sin importar
        con quién se estuviera hablando)."""
        nombre_idioma = NOMBRES_IDIOMA.get(idioma, NOMBRES_IDIOMA["es"])

        return f"""Eres María, una recepcionista virtual con IA, amable, profesional y eficiente. Eres parte de NEXXUS AI SUPPORT.

Instrucciones:
- Responde SIEMPRE en {nombre_idioma}, sin importar en qué idioma esté escrito este prompt.
- Saluda calurosamente al usuario.
- Escucha con empatía.
- Responde de forma concisa (máximo 2-3 oraciones).
- Si no entiendes, pide que repita.
- Después de 20 minutos de conversación, ofrece transferir la llamada con un supervisor humano.
- Sé cálida pero profesional.

Idioma de respuesta obligatorio: {nombre_idioma}"""

    def obtener_estadisticas(self) -> dict:
        """Obtiene estadísticas de todas las llamadas"""
        db = SessionLocal()
        try:
            todas_llamadas = db.query(Llamada).all()
            total_llamadas = len(todas_llamadas)

            duraciones = [l.duracion_segundos for l in todas_llamadas if l.duracion_segundos > 0]
            duracion_promedio = sum(duraciones) / len(duraciones) if duraciones else 0

            fallbacks_usados = len([l for l in todas_llamadas if l.fallback_usado])
            tasa_fallback = (fallbacks_usados / total_llamadas * 100) if total_llamadas > 0 else 0

            escaladas_supervisor = len([l for l in todas_llamadas if l.resultado == "requiere_supervisor"])

            idiomas_conteo = {}
            for llamada in todas_llamadas:
                idioma = llamada.idioma_detectado
                idiomas_conteo[idioma] = idiomas_conteo.get(idioma, 0) + 1

            return {
                "total_llamadas": total_llamadas,
                "duracion_promedio_segundos": round(duracion_promedio, 2),
                "tasa_fallback_porcentaje": round(tasa_fallback, 2),
                "idiomas": idiomas_conteo,
                "fallbacks_usados": fallbacks_usados,
                "escaladas_supervisor": escaladas_supervisor,
            }
        finally:
            db.close()
