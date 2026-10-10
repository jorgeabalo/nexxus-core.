# AITA/Nexxus — Manager Panel (Fase 1)

Primer cliente: **Golden Age Fitness & Training** (`tenant_slug = golden_age`).
URL: `https://nexxus-core-production.up.railway.app/manager`

## Arquitectura

```
Cliente llama
   │
   ▼
TWILIO (+1 346-245-7940) ── /webhooks/twilio/voice · /api/twilio/mensaje · /webhooks/twilio/status
   │
   ▼
NEXXUS CORE (FastAPI, Railway)
   ├─ Claudia (recepcionista_service.py) ── LLM configurable: ANTHROPIC_MODEL / SUMMARY_MODEL
   ├─ services/call_logger.py ── hilo de fondo, no bloqueante ──┐   (service_role, solo servidor)
   └─ /manager  (HTML/CSS/JS estático, sin datos)                │
                                                                 ▼
SUPABASE (proyecto "Golden-age")
   ├─ tenants ─ tenant_users (owner/manager/staff) ─ tenant_invites ─ auth.users
   ├─ members · staff · services · check_ins · appointments · payments · messages · …
   ├─ calls · leads · alerts
   ├─ vistas *_overview + RPC manager_dashboard / manager_activity / manager_alerts
   └─ RLS: cada usuario solo ve/modifica su tenant
                                                                 ▲
MANAGER PANEL (navegador / teléfono)                             │
   login Supabase Auth → JWT del usuario → consultas con RLS ────┘
```

### Principios
* **Claudia tiene prioridad.** `call_logger` encola eventos y los procesa en un
  hilo propio con timeout corto. Cualquier error de Supabase se captura y se
  registra (`CALL_LOGGER_ERROR` en logs); la respuesta TwiML nunca espera.
  Si faltan `SUPABASE_URL`/`SUPABASE_SERVICE_ROLE_KEY`, el logger se apaga solo.
* **Nada inventado.** Sin filas → 0 / "No data yet". No hay datos demo.
* **Sin transcript.** Al terminar la llamada se genera un resumen breve
  (`calls.summary`, `intent`, `outcome`) con el modelo de `SUMMARY_MODEL`
  (o `ANTHROPIC_MODEL`). El texto de la conversación no se guarda.
* **Secretos.** El navegador solo recibe `SUPABASE_URL` y la clave pública
  (`SUPABASE_ANON_KEY`) vía `GET /api/manager/config`. `SUPABASE_SERVICE_ROLE_KEY`,
  `ANTHROPIC_API_KEY` y `TWILIO_AUTH_TOKEN` solo existen en el servidor.
  `/manager` envía `Cache-Control: no-store`, CSP estricta y `X-Frame-Options: DENY`.

## Trazabilidad

```
calls.id ◄── leads.call_id           calls.lead_id ──► leads.id
calls.id ◄── appointments.call_id    leads.id ◄── appointments.lead_id
calls.id ◄── payments.call_id        appointments.id ◄── payments.appointment_id
alerts.call_id / lead_id / appointment_id / payment_id / member_id
```
Un trigger (`enforce_same_tenant`) impide enlazar filas de tenants distintos.

## Qué escribe Claudia hoy

| Evento | Escritura |
|---|---|
| Entra la llamada (`/voice`) | `calls` (in_progress, caller_phone, called_phone) — tenant por `tenants.twilio_phone` |
| El cliente deja sus datos (`[CONTACTO: …]`) | `leads` (call_id) + `calls.lead_id`, `follow_up_required=true` + `alerts` tipo `follow_up` |
| Termina la llamada (`/status`) | `calls.status`, `ended_at`, `duration_seconds`, `summary`, `intent`, `outcome` |
| Llamada sin cierre > 30 min | `calls.status = abandoned` |

### Pendiente de conectar (estructura ya lista)
* **Citas desde Claudia:** Claudia aún no agenda. Cuando lo haga: `appointments`
  con `source='claudia'`, `call_id`, `lead_id` y `calls.appointment_id`.
* **Transferencias:** Claudia hoy da el teléfono de Roberto; no hace `<Dial>`.
  Cuando transfiera de verdad: `calls.transferred = true`.
* **Identificar socio que llama:** `calls.member_id` (buscar por teléfono).
* **Check-ins:** la tabla existe; falta el registro por QR/recepción.
* **Pagos online:** `payments.provider` / `provider_ref` preparados para Stripe/PayPal.
* El status callback de Twilio debe estar configurado en el número
  (`/webhooks/twilio/status`) para cerrar llamadas y generar resúmenes.

## Base de datos (migraciones en `supabase/migrations/`)
1. `20260927120000_tenancy_core.sql` — `tenants`, `tenant_users`, `tenant_invites`; `tenant_id` en las 9 tablas existentes; FKs compuestas (id, tenant_id); índices.
2. `20260927120100_operations.sql` — columnas nuevas en `appointments` y `payments`; tablas `calls`, `leads`, `alerts`; triggers `updated_at`.
3. `20260927120200_security_rls.sql` — helpers de tenant, alta por invitación, RLS en todas las tablas.
4. `20260927120300_manager_views.sql` — vistas `member_overview`, `appointment_overview`, `payment_overview`, `call_overview`; RPC `manager_dashboard`, `manager_activity`, `manager_alerts`.
5. `20260927120400_traceability_links.sql` — `payments.appointment_id/lead_id/call_id`, trigger `enforce_same_tenant`.
6. `20260927120500_private_auth_helpers.sql` — helpers SECURITY DEFINER movidos al esquema `private`.

No se borró ni renombró ninguna tabla. Único cambio de restricción existente:
`members.member_id` pasó de único global a único por tenant.

## Roles
| Rol | Ver | Crear/editar | Borrar | Equipo |
|---|---|---|---|---|
| owner | su tenant | sí | sí | invita/gestiona |
| manager | su tenant | sí | sí | ve invitaciones |
| staff | su tenant | sí | no | — |

## Alta de usuarios (sin contraseñas en código)
1. Registrar la invitación (SQL Editor de Supabase):
   ```sql
   insert into public.tenant_invites (tenant_id, email, role, staff_id)
   select t.id, 'EMAIL_EN_MINUSCULAS', 'owner',            -- o 'manager' / 'staff'
          (select s.id from public.staff s where s.tenant_id = t.id and s.first_name = 'Roberto' limit 1)
   from public.tenants t where t.slug = 'golden_age';
   ```
2. Supabase → Authentication → Users → **Invite user** con ese mismo email.
3. La persona abre el email, llega a `/manager`, crea su contraseña y entra.
   El vínculo tenant/rol se crea solo (trigger), exista o no el usuario antes.

## Añadir otro tenant
`insert into tenants (slug, name, vertical, timezone, twilio_phone, branding, modules)`,
mapear su número en `phone_tenant_mapping.json`, e invitar a su owner. El
mismo frontend cambia marca, colores y módulos según `tenants.branding/modules`.

## Tests
* `tests/test_manager_and_claudia.py` — Supabase caído/lento no afecta a
  `/voice`, `/api/twilio/mensaje` ni `/status`; trazabilidad call→lead→alert;
  idempotencia; modelo configurable; `/api/manager/config` sin secretos;
  cabeceras de seguridad; endpoints existentes.
* RLS y aislamiento: probado en SQL con usuarios simulados de dos tenants
  (transacción revertida, sin datos residuales).

# Member Panel (portal del socio)

URL: `https://nexxus-core-production.up.railway.app/m` (español por defecto, botón English).

## Cómo entra el socio
1. **QR / enlace personal** `…/m/q/<código>` — se lo envía el gym (SMS) o lo imprime.
   * `código = <member_id>.<HMAC-SHA256(PORTAL_TOKEN_SECRET, member_id:portal_token_version)>`.
     No se guarda en la base; se recalcula.
   * El servidor valida la firma, asegura el usuario de Supabase Auth del socio
     (`members.user_id`; si no tiene email se usa un alias técnico
     `m-<id>@members.aita-nexxus.app` que nunca recibe correo), genera un enlace
     mágico de un solo uso y redirige a `/m/#login=<token_hash>`; el navegador lo
     canjea con `verifyOtp` y queda con sesión propia (clave `aita-member-auth`,
     separada de `/manager`).
   * **Regenerar QR** = `portal_token_version + 1` → el QR anterior deja de servir al instante.
   * Límite: 20 aperturas/minuto por IP.
2. **Enlace mágico por email** desde `/m` (`signInWithOtp`, `shouldCreateUser:false`:
   solo socios ya vinculados; misma respuesta exista o no el email).

## Qué ve y hace el socio (todo con su JWT; RLS + funciones `member_*`)
| Vista | Datos |
|---|---|
| Inicio | estado de membresía, próximo pago / vencido, próxima cita, su QR, visitas, contacto del gym |
| Citas | próximas e historial; **pedir cita** (`member_request_appointment`: queda `scheduled`, `source='member_portal'`, alerta `appointment_confirmation` al Manager; máx. 3 pendientes, ≤ 90 días); **cancelar** las suyas futuras |
| Pagos | historial y saldo pendiente (solo lectura) |
| Visitas | este mes, última, recientes |
| Mis datos | edita solo teléfono, email y contacto de emergencia (`member_update_contact`) |

Un socio NO puede llamar a `manager_dashboard/activity/alerts` (exigen ser staff del tenant).

## Manager Panel → ficha del socio → "Member portal"
Estado (not invited / invited / activated, primer y último acceso), **Send access**
(SMS y/o email), **Show / print QR**, **Regenerate QR**. Solo owner/manager
(el backend valida el JWT contra `tenant_users`).

## Endpoints
| Método | Ruta | Quién |
|---|---|---|
| GET | `/m`, `/m/assets/*` | público (sin datos) |
| GET | `/m/q/{código}` | público, firmado + rate limit |
| GET | `/api/member/qr` | socio (Bearer) |
| GET | `/api/manager/members/{id}/portal-link` | owner/manager |
| POST | `/api/manager/members/{id}/portal-access` `{sms,email,regenerate}` | owner/manager |

## Configuración
* Railway: `PORTAL_TOKEN_SECRET` (≥ 32 caracteres aleatorios; sin él el portal por QR devuelve 503).
  Opcional `PUBLIC_BASE_URL` (si no, se usa el host de la petición).
* Supabase → Authentication → URL Configuration → Redirect URLs: añadir
  `https://nexxus-core-production.up.railway.app/**`.
* SMS al socio: requiere el registro A2P 10DLC del número de Twilio.
* Migración: `20260928120000_member_portal.sql`.

## Tests
`tests/test_member_portal.py` (firma, manipulación, secreto ausente, revocación al
regenerar, socio cancelado, login QR, alias sin email, rate limit, permisos
owner/manager/staff/otro tenant/socio, envío SMS+email, cabeceras) y E2E con
Playwright del portal y de la tarjeta del Manager (sin violaciones de CSP).
RLS del socio probado en SQL (solo sus filas; no escribe directo; no cancela citas ajenas).


## Member Panel · Progreso, evaluaciones y SMS (migración `20260930120000_member_progress_onboarding`)

### Mi progreso (`/m/#/progress`)
* Tarjetas **Antes → Ahora → Cambio** con fechas y mini-gráfico para: **fuerza** (ejercicio, peso y
  repeticiones), **movilidad y vida diaria** (levantarse de una silla, escaleras, caminar 1-5 y prueba de
  silla 30 s), **resistencia** (minutos caminando/bicicleta/otra), **bienestar** (energía, sueño, cómo
  se siente 1-5), **constancia** (asistencia real de `check_ins` y sesiones completadas) y **cuerpo**
  (peso, cintura, brazo, muslo, estatura).
* "Antes" = línea base (`is_baseline`) o, si no hay, el primer registro (se indica). Nunca se sobrescribe.
* Cada dato muestra quién lo registró: *declarado por el socio* o *tomado por el staff (nombre)*;
  lo decide un trigger en el servidor, no el navegador.
* Mensajes de apoyo solo cuando los datos lo demuestran (mismo ejercicio y peso → más repeticiones,
  más minutos, más facilidad, más energía, más visitas). Si las condiciones de la prueba cambiaron o no
  hay mejora, se dice con respeto y se sugiere revisarlo con el entrenador. Bajar de peso no se
  presenta como éxito; el texto habla de "cambios observados durante el programa".
* Figura SVG masculina/femenina según `members.gender` (M/F). Si falta, figura neutra y el socio puede
  indicarlo (`member_set_sex`). Nunca se infiere del nombre.
* Unidades lb/in ↔ kg/cm (se guarda en lb/in; conversión 0.45359237 y 2.54).
* "Registrar nueva medición" = actualización breve (`member_log_progress`), nunca línea base.

### Evaluación (`/m/#/evaluation`)
* Formulario por pasos con barra de progreso, borrador en servidor + copia local, "Guardar y seguir
  después". "Enviar" usa `member_submit_evaluation(kind, payload, client_ref)`: idempotente; la
  confirmación solo aparece si Supabase devolvió OK; si falla, el borrador se conserva y el reintento
  usa la misma referencia.
* Cada evaluación es un registro independiente (`member_evaluations`, inmutable al enviarse); la inicial
  crea la línea base; la próxima queda a **90 días** (`members.next_evaluation_due`).
* **Cuestionario original: pendiente** (ver `supabase/questionnaires/README.md`). Sin él, la evaluación
  guarda medidas e indicadores y `onboarding_completed` sigue en `false`.
* Socio existente (`joined_as = existing` o NULL): entra con su QR sin onboarding; queda pendiente.
  Socio nuevo (`joined_as = new`): el inicio le presenta la evaluación inicial.
* PDF de archivo generado al vuelo desde el registro guardado: `GET /api/member/evaluations/{id}/pdf`
  (solo el propio socio) y `GET /api/manager/evaluations/{id}/pdf` (owner/manager del mismo gym).
  No hay enlaces públicos ni archivos almacenados.

### Invitaciones a evaluar
* Manager → ficha → "Send evaluation invite" (SMS y/o email) con enlace seguro `…/m/q/<código>?next=evaluation`.
* Automático cada 6 h si `EVALUATION_INVITES_AUTO=1` y `PUBLIC_BASE_URL` configurada: una sola invitación
  por fecha de vencimiento (`evaluation_invitations`, índice único).

### SMS: estados reales de entrega
* `_send_sms` ya no dice "enviado" al recibir un SID: guarda en `sms_messages` (sin cuerpo ni enlace;
  teléfono enmascarado) el estado **Aceptado/En cola → Enviado → Entregado / Fallido** y el código de error.
* Twilio avisa por `POST /webhooks/twilio/sms-status` (StatusCallback por mensaje, firma
  `X-Twilio-Signature` validada contra `PUBLIC_BASE_URL`). "Check SMS delivery" consulta a Twilio a demanda.
* "SMS diagnostics" (owner/manager) muestra datos reales de Twilio: tipo de cuenta, si el número puede
  enviar SMS y los últimos mensajes con su código de error (p. ej. 30034 = número sin registro A2P 10DLC).
* El GET de `/m/q/<código>` ya no inicia sesión ni marca "activado": solo muestra el botón **Entrar**, que
  hace `POST /api/member/qr-login`. Así las vistas previas/escáneres de SMS no consumen ni activan el acceso.
* Números validados en E.164 (NANP estricto para EE. UU.).

### Configuración
* Railway: `PUBLIC_BASE_URL=https://nexxus-core-production.up.railway.app` (obligatoria para enlaces y
  callbacks), `EVALUATION_INVITES_AUTO=1` (opcional).
* `requirements.txt`: `reportlab` (PDF).

## Cuestionario subido (papel, PDF o fotos)

- El socio puede llenar la evaluación en línea **o** descargar el PDF rellenable (solo existe si el
  gimnasio cargó su cuestionario original; nunca se inventan preguntas) y subirlo, o subir fotos/escaneos.
- El original se guarda **sin modificar** en el bucket privado `evaluation-documents` de Supabase Storage
  (`tenant/socio/documento/n.ext`), sin políticas públicas: solo el backend lo lee y lo entrega al propio
  socio o a owner/manager. Cada acceso queda en `document_access_log` (visible solo para owner).
- Extracción: PDF rellenable → campos del formulario (sin IA). Fotos/escaneos → modelo configurable
  (`EVALUATION_EXTRACTION_MODEL` o `ANTHROPIC_MODEL`). `EVALUATION_AI_EXTRACTION=0` la desactiva
  (el socio transcribe mirando su original). **Antes de usar la IA con datos reales de salud hace falta
  un acuerdo BAA/de tratamiento con Anthropic**.
- Nada se guarda como dato confirmado hasta que el socio revisa y confirma (`member_confirm_document`).
  En blanco queda "en blanco" (nunca "No"); ilegible queda "ilegible". Lo extraído y lo confirmado se
  guardan por separado para auditar correcciones.
- La evaluación confirmada se compara con la inicial en el portal y en el Manager (botón *Compare*).

# Contabilidad básica (`/manager#/accounting`)

Ingresos, gastos, cuentas por cobrar / por pagar, recibos y reporte para el CPA.
Migración: `20261006120000_accounting_basic.sql` (idempotente; no borra ni renombra nada).

* **Tablas:** `accounting_categories`, `accounting_transactions`, `accounting_obligations`
  (todas con `tenant_id`, checks de cantidad/estado/tipo e índices por tenant, fecha, tipo y estado).
  Nada se borra: cancelar = `status='cancelled'` (sin política DELETE).
* **Pagos de socios:** no se copian. `payments` pagados cuentan como ingreso (categoría *Membresías*) y
  los pendientes como *por cobrar*. Si un pago se enlaza (`source_type='payment'`, `source_id`), deja de
  contarse desde `payments`; un índice único impide enlazarlo dos veces.
* **Recibos:** bucket privado `accounting-receipts` (`tenant/movimiento/uuid.ext`, JPG/PNG/WEBP/HEIC/PDF,
  máx. 10 MB, validados por contenido). Solo el backend los lee y entrega.
* **Categorías iniciales:** 5 de ingreso y 9 de gasto por tenant (también para tenants nuevos, por trigger).
  No se crean movimientos ficticios. La migración activa `tenants.modules.accounting`.
* **Permisos:** owner/manager todo; staff consulta y registra movimientos (y adjunta recibo a los suyos),
  sin editar, cancelar, gestionar categorías/pendientes ni exportar; socios y otros tenants: nada.
* **Reporte:** CSV (Excel, UTF-8 con BOM) y PDF con resumen, detalle, gastos por categoría y pendientes.
  Se indica que no es una declaración fiscal oficial.
* **Idioma:** español / inglés (botón en la pantalla; se recuerda en el navegador).

| Método | Ruta (`/api/manager/accounting/…`) | Quién |
|---|---|---|
| GET | `summary`, `transactions`, `categories`, `obligations` (`?tenant_id&start&end`) | owner/manager/staff |
| POST | `transactions` | owner/manager/staff |
| PATCH / POST | `transactions/{id}` · `transactions/{id}/cancel` | owner/manager |
| POST / GET | `transactions/{id}/receipt` (multipart `tenant_id`, `file`) | owner/manager/staff* |
| POST / PATCH | `categories` · `categories/{id}` | owner/manager |
| POST / PATCH / POST | `obligations` · `obligations/{id}` · `obligations/{id}/pay` | owner/manager |
| GET | `export?format=csv\|pdf&lang=es\|en` | owner/manager |

Tests: `tests/test_accounting.py` y `tests/sql/test_accounting.sql`.

# Nexxus Manager (identidad y navegación multitenant)

**Nexxus Manager** es la marca fija de la plataforma; la empresa activa (tenant) aporta sus datos:

| Dato | Fuente en `tenants` |
|---|---|
| Nombre comercial | `branding.business_name` → `name` (fallback genérico: "Nexxus"; ningún cliente está fijo en el código) |
| Logotipo | `branding.logo_url`: ruta local `/…` terminada en `.png/.jpg/.jpeg/.webp/.gif` (opcional `?versión`), solo con letras, números y `. _ - ~ /`: sin `%`, `\`, `..`, `//`, espacios ni SVG. O PNG/JPEG/WebP/GIF en `data:image/…;base64` cuyo contenido tiene la firma real del formato. Si no, iniciales |
| Colores | `branding.color_primary`, `branding.color_accent`: solo `#RRGGBB`. Si faltan o no son válidos (p. ej. `url(…)`, `#123`, con espacios) se quita la variable y vuelve el color por defecto; nunca quedan los del tenant anterior |
| Teléfono / dirección | `settings.public_phone`, `settings.address` (solo esos campos se leen) |
| Zona horaria / idioma | `timezone`, `branding.language` o `branding.locale` |
| Módulos | `modules` (un módulo con `false` no aparece ni se puede abrir) |

* **Menú** (`manager/assets/js/nav.js`): Resumen, Miembros, Asistencia, Citas y servicios, Pagos,
  Contabilidad, Personal, Inventario, Marketing, Claudia IA, Configuración. Las rutas (`#/clave`) no cambian
  (Contabilidad sigue en `#/accounting`).
* **Por rol:** owner ve todo lo habilitado; manager todo salvo Configuración; staff Resumen, Miembros,
  Asistencia, Citas, Pagos, Contabilidad y Claudia (ese es el **máximo** de cada rol, `ROLE_MAX`).
  `tenants.settings.role_modules = {"staff": [...], "manager": [...]}` solo **restringe**: lo visible es la
  intersección de módulos habilitados en el tenant ∩ máximo del rol ∩ `role_modules`. Staff nunca ve
  Personal ni Configuración aunque `role_modules` los incluya; el owner no se limita con `role_modules`.
  Una ruta no permitida (también escrita a mano en la URL) muestra "Sin acceso". Esto solo controla lo que se
  muestra: RLS y el backend siguen imponiendo los permisos.
* **Cambio de empresa:** si la persona tiene más de una, selector en el encabezado y, en móvil (≤ 720 px),
  dentro del menú lateral; al cambiar se cierra el menú y se aplican colores, idioma y módulos de la nueva.
  Con una sola empresa no se muestra selector.
* **Idioma:** selector ES/EN en el encabezado (`manager/assets/js/i18n.js`, compartido con Contabilidad).
  `aita.lang` es una **preferencia global del usuario** en ese navegador: si la eligió, se mantiene al cambiar
  de empresa. Sin preferencia, se usa el idioma de la empresa activa y, si esa empresa no define idioma, el del
  navegador (nunca el de la empresa anterior).
* **Estado del sistema:** punto discreto que comprueba `/api/manager/config` cada minuto.
* Pruebas: `tests/test_nexxus_manager.py` y `node --test tests/js/nav.test.mjs tests/js/i18n.test.mjs`.

# AITA Marketing (Fase 1: fundación)

Módulo nativo del Manager (`#/marketing`, pestañas `overview|content|calendar|campaigns|brand`).
Solo **owner** y **manager** (tope fijo en `nav.js` → `roles` y en el backend; staff y socios: 403,
aunque `settings.role_modules` incluya `marketing`). Nexxus es la única interfaz: los motores
(Postiz, ComfyUI/Wan, Remotion, changedetection.io/SerpBear/Google Places) irán detrás de los
adaptadores de `services/marketing_providers.py`; hoy todos están **desactivados** y no hacen red.

| Pieza | Archivo |
|---|---|
| Migración | `supabase/migrations/20261009120000_aita_marketing.sql` |
| Reglas de estado y límites (puras) | `services/marketing_domain.py` |
| Servicio (rol, tenant, transiciones, historial, cola) | `services/marketing.py` |
| Rutas `/api/manager/marketing/*` | `services/marketing_routes.py` |
| Adaptadores (Publisher, Creative, ReelRenderer, CompetitorData) | `services/marketing_providers.py` |
| Vista | `manager/assets/js/modules/marketing*.js` |

**Estados:** idea, draft, generating, review, approved, rejected, scheduled, publishing, published,
failed, archived. Las transiciones están en `TRANSITIONS` (Python) y en el trigger
`private.marketing_content_transition` (SQL); un test comprueba que coinciden. Nada pasa a
`scheduled` sin estar `approved`; `publishing/published/failed/generating` solo los moverá el sistema.
Rechazar exige observación. Cada cambio queda en `marketing_approval_events` (inmutable).

**Puerta única (`services/marketing_gate.py`, aplicada en `MarketingService.ctx` a todos los endpoints):**
Marketing solo se abre si `tenants.modules.marketing` es exactamente `true`. Ausente, `null`, `"true"`, `1` o
`modules` mal formado → `403 marketing_disabled`, sin consultar ninguna tabla `marketing_*` (en producción
pueden no existir hasta aplicar la migración). Habilitado pero sin tablas → `503 marketing_unavailable`, sin
detalles; solo ese error (PostgREST `PGRST205`/`42P01`): RLS, permisos o conexión siguen siendo `500`.
El orden es: sesión válida → rol owner/manager (staff y socios: `403 forbidden`) → módulo → tablas.

**Escritura:** los usuarios solo **leen** por RLS (owner/manager de su tenant); toda escritura pasa por
el backend (service role) tras validar rol, tenant, reglas y límites.

**Brand Kit:** si no hay perfil guardado, se muestra (sin guardarlo) lo que el tenant activo ya tiene:
`branding.business_name`/`name`, `color_primary`, `color_accent`, idioma y teléfono. El logo del tenant
se usa por referencia (misma validación que `safeLogoUrl`); el Brand Kit solo guarda rutas locales
(`/media/…`), nunca base64, SVG ni URLs externas.

**Programar (Fase 1):** crea una fila por red en `marketing_publications` con `idempotency_key`
(`<content>:<canal>:<fecha>`) y estado `pending` (proveedor `disabled`). No se publica nada.

**Configuración comercial** (`marketing_settings`, la fija el operador, no el tenant):
```sql
-- Activar Marketing para un tenant con su plan (ejemplo: 8 publicaciones al mes)
update public.marketing_settings
   set marketing_enabled = true, approval_required = true, plan_code = 'starter',
       monthly_post_limit = 8, monthly_image_limit = null, monthly_reel_limit = 4,
       connected_channel_limit = 1, competitor_limit = 3
 where tenant_id = (select id from public.tenants where slug = 'TENANT_SLUG');
-- y que aparezca en el menú si el tenant lo tenía apagado
update public.tenants set modules = jsonb_set(modules, '{marketing}', 'true') where slug = 'TENANT_SLUG';
```
`null` = sin límite; `0` = nada permitido. Cuentan las piezas `scheduled/publishing/published` del mes
(zona horaria del tenant): imágenes = image + carousel; reels = reel + video.

Pruebas: `tests/test_marketing.py`, `tests/test_marketing_http.py`, `node --test tests/js/marketing.test.mjs`
y `NODE_PATH=… node tests/sql/marketing.mjs` (RLS y triggers en PGlite).
