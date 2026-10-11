-- AITA Marketing: ampliaciones mensuales autorizadas por el tenant.
-- No cobra ni llama a Stripe. Registra una autorización inmutable para la factura mensual.

create table if not exists public.marketing_publication_addons (
  id                       uuid primary key default gen_random_uuid(),
  tenant_id                uuid not null references public.tenants(id),
  cycle_start              date not null check (cycle_start = date_trunc('month', cycle_start)::date),
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
  requested_by             uuid not null,
  authorized_by            uuid not null,
  idempotency_key          text not null check (idempotency_key ~ '^[A-Za-z0-9_-]{16,120}$'),
  authorized_at            timestamptz not null default now(),
  created_at               timestamptz not null default now(),
  unique (tenant_id, idempotency_key)
);

create index if not exists marketing_publication_addons_tenant_cycle_idx
  on public.marketing_publication_addons(tenant_id, cycle_start);

alter table public.marketing_publication_addons enable row level security;

drop policy if exists marketing_publication_addons_read on public.marketing_publication_addons;
create policy marketing_publication_addons_read on public.marketing_publication_addons
  for select to authenticated
  using (private.has_tenant_role(tenant_id, array['owner','manager']));

create or replace function private.marketing_publication_addons_immutable()
returns trigger language plpgsql
set search_path = ''
as $$
begin
  raise exception 'marketing_publication_addons_immutable' using errcode = '42501';
end;
$$;

drop trigger if exists marketing_publication_addons_immutable on public.marketing_publication_addons;
create trigger marketing_publication_addons_immutable
  before update or delete on public.marketing_publication_addons
  for each row execute function private.marketing_publication_addons_immutable();

revoke all privileges on table public.marketing_publication_addons from public, anon, authenticated, service_role;
grant select on table public.marketing_publication_addons to authenticated;
grant select, insert on table public.marketing_publication_addons to service_role;

revoke all on function private.marketing_publication_addons_immutable() from public, anon, authenticated, service_role;
