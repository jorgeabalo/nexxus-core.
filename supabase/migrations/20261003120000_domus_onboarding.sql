-- Additive Domus foundation. No changes to business tables or Alexa V1 routing.
begin;
create schema if not exists private;

create table public.domus_homes (
  id uuid primary key default gen_random_uuid(),
  name text not null check (length(btrim(name)) between 1 and 80),
  timezone text not null default 'America/Chicago',
  locale text not null default 'es-US' check (locale in ('es-US','es-MX','es-ES','en-US')),
  setup_stage text not null default 'rooms' check (setup_stage in ('rooms','assistants','devices','review')),
  onboarding_completed_at timestamptz,
  created_by uuid not null references auth.users(id) on delete restrict,
  request_key uuid not null,
  created_at timestamptz not null default now(),
  unique (created_by, request_key)
);
create table public.domus_home_users (
  home_id uuid not null references public.domus_homes(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  role text not null check (role in ('owner','member','guest')),
  active boolean not null default true,
  created_at timestamptz not null default now(),
  primary key (home_id,user_id)
);
create index domus_users_by_user on public.domus_home_users(user_id,home_id) where active;
create table public.domus_rooms (
  id uuid primary key default gen_random_uuid(),
  home_id uuid not null references public.domus_homes(id) on delete cascade,
  name text not null check (length(btrim(name)) between 1 and 60),
  created_at timestamptz not null default now(),
  unique (id,home_id)
);
create unique index domus_room_name on public.domus_rooms(home_id,lower(btrim(name)));
create table public.domus_channels (
  id uuid primary key default gen_random_uuid(),
  home_id uuid not null references public.domus_homes(id) on delete cascade,
  platform text not null check (platform in ('alexa','google','app','other')),
  connection_status text not null default 'pending' check (connection_status in ('pending','connected','attention')),
  created_at timestamptz not null default now(),
  unique (home_id,platform)
);
create table public.domus_devices (
  id uuid primary key default gen_random_uuid(),
  home_id uuid not null references public.domus_homes(id) on delete cascade,
  room_id uuid,
  name text not null check (length(btrim(name)) between 1 and 80),
  kind text not null check (kind in ('tv','music','plug','thermostat','vacuum','security','other')),
  brand text not null default '' check (length(brand) <= 60),
  connection_status text not null default 'pending' check (connection_status in ('pending','connected','attention')),
  created_at timestamptz not null default now(),
  foreign key (room_id,home_id) references public.domus_rooms(id,home_id) on delete restrict
);
create index domus_devices_home on public.domus_devices(home_id);
-- Append-only personal consent. No credentials, tokens or household transcripts.
create table public.domus_consents (
  id uuid primary key default gen_random_uuid(),
  home_id uuid not null references public.domus_homes(id) on delete cascade,
  user_id uuid not null default auth.uid() references auth.users(id) on delete cascade,
  scope text not null check (scope = 'anthropic_voice_queries_v1'),
  granted boolean not null,
  recorded_at timestamptz not null default now()
);
create index domus_consents_latest on public.domus_consents(home_id,user_id,recorded_at desc);

create function private.domus_has_role(p_home uuid, p_roles text[])
returns boolean language sql stable security definer set search_path = '' as $$
  select exists (select 1 from public.domus_home_users
    where home_id=p_home and user_id=(select auth.uid()) and active and role=any(p_roles))
$$;
revoke all on function private.domus_has_role(uuid,text[]) from public,anon;
grant usage on schema private to authenticated;
grant execute on function private.domus_has_role(uuid,text[]) to authenticated;

-- Only this operation bootstraps an owner; users cannot insert memberships.
create function public.domus_create_home(p_name text, p_timezone text, p_locale text, p_request_key uuid)
returns uuid language plpgsql security definer set search_path = '' as $$
declare u uuid := auth.uid(); h uuid;
begin
  if u is null then raise exception 'authentication_required' using errcode='42501'; end if;
  if p_timezone is null or not exists (select 1 from pg_catalog.pg_timezone_names where name=p_timezone)
    then raise exception 'invalid_timezone' using errcode='22023'; end if;
  perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtext(u::text)::bigint);
  select id into h from public.domus_homes where created_by=u and request_key=p_request_key;
  if h is not null then return h; end if;
  if (select count(*) from public.domus_homes where created_by=u) >= 20
    then raise exception 'home_limit_reached' using errcode='22023'; end if;
  insert into public.domus_homes(name,timezone,locale,created_by,request_key)
    values (btrim(p_name),p_timezone,p_locale,u,p_request_key) returning id into h;
  insert into public.domus_home_users(home_id,user_id,role) values(h,u,'owner');
  return h;
end $$;

create function public.domus_complete_onboarding(p_home uuid)
returns void language plpgsql security definer set search_path = '' as $$
begin
  if not private.domus_has_role(p_home,array['owner'])
    then raise exception 'not_authorized' using errcode='42501'; end if;
  if not exists(select 1 from public.domus_rooms where home_id=p_home)
    or not exists(select 1 from public.domus_channels where home_id=p_home)
    then raise exception 'setup_incomplete' using errcode='22023'; end if;
  update public.domus_homes set setup_stage='review',
    onboarding_completed_at=coalesce(onboarding_completed_at,now()) where id=p_home;
end $$;
revoke all on function public.domus_create_home(text,text,text,uuid) from public,anon;
revoke all on function public.domus_complete_onboarding(uuid) from public,anon;
grant execute on function public.domus_create_home(text,text,text,uuid) to authenticated;
grant execute on function public.domus_complete_onboarding(uuid) to authenticated;

do $$ declare t text; begin
  foreach t in array array['domus_homes','domus_home_users','domus_rooms','domus_channels','domus_devices','domus_consents'] loop
    execute format('alter table public.%I enable row level security',t);
    -- Explicit revoke also counters pre-existing default table privileges.
    execute format('revoke all on public.%I from public,anon,authenticated',t);
    execute format('grant select on public.%I to authenticated',t);
    execute format('grant all on public.%I to service_role',t);
  end loop;
end $$;
create policy domus_homes_read on public.domus_homes for select to authenticated
  using (private.domus_has_role(id,array['owner','member','guest']));
create policy domus_homes_edit on public.domus_homes for update to authenticated
  using (private.domus_has_role(id,array['owner'])) with check (private.domus_has_role(id,array['owner']));
grant update(name,locale,setup_stage) on public.domus_homes to authenticated;
create policy domus_users_read on public.domus_home_users for select to authenticated
  using (user_id=(select auth.uid()) or private.domus_has_role(home_id,array['owner']));
create policy domus_consents_read on public.domus_consents for select to authenticated
  using (user_id=(select auth.uid()) and private.domus_has_role(home_id,array['owner','member','guest']));
create policy domus_consents_add on public.domus_consents for insert to authenticated
  with check (user_id=(select auth.uid()) and private.domus_has_role(home_id,array['owner','member','guest']));
grant insert(home_id,scope,granted) on public.domus_consents to authenticated;
do $$ declare t text; begin
  foreach t in array array['domus_rooms','domus_channels','domus_devices'] loop
    execute format('create policy %I on public.%I for select to authenticated using (private.domus_has_role(home_id,array[''owner'',''member'',''guest'']))',t||'_read',t);
    execute format('create policy %I on public.%I for insert to authenticated with check (private.domus_has_role(home_id,array[''owner'']))',t||'_add',t);
    execute format('create policy %I on public.%I for update to authenticated using (private.domus_has_role(home_id,array[''owner''])) with check (private.domus_has_role(home_id,array[''owner'']))',t||'_edit',t);
  end loop;
end $$;
grant insert(home_id,name),update(name) on public.domus_rooms to authenticated;
grant insert(home_id,platform) on public.domus_channels to authenticated;
grant insert(home_id,room_id,name,kind,brand),update(room_id,name,kind,brand) on public.domus_devices to authenticated;
-- connection_status and memberships require trusted backend workflows.
commit;
