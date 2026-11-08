# AITA Car — prototipo funcional, 11 de octubre de 2026

Nueva vertical de AITA / Nexxus, preparada en una copia aislada de `nexxus-core.`.
No se han subido cambios, creado PR, fusionado, aplicado migraciones ni desplegado.

## Estado del repositorio revisado

- Repositorio verificado: https://github.com/jorgeabalo/nexxus-core.
- `main` consultado en GitHub: `91eb01187cc04908fcfb0390eed881d9a23176f0`, merge del PR #19 de Marketing.
- La copia local encontrada estaba en `feature/marketing-reel-studio`, commit `c758014`, con cambios sin guardar de Marketing. No se modificó esa copia.
- La nueva copia parte del `main` verificado y usa la rama local `feature/aita-car-prototype`.
- Arquitectura existente: FastAPI, servicios independientes, SupabaseAdmin, JWT de Supabase, membresías `tenant_users`, habilitación de módulos en `tenants.modules` y frontend sin framework. El README general tiene referencias al antiguo MVP de un solo negocio; los servicios actuales sí tienen puertas de acceso multi-tenant.
- Car sigue el patrón de servicio y router, pero arranca con su propio servidor. No se monta en `main.py` para evitar que el modo demo se exponga en la aplicación real.

## Ejecutar la demo

Desde la raíz de esta copia:

```sh
python3 -m venv work/car-env
work/car-env/bin/pip install -r requirements-car.txt
AITA_CAR_DEMO=1 work/car-env/bin/python -m car.demo
```

Si las dependencias ya están disponibles: `AITA_CAR_DEMO=1 python3 -m car.demo`.
Abrir http://127.0.0.1:8011/car. Documentación de API en `/docs`.
Escucha exclusivamente en loopback. No usar túneles, proxies públicos ni servidores externos con esta entrada.
Los datos persisten en `work/aita-car.sqlite`; son locales y no cifrados. Las sesiones caducan en una hora y se invalidan al reiniciar el servidor. El prototipo no carga `.env`, claves ni servicios externos.

## Lo que funciona

| Área | Comportamiento |
|---|---|
| Perfil / vehículos | Nombre del conductor; hasta 10 vehículos por cuenta con marca, modelo, año, odómetro, MPG, precio por galón y próximo aceite |
| Viajes | Iniciar, registrar puntos, reanudar GPS tras recarga, finalizar o cancelar; una sesión activa por vehículo |
| GPS | GPS del navegador con permiso; millas por Haversine; rechazo de precisión >50 m, fechas futuras, puntos repetidos/fuera de orden y saltos >130 mph; filtro de movimiento menor a la incertidumbre |
| Simulación | Ruta de Chicago con puntos ficticios; nunca incrementa el odómetro real |
| Millaje | Viajes GPS finalizados incrementan una sola vez el odómetro estimado; no sustituye la lectura del tablero/ECU |
| Gasolina / peajes | Gasolina = millas / MPG × USD/galón; valores congelados al inicio del viaje. Peajes estimados introducidos manualmente, sin proveedor de tarifas |
| Gastos | Registro manual de gasolina, peajes, mantenimiento y otros, con importe, nota y fecha; separado de costos estimados de viajes |
| Mantenimiento | Recordatorio de aceite por vehículo y millaje; registro del servicio y siguiente intervalo personalizado. Sin catálogo oficial por fabricante |
| OBD | RPM, refrigerante, voltaje y código P0442 ficticios; etiquetados como simulados, no conectados |
| Estacionamiento | Último punto al finalizar viaje o ubicación GPS manual; fecha, origen y enlace a Google Maps a petición del usuario |
| Seguridad Pro | Control de acceso en backend, historial de alertas simuladas de movimiento; sin monitoreo, notificaciones push, respuesta de emergencia ni inmovilización |
| Nexxus | Respuesta local basada en vehículo, gastos, recordatorio y estacionamiento. Reconocimiento y lectura de voz opcionales según navegador; sin LLM ni consumo de API |
| Planes | Básico USD 19.99/mes; Pro USD 29.99/mes. Selector de demostración sin pagos ni suscripciones reales |

El navegador puede detener GPS al bloquear la pantalla o pasar a segundo plano. No se promete registro automático, reconexión ni funcionamiento en todos los carros. Para probar en un teléfono fuera de este equipo será necesaria una etapa posterior con HTTPS e identidad real: el servidor de demo no se publica.
El reconocimiento de voz puede usar servicios del proveedor del navegador. No se graba audio en el backend.

## Módulos y seguridad

- `car/domain.py`: contratos y validación de entrada; fórmula de distancia.
- `car/store.py`: persistencia SQLite, transacciones y bloqueo entre hilos; claves y búsquedas por tenant + vehículo. La clave foránea compuesta impide asociar registros con vehículos de otra cuenta.
- `car/api.py`: operaciones de negocio. El tenant viene de la dependencia de identidad del servidor; se rechazan campos extra en cuerpos. Acceso al vehículo antes de operar; 404 para recursos ajenos. Finalización idempotente.
- `car/demo.py`: identidad ficticia de dos cuentas seleccionables, cookies aleatorias HttpOnly/SameSite Strict, sesión de una hora, control de origen y Host, sin caché, CSP, límite de cuerpo de 16 KiB y servidor loopback. La cookie no tiene Secure porque es HTTP local; jamás usar esta entrada en producción.
- `car/auth.py`: puerta de integración con `SupabaseAdmin.user_from_jwt`, membresía activa owner/manager y `tenants.modules.car === true`. No está conectada a la demo. `X-Aita-Tenant` solo selecciona la cuenta: no concede acceso sin membresía. Las pruebas usan una identidad Supabase ficticia; todavía no se ha probado contra Supabase real.
- `car/adapters.py`: contratos `OBDAdapter`, `ProjectionAdapter` y `NexxusAdapter`; implementación local y adaptadores futuros deshabilitados.
- `car/assets/*`: interfaz móvil adaptable, sin frameworks, CDN, imágenes externas ni claves en navegador. Los datos de usuario se muestran con `textContent`.

**Alcance del aislamiento:** tenant representa una cuenta/hogar. Los miembros owner/manager de la misma cuenta compartirán vehículos; no hay subpermisos por conductor implementados. La demo permite elegir A/B porque son cuentas ficticias, no usuarios reales. No es un login SaaS.
SQLite es adecuado para esta demo local, no una garantía de aislamiento RLS en Supabase. El plan solo puede cambiarse en el endpoint `demo-plan` del router demo; ese endpoint no existe en el router no demo.

## Implementación incremental propuesta

1. **Ahora — demo local:** interfaz, operaciones, persistencia, pruebas y contratos. Validar contigo navegación, límites Básico/Pro, mantenimiento y modelo de cuenta familiar frente a flota.
2. **Integración Nexxus sin hardware:** repositorio/almacenamiento Supabase con migración revisada; tablas Car con `tenant_id`, RLS, claves compuestas, RPC transaccional de finalización de viajes y permisos mínimos. Montar router solo con JWT real + módulo habilitado. Crear perfil Car explícitamente (la demo lo crea con datos ficticios). Usar registro de suscripción verificado por servidor; ningún usuario eleva su plan. Probar JWT reales, revocación, roles y dos tenants en PostgreSQL. Revisar PR y autorizar cualquier aplicación de migraciones.
3. **Nexxus conversacional real:** conectar el proveedor existente mediante `NexxusAdapter`; herramientas de lectura con contexto autorizado, presupuesto y límites por cuenta. Respuestas sin inventar datos del vehículo. No enviar rutas completas al modelo por defecto. Instrumentar gastos sin almacenar audio ni secretos.
4. **App móvil nativa:** decidir iOS/Android o capa nativa compartida; permisos GPS/BLE, persistencia offline, sincronización idempotente y límites de batería. Añadir hora/antigüedad a cada muestra, tolerancia a desconexión y ajuste manual auditado del odómetro. Probar rutas conocidas contra referencia para cuantificar error de millaje y comportamiento con pantalla bloqueada.
5. **Hardware y proyección:** validar vLinker/otro OBD elegido, BLE GATT, comandos ELM327, PIDs disponibles por vehículo y reconexión. Ottocast es un producto distinto, no un API universal; obtener modelo/firmware antes de implementar. Diseñar CarPlay/Android Auto por categoría admitida y completar solicitudes/revisión de plataforma antes de afirmar integración. Registrar una matriz probada teléfono/SO/auto/adaptador/firmware; sin compatibilidad universal.
6. **Pro y lanzamiento:** sensores/proveedor de alertas reales, notificaciones con consentimiento, antifalsas alarmas, privacidad de ubicación, retención/borrado y auditoría. Pagos con eventos verificados del proveedor, límites del plan y revocación. Revisión de seguridad y pruebas end-to-end antes de solicitar autorización para desplegar.

No se añade una migración Supabase ejecutable en esta fase; falta decidir el alcance de cuenta/usuario y trasladar la transacción SQLite a PostgreSQL. La puerta JWT preparada facilita esa integración, pero no basta por sí sola para publicar el prototipo.

## Pruebas y demo de revisión

```sh
python3 -m pytest --noconftest tests/car -q
```

La suite de Car usa SQLite temporal y TestClient, sin red ni credenciales. Cubre separación A/B en todos los recursos, origen/Host, sesión y logout, plan Pro, estado de viajes, cancelación, precisión/ruido GPS, idempotencia, costos, persistencia, validación, mantenimiento, respuestas Nexxus y puerta JWT/módulo con dobles de prueba.
`--noconftest` evita cargar las integraciones de otras verticales. No se ha ejecutado la suite completa del repositorio ni aplicado RLS real; `main.py` no cambia.

Guion de demo (~5 minutos):

1. Entrar con Jorge, revisar el Sportage y sus parámetros en Perfil.
2. Viajes → Probar viaje simulado → introducir peajes → Finalizar. Ver millas/costo y estacionamiento; odómetro conservado.
3. Cuidado → guardar gasolina de USD 40 → registrar aceite con intervalo definido por el usuario → leer OBD simulado.
4. Nexxus → preguntar por gastos y por aceite. Activar voz opcionalmente.
5. Resumen → intentar alerta Pro con Básico (rechazada) → cambiar al plan Pro → guardar alerta simulada.
6. Salir y entrar con Cliente B: comprobar que no ve viajes, gastos ni alertas de Jorge.
7. Opcional: GPS real con permiso del usuario, caminando o como pasajero. Finalizar/cancelar antes de salir. Si hay viaje pendiente tras recargar, reanudar o cancelar.

## Fuentes de restricciones de plataforma

- Apple: https://developer.apple.com/documentation/carplay/requesting-carplay-entitlements
- Android: https://developer.android.com/training/cars/apps/library

Consultadas el 11 de octubre de 2026. CarPlay requiere el entitlement correspondiente; Android Auto exige categoría y diseño admitidos. Este prototipo no tiene ninguna de esas integraciones aprobadas.

## Resultado de verificación de esta entrega

- **26 pruebas aprobadas** (0 fallos; aviso de deprecación de una dependencia de TestClient).
- Sintaxis JavaScript y comprobación de diferencias sin errores.
- Demo real en navegador: login A, viaje simulado de 2.95 mi, gasolina estimada USD 0.39, peajes USD 2.50, estacionamiento simulado y odómetro real conservado; gasto manual USD 40 y Nexxus consultándolo; Básico rechaza alerta Pro, Pro genera alerta simulada.
- Cambio a cuenta B: cero gastos y cero millas; respuesta anterior de Nexxus eliminada. Cuenta A conserva sus registros al volver.
- Vista móvil de 390 × 844: ancho del documento 390, sin desbordamiento horizontal. Captura entregada por separado.
- GPS físico, micrófono y lectura de voz no probados: requieren permisos/dispositivo del usuario. Supabase real, hardware, segundo plano y plataformas del carro no probados ni conectados.

## Diseño cockpit

Interfaz actualizada a negro, rojo y blanco, con marca gráfica AITA en la cabecera, iluminación roja, instrumentos circulares para millas/gastos/mantenimiento y controles oscuros. Los aros son decorativos: no indican velocidad ni valores de telemetría en vivo. Se conserva la identificación de datos simulados. Vista móvil comprobada a 390 px sin desbordamiento horizontal. No cambia la lógica del backend.


## Idiomas: English / Español

Selector disponible en la cabecera antes y después de entrar. Preferencia guardada localmente por navegador, con español inicial. Pantallas, botones, avisos, historial y errores se traducen; notas y nombres introducidos por el usuario conservan su contenido. Formato de fechas y voz usan en-US/es-US. Nexxus recibe language=en|es validado por backend y responde en el idioma elegido. Al cambiar idioma se limpia la respuesta anterior y se conservan los borradores de perfil/vehículo. No se reinicia el viaje GPS. Verificación actual: **32 pruebas aprobadas**, incluida selección de idioma y respuestas contextuales bilingües. Cambio de idioma y respuesta inglesa verificados en navegador; micrófono físico no probado.


## Onboarding del dueño y unidades

Al entrar por primera vez se muestra un registro obligatorio en la interfaz, antes del cockpit: nombre completo, dirección, teléfono, marca/modelo, año y lectura inicial del odómetro, con selección mi/km. El servidor valida, guarda dueño + vehículo en una transacción y marca onboarding_completed; no permite repetir el registro ni cambiar el plan desde el formulario. Conserva la lectura inicial, su unidad original y fecha. Las cuentas de demo anteriores también deben completar el registro; sus vehículos/registros de ejemplo se conservan separados del vehículo nuevo.

Los cálculos siguen en millas internamente; km se convierte con 1 mi = 1.609344 km. Cockpit, historial, edición, intervalos de mantenimiento y Nexxus presentan la unidad elegida para el vehículo. Las estimaciones iniciales de consumo/precio/aceite se explican en el formulario y son editables. Dirección/teléfono son datos locales de la cuenta y no se incorporan al contexto de Nexxus. El backend demo sigue disponible para pruebas directas sin onboarding; el bloqueo del cockpit es de interfaz. La etapa de identidad/almacenamiento real debe aplicar el estado de alta en la puerta de acceso de producción.

Verificación actual: **41 pruebas aprobadas**. Se probó el flujo completo en navegador con datos ficticios y 80,000 km: guardado, apertura del cockpit y conversión correcta. Persistencia, validación atómica, aislamiento y prohibición de repetir el alta cubiertos por tests. No se desplegó nada.


El onboarding explica que el dueño puede empezar en Básico (USD 19.99/mes) y pasar a Pro (USD 29.99/mes) posteriormente conservando sus registros. Perfil incluye el acceso a probar Pro; no cobra ni crea suscripciones reales. Ambos idiomas incluyen el aviso de hardware/disponibilidad y seguridad simulada.


## Centro de apps: primera etapa

Apps contiene accesos oficiales a Google Maps, Waze, Apple Maps, Spotify, Pandora y Amazon Music. Los mapas reciben un destino escrito voluntariamente, codificado con URLSearchParams. Nunca se usa automáticamente la dirección del onboarding o GPS. El destino se borra al cambiar de cuenta/vehículo. No hay claves, OAuth, reproducción remota, iframe ni comunicación automática con servicios. Los enlaces se abren en otra pestaña con noopener/noreferrer; teléfono/SO decide si los entrega a una app instalada.

Alexa figura como integración pendiente; no existe conexión de cuenta ni envío de comandos. Etapas siguientes: navegación nativa/deep links probados por plataforma, autorización de cada proveedor de música para control admitido, y vinculación de cuentas/skill para Alexa. AITA Car no controla apps de terceros ni obtiene aprobación CarPlay/Android Auto por añadir estos accesos.
Fuentes: https://developers.google.com/maps/documentation/urls/get-started · https://developers.google.com/waze/deeplinks · https://developer.apple.com/documentation/mapkit/unified-map-urls

## Tema claro y oscuro

Selector Claro/Oscuro en la cabecera, traducido Light/Dark. Preferencia local persistente; oscuro inicial para conservar el cockpit. El tema claro usa fondos blancos/grises, texto oscuro y acentos rojos; cambia logo, formularios, instrumentos, Apps, onboarding y Nexxus. Se comprobó en navegador que Claro permanece después de recargar.

Model selection now offers 14 editable presets including Kia Rio 2023. Only Sportage 2021, Corolla 2022 and Rio 2023 have model-specific illustrations; other models retain the labelled generic fallback. Selecting a preset does not save a vehicle or set an odometer.

## Make/model catalog
CarGurus Car Selector API snapshot requested 2026-10-11, USED catalog, using its documented listMakes and listModels endpoints. Source: https://www.cargurus.com/Cars/developers/docs/CarSelector.html . Names only; no listing content, images, prices, owner data or GPS are transmitted. Local browser suggestions filter models by make; users may enter unlisted names. This snapshot does not establish year availability, trim, maintenance specifications or hardware compatibility. Production API access/reuse terms and refresh policy remain to be confirmed.

## Visual cockpit refresh
Large touch controls, six section keys, local clock, OBD simulation status, active trip indicator, oil reminder gauge and seven-day distance bars derived from finished trips (including simulated trips). No speed, fuel level or live sensor values are fabricated. Nexxus pixel nebula remains available across tabs with a direct assistant shortcut; reduced-motion preferences disable its animation. Light/dark themes retained.
Market release remains blocked on production authentication/tenant enforcement, payment integration with the AITA core, privacy controls and provider disclosures, real AI credentials, native background/voice validation, live navigation providers and hardware/projection testing. This update does not implement those services or deploy.

## Maps and Android Auto work — 2026-10-11
Embedded real OpenStreetMap map is opt-in and disabled initially. Example coordinates are public Chicago coordinates; actual GPS is requested only via explicit share-location action with provider disclosure. Closing the map, switching accounts/vehicles or losing the session removes its src. Pending GPS results are rejected after context changes. Name/address/phone are never sent to the map provider. CSP frame-src permits only https://www.openstreetmap.org; other script/connect restrictions remain unchanged. This is a map viewer, not a route engine or live traffic integration. Google Maps searches for gas/EV charging delegate results and availability to Google, and do not automatically transmit AITA GPS/profile data.

Native Android Auto POI source is in android/. It shows public example landmarks with a host-rendered PlaceListMapTemplate and delegates navigation via CarContext.ACTION_NAVIGATE. No SDK/Gradle was available, so no APK/build/device validation is claimed. It has no backend session, real POI feed, voice or account data. See android/README.md for toolchain and next tests. Native app readiness remains pending.
Sources: https://wiki.openstreetmap.org/wiki/Export and https://developer.android.com/training/cars/apps/poi .
Verification: browser loaded real map controls/attribution, close removed iframe src; backend tests41 passed. GPS/hardware not exercised.

Nexxus presence enlarged: persistent 116px desktop control and full-width 98px mobile dock, larger pixel nebula and assistant-panel animation. Reduced motion preference still respected. This changes presence/access only; real AI connection remains pending credentials.

Nexxus artwork now reuses the existing Domus SVG nebula (620 pixels per instance), with unique gradient IDs, breathing, floating and twinkling animations.

Nebula depth effect:620 existing Domus SVG particles split into three animated orbit planes with different scale, rotation, opacity and blur. This is an illustrated 3D effect, not a WebGL model. Reduced motion preserves static depth.

Nebula motion changed to a co-rotating vortex: outer/middle/inner particle planes use15/9/5-second loops with elliptical compression and radial breathing around a stable core.

## Voice/text expense commands
Nexxus accepts simple explicit expense commands with numeric amounts (Spanish/English):“Gasté45 dólares en gasolina”,“Pagué80 por cambio de aceite”,“I paid12.50 in tolls”. Browser speech transcript and typed text use the same local parser. A vehicle/category/USD amount/note preview requires a button confirmation before posting to the tenant-scoped expense API. Switching vehicle/account/language cancels drafts. No external AI dependency; free conversation still pending credentials. Written number words and complex commands are not supported. Oil expenses do not silently mark maintenance complete; user records maintenance separately in Care.6 parser checks passed; browser confirmed the pending preview; physical microphone/iPhone not tested.

Alexa app hub entry links to the official iOS App Store listing https://apps.apple.com/us/app/amazon-alexa/id944011620 . No undocumented URL scheme, Alexa embedding, microphone access, account linking or command transmission implemented. Alexa for Apps documentation describes Alexa-to-app deep links and must not be represented as a Nexxus-to-Alexa control API.

### Configuración de conectores
Preferencias por cuenta para mapas, música, OBD, pantalla y Nexxus. Mapas/música abren URLs oficiales. No se guardan claves ni tokens; elegir OpenAI no habilita el proveedor. OBD nuevo usuario devuelve sin conexión sin datos ficticios; viajes/alertas simulados bloqueados para usuarios nuevos. CarPlay, BLE, Alexa y pagos requieren implementación real; esta apariencia no certifica preparación comercial.

### Autorización de cobros
Por instrucción de Jorge: todas las cuentas permanecen sin cobro durante las pruebas hasta nueva autorización explícita. Dashboard expone modo trial y pagos deshabilitados. Los precios son de referencia; cambiar de plan no inicia suscripción. No crear cobros, recopilar tarjetas ni activar proveedor de pagos automáticamente. La activación futura requiere autorización del dueño y completar la integración real.
