# Registro de hogares AITA Domus — primera base

El nuevo panel `/domus` registra hogares, habitaciones, asistentes y dispositivos usando las cuentas de Supabase Auth existentes. Cada hogar tiene sus propios permisos. Esta versión registra equipos; no descubre ni controla dispositivos ni vincula automáticamente Alexa/Google. La skill existente y su invocación «Alexa, abre mi Nexo» conservan su funcionamiento y configuración.

## Activación

1. Revisar y aplicar `supabase/migrations/20261003120000_domus_onboarding.sql` en el proyecto Supabase del backend, con el flujo habitual de migraciones. Es una migración transaccional aditiva: crea seis tablas `domus_*`, políticas RLS y funciones propias. No cambia tablas de negocio. No ejecutarla una segunda vez: registrar su aplicación en el historial de migraciones.
2. Publicar este cambio del backend. Por defecto el panel responde 404 y no afecta los canales actuales.
3. Mantener `SUPABASE_URL` y `SUPABASE_ANON_KEY`, ya usadas por Manager. Activar `DOMUS_ONBOARDING_ENABLED=true` después de aplicar y verificar la migración. No necesita nuevas claves privadas. Nunca introducir `SUPABASE_SERVICE_ROLE_KEY` en el navegador.
4. Abrir `/domus`, entrar con una cuenta de Supabase Auth existente y crear una casa. La primera versión no incluye alta pública ni invitaciones; el administrador da de alta las cuentas por el proceso existente. No reutilizar las cuentas comerciales para nuevos clientes sin un proceso de alta definido.
5. Registrar al menos una habitación y un asistente. Los dispositivos son opcionales. Finalizar guarda la fecha del registro; las conexiones siguen pendientes.

No se ha aplicado esta migración a producción ni se ha activado el panel como parte de su preparación. No hay cambios necesarios en Amazon Developer Console para este panel.

## Permisos y datos

- La creación de una casa y su propietario ocurre en una función atómica autenticada. Una clave de solicitud evita duplicar casas al reintentar. Límite inicial: 20 casas creadas por cuenta.
- Propietarios: registro y edición básica. Miembros e invitados: consulta. Todos guardan únicamente su propio consentimiento. El navegador no puede crear membresías, elevar roles, mover dispositivos a otra casa ni marcar conexiones como verificadas.
- Se guarda nombre de casa, zona horaria validada, idioma, habitaciones, plataformas, nombre/tipo/marca de dispositivos y preferencia de consentimiento. No se guardan contraseñas de equipos, tokens de fabricantes, Alexa user IDs ni conversaciones.
- Un dispositivo no puede pertenecer a una habitación de otra casa, incluso si el usuario administra ambas.
- El consentimiento para Anthropic se registra como historial inmutable y puede revocarse añadiendo una nueva preferencia. La opción empieza desmarcada. Todavía no está conectada al enrutamiento Alexa V1: ese canal continúa con la autorización y configuración existentes de Jorge. No admitir otras casas en el canal de voz hasta implementar la vinculación y la evaluación de consentimiento en servidor.
- El panel usa un almacenamiento de sesión separado del panel Manager. El servidor entrega solo URL y clave pública; Supabase comprueba el JWT y RLS en cada operación.

## Conexiones posteriores

La siguiente etapa necesita una vinculación autenticada entre la identidad de Alexa y el hogar, tokens cifrados por proveedor, permisos por dispositivo y adaptadores verificables. Los nombres de marca registrados no conceden acceso. Los proveedores deberán ofrecer OAuth, Matter, API local o una pasarela compatible; elegir el flujo real por fabricante antes de mostrar un botón «Conectar».

Google requiere su propia integración compatible. Google retiró Conversational Actions el 13 de junio de 2023; no asumir una skill personalizada equivalente. Véase [Google, retirada de Conversational Actions](https://developers.google.com/assistant/ca-sunset). La plataforma `google` es un registro pendiente, no una integración activa.

La arquitectura de permisos sigue [RLS de Supabase](https://supabase.com/docs/guides/database/postgres/row-level-security) y [funciones de base de datos](https://supabase.com/docs/guides/database/functions). Las funciones con privilegios usan un `search_path` vacío y referencias explícitas.

## Verificación reproducible

```sh
python -m pytest tests/test_domus.py tests/test_domus_intelligence.py tests/test_domus_onboarding.py tests/test_manager_and_claudia.py -q
node --check domus/panel/app.js
npm install --prefix ../domus-db-check @electric-sql/pglite --no-audit --no-fund --cache ../domus-db-check/npm-cache
NODE_PATH=../domus-db-check/node_modules node tests/sql/domus_security.mjs
```

PGlite ejecuta PostgreSQL real en una base aislada. El script prueba 21 condiciones: aislamiento, propietario atómico e idempotencia, invitado de solo lectura, restricciones de columnas y membresías, habitación de otro hogar, consentimientos personales e inmutables, acceso anónimo y conservación de una tabla de negocio. Antes de activar en producción, verificar también contra el proyecto Supabase de destino y sus políticas existentes. Las pruebas locales no acreditan la conexión a fabricantes ni un recorrido completo con una cuenta real.
