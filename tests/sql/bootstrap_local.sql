-- Emulación mínima de Supabase para probar migraciones localmente.
create role anon nologin; create role authenticated nologin; create role service_role nologin bypassrls;
create schema auth;
create table auth.users (id uuid primary key default gen_random_uuid(), email text);
create function auth.uid() returns uuid language sql stable as $$
  select nullif(current_setting('request.jwt.claims', true)::json->>'sub','')::uuid $$;
grant usage on schema auth to anon, authenticated;
grant execute on function auth.uid() to anon, authenticated;
grant usage on schema public to anon, authenticated, service_role;

create table public.tenants (id uuid default gen_random_uuid() not null primary key, slug text not null unique, name text not null, vertical text default 'generic'::text not null, timezone text default 'America/Chicago'::text not null, twilio_phone text, branding jsonb default '{}'::jsonb not null, modules jsonb default '{}'::jsonb not null, settings jsonb default '{}'::jsonb not null, active boolean default true not null, created_at timestamp with time zone default now() not null, updated_at timestamp with time zone default now() not null);
create table public.staff (id uuid default gen_random_uuid() not null primary key, first_name text not null, last_name text, role text, email text, phone text, active boolean default true, created_at timestamp with time zone default now(), tenant_id uuid not null references public.tenants(id));
create table public.tenant_users (id uuid default gen_random_uuid() not null primary key, tenant_id uuid not null, user_id uuid not null, role text not null, staff_id uuid, active boolean default true not null, created_at timestamp with time zone default now() not null, unique(tenant_id,user_id));
create table public.members (id uuid default gen_random_uuid() not null primary key, member_id text, first_name text not null, last_name text, email text, phone text, date_of_birth date, gender text, address text, city text, state text, zip_code text, emergency_contact_name text, emergency_contact_phone text, membership_type text, membership_status text default 'active'::text, start_date date default CURRENT_DATE, next_payment_date date, qr_token uuid default gen_random_uuid(), onboarding_completed boolean default false, notes text, created_at timestamp with time zone default now(), updated_at timestamp with time zone default now(), tenant_id uuid not null references public.tenants(id), user_id uuid unique references auth.users(id) on delete set null, portal_token_version integer default 1 not null, portal_invited_at timestamp with time zone, portal_activated_at timestamp with time zone, portal_last_used_at timestamp with time zone, unique (id, tenant_id));
create table public.measurements (id uuid default gen_random_uuid() not null primary key, member_id uuid not null, measurement_date date default CURRENT_DATE, weight_lb numeric, body_fat_percent numeric, waist_in numeric, chest_in numeric, left_arm_in numeric, right_arm_in numeric, left_leg_in numeric, right_leg_in numeric, notes text, created_at timestamp with time zone default now(), tenant_id uuid not null, constraint measurements_member_fk foreign key (member_id, tenant_id) references public.members(id, tenant_id) on delete cascade);
create table public.check_ins (id uuid default gen_random_uuid() not null primary key, member_id uuid not null references public.members(id) on delete cascade, check_in_time timestamp with time zone default now(), check_out_time timestamp with time zone, method text default 'qr'::text, notes text, tenant_id uuid not null);
create table public.appointments (id uuid default gen_random_uuid() not null primary key, member_id uuid, staff_id uuid, service text, appointment_date date not null, start_time time without time zone not null, end_time time without time zone, status text default 'scheduled'::text, notes text, created_at timestamp with time zone default now(), tenant_id uuid not null, source text default 'manager'::text not null, client_name text, client_phone text, call_id uuid, lead_id uuid, confirmed_at timestamp with time zone, cancelled_at timestamp with time zone, cancel_reason text, rescheduled_from uuid, created_by uuid, updated_at timestamp with time zone default now() not null);
create table public.payments (id uuid default gen_random_uuid() not null primary key, member_id uuid, amount numeric not null, payment_date timestamp with time zone, payment_method text, payment_status text default 'pending'::text, description text, transaction_reference text, created_at timestamp with time zone default now(), tenant_id uuid not null, due_date date, provider text, provider_ref text, updated_at timestamp with time zone default now() not null, appointment_id uuid, lead_id uuid, call_id uuid);
create table public.services (id uuid default gen_random_uuid() not null primary key, name text not null, description text, price numeric, duration_minutes integer, active boolean default true, created_at timestamp with time zone default now(), tenant_id uuid not null);
create table public.alerts (id uuid default gen_random_uuid() not null primary key, tenant_id uuid not null, type text not null check (type = any (array['follow_up','call_manager','payment_overdue','member_inactive','appointment_confirmation','lead','other'])), severity text default 'warning'::text not null check (severity = any (array['info','warning','critical'])), title text not null, detail text, member_id uuid, call_id uuid, lead_id uuid, appointment_id uuid, payment_id uuid, status text default 'open'::text not null, due_at timestamp with time zone, resolved_at timestamp with time zone, resolved_by uuid, created_at timestamp with time zone default now() not null);

create schema private;
grant usage on schema private to authenticated;
create function private.user_tenant_ids() returns setof uuid language sql stable security definer set search_path = '' as $$
  select tu.tenant_id from public.tenant_users tu where tu.user_id = (select auth.uid()) and tu.active $$;
create function private.has_tenant_role(p_tenant uuid, p_roles text[]) returns boolean language sql stable security definer set search_path = '' as $$
  select exists (select 1 from public.tenant_users tu where tu.user_id = (select auth.uid()) and tu.active and tu.tenant_id = p_tenant and tu.role = any (p_roles)) $$;
create function private.my_member_ids() returns setof uuid language sql stable security definer set search_path = '' as $$
  select m.id from public.members m where m.user_id = (select auth.uid()) $$;
create function private.my_member_tenant_ids() returns setof uuid language sql stable security definer set search_path = '' as $$
  select m.tenant_id from public.members m where m.user_id = (select auth.uid()) $$;
grant execute on all functions in schema private to authenticated;

-- RLS existente (igual que en producción)
do $$ declare t text; begin
  foreach t in array array['members','measurements','check_ins','appointments','payments','services','alerts','staff'] loop
    execute format('alter table public.%I enable row level security', t);
    execute format('create policy %I on public.%I for select to authenticated using (tenant_id in (select private.user_tenant_ids()))', t||'_select', t);
    execute format('create policy %I on public.%I for insert to authenticated with check (tenant_id in (select private.user_tenant_ids()))', t||'_insert', t);
    execute format('create policy %I on public.%I for update to authenticated using (tenant_id in (select private.user_tenant_ids())) with check (tenant_id in (select private.user_tenant_ids()))', t||'_update', t);
    execute format('create policy %I on public.%I for delete to authenticated using (private.has_tenant_role(tenant_id, array[''owner'',''manager'']))', t||'_delete', t);
  end loop; end $$;
create policy members_self_select on public.members for select to authenticated using (id in (select private.my_member_ids()));
create policy measurements_self_select on public.measurements for select to authenticated using (member_id in (select private.my_member_ids()));
alter table public.tenants enable row level security;
create policy tenants_select on public.tenants for select to authenticated using (id in (select private.user_tenant_ids()));
grant select, insert, update, delete on all tables in schema public to authenticated;
alter default privileges in schema public grant select, insert, update, delete on tables to authenticated;
