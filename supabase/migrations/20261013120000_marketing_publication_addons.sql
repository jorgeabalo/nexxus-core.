-- AITA Marketing: ampliaciones mensuales de publicaciones (recurrentes hasta cancelación) y límite efectivo.
-- NO cobra ni llama a Stripe. La autorización del owner crea un registro comercial inequívoco
-- (status = 'authorized_uninvoiced': autorizado, sin factura ni pago) para que AITA lo añada como línea
-- recurrente a la factura mensual del tenant. Autorización NO significa pago capturado.
-- * Una sola ampliación vigente por tenant y ciclo: un cambio termina la vigente al cierre del ciclo y la
--   nueva empieza el ciclo siguiente (nunca se acumulan). Sin ampliación vigente, empieza en el ciclo actual.
-- * Límite efectivo del ciclo = monthly_post_limit + ampliación vigente de ese ciclo. La base lo exige al
--   programar (disparador en marketing_content) con un bloqueo por tenant: programaciones concurrentes no
--   lo superan. Ciclo = mes calendario en la zona horaria del tenant (tenants.timezone).
-- * El manager solicita; autoriza, rechaza o cancela el owner (o un manager con permiso de compras delegado).
-- Solo service_role ejecuta las funciones; owner/manager leen su tenant (RLS); nada se borra.

create table if not exists public.marketing_publication_addons (
  id                       uuid primary key default gen_random_uuid(),
  tenant_id                uuid not null references public.tenants(id),
  cadence                  text not null default 'monthly' check (cadence in ('monthly', 'weekly')),
  per_week                 integer check (per_week between 1 and 125),
  additional_publications  integer not null check (additional_publications between 1 and 500),
  blocks_8                 integer generated always as (additional_publications / 8) stored,
  blocks_4                 integer generated always as ((additional_publications % 8) / 4) stored,
  blocks_1                 integer generated always as (additional_publications % 4) stored,
  amount_cents             integer generated always as (
    (additional_publications / 8) * 3500 + ((additional_publications % 8) / 4) * 2000 + (additional_publications % 4) * 600
  ) stored,
  currency                 text not null default 'USD' check (currency = 'USD'),
  billing_line             text generated always as (
    'Publicaciones adicionales de Marketing: ' || additional_publications || ' por mes — $'
    || ((additional_publications / 8) * 35 + ((additional_publications % 8) / 4) * 20 + (additional_publications % 4) * 6)
  ) stored,
  status                   text not null default 'requested'
                             check (status in ('requested', 'declined', 'authorized_uninvoiced', 'cancelled', 'replaced')),
  starts_cycle             date check (starts_cycle = date_trunc('month', starts_cycle)::date),
  ends_cycle               date check (ends_cycle = date_trunc('month', ends_cycle)::date),
  requested_by             uuid not null,
  requested_at             timestamptz not null default now(),
  authorized_by            uuid,
  authorized_at            timestamptz,
  declined_by              uuid,
  declined_at              timestamptz,
  cancelled_by             uuid,
  cancelled_at             timestamptz,
  replaced_by              uuid references public.marketing_publication_addons(id) deferrable initially deferred,
  idempotency_key          text not null check (idempotency_key ~ '^[A-Za-z0-9_-]{16,120}$'),
  created_at               timestamptz not null default now(),
  unique (tenant_id, idempotency_key),
  check ((cadence = 'weekly') = (per_week is not null)),
  check (cadence <> 'weekly' or additional_publications = per_week * 4),
  check ((status in ('authorized_uninvoiced', 'cancelled', 'replaced'))
         = (authorized_by is not null and authorized_at is not null and starts_cycle is not null)),
  check ((status = 'declined') = (declined_by is not null and declined_at is not null)),
  check ((status = 'cancelled') = (cancelled_by is not null and cancelled_at is not null)),
  check ((status = 'replaced') = (replaced_by is not null)),
  check ((status in ('cancelled', 'replaced')) = (ends_cycle is not null)),
  check (ends_cycle is null or ends_cycle >= starts_cycle)
);
create index if not exists marketing_publication_addons_tenant_idx
  on public.marketing_publication_addons(tenant_id, status, starts_cycle);

-- Historial inmutable y outbox para la futura facturación (solo service_role; el tenant no lo ve).
create table if not exists public.marketing_publication_addon_events (
  id                       bigint generated always as identity primary key,
  addon_id                 uuid not null references public.marketing_publication_addons(id),
  tenant_id                uuid not null references public.tenants(id),
  event                    text not null check (event in ('requested', 'declined', 'authorized', 'cancelled', 'replaced')),
  actor_id                 uuid,
  status                   text not null,
  additional_publications  integer not null,
  amount_cents             integer not null,
  currency                 text not null,
  billing_line             text not null,
  starts_cycle             date,
  ends_cycle               date,
  created_at               timestamptz not null default now()
);
create index if not exists marketing_publication_addon_events_tenant_idx
  on public.marketing_publication_addon_events(tenant_id, created_at);

-- Permiso de compras delegado por el owner: si está activo, los managers también pueden autorizar.
create table if not exists public.marketing_purchase_permissions (
  tenant_id               uuid primary key references public.tenants(id),
  managers_can_authorize  boolean not null default false,
  updated_by              uuid not null,
  updated_at              timestamptz not null default now()
);

alter table public.marketing_publication_addons enable row level security;
alter table public.marketing_publication_addon_events enable row level security;
alter table public.marketing_purchase_permissions enable row level security;

drop policy if exists marketing_publication_addons_read on public.marketing_publication_addons;
create policy marketing_publication_addons_read on public.marketing_publication_addons
  for select to authenticated using (private.has_tenant_role(tenant_id, array['owner','manager']));
drop policy if exists marketing_purchase_permissions_read on public.marketing_purchase_permissions;
create policy marketing_purchase_permissions_read on public.marketing_purchase_permissions
  for select to authenticated using (private.has_tenant_role(tenant_id, array['owner','manager']));

-- ---------- reglas compartidas ----------
create or replace function private.marketing_quota_lock(p_tenant uuid) returns void
language sql volatile security definer set search_path = '' as $$
  select pg_advisory_xact_lock(hashtextextended('marketing_quota:' || p_tenant::text, 0)) $$;

create or replace function private.marketing_cycle(p_tenant uuid, p_at timestamptz) returns date
language sql stable security definer set search_path = '' as $$
  select date_trunc('month', p_at at time zone
           coalesce((select t.timezone from public.tenants t where t.id = p_tenant), 'UTC'))::date $$;

create or replace function private.marketing_addon_approver(p_tenant uuid, p_user uuid) returns boolean
language sql stable security definer set search_path = '' as $$
  select exists (
    select 1 from public.tenant_users tu
     where tu.tenant_id = p_tenant and tu.user_id = p_user and tu.active
       and (tu.role = 'owner'
            or (tu.role = 'manager' and exists (select 1 from public.marketing_purchase_permissions p
                                                 where p.tenant_id = p_tenant and p.managers_can_authorize)))) $$;

-- Ampliación vigente en un ciclo (0 si ninguna) y límite efectivo (null = sin límite).
create or replace function private.marketing_addon_for_cycle(p_tenant uuid, p_cycle date) returns integer
language sql stable security definer set search_path = '' as $$
  select coalesce(sum(a.additional_publications), 0)::integer from public.marketing_publication_addons a
   where a.tenant_id = p_tenant and a.status in ('authorized_uninvoiced', 'cancelled', 'replaced')
     and a.starts_cycle <= p_cycle and (a.ends_cycle is null or a.ends_cycle > p_cycle) $$;

create or replace function private.marketing_post_limit(p_tenant uuid, p_cycle date) returns integer
language sql stable security definer set search_path = '' as $$
  select case when s.monthly_post_limit is null then null
              else s.monthly_post_limit + private.marketing_addon_for_cycle(p_tenant, p_cycle) end
    from public.marketing_settings s where s.tenant_id = p_tenant $$;

-- ---------- guardián: transiciones de un solo sentido, una vigente por ciclo, nada se borra ----------
create or replace function private.marketing_publication_addons_guard()
returns trigger language plpgsql security definer set search_path = '' as $$
declare
  -- Las columnas generadas aún no están calculadas en un disparador BEFORE: se excluyen de la comparación.
  mutable text[] := array['status', 'starts_cycle', 'ends_cycle', 'authorized_by', 'authorized_at', 'declined_by',
                          'declined_at', 'cancelled_by', 'cancelled_at', 'replaced_by',
                          'blocks_8', 'blocks_4', 'blocks_1', 'amount_cents', 'billing_line'];
  cur date;
begin
  if tg_op = 'DELETE' then
    raise exception 'marketing_publication_addons_immutable' using errcode = '42501';
  end if;
  perform private.marketing_quota_lock(new.tenant_id);
  cur := private.marketing_cycle(new.tenant_id, now());
  if tg_op = 'INSERT' then
    if not exists (select 1 from public.tenant_users tu where tu.tenant_id = new.tenant_id
                    and tu.user_id = new.requested_by and tu.active and tu.role in ('owner', 'manager')) then
      raise exception 'marketing_addon_requester_not_allowed' using errcode = '42501';
    end if;
    if new.status not in ('requested', 'authorized_uninvoiced') then
      raise exception 'marketing_addon_invalid_status' using errcode = '23514';
    end if;
  else
    if (to_jsonb(new) - mutable) is distinct from (to_jsonb(old) - mutable)
       or not ((old.status = 'requested' and new.status in ('authorized_uninvoiced', 'declined'))
               or (old.status = 'authorized_uninvoiced' and new.status in ('cancelled', 'replaced'))) then
      raise exception 'marketing_publication_addons_immutable' using errcode = '42501';
    end if;
    if old.status = 'authorized_uninvoiced' and (new.authorized_by is distinct from old.authorized_by
        or new.authorized_at is distinct from old.authorized_at or new.starts_cycle is distinct from old.starts_cycle) then
      raise exception 'marketing_publication_addons_immutable' using errcode = '42501';
    end if;
  end if;
  if new.status = 'authorized_uninvoiced' and (tg_op = 'INSERT' or old.status = 'requested') then
    if not private.marketing_addon_approver(new.tenant_id, new.authorized_by) then
      raise exception 'marketing_addon_owner_authorization_required' using errcode = '42501';
    end if;
    if new.starts_cycle < cur then
      raise exception 'marketing_addon_no_backdating' using errcode = '23514';
    end if;
  end if;
  if new.status = 'declined' and not private.marketing_addon_approver(new.tenant_id, new.declined_by) then
    raise exception 'marketing_addon_owner_authorization_required' using errcode = '42501';
  end if;
  if new.status = 'cancelled' and not private.marketing_addon_approver(new.tenant_id, new.cancelled_by) then
    raise exception 'marketing_addon_owner_authorization_required' using errcode = '42501';
  end if;
  -- Ni cancelar ni reemplazar quitan lo del ciclo en curso: el fin nunca es anterior al ciclo siguiente.
  if new.status in ('cancelled', 'replaced') and new.ends_cycle < greatest(new.starts_cycle, (cur + interval '1 month')::date) then
    raise exception 'marketing_addon_no_retroactive_end' using errcode = '23514';
  end if;
  if new.status in ('authorized_uninvoiced', 'cancelled', 'replaced') and exists (
       select 1 from public.marketing_publication_addons o
        where o.tenant_id = new.tenant_id and o.id <> new.id
          and o.status in ('authorized_uninvoiced', 'cancelled', 'replaced')
          and daterange(o.starts_cycle, o.ends_cycle) && daterange(new.starts_cycle, new.ends_cycle)) then
    raise exception 'marketing_addon_overlap' using errcode = '23P01';                  -- una vigente por ciclo
  end if;
  return new;
end $$;
drop trigger if exists marketing_publication_addons_guard on public.marketing_publication_addons;
create trigger marketing_publication_addons_guard before insert or update or delete on public.marketing_publication_addons
  for each row execute function private.marketing_publication_addons_guard();

-- Historial: cada alta o transición queda registrada (también es el outbox de la facturación futura).
create or replace function private.marketing_publication_addons_log()
returns trigger language plpgsql security definer set search_path = '' as $$
begin
  insert into public.marketing_publication_addon_events (addon_id, tenant_id, event, actor_id, status,
    additional_publications, amount_cents, currency, billing_line, starts_cycle, ends_cycle)
  values (new.id, new.tenant_id,
          case new.status when 'authorized_uninvoiced' then 'authorized' else new.status end,
          case new.status when 'requested' then new.requested_by when 'authorized_uninvoiced' then new.authorized_by
                          when 'declined' then new.declined_by when 'cancelled' then new.cancelled_by else null end,
          new.status, new.additional_publications, new.amount_cents, new.currency, new.billing_line,
          new.starts_cycle, new.ends_cycle);
  return null;
end $$;
drop trigger if exists marketing_publication_addons_log on public.marketing_publication_addons;
create trigger marketing_publication_addons_log after insert or update on public.marketing_publication_addons
  for each row execute function private.marketing_publication_addons_log();

create or replace function private.marketing_publication_addon_events_guard()
returns trigger language plpgsql security definer set search_path = '' as $$
begin
  raise exception 'marketing_publication_addon_events_immutable' using errcode = '42501';
end $$;
drop trigger if exists marketing_publication_addon_events_guard on public.marketing_publication_addon_events;
create trigger marketing_publication_addon_events_guard before update or delete on public.marketing_publication_addon_events
  for each row execute function private.marketing_publication_addon_events_guard();

-- Solo un owner activo del tenant cambia el permiso delegado. Nunca se borra (se desactiva).
create or replace function private.marketing_purchase_permissions_guard()
returns trigger language plpgsql security definer set search_path = '' as $$
begin
  if tg_op = 'DELETE' or (tg_op = 'UPDATE' and new.tenant_id is distinct from old.tenant_id) then
    raise exception 'marketing_purchase_permissions_immutable' using errcode = '42501';
  end if;
  if not exists (select 1 from public.tenant_users tu where tu.tenant_id = new.tenant_id
                  and tu.user_id = new.updated_by and tu.active and tu.role = 'owner') then
    raise exception 'marketing_purchase_permission_owner_only' using errcode = '42501';
  end if;
  return new;
end $$;
drop trigger if exists marketing_purchase_permissions_guard on public.marketing_purchase_permissions;
create trigger marketing_purchase_permissions_guard before insert or update or delete on public.marketing_purchase_permissions
  for each row execute function private.marketing_purchase_permissions_guard();

-- ---------- límite efectivo al programar (la base lo exige, también con concurrencia) ----------
create or replace function private.marketing_content_post_limit()
returns trigger language plpgsql security definer set search_path = '' as $$
declare cyc date; lim integer; tz text; used integer;
begin
  if new.status not in ('scheduled', 'publishing', 'published') or new.scheduled_at is null then
    return new;
  end if;
  if tg_op = 'UPDATE' and old.status in ('scheduled', 'publishing', 'published')
     and old.scheduled_at is not distinct from new.scheduled_at and old.tenant_id = new.tenant_id then
    return new;                                          -- ya contaba en ese ciclo
  end if;
  perform private.marketing_quota_lock(new.tenant_id);
  cyc := private.marketing_cycle(new.tenant_id, new.scheduled_at);
  lim := private.marketing_post_limit(new.tenant_id, cyc);
  if lim is null then
    return new;
  end if;
  select coalesce(t.timezone, 'UTC') into tz from public.tenants t where t.id = new.tenant_id;
  select count(*) into used from public.marketing_content m
   where m.tenant_id = new.tenant_id and m.id <> new.id and m.status in ('scheduled', 'publishing', 'published')
     and m.scheduled_at >= (cyc::timestamp at time zone tz)
     and m.scheduled_at < ((cyc + interval '1 month')::timestamp at time zone tz);
  if used + 1 > lim then
    raise exception 'limit_monthly_post_limit' using errcode = '23514';
  end if;
  return new;
end $$;
drop trigger if exists marketing_content_post_limit on public.marketing_content;
create trigger marketing_content_post_limit before insert or update on public.marketing_content
  for each row execute function private.marketing_content_post_limit();

-- ---------- operaciones atómicas (solo service_role) ----------
-- Aplica una autorización: sin ampliación que cubra el ciclo actual, empieza ahora; si la hay, empieza el
-- ciclo siguiente y la vigente termina al cierre del actual (las futuras se reemplazan sin llegar a empezar).
create or replace function private.marketing_apply_addon(p_tenant uuid, p_new uuid) returns date
language plpgsql security definer set search_path = '' as $$
declare cur date := private.marketing_cycle(p_tenant, now()); start date; o record;
begin
  start := case when exists (select 1 from public.marketing_publication_addons a where a.tenant_id = p_tenant
                               and a.id <> p_new and a.status in ('authorized_uninvoiced', 'cancelled', 'replaced')
                               and a.starts_cycle <= cur and (a.ends_cycle is null or a.ends_cycle > cur))
                then (cur + interval '1 month')::date else cur end;
  for o in select a.id, a.starts_cycle from public.marketing_publication_addons a
            where a.tenant_id = p_tenant and a.id <> p_new and a.status = 'authorized_uninvoiced' loop
    update public.marketing_publication_addons
       set status = 'replaced', replaced_by = p_new, ends_cycle = greatest(o.starts_cycle, start)
     where id = o.id;
  end loop;
  return start;
end $$;

create or replace function public.marketing_submit_publication_addon(p_tenant uuid, p_actor uuid, p_cadence text,
  p_per_week integer, p_additional integer, p_authorize boolean, p_key text)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare a public.marketing_publication_addons; new_id uuid := gen_random_uuid(); start date;
        cur date := private.marketing_cycle(p_tenant, now());
begin
  perform private.marketing_quota_lock(p_tenant);
  select * into a from public.marketing_publication_addons where tenant_id = p_tenant and idempotency_key = p_key;
  if found then
    if a.additional_publications <> p_additional or a.cadence <> coalesce(p_cadence, 'monthly')
       or a.per_week is distinct from p_per_week then
      return jsonb_build_object('error', 'idempotency_conflict');
    end if;
    return jsonb_build_object('result', 'existing', 'addon', to_jsonb(a));
  end if;
  if not exists (select 1 from public.tenant_users tu where tu.tenant_id = p_tenant and tu.user_id = p_actor
                  and tu.active and tu.role in ('owner', 'manager')) then
    return jsonb_build_object('error', 'forbidden');
  end if;
  if p_authorize and not private.marketing_addon_approver(p_tenant, p_actor) then
    return jsonb_build_object('error', 'owner_authorization_required');
  end if;
  if not p_authorize then
    insert into public.marketing_publication_addons (id, tenant_id, cadence, per_week, additional_publications,
      requested_by, idempotency_key)
    values (new_id, p_tenant, coalesce(p_cadence, 'monthly'), p_per_week, p_additional, p_actor, p_key) returning * into a;
    return jsonb_build_object('result', 'requested', 'addon', to_jsonb(a));
  end if;
  -- La misma ampliación ya vigente y sin cambio pendiente: nada nuevo (ni otra suscripción ni otro cargo).
  select * into a from public.marketing_publication_addons x
   where x.tenant_id = p_tenant and x.status = 'authorized_uninvoiced' and x.starts_cycle <= cur
     and x.additional_publications = p_additional and x.cadence = coalesce(p_cadence, 'monthly')
     and x.per_week is not distinct from p_per_week
     and not exists (select 1 from public.marketing_publication_addons f where f.tenant_id = p_tenant
                      and f.status = 'authorized_uninvoiced' and f.starts_cycle > cur);
  if found then
    return jsonb_build_object('result', 'unchanged', 'addon', to_jsonb(a));
  end if;
  start := private.marketing_apply_addon(p_tenant, new_id);
  insert into public.marketing_publication_addons (id, tenant_id, cadence, per_week, additional_publications, status,
    starts_cycle, requested_by, authorized_by, authorized_at, idempotency_key)
  values (new_id, p_tenant, coalesce(p_cadence, 'monthly'), p_per_week, p_additional, 'authorized_uninvoiced', start,
          p_actor, p_actor, now(), p_key) returning * into a;
  return jsonb_build_object('result', 'authorized', 'addon', to_jsonb(a));
end $$;

create or replace function public.marketing_decide_publication_addon(p_tenant uuid, p_addon uuid, p_actor uuid,
  p_approve boolean)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare a public.marketing_publication_addons; start date;
begin
  perform private.marketing_quota_lock(p_tenant);
  select * into a from public.marketing_publication_addons where id = p_addon and tenant_id = p_tenant for update;
  if not found then
    return jsonb_build_object('error', 'not_found');
  end if;
  if not private.marketing_addon_approver(p_tenant, p_actor) then
    return jsonb_build_object('error', 'owner_authorization_required');
  end if;
  if a.status = (case when p_approve then 'authorized_uninvoiced' else 'declined' end) then
    return jsonb_build_object('result', 'existing', 'addon', to_jsonb(a));        -- repetición: mismo resultado
  end if;
  if a.status <> 'requested' then
    return jsonb_build_object('error', 'addon_not_pending');
  end if;
  if not p_approve then
    update public.marketing_publication_addons set status = 'declined', declined_by = p_actor, declined_at = now()
     where id = p_addon returning * into a;
    return jsonb_build_object('result', 'declined', 'addon', to_jsonb(a));
  end if;
  start := private.marketing_apply_addon(p_tenant, p_addon);
  update public.marketing_publication_addons
     set status = 'authorized_uninvoiced', authorized_by = p_actor, authorized_at = now(), starts_cycle = start
   where id = p_addon returning * into a;
  return jsonb_build_object('result', 'authorized', 'addon', to_jsonb(a));
end $$;

create or replace function public.marketing_cancel_publication_addon(p_tenant uuid, p_addon uuid, p_actor uuid)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare a public.marketing_publication_addons; nxt date;
begin
  perform private.marketing_quota_lock(p_tenant);
  select * into a from public.marketing_publication_addons where id = p_addon and tenant_id = p_tenant for update;
  if not found then
    return jsonb_build_object('error', 'not_found');
  end if;
  if not private.marketing_addon_approver(p_tenant, p_actor) then
    return jsonb_build_object('error', 'owner_authorization_required');
  end if;
  if a.status = 'cancelled' then
    return jsonb_build_object('result', 'existing', 'addon', to_jsonb(a));
  end if;
  if a.status <> 'authorized_uninvoiced' then
    return jsonb_build_object('error', 'addon_not_active');
  end if;
  nxt := (private.marketing_cycle(p_tenant, now()) + interval '1 month')::date;
  update public.marketing_publication_addons
     set status = 'cancelled', cancelled_by = p_actor, cancelled_at = now(), ends_cycle = greatest(a.starts_cycle, nxt)
   where id = p_addon returning * into a;
  return jsonb_build_object('result', 'cancelled', 'addon', to_jsonb(a));
end $$;

-- ---------- permisos mínimos ----------
revoke all privileges on table public.marketing_publication_addons, public.marketing_publication_addon_events,
  public.marketing_purchase_permissions from public, anon, authenticated, service_role;
grant select on table public.marketing_publication_addons, public.marketing_purchase_permissions to authenticated;
grant select on table public.marketing_publication_addons, public.marketing_publication_addon_events to service_role;
grant select, insert on table public.marketing_purchase_permissions to service_role;
grant update (managers_can_authorize, updated_by, updated_at) on table public.marketing_purchase_permissions to service_role;

revoke all on function private.marketing_quota_lock(uuid), private.marketing_cycle(uuid, timestamptz),
  private.marketing_addon_approver(uuid, uuid), private.marketing_addon_for_cycle(uuid, date),
  private.marketing_post_limit(uuid, date), private.marketing_publication_addons_guard(),
  private.marketing_publication_addons_log(), private.marketing_publication_addon_events_guard(),
  private.marketing_purchase_permissions_guard(), private.marketing_content_post_limit(),
  private.marketing_apply_addon(uuid, uuid) from public, anon, authenticated, service_role;
revoke all on function public.marketing_submit_publication_addon(uuid, uuid, text, integer, integer, boolean, text),
  public.marketing_decide_publication_addon(uuid, uuid, uuid, boolean),
  public.marketing_cancel_publication_addon(uuid, uuid, uuid) from public, anon, authenticated;
grant execute on function public.marketing_submit_publication_addon(uuid, uuid, text, integer, integer, boolean, text),
  public.marketing_decide_publication_addon(uuid, uuid, uuid, boolean),
  public.marketing_cancel_publication_addon(uuid, uuid, uuid) to service_role;
