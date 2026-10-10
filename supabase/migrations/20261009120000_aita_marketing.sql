-- =====================================================================
-- AITA Marketing — Fase 1 (fundación). Módulo del Manager Panel (#/marketing).
--
-- * marketing_settings         — configuración comercial por tenant (plan y límites).
-- * marketing_brand_profiles   — Brand Kit (identidad de marca) por tenant.
-- * marketing_campaigns        — campañas.
-- * marketing_content          — piezas de contenido y su estado.
-- * marketing_assets           — metadatos y rutas de archivos (nunca binarios).
-- * marketing_approval_events  — historial inmutable de cambios de estado.
-- * marketing_publications     — cola de publicación por canal (con idempotency_key).
--
-- Permisos (RLS + privilegios mínimos):
--   PUBLIC / anon → ningún privilegio. authenticated → solo SELECT. service_role → SELECT,
--   INSERT, UPDATE, DELETE (sin TRUNCATE, REFERENCES, TRIGGER ni MAINTAIN).
--   owner / manager → LEEN los datos de su tenant.
--   staff / socios / otros tenants / anon → nada.
--   Escritura: solo el backend (service role) después de comprobar rol, tenant
--   y reglas de dominio. Los usuarios no pueden escribir directamente, así no
--   pueden saltarse aprobaciones ni límites del plan.
--
-- Las transiciones de estado también se imponen aquí (trigger), en espejo de
-- services/marketing_domain.py → TRANSITIONS. Nada se programa ni se publica
-- sin estar aprobado.
--
-- Fase 1 no publica nada en redes: no hay credenciales ni proveedores reales.
-- Idempotente: se puede ejecutar más de una vez. No borra ni renombra nada.
-- =====================================================================

create or replace function public.touch_updated_at()
returns trigger language plpgsql set search_path = '' as $$
begin new.updated_at := now(); return new; end $$;

-- ---------- configuración comercial ----------
-- La define el operador de la plataforma (SQL / service role), no el propio tenant:
-- así una empresa no puede subirse sus propios límites.
create table if not exists public.marketing_settings (
  tenant_id               uuid primary key references public.tenants(id) on delete cascade,
  marketing_enabled       boolean not null default false,
  approval_required       boolean not null default true,
  plan_code               text not null default 'none' check (plan_code ~ '^[a-z0-9_]{1,40}$'),
  monthly_post_limit      integer check (monthly_post_limit is null or monthly_post_limit between 0 and 10000),
  monthly_image_limit     integer check (monthly_image_limit is null or monthly_image_limit between 0 and 10000),
  monthly_reel_limit      integer check (monthly_reel_limit is null or monthly_reel_limit between 0 and 10000),
  connected_channel_limit integer check (connected_channel_limit is null or connected_channel_limit between 0 and 100),
  competitor_limit        integer check (competitor_limit is null or competitor_limit between 0 and 100),
  created_at              timestamptz not null default now(),
  updated_at              timestamptz not null default now()
);
drop trigger if exists marketing_settings_touch on public.marketing_settings;
create trigger marketing_settings_touch before update on public.marketing_settings
  for each row execute function public.touch_updated_at();

-- ---------- Brand Kit ----------
create table if not exists public.marketing_brand_profiles (
  id                uuid primary key default gen_random_uuid(),
  tenant_id         uuid not null unique references public.tenants(id) on delete cascade,
  business_name     text check (business_name is null or length(btrim(business_name)) between 1 and 120),
  description       text check (description is null or length(description) <= 2000),
  target_audience   text check (target_audience is null or length(target_audience) <= 1000),
  tone              text check (tone is null or length(tone) <= 500),
  languages         text[] not null default '{}' check (languages <@ array['en','es']::text[]),
  priority_services text[] not null default '{}' check (cardinality(priority_services) <= 30),
  cta               text check (cta is null or length(cta) <= 200),
  phone             text check (phone is null or phone ~ '^[0-9+()\-. ]{7,25}$'),
  website           text check (website is null or website ~ '^https?://[A-Za-z0-9.-]+\.[A-Za-z]{2,}'),
  color_primary     text check (color_primary is null or color_primary ~ '^#[0-9A-Fa-f]{6}$'),
  color_accent      text check (color_accent is null or color_accent ~ '^#[0-9A-Fa-f]{6}$'),
  color_secondary   text check (color_secondary is null or color_secondary ~ '^#[0-9A-Fa-f]{6}$'),
  -- Solo ruta local segura (/media/…; nunca //host, /\host ni URLs externas).
  -- Sin imágenes en base64: los binarios no se guardan en tablas. Si es null se
  -- usa por referencia el logo del tenant (tenants.branding.logo_url).
  logo_url          text check (logo_url is null or (length(logo_url) <= 500 and logo_url ~ '^/([^/\\[:space:]][^\\[:space:]]*)?$')),
  banned_topics     text[] not null default '{}' check (cardinality(banned_topics) <= 50),
  compliance_notes  text check (compliance_notes is null or length(compliance_notes) <= 2000),
  ai_instructions   text check (ai_instructions is null or length(ai_instructions) <= 4000),
  updated_by        uuid,
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now()
);
drop trigger if exists marketing_brand_profiles_touch on public.marketing_brand_profiles;
create trigger marketing_brand_profiles_touch before update on public.marketing_brand_profiles
  for each row execute function public.touch_updated_at();

-- ---------- campañas ----------
create table if not exists public.marketing_campaigns (
  id          uuid primary key default gen_random_uuid(),
  tenant_id   uuid not null references public.tenants(id) on delete restrict,
  name        text not null check (length(btrim(name)) between 1 and 120),
  objective   text check (objective is null or length(objective) <= 300),
  description text check (description is null or length(description) <= 2000),
  start_date  date,
  end_date    date,
  budget      numeric(12,2) check (budget is null or (budget >= 0 and budget < 100000000)),
  status      text not null default 'planned' check (status in ('planned','active','paused','completed','archived')),
  created_by  uuid not null,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now(),
  unique (id, tenant_id),
  check (end_date is null or start_date is null or end_date >= start_date)
);
create index if not exists marketing_campaigns_tenant_idx on public.marketing_campaigns(tenant_id, status, start_date);
drop trigger if exists marketing_campaigns_touch on public.marketing_campaigns;
create trigger marketing_campaigns_touch before update on public.marketing_campaigns
  for each row execute function public.touch_updated_at();

-- ---------- contenido ----------
create table if not exists public.marketing_content (
  id           uuid primary key default gen_random_uuid(),
  tenant_id    uuid not null references public.tenants(id) on delete restrict,
  campaign_id  uuid,
  title        text not null check (length(btrim(title)) between 1 and 160),
  objective    text check (objective is null or length(objective) <= 300),
  topic        text check (topic is null or length(topic) <= 300),
  format       text not null check (format in ('image','carousel','reel','story','video','text')),
  channels     text[] not null default '{}'
               check (channels <@ array['instagram','facebook','tiktok','linkedin','x','youtube','google_business','threads']::text[]),
  language     text not null default 'en' check (language in ('en','es')),
  caption      text check (caption is null or length(caption) <= 5000),
  script       text check (script is null or length(script) <= 10000),
  cta          text check (cta is null or length(cta) <= 200),
  hashtags     text[] not null default '{}' check (cardinality(hashtags) <= 30),
  planned_at   timestamptz,
  scheduled_at timestamptz,
  notes        text check (notes is null or length(notes) <= 2000),
  status       text not null default 'draft'
               check (status in ('idea','draft','generating','review','approved','rejected',
                                 'scheduled','publishing','published','failed','archived')),
  assigned_to  uuid,           -- responsable (auth.users.id de alguien del equipo)
  submitted_at timestamptz,
  approved_by  uuid,
  approved_at  timestamptz,
  created_by   uuid not null,
  updated_by   uuid,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now(),
  unique (id, tenant_id),
  check (status <> 'scheduled' or scheduled_at is not null),
  constraint marketing_content_campaign_fk foreign key (campaign_id, tenant_id)
    references public.marketing_campaigns(id, tenant_id) on delete set null (campaign_id)
);
create index if not exists marketing_content_tenant_status_idx on public.marketing_content(tenant_id, status);
create index if not exists marketing_content_tenant_planned_idx on public.marketing_content(tenant_id, planned_at);
create index if not exists marketing_content_tenant_scheduled_idx on public.marketing_content(tenant_id, scheduled_at)
  where scheduled_at is not null;
create index if not exists marketing_content_campaign_idx on public.marketing_content(campaign_id) where campaign_id is not null;
drop trigger if exists marketing_content_touch on public.marketing_content;
create trigger marketing_content_touch before update on public.marketing_content
  for each row execute function public.touch_updated_at();

-- Reglas de estado (espejo de services/marketing_domain.py → TRANSITIONS).
create or replace function private.marketing_content_transition()
returns trigger language plpgsql set search_path = '' as $$
declare allowed text[];
begin
  if tg_op = 'INSERT' then
    if new.status not in ('idea','draft') then
      raise exception 'new content must start as idea or draft' using errcode = '23514';
    end if;
    return new;
  end if;
  if new.tenant_id is distinct from old.tenant_id then
    raise exception 'tenant_id is immutable' using errcode = '23514';
  end if;
  if new.status is distinct from old.status then
    allowed := case old.status
      when 'idea'       then array['draft','archived']
      when 'draft'      then array['idea','generating','review','approved','archived']
      when 'generating' then array['draft','review','failed']
      when 'review'     then array['approved','rejected','draft']
      when 'approved'   then array['scheduled','draft','archived']
      when 'rejected'   then array['draft','archived']
      when 'scheduled'  then array['approved','publishing']
      when 'publishing' then array['published','failed']
      when 'published'  then array['archived']
      when 'failed'     then array['draft','archived']
      when 'archived'   then array['draft']
      else array[]::text[] end;
    if not (new.status = any (allowed)) then
      raise exception 'invalid marketing status transition % -> %', old.status, new.status using errcode = '23514';
    end if;
    -- Saltarse la revisión (draft → approved) solo si el tenant lo permite.
    if old.status = 'draft' and new.status = 'approved' and coalesce((
         select s.approval_required from public.marketing_settings s where s.tenant_id = new.tenant_id), true) then
      raise exception 'approval required' using errcode = '23514';
    end if;
  end if;
  return new;
end $$;
revoke all on function private.marketing_content_transition() from public, anon, authenticated;
drop trigger if exists marketing_content_transition on public.marketing_content;
create trigger marketing_content_transition before insert or update on public.marketing_content
  for each row execute function private.marketing_content_transition();

-- ---------- archivos (solo metadatos y rutas) ----------
create table if not exists public.marketing_assets (
  id             uuid primary key default gen_random_uuid(),
  tenant_id      uuid not null references public.tenants(id) on delete restrict,
  content_id     uuid,
  kind           text not null check (kind in ('image','video','audio','logo','document','other')),
  source         text not null default 'upload' check (source in ('upload','generated','rendered','tenant')),
  provider       text check (provider is null or provider ~ '^[a-z0-9_]{1,40}$'),
  storage_bucket text not null default 'marketing-assets' check (storage_bucket ~ '^[a-z0-9-]{3,63}$'),
  storage_path   text not null check (length(storage_path) between 1 and 500
                                      and storage_path !~ '(^/|\.\.|://|^data:)'),
  mime_type      text check (mime_type is null or mime_type ~ '^[a-z]+/[a-z0-9.+-]+$'),
  size_bytes     bigint check (size_bytes is null or size_bytes between 0 and 2147483648),
  width          integer check (width is null or width > 0),
  height         integer check (height is null or height > 0),
  duration_ms    integer check (duration_ms is null or duration_ms >= 0),
  metadata       jsonb not null default '{}'::jsonb,
  created_by     uuid,
  created_at     timestamptz not null default now(),
  unique (id, tenant_id),
  unique (storage_bucket, storage_path),
  constraint marketing_assets_content_fk foreign key (content_id, tenant_id)
    references public.marketing_content(id, tenant_id) on delete set null (content_id)
);
create index if not exists marketing_assets_tenant_idx on public.marketing_assets(tenant_id, content_id);

-- ---------- historial de aprobación (inmutable) ----------
create table if not exists public.marketing_approval_events (
  id          uuid primary key default gen_random_uuid(),
  tenant_id   uuid not null references public.tenants(id) on delete restrict,
  content_id  uuid not null,
  action      text not null check (action in ('create','submit','approve','reject','reopen','schedule',
                                              'unschedule','archive','restore','status')),
  from_status text,
  to_status   text not null,
  comment     text check (comment is null or length(comment) <= 2000),
  actor_id    uuid not null,
  actor_role  text check (actor_role is null or actor_role in ('owner','manager','staff','system')),
  created_at  timestamptz not null default now(),
  constraint marketing_approval_events_content_fk foreign key (content_id, tenant_id)
    references public.marketing_content(id, tenant_id) on delete restrict
);
create index if not exists marketing_approval_events_content_idx
  on public.marketing_approval_events(tenant_id, content_id, created_at);

create or replace function private.marketing_events_immutable()
returns trigger language plpgsql set search_path = '' as $$
begin raise exception 'marketing approval history is immutable' using errcode = '42501'; end $$;
revoke all on function private.marketing_events_immutable() from public, anon, authenticated;
drop trigger if exists marketing_approval_events_immutable on public.marketing_approval_events;
create trigger marketing_approval_events_immutable before update or delete on public.marketing_approval_events
  for each row execute function private.marketing_events_immutable();

-- ---------- cola de publicación ----------
create table if not exists public.marketing_publications (
  id              uuid primary key default gen_random_uuid(),
  tenant_id       uuid not null references public.tenants(id) on delete restrict,
  content_id      uuid not null,
  channel         text not null
                  check (channel in ('instagram','facebook','tiktok','linkedin','x','youtube','google_business','threads')),
  provider        text not null default 'disabled' check (provider ~ '^[a-z0-9_]{1,40}$'),
  status          text not null default 'pending'
                  check (status in ('pending','queued','publishing','published','failed','cancelled')),
  scheduled_at    timestamptz,
  published_at    timestamptz,
  idempotency_key text not null check (length(idempotency_key) between 16 and 120),
  external_id     text check (external_id is null or length(external_id) <= 200),
  external_url    text check (external_url is null or external_url ~ '^https://'),
  attempts        integer not null default 0 check (attempts >= 0),
  last_error      text check (last_error is null or length(last_error) <= 300),   -- sin tokens (redact)
  metrics         jsonb not null default '{}'::jsonb,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now(),
  unique (tenant_id, idempotency_key),
  constraint marketing_publications_content_fk foreign key (content_id, tenant_id)
    references public.marketing_content(id, tenant_id) on delete restrict
);
create index if not exists marketing_publications_tenant_idx
  on public.marketing_publications(tenant_id, status, scheduled_at);
create index if not exists marketing_publications_content_idx on public.marketing_publications(content_id);
drop trigger if exists marketing_publications_touch on public.marketing_publications;
create trigger marketing_publications_touch before update on public.marketing_publications
  for each row execute function public.touch_updated_at();

-- ---------- RLS ----------
do $$
declare t text;
begin
  foreach t in array array['marketing_settings','marketing_brand_profiles','marketing_campaigns','marketing_content',
                           'marketing_assets','marketing_approval_events','marketing_publications'] loop
    execute format('alter table public.%I enable row level security', t);
    execute format('drop policy if exists %I on public.%I', t || '_select', t);
    -- leer: solo owner/manager del mismo tenant (staff podrá recibir acceso en el futuro)
    execute format($p$create policy %I on public.%I for select to authenticated
                     using (private.has_tenant_role(tenant_id, array['owner','manager']))$p$, t || '_select', t);
    -- Mínimo privilegio. Supabase concede por defecto TODO (incl. REFERENCES, TRIGGER y, en
    -- PostgreSQL 17, MAINTAIN) a anon/authenticated/service_role en tablas nuevas de public:
    -- se revoca todo y se concede solo lo necesario.
    execute format('revoke all privileges on public.%I from public, anon, authenticated, service_role', t);
    -- leer: authenticated solo SELECT (y RLS limita a owner/manager de su tenant)
    execute format('grant select on public.%I to authenticated', t);
    -- escribir: solo el backend (service role) tras validar rol y reglas de dominio
    execute format('grant select, insert, update, delete on public.%I to service_role', t);
  end loop;
end $$;

-- ---------- bucket privado para archivos de marketing ----------
do $$ begin
  if exists (select 1 from pg_namespace where nspname = 'storage') then
    insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
    values ('marketing-assets', 'marketing-assets', false, 524288000,
            array['image/jpeg','image/png','image/webp','image/gif','video/mp4','video/quicktime','audio/mpeg'])
    on conflict (id) do update set public = false, file_size_limit = excluded.file_size_limit,
                                   allowed_mime_types = excluded.allowed_mime_types;
  end if;
end $$;

-- ---------- configuración inicial: módulo apagado, aprobación obligatoria ----------
-- No se activa para nadie automáticamente ni se tocan tenants.modules.
insert into public.marketing_settings (tenant_id)
select id from public.tenants
on conflict (tenant_id) do nothing;
