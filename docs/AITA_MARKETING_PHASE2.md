# AITA Marketing — Fase 2: Biblioteca privada, Estudio de Reels y arquitectura de IA

Estado: **código y migración en un PR en borrador**. La migración NO se ha ejecutado en ningún entorno real,
no hay proveedores reales activos, no hay claves, no se gasta nada y no se publica nada.

## 1. Arquitectura

```
Navegador (Manager Panel, #/marketing/library | studio | jobs)
   │  JWT de Supabase + tenant_id                     (nunca claves ni URLs permanentes)
   ▼
FastAPI  services/marketing_studio_routes.py
   │  LibraryService / StudioService  ──►  MarketingService.ctx()   (puerta única de Fase 1)
   │        sesión → owner/manager del tenant → tenants.modules.marketing === true → settings
   ├── marketing_media_files.py   validación de archivos (extensión + MIME + firma real), rutas
   ├── marketing_privacy.py       personas reales, clases de privacidad, detección local sin identidad
   ├── marketing_mix.py           mezcla real/IA → escenas y segundos (resto mayor)
   ├── marketing_reel_plan.py     asistente de 7 pasos, subtareas y estimación de coste
   ├── marketing_ai_router.py     MarketingAIRouter + adaptadores (Mock, OmniRoute-contrato, FFmpeg local)
   ├── marketing_ai_catalog.(py|json)  catálogo central de modelos (sin claves)
   ├── marketing_worker.py        JobRunner: reclama y ejecuta trabajos (preparado para worker/cola)
   └── marketing_jobs_domain.py   estados de archivos y trabajos, límites, errores públicos
   ▼
Supabase (service role, solo backend)
   ├── tablas marketing_media, *_media_events, *_derivatives, *_generation_jobs, *_job_events, *_inputs, *_outputs, *_model_usage
   └── Storage privado marketing-assets: {tenant}/originals/{asset}/{nombre} · {tenant}/derivatives/{asset}/{id}.{ext}
```

Cada módulo Python tiene menos de 500 líneas y no hace red salvo `supabase_admin.py` (Storage) y, en el
futuro, un transporte de OmniRoute inyectado explícitamente.

## 2. Flujo de datos (demostrable hoy con el mock)

1. **Subir** (owner/manager) → el backend calcula el límite **antes de leer el cuerpo** (rol, módulo, tamaño
   por archivo y espacio libre), corta la lectura al superarlo, valida extensión, MIME declarado, firma real y
   estructura (truncados, polyglot, metadatos imposibles); construye la ruta; sube con `x-upsert: false`;
   guarda checksum, quién y cuándo, `validation_status = passed` y `malware_scan_status = unavailable`.
2. **Clasificar** personas y menores: `contains_people`, `contains_minors` (sí/no/no sé) + `people_policy` + `consent_status`.
3. **Elegir mezcla** real/IA (preset o personalizada en pasos de 5 %), ver la aproximación en escenas/segundos.
4. **Guion** (gancho, mensaje, CTA) con filtro de afirmaciones prohibidas.
5. **Estimar**: el router elige modelo por subtarea → `awaiting_generation_approval` con coste estimado.
6. **Aprobar generación** (casilla de confirmación explícita) → se comprueban límites del mes → `queued`.
7. **Procesar (mock)** → `processing` → `succeeded`, con escenas por origen, uso por modelo y coste 0.
8. **Revisar** escenas → **Enviar a aprobación**: crea el contenido (formato reel) y lo pasa a `review` en el
   flujo de Fase 1. Desde ahí sigue la aprobación humana; **no se programa ni se publica nada**.

## 3. Permisos

| Quién | Biblioteca | Estudio / trabajos |
|---|---|---|
| owner / manager del tenant (módulo activo) | ver, subir, clasificar, anonimizar, archivar | crear, estimar, aprobar, procesar, cancelar, enviar a aprobación |
| owner / manager con `marketing_enabled=false` | solo ver | solo ver |
| staff, socios, anónimos | 403 (sin tocar tablas) | 403 |
| otro tenant | 404 / no visible (RLS + filtro por tenant en cada consulta) | igual |

* La puerta es la misma de Fase 1 (`MarketingService.ctx`); una prueba verifica que **todos** los métodos públicos
  nuevos la usan.
* Todo id que manda el navegador se vuelve a buscar filtrando por `tenant_id`. Las entradas de un trabajo
  referencian archivos con clave compuesta `(id, tenant_id)`: la base de datos impide mezclar tenants.
* RLS: owner/manager **leen** su tenant; nadie escribe directamente (solo el service role tras validar).
  Privilegios mínimos: PUBLIC/anon nada, authenticated solo SELECT, service_role SELECT/INSERT/UPDATE/DELETE.
* El estado de un trabajo se consulta revalidando la sesión y el rol en cada llamada.

## 4. Personas reales

| Política | Significado | ¿Puede ir a un proveedor externo? |
|---|---|---|
| `exclude` (por defecto si no se sabe) | no se usa con IA | **No** (ni siquiera como entrada de un trabajo) |
| `anonymize` | se usa solo un **derivado** anonimizado real y revisado por una persona (hoy no existe: es simulado) | Solo el derivado real revisado |
| `consented` | hay consentimiento registrado (`consent_status = granted`) | Sí, solo a modelos que admiten personas reales, con retención conocida y uso comercial |
| `no_people` | confirmado que no hay personas (`contains_people = false`) | Sí |

**Menores**: si hay o puede haber personas y no se ha confirmado `contains_minors = false`, el archivo queda
**excluido** (Python y `check` en la tabla). Tampoco puede marcarse “sin personas” con menores.

**Consentimiento retirable**: “Retirar consentimiento” pone `consent_status = revoked` y `people_policy = exclude`
(la tabla exige ambas cosas juntas) y queda en la auditoría. Efecto inmediato: no se crean trabajos nuevos con
ese archivo; los pendientes fallan al aprobar (`consent_revoked`) y, si ya estaban en cola, el worker los marca
como fallidos **antes** de enviar nada; el trigger impide además pasarlos a `queued`/`processing`.

Clases de privacidad: `synthetic_only`, `business_media_no_people`, `anonymized_people`, `consented_people`,
`restricted`. **`restricted` nunca sale del servidor**; solo pueden procesarlo herramientas locales.

### Límites de la anonimización (se muestran siempre en la interfaz)

* Pixelar o difuminar caras **no garantiza** el anonimato.
* Cuerpo, tatuajes, uniforme, voz, ubicación o contexto pueden identificar a alguien.
* La empresa es responsable de tener la autorización de las personas.
* El original nunca se altera: el resultado es un derivado en `derivatives/`.
* Si la detección no es fiable (confianza < 0,85 o detector no disponible) se **detiene** y se exige revisión
  humana. En producción no hay detector local instalado, así que **siempre** se pide revisión.
* Un derivado anonimizado solo puede quedar `ready` con `reviewed_by` y `reviewed_at` (lo impone la base de datos).
* Sin reconocimiento facial: el detector solo devuelve posiciones, nunca identidades ni comparaciones biométricas.
* Métodos previstos: `pixelate_faces`, `blur_faces`, `crop_people`, `silhouette`, `replace_background_and_people`.
* **En esta fase la anonimización es simulada** y nunca se marca nada como anonimizado: el derivado queda
  `mock_only` (detección simulada, `is_mock = true`) o `awaiting_processing` (sin detector), sin archivo.
  Un derivado simulado **no puede** aprobarse (`mock_derivative`), entrar en un trabajo (trigger
  `marketing_input_guard` + Python), salir hacia proveedores ni usarse en contenido. La interfaz lo muestra como
  “Simulado: NO anonimizado”.

## 4b. Borrado controlado

“Inmutable” significa que el original **no se sobrescribe** (ruta, checksum, tamaño, tipo, autor y fecha son
fijos), no que no pueda eliminarse. Eliminar (owner/manager, con confirmación y motivo):

1. se comprueba que ningún trabajo activo (`draft`, `awaiting_generation_approval`, `queued`, `processing`) lo
   use → si no, `media_in_use` (también lo impone el trigger);
2. se registra `delete` en `marketing_media_events` (solo inserción) con motivo, checksum y tamaño;
3. se ejecuta la **misma purga** que la retención automática (ver 4e): bloquea el acceso, borra original y
   derivados de Storage y deja la fila como registro mínimo (`deleted` + `purged`, con `deleted_by`).

La fila queda como registro (los trabajos terminados que la usaron siguen siendo auditables); un `DELETE` directo
está prohibido. El mismo archivo puede volver a subirse después.

## 4c. Validación de archivos y antivirus

* `validation_status` (formato) y `malware_scan_status` (antivirus) son campos **distintos**.
* Validación: extensión + MIME declarado + firma real; estructura completa (PNG con IEND, JPEG con EOI, GIF con
  trailer, tamaño RIFF de WebP, cajas MP4/MOV que no prometen más bytes de los que hay); búsqueda de contenido
  activo o contenedores escondidos (HTML, `<script`, SVG, PHP, `javascript:`, ZIP) en ventanas de 128 KB al
  principio y al final; dimensiones 1–20 000 px; duración ≤ 15 min (configurable) y coherente con el tamaño.
* Parsers acotados: JPEG solo en el primer MB, MP4 con profundidad ≤ 4 y ≤ 4096 cajas; nunca se decodifica la imagen.
* **No hay antivirus.** Sin escáner, `malware_scan_status = unavailable` (nunca `clean`; la tabla exige escáner
  y fecha para `clean`). Material real sin `clean` **no puede** ir a un proveedor externo (router).
* Antes de permitir proveedores reales hace falta: un escáner (p. ej. ClamAV en un servicio aislado) con firmas
  actualizadas, escaneo asíncrono en el estado `scanning`, cuarentena de `infected`, reintento de `error`,
  registro de `malware_scanner` y `malware_scanned_at`, y pruebas con EICAR.
* Los metadatos (EXIF, GPS) del original no se leen ni se guardan; quitar EXIF de los derivados queda pendiente
  para cuando haya procesamiento real.

## 4d. Vista previa, reproducción con HTTP Range y CSP

* El `<video>` no puede enviar la cabecera Authorization. La petición autenticada que autoriza la vista previa
  (`POST …/library/{id}/stream-session`, con el JWT) crea una **sesión opaca** de un solo archivo, ligada a
  tenant + usuario + archivo, y la coloca en una **cookie** `mk_stream`: `HttpOnly`, `Secure` en producción
  (solo se omite en localhost/127.0.0.1), `SameSite=Strict`, `Path` = la ruta **exacta** del archivo y
  `Max-Age` ≤ 600 s (`MARKETING_STREAM_TTL`, 60–600, 600 por defecto). **Secure no se decide con `Host`,
  `X-Forwarded-Host` ni la URL**: es obligatorio salvo con una configuración explícita de test/local
  (`APP_ENV` = `test`, `local` o `development` **y** `MARKETING_STREAM_COOKIE_INSECURE=1`). Sin `APP_ENV`, o con
  cualquier otro valor, es producción: si `MARKETING_STREAM_COOKIE_INSECURE` está activo, la aplicación **se
  niega a arrancar** (`RuntimeError` al construir las rutas). En la base de datos solo se guarda su hash
  (`marketing_stream_tokens`, solo service_role; la base exige caducidad ≤ 10 minutos).
* El reproductor usa una **URL limpia**, sin token: `GET|HEAD /api/manager/marketing/library/{id}/stream`. El
  navegador envía la cookie en HEAD, GET y cada Range (`Range: bytes=a-b | a- | -n` → 206 + `Content-Range` +
  `Content-Length` + `Accept-Ranges: bytes`; 416 si el rango es inválido) y puede avanzar y retroceder **sin
  descargar el archivo completo**. El rango se pide así a Storage y se transmite en trozos.
* Cada petición (HEAD y cada Range) revalida: sesión vigente, no revocada y de **ese** archivo, usuario todavía
  owner/manager activo del tenant, módulo Marketing activo, archivo validado, no eliminado ni pendiente de purga,
  y ruta del tenant. Sin cookie, nada (ni siquiera con el JWT).
* Revocación: al cerrar la vista previa (`POST …/stream-revoke`), al eliminar, al purgar y al retirar el
  consentimiento se revoca la sesión y las respuestas expiran la cookie (`Max-Age=0`, mismo Path). Si la sesión
  caduca durante la reproducción, el panel pide otra y continúa en el mismo segundo con la misma URL limpia.
* Como la URL ya no lleva secretos, desaparece el filtro de logs. Las respuestas llevan
  `Cache-Control: private, no-store`, `Referrer-Policy: no-referrer`, `X-Content-Type-Options: nosniff` y
  `Cross-Origin-Resource-Policy: same-origin`.
* CSP: ya no hace falta `blob:`; el vídeo queda cubierto por `default-src 'self'`, igual que en `main`.
  `GET|HEAD …/library/{id}/content` (con JWT) sigue disponible para clientes de la API.

## 4e. Retención y eliminación automática

Ningún archivo se guarda indefinidamente: `expires_at` es obligatorio y su **tope absoluto es 90 días desde la
subida**. Una publicación lo conserva hasta 30 días después de publicarse, pero
`expires_at = mínimo(published_at + 30 días, subida + 90 días)` (lo exige también la base). La protección por
trabajo activo añade como máximo 14 días. Nunca hay retención permanente.

| Caso | Retención |
|---|---|
| archivo sin usar | 30 días (o el máximo del plan si es menor) |
| derivado simulado / temporal | 7 días |
| resultados de trabajos simulados, fallidos o cancelados | 7 días |
| usado por un trabajo activo o una publicación programada | protegido como **máximo 14 días** desde que empieza la protección (`protected_until`); después el trabajo se cancela de forma idempotente (`timeout`), se solicita la purga y se purga (`workflow_timeout`) |
| publicación confirmada (`published`) | `expires_at` = mínimo(`published_at` + 30 días, subida + 90 días); luego se purga (`publication_done`) |
| consentimiento retirado | purga prioritaria; trabajos pendientes cancelados (reservas liberadas); resultados bloqueados para reutilización |

* El operador fija `max_retention_days` (7–90, por defecto 30). Owner/manager eligen 7, 30, 60 o 90 días desde la
  subida, nunca por encima del máximo y nunca "permanente" (también lo impone el trigger).
* Aviso 7 días antes (`expiring` + evento `expiry_warning`, una sola vez); la interfaz muestra la fecha exacta.
  Nunca se extiende automáticamente.
* Purga (`services/marketing_retention.py`, `RetentionRunner`): idempotente, aislada por tenant, reintentable
  (hasta 5 intentos; después queda `purge_failed` para el operador), segura si el objeto ya no existe (404),
  auditada (`purged`, `purge_failed`) e incapaz de borrar una ruta que no sea `{tenant}/…/{asset}/…` de ese archivo.
  Al empezar bloquea el acceso (`purge_pending`); al terminar borra original, derivados y resultados temporales y
  deja solo: id, tenant, hash, tipo, tamaño, quién subió, fechas y motivo (sin nombre, metadatos ni ruta real).
* **Archivo vencido = inaccesible aunque el purgador no haya corrido** (migración `20261012120000`): con
  `expires_at <= ahora` desaparece del listado (filtro en la consulta y en RLS), y abrir, descargar,
  previsualizar, abrir una sesión de reproducción, clasificar, archivar, cambiar retención, anonimizar o usarlo
  en un trabajo responde **410 `media_expired`**. Solo se admiten eliminarlo y retirar el consentimiento. Otro
  tenant sigue viendo 404 (sin oráculo). En la base: RLS lo oculta a `authenticated`, ningún trabajo lo acepta
  como entrada ni entra en cola con él, ninguna sesión de reproducción se crea para él (y ninguna dura más que
  el archivo) y su vencimiento ya no puede ampliarse. Sigue ocupando cuota hasta que se purga.
* **No hay tarea programada en producción**: `RetentionRunner` se ejecuta con el comando de §4g, pero nada lo
  programa todavía (lo comprueba una prueba). La única eliminación real posible es la que pide un owner/manager.

## 4g. Procesos de fondo: purgador y worker (preparados, sin programar)

Comando independiente del proceso web: `python -m services.marketing_runtime` (usa la clave de servicio de
`SUPABASE_URL`/`SUPABASE_SERVICE_ROLE_KEY`; imprime una línea JSON por lote, sin secretos).

**Purgador (`retention`)**
* **Dry-run por defecto**: lee de verdad y solo *registra* lo que escribiría (`would_write`). Escribe únicamente
  con `--apply`.
* Por lotes (`--batch-size`, `--max-batches`) y repetible: cada lote vuelve a leer el estado.
* Un solo purgador a la vez: lease `retention_runner` en `marketing_runtime_leases`
  (`marketing_acquire_runtime_lease`, TTL 15 min). Si otro lo tiene, devuelve `{"locked": true}` y no hace nada;
  si un purgador muere, su lease vence y otro lo retoma. No se usa `pg_advisory_lock` porque PostgREST reparte
  las llamadas entre conexiones del pool y un bloqueo de sesión no sería fiable.
* Orden de la purga: primero el acceso lógico (`purge_pending` + sesiones revocadas) y después el objeto de
  Storage; nunca una ruta que no sea `{tenant}/…/{archivo}` de ese archivo; si el objeto ya no existe (404) la
  purga se cierra; reintentos limitados (5) con `last_purge_error`.
* Trabajos atascados (`marketing_timeout_jobs`): lease vencido sin reintentos, o aprobado hace más de 24 h sin
  terminar → `failed`/`timeout`; sus resultados quedan bloqueados (`review_status = rejected`) y sus reservas se
  cierran una sola vez (costo: deja de contar lo reservado; espacio: disparador). Reservas abandonadas
  (`marketing_expire_storage_reservations`): subidas 15 min, trabajos 24 h.

Comando futuro de **Railway Cron** (NO creado ni activado; servicio aparte con el mismo repositorio y variables):

```bash
python -m services.marketing_runtime retention --apply --batch-size 200 --max-batches 10
```

Frecuencia sugerida: cada 15 minutos (`*/15 * * * *`). Antes de activarlo: ejecutar a mano sin `--apply` y revisar
la salida.

**Worker (`worker`)**
* Solo arranca con `MARKETING_WORKER_ENABLED=true`; se niega si `OMNIROUTE_ENABLED` o
  `MARKETING_LOCAL_TOOLS_ENABLED` están activos (este PR no conecta proveedores ni ejecuta FFmpeg, MoviePy,
  Playwright, ComfyUI, Wan ni OmniRoute).
* Reclamo atómico: `marketing_claim_job` (`FOR UPDATE SKIP LOCKED` + lease de 30–900 s): dos workers nunca toman el
  mismo trabajo. Heartbeat (`marketing_job_heartbeat`) antes de cada subtarea y antes de registrar resultados; si
  el lease se perdió, el worker se detiene sin escribir nada más. Solo quien tiene el lease vigente puede
  terminarlo con éxito o registrar resultados (disparadores de la base); un worker obsoleto no libera nada ajeno.
* Lease vencido → reintento por otro worker (máx. 3 intentos) con la **misma** `idempotency_key`: las subtareas ya
  cobradas no se repiten. Sin reintentos → timeout (arriba).
* Antes de gastar vuelve a comprobar: entradas (vencidas, consentimiento, menores…), generación habilitada y
  "Marketing AI budget" > 0. Si la entrada ya no vale al reclamar, el trabajo falla con `media_not_ready` sin
  bloquear la cola.

Comando futuro (servicio worker de Railway o Cron; NO creado ni activado):

```bash
MARKETING_WORKER_ENABLED=true python -m services.marketing_runtime worker --max-jobs 20 --lease-seconds 120
```

## 4f. "Marketing AI budget" (costo de los trabajos de Marketing)

Migración `20261011140000_marketing_generation_budget.sql`. **El límite implementado es exclusivamente el
"Marketing AI budget"**: limita solo el gasto de IA de los trabajos de Marketing. **No es** el presupuesto global
de 80 USD por tenant (voz, IA de Claudia, infraestructura…), que irá en otro PR.

* Cuota de almacenamiento (ver tabla de límites): `public.marketing_storage_used()` lo cuenta todo y
  `public.marketing_reserve_storage()` reserva de forma atómica (bloquea la configuración del tenant). Las subidas
  reservan sus bytes antes de guardar el archivo (y los liberan si falla); al aprobar un trabajo se reservan los
  bytes estimados del resultado (fila `job:{id}`). Una subida ya registrada no cuenta dos veces.
* **Reservas abandonadas**: cada reserva caduca (subida: 15 min; trabajo: 24 h) y una caducada deja de contar.
  Se cierra **una sola vez** (`consumed`, `released` o `expired`): un disparador impide reabrirla o cambiar sus
  bytes, `marketing_release_storage` solo actúa sobre reservas vivas y `marketing_expire_storage_reservations()`
  (llamada por `RetentionRunner.run`) las marca `expired` de forma idempotente. La reserva de un trabajo se cierra
  sola cuando termina (disparador `marketing_job_storage_release`): completado → `consumed`; cancelado, fallido,
  timeout (trabajo aprobado sin terminar en 24 h), consentimiento retirado o purga del archivo → `released`.
* **Tamaño real del resultado**: antes de registrar un resultado con archivo, el worker llama a
  `marketing_confirm_output_storage(tenant, job, bytes_reales)`, que compara el tamaño real con lo reservado + lo
  disponible (bloqueando la configuración del tenant). Si no cabe: no se registra, se borra el temporal (solo
  rutas `{tenant}/derivatives/{job}/…`), se libera la reserva y el trabajo falla con `storage_quota_exceeded`.
  Si cabe, la reserva pasa al tamaño real hasta que el trabajo termina.
* **Concurrencia en PostgreSQL real**: `tests/test_marketing_pg_concurrency.py` crea un clúster desechable
  (initdb en un directorio temporal, solo 127.0.0.1), carga las migraciones y abre transacciones simultáneas:
  dos subidas, dos aprobaciones por bytes, dos por costo y subida contra aprobación, cerca del límite → solo una
  entra. Se omite sin `MARKETING_PG_BIN`. Nunca usa Supabase ni producción. Comando exacto (macOS con
  Homebrew `postgresql@17`; desde la raíz del repositorio; el entorno solo necesita `pytest` y `psycopg`, por eso
  `--noconftest` evita cargar las dependencias del resto de la suite):

  ```bash
  python3 -m venv /tmp/mk-pg-venv && /tmp/mk-pg-venv/bin/pip install pytest "psycopg[binary]==3.2.3"
  MARKETING_PG_BIN=/opt/homebrew/opt/postgresql@17/bin /tmp/mk-pg-venv/bin/python -m pytest -v -p no:cacheprovider --noconftest tests/test_marketing_pg_concurrency.py
  ```

  En Linux, `MARKETING_PG_BIN` es la carpeta de `initdb`/`pg_ctl` (p. ej. `/usr/lib/postgresql/17/bin`). La
  prueba arranca el servidor con `LC_ALL=C` (en macOS el postmaster no arranca sin un locale válido), lo
  detiene y borra el directorio al terminar. Resultado esperado: `17 passed`.

* `marketing_settings.monthly_ai_cost_limit` (USD/mes) lo fija **solo el operador**; `0` por defecto = ninguna
  generación, ni siquiera simulada. **Desconocido (`null`) o 0 = cerrado** y nunca más de **80 USD/mes** (objetivo
  de costo total de AITA por tenant): la base lo exige (`not null`, 0–80) y el código también falla cerrado. El
  tenant no puede cambiarlo (sin endpoint; SELECT only).
* Cada trabajo conserva: costo máximo estimado (`estimated_cost`), costo reservado (`reserved_cost`, inmutable
  tras aprobar), costo real (`actual_cost`), proveedor/modelo (`selected_provider`, `selected_model` y, por
  subtarea, `marketing_model_usage`) e idempotencia (`idempotency_key` del trabajo y de cada subtarea).
* Aprobar y reservar es **una sola operación atómica** (`public.marketing_approve_generation`, solo service_role):
  bloquea la fila de configuración del tenant (`SELECT … FOR UPDATE`) y el trabajo, comprueba el estado, calcula el
  consumo del mes (reservado de los trabajos en cola/procesando + costo real de los terminados) y rechaza con
  `budget_exceeded` si el nuevo costo máximo no cabe. Sin costo estimable → `cost_not_estimable`, no se ejecuta.
  Al cancelar o fallar sin gasto, lo reservado deja de contar automáticamente.
* El worker nunca gasta más que lo reservado ni que el máximo aprobado (`budget_exceeded`).
* Con proveedores reales apagados y mocks, el costo real es 0 y no hay red.
* Un Reel 100 % IA se marca (`full_ai`), muestra su costo estimado y nunca se inicia sin aprobación explícita.
  Prioridad económica: material del cliente → local → plantillas/FFmpeg → modelos pequeños → imagen →
  imagen-a-vídeo → texto-a-vídeo como último recurso; la sugerencia por defecto usa el material del cliente.
* Interfaz: "Marketing AI budget" del mes (consumido, reservado, disponible y avisos al 25 %, 10 % y 0 %) con la
  aclaración de que no es el presupuesto global. En cada trabajo el manager ve **costo estimado, reservado y real y
  la categoría del costo** (`marketing_ai_budget`). Proveedor y modelo son **información interna**: se guardan en
  el trabajo y en `marketing_model_usage`, pero la API no los devuelve.
* Concurrencia: probada con hilos reales contra un doble que reproduce el bloqueo, y en SQL la lógica y el
  `FOR UPDATE`. PGlite es de una sola conexión: conviene repetir la prueba entre conexiones reales en staging.

## 5. Mezcla real / IA

* Presets 100/0, 75/25, 50/50, 25/75, 0/100 y personalizado en pasos de 5 % (el dominio admite cualquier
  entero 0–100 múltiplo de 5). Real + IA = 100 en Python y en un `check` de la tabla.
* Conversión: escenas reales = reparto por **resto mayor** de `escenas × %real / 100`; los segundos se reparten
  igual entre escenas (el total cuadra exacto). Se muestra como **aproximación** antes de generar.
* Origen de cada escena: `client_original`, `client_ai_adapted` (si se marca “adaptar con IA”) o `ai_generated`.
  La interfaz muestra escenas y segundos por origen.
* La IA puede **sugerir** una mezcla (marcada como sugerencia, nunca se aplica sola). Solo owner/manager confirman;
  la mezcla confirmada se guarda en el trabajo y queda **congelada al aprobar** (trigger). Una regeneración que la
  cambia exige `mix_reconfirmed: true`.

Adaptaciones permitidas: iluminación, encuadre 9:16, fondo/decoración, transiciones, textos y CTA (composición
determinista). No permitidas: resultados físicos falsos, alterar cuerpos/identidad/edad/etnia, testimonios
inventados, antes/después falsos, clonar voz o cara sin autorización explícita.

## 6. Selección por coste y calidad (MarketingAIRouter)

El router recibe una **tarea** (`RouteRequest`: tenant_id, task_type, quality_tier, maximum_cost, maximum_latency,
privacy_class, input_media_types, output_requirements, aspect_ratio, duration, language, brand_constraints,
idempotency_key) y aplica, en orden:

1. excluir modelos desactivados o sin soporte para la tarea / entradas;
2. excluir los que violan la privacidad;
3. excluir los que superan el coste máximo **y los externos sin precio verificado**;
4. excluir los que no llegan a la calidad mínima del nivel (draft 30, standard 60, premium 80);
5. elegir el menor coste esperado (coste ÷ fiabilidad);
6. desempatar por latencia y fiabilidad;
7. fallback solo entre los que cumplen todo;
8. nunca subir automáticamente a algo más caro que el presupuesto aprobado.

| Tarea | Método preferido | Calidad mínima | Política de privacidad | Fallback |
|---|---|---|---|---|
| marketing_copy, storyboard | modelo de texto barato (hoy mock) | según nivel | synthetic_only | otro modelo de texto dentro del presupuesto |
| moderation | modelo de moderación (hoy mock) | draft | synthetic_only | bloquear y pedir revisión humana |
| image_generation | modelo de imagen (OmniRoute, apagado) | según nivel | synthetic_only | ninguno fuera de presupuesto |
| image_edit, background_replacement | modelo de edición (apagado) | según nivel | business_media_no_people / consented / anonymized | herramientas locales |
| image_to_video, video_extension | modelo de vídeo (apagado) | según nivel | igual que la entrada; restricted nunca | reducir escenas IA |
| text_to_video | modelo de vídeo (apagado) | según nivel | synthetic_only | menos segundos generados |
| face_detection, face_anonymization | **local** (FFmpeg/librería pequeña; hoy mock) | standard | restricted permitido (no sale) | revisión humana |
| transcription, subtitles | **local** (FFmpeg / modelo pequeño; hoy mock) | standard | restricted permitido | subtítulos manuales |
| final_render | **local** FFmpeg (hoy mock) | standard | restricted permitido | ninguno (falla con código público) |

Los modelos y precios viven en `services/marketing_ai_catalog.json` (versión, fecha, fuente de precios, moneda,
unidad de facturación, coste estimado; el coste real se guarda en `marketing_model_usage`). Se puede apuntar a
otro archivo con `MARKETING_AI_CATALOG_PATH`. El catálogo **rechaza** campos con aspecto de clave. La pestaña
Trabajos muestra una vista técnica del catálogo sin claves.

## 7. OmniRoute

Hay **más de un proyecto** llamado OmniRoute; no se ha confirmado cuál se usará, así que **no se instala ni se
ejecuta nada** y solo existe un **contrato** (`OmniRouteAdapter`) más un proveedor simulado.

Candidatos encontrados (a confirmar por el equipo):

* `diegosouzapw/OmniRoute` — gateway compatible con la API de OpenAI, enrutado por estrategias (p. ej. coste),
  paquete npm `omniroute` e imagen Docker (puerto 20128). Puntos de atención reportados: panel de administración
  con contraseña por defecto que debe cambiarse y el gateway ve credenciales y prompts.
* `v0l/OmniRoute` — fork en TypeScript/Next.js del anterior, con endpoints de imágenes y vídeo
  (`/v1/images/generations`, `/v1/videos/generations`) a través de ComfyUI / SD WebUI.

Fuentes consultadas: https://github.com/v0l/OmniRoute · https://www.jsdelivr.com/package/npm/omniroute ·
https://pinggy.io/blog/omniroute_ai_gateway_security/ · https://agentpedia.codes/blog/omniroute-ai-gateway-routing-setup-guide ·
https://railway.com/deploy/omniroute-1.md · https://dev.co/ai/mcp/omniroute

Contrato del adaptador: cuerpo `{model, task, aspect_ratio, duration, language, quality_tier, max_cost,
idempotency_key, metadata.tenant (hash)}`. Nunca binarios ni URLs firmadas en el cuerpo ni en logs. Sin un
transporte inyectado explícitamente, `submit()` devuelve `provider_disabled`.

## 8. Variables de entorno

| Variable | Defecto | Efecto |
|---|---|---|
| `OMNIROUTE_ENABLED` | `false` | Interruptor del proveedor OmniRoute (además necesita transporte y catálogo con precio verificado) |
| `OMNIROUTE_BASE_URL` | vacío | URL del gateway (solo servidor) |
| `MARKETING_AI_MOCK_ENABLED` | `true` | Proveedor simulado (sin red, coste 0) |
| `MARKETING_LOCAL_TOOLS_ENABLED` | `false` | Herramientas locales (FFmpeg); no instaladas en producción |
| `MARKETING_AI_CATALOG_PATH` | catálogo del repo | Otro catálogo en el servidor |
| `MARKETING_STREAM_TTL` | 600 | Vida de la sesión de reproducción (cookie) en segundos (se limita a 60–600) |
| `APP_ENV` | `production` | `test`, `local` o `development` permiten (solo junto con la siguiente) una cookie sin Secure |
| `MARKETING_WORKER_ENABLED` | — | `true` = el comando `worker` puede ejecutar trabajos (sin esto, no arranca) |
| `MARKETING_STREAM_COOKIE_INSECURE` | — | `1` = cookie sin Secure, **solo** con `APP_ENV` de test/local; en producción impide arrancar |
| `MARKETING_MAX_VIDEO_DURATION_MS` | 900000 | Duración máxima creíble de un vídeo subido (15 min) |
| `MARKETING_HARD_MAX_UPLOAD_BYTES` | 100 MB | Tope absoluto por archivo (memoria del servidor) |

No hay ninguna clave nueva. Las claves futuras irían solo en variables del servidor; nunca en el navegador,
tablas de contenido ni logs (los logs registran solo el tipo de error).

## 9. Límites del plan (migración nueva, cerrados por defecto)

No se reutiliza `monthly_reel_limit` (sigue contando solo reels **programados/publicados**, igual que en Fase 1).
Columnas nuevas en `marketing_settings` (las fija el operador; el tenant no puede escribir en esa tabla):

| Límite | Qué cuenta | Cuándo se consume | Fallo / reintento / cancelación |
|---|---|---|---|
| `ai_generation_enabled` (false) | interruptor | — | false = no se aprueba nada |
| `monthly_generation_job_limit` (0) | trabajos aprobados en el mes | al aprobar | un trabajo cancelado o fallido **sin cargo del proveedor** no cuenta; reintentar no crea trabajo nuevo |
| `monthly_regeneration_limit` (0) | trabajos con `regeneration_of` | al aprobar | igual |
| `monthly_generated_image_limit` (0) | imágenes generadas estimadas | al aprobar | igual |
| `monthly_generated_video_seconds_limit` (0) | segundos generados o adaptados con IA | al aprobar | igual |
| `monthly_ai_cost_limit` (0) | coste real, o el estimado si aún no hay real | al aprobar (reserva) | cancelado/fallido cuenta solo su coste real |
| `library_storage_limit_bytes` (0) | originales + derivados (anonimizaciones, vistas previas) + resultados/temporales + subidas en curso + bytes estimados de trabajos pendientes | al reservar (subida o aprobación), de forma atómica | 0 = Biblioteca no habilitada; eliminar/purgar libera espacio; archivar no |
| `max_upload_bytes` (50 MB) | tamaño por archivo | al subir | — |

`null` = sin límite; `0` = nada permitido. El mes es el del huso horario del tenant.
Con `ai_generation_enabled = false`, `monthly_generation_job_limit = 0` o `monthly_ai_cost_limit = 0`
**no se aprueba ninguna generación, ni real ni simulada** (`generation_disabled`), y la interfaz muestra
“Generación no habilitada en este plan”. La Biblioteca (subir, clasificar, eliminar) **no consume generación** y
tiene su propio límite de almacenamiento, que fija el operador: `0` (por defecto) = Biblioteca no habilitada,
`null` = sin límite. Ninguna empresa recibe espacio automáticamente. La interfaz distingue: Biblioteca no
habilitada (0), habilitada (usado / límite), límite alcanzado y generación no habilitada (límites de IA en 0).
Golden Age no cambia: sus límites de Fase 1 se conservan y los nuevos quedan en 0. Tras el merge y la migración,
el operador autorizará por separado su cuota (p. ej. 1 GiB). El tenant nunca puede elevar sus límites: ningún endpoint escribe `marketing_settings` y `authenticated`
solo tiene SELECT (probado en SQL).

## 9b. Ejecución asíncrona

* `marketing_worker.JobRunner` reclama con `marketing_claim_job` (lease; ver §4g), vuelve a comprobar las entradas,
  ejecuta subtareas con idempotencia y heartbeat y cierra el trabajo solo con su lease vigente. `run_pending()`
  lo usa `python -m services.marketing_runtime worker`; **en esta fase no hay worker desplegado**.
* Desde una petición HTTP solo se ejecutan trabajos **100 % simulados** (rápidos, sin red). Cualquier trabajo con
  vídeo real, render, anonimización real o proveedores reales responde `requires_worker` y queda en cola.
* Vídeo, render, anonimización y proveedores reales **requieren** ese worker asíncrono (con timeouts, reintentos
  limitados y métricas) antes de activarse.
* Los resultados simulados quedan marcados (`result_metadata.mock`, `mock_generation_job:` en las notas) y
  `MarketingService` bloquea programarlos (`mock_content_not_publishable`).

## 10. Proveedores desactivados y cómo activarlos en el futuro

Hoy: OmniRoute apagado y sin transporte, FFmpeg local apagado, sin red, sin gasto, sin publicación, sin Postiz.
Para activar un proveedor real (cada paso con autorización explícita):

1. Confirmar el proyecto OmniRoute exacto, versión y API; revisión de seguridad (credenciales, retención,
   panel de administración, red privada).
2. Cargar precios **verificados** en el catálogo (fuente, fecha, moneda, unidad) y `price_verified: true`.
3. Implementar el transporte con timeouts, límite de reintentos, idempotencia y redacción de errores; pruebas.
4. Definir variables del servidor y activarlas primero en un entorno de prueba.
5. Ajustar límites del tenant piloto (operador) y activar `ai_generation_enabled` solo para él.
6. Revisar el primer trabajo real y los costes en `marketing_model_usage`.

## 11. Rollback

* Código: revertir el commit del PR. La Fase 1 no depende de nada nuevo.
* Base de datos: la migración es aditiva. Si se hubiera aplicado, basta con dejar `ai_generation_enabled=false`
  y los límites en 0; las tablas nuevas pueden quedarse vacías. Borrarlas exige una migración nueva y revisada
  (los historiales son de solo inserción a propósito).
* Bucket: la migración solo **añade** `video/webm` a los tipos permitidos.
* Las migraciones de retención y de costo de Marketing también son aditivas; con `monthly_ai_cost_limit = 0`
  (por defecto) la generación queda bloqueada.
* Si las tablas nuevas faltan, los endpoints responden 503 `marketing_unavailable` (sin detalles).

## 12. Pruebas

* Python: `tests/test_marketing_studio.py` (permisos, archivos, truncados/polyglot/metadatos, antivirus, borrado,
  menores, consentimiento, anonimización simulada), `tests/test_marketing_studio_jobs.py` (flujo mock, worker,
  idempotencia, límites en 0), `tests/test_marketing_studio_http.py` (subida con límite previo, vista previa y CSP)
  y `tests/test_marketing_ai_router.py` (catálogo, router, mezcla, sincronía Python↔SQL, endpoints, puerta).
* JS: `tests/js/marketing-studio.test.mjs` (mezcla, privacidad, etapas, textos ES/EN, sin claves).
* SQL (PGlite): `tests/sql/marketing_studio.mjs` (RLS, mínimo privilegio, inmutabilidad, trabajos, aislamiento,
  límites por defecto, bucket, atomicidad), `tests/sql/marketing_retention.mjs` y
  `tests/sql/marketing_generation_budget.mjs`.
* Retención y costos: `tests/test_marketing_retention.py` (reloj controlado) y `tests/test_marketing_generation_cost.py`.
* Ejecutar: `PGLITE_NODE_PATH=/ruta/node_modules python3 -m pytest -q tests/test_marketing*.py`.

## 13. Riesgos y pendientes

* Sin antivirus (ver 4c): el material real no sale hacia proveedores externos hasta que exista.
* GIF/WebM/MOV no se decodifican: se valida firma, estructura y cabecera, no el contenido completo.
* La subida se acumula en memoria hasta el límite del tenant (50 MB por defecto, tope 100 MB); para vídeos grandes
  hará falta subida reanudable directa a Storage.
* La anonimización real (FFmpeg/detector local) y el render final están simulados y nunca se presentan como reales.
* No hay worker desplegado: con proveedores reales hará falta desplegarlo (ver 9b).
* Costes: hoy 0 (mock). Con proveedores reales, el coste depende del catálogo verificado y de los límites.

## 14. Qué falta para publicar de verdad

Conectar redes (Postiz u otro) con su propio PR y autorización, OAuth por tenant, revisión legal de
consentimientos, worker de publicación con idempotencia, métricas, y pruebas en entorno de staging.
Nada de eso está en esta fase.
