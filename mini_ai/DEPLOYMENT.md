# Servicio independiente autorizado de Mini AI

Servicio Railway `nexxus-mini-ai`, producción del proyecto `nexxus-core`,
ID `429aea70-52eb-4646-b43d-0d2c61645f08`. Dominio asignado:
`https://nexxus-mini-ai-production.up.railway.app`.

El despliegue se autoriza en la conversación del 18 de octubre de 2026. No se
fusiona main ni se actualiza el servicio de Claudia. El servicio nuevo usa
`mini_ai/Dockerfile`, un worker y `mini_ai.app:app`; solo copia el módulo Mini AI
y el helper de Supabase. No importa main, modelos de negocios ni Claudia.
Las claves se referencian desde las variables existentes de Railway, sin
copiarlas al cliente. La fuente se fija a un commit para impedir despliegues
accidentales por futuras subidas a la rama.

Supabase: la migración de `mini_ai_quota` se aplicó y se verificaron permisos
(service_role permite ejecución, anon la deniega). La cuenta autorizada fue
comprobada en auth.users y configurada en Railway; sus datos no se incluyen aquí.

`POST /api/mini-ai/session` acepta correo/contraseña del usuario existente:
valida el correo configurado, reenvía a Supabase Auth con anon key, verifica
la sesión y el UUID autorizado. Devuelve solo access_token y caducidad, sin
refresh token. Limita diez intentos/minuto en el único worker, además de las
protecciones de Supabase. Errores genéricos y Cache-Control no-store; no se
registran cuerpos, contraseñas ni tokens. El cliente borra el campo contraseña
y conserva la sesión únicamente en memoria. Se vuelve a conectar al caducar.
No se crean cuentas ni cambian contraseñas.

La prueba previa al arranque (`python -m mini_ai.smoke`) genera una sesión de
prueba del usuario mediante la API administrativa de Supabase, sin enviar correo
ni necesitar contraseña. Consume el enlace inmediatamente, valida autorización,
cuota y una respuesta breve de Anthropic, y cierra solo esa sesión temporal con
scope=local. No registra enlaces, tokens, datos del usuario o respuesta de IA.
La prueba consume un intento de cuota. Se retira el comando previo una vez
verificado el primer despliegue para no repetir accesos de prueba innecesarios.

Rollback: desactivar MINI_AI_ENABLED o detener únicamente este servicio. La tabla
de cuota puede conservarse. No modificar fuentes, variables o despliegues de los
servicios anteriores para revertir Mini AI.

## Resultado verificado

Despliegue `1441406c-f203-4ec9-8129-b5a068879265`, SUCCESS, commit
`f53e356a86ac0a3b3593ad9a0d6cfec3b79b12f0`. El comando previo registró
MINI_AI_SMOKE_PASS: sesión Supabase, autorización, cuota SQL y respuesta real
de Anthropic verificadas. La sesión temporal se cerró y se retiró el comando
previo de la configuración para futuros despliegues. No se registra su token.

Se verificaron además por HTTPS público /health=200 y las rutas protegidas
sin sesión o con cuenta no configurada=401, con la CA del paquete USB.
Las referencias de claves utilizan el UUID del servicio existente: las
referencias por nombre con punto final no se resolvían. Ningún valor secreto
se guardó en el cliente, repositorio o archivos locales.
