-- =====================================================================
-- AITA Marketing — Fase 2: Biblioteca multimedia privada + Estudio de Reels + trabajos de IA.
-- Migración NUEVA (no modifica 20261009120000_aita_marketing.sql, ya aplicada).
--
-- * marketing_media                 — originales de la Biblioteca (no se sobrescriben; borrado controlado y auditado).
-- * marketing_media_events          — auditoría de la Biblioteca (subida, clasificación, consentimiento, borrado).
-- * marketing_media_derivatives     — derivados (anonimizado, adaptado con IA, miniatura, render).
-- * marketing_generation_jobs       — trabajos asíncronos de generación (mezcla real/IA, coste, estado).
-- * marketing_generation_job_events — historial inmutable de cambios de estado de cada trabajo.
-- * marketing_generation_inputs     — qué archivos usa cada trabajo (y con qué clase de privacidad).
-- * marketing_generation_outputs    — escenas / resultados generados (origen de cada escena).
-- * marketing_model_usage           — uso y coste por modelo (solo inserción; evidencia de facturación).
-- * marketing_settings              — límites NUEVOS de generación, todos cerrados por defecto.
--
-- Permisos: igual que Fase 1. PUBLIC/anon nada; authenticated solo SELECT (RLS: owner/manager del
-- tenant); service_role SELECT/INSERT/UPDATE/DELETE. Los usuarios nunca escriben directamente.
-- Reglas de estado y de mezcla: espejo de services/marketing_jobs_domain.py (prueba de sincronía).
-- Idempotente. No borra ni renombra nada. No activa nada para ningún tenant.
-- =====================================================================
-- ---------- límites nuevos (los define el operador, nunca el tenant) ----------
-- 0 = nada permitido (por defecto: cerrado). null = sin límite. Ver docs/AITA_MARKETING_PHASE2.md.
alter table public.marketing_settings
  add column if not exists ai_generation_enabled boolean not null default false,
  add column if not exists max_upload_bytes bigint not null default 52428800
      check (max_upload_bytes between 0 and 524288000),
  add column if not exists library_storage_limit_bytes bigint default 0
      check (library_storage_limit_bytes is null or library_storage_limit_bytes between 0 and 1099511627776),
  add column if not exists monthly_generation_job_limit integer default 0
      check (monthly_generation_job_limit is null or monthly_generation_job_limit between 0 and 10000),
  add column if not exists monthly_regeneration_limit integer default 0
      check (monthly_regeneration_limit is null or monthly_regeneration_limit between 0 and 10000),
  add column if not exists monthly_generated_image_limit integer default 0
      check (monthly_generated_image_limit is null or monthly_generated_image_limit between 0 and 100000),
  add column if not exists monthly_generated_video_seconds_limit integer default 0
      check (monthly_generated_video_seconds_limit is null or monthly_generated_video_seconds_limit between 0 and 1000000);
-- El coste de IA de Marketing: monthly_ai_cost_limit y reserva atómica (20261011140000_marketing_generation_budget.sql).
-- ---------- Biblioteca: originales ----------
create table if not exists public.marketing_media (
  id                uuid primary key default gen_random_uuid(),
  tenant_id         uuid not null references public.tenants(id) on delete restrict,
  storage_bucket    text not null default 'marketing-assets' check (storage_bucket = 'marketing-assets'),
  storage_path      text not null check (length(storage_path) between 1 and 300
                                         and storage_path ~ '^[0-9a-f-]{36}/originals/[0-9a-f-]{36}/[A-Za-z0-9._-]{1,120}$'
                                         and storage_path !~ '\.\.'),
  original_filename text check (original_filename is null or length(original_filename) <= 200),
  media_type        text not null check (media_type in ('image','video')),
  mime_type         text not null check (mime_type in ('image/png','image/jpeg','image/webp','image/gif',
                                                       'video/mp4','video/quicktime','video/webm')),
  byte_size         bigint not null check (byte_size between 1 and 524288000),
  width             integer check (width is null or width between 1 and 20000),
  height            integer check (height is null or height between 1 and 20000),
  duration_ms       integer check (duration_ms is null or duration_ms between 0 and 36000000),
  checksum          text not null check (checksum ~ '^[0-9a-f]{64}$'),
  uploaded_by       uuid not null,
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now(),
  -- validación del formato (firma/cabeceras) y escaneo antivirus son cosas DISTINTAS
  validation_status   text not null default 'pending' check (validation_status in ('pending','passed','failed')),
  malware_scan_status text not null default 'not_scanned'
                      check (malware_scan_status in ('not_scanned','unavailable','pending','clean','infected','error')),
  malware_scanner     text check (malware_scanner is null or length(malware_scanner) <= 60),
  malware_scanned_at  timestamptz,
  contains_people   boolean,                                   -- null = desconocido
  contains_minors   boolean,                                   -- null = desconocido
  people_policy     text not null default 'exclude'
                    check (people_policy in ('exclude','anonymize','consented','no_people')),
  consent_status    text not null default 'unknown'
                    check (consent_status in ('unknown','not_required','pending','granted','revoked')),
  consent_updated_by uuid,
  consent_updated_at timestamptz,
  processing_status text not null default 'uploaded'
                    check (processing_status in ('uploaded','scanning','ready','rejected','processing','failed',
                                                 'archived','deleted')),
  deleted_by        uuid,
  deleted_at        timestamptz,
  delete_reason     text check (delete_reason is null or length(delete_reason) <= 300),
  metadata          jsonb not null default '{}'::jsonb,
  unique (id, tenant_id),
  unique (storage_bucket, storage_path),
  -- la ruta pertenece a ESTE tenant y a ESTE archivo
  check (split_part(storage_path, '/', 1) = tenant_id::text and split_part(storage_path, '/', 3) = id::text),
  -- coherencia de la política de personas
  check (people_policy <> 'no_people' or (contains_people is false and contains_minors is not true)),
  check (people_policy <> 'consented' or consent_status = 'granted'),
  -- si puede haber menores (o no se sabe) y hay o puede haber personas: siempre excluido
  check (people_policy = 'exclude' or contains_people is false or contains_minors is false),
  -- un consentimiento retirado excluye el archivo
  check (consent_status <> 'revoked' or people_policy = 'exclude'),
  -- sin escáner real nunca puede figurar como limpio
  check (malware_scan_status <> 'clean' or (malware_scanner is not null and malware_scanned_at is not null)),
  -- un archivo solo está listo si pasó la validación de formato
  check (processing_status not in ('ready','processing') or validation_status = 'passed'),
  -- borrado controlado: quién y cuándo
  constraint marketing_media_deleted_check check (processing_status <> 'deleted' or deleted_at is not null)
);
create index if not exists marketing_media_tenant_idx on public.marketing_media(tenant_id, processing_status, created_at desc);
create unique index if not exists marketing_media_checksum_idx on public.marketing_media(tenant_id, checksum)
  where processing_status <> 'deleted';
drop trigger if exists marketing_media_touch on public.marketing_media;
create trigger marketing_media_touch before update on public.marketing_media
  for each row execute function public.touch_updated_at();

-- El original no se SOBRESCRIBE: ruta, checksum, tamaño, tipo, autor y fecha son fijos. Sí puede
-- ELIMINARSE de forma controlada (estado 'deleted' + auditoría); la fila queda como registro.
-- No se puede eliminar si lo usa un trabajo activo.
create or replace function private.marketing_media_guard()
returns trigger language plpgsql set search_path = '' as $$
declare allowed text[];
begin
  if tg_op = 'DELETE' then
    raise exception 'use controlled deletion (status deleted) to keep the audit trail' using errcode = '42501';
  end if;
  if tg_op = 'INSERT' then
    if new.processing_status <> 'uploaded' or new.deleted_at is not null then
      raise exception 'new media must start as uploaded' using errcode = '23514';
    end if;
    return new;
  end if;
  if (new.tenant_id, new.storage_bucket, new.storage_path, new.mime_type, new.media_type, new.byte_size,
      new.checksum, new.uploaded_by, new.created_at, new.original_filename)
     is distinct from
     (old.tenant_id, old.storage_bucket, old.storage_path, old.mime_type, old.media_type, old.byte_size,
      old.checksum, old.uploaded_by, old.created_at, old.original_filename) then
    raise exception 'marketing original is immutable' using errcode = '42501';
  end if;
  if old.processing_status = 'deleted' then
    raise exception 'deleted media cannot change' using errcode = '42501';
  end if;
  if new.processing_status is distinct from old.processing_status then
    allowed := case old.processing_status
      when 'uploaded'   then array['scanning','ready','rejected','deleted']
      when 'scanning'   then array['ready','rejected','failed','deleted']
      when 'ready'      then array['processing','archived','deleted']
      when 'processing' then array['ready','failed']
      when 'failed'     then array['ready','archived','deleted']
      when 'rejected'   then array['archived','deleted']
      when 'archived'   then array['ready','deleted']
      else array[]::text[] end;
    if not (new.processing_status = any (allowed)) then
      raise exception 'invalid media status % -> %', old.processing_status, new.processing_status using errcode = '23514';
    end if;
    if new.processing_status = 'deleted' and exists (
         select 1 from public.marketing_generation_inputs i
           join public.marketing_generation_jobs j on j.id = i.job_id and j.tenant_id = i.tenant_id
          where i.tenant_id = new.tenant_id and i.media_id = new.id
            and j.status in ('draft','awaiting_generation_approval','queued','processing')) then
      raise exception 'media is used by an active generation job' using errcode = '23514';
    end if;
  end if;
  return new;
end $$;
drop trigger if exists marketing_media_guard on public.marketing_media;
create trigger marketing_media_guard before insert or update or delete on public.marketing_media
  for each row execute function private.marketing_media_guard();
-- ---------- Biblioteca: derivados (nunca sobrescriben el original) ----------
-- Mientras la anonimización sea simulada, un derivado es 'mock_only' (o 'awaiting_processing'):
-- nunca 'ready', nunca entra en un trabajo y nunca sale hacia proveedores.
create table if not exists public.marketing_media_derivatives (
  id                   uuid primary key default gen_random_uuid(),
  tenant_id            uuid not null references public.tenants(id) on delete restrict,
  media_id             uuid not null,
  kind                 text not null check (kind in ('anonymized','ai_adapted','thumbnail','render')),
  method               text check (method is null or method in ('pixelate_faces','blur_faces','crop_people',
                                                                 'silhouette','replace_background_and_people')),
  storage_path         text check (storage_path is null or (
                                   storage_path ~ '^[0-9a-f-]{36}/derivatives/[0-9a-f-]{36}/[0-9a-f-]{36}\.[a-z0-9]{2,5}$'
                                   and split_part(storage_path, '/', 1) = tenant_id::text
                                   and split_part(storage_path, '/', 3) = media_id::text)),
  mime_type            text,
  byte_size            bigint check (byte_size is null or byte_size >= 0),
  width                integer, height integer, duration_ms integer,
  checksum             text check (checksum is null or checksum ~ '^[0-9a-f]{64}$'),
  status               text not null default 'awaiting_processing'
                       check (status in ('awaiting_processing','mock_only','needs_review','ready','rejected',
                                         'failed','deleted')),
  is_mock              boolean not null default false,
  detection_confidence numeric(4,3) check (detection_confidence is null or detection_confidence between 0 and 1),
  review_required      boolean not null default true,
  reviewed_by          uuid,
  reviewed_at          timestamptz,
  created_by           uuid not null,
  created_at           timestamptz not null default now(),
  metadata             jsonb not null default '{}'::jsonb,
  unique (id, tenant_id),
  unique (storage_path),
  -- un derivado simulado nunca puede pasar por real
  check (not is_mock or status in ('mock_only','rejected','deleted')),
  -- un anonimizado solo queda "listo" si es real, tiene archivo y una persona lo revisó
  check (status <> 'ready' or kind <> 'anonymized' or (not is_mock and storage_path is not null
                                                        and reviewed_by is not null and reviewed_at is not null)),
  constraint marketing_media_derivatives_media_fk foreign key (media_id, tenant_id)
    references public.marketing_media(id, tenant_id) on delete restrict
);
create index if not exists marketing_media_derivatives_media_idx on public.marketing_media_derivatives(tenant_id, media_id);

create or replace function private.marketing_derivative_guard()
returns trigger language plpgsql set search_path = '' as $$
begin
  if tg_op = 'DELETE' then
    raise exception 'use controlled deletion (status deleted)' using errcode = '42501';
  end if;
  if (new.tenant_id, new.media_id, new.kind, new.method, new.created_by, new.created_at, new.is_mock)
     is distinct from (old.tenant_id, old.media_id, old.kind, old.method, old.created_by, old.created_at, old.is_mock)
     or (old.storage_path is not null and new.storage_path is distinct from old.storage_path)
     or (old.checksum is not null and new.checksum is distinct from old.checksum) then
    raise exception 'marketing derivative identity is immutable' using errcode = '42501';
  end if;
  if old.status = 'deleted' and new.status is distinct from old.status then
    raise exception 'deleted derivative cannot change' using errcode = '42501';
  end if;
  return new;
end $$;
drop trigger if exists marketing_derivative_guard on public.marketing_media_derivatives;
create trigger marketing_derivative_guard before update or delete on public.marketing_media_derivatives
  for each row execute function private.marketing_derivative_guard();
-- ---------- auditoría de la Biblioteca (solo inserción) ----------
create table if not exists public.marketing_media_events (
  id            uuid primary key default gen_random_uuid(),
  tenant_id     uuid not null references public.tenants(id) on delete restrict,
  media_id      uuid not null,
  derivative_id uuid,
  action        text not null check (action in ('upload','classify','consent_revoke','archive','restore',
                                                'anonymize_request','delete','storage_removed','storage_remove_failed',
                                                'expiry_warning','retention_change','purge_requested','purged',
                                                'purge_failed','protected','jobs_cancelled')),
  actor_id      uuid,
  actor_role    text check (actor_role is null or actor_role in ('owner','manager','system')),
  detail        jsonb not null default '{}'::jsonb,
  created_at    timestamptz not null default now(),
  constraint marketing_media_events_media_fk foreign key (media_id, tenant_id)
    references public.marketing_media(id, tenant_id) on delete restrict
);
create index if not exists marketing_media_events_media_idx on public.marketing_media_events(tenant_id, media_id, created_at);
-- ---------- trabajos de generación ----------
create table if not exists public.marketing_generation_jobs (
  id                uuid primary key default gen_random_uuid(),
  tenant_id         uuid not null references public.tenants(id) on delete restrict,
  content_id        uuid,
  created_by        uuid not null,
  task_type         text not null check (task_type in ('marketing_copy','storyboard','image_generation','image_edit',
                      'image_to_video','text_to_video','video_extension','background_replacement','face_detection',
                      'face_anonymization','transcription','subtitles','moderation','final_render','reel')),
  status            text not null default 'draft' check (status in ('draft','awaiting_generation_approval','queued',
                                                                    'processing','succeeded','failed','cancelled')),
  real_media_percent integer not null check (real_media_percent between 0 and 100),
  ai_media_percent   integer not null check (ai_media_percent between 0 and 100),
  quality_tier      text not null default 'draft' check (quality_tier in ('draft','standard','premium')),
  people_policy     text not null default 'exclude' check (people_policy in ('exclude','anonymize','consented','no_people')),
  maximum_cost      numeric(10,2) not null default 0 check (maximum_cost >= 0),
  estimated_cost    numeric(10,4) check (estimated_cost is null or estimated_cost >= 0),
  actual_cost       numeric(10,4) check (actual_cost is null or actual_cost >= 0),
  currency          text not null default 'USD' check (currency ~ '^[A-Z]{3}$'),
  router_strategy   text not null default 'cheapest_within_constraints' check (router_strategy ~ '^[a-z_]{1,40}$'),
  selected_provider text check (selected_provider is null or selected_provider ~ '^[a-z0-9_]{1,40}$'),
  selected_model    text check (selected_model is null or length(selected_model) <= 120),
  provider_job_id   text check (provider_job_id is null or length(provider_job_id) <= 200),
  idempotency_key   text not null check (length(idempotency_key) between 16 and 120),
  regeneration_of   uuid,
  approved_by       uuid,
  request_metadata  jsonb not null default '{}'::jsonb,
  result_metadata   jsonb not null default '{}'::jsonb,
  error_code        text check (error_code is null or error_code ~ '^[a-z0-9_]{1,60}$'),   -- código público seguro
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now(),
  approved_at       timestamptz,
  completed_at      timestamptz,
  unique (id, tenant_id),
  unique (tenant_id, idempotency_key),
  check (real_media_percent + ai_media_percent = 100),
  check (actual_cost is null or actual_cost <= maximum_cost or status in ('failed','cancelled','succeeded')),
  constraint marketing_generation_jobs_content_fk foreign key (content_id, tenant_id)
    references public.marketing_content(id, tenant_id) on delete restrict
);
create index if not exists marketing_generation_jobs_tenant_idx on public.marketing_generation_jobs(tenant_id, status, created_at desc);
drop trigger if exists marketing_generation_jobs_touch on public.marketing_generation_jobs;
create trigger marketing_generation_jobs_touch before update on public.marketing_generation_jobs
  for each row execute function public.touch_updated_at();

-- Reglas de estado (espejo de services/marketing_jobs_domain.py → JOB_TRANSITIONS).
create or replace function private.marketing_job_transition()
returns trigger language plpgsql set search_path = '' as $$
declare allowed text[];
begin
  if tg_op = 'INSERT' then
    if new.status <> 'draft' or new.approved_at is not null or new.approved_by is not null then
      raise exception 'new generation job must start as unapproved draft' using errcode = '23514';
    end if;
    return new;
  end if;
  if (new.tenant_id, new.created_by, new.idempotency_key, new.task_type, new.created_at, new.regeneration_of)
     is distinct from (old.tenant_id, old.created_by, old.idempotency_key, old.task_type, old.created_at, old.regeneration_of) then
    raise exception 'generation job identity is immutable' using errcode = '23514';
  end if;
  -- una vez aprobada, la mezcla, la calidad, la política y el presupuesto no cambian
  if old.approved_at is not null and (new.real_media_percent, new.ai_media_percent, new.quality_tier, new.people_policy,
       new.maximum_cost, new.approved_at, new.approved_by)
     is distinct from (old.real_media_percent, old.ai_media_percent, old.quality_tier, old.people_policy,
       old.maximum_cost, old.approved_at, old.approved_by) then
    raise exception 'approved generation parameters are immutable' using errcode = '23514';
  end if;
  -- la evidencia de coste y proveedor nunca se borra ni disminuye
  if (old.actual_cost is not null and (new.actual_cost is null or new.actual_cost < old.actual_cost))
     or (old.selected_provider is not null and new.selected_provider is distinct from old.selected_provider)
     or (old.selected_model is not null and new.selected_model is distinct from old.selected_model)
     or (old.provider_job_id is not null and new.provider_job_id is distinct from old.provider_job_id) then
    raise exception 'generation cost/provider evidence is immutable' using errcode = '23514';
  end if;
  if new.status is distinct from old.status then
    allowed := case old.status
      when 'draft'                        then array['awaiting_generation_approval','cancelled']
      when 'awaiting_generation_approval' then array['queued','draft','cancelled']
      when 'queued'                       then array['processing','failed','cancelled']
      when 'processing'                   then array['succeeded','failed','cancelled']
      else array[]::text[] end;                       -- succeeded / failed / cancelled son finales
    if not (new.status = any (allowed)) then
      raise exception 'invalid generation job transition % -> %', old.status, new.status using errcode = '23514';
    end if;
    -- nada entra en cola sin aprobación explícita de owner/manager
    if new.status = 'queued' and (new.approved_at is null or new.approved_by is null) then
      raise exception 'generation approval required' using errcode = '23514';
    end if;
    -- al entrar en cola o empezar: las entradas siguen permitidas (consentimiento retirado, menores,
    -- archivo eliminado o excluido → bloqueo inmediato)
    if new.status in ('queued','processing') and exists (
         select 1 from public.marketing_generation_inputs i
           join public.marketing_media m on m.id = i.media_id and m.tenant_id = i.tenant_id
          where i.job_id = new.id and i.tenant_id = new.tenant_id
            and (m.processing_status <> 'ready'
                 or (m.people_policy = 'exclude' and m.contains_people is not false)
                 or (m.people_policy = 'consented' and m.consent_status <> 'granted')
                 or (m.contains_people is not false and m.contains_minors is not false))) then
      raise exception 'generation inputs are no longer allowed' using errcode = '23514';
    end if;
  elsif old.status in ('succeeded','failed','cancelled') and (new.real_media_percent, new.ai_media_percent,
        new.result_metadata, new.error_code, new.completed_at)
        is distinct from (old.real_media_percent, old.ai_media_percent, old.result_metadata, old.error_code, old.completed_at) then
    raise exception 'finished generation job is immutable' using errcode = '23514';
  end if;
  return new;
end $$;
drop trigger if exists marketing_job_transition on public.marketing_generation_jobs;
create trigger marketing_job_transition before insert or update on public.marketing_generation_jobs
  for each row execute function private.marketing_job_transition();

create or replace function private.marketing_no_delete()
returns trigger language plpgsql set search_path = '' as $$
begin raise exception 'marketing generation history is immutable' using errcode = '42501'; end $$;
drop trigger if exists marketing_generation_jobs_no_delete on public.marketing_generation_jobs;
create trigger marketing_generation_jobs_no_delete before delete on public.marketing_generation_jobs
  for each row execute function private.marketing_no_delete();
-- ---------- historial inmutable de los trabajos ----------
create table if not exists public.marketing_generation_job_events (
  id          uuid primary key default gen_random_uuid(),
  tenant_id   uuid not null references public.tenants(id) on delete restrict,
  job_id      uuid not null,
  action      text not null check (action in ('create','request_approval','approve','reopen','start','succeed',
                                              'fail','cancel','estimate','review')),
  from_status text,
  to_status   text not null,
  detail      jsonb not null default '{}'::jsonb,
  actor_id    uuid,
  actor_role  text check (actor_role is null or actor_role in ('owner','manager','system')),
  created_at  timestamptz not null default now(),
  constraint marketing_generation_job_events_job_fk foreign key (job_id, tenant_id)
    references public.marketing_generation_jobs(id, tenant_id) on delete restrict
);
create index if not exists marketing_generation_job_events_job_idx
  on public.marketing_generation_job_events(tenant_id, job_id, created_at);
-- ---------- entradas, salidas y uso ----------
create table if not exists public.marketing_generation_inputs (
  id            uuid primary key default gen_random_uuid(),
  tenant_id     uuid not null references public.tenants(id) on delete restrict,
  job_id        uuid not null,
  media_id      uuid,
  derivative_id uuid,
  role          text not null default 'source' check (role in ('source','reference','logo','audio')),
  privacy_class text not null check (privacy_class in ('synthetic_only','business_media_no_people','anonymized_people',
                                                       'consented_people','restricted')),
  created_at    timestamptz not null default now(),
  check (media_id is not null or derivative_id is not null),
  constraint marketing_generation_inputs_job_fk foreign key (job_id, tenant_id)
    references public.marketing_generation_jobs(id, tenant_id) on delete restrict,
  -- claves compuestas: un trabajo NUNCA puede usar archivos de otro tenant
  constraint marketing_generation_inputs_media_fk foreign key (media_id, tenant_id)
    references public.marketing_media(id, tenant_id) on delete restrict,
  constraint marketing_generation_inputs_derivative_fk foreign key (derivative_id, tenant_id)
    references public.marketing_media_derivatives(id, tenant_id) on delete restrict
);
create index if not exists marketing_generation_inputs_job_idx on public.marketing_generation_inputs(tenant_id, job_id);
-- Solo entran archivos listos y permitidos; nunca un derivado simulado ni uno sin revisar.
create or replace function private.marketing_input_guard()
returns trigger language plpgsql set search_path = '' as $$
begin
  if new.media_id is not null and not exists (
       select 1 from public.marketing_media m where m.id = new.media_id and m.tenant_id = new.tenant_id
          and m.processing_status = 'ready' and m.validation_status = 'passed'
          and not (m.people_policy = 'exclude' and m.contains_people is not false)
          and not (m.contains_people is not false and m.contains_minors is not false)) then
    raise exception 'media not allowed as generation input' using errcode = '23514';
  end if;
  if new.derivative_id is not null and not exists (
       select 1 from public.marketing_media_derivatives d where d.id = new.derivative_id and d.tenant_id = new.tenant_id
          and d.status = 'ready' and not d.is_mock) then
    raise exception 'derivative not allowed as generation input' using errcode = '23514';
  end if;
  return new;
end $$;
drop trigger if exists marketing_input_guard on public.marketing_generation_inputs;
create trigger marketing_input_guard before insert on public.marketing_generation_inputs
  for each row execute function private.marketing_input_guard();

create table if not exists public.marketing_generation_outputs (
  id            uuid primary key default gen_random_uuid(),
  tenant_id     uuid not null references public.tenants(id) on delete restrict,
  job_id        uuid not null,
  kind          text not null check (kind in ('script','storyboard','scene','image','video','subtitles','cover','render')),
  scene_index   integer check (scene_index is null or scene_index between 0 and 200),
  origin        text check (origin is null or origin in ('client_original','client_ai_adapted','ai_generated')),
  duration_ms   integer check (duration_ms is null or duration_ms between 0 and 600000),
  storage_path  text check (storage_path is null or (storage_path ~ '^[0-9a-f-]{36}/derivatives/' and storage_path !~ '\.\.'
                                                     and split_part(storage_path, '/', 1) = tenant_id::text)),
  mime_type     text,
  review_status text not null default 'generated' check (review_status in ('generated','in_review','approved','rejected')),
  metadata      jsonb not null default '{}'::jsonb,
  created_at    timestamptz not null default now(),
  constraint marketing_generation_outputs_job_fk foreign key (job_id, tenant_id)
    references public.marketing_generation_jobs(id, tenant_id) on delete restrict
);
create index if not exists marketing_generation_outputs_job_idx on public.marketing_generation_outputs(tenant_id, job_id, scene_index);

create table if not exists public.marketing_model_usage (
  id              uuid primary key default gen_random_uuid(),
  tenant_id       uuid not null references public.tenants(id) on delete restrict,
  job_id          uuid not null,
  provider        text not null check (provider ~ '^[a-z0-9_]{1,40}$'),
  model_id        text not null check (length(model_id) between 1 and 120),
  task_type       text not null,
  catalog_version text not null check (length(catalog_version) between 1 and 40),
  billing_unit    text not null check (billing_unit in ('request','image','second','1k_tokens','minute')),
  units           numeric(12,3) not null check (units >= 0),
  estimated_cost  numeric(10,4) not null check (estimated_cost >= 0),
  actual_cost     numeric(10,4) check (actual_cost is null or actual_cost >= 0),
  currency        text not null default 'USD' check (currency ~ '^[A-Z]{3}$'),
  idempotency_key text not null check (length(idempotency_key) between 16 and 160),
  provider_job_id text check (provider_job_id is null or length(provider_job_id) <= 200),
  status          text not null check (status in ('estimated','charged','not_charged','failed')),
  created_at      timestamptz not null default now(),
  unique (tenant_id, idempotency_key),          -- un reintento nunca crea un segundo cargo
  constraint marketing_model_usage_job_fk foreign key (job_id, tenant_id)
    references public.marketing_generation_jobs(id, tenant_id) on delete restrict
);
create index if not exists marketing_model_usage_tenant_idx on public.marketing_model_usage(tenant_id, created_at);

-- historial, entradas y uso: solo inserción
create or replace function private.marketing_append_only()
returns trigger language plpgsql set search_path = '' as $$
begin raise exception '% is append-only', tg_table_name using errcode = '42501'; end $$;
do $$
declare t text;
begin
  foreach t in array array['marketing_generation_job_events','marketing_generation_inputs','marketing_model_usage',
                           'marketing_media_events'] loop
    execute format('drop trigger if exists %I on public.%I', t || '_append_only', t);
    execute format('create trigger %I before update or delete on public.%I for each row execute function private.marketing_append_only()',
                   t || '_append_only', t);
  end loop;
end $$;
drop trigger if exists marketing_generation_outputs_no_delete on public.marketing_generation_outputs;
create trigger marketing_generation_outputs_no_delete before delete on public.marketing_generation_outputs
  for each row execute function private.marketing_no_delete();

revoke all on function private.marketing_media_guard(), private.marketing_derivative_guard(), private.marketing_job_transition(),
  private.marketing_no_delete(), private.marketing_input_guard(), private.marketing_append_only() from public, anon, authenticated;
-- ---------- RLS y mínimo privilegio (mismo patrón que Fase 1) ----------
do $$
declare t text;
begin
  foreach t in array array['marketing_media','marketing_media_derivatives','marketing_media_events','marketing_generation_jobs',
                           'marketing_generation_job_events','marketing_generation_inputs',
                           'marketing_generation_outputs','marketing_model_usage'] loop
    execute format('alter table public.%I enable row level security', t);
    execute format('drop policy if exists %I on public.%I', t || '_select', t);
    execute format($p$create policy %I on public.%I for select to authenticated
                     using (private.has_tenant_role(tenant_id, array['owner','manager']))$p$, t || '_select', t);
    execute format('revoke all privileges on public.%I from public, anon, authenticated, service_role', t);
    execute format('grant select on public.%I to authenticated', t);
    execute format('grant select, insert, update, delete on public.%I to service_role', t);
  end loop;
end $$;
-- ---------- bucket privado: se añade WebM (los demás tipos se conservan) ----------
do $$ begin
  if exists (select 1 from pg_namespace where nspname = 'storage') then
    update storage.buckets
       set public = false,
           allowed_mime_types = array['image/jpeg','image/png','image/webp','image/gif','video/mp4',
                                      'video/quicktime','video/webm','audio/mpeg']
     where id = 'marketing-assets';
  end if;
end $$;
