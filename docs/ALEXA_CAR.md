# Alexa AITA Car — conexión privada pendiente de activación

Skill creada en la consola existente de Amazon, separada de Domus. ID: amzn1.ask.skill.d4b91cee-c58b-4c43-97bd-4d2256fb3943. Modelo es-US compilado correctamente por Amazon el 11 de octubre de 2026; una advertencia, sin errores. Invocación: `aita carro`. No publicada en el catálogo. Todavía no conectada al piloto ni probada en el teléfono.

## Implementado

Consulta mantenimiento/gastos/estacionamiento del vehículo principal. Registra gastos USD con categoría e importe, confirmación sí/no en la misma sesión, caducidad 120 segundos e idempotencia por requestId. Aislamiento por cuenta. No controla música, mapas, OBD o dispositivos.

Proveedor OAuth authorization-code: consentimiento explícito, redirects exactos, cliente confidencial autenticado con HTTP Basic, códigos de un solo uso (120 segundos), acceso de una hora, renovación con rotación y autorización máxima de 30 días. Solo hashes persistidos. No exporta Supabase JWT. `/alexa/manage` permite revocar todos los tokens y borradores de la cuenta; la eliminación de la allowlist también bloquea inmediatamente el acceso.

HTTPS: SDK ASK oficial verifica certificado y firma; se comprueban skillId, coherencia de sesión, timestamp y requestId antes de despachar. Peticiones de máximo 32KB, descarga de certificado acotada y límite global 120 solicitudes/minuto por endpoint. Las únicas excepciones a Origin son `/api/car/alexa` (firma Amazon) y `/alexa/token` (cliente confidencial). Resto de API y consentimiento conservan Origin y autenticación web. Permanece apagado por defecto y falla cerrado si falta configuración.

## Configuración preparada

- Authorization URI: https://aita-car-production.up.railway.app/alexa/authorize
- Token URI: https://aita-car-production.up.railway.app/alexa/token
- Endpoint skill HTTPS: https://aita-car-production.up.railway.app/api/car/alexa
- Grant: Authorization Code; autenticación HTTP Basic; scope `car`; PKCE no configurado.
- Redirects exactos mostrados por Amazon:
  - https://alexa.amazon.co.jp/api/skill/link/M1BZAATBKE01YP
  - https://layla.amazon.com/api/skill/link/M1BZAATBKE01YP
  - https://pitangui.amazon.com/api/skill/link/M1BZAATBKE01YP
- Variables servidor: AITA_CAR_ALEXA_ENABLED, AITA_CAR_ALEXA_SKILL_ID, AITA_CAR_ALEXA_CLIENT_ID, AITA_CAR_ALEXA_CLIENT_SECRET (mínimo 32 caracteres aleatorios), AITA_CAR_ALEXA_REDIRECTS (lista separada por comas).

No introducir secretos en Git, documentación, capturas ni chat. El dueño debe completar la entrada de la nueva credencial en Amazon; misma credencial en el servicio Car de Railway. Activación final requiere aprobación del acceso de Alexa a consultas y respuestas del vehículo y gastos, incluyendo ubicación de estacionamiento. No aceptar contratos ni publicar skill comercial sin autorización.

## Prueba final pendiente

Configurar cliente/secret y activar endpoint solo tras aprobar alcance. En la app Alexa del iPhone (cuenta Amazon desarrolladora): habilitar skill de desarrollo, vincular cuenta AITA con inicio de sesión y consentimiento. Consultar mantenimiento, dictar gasto, cancelar, confirmar y verificarlo en Car. Revocar y verificar bloqueo. La integración en Ottocast y la detección automática de viajes son trabajos separados y no quedan resueltos por Alexa.

Fuentes oficiales:
La dependencia oscrypto se fija al commit oficial d5f3437ed24257895ae1edd9e503cfb352e635a8 para corregir la detección de OpenSSL en Linux. No se deshabilita la validación de certificados. Fuente: https://github.com/wbond/oscrypto/commit/d5f3437ed24257895ae1edd9e503cfb352e635a8

https://developer.amazon.com/en-US/docs/alexa/custom-skills/security-testing-for-an-alexa-skill.html
https://developer.amazon.com/docs/alexaplus/account-linking/configure-authorization-code-grant.html
