"""
Servicio para manejo de webhooks de Twilio.
Soporta multi-tenant con resolución automática por número de teléfono.
"""

import os
import json
import logging
from typing import Optional, Dict, Any
from twilio.request_validator import RequestValidator
from urllib.parse import urlencode

logger = logging.getLogger(__name__)


class TwilioWebhookException(Exception):
    """Excepción personalizada para errores del webhook de Twilio"""
    pass


class PhoneTenantResolver:
    """
    Resuelve el tenant (cliente/negocio) basado en el número de teléfono llamado.
    Mantiene un mapeo escalable de números → tenants.
    """

    def __init__(self, mapping_file: str = "phone_tenant_mapping.json"):
        """
        Inicializa el resolver cargando el mapeo de teléfono → tenant.

        Args:
            mapping_file: Ruta al archivo JSON con el mapeo
        """
        self.mapping = self._load_mapping(mapping_file)
        logger.info(f"PhoneTenantResolver inicializado con {len(self.mapping)} números")

    def _load_mapping(self, mapping_file: str) -> Dict[str, Dict[str, str]]:
        """Carga el mapeo de números a tenants desde JSON"""
        try:
            if not os.path.exists(mapping_file):
                logger.warning(f"Archivo {mapping_file} no existe. Usando mapeo vacío.")
                return {}

            with open(mapping_file, 'r') as f:
                mapping = json.load(f)

            logger.info(f"Mapeo cargado: {list(mapping.keys())}")
            return mapping
        except Exception as e:
            logger.error(f"Error cargando {mapping_file}: {e}")
            return {}

    def resolve(self, phone_number: str) -> Optional[Dict[str, str]]:
        """
        Resuelve un número de teléfono a su tenant.

        Args:
            phone_number: Número en formato E.164 (+XXXXXXXXXXXX)

        Returns:
            Dict con keys: tenant, agent, config_file
            O None si no se encuentra
        """
        if not phone_number:
            return None

        normalized = phone_number.strip()

        if normalized in self.mapping:
            result = self.mapping[normalized]
            logger.info(f"Tenant resuelto: {normalized} → {result.get('tenant')}")
            return result

        logger.warning(f"Número no mapeado: {normalized}")
        return None


class TwilioSignatureValidator:
    """
    Valida que las requests provengan genuinamente de Twilio
    usando X-Twilio-Signature y TWILIO_AUTH_TOKEN.
    """

    def __init__(self):
        """Inicializa el validador con la clave de autenticación de Twilio"""
        self.auth_token = os.getenv("TWILIO_AUTH_TOKEN")
        self.validator = RequestValidator(self.auth_token) if self.auth_token else None

        if not self.auth_token:
            logger.warning("TWILIO_AUTH_TOKEN no configurado. Validación de firma deshabilitada.")

    def validate(self, url: str, data: Dict[str, Any], signature: str) -> bool:
        """
        Valida que el request provenga de Twilio.

        Args:
            url: URL completa del webhook
            data: Datos POST (form-urlencoded parseados a dict)
            signature: Valor del header X-Twilio-Signature

        Returns:
            True si es válido, False si no
        """
        if not self.validator:
            logger.warning("Validador no disponible. Request aceptado sin validación.")
            return True

        try:
            is_valid = self.validator.validate(url, data, signature)

            if not is_valid:
                logger.warning(f"Firma de Twilio inválida. URL: {url}")

            return is_valid
        except Exception as e:
            logger.error(f"Error validando firma de Twilio: {e}")
            return False


class TwiMLResponseBuilder:
    """
    Construye respuestas TwiML válidas para Twilio.
    No depende de bibliotecas externas para máxima compatibilidad.
    """

    @staticmethod
    def gather_response(
        session_id: str,
        greeting: str,
        max_duration_seconds: int = 10,
        callback_url: str = "/api/twilio/mensaje"
    ) -> str:
        """
        Crea una respuesta TwiML que:
        1. Reproduce un saludo
        2. Captura input de voz del usuario
        3. Transcriba automáticamente
        4. Envía la transcripción a callback_url

        CAMBIO CLAVE: Usa <Gather> en lugar de <Record>
        - <Gather input="speech"> captura voz INTERACTIVA
        - <Record> solo graba sin transcribir en tiempo real

        Args:
            session_id: ID de sesión para la llamada
            greeting: Texto que Twilio debe decir
            max_duration_seconds: Duración máxima de grabación
            callback_url: Endpoint donde enviar la transcripción

        Returns:
            String XML válido (TwiML)
        """
        safe_greeting = greeting.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Gather
        input="speech"
        action="{callback_url}"
        method="POST"
        language="es-US"
        speechTimeout="auto"
        numDigits="1"
    >
        <Say voice="Polly.Conchita" language="es-ES">{safe_greeting}</Say>
    </Gather>
    <Say voice="Polly.Conchita" language="es-ES">No escuché tu respuesta. Por favor llama de nuevo.</Say>
    <Hangup/>
</Response>"""
        return xml

    @staticmethod
    def error_response(message: str = "Lo sentimos, hay un problema técnico. Por favor intente más tarde.") -> str:
        """
        Respuesta de error que reproduce un mensaje y cuelga.
        """
        safe_message = message.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="Polly.Conchita" language="es-ES">{safe_message}</Say>
    <Hangup/>
</Response>"""
        return xml


class TwilioWebhookHandler:
    """
    Manejador principal del webhook de Twilio.
    Orquesta: validación, resolución de tenant, y respuesta TwiML.
    """

    def __init__(
        self,
        phone_resolver: Optional[PhoneTenantResolver] = None,
        signature_validator: Optional[TwilioSignatureValidator] = None
    ):
        """
        Inicializa el manejador.

        Args:
            phone_resolver: Si None, crea uno automáticamente
            signature_validator: Si None, crea uno automáticamente
        """
        self.phone_resolver = phone_resolver or PhoneTenantResolver()
        self.signature_validator = signature_validator or TwilioSignatureValidator()
        self.twiml_builder = TwiMLResponseBuilder()

    def handle_incoming_call(
        self,
        call_data: Dict[str, str],
        request_url: str,
        signature: str,
        session_creator_callback=None
    ) -> tuple[str, int, str]:
        """
        Procesa una llamada entrante de Twilio.

        Args:
            call_data: Datos POST de Twilio {From, To, CallSid, CallStatus, ...}
            request_url: URL completa del request
            signature: Header X-Twilio-Signature
            session_creator_callback: Función para crear sesión

        Returns:
            Tupla: (twiml_response: str, status_code: int, content_type: str)
        """

        # 1. Validar firma (seguridad)
        if not self.signature_validator.validate(request_url, call_data, signature):
            logger.error("Request no pasó validación de firma")
            return (
                self.twiml_builder.error_response("Validación fallida"),
                403,
                "application/xml"
            )

        # 2. Extraer datos básicos
        call_sid = call_data.get("CallSid", "UNKNOWN")
        from_number = call_data.get("From", "UNKNOWN")
        to_number = call_data.get("To", "UNKNOWN")
        call_status = call_data.get("CallStatus", "unknown")

        logger.info(
            f"[{call_sid}] Llamada entrante: {from_number} → {to_number} "
            f"(status: {call_status})"
        )

        # 3. Resolver tenant
        tenant_info = self.phone_resolver.resolve(to_number)

        if not tenant_info:
            logger.error(f"[{call_sid}] Número no mapeado: {to_number}")
            return (
                self.twiml_builder.error_response(
                    "El número no está configurado. Contacte al administrador."
                ),
                200,
                "application/xml"
            )

        tenant = tenant_info.get("tenant")
        agent = tenant_info.get("agent", "Assistant")
        config_file = tenant_info.get("config_file")

        logger.info(f"[{call_sid}] Tenant resuelto: {tenant}, Agent: {agent}")

        # 4. Crear sesión
        session_id = None
        if session_creator_callback:
            try:
                session_id = f"{tenant}_{call_sid}"
                logger.info(f"[{call_sid}] Sesión: {session_id}")
            except Exception as e:
                logger.error(f"[{call_sid}] Error creando sesión: {e}")
                return (
                    self.twiml_builder.error_response("Error iniciando sesión"),
                    500,
                    "application/xml"
                )

        # 5. Generar respuesta TwiML
        greeting = f"Hola, soy {agent} de {tenant}. ¿En qué puedo ayudarte?"

        try:
            twiml = self.twiml_builder.gather_response(
                session_id=session_id or call_sid,
                greeting=greeting,
                max_duration_seconds=600,
                callback_url="/api/twilio/mensaje"
            )

            logger.info(f"[{call_sid}] TwiML generado correctamente")

            return (twiml, 200, "application/xml")

        except Exception as e:
            logger.error(f"[{call_sid}] Error generando TwiML: {e}")
            return (
                self.twiml_builder.error_response("Error procesando su llamada"),
                500,
                "application/xml"
            )
