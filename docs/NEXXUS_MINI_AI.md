# Nexxus Mini AI

Estado al 18 de octubre de 2026: servicio independiente desplegado con autorización
en `https://nexxus-mini-ai-production.up.railway.app`. Usa Anthropic y Supabase
existentes. La rama `codex/nexxus-mini-ai` permanece separada de main. No se
modificó el despliegue de Claudia ni se fusionó main.

## Arquitectura y aislamiento

El proveedor real se identificó en `domus/intelligence.py`: AsyncAnthropic.
Mini AI tiene cliente de proveedor, prompt y contexto propios, sin herramientas,
búsqueda web ni acceso a datos de negocios, dispositivos o sesiones de Claudia.
El servicio Railway dedicado ejecuta `mini_ai.app:app` con un único worker.
El Dockerfile solo copia Mini AI y el helper existente de Supabase; no importa
main, RecepcionistaIAService ni los modelos de negocios.

[Configuración y verificación del despliegue](../mini_ai/DEPLOYMENT.md).

## Acceso del usuario

El cliente permite conectar con correo y contraseña de la cuenta Supabase ya
existente en Nexxus Manager. Se autoriza únicamente el UUID configurado en Railway.
No se crean cuentas ni cambian contraseñas. El campo de contraseña se borra al
conectar y no se escribe en disco. La sesión vive solo en memoria; al caducar hay
que volver a conectar. No hay que copiar claves o tokens manualmente.

`POST /api/mini-ai/session` acepta JSON con `email` y `password`. Comprueba el
correo configurado, usa Supabase Auth con anon key, valida el access token con
`/auth/v1/user` y comprueba UUID y correo. Devuelve solo `access_token` y
`expires_in`, sin refresh token ni objetos de usuario. Cuerpo de hasta 4.096 bytes,
campos extra prohibidos, diez intentos por minuto en el worker, además de las
protecciones de Supabase. Errores genéricos, sin eco de contraseñas ni validaciones.
No se registran cuerpos, cabeceras Authorization o contraseñas.

## Chat

`POST /api/mini-ai/chat` requiere `Authorization: Bearer <access-token>` y JSON:

```json
{"messages":[{"role":"user","content":"Hola, Nexxus"}]}
```

```json
{"assistant":"Nexxus Mini AI","reply":"Hola…"}
```

Se valida la sesión en Supabase y se exige UUID en `MINI_AI_USER_IDS`. No se
acepta un tenant elegido por el cliente. El contexto es solo texto, con roles
user/assistant alternados desde usuario y terminando en usuario, hasta diez
mensajes de 2.000 caracteres y cuerpo de 24.000 bytes, también fragmentado.
El servidor fija el prompt. No hay memoria del servidor; el cliente conserva
hasta cuatro intercambios previos. El contexto no sirve para autorizar acciones.
El proveedor tiene límite de 500 tokens, timeout de 25 s y ningún reintento
automático. No se usan respuestas prefabricadas fingiendo una IA operativa.

Códigos: 401 sesión o credenciales inválidas; 403 usuario no autorizado; 413
cuerpo excesivo; 415 tipo incorrecto; 422 formato; 429 cuota o intentos; 502
proveedor; 503 configuración o dependencia. Respuestas `Cache-Control: no-store`.

## Configuración, solo servidor

| Variable | Uso |
|---|---|
| `MINI_AI_ENABLED` | true habilita; false/ausente falla cerrado |
| `MINI_AI_EMAIL` | Correo de la cuenta permitida |
| `MINI_AI_USER_IDS` | UUID autorizados separados por coma |
| `MINI_AI_MODEL` | Modelo de Anthropic configurado |
| `ANTHROPIC_API_KEY` | Clave existente, solo servidor |
| `SUPABASE_URL` | Proyecto existente |
| `SUPABASE_ANON_KEY` | Auth del usuario |
| `SUPABASE_SERVICE_ROLE_KEY` | Cuota y comprobación administrativa del despliegue |

Las claves se referencian dentro de Railway usando el UUID del servicio de
origen; no se copiaron al cliente ni a archivos locales. Las referencias por
nombre fallaban por el punto final del servicio. Se comprobó su resolución por
identificador antes de activar Mini AI. La fuente está fijada al commit
autorizado: subir documentación a la rama no despliega automáticamente.

La migración `20261018120000_mini_ai_quota.sql` se aplicó en Supabase mediante
el SQL Editor. La tabla tiene RLS, sin acceso anon/authenticated; la función
solo puede ejecutarse como service_role. No guarda texto ni audio. Bloquea por
usuario y reserva atómicamente hasta seis intentos/minuto y cien/día UTC entre
workers/instancias. Las fallas del proveedor consumen intento. Si falta o falla
la cuota, no se llama al proveedor. No es una factura monetaria exacta.
Si se adopta el flujo de migraciones de Supabase CLI, registrar esta migración
manual como aplicada antes de ejecutar db push; no volver a crear la tabla.

Rollback: desactivar Mini AI o detener únicamente su servicio. Puede conservarse
la tabla de cuota. No modificar Claudia ni main para revertir este servicio.

## Cliente y USB

[Instalación y límites de Snow Leopard](../clients/nexxus-mini/INSTALACION.md).
Python 2.6/Tk; curl i386 con OpenSSL y CA dentro del ZIP; grabación Audio Queue
con capturador x86_64 de mínimo declarado 10.6. Binarios originales MacPorts
con firmas verificadas y rutas de bibliotecas adaptadas. El empaquetador fija
versiones/hashes e incluye fuentes, licencias, procedencia y manifiesto.
No instala archivos de sistema ni requiere Safari o App Store.

El Mac mini 2007/10.6.8 sigue pendiente de prueba física: Tk, carga del transporte
Intel, handshake TLS y micrófono real. La gramática de Python 2.6 pasó, pero no
acredita ejecución en ese hardware. El audio se graba localmente diez segundos;
transcripción y respuestas habladas siguen pendientes.

## Verificación

- 46 pruebas Python de Mini AI pasan, incluidas autenticación, aislamiento,
  validación, cuota, errores sin secretos, token/contraseña fuera de argumentos,
  TLS válido y rechazo de CA/hostname incorrectos, integridad del paquete y
  limpieza de la sesión temporal de despliegue.
- Pruebas SQL con PostgreSQL WASM (PGlite 0.5.10): límites, reinicios, usuarios
  independientes y permisos. No simulan carreras entre conexiones independientes.
- Prueba real previa al arranque: Supabase Auth, autorización del UUID, cuota SQL
  y respuesta de Anthropic. Usa una sesión temporal generada sin correo, que se
  cierra con scope=local para conservar las sesiones de otros dispositivos.
- HTTPS público con CA del paquete: /health responde 200; chat sin sesión y
  acceso con cuenta no configurada responden 401. No se conocen ni probaron las
  contraseñas reales del usuario.
- Capturador compila sin advertencias en el host, declara mínimo 10.6 y no accedió
  al micrófono durante el desarrollo. ZIP con CRC, hashes y permisos comprobados.
- La revisión general anterior reprodujo 23 fallos también en main: 21 pruebas
  que esperan rutas heredadas retiradas y dos de Alexa/dependencias del entorno.

```sh
pytest -q tests/mini_ai
npm ci --prefix tests/mini_ai
npm test --prefix tests/mini_ai
```
