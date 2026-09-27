import json
import os
import re
import logging
from datetime import datetime
from typing import Optional, Dict, Any
from anthropic import Anthropic

logger = logging.getLogger(__name__)

# Etiqueta oculta que Claudia añade cuando el cliente deja sus datos.
# Se quita del texto antes de leerlo en voz alta y se registra como lead.
PATRON_CONTACTO = re.compile(r"\[CONTACTO:(.*?)\]", re.IGNORECASE | re.DOTALL)

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
        self.cargar_config()
    
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
                    "telefono": "832-245-4634"
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
Teléfono para Supervisor: {contactos.get('propietario', {}).get('telefono', '832-245-4634')}

INSTRUCCIONES IMPORTANTES:
1. Eres entrenadora de Golden Age, no recepcionista. Habla con calidez y profesionalismo.
2. Puedes responder cualquier pregunta sobre: horarios, servicios, precios, ubicación, personal, operaciones.
3. Mantén las respuestas concisas y útiles.
4. NUNCA digas "no puedo procesar" - siempre intenta ayudar primero.
5. Si el cliente pide explícitamente hablar con el propietario, transferir a Roberto Gracian al {contactos.get('propietario', {}).get('telefono', '832-245-4634')}.
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
                         telefono_llamante: Optional[str] = None) -> str:
        """Procesa un mensaje del usuario y retorna la respuesta de Claudia"""
        
        if sesion_id not in self.sesiones:
            self.iniciar_sesion(sesion_id)
        
        sesion = self.sesiones[sesion_id]
        tiempo_transcurrido = (datetime.now() - sesion['inicio']).total_seconds()
        
        # Verificar si pasó el tiempo límite
        if tiempo_transcurrido > self.UMBRAL_ESCALADO_SEGUNDOS:
            return f"Ha pasado el tiempo máximo de esta llamada. Por favor, contacta a Roberto Gracian al 832-245-4634 para continuar. ¡Gracias!"
        
        # Agregar mensaje al historial
        sesion['historial'].append({
            'role': 'user',
            'content': mensaje_usuario
        })
        
        try:
            # Llamar a Claude API con el prompt del sistema
            respuesta = self.client.messages.create(
                model=os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001"),
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
            
            return self._extraer_contacto(sesion_id, respuesta_texto, telefono_llamante)
        
        except Exception as e:
            # Log the actual error for debugging
            import traceback
            print(f"ERROR EN PROCESAR_MENSAJE: {str(e)}")
            print(f"TRACEBACK: {traceback.format_exc()}")
            # Fallback si hay error con la API
            return f"Disculpa, tengo un problema técnico. Por favor, llama directamente al 281-352-4784 o habla con Roberto Gracian al 832-245-4634. ¡Gracias!"
    
    def _extraer_contacto(self, sesion_id: str, texto: str,
                          telefono_llamante: Optional[str]) -> str:
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
        limpio = PATRON_CONTACTO.sub("", texto)
        limpio = limpio.replace("*", "").replace("#", "")
        return re.sub(r"\s+", " ", limpio).strip()

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
