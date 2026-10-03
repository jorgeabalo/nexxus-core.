-- =====================================================================
-- AITA/Nexxus — Manager Panel Fase 1
-- 1) Núcleo multi-tenant: tenants, tenant_users, tenant_invites
--    + tenant_id en todas las tablas de negocio existentes.
-- =====================================================================

-- ---------- tenants ----------
create table if not exists public.tenants (
  id           uuid primary key default gen_random_uuid(),
  slug         text not null unique check (slug ~ '^[a-z0-9_]+$'),
  name         text not null,
  vertical     text not null default 'generic',
  timezone     text not null default 'America/Chicago',
  twilio_phone text unique,
  branding     jsonb not null default '{}'::jsonb,   -- nombre visible, colores, logo
  modules      jsonb not null default '{}'::jsonb,   -- módulos habilitados del panel
  settings     jsonb not null default '{}'::jsonb,
  active       boolean not null default true,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now()
);

insert into public.tenants (slug, name, vertical, timezone, twilio_phone, branding, modules)
values (
  'golden_age',
  'Golden Age Fitness & Training',
  'gym',
  'America/Chicago',
  '+13462457940',
  '{"display_name":"GOLDEN AGE","subtitle":"Manager Dashboard","color_primary":"#0B1F3A","color_accent":"#C9A227","currency":"USD","locale":"en-US"}'::jsonb,
  '{"dashboard":true,"members":true,"schedule":true,"claudia":true,"payments":true,"accounting":false,"marketing":false,"inventory":false,"agents":false,"settings":false}'::jsonb
)
on conflict (slug) do nothing;

-- ---------- tenant_users (usuario de Supabase Auth ↔ tenant + rol) ----------
create table if not exists public.tenant_users (
  id         uuid primary key default gen_random_uuid(),
  tenant_id  uuid not null references public.tenants(id) on delete cascade,
  user_id    uuid not null references auth.users(id) on delete cascade,
  role       text not null check (role in ('owner','manager','staff')),
  staff_id   uuid references public.staff(id) on delete set null,
  active     boolean not null default true,
  created_at timestamptz not null default now(),
  unique (tenant_id, user_id)
);
create index if not exists tenant_users_user_idx on public.tenant_users(user_id);

-- ---------- tenant_invites (asignación por email, sin contraseñas en código) ----------
create table if not exists public.tenant_invites (
  id          uuid primary key default gen_random_uuid(),
  tenant_id   uuid not null references public.tenants(id) on delete cascade,
  email       text not null check (email = lower(email)),
  role        text not null check (role in ('owner','manager','staff')),
  staff_id    uuid references public.staff(id) on delete set null,
  created_at  timestamptz not null default now(),
  accepted_at timestamptz,
  unique (tenant_id, email)
);

-- ---------- tenant_id en las tablas existentes ----------
do $$
declare
  t  text;
  ga uuid;
begin
  select id into ga from public.tenants where slug = 'golden_age';
  foreach t in array array['members','staff','services','check_ins','appointments',
                           'payments','messages','measurements','member_onboarding']
  loop
    if not exists (select 1 from information_schema.columns
                   where table_schema='public' and table_name=t and column_name='tenant_id') then
      execute format('alter table public.%I add column tenant_id uuid references public.tenants(id) on delete restrict', t);
      execute format('update public.%I set tenant_id = %L where tenant_id is null', t, ga);
      execute format('alter table public.%I alter column tenant_id set not null', t);
      execute format('create index if not exists %I on public.%I(tenant_id)', t || '_tenant_idx', t);
    end if;
  end loop;
end $$;

-- member_id (código visible del socio) pasa a ser único por tenant, no global
alter table public.members drop constraint if exists members_member_id_key;
create unique index if not exists members_tenant_member_code_key on public.members(tenant_id, member_id) where member_id is not null;
create index if not exists members_tenant_phone_idx on public.members(tenant_id, phone);

-- Claves compuestas (id, tenant_id) para que ninguna fila hija pueda apuntar
-- a un socio / staff de OTRO tenant. Reemplazan a las FKs simples.
alter table public.members add constraint members_id_tenant_key unique (id, tenant_id);
alter table public.staff   add constraint staff_id_tenant_key   unique (id, tenant_id);

alter table public.check_ins         drop constraint if exists check_ins_member_id_fkey;
alter table public.payments          drop constraint if exists payments_member_id_fkey;
alter table public.appointments      drop constraint if exists appointments_member_id_fkey;
alter table public.appointments      drop constraint if exists appointments_staff_id_fkey;
alter table public.messages          drop constraint if exists messages_member_id_fkey;
alter table public.measurements      drop constraint if exists measurements_member_id_fkey;
alter table public.member_onboarding drop constraint if exists member_onboarding_member_id_fkey;

alter table public.check_ins         add constraint check_ins_member_fk         foreign key (member_id, tenant_id) references public.members(id, tenant_id) on delete cascade;
alter table public.payments          add constraint payments_member_fk          foreign key (member_id, tenant_id) references public.members(id, tenant_id) on delete cascade;
alter table public.appointments      add constraint appointments_member_fk      foreign key (member_id, tenant_id) references public.members(id, tenant_id) on delete cascade;
alter table public.appointments      add constraint appointments_staff_fk       foreign key (staff_id,  tenant_id) references public.staff(id,   tenant_id) on delete set null (staff_id);
alter table public.messages          add constraint messages_member_fk          foreign key (member_id, tenant_id) references public.members(id, tenant_id) on delete cascade;
alter table public.measurements      add constraint measurements_member_fk      foreign key (member_id, tenant_id) references public.members(id, tenant_id) on delete cascade;
alter table public.member_onboarding add constraint member_onboarding_member_fk foreign key (member_id, tenant_id) references public.members(id, tenant_id) on delete cascade;

-- Índices para las consultas del panel
create index if not exists check_ins_tenant_time_idx     on public.check_ins(tenant_id, check_in_time desc);
create index if not exists check_ins_member_time_idx     on public.check_ins(member_id, check_in_time desc);
create index if not exists appointments_tenant_date_idx  on public.appointments(tenant_id, appointment_date, start_time);
create index if not exists appointments_member_date_idx  on public.appointments(member_id, appointment_date);
create index if not exists payments_member_idx           on public.payments(member_id);
