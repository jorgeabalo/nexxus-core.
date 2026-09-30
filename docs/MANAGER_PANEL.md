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
