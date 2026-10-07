-- =====================================================================
-- Contabilidad básica (Manager Panel → Accounting / Contabilidad)
--
-- * accounting_categories   — categorías de ingreso / gasto por tenant.
-- * accounting_transactions — movimientos (ingresos y gastos). Nunca se borran:
--                             "cancelar" = status 'cancelled'.
-- * accounting_obligations  — cuentas por cobrar / por pagar.
-- * Bucket PRIVADO accounting-receipts (fotos/PDF de recibos): sin políticas para
--   anon/authenticated; solo el backend (service role) lee y escribe.
--
-- Pagos de socios: NO se copian. La tabla payments sigue siendo la fuente; el
-- backend la consolida en el resumen. Si algún día un pago se registra también
-- aquí, source_type='payment' + source_id = payments.id, y el índice único
-- impide que exista dos veces.
--
-- Permisos (RLS, mismo patrón que el resto del panel):
--   owner / manager → ver, crear y editar todo.
--   staff           → ver y registrar movimientos (no edita, no cancela,
--                     no gestiona categorías ni pendientes).
--   socio (member)  → nada (no pertenece a tenant_users).
--
-- Idempotente: se puede ejecutar más de una vez. No borra ni renombra nada.
-- =====================================================================

-- ---------- updated_at genérico (misma definición que 20260927120100) ----------
create or replace function public.touch_updated_at()
returns trigger language plpgsql set search_path = '' as $$
begin new.updated_at := now(); return new; end $$;

-- ---------- categorías ----------
create table if not exists public.accounting_categories (
  id         uuid primary key default gen_random_uuid(),
  tenant_id  uuid not null references public.tenants(id) on delete restrict,
  name       text not null check (length(btrim(name)) between 1 and 80),
  type       text not null check (type in ('income','expense')),
  active     boolean not null default true,
  created_at timestamptz not null default now(),
  unique (id, tenant_id)
);
create unique index if not exists accounting_categories_name_key
  on public.accounting_categories(tenant_id, type, lower(btrim(name)));
create index if not exists accounting_categories_tenant_idx
  on public.accounting_categories(tenant_id, type, active);

-- ---------- movimientos ----------
create table if not exists public.accounting_transactions (
  id               uuid primary key default gen_random_uuid(),
  tenant_id        uuid not null references public.tenants(id) on delete restrict,
  transaction_date date not null,
  type             text not null check (type in ('income','expense')),
  category_id      uuid,
  description      text not null check (length(btrim(description)) between 1 and 300),
  amount           numeric(12,2) not null check (amount > 0 and amount < 100000000),
  payment_method   text not null default 'other' check (payment_method in ('cash','card','bank','check','other')),
  status           text not null default 'paid' check (status in ('pending','paid','cancelled')),
  receipt_url      text,      -- ruta dentro del bucket privado (nunca una URL pública)
  member_id        uuid,
  source_type      text check (source_type is null or source_type ~ '^[a-z_]{1,40}$'),
  source_id        uuid,
  notes            text check (notes is null or length(notes) <= 2000),
  created_by       uuid not null,
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now(),
  unique (id, tenant_id),
  check ((source_type is null) = (source_id is null)),
  constraint accounting_transactions_category_fk foreign key (category_id, tenant_id)
    references public.accounting_categories(id, tenant_id) on delete restrict,
  constraint accounting_transactions_member_fk foreign key (member_id, tenant_id)
    references public.members(id, tenant_id) on delete set null (member_id)
);
create index if not exists accounting_transactions_tenant_date_idx
  on public.accounting_transactions(tenant_id, transaction_date desc);
create index if not exists accounting_transactions_tenant_type_idx
  on public.accounting_transactions(tenant_id, type, status, transaction_date);
create index if not exists accounting_transactions_category_idx
  on public.accounting_transactions(category_id);
-- Un mismo origen (p. ej. un pago de socio) solo puede contarse una vez.
create unique index if not exists accounting_transactions_source_key
  on public.accounting_transactions(tenant_id, source_type, source_id)
  where source_id is not null and status <> 'cancelled';

-- La categoría debe ser del mismo tipo que el movimiento.
create or replace function public.accounting_check_category()
returns trigger language plpgsql set search_path = '' as $$
declare ctype text;
begin
  if new.category_id is not null then
    select c.type into ctype from public.accounting_categories c
     where c.id = new.category_id and c.tenant_id = new.tenant_id;
    if ctype is distinct from new.type then
      raise exception 'category type does not match transaction type' using errcode = '23514';
    end if;
  end if;
  return new;
end $$;
drop trigger if exists accounting_transactions_category_chk on public.accounting_transactions;
create trigger accounting_transactions_category_chk before insert or update on public.accounting_transactions
  for each row execute function public.accounting_check_category();
drop trigger if exists accounting_transactions_touch on public.accounting_transactions;
create trigger accounting_transactions_touch before update on public.accounting_transactions
  for each row execute function public.touch_updated_at();

-- ---------- cuentas por cobrar / por pagar ----------
create table if not exists public.accounting_obligations (
  id                     uuid primary key default gen_random_uuid(),
  tenant_id              uuid not null references public.tenants(id) on delete restrict,
  obligation_type        text not null check (obligation_type in ('receivable','payable')),
  counterparty           text not null check (length(btrim(counterparty)) between 1 and 120),
  description            text check (description is null or length(description) <= 300),
  amount                 numeric(12,2) not null check (amount > 0 and amount < 100000000),
  due_date               date not null,
  status                 text not null default 'pending' check (status in ('pending','paid','overdue','cancelled')),
  related_transaction_id uuid,
  paid_at                timestamptz,
  created_by             uuid not null,
  created_at             timestamptz not null default now(),
  updated_at             timestamptz not null default now(),
  constraint accounting_obligations_txn_fk foreign key (related_transaction_id, tenant_id)
    references public.accounting_transactions(id, tenant_id) on delete restrict
);
create index if not exists accounting_obligations_tenant_due_idx
  on public.accounting_obligations(tenant_id, status, due_date);
create index if not exists accounting_obligations_tenant_type_idx
  on public.accounting_obligations(tenant_id, obligation_type, status);
drop trigger if exists accounting_obligations_touch on public.accounting_obligations;
create trigger accounting_obligations_touch before update on public.accounting_obligations
  for each row execute function public.touch_updated_at();

-- ---------- RLS ----------
alter table public.accounting_categories   enable row level security;
alter table public.accounting_transactions enable row level security;
alter table public.accounting_obligations  enable row level security;

do $$
declare t text;
begin
  foreach t in array array['accounting_categories','accounting_transactions','accounting_obligations'] loop
    execute format('drop policy if exists %I on public.%I', t || '_select', t);
    execute format('drop policy if exists %I on public.%I', t || '_insert', t);
    execute format('drop policy if exists %I on public.%I', t || '_update', t);
    -- ver: cualquier persona del equipo del tenant (owner/manager/staff); nunca un socio
    execute format($p$create policy %I on public.%I for select to authenticated
                     using (private.has_tenant_role(tenant_id, array['owner','manager','staff']))$p$, t || '_select', t);
    -- editar: owner/manager
    execute format($p$create policy %I on public.%I for update to authenticated
                     using (private.has_tenant_role(tenant_id, array['owner','manager']))
                     with check (private.has_tenant_role(tenant_id, array['owner','manager']))$p$, t || '_update', t);
    -- sin política de DELETE: nada se borra físicamente
    execute format('revoke all on public.%I from anon', t);
    execute format('revoke delete on public.%I from authenticated', t);
  end loop;
end $$;

create policy accounting_categories_insert on public.accounting_categories for insert to authenticated
  with check (private.has_tenant_role(tenant_id, array['owner','manager']));
create policy accounting_obligations_insert on public.accounting_obligations for insert to authenticated
  with check (private.has_tenant_role(tenant_id, array['owner','manager']) and created_by = (select auth.uid()));
-- staff también registra movimientos (a su nombre); no los edita ni cancela
create policy accounting_transactions_insert on public.accounting_transactions for insert to authenticated
  with check (private.has_tenant_role(tenant_id, array['owner','manager','staff']) and created_by = (select auth.uid()));

grant select, insert, update on public.accounting_categories, public.accounting_transactions,
  public.accounting_obligations to authenticated;

-- ---------- bucket privado de recibos ----------
do $$ begin
  if exists (select 1 from pg_namespace where nspname = 'storage') then
    insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
    values ('accounting-receipts', 'accounting-receipts', false, 10485760,
            array['application/pdf','image/jpeg','image/png','image/webp','image/heic'])
    on conflict (id) do update set public = false, file_size_limit = excluded.file_size_limit,
                                   allowed_mime_types = excluded.allowed_mime_types;
  end if;
end $$;

-- ---------- categorías iniciales (por tenant; sin movimientos ficticios) ----------
create or replace function private.seed_accounting_categories(p_tenant uuid)
returns void language sql security definer set search_path = '' as $$
  insert into public.accounting_categories (tenant_id, name, type)
  select p_tenant, c.name, c.type
  from (values
    ('Membresías','income'), ('Sesiones de entrenamiento','income'), ('Masajes','income'),
    ('Servicios de recuperación','income'), ('Otros ingresos','income'),
    ('Alquiler','expense'), ('Nómina','expense'), ('Servicios públicos','expense'),
    ('Equipos','expense'), ('Mantenimiento','expense'), ('Marketing','expense'),
    ('Suministros','expense'), ('Seguros','expense'), ('Otros gastos','expense')
  ) as c(name, type)
  on conflict do nothing
$$;
revoke all on function private.seed_accounting_categories(uuid) from public, anon, authenticated;

create or replace function public.on_tenant_created_accounting()
returns trigger language plpgsql security definer set search_path = '' as $$
begin
  perform private.seed_accounting_categories(new.id);
  return new;
end $$;
revoke all on function public.on_tenant_created_accounting() from public, anon, authenticated;
drop trigger if exists tenants_seed_accounting on public.tenants;
create trigger tenants_seed_accounting after insert on public.tenants
  for each row execute function public.on_tenant_created_accounting();

select private.seed_accounting_categories(id) from public.tenants;

-- ---------- activar el módulo en el panel (el owner puede apagarlo) ----------
update public.tenants
   set modules = jsonb_set(coalesce(modules, '{}'::jsonb), '{accounting}', 'true'::jsonb)
 where coalesce(modules->>'accounting', 'false') <> 'true';
