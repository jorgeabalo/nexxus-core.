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
   └── marketing_jobs_domain.py   estados de archivos y trabajos, límites, errores públicos
   ▼
Supabase (service role, solo backend)
   ├── tablas marketing_media, *_derivatives, *_generation_jobs, *_job_events, *_inputs, *_outputs, *_model_usage
   └── Storage privado marketing-assets: {tenant}/originals/{asset}/{nombre} · {tenant}/derivatives/{asset}/{id}.{ext}
```

Cada módulo Python tiene menos de 500 líneas y no hace red salvo `supabase_admin.py` (Storage) y, en el
futuro, un transporte de OmniRoute inyectado explícitamente.

## 2. Flujo de datos (demostrable hoy con el mock)

1. **Subir** (owner/manager) → el backend valida tamaño, extensión, MIME declarado y firma real; rechaza
   SVG/HTML/scripts/ejecutables; construye la ruta; sube con `x-upsert: false`; guarda checksum, quién y cuándo.
2. **Clasificar** personas: `contains_people` (sí/no/no sé) + `people_policy` + `consent_status`.
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
| `anonymize` | se usa solo un **derivado** anonimizado y revisado por una persona | Solo el derivado revisado |
| `consented` | hay consentimiento registrado (`consent_status = granted`) | Sí, solo a modelos que admiten personas reales, con retención conocida y uso comercial |
| `no_people` | confirmado que no hay personas (`contains_people = false`) | Sí |

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
  En esta fase el procesamiento es **simulado** (un marcador gris, nunca una copia del original).

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
| `MARKETING_SIGNED_URL_TTL` | `300` | Segundos de las URLs firmadas (se limita a 60–900) |
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
| `library_storage_limit_bytes` (0) | bytes de originales (también archivados) | al subir | archivar no libera espacio (nunca se borra) |
| `max_upload_bytes` (50 MB) | tamaño por archivo | al subir | — |

`null` = sin límite; `0` = nada permitido. El mes es el del huso horario del tenant. Golden Age no cambia: sus
límites de Fase 1 se conservan y los nuevos quedan en 0 (generación apagada) hasta que se decida.

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
* Si las tablas nuevas faltan, los endpoints responden 503 `marketing_unavailable` (sin detalles).

## 12. Pruebas

* Python: `tests/test_marketing_studio.py` (permisos, archivos, privacidad, flujo completo mock, límites,
  idempotencia, sin red) y `tests/test_marketing_ai_router.py` (catálogo, política del router, mezcla,
  sincronía Python↔SQL, endpoints HTTP, puerta).
* JS: `tests/js/marketing-studio.test.mjs` (mezcla, privacidad, etapas, textos ES/EN, sin claves).
* SQL (PGlite): `tests/sql/marketing_studio.mjs` (RLS, mínimo privilegio, inmutabilidad, trabajos, aislamiento,
  límites por defecto, bucket, atomicidad).
* Ejecutar: `PGLITE_NODE_PATH=/ruta/node_modules python3 -m pytest -q tests/test_marketing*.py`.

## 13. Riesgos y pendientes

* Sin escaneo antivirus de archivos (estado `scanning` reservado); la validación por firma reduce el riesgo pero
  no sustituye un escáner.
* El GIF/WebM/MOV no se decodifica: se valida firma y cabecera, no el contenido completo.
* La subida se lee en memoria (tope 100 MB por defecto); para vídeos grandes hará falta subida reanudable.
* La anonimización real (FFmpeg/detector local) y el render final están simulados.
* El procesamiento es síncrono en la petición; con proveedores reales debe moverse a un worker.
* Costes: hoy 0 (mock). Con proveedores reales, el coste depende del catálogo verificado y de los límites.

## 14. Qué falta para publicar de verdad

Conectar redes (Postiz u otro) con su propio PR y autorización, OAuth por tenant, revisión legal de
consentimientos, worker de publicación con idempotencia, métricas, y pruebas en entorno de staging.
Nada de eso está en esta fase.
