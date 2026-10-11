-- AITA Marketing: ampliaciones mensuales de publicaciones (recurrentes hasta cancelación).
-- No cobra ni llama a Stripe: registra la solicitud y la autorización para incorporarlas a la factura mensual.
-- * El manager puede SOLICITAR; autoriza el owner (o un manager si el owner delegó el permiso de compras).
-- * El importe lo calcula la base (columnas generadas): la combinación exacta más económica de +8, +4 y +1.
-- * Filas inmutables salvo transiciones de un solo sentido: requested → authorized | declined;
--   authorized → cancelled (con efecto desde un mes posterior). Nunca se borran.
-- Solo service_role escribe; owner/manager leen su tenant (RLS). No modifica tablas existentes.

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
    (additional_publications / 8) * 3500
    + ((additional_publications % 8) / 4) * 2000
    + (additional_publications % 4) * 600
  ) stored,
  currency                 text not null default 'USD' check (currency = 'USD'),
  starts_cycle             date not null check (starts_cycle = date_trunc('month', starts_cycle)::date),
  ends_cycle               date check (ends_cycle is null or ends_cycle = date_trunc('month', ends_cycle)::date),
  status                   text not null default 'requested'
                             check (status in ('requested', 'authorized', 'declined', 'cancelled')),
  requested_by             uuid not null,
  requested_at             timestamptz not null default now(),
  decided_by               uuid,
  decided_at               timestamptz,
  cancelled_by             uuid,
  cancelled_at             timestamptz,
  idempotency_key          text not null check (idempotency_key ~ '^[A-Za-z0-9_-]{16,120}$'),
  created_at               timestamptz not null default now(),
  unique (tenant_id, idempotency_key),
  check ((cadence = 'weekly') = (per_week is not null)),
  check (cadence <> 'weekly' or additional_publications = per_week * 4),
  check ((status = 'requested') = (decided_by is null and decided_at is null)),
  check ((status = 'cancelled') = (cancelled_by is not null and cancelled_at is not null and ends_cycle is not null)),
  check (status = 'cancelled' or (cancelled_by is null and cancelled_at is null and ends_cycle is null)),
  check (ends_cycle is null or ends_cycle > starts_cycle)
);

create index if not exists marketing_publication_addons_tenant_idx
  on public.marketing_publication_addons(tenant_id, status, starts_cycle);

-- Permiso de compras delegado por el owner: si está activo, los managers también pueden autorizar.
create table if not exists public.marketing_purchase_permissions (
  tenant_id               uuid primary key references public.tenants(id),
  managers_can_authorize  boolean not null default false,
  updated_by              uuid not null,
  updated_at              timestamptz not null default now()
);

alter table public.marketing_publication_addons enable row level security;
alter table public.marketing_purchase_permissions enable row level security;

drop policy if exists marketing_publication_addons_read on public.marketing_publication_addons;
create policy marketing_publication_addons_read on public.marketing_publication_addons
  for select to authenticated
  using (private.has_tenant_role(tenant_id, array['owner','manager']));

drop policy if exists marketing_purchase_permissions_read on public.marketing_purchase_permissions;
create policy marketing_purchase_permissions_read on public.marketing_purchase_permissions
  for select to authenticated
  using (private.has_tenant_role(tenant_id, array['owner','manager']));

-- ¿Puede este usuario autorizar cargos de este tenant? Owner activo, o manager activo con permiso delegado.
create or replace function private.marketing_addon_approver(p_tenant uuid, p_user uuid)
returns boolean language sql stable security definer set search_path = '' as $$
  select exists (
    select 1 from public.tenant_users tu
     where tu.tenant_id = p_tenant and tu.user_id = p_user and tu.active
       and (tu.role = 'owner'
            or (tu.role = 'manager' and exists (
                  select 1 from public.marketing_purchase_permissions p
                   where p.tenant_id = p_tenant and p.managers_can_authorize))))
$$;

create or replace function private.marketing_publication_addons_guard()
returns trigger language plpgsql security definer set search_path = '' as $$
declare
  -- Las columnas generadas aún no están calculadas en un disparador BEFORE: se excluyen (dependen de
  -- additional_publications, que sí se compara).
  mutable text[] := array['status', 'decided_by', 'decided_at', 'cancelled_by', 'cancelled_at', 'ends_cycle',
                          'blocks_8', 'blocks_4', 'blocks_1', 'amount_cents'];
begin
  if tg_op = 'DELETE' then
    raise exception 'marketing_publication_addons_immutable' using errcode = '42501';
  end if;
  if tg_op = 'INSERT' then
    if not exists (select 1 from public.tenant_users tu where tu.tenant_id = new.tenant_id
                    and tu.user_id = new.requested_by and tu.active and tu.role in ('owner', 'manager')) then
      raise exception 'marketing_addon_requester_not_allowed' using errcode = '42501';
    end if;
    if new.status not in ('requested', 'authorized') then
      raise exception 'marketing_addon_invalid_status' using errcode = '23514';
    end if;
  else
    if (to_jsonb(new) - mutable) is distinct from (to_jsonb(old) - mutable)
       or not ((old.status = 'requested' and new.status in ('authorized', 'declined'))
               or (old.status = 'authorized' and new.status = 'cancelled')) then
      raise exception 'marketing_publication_addons_immutable' using errcode = '42501';
    end if;
    if new.status = 'cancelled' and (new.decided_by is distinct from old.decided_by
                                     or new.decided_at is distinct from old.decided_at) then
      raise exception 'marketing_publication_addons_immutable' using errcode = '42501';
    end if;
  end if;
  if new.status in ('authorized', 'declined') and (tg_op = 'INSERT' or old.status = 'requested')
     and not private.marketing_addon_approver(new.tenant_id, new.decided_by) then
    raise exception 'marketing_addon_owner_authorization_required' using errcode = '42501';
  end if;
  if new.status = 'cancelled' and not private.marketing_addon_approver(new.tenant_id, new.cancelled_by) then
    raise exception 'marketing_addon_owner_authorization_required' using errcode = '42501';
  end if;
  if new.status = 'cancelled' and new.ends_cycle < date_trunc('month', now())::date then
    raise exception 'marketing_addon_no_retroactive_cancel' using errcode = '23514';   -- lo ya usado se factura
  end if;
  return new;
end;
$$;

drop trigger if exists marketing_publication_addons_guard on public.marketing_publication_addons;
create trigger marketing_publication_addons_guard
  before insert or update or delete on public.marketing_publication_addons
  for each row execute function private.marketing_publication_addons_guard();

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
end;
$$;

drop trigger if exists marketing_purchase_permissions_guard on public.marketing_purchase_permissions;
create trigger marketing_purchase_permissions_guard
  before insert or update or delete on public.marketing_purchase_permissions
  for each row execute function private.marketing_purchase_permissions_guard();

revoke all privileges on table public.marketing_publication_addons, public.marketing_purchase_permissions
  from public, anon, authenticated, service_role;
grant select on table public.marketing_publication_addons, public.marketing_purchase_permissions to authenticated;
grant select, insert on table public.marketing_publication_addons, public.marketing_purchase_permissions to service_role;
grant update (status, decided_by, decided_at, cancelled_by, cancelled_at, ends_cycle)
  on table public.marketing_publication_addons to service_role;
grant update (managers_can_authorize, updated_by, updated_at)
  on table public.marketing_purchase_permissions to service_role;

revoke all on function private.marketing_addon_approver(uuid, uuid),
  private.marketing_publication_addons_guard(), private.marketing_purchase_permissions_guard()
  from public, anon, authenticated, service_role;
