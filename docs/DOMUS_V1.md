# AITA Domus V1 — integración Alexa / AITA Domus / Nexxus

## Inspección y alcance

Base inspeccionada: `/Users/jorgeabalo/nexxus-core-repo`, commit `841810a`.
`main.py` es FastAPI y registra los webhooks Twilio, panel Manager y portal Member.
`services/twilio_service.py` verifica llamadas y resuelve el tenant por número;
`recepcionista_service.py` mantiene las conversaciones de Claudia; `services/call_logger.py`
registra llamadas y leads en Supabase sin bloquear Claudia. El motor genérico y
los modelos SQLite conviven con el portal Supabase. No hay Jarvis ni conectores
domésticos en esta base. Existe otra copia `nexxus-core.`; esta entrega usa
`nexxus-core-repo`, que incluye la sección Team más reciente.

La única modificación de código existente es registrar un router después de
crear FastAPI. No se cambian webhooks, teléfonos, tenants, bases ni credenciales.
No hay migraciones ni despliegue. Domus está desactivado por defecto.

## Flujo

Alexa → POST `/api/domus/alexa` → autorización + firma SDK Amazon →
`DomusService` de Nexxus → adaptador directo o `JarvisAdapter` → respuesta PlainText.

- Launch abre la sesión; Help explica; Stop/Cancel la cierran; SessionEnded no habla.
- `JarvisCommandIntent` envía la consulta a un endpoint HTTPS autenticado de Jarvis.
- `DomesticCommandIntent` usa un dispositivo resuelto por Alexa; no llama a Jarvis.
- TV, WiiM, plug, thermostat y Roomba tienen claves de adaptador preparadas.
  Todavía no ejecutan acciones y lo dicen expresamente.
- Security, Energy, Pantry y panel tienen módulos reservados para siguientes fases.
- Sin Jarvis configurado se responde que falta la conexión; no se inventan tareas
  ni estados de dispositivos. No existe un motor Jarvis oculto en esta entrega.

## Configuración exacta de Alexa Developer Console

1. En https://developer.amazon.com/alexa/console/ask crea una skill llamada
   **AITA Domus**, modelo **Custom**, idioma **Spanish (US) / es-US** y hosting
   **Provision your own**. Los Echo deben usar el mismo idioma y cuenta de prueba.
2. En **Build → Interaction Model → JSON Editor**, importa
   `domus/alexa-model-es-US.json`. Guarda y ejecuta **Build Model**.
   Invocation name: `nexxus`. Si Alexa reconoce mal AITA, prueba un nombre
   equivalente como `mi domus` y actualiza el modelo; no cambia el backend.
3. En **Build → Endpoint**, elige **HTTPS**, Default Region:
   `https://<dominio-publico-nexxus>/api/domus/alexa`.
   Debe ser accesible por puerto 443, con certificado válido para ese dominio.
   Selecciona la opción de certificado de autoridad confiable o wildcard según
   el certificado real del proveedor. No introduzcas tokens en la URL.
4. Copia el Skill ID de la consola a `DOMUS_ALEXA_SKILL_ID`.
5. En **Test**, habilita **Development**. Obtén `context.System.user.userId`
   del JSON de una solicitud del simulador y cópialo a `DOMUS_ALEXA_USER_IDS`.
   La primera solicitud puede recibir 503/403 hasta configurar esta lista.
   No desactives la verificación para obtenerlo. Admite IDs separados por comas,
   todos pertenecientes a la misma casa fija de esta V1.
6. Configura las variables del servidor y reinicia en un entorno de pruebas.
   Usa el simulador y después un Echo registrado en esa cuenta:
   - «Alexa, abre nexxus» → bienvenida.
   - «Consulta a Nexxus qué tengo pendiente» → respuesta de Jarvis si está conectado.
   - «Controla televisión para apagar» → dispositivo todavía no conectado.
   - «Ayuda», «para», «cancela» → ayuda y cierre.
7. Mantén la skill en Development para tu casa. Publicación y account linking
   multiusuario quedan fuera de esta V1. No se requieren permisos de contactos,
   dirección, notificaciones ni acceso a listas para este flujo.

El modelo usa una frase introductoria para `AMAZON.SearchQuery`; no captura
cualquier frase libre de manera universal. «Pon música en la sala» requerirá
intents/slots de acción, habitación y dispositivo al conectar WiiM.

## Variables que faltan

| Variable | Valor requerido | Estado de esta entrega |
|---|---|---|
| DOMUS_ENABLED | `true` para habilitar, cualquier otro valor deshabilita | deshabilitado |
| DOMUS_ALEXA_SKILL_ID | Skill ID real `amzn1.ask.skill...` | pendiente |
| DOMUS_ALEXA_USER_IDS | IDs Alexa autorizados separados por comas | pendiente |
| DOMUS_HOME_ID | ID fijo de la casa de Jorge, no tenant Golden Age | pendiente |
| DOMUS_JARVIS_URL | Endpoint HTTPS completo que acepte el contrato inferior | no localizado |
| DOMUS_JARVIS_TOKEN | Token privado de servicio dedicado a Domus | pendiente |

Usa `domus/.env.example` como plantilla. El módulo lee variables del proceso;
no carga archivos .env adicionales. No se necesitan claves AWS para HTTPS,
ni otra credencial Twilio, Supabase o Anthropic. No reutilices tokens de negocios.
Estas variables no se rellenaron ni se inspeccionaron archivos secretos existentes.
También falta confirmar el dominio público y registrar/configurar la skill.

## Contrato que debe implementar el servicio Jarvis

POST a `DOMUS_JARVIS_URL`, `Authorization: Bearer <DOMUS_JARVIS_TOKEN>`:

```json
{
  "home_id": "<DOMUS_HOME_ID>",
  "text": "qué tengo pendiente",
  "request_id": "<Alexa requestId>",
  "mode": "read_only",
  "source": "alexa",
  "session_id": "domus:<DOMUS_HOME_ID>"
}
```

Respuesta 2xx: `{"speech":"Tu respuesta hablada"}`. El token debe estar
limitado en Jarvis a esa casa y consultas. Jarvis debe verificar el token,
ignorar instrucciones que pretendan cambiar la casa o conceder permisos,
y **hacer cumplir read_only en el servidor**. Enviar ese campo por sí solo
no restringe un servicio externo: no conectes un endpoint con herramientas
que puedan escribir o ejecutar acciones sin implementar esos permisos.
No se reenvían IDs personales Alexa ni historiales de Claudia.

Tiempo de conexión/respuesta Jarvis: 3 segundos, máximo de despacho 3,5;
respuesta hablada limitada a 2000 caracteres, sin SSML generado por modelos.
Errores no exponen URL, token ni excepción al usuario.

## Seguridad y evolución

ASK SDK valida `Signature-256`, SHA-256, cadena de confianza, vigencia y SAN de
Amazon, y URL de certificado. La descarga se limita en tiempo/tamaño y no sigue
redirecciones. Se exige timestamp dentro de ±150 segundos, Skill ID y usuario
permitido. El home_id lo determina el servidor, nunca slots o sessionAttributes.
Las consultas se limitan a 32 KiB y el texto a 1000 caracteres. No se registran
solicitudes ni credenciales. El SDK se importa de forma diferida: si falta,
Domus se cierra y Nexxus puede iniciar con sus dependencias habituales.

V1 no tiene acciones domésticas activas ni historial persistente. La ventana de
fecha no evita repeticiones dentro de 150 segundos: antes de activar acciones,
añadir deduplicación compartida por requestId (por ejemplo Redis), auditoría,
límites por casa y permisos de acción. Los adaptadores deberán validar acción,
habitación y dispositivo contra configuración fija; no ejecutar texto como
comandos de sistema. Security requiere políticas propias para abrir puertas o
desarmar alarmas. El panel podrá consumir el mismo servicio con autenticación
propia; no reutilizar la firma Alexa como login de panel.

## Instalación y verificación

En un entorno virtual nuevo, instalar ambas listas sin modificar las versiones
existentes de Nexxus:

```sh
python -m pip install -r requirements.txt -r requirements-domus.txt
python -m pytest -q
```

La entrega incluye un parche revisable y los archivos añadidos. Aplicar sólo
sobre la base indicada y comprobar antes que main.py no haya cambiado. No hace
falta migrar bases ni borrar datos. Para rollback, retirar el registro del router
y los archivos añadidos. No subir secretos ni el entorno virtual al repositorio.

## Fuentes oficiales

- https://developer.amazon.com/en-US/docs/alexa/custom-skills/host-a-custom-skill-as-a-web-service.html
- https://developer.amazon.com/en-GB/docs/alexa/custom-skills/slot-type-reference.html
- https://developer.amazon.com/en-US/docs/alexa/custom-skills/understanding-how-users-invoke-custom-skills.html

## Nombre del asistente

El asistente de AITA Domus se llama **Nexxus**. Bienvenida: «Soy Nexxus, tu asistente de AITA Domus». Las consultas de voz usan «consulta a Nexxus», «pregunta a Nexxus» o «dile a Nexxus». La skill se abre con «Alexa, abre Nexxus». JarvisCommandIntent, JarvisAdapter y DOMUS_JARVIS_* se conservan como identificadores internos de compatibilidad; no son el nombre que se presenta al usuario.
