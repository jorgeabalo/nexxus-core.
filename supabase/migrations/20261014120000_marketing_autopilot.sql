-- AITA Marketing: modo de control (Revisar antes de publicar / Piloto automático de AITA) e idempotencia de
-- «Planear contenido». NO publica nada: los conectores siguen desactivados y la aprobación automática exige
-- además, en el backend, conectores activos, presupuesto interno válido y material vigente con consentimiento.
-- * El modo manual es el predeterminado. Activar el piloto exige autorización del owner, o de un manager con el
--   permiso delegado específico de administrar el piloto (distinto del permiso de compras). Pausar puede
--   cualquier owner o manager; al pausar se vuelve al modo manual y lo futuro queda esperando revisión.
-- * Historial inmutable de activación, pausa, reactivación, cambio de reglas y permiso delegado.
-- * Aprobación automática atómica: nunca se atribuye al owner como aprobación personal (actor_role = 'system'),
--   guarda la versión de reglas y la autorización usadas, y falla si el piloto está en pausa, la autorización ya
--   no es válida, cambiaron las reglas o el tenant, se agotó la cuota o el formato/plataforma no está autorizado.
-- * Máximo una publicación automática por día (único por tenant y día local).
-- * POST /content/plan: una clave de idempotencia por tenant y usuario crea un solo contenido aunque lleguen
--   peticiones simultáneas; la misma clave con otro cuerpo se rechaza.
-- Depende de 20261009120000_aita_marketing.sql y 20261013120000_marketing_publication_addons.sql (no los modifica).
-- Solo service_role ejecuta las funciones; owner/manager leen su tenant (RLS); nada se borra.

create table if not exists public.marketing_autopilot_settings (
  tenant_id            uuid primary key references public.tenants(id),
  mode                 text not null default 'manual' check (mode in ('manual', 'autopilot')),
  rules                jsonb check (rules is null or jsonb_typeof(rules) = 'object'),
  rules_version        integer not null default 0 check (rules_version >= 0),
  authorized_by        uuid,
  authorized_at        timestamptz,
  starts_on            date,
  paused_at            timestamptz,
  paused_by            uuid,
  managers_can_manage  boolean not null default false,
  updated_by           uuid not null,
  updated_at           timestamptz not null default now(),
  check (mode <> 'autopilot' or (rules is not null and rules_version > 0 and authorized_by is not null
                                 and authorized_at is not null and starts_on is not null and paused_at is null))
);

create table if not exists public.marketing_autopilot_events (
  id             bigint generated always as identity primary key,
  tenant_id      uuid not null references public.tenants(id),
  event          text not null check (event in ('activated', 'paused', 'reactivated', 'rules_changed',
                                                'delegation_granted', 'delegation_revoked')),
  actor_id       uuid not null,
  actor_role     text not null check (actor_role in ('owner', 'manager')),
  rules          jsonb,
  rules_version  integer not null,
  starts_on      date,
  created_at     timestamptz not null default now()
);
create index if not exists marketing_autopilot_events_tenant_idx on public.marketing_autopilot_events(tenant_id, created_at);

-- Contenido creado por el piloto: su hueco del calendario (uno por día local) y, si se aprobó automáticamente,
-- con qué autorización y versión de reglas.
create table if not exists public.marketing_autopilot_items (
  content_id              uuid primary key,
  tenant_id               uuid not null references public.tenants(id),
  slot_at                 timestamptz not null,
  slot_day                date not null,
  rules_version           integer not null check (rules_version > 0),
  auto_approved_at        timestamptz,
  approved_rules_version  integer,
  authorized_by           uuid,
  authorized_at           timestamptz,
  created_at              timestamptz not null default now(),
  unique (tenant_id, slot_day),
  foreign key (content_id, tenant_id) references public.marketing_content(id, tenant_id),
  check ((auto_approved_at is null) = (approved_rules_version is null and authorized_by is null and authorized_at is null))
);

-- Idempotencia de «Planear contenido» (solo service_role, a través de la función).
create table if not exists public.marketing_plan_requests (
  tenant_id        uuid not null references public.tenants(id),
  user_id          uuid not null,
  idempotency_key  text not null check (idempotency_key ~ '^[A-Za-z0-9_-]{16,120}$'),
  request_hash     text not null check (request_hash ~ '^[0-9a-f]{64}$'),
  content_id       uuid,
  created_at       timestamptz not null default now(),
  primary key (tenant_id, user_id, idempotency_key),
  foreign key (content_id, tenant_id) references public.marketing_content(id, tenant_id)
);

alter table public.marketing_autopilot_settings enable row level security;
alter table public.marketing_autopilot_events enable row level security;
alter table public.marketing_autopilot_items enable row level security;
alter table public.marketing_plan_requests enable row level security;

drop policy if exists marketing_autopilot_settings_read on public.marketing_autopilot_settings;
create policy marketing_autopilot_settings_read on public.marketing_autopilot_settings
  for select to authenticated using (private.has_tenant_role(tenant_id, array['owner','manager']));
drop policy if exists marketing_autopilot_events_read on public.marketing_autopilot_events;
create policy marketing_autopilot_events_read on public.marketing_autopilot_events
  for select to authenticated using (private.has_tenant_role(tenant_id, array['owner','manager']));
drop policy if exists marketing_autopilot_items_read on public.marketing_autopilot_items;
create policy marketing_autopilot_items_read on public.marketing_autopilot_items
  for select to authenticated using (private.has_tenant_role(tenant_id, array['owner','manager']));

-- ---------- reglas compartidas ----------
create or replace function private.marketing_autopilot_lock(p_tenant uuid) returns void
language sql volatile security definer set search_path = '' as $$
  select pg_advisory_xact_lock(hashtextextended('marketing_autopilot:' || p_tenant::text, 0)) $$;

-- Quién puede activar o cambiar las reglas: un owner activo, o un manager activo con el permiso delegado.
create or replace function private.marketing_autopilot_manager(p_tenant uuid, p_user uuid) returns boolean
language sql stable security definer set search_path = '' as $$
  select exists (
    select 1 from public.tenant_users tu
     where tu.tenant_id = p_tenant and tu.user_id = p_user and tu.active
       and (tu.role = 'owner'
            or (tu.role = 'manager' and exists (select 1 from public.marketing_autopilot_settings s
                                                 where s.tenant_id = p_tenant and s.managers_can_manage)))) $$;

-- Al pausar: lo futuro del piloto (aprobado o programado) vuelve a «Listo para revisar»; la cola se cancela.
-- Lo publicado y los borradores no se tocan.
create or replace function private.marketing_autopilot_hold(p_tenant uuid, p_actor uuid) returns integer
language plpgsql security definer set search_path = '' as $$
declare m record; n integer := 0; note text := 'Piloto automático en pausa: queda esperando revisión.';
begin
  for m in select c.id, c.status from public.marketing_content c
             join public.marketing_autopilot_items i on i.content_id = c.id and i.tenant_id = c.tenant_id
            where c.tenant_id = p_tenant and c.status in ('approved', 'scheduled')
              and coalesce(c.scheduled_at, i.slot_at) > now()
            for update of c loop
    if m.status = 'scheduled' then
      update public.marketing_content set status = 'approved', scheduled_at = null, updated_by = p_actor where id = m.id;
      insert into public.marketing_approval_events (tenant_id, content_id, action, from_status, to_status, comment, actor_id, actor_role)
      values (p_tenant, m.id, 'unschedule', 'scheduled', 'approved', note, p_actor, 'system');
      update public.marketing_publications set status = 'cancelled'
       where tenant_id = p_tenant and content_id = m.id and status in ('pending', 'queued');
    end if;
    update public.marketing_content set status = 'draft', approved_by = null, approved_at = null, updated_by = p_actor
     where id = m.id;
    insert into public.marketing_approval_events (tenant_id, content_id, action, from_status, to_status, comment, actor_id, actor_role)
    values (p_tenant, m.id, 'reopen', 'approved', 'draft', note, p_actor, 'system');
    update public.marketing_content set status = 'review', submitted_at = now(), updated_by = p_actor where id = m.id;
    insert into public.marketing_approval_events (tenant_id, content_id, action, from_status, to_status, comment, actor_id, actor_role)
    values (p_tenant, m.id, 'submit', 'draft', 'review', note, p_actor, 'system');
    n := n + 1;
  end loop;
  return n;
end $$;

-- ---------- historial e inmutabilidad ----------
create or replace function private.marketing_autopilot_immutable()
returns trigger language plpgsql security definer set search_path = '' as $$
begin
  raise exception 'marketing_autopilot_history_immutable' using errcode = '42501';
end $$;
drop trigger if exists marketing_autopilot_events_guard on public.marketing_autopilot_events;
create trigger marketing_autopilot_events_guard before update or delete on public.marketing_autopilot_events
  for each row execute function private.marketing_autopilot_immutable();
drop trigger if exists marketing_plan_requests_guard on public.marketing_plan_requests;
create trigger marketing_plan_requests_guard before delete on public.marketing_plan_requests
  for each row execute function private.marketing_autopilot_immutable();
drop trigger if exists marketing_autopilot_items_guard on public.marketing_autopilot_items;
create trigger marketing_autopilot_items_guard before delete on public.marketing_autopilot_items
  for each row execute function private.marketing_autopilot_immutable();

-- ---------- operaciones atómicas (solo service_role) ----------
-- p_action: activate (activa, reactiva o cambia reglas) · pause · grant · revoke (permiso delegado).
create or replace function public.marketing_autopilot_set(p_tenant uuid, p_actor uuid, p_action text, p_rules jsonb,
  p_starts_on date)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare s public.marketing_autopilot_settings; r text; ev text; ver integer; held integer := 0;
begin
  perform private.marketing_autopilot_lock(p_tenant);
  select tu.role into r from public.tenant_users tu
   where tu.tenant_id = p_tenant and tu.user_id = p_actor and tu.active and tu.role in ('owner', 'manager') limit 1;
  if r is null then
    return jsonb_build_object('error', 'forbidden');
  end if;
  insert into public.marketing_autopilot_settings (tenant_id, updated_by) values (p_tenant, p_actor)
  on conflict (tenant_id) do nothing;
  select * into s from public.marketing_autopilot_settings where tenant_id = p_tenant for update;
  if p_action in ('grant', 'revoke') then
    if r <> 'owner' then
      return jsonb_build_object('error', 'owner_only');
    end if;
    if s.managers_can_manage = (p_action = 'grant') then
      return jsonb_build_object('result', 'unchanged', 'settings', to_jsonb(s));
    end if;
    update public.marketing_autopilot_settings set managers_can_manage = (p_action = 'grant'), updated_by = p_actor,
           updated_at = now() where tenant_id = p_tenant returning * into s;
    ev := case p_action when 'grant' then 'delegation_granted' else 'delegation_revoked' end;
  elsif p_action = 'pause' then
    if s.mode = 'manual' then
      return jsonb_build_object('result', 'unchanged', 'settings', to_jsonb(s));
    end if;
    update public.marketing_autopilot_settings set mode = 'manual', paused_at = now(), paused_by = p_actor,
           updated_by = p_actor, updated_at = now() where tenant_id = p_tenant returning * into s;
    held := private.marketing_autopilot_hold(p_tenant, p_actor);
    ev := 'paused';
  elsif p_action = 'activate' then
    if not private.marketing_autopilot_manager(p_tenant, p_actor) then
      return jsonb_build_object('error', 'autopilot_authorization_required');
    end if;
    if p_rules is null or jsonb_typeof(p_rules) <> 'object' or p_starts_on is null then
      return jsonb_build_object('error', 'invalid_autopilot_rules');
    end if;
    if s.mode = 'autopilot' and s.rules = p_rules and s.starts_on = p_starts_on then
      return jsonb_build_object('result', 'unchanged', 'settings', to_jsonb(s));
    end if;
    ev := case when s.mode = 'autopilot' then 'rules_changed' when s.rules_version > 0 then 'reactivated' else 'activated' end;
    ver := case when s.rules is distinct from p_rules or s.starts_on is distinct from p_starts_on
                then s.rules_version + 1 else s.rules_version end;
    update public.marketing_autopilot_settings
       set mode = 'autopilot', rules = p_rules, rules_version = ver, authorized_by = p_actor, authorized_at = now(),
           starts_on = p_starts_on, paused_at = null, paused_by = null, updated_by = p_actor, updated_at = now()
     where tenant_id = p_tenant returning * into s;
  else
    return jsonb_build_object('error', 'invalid_action');
  end if;
  insert into public.marketing_autopilot_events (tenant_id, event, actor_id, actor_role, rules, rules_version, starts_on)
  values (p_tenant, ev, p_actor, r, s.rules, s.rules_version, s.starts_on);
  return jsonb_build_object('result', ev, 'settings', to_jsonb(s), 'held', held);
end $$;

-- Reserva el hueco del día para un contenido del piloto (idempotente; uno por día).
create or replace function public.marketing_autopilot_claim(p_tenant uuid, p_content uuid, p_slot_at timestamptz,
  p_slot_day date, p_version integer)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare s public.marketing_autopilot_settings; i public.marketing_autopilot_items;
begin
  perform private.marketing_autopilot_lock(p_tenant);
  select * into s from public.marketing_autopilot_settings where tenant_id = p_tenant;
  if not found or s.mode <> 'autopilot' or s.rules_version <> p_version then
    return jsonb_build_object('error', 'autopilot_paused');
  end if;
  select * into i from public.marketing_autopilot_items where tenant_id = p_tenant and slot_day = p_slot_day;
  if found then
    return case when i.content_id = p_content then jsonb_build_object('result', 'existing', 'item', to_jsonb(i))
                else jsonb_build_object('error', 'slot_taken') end;
  end if;
  if not exists (select 1 from public.marketing_content c where c.id = p_content and c.tenant_id = p_tenant) then
    return jsonb_build_object('error', 'not_found');
  end if;
  insert into public.marketing_autopilot_items (content_id, tenant_id, slot_at, slot_day, rules_version)
  values (p_content, p_tenant, p_slot_at, p_slot_day, p_version) returning * into i;
  return jsonb_build_object('result', 'claimed', 'item', to_jsonb(i));
end $$;

-- Aprobación automática (review → approved) bajo la autorización vigente. Nunca como aprobación personal.
create or replace function public.marketing_autopilot_approve(p_tenant uuid, p_content uuid, p_version integer)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare s public.marketing_autopilot_settings; i public.marketing_autopilot_items; c public.marketing_content;
        cyc date; lim integer; used integer; tz text;
begin
  perform private.marketing_autopilot_lock(p_tenant);
  perform private.marketing_quota_lock(p_tenant);
  select * into s from public.marketing_autopilot_settings where tenant_id = p_tenant;
  if not found or s.mode <> 'autopilot' or s.paused_at is not null then
    return jsonb_build_object('error', 'autopilot_paused');
  end if;
  if not private.marketing_autopilot_manager(p_tenant, s.authorized_by) then
    return jsonb_build_object('error', 'authorization_withdrawn');
  end if;
  if s.rules_version <> p_version then
    return jsonb_build_object('error', 'rules_changed');
  end if;
  select * into i from public.marketing_autopilot_items where content_id = p_content;
  if not found or i.tenant_id <> p_tenant then
    return jsonb_build_object('error', 'tenant_mismatch');
  end if;
  select * into c from public.marketing_content where id = p_content and tenant_id = p_tenant for update;
  if not found then
    return jsonb_build_object('error', 'tenant_mismatch');
  end if;
  if c.status <> 'review' then
    return jsonb_build_object('error', 'invalid_transition');
  end if;
  if not (c.format = any (array(select jsonb_array_elements_text(coalesce(s.rules->'formats', '[]'::jsonb))))) then
    return jsonb_build_object('error', 'format_not_authorized');
  end if;
  if cardinality(c.channels) = 0
     or not (c.channels <@ array(select jsonb_array_elements_text(coalesce(s.rules->'platforms', '[]'::jsonb)))) then
    return jsonb_build_object('error', 'platform_not_authorized');
  end if;
  cyc := private.marketing_cycle(p_tenant, i.slot_at);
  lim := private.marketing_post_limit(p_tenant, cyc);
  if lim is not null then
    select coalesce(t.timezone, 'UTC') into tz from public.tenants t where t.id = p_tenant;
    select count(*) into used from public.marketing_content m
     where m.tenant_id = p_tenant and m.id <> p_content
       and m.scheduled_at >= (cyc::timestamp at time zone tz)
       and m.scheduled_at < ((cyc + interval '1 month')::timestamp at time zone tz)
       and m.status in ('scheduled', 'publishing', 'published');
    select used + count(*) into used from public.marketing_autopilot_items x
      join public.marketing_content m on m.id = x.content_id and m.tenant_id = x.tenant_id
     where x.tenant_id = p_tenant and x.content_id <> p_content and m.status = 'approved'
       and private.marketing_cycle(p_tenant, x.slot_at) = cyc;
    if used + 1 > lim then
      return jsonb_build_object('error', 'quota_exhausted');
    end if;
  end if;
  update public.marketing_content set status = 'approved', approved_by = null, approved_at = now(),
         updated_by = s.authorized_by where id = p_content;
  insert into public.marketing_approval_events (tenant_id, content_id, action, from_status, to_status, comment, actor_id, actor_role)
  values (p_tenant, p_content, 'approve', 'review', 'approved',
          'Aprobado automáticamente bajo la autorización del owner.', s.authorized_by, 'system');
  update public.marketing_autopilot_items set auto_approved_at = now(), approved_rules_version = s.rules_version,
         authorized_by = s.authorized_by, authorized_at = s.authorized_at where content_id = p_content returning * into i;
  return jsonb_build_object('result', 'approved', 'item', to_jsonb(i));
end $$;

-- Crea el contenido de «Planear contenido» una sola vez por clave (tenant + usuario), también con concurrencia.
create or replace function public.marketing_plan_content(p_tenant uuid, p_user uuid, p_role text, p_key text,
  p_hash text, p_content jsonb)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare r public.marketing_plan_requests; new_id uuid := gen_random_uuid();
begin
  if not exists (select 1 from public.tenant_users tu where tu.tenant_id = p_tenant and tu.user_id = p_user
                  and tu.active and tu.role in ('owner', 'manager')) then
    return jsonb_build_object('error', 'forbidden');
  end if;
  insert into public.marketing_plan_requests (tenant_id, user_id, idempotency_key, request_hash)
  values (p_tenant, p_user, p_key, p_hash)
  on conflict (tenant_id, user_id, idempotency_key) do nothing returning * into r;
  if not found then                                -- la otra petición ya terminó (el bloqueo del índice espera)
    select * into r from public.marketing_plan_requests
     where tenant_id = p_tenant and user_id = p_user and idempotency_key = p_key;
    if r.request_hash <> p_hash then
      return jsonb_build_object('error', 'idempotency_conflict');
    end if;
    return jsonb_build_object('result', 'existing', 'content_id', r.content_id);
  end if;
  insert into public.marketing_content (id, tenant_id, title, objective, topic, format, channels, language, cta,
    planned_at, notes, status, assigned_to, created_by, updated_by)
  values (new_id, p_tenant, p_content->>'title', p_content->>'objective', p_content->>'topic', p_content->>'format',
          coalesce(array(select jsonb_array_elements_text(p_content->'channels')), '{}'),
          coalesce(p_content->>'language', 'es'), p_content->>'cta', (p_content->>'planned_at')::timestamptz,
          p_content->>'notes', 'draft', coalesce((p_content->>'assigned_to')::uuid, p_user), p_user, p_user);
  insert into public.marketing_approval_events (tenant_id, content_id, action, from_status, to_status, actor_id, actor_role)
  values (p_tenant, new_id, 'create', null, 'draft', p_user, case when p_role in ('owner', 'manager') then p_role else 'system' end);
  update public.marketing_plan_requests set content_id = new_id
   where tenant_id = p_tenant and user_id = p_user and idempotency_key = p_key;
  return jsonb_build_object('result', 'created', 'content_id', new_id);
end $$;

-- ---------- permisos mínimos ----------
revoke all privileges on table public.marketing_autopilot_settings, public.marketing_autopilot_events,
  public.marketing_autopilot_items, public.marketing_plan_requests from public, anon, authenticated, service_role;
grant select on table public.marketing_autopilot_settings, public.marketing_autopilot_events,
  public.marketing_autopilot_items to authenticated;
grant select on table public.marketing_autopilot_settings, public.marketing_autopilot_events,
  public.marketing_autopilot_items, public.marketing_plan_requests to service_role;

revoke all on function private.marketing_autopilot_lock(uuid), private.marketing_autopilot_manager(uuid, uuid),
  private.marketing_autopilot_hold(uuid, uuid), private.marketing_autopilot_immutable()
  from public, anon, authenticated, service_role;
revoke all on function public.marketing_autopilot_set(uuid, uuid, text, jsonb, date),
  public.marketing_autopilot_claim(uuid, uuid, timestamptz, date, integer),
  public.marketing_autopilot_approve(uuid, uuid, integer),
  public.marketing_plan_content(uuid, uuid, text, text, text, jsonb) from public, anon, authenticated;
grant execute on function public.marketing_autopilot_set(uuid, uuid, text, jsonb, date),
  public.marketing_autopilot_claim(uuid, uuid, timestamptz, date, integer),
  public.marketing_autopilot_approve(uuid, uuid, integer),
  public.marketing_plan_content(uuid, uuid, text, text, text, jsonb) to service_role;
