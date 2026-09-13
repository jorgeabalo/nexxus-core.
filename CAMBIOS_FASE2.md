# NEXXUS AI Support — Fase 2: motor genérico multi-tenant (11/09/2026)

## Fase 2g: alertas de riesgo — quién se puede ir, a quién reactivar (11/09/2026, mismo día)

Jorge pidió, entre varias ideas de negocio (financiero, marketing, fidelidad,
contabilidad, inventario), priorizar esta primero: detectar socios en riesgo
de irse ANTES de que cancelen, y armar la lista de socios pausados/cancelados
para reactivar. Se eligió porque no necesita ninguna integración nueva — usa
datos que el sistema ya calculaba (deuda, asistencia).

- **`riesgo_service.py`** (nuevo): reglas simples y explicables, a propósito
  — no es un modelo predictivo ni una caja negra, cada alerta trae el motivo
  exacto ("Debe 2 meses", "No viene hace 24 días", "Bajó su asistencia 71%
  este mes"). Niveles: **crítico** (debe 2+ meses, no viene hace 21+ días, o
  nunca registró una visita siendo socio antiguo) y **alerta** (debe 1 mes,
  no viene hace 10-20 días, o la asistencia cayó 50%+ contra el período
  anterior — con un piso mínimo de actividad previa para no disparar falsos
  positivos). Un socio pausado o cancelado nunca entra en "riesgo" — ya se
  fue; entra en la lista separada de **reactivación**.
- **Endpoint nuevo**: `GET /api/{slug}/alertas` (admin) — devuelve ambas
  listas más los totales. El perfil de un socio (`GET .../perfil`) ahora
  también trae su nivel de riesgo individual.
- **Panel del negocio** (`panel.html`): nueva sección "Alertas" arriba de
  todo (crítico en rojo, alerta en ámbar, cada uno con el motivo y link al
  perfil del socio), y 2 KPIs nuevos ("En riesgo de irse" / "Para
  reactivar") reemplazando dos que pesaban menos para esta decisión (tasa de
  fallback, escaladas — siguen disponibles en el perfil, solo no ocupan un
  KPI de primera vista).
- **Dashboard del socio** (`panel_cliente.html`): si ese socio puntual está
  en riesgo, aparece un banner arriba de todo con el motivo — para cuando
  alguien de recepción entra al perfil de un socio puntual, no solo desde
  la lista general.
- `seed_demo.py`: se agregó un 6º socio demo ("Sofía Delgado", cancelado) y
  se le dio a Carlos Núñez un patrón de asistencia que cae de verdad (7
  visitas el mes pasado, 2 este mes) para poder probar las 3 señales con
  datos reales, no solo con tests.

Probado: 18 tests nuevos (`tests/test_riesgo.py`) cubriendo cada señal por
separado, la combinación de señales, los casos límite (socio nuevo sin
visitas todavía vs. socio antiguo que nunca vino, caída de asistencia sin
suficiente historial previo), aislamiento por negocio, y los endpoints HTTP
correspondientes. **80 tests en total, los 80 pasan.** Probado también a
mano contra el servidor real con los 6 socios demo — la salida de
`/api/fuerza-total/alertas` coincide exactamente con lo esperado.

Pendiente (ideas que Jorge planteó pero no se priorizaron esta vez, quedan
para cuando las pida): convertir estas alertas en un mensaje/llamada
saliente automático (necesita Twilio conectado), campañas de reactivación
con oferta concreta, reglas de fidelidad/estímulos, resumen financiero
periódico con observaciones, contabilidad (QuickBooks) e inventario.

## Fase 2f: registro de progreso del socio — peso, medidas, BMI, asistencia (11/09/2026, mismo día)

Jorge pidió: poder tener un registro de cada socio con medidas y peso, BMI,
tiempo en el gym, cambios (progreso), un dashboard del cliente, y qué tan
habitual viene. Se agregó todo esto:

- **Dos tablas nuevas** (`models.py`): `MedicionCliente` (historial de
  peso/cintura/cadera/pecho/brazo de un socio, una fila por visita a la
  balanza) y `Checkin` (una fila por cada vez que un socio hace check-in).
  Ninguna de las dos vive en `verticals/gym.py` — son genéricas, igual que
  `ClienteNegocio`, porque cualquier vertical basado en seguimiento físico
  (ej. una clínica de nutrición) las puede reusar sin tocar nada.
- `ClienteNegocio` ahora tiene `altura_cm` (para calcular BMI) y
  `fecha_ingreso` (para "tiempo en el gym" — antigüedad como socio).
- **`progreso_service.py`** (nuevo, mismo patrón que `billing_service.py`):
  calcula el BMI siempre a partir de peso+altura (nunca se guarda como
  columna, así nunca queda desactualizado), arma el "progreso" de un socio
  (cambio de peso/BMI entre su primera y su última medición), y calcula
  "qué tan habitual" viene (visitas en los últimos 30 días, promedio
  semanal, días desde la última visita).
- **Se cerró un pendiente viejo**: `registrar_checkin` (la herramienta de
  voz del vertical Gym) antes solo confirmaba en la conversación pero no
  guardaba nada — ahora persiste un `Checkin` real cada vez.
- **Herramienta de voz nueva**: `consultar_progreso` — un socio (o el
  personal del gym) puede preguntarle por teléfono a la asistente "¿cómo
  voy?" y responde con datos reales (peso actual, BMI, cambio desde la
  primera medición, qué tan seguido viene) — nunca inventados, mismo
  principio que ya se usa para las preguntas de pago.
- **3 endpoints nuevos** (admin, por negocio): `POST .../clientes/{id}/mediciones`
  (cargar una medición nueva), `GET .../clientes/{id}/mediciones` (historial),
  `GET .../clientes/{id}/perfil` (perfil completo: lo que alimenta el
  dashboard). Todos validan que el cliente sea de ESE negocio (404 si no).
- **Dashboard del cliente** (`panel_cliente.html`, nueva página en
  `/{slug}/admin/clientes/{id}`): KPIs (peso actual, BMI actual + cambio,
  meses como socio, visitas del mes), gráfico de evolución de peso (SVG,
  con tooltip al pasar el mouse), estado de cuenta, historial de
  mediciones, y un formulario para cargar una medición nueva desde el panel
  (sin tocar la base a mano ni usar la API directamente). El panel del
  negocio (`panel.html`) ahora linkea a cada socio desde la tabla de
  "pagos atrasados" y desde una tabla nueva de "Todos los socios".
- `seed_demo.py` actualizado: cada socio demo tiene altura, fecha de
  ingreso, un historial de peso (2-4 mediciones) y check-ins recientes —
  para que el dashboard tenga algo real que mostrar apenas se corre.

Probado: 24 tests nuevos (`tests/test_progreso.py` + agregados a
`test_gym_vertical.py` y `test_api.py`) — cálculo de BMI, clasificación,
progreso con y sin historial, frecuencia de asistencia, aislamiento por
negocio (un cliente de otro negocio da 404), y el flujo de punta a punta
"check-in por voz -> se refleja en el perfil del dashboard". Además probado
a mano contra el servidor real: perfil, historial de mediciones y la página
del dashboard cargan con los datos del gym demo. **62 tests en total, los
62 pasan.**

Pendiente: la carga de medidas es manual desde el panel (peso/cintura/etc.
con cinta métrica y balanza) — no hay integración con básculas inteligentes
ni wearables. El gráfico de evolución solo muestra peso; agregar más
series (cintura, etc.) es sencillo si Jorge lo pide.

## Fase 2e: suite de tests automatizados (11/09/2026, mismo día)

Hasta ahora todas las pruebas de este documento (aislamiento multi-tenant,
herramientas del vertical Gym, ciclo de facturación, endpoints HTTP) se
habían corrido a mano, con scripts sueltos de Python y `curl`. Se formalizó
todo eso en una suite de `pytest` real bajo `tests/`, para que cualquier
cambio futuro se pueda verificar en segundos en vez de rearmar las pruebas
manuales cada vez.

- `tests/conftest.py`: usa la variable de entorno `NEXXUS_DB_PATH` (agregada
  a `models.py` para esto) para que los tests corran contra su propio
  archivo SQLite (`test_nexxus.sqlite`) — nunca tocan `nexxus_core.sqlite`,
  la base de desarrollo/demo. Cada test arranca con las tablas vacías.
  También fija las credenciales de admin/operador y deja vacías las claves
  de Anthropic/Stripe, para que la suite sea 100% determinista: sin key de
  Anthropic el motor usa su fallback pre-grabado (no llama a la red, no
  gasta cuota real), y sin claves de Stripe la facturación corre en modo
  "solo local" — mismo comportamiento ya documentado más abajo.
- `tests/test_gym_vertical.py` (9 tests): las 3 herramientas del vertical
  Gym contra datos reales — la prueba central de que la IA nunca inventa un
  monto de pago (o encuentra el dato real, o dice explícitamente que no lo
  encontró).
- `tests/test_multitenant.py` (7 tests): dos negocios con el mismo número de
  teléfono de socio nunca se mezclan (ni en la identificación de quién
  llama, ni en las estadísticas).
- `tests/test_billing.py` (7 tests): ciclo completo de facturación SaaS —
  alta sin claves de Stripe, plan inválido, y el flujo pago fallido →
  período de gracia → (no vencido, no suspende) → (vencido, sí suspende) →
  pago exitoso → reactiva, más los dos casos del webhook.
- `tests/test_api.py` (15 tests): a nivel HTTP, usando el `TestClient` de
  FastAPI (corre la app in-process, sin levantar un servidor real ni pegarle
  con `curl`) — auth de admin vs. operador, alta de negocio/socio,
  actualización de socio, flujo completo de llamada (iniciar/mensaje/
  finalizar), negocio suspendido rechaza pedidos, páginas HTML cargan.

**38 tests, los 38 pasan** (`python3 -m pytest -v` desde `nexxus-core/`).

Un efecto secundario necesario: correr `TestClient` con `httpx==0.28.1` (la
versión que ya necesitábamos para `anthropic==1.2.0`) rompía con la
`starlette` vieja que traía `fastapi==0.104.1` — mismo tipo de conflicto de
versiones ya documentado para `anthropic`/`httpx`. Se subió `fastapi` a
`0.115.6`, verificado en este entorno que: (a) los 38 tests pasan, y (b) el
servidor de desarrollo sigue levantando y sirviendo normal (`/health`,
`/admin`, `/fuerza-total` — los mismos 3 chequeos que se vienen usando en
cada ronda de pruebas manuales de este documento). No cambia ningún
comportamiento de la API, es puramente una actualización de dependencia.

Se agregó `pytest` y `httpx` a `requirements.txt` (marcados como "solo para
desarrollo/tests", no hacen falta para correr el servidor en producción).

Primer avance de construcción real de la Fase 2, sobre la base del MVP de chat
(`recepcionista-ia-proyecto`). Esto es el motor + modelo de datos — todavía
sin Twilio (llamadas telefónicas reales) ni Stripe/PayPal conectados.

## Qué se construyó

1. **Modelo de datos multi-tenant** (`models.py`): se agregó `Negocio` (cada
   cliente de Jorge — un gym, una clínica, etc.), `ClienteNegocio` (el socio/
   paciente DEL negocio, con sus datos reales de membresía/pago) y
   `Suscripcion` (lo que Jorge le cobra al negocio por usar NEXXUS). `Llamada`
   ahora lleva `negocio_id`, así que todo queda aislado por negocio.

2. **Arquitectura de plantilla genérica** (`verticals/`): el motor no tiene
   ninguna lógica de gimnasios adentro. Cada vertical (por ahora solo `gym`)
   es un archivo que define sus propias instrucciones de IA y sus propias
   "herramientas". Agregar Restaurante o Clínica después es agregar un
   archivo, no reescribir el motor — tal como se había decidido en la
   planificación.

3. **Tool-calling real de Claude** (`generic_service.py`): esto es el cambio
   más importante. Antes (y en cualquier chatbot simple), el modelo podía
   "inventar" una respuesta a "¿cuánto debe este cliente?". Ahora el modelo
   está obligado a invocar una herramienta que consulta la base de datos real
   antes de contestar preguntas de pago — la respuesta siempre sale de un
   dato real, nunca de la memoria del modelo. Esto es exactamente lo que
   Jorge pidió originalmente por voz: "dame el reporte de este cliente a ver
   si ha pagado o no".

4. **Vertical Gym** (`verticals/gym.py`): 3 herramientas — consultar estado de
   cuenta/membresía (por nombre o teléfono), consultar horario de clases, y
   confirmar check-in (rechaza si la membresía no está activa).

5. **API multi-tenant** (`main.py`): mismos endpoints que el MVP anterior
   (iniciar/mensaje/finalizar/estadísticas) pero con el negocio en la URL
   (`/api/fuerza-total/iniciar`). Se agregó también un panel de estadísticas
   GLOBALES (`/api/admin/estadisticas-globales`) con su propia autenticación
   separada — es lo que va a usar Jorge como operador, distinto del login de
   cada negocio individual.

6. Se subió el SDK de `anthropic` (0.25.0 → 1.2.0) — la versión vieja no
   tenía tool-calling estable y era la que causaba el bug de `httpx`
   documentado en el MVP anterior. Verificado en este entorno que la versión
   nueva funciona sin ese conflicto.

## Qué se probó (en este entorno, sin API key real de Anthropic)

- Aislamiento multi-tenant: dos negocios distintos, mismo número de teléfono
  de cliente en ambos → cada uno lo identifica (o no) de forma independiente,
  las estadísticas no se mezclan.
- Las 3 herramientas del vertical Gym contra datos reales sembrados
  (`seed_demo.py`): buscar por nombre, por teléfono, socio inexistente (no
  inventa nada), horario de clases, check-in aceptado/rechazado según estado
  de membresía.
- API HTTP completa: negocio inexistente → 404, estadísticas sin auth → 401,
  con auth → 200, panel global del operador con su propia auth → 200, página
  de chat del negocio → 200.
- Sin API key, el flujo completo cae correctamente al fallback (mismo
  comportamiento ya probado en el MVP anterior).

## Qué NO se probó todavía (necesita la API key real de Jorge)

- Una conversación real donde Claude decida invocar `consultar_estado_cuenta`
  y arme una respuesta natural con el dato real — la lógica está armada y
  sigue el patrón oficial de tool-use de Anthropic, pero la prueba de punta a
  punta con el modelo respondiendo de verdad falta hacerla con la key real de
  Jorge (mismo paso que ya hicimos con el MVP: probarlo en su Mac).

## Pendiente (no es parte de este avance)

- Conectar Twilio (llamadas telefónicas reales) — el motor ya está separado
  de la capa de transporte, así que cuando Twilio esté aprobado se agrega un
  router de webhooks de voz que llama a los mismos métodos del servicio.
- Conectar Stripe/PayPal de verdad — el código de facturación ya está (ver
  abajo), falta la cuenta de Stripe real de Jorge para probarlo con dinero
  de prueba en serio (hoy corre "a ciegas" en modo local si no hay claves).
- Adaptar el frontend (`recepcionista.html` / `admin_recepcionista.html`) al
  modelo multi-tenant y al panel de operador — por ahora el HTML del MVP
  anterior se sirve reescribiendo las URLs de la API al vuelo (funciona para
  probar, pero el dashboard visual todavía no refleja las estadísticas
  nuevas ni el panel de Jorge). Hay un boceto visual de cómo debería verse
  cada uno (enviado aparte) — falta construirlo de verdad, conectado a datos
  reales de la API.
- Tabla de check-ins históricos (hoy `registrar_checkin` confirma pero no
  guarda un registro persistente de asistencias).
- Envío real de los recordatorios de pago (7/3/1 días antes del cobro) —
  hoy la lógica de gracia/suspensión/reactivación está lista y probada, pero
  el envío de email/SMS del recordatorio en sí todavía no está construido.
- Automatizar `chequear_suspensiones_vencidas()` con una tarea programada
  diaria — hoy es un endpoint que hay que llamar manualmente.

## Fase 2d: alta de negocios y socios sin tocar la base a mano (11/09/2026)

Hasta ahora la única forma de cargar un negocio nuevo o sus socios era
`seed_demo.py` — un script de prueba, no algo que sirva para dar de alta un
cliente real. Se agregaron 4 endpoints:

- `POST /api/admin/negocios` (Jorge, operador): da de alta un negocio nuevo
  — slug, nombre, vertical, idiomas, nombre de la asistente, plan. Si se le
  pasa email de facturación, arranca también la suscripción de Stripe (modo
  test) en el mismo paso. Rechaza slugs repetidos (409) y verticales que no
  existen (400).
- `POST /api/{slug}/clientes`: carga un socio/cliente nuevo del negocio
  (nombre, teléfono, meses adeudados, próximo vencimiento, etc.)
- `PATCH /api/{slug}/clientes/{id}`: actualiza el estado de un socio — por
  ejemplo, registrar que pagó (baja `meses_adeudados` a 0, corre el próximo
  vencimiento). Sin esto, un socio cargado quedaba "congelado" para siempre.

Esto es lo que hace falta para poder cargar un gym real (el que Jorge dijo
que está en camino) sin que yo edite la base de datos a mano cada vez.

Probado de punta a punta: crear negocio, error si el slug ya existe, error
si el vertical no existe, cargar un socio, consultarlo, actualizarlo
(marcar como pagado) y confirmar que el cambio quedó guardado.

## Fase 2c: paneles conectados a datos reales (11/09/2026, mismo día)

Se agregaron 3 endpoints de lectura (`/api/{slug}/clientes`,
`/api/{slug}/llamadas-recientes`, `/api/admin/negocios`) y una página
(`panel.html`) que los consume en vivo — reemplaza el boceto visual
estático por un panel real, aunque todavía sin el diseño final del boceto
(esto es funcional, no la versión pulida).

- `/{slug}/admin` → panel del negocio (ej. `/fuerza-total/admin`): KPIs
  reales, llamadas recientes, y la lista de socios con pagos atrasados —
  mismos datos que usa la IA por tool-calling, ahora visibles sin pasar por
  el chat.
- `/admin` → panel de operador (Jorge): todos los negocios, su plan y
  estado de suscripción, mezcla de planes.
- Ambos piden usuario/contraseña (los mismos `ADMIN_USER`/`ADMIN_PASSWORD`
  y `OPERADOR_USER`/`OPERADOR_PASSWORD` del `.env`).

Probado: los 3 endpoints nuevos con datos reales del gym demo, y que ambas
páginas HTML cargan (200).

Pendiente: unificar esto con el diseño visual del boceto (Artifact) — hoy
son funcionalmente equivalentes pero visualmente más simples.

## Fase 2b: facturación SaaS con Stripe (11/09/2026, mismo día)

Se agregó `billing_service.py` — separado por completo del motor de IA:
esto es lo que Jorge le cobra a cada negocio por usar NEXXUS (Starter $99 /
Professional $199 / Enterprise $499), NO lo que cada negocio le cobra a sus
propios clientes.

Implementado y probado (con datos simulados, sin cuenta de Stripe real
todavía — mismo enfoque que con Twilio): alta de suscripción por negocio,
webhook de Stripe (`POST /webhooks/stripe`) que distingue pago exitoso /
pago fallido / cancelación, período de gracia de 3 días desde el primer pago
fallido, suspensión automática al vencer la gracia (`negocio.activo=False`,
que ya hace que la API rechace pedidos de ese negocio — mismo mecanismo que
usa el resto del sistema), y reactivación automática al recibir un pago
exitoso.

Corre en modo test — no necesita que la empresa de Jorge esté formada. En
cuanto tenga su cuenta de Stripe, solo hace falta completar las variables en
`.env` (`STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, y los `price_id` de
cada plan) — el código ya está listo para usarlas.

Probado: crear suscripción sin claves de Stripe (crea el registro local sin
romper nada), plan inválido rechazado, ciclo completo pago fallido → período
de gracia → (todavía no vencido, no suspende) → (vencido, sí suspende,
negocio queda inactivo) → pago exitoso → reactiva. Todo también probado a
través de la API HTTP real (con autenticación de operador).
