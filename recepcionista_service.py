import json
import os
import re
import logging
import threading
from datetime import datetime
from typing import Optional, Dict, Any
from anthropic import Anthropic

logger = logging.getLogger(__name__)

# Etiqueta oculta que Claudia añade cuando el cliente deja sus datos.
# Se quita del texto antes de leerlo en voz alta y se registra como lead.
PATRON_CONTACTO = re.compile(r"\[CONTACTO:(.*?)\]", re.IGNORECASE | re.DOTALL)
# Etiqueta que Claudia añade cuando la conversación terminó: se cuelga tras despedirse.
PATRON_FIN = re.compile(r"\[FIN\]", re.IGNORECASE)
# Despedidas del cliente (respaldo por si el modelo no pone [FIN])
PATRON_DESPEDIDA = re.compile(
    r"^\W*(ok(ay)?|vale|bueno|listo|perfecto|gracias|muchas gracias|eso es todo|nada m[aá]s|no,? gracias|"
    r"ad[ií][oó]s|chao|chau|bye|good ?bye|hasta luego|hasta pronto|nos vemos|thank you|thanks|that'?s all)"
    r"([\s,.!¡]+(ad[ií][oó]s|chao|chau|bye|gracias|muchas gracias|hasta luego|hasta pronto|nos vemos|eso es todo|nada m[aá]s|thank you|thanks))*\W*$",
    re.IGNORECASE)
PALABRAS_ADIOS = re.compile(r"\b(ad[ií][oó]s|chao|chau|bye|goodbye|hasta luego|hasta pronto|nos vemos)\b", re.IGNORECASE)

class RecepcionistaIAService:
    """Servicio de recepcionista IA para Golden Age Fitness"""
    
    UMBRAL_ESCALADO_SEGUNDOS = 10 * 60  # 10 minutos
    
    def __init__(self):
        try:
            self.client = Anthropic()
            print("✓ Anthropic client inicializado correctamente")
        except Exception as e:
            import traceback
            print(f"✗ ERROR inicializando Anthropic: {str(e)}")
            print(f"TRACEBACK: {traceback.format_exc()}")
            raise
        self.sesiones = {}
        # Hook opcional: lo asigna main.py para registrar leads en Supabase.
        # Firma: on_lead(sesion_id, lead_dict, numero_negocio). Nunca debe lanzar.
        self.on_lead = None
        self.cargar_config()
    
    @staticmethod
    def modelo(variable: str = "ANTHROPIC_MODEL") -> str:
        """Modelo LLM configurable por entorno. No se acopla a un ID concreto:
        ANTHROPIC_MODEL (o MODELO_CLAUDE, nombre heredado). El valor por
        defecto solo se usa si ninguna variable está definida."""
        return (os.getenv(variable) or os.getenv("ANTHROPIC_MODEL")
                or os.getenv("MODELO_CLAUDE") or "claude-haiku-4-5-20251001")

    def cargar_config(self):
        """Carga la configuración del negocio desde JSON"""
        try:
            # Intenta cargar desde la raíz del proyecto
            with open('golden_age_config.json', 'r', encoding='utf-8') as f:
                self.config = json.load(f)
        except FileNotFoundError:
            # Fallback: usar configuración por defecto
            self.config = self._config_default()
    
    def _config_default(self) -> Dict:
        """Configuración por defecto si el archivo no existe"""
        return {
            "negocio": {
                "nombre": "Golden Age Fitness & Training",
                "direccion": "1914 Gessner Rd, Houston, TX",
                "telefono": "281-352-4784",
                "website": "https://goldenagefitness.com"
            },
            "horarios": {
                "lunes_viernes": "7:00 AM - 9:00 PM",
                "sabado": "8:00 AM - 2:00 PM",
                "domingo": "Cerrado"
            },
            "servicios": [
                "Pesas y levantamiento",
                "Bicicletas estacionarias",
                "Caminadoras",
                "Entrenamiento personalizado",
                "Masajes terapéuticos",
                "Terapia de luz roja",
                "Magnetoterapia",
                "Asesoría nutricional",
                "Seguimiento de progreso",
                "Clases grupales"
            ],
            "precios": {
                "sesion_individual": "$60 por sesión",
                "entrenamiento_personal": "$280/mes (3 entrenamientos por semana)"
            },
            "contactos": {
                "propietario": {
                    "nombre": "Roberto Gracian",
                    "telefono": "832-388-4711"
                }
            }
        }
    
    def _generar_prompt_sistema(self, telefono_llamante: Optional[str] = None) -> str:
        """Genera el prompt del sistema con conocimiento del negocio"""
        if telefono_llamante:
            linea_telefono = (f"El cliente llama desde el número {telefono_llamante}. "
                              "Pregúntale si podemos contactarle a ese mismo número antes de pedirle otro.")
        else:
            linea_telefono = "Pídele un número de teléfono o un correo electrónico."
        
        config = self.config
        negocio = config.get('negocio', {})
        horarios = config.get('horarios', {})
        servicios = config.get('servicios', [])
        precios = config.get('precios', {})
        contactos = config.get('contactos', {})
        filosofia = config.get('filosofia_claudia', {})
        
        servicios_txt = '\n'.join([f"- {s}" for s in servicios])
        
        prompt = f"""Eres Claudia, la entrenadora de Golden Age Fitness & Training.

INFORMACIÓN DEL NEGOCIO:
Nombre: {negocio.get('nombre', 'Golden Age Fitness & Training')}
Dirección: {negocio.get('direccion', '1914 Gessner Rd, Houston, TX')}
Teléfono: {negocio.get('telefono', '281-352-4784')}
Website: {negocio.get('website', '')}

HORARIOS:
- Lunes a Viernes: {horarios.get('lunes_viernes', '7:00 AM - 9:00 PM')}
- Sábado: {horarios.get('sabado', '8:00 AM - 2:00 PM')}
- Domingo: {horarios.get('domingo', 'Cerrado')}

SERVICIOS QUE OFRECEMOS:
{servicios_txt}

PRECIOS:
- Sesión Individual: {precios.get('sesion_individual', '$60 por sesión')}
- Entrenamiento Personal: {precios.get('entrenamiento_personal', '$280/mes (3 entrenamientos por semana)')}

PROPIETARIO:
Nombre: {contactos.get('propietario', {}).get('nombre', 'Roberto Gracian')}
Teléfono para Supervisor: {contactos.get('propietario', {}).get('telefono', '832-388-4711')}

INSTRUCCIONES IMPORTANTES:
1. Eres entrenadora de Golden Age, no recepcionista. Habla con calidez y profesionalismo.
2. Puedes responder cualquier pregunta sobre: horarios, servicios, precios, ubicación, personal, operaciones.
3. Mantén las respuestas concisas y útiles.
4. NUNCA digas "no puedo procesar" - siempre intenta ayudar primero.
5. Si el cliente pide explícitamente hablar con el propietario, transferir a Roberto Gracian al {contactos.get('propietario', {}).get('telefono', '832-388-4711')}.
6. Si hay quejas graves, pagos, decisiones importantes o información que no tienes, transfiere a Roberto.
7. Habla tanto en inglés como en español, según lo que pida el cliente.
8. Sé inspiradora y profesional. Golden Age es un lugar especial para personas que quieren mejorar su salud.

LÍMITES:
- Máximo 10 minutos de llamada. Si se acerca ese tiempo, ofrece transferir con Roberto si es necesario.
- Si no sabes algo, ofrece transferir a Roberto o dejar un mensaje.

ESTO ES UNA LLAMADA TELEFÓNICA (muy importante):
- Responde en 1 o 2 frases cortas, como hablaría una persona por teléfono. Nada de listas, viñetas, asteriscos, emojis ni formato.
- Si la respuesta tiene varios datos, da lo más importante y pregunta si quiere más detalle.
- Escribe los números como se dicen en voz alta (por ejemplo "sesenta dólares", "de siete de la mañana a nueve de la noche").
- Termina normalmente con una pregunta breve para seguir la conversación.

DEJAR DATOS DE CONTACTO:
- Si el cliente quiere más información, quiere inscribirse, pide algo que no puedes resolver, o Roberto no está disponible, ofrécele dejar su nombre y su teléfono o correo para que el equipo de Golden Age Gym le responda a la brevedad.
- {linea_telefono}
- Pide los datos de uno en uno, repítelos para confirmar (el correo deletreado si hace falta) y agradece: "Perfecto, el equipo de Golden Age Gym te contactará a la brevedad."
- Cuando el cliente haya CONFIRMADO sus datos, añade al final de tu respuesta, en una línea aparte, exactamente: [CONTACTO: nombre=...; telefono=...; email=...; motivo=...] (deja vacío lo que no tengas). Esta etiqueta no se lee en voz alta; ponla solo una vez por cliente.

TERMINAR LA LLAMADA:
- Si el cliente se despide ("chao", "adiós", "gracias, eso es todo", "bye") o dice que no necesita nada más, despídete en UNA frase corta y cálida, sin hacer más preguntas (por ejemplo: "¡Gracias por llamar a Golden Age Gym! Que tengas un excelente día.") y añade al final, en una línea aparte, exactamente: [FIN]
- No pongas [FIN] si el cliente todavía tiene una pregunta pendiente.
"""
        return prompt
    
    def iniciar_sesion(self, sesion_id: str) -> Dict[str, Any]:
        """Inicia una nueva sesión"""
        self.sesiones[sesion_id] = {
            'inicio': datetime.now(),
            'historial': [],
            'estado': 'activa'
        }
        return {
            'sesion_id': sesion_id,
            'estado': 'inicializada',
            'mensaje': 'Sesión iniciada con Claudia'
        }
    
    def procesar_mensaje(self, sesion_id: str, mensaje_usuario: str,
                         telefono_llamante: Optional[str] = None,
                         numero_negocio: Optional[str] = None) -> str:
        """Procesa un mensaje del usuario y retorna la respuesta de Claudia"""
        
        if sesion_id not in self.sesiones:
            self.iniciar_sesion(sesion_id)
        
        sesion = self.sesiones[sesion_id]
        tiempo_transcurrido = (datetime.now() - sesion['inicio']).total_seconds()
        
        # Verificar si pasó el tiempo límite
        if tiempo_transcurrido > self.UMBRAL_ESCALADO_SEGUNDOS:
            return f"Ha pasado el tiempo máximo de esta llamada. Por favor, contacta a Roberto Gracian al 832-388-4711 para continuar. ¡Gracias!"
        
        # Agregar mensaje al historial
        sesion['historial'].append({
            'role': 'user',
            'content': mensaje_usuario
        })
        
        try:
            # Llamar a Claude API con el prompt del sistema
            respuesta = self.client.messages.create(
                model=self.modelo(),
                max_tokens=200,  # respuestas cortas = se generan y se leen más rápido
                system=self._generar_prompt_sistema(telefono_llamante),
                messages=sesion['historial']
            )
            
            respuesta_texto = respuesta.content[0].text
            
            # Agregar respuesta al historial (con la etiqueta, para que Claude sepa que ya guardó el contacto)
            sesion['historial'].append({
                'role': 'assistant',
                'content': respuesta_texto
            })
            
            if PATRON_FIN.search(respuesta_texto) or self._es_despedida(mensaje_usuario):
                sesion['finalizar'] = True
            limpio = self._extraer_contacto(sesion_id, respuesta_texto, telefono_llamante, numero_negocio)
            return PATRON_FIN.sub("", limpio).strip()
        
        except Exception as e:
            # Log the actual error for debugging
            import traceback
            print(f"ERROR EN PROCESAR_MENSAJE: {str(e)}")
            print(f"TRACEBACK: {traceback.format_exc()}")
            # Fallback si hay error con la API
            return f"Disculpa, tengo un problema técnico. Por favor, llama directamente al 281-352-4784 o habla con Roberto Gracian al 832-388-4711. ¡Gracias!"
    
    def _extraer_contacto(self, sesion_id: str, texto: str,
                          telefono_llamante: Optional[str],
                          numero_negocio: Optional[str] = None) -> str:
        """Registra los datos de contacto que dejó el cliente y devuelve el texto limpio para leer en voz alta."""
        for match in PATRON_CONTACTO.finditer(texto):
            lead = {"sesion_id": sesion_id, "telefono_llamante": telefono_llamante,
                    "fecha": datetime.now().isoformat(timespec="seconds")}
            for parte in match.group(1).split(";"):
                if "=" in parte:
                    clave, valor = parte.split("=", 1)
                    lead[clave.strip().lower()] = valor.strip()
            self.sesiones.get(sesion_id, {}).setdefault('contactos', []).append(lead)
            logger.warning(f"NUEVO_CONTACTO {json.dumps(lead, ensure_ascii=False)}")
            print(f"NUEVO_CONTACTO {json.dumps(lead, ensure_ascii=False)}", flush=True)
            # El SMS se envía en segundo plano para no retrasar la respuesta de voz
            threading.Thread(target=self._avisar_por_sms, args=(lead, numero_negocio), daemon=True).start()
            if self.on_lead:
                try:
                    self.on_lead(sesion_id, lead, numero_negocio)
                except Exception as e:  # el registro nunca afecta a la llamada
                    logger.error(f"on_lead falló: {e}")
        limpio = PATRON_CONTACTO.sub("", texto)
        limpio = limpio.replace("*", "").replace("#", "")
        return re.sub(r"\s+", " ", limpio).strip()

    @staticmethod
    def _a_e164(numero: str) -> str:
        digitos = re.sub(r"\D", "", numero or "")
        if len(digitos) == 10:
            digitos = "1" + digitos
        return f"+{digitos}" if digitos else ""

    def _avisar_por_sms(self, lead: Dict[str, Any], numero_negocio: Optional[str]) -> None:
        """Envía un SMS al dueño con los datos del contacto para que devuelva la llamada."""
        try:
            sid = os.getenv("TWILIO_ACCOUNT_SID")
            token = os.getenv("TWILIO_AUTH_TOKEN")
            destino = self._a_e164(os.getenv("LEADS_SMS_TO") or
                                   self.config.get("contactos", {}).get("propietario", {}).get("telefono", ""))
            origen = self._a_e164(os.getenv("LEADS_SMS_FROM") or numero_negocio or "")
            if not (sid and token and destino and origen):
                logger.error("SMS de contacto NO enviado: faltan TWILIO_ACCOUNT_SID/TOKEN, destino u origen")
                return
            telefono = lead.get("telefono") or lead.get("telefono_llamante") or "-"
            partes = [
                "Golden Age Gym - Nuevo contacto (Claudia)",
                f"Nombre: {lead.get('nombre') or '-'}",
                f"Tel: {telefono}",
            ]
            if lead.get("email"):
                partes.append(f"Email: {lead['email']}")
            if lead.get("motivo"):
                partes.append(f"Motivo: {lead['motivo']}")
            partes.append("Por favor devolver la llamada.")
            from twilio.rest import Client
            msg = Client(sid, token).messages.create(to=destino, from_=origen, body="\n".join(partes))
            logger.warning(f"SMS de contacto enviado a {destino} (sid={msg.sid}, estado={msg.status})")
            print(f"SMS_CONTACTO_OK sid={msg.sid} estado={msg.status}", flush=True)
        except Exception as e:
            logger.error(f"Error enviando SMS de contacto: {e}")
            print(f"SMS_CONTACTO_ERROR {e}", flush=True)

    def _enviar_sms(self, destino: str, origen: str, cuerpo: str, etiqueta: str) -> bool:
        """Envía un SMS con Twilio. Devuelve True si Twilio lo aceptó."""
        try:
            sid = os.getenv("TWILIO_ACCOUNT_SID")
            token = os.getenv("TWILIO_AUTH_TOKEN")
            destino, origen = self._a_e164(destino), self._a_e164(origen)
            if not (sid and token and destino and origen):
                logger.error(f"{etiqueta}: SMS no enviado, faltan credenciales, destino u origen")
                return False
            from twilio.rest import Client
            msg = Client(sid, token).messages.create(to=destino, from_=origen, body=cuerpo)
            print(f"{etiqueta}_OK to={destino} sid={msg.sid} estado={msg.status}", flush=True)
            return True
        except Exception as e:
            print(f"{etiqueta}_ERROR {e}", flush=True)
            return False

    def enviar_sms_despedida(self, telefono_llamante: str, numero_negocio: str) -> bool:
        """SMS de agradecimiento al cliente cuando termina la llamada."""
        if os.getenv("SMS_DESPEDIDA_ACTIVO", "true").lower() in ("false", "0", "no"):
            return False
        destino = self._a_e164(telefono_llamante)
        # Solo números de EE. UU./Canadá válidos (evita ocultos, "anonymous", internacionales)
        if not re.fullmatch(r"\+1\d{10}", destino or ""):
            print(f"SMS_DESPEDIDA_OMITIDO numero={telefono_llamante!r}", flush=True)
            return False
        negocio = self.config.get("negocio", {})
        lineas = [
            "¡Gracias por llamar a Golden Age Gym! Fue un gusto atenderte.",
            f"Dirección: {negocio.get('direccion', '1914 Gessner Rd, Houston, TX')}",
            f"Teléfono: {negocio.get('telefono', '281-352-4784')}",
        ]
        if negocio.get("website"):
            lineas.append(f"Web: {negocio['website']}")
        lineas.append("¡Te esperamos!")
        return self._enviar_sms(destino, os.getenv("LEADS_SMS_FROM") or numero_negocio,
                                "\n".join(lineas), "SMS_DESPEDIDA")

    @staticmethod
    def _es_despedida(texto: str) -> bool:
        """True si lo que dijo el cliente es claramente una despedida."""
        t = (texto or "").strip()
        if not t:
            return False
        if PATRON_DESPEDIDA.match(t) and PALABRAS_ADIOS.search(t):
            return True
        # frases cortas que terminan en adiós/chao ("ok, gracias, chao")
        return len(t.split()) <= 6 and bool(PALABRAS_ADIOS.search(t)) and "?" not in t

    def debe_colgar(self, sesion_id: str) -> bool:
        """Claudia se despidió: el siguiente TwiML debe colgar."""
        return bool((self.sesiones.get(sesion_id) or {}).get('finalizar'))

    def historial_de(self, sesion_id: str) -> list:
        """Copia del historial de la sesión (para el resumen de fin de llamada)."""
        sesion = self.sesiones.get(sesion_id) or {}
        return [dict(m) for m in sesion.get('historial', [])]

    def generar_resumen(self, historial: list) -> Optional[Dict[str, str]]:
        """Resumen breve de la llamada para el Manager Panel.
        Devuelve {summary, intent, outcome} o None si no hubo conversación.
        No guarda ni devuelve el transcript. Modelo: SUMMARY_MODEL o ANTHROPIC_MODEL."""
        if not historial:
            return None
        lineas = []
        for m in historial[-30:]:
            quien = "Cliente" if m.get("role") == "user" else "Claudia"
            texto = PATRON_CONTACTO.sub("", str(m.get("content", ""))).strip()
            if texto:
                lineas.append(f"{quien}: {texto}")
        if not lineas:
            return None
        instrucciones = (
            "Resume esta llamada telefónica a un gimnasio para el panel del gerente. "
            "Responde SOLO con JSON válido, sin texto extra, con estas claves:\n"
            '"summary": 1-2 frases en español con lo que pidió el cliente y lo que se resolvió '
            "(sin inventar nada que no esté en la conversación);\n"
            '"intent": una de [information, pricing, schedule, membership, appointment, complaint, payment, other];\n'
            '"outcome": una de [information_provided, lead_captured, appointment_booked, transfer_requested, unresolved, caller_hung_up].'
        )
        try:
            r = self.client.messages.create(
                model=self.modelo("SUMMARY_MODEL"),
                max_tokens=250,
                system=instrucciones,
                messages=[{"role": "user", "content": "\n".join(lineas)}],
            )
            texto = r.content[0].text.strip()
            inicio, fin = texto.find("{"), texto.rfind("}")
            datos = json.loads(texto[inicio:fin + 1]) if inicio >= 0 and fin > inicio else {}
            return {k: str(datos[k]) for k in ("summary", "intent", "outcome") if datos.get(k)}
        except Exception as e:
            logger.error(f"generar_resumen falló: {e}")
            return None

    def finalizar_sesion(self, sesion_id: str) -> Dict[str, Any]:
        """Finaliza una sesión"""
        if sesion_id in self.sesiones:
            sesion = self.sesiones[sesion_id]
            duracion = (datetime.now() - sesion['inicio']).total_seconds() / 60
            
            del self.sesiones[sesion_id]
            
            return {
                'sesion_id': sesion_id,
                'estado': 'finalizada',
                'duracion_minutos': round(duracion, 2)
            }
        
        return {
            'sesion_id': sesion_id,
            'estado': 'sesion_no_encontrada'
        }
