# Nexxus Mini AI — primera implementación

## Revisión inicial y aislamiento

Repositorio: `jorgeabalo/nexxus-core.` (punto final). Base: `main` en
`7e644f5fea123be1523d14244cf0e1cb55e137ea`. Rama: `codex/nexxus-mini-ai`.
Se inspeccionaron las ramas remotas y el árbol actual. La rama
`fix/remove-legacy-chat-endpoints` no tenía commits exclusivos respecto a main.
El README de recepcionista no describe por completo el código actual: no se han
dado sus endpoints antiguos por disponibles.

`domus/intelligence.py` ya usa `AsyncAnthropic`, con `ANTHROPIC_API_KEY` en el
servidor y contexto separado de los negocios. Mini AI reutiliza **ese proveedor
y su convención de variables**, con un cliente independiente, prompt propio,
sin herramientas, sin importar RecepcionistaIAService ni sesiones de Claudia.
Se revisaron solo los nombres de variables en Railway: existen
`ANTHROPIC_API_KEY`, `SUPABASE_URL`, `SUPABASE_ANON_KEY` y
`SUPABASE_SERVICE_ROLE_KEY`; no se leyeron sus valores ni se verificó su validez. No se han
hecho peticiones reales a Anthropic, modificado main, aplicado migraciones o desplegado.

## API nueva

`POST /api/mini-ai/chat`, registrada en el FastAPI existente de `main.py`.
Deshabilitada salvo `MINI_AI_ENABLED=true`. Contrato nuevo, no endpoint existente.

```http
Authorization: Bearer <access-token-del-usuario-Supabase>
Content-Type: application/json
```

```json
{"messages":[{"role":"user","content":"Hola, Nexxus"}]}
```

```json
{"assistant":"Nexxus Mini AI","reply":"Hola…"}
```

Se valida la sesión con el helper existente `SupabaseAdmin.user_from_jwt`
(consulta `/auth/v1/user`, sin confiar en decodificar el JWT en el cliente).
Además se exige UUID en `MINI_AI_USER_IDS`, una lista explícita de usuarios
permitidos. No basta con tener una cuenta del proyecto. Nunca se usa el tenant
que envíe el cliente; los campos extra se rechazan. No hay acceso a datos de negocios.

Hasta 10 mensajes alternados desde usuario, terminando en usuario; 2.000
caracteres por mensaje, máximo 24.000 bytes por cuerpo, también con transferencia
fragmentada. Solo roles user/assistant, sin instrucciones system controladas por
el cliente. El servidor pone el prompt Nexxus. El proveedor devuelve hasta 500
tokens, con timeout de 25 s y sin reintentos automáticos. No hay herramientas,
búsqueda web ni memoria del servidor. El contexto enviado por el cliente no es
evidencia fiable para autorizar acciones; esta API no puede realizar acciones.

Respuestas: 401 sesión inválida/caducada; 403 acceso no autorizado; 413 cuerpo
excesivo; 415 tipo incorrecto; 422 formato inválido; 429 cuota; 502 proveedor;
503 deshabilitado o dependencia sin configurar. No se devuelven errores del
proveedor, secretos ni texto de entrada en errores de validación. Las respuestas
del prefijo llevan `Cache-Control: no-store` en la aplicación principal.
No registrar cabeceras Authorization ni cuerpos en Railway o proxies.

## Configuración pendiente, solo servidor

| Variable | Uso |
|---|---|
| `MINI_AI_ENABLED` | `false`/ausente deshabilita; activar solo en entorno aprobado |
| `MINI_AI_USER_IDS` | UUID de usuarios autorizados, separados por coma; vacío deniega todos |
| `MINI_AI_MODEL` | Opcional: modelo de Anthropic autorizado para esta función |
| `ANTHROPIC_API_KEY` | Clave ya usada por el servidor; nunca en el cliente |
| `SUPABASE_URL` | Proyecto existente |
| `SUPABASE_ANON_KEY` | Helper existente de validación Auth |
| `SUPABASE_SERVICE_ROLE_KEY` | Solo servidor: ejecución de cuota |

Modelo: `MINI_AI_MODEL` → `DOMUS_AI_MODEL` → `ANTHROPIC_MODEL` → `MODELO_CLAUDE`
→ `claude-haiku-4-5-20251001`, como la integración real existente. Que el modelo
esté disponible para la cuenta debe verificarse en un entorno aprobado.

La migración `20261018120000_mini_ai_quota.sql` crea una tabla con RLS, sin acceso
para anon/authenticated, y una función exclusiva de service_role. Consume cuota
atómicamente con bloqueo por usuario: 6 intentos por minuto y 100 por día UTC,
compartidos por workers/instancias. Las fallas del proveedor consumen intento;
no hay reembolsos automáticos que permitan eludir el límite. No guarda mensajes
ni audio. El uso no es una factura monetaria exacta: configurar también alertas
y presupuestos del proveedor antes de habilitarlo para más usuarios.
Si la migración no está aplicada, la cuota falla cerrada y no llama a la IA.
Desactivar `MINI_AI_ENABLED` es la reversión inmediata; quitar el usuario de la
lista revoca su acceso a Mini AI sin cambiar Claudia. No es necesario borrar la tabla.

## Cliente legado y USB

[Instalación, compilación, micrófono y TLS](../clients/nexxus-mini/INSTALACION.md).
Python 2.6 + Tk, sin dependencias Python externas; transporte por curl externo
verificado; captura C con Audio Queue Services. No hay claves de proveedor en el
cliente. El access token temporal de usuario es una credencial de sesión y se
mantiene solo en memoria; no es una clave de servidor.

Pendientes para una versión final: prueba física del transporte TLS Intel y
del capturador x86_64 incluidos en el ZIP de evaluación;
flujo de inicio de sesión/vinculación y renovación fácil; servicio de
transcripción autorizado, consentimiento para subir audio y sus límites;
salida de voz. No se presenta la captura local como conversación por voz.

## Verificación

```sh
pytest -q tests/mini_ai
pytest -q
clang -Wall -Wextra -Werror -framework AudioToolbox -framework CoreFoundation \
  clients/nexxus-mini/record-audio.c -o /tmp/nexxus-mini-record-audio
```

Las pruebas sustituyen proveedor y Supabase: no consumen cuota real ni leen
secretos de producción. Cubren sesiones, permisos, separación de contexto,
validación y tamaño, fallas cerradas de cuota, errores sin secretos y transporte
TLS sin shell ni token en argumentos. La compilación del capturador en un Mac
moderno no demuestra ejecución en 10.6. El SQL necesita además comprobación en
un PostgreSQL de pruebas, nunca directamente en producción.

Fuentes: [validación de usuario Supabase](https://supabase.com/docs/reference/javascript/auth-getuser),
[API de Anthropic](https://platform.claude.com/docs/en/api/messages),
[Audio Queue Services](https://developer.apple.com/documentation/audiotoolbox/audio-queue-services),
[curl](https://curl.se/docs/manpage.html).

### Resultados de esta revisión

- 31 pruebas de Mini AI en Python pasan, incluida la integración de ruta en main
  y el contrato del adaptador de Anthropic con proveedor sustituido.
- `npm ci --prefix tests/mini_ai && npm test --prefix tests/mini_ai` comprueba
  la migración real en PostgreSQL WASM (PGlite 0.5.10, solo desarrollo): cuota,
  reinicios, usuarios independientes y permisos anon/authenticated/service_role.
  Es un motor de una conexión; no acredita carreras entre múltiples conexiones.
  Antes de activar producción queda probar carga concurrente en PostgreSQL/Supabase.
- El capturador C compila con `-Wall -Wextra -Werror` en el host moderno. No se
  inició grabación ni se accedió al micrófono durante el desarrollo.
- La suite completa conserva los mismos 23 fallos que una copia intacta de la
  base: 21 pruebas de API heredada que esperan rutas retiradas y 2 de verificación
  Alexa en este entorno (falta `ask-sdk-webservice-support`). No se han restaurado
  endpoints retirados ni cambiado Claudia para hacer pasar pruebas antiguas.
- No se han verificado con servicios reales el proveedor, Supabase Auth,
  certificados de Railway ni captura/Tk/TLS en el hardware 10.6.8.

### Railway comprobado en modo lectura

Producción: `nexxus-core.` y `confident-adventure` siguen `main`; `aita-car`
sigue `feature/aita-car-prototype`. Staging: `miraculous-analysis` sigue
`codex/claude-aita-integration`. Ningún servicio inventariado sigue la nueva
rama. No se cambió configuración ni se solicitó despliegue. Se publica solo
la rama de GitHub; no se abre PR para evitar activar posibles entornos de
vista previa cuya configuración no se ha verificado.

## Segunda entrega: paquete USB de evaluación

`tools/build_mini_usb.py` crea un ZIP autocontenido con transporte Intel de
MacPorts, bibliotecas relocadas, CA, capturador x86_64 con mínimo declarado 10.6,
diagnóstico local, procedencia, fuentes y licencias. Versiones y hashes se fijan
en `tools/mini_usb/*-lock.json`. No se añaden binarios al repositorio.
Esta entrega sustituye la limitación inicial de «transporte no incluido» en el
ZIP de evaluación. Sigue pendiente ejecutarlo en el Mac real, así como desplegar
la API con autorización y completar vinculación/transcripción/salida de voz.
Las pruebas TLS locales verifican CA y hostname, sin transmitir el token cuando
falla la verificación. No se han enviado solicitudes de IA ni audio reales.

Validación de la segunda entrega: 35 pruebas Python pasan, además de las pruebas
SQL. Cliente y diagnóstico pasan el análisis con la gramática oficial de Python
2.6.9; esto comprueba sintaxis, no el funcionamiento de Tk o del sistema legado.
El ZIP se comprobó con CRC, hashes de todos sus archivos y permisos ejecutables.
