-- =====================================================================
-- AITA — control global de costos internos por tenant (objetivo: < 80 USD/mes por tenant).
-- Migración NUEVA. No se ejecuta en producción desde este PR. No activa nada.
--
-- * aita_cost_budgets — presupuesto mensual por tenant en centavos. Lo fija SOLO el operador
--   (service role); el total nunca puede superar 8000 (80 USD) y los subpresupuestos no pueden
--   sumar más que el total. Por defecto: total 8000 y subpresupuestos en 0 (cerrado).
--   NO incluye el presupuesto publicitario del cliente (Google Ads, Meta Ads…), que va aparte.
-- * aita_cost_ledger — registro inmutable de cada operación facturable: se reserva antes de
--   ejecutar y se concilia UNA sola vez (costo real; la diferencia queda liberada).
-- * aita_cost_reserve / aita_cost_reconcile / aita_cost_summary — funciones atómicas para el
--   backend (solo service_role). La reserva bloquea la fila del presupuesto del tenant
--   (SELECT … FOR UPDATE): dos operaciones simultáneas nunca reservan el mismo saldo.
-- Idempotente.
-- =====================================================================

create table if not exists public.aita_cost_budgets (
  tenant_id                               uuid primary key references public.tenants(id) on delete cascade,
  monthly_total_cost_limit_cents          integer not null default 8000 check (monthly_total_cost_limit_cents between 0 and 8000),
  monthly_voice_cost_limit_cents          integer not null default 0 check (monthly_voice_cost_limit_cents >= 0),
  monthly_marketing_ai_cost_limit_cents   integer not null default 0 check (monthly_marketing_ai_cost_limit_cents >= 0),
  monthly_storage_cost_limit_cents        integer not null default 0 check (monthly_storage_cost_limit_cents >= 0),
  monthly_infrastructure_allocation_cents integer not null default 0 check (monthly_infrastructure_allocation_cents >= 0),
  monthly_reserve_cents                   integer not null default 0 check (monthly_reserve_cents >= 0),
  currency                                text not null default 'USD' check (currency = 'USD'),
  updated_at                              timestamptz not null default now(),
  check (monthly_voice_cost_limit_cents + monthly_marketing_ai_cost_limit_cents + monthly_storage_cost_limit_cents
         + monthly_infrastructure_allocation_cents + monthly_reserve_cents <= monthly_total_cost_limit_cents)
);
drop trigger if exists aita_cost_budgets_touch on public.aita_cost_budgets;
create trigger aita_cost_budgets_touch before update on public.aita_cost_budgets
  for each row execute function public.touch_updated_at();

create table if not exists public.aita_cost_ledger (
  id                   uuid primary key default gen_random_uuid(),
  tenant_id            uuid not null references public.tenants(id) on delete restrict,
  period               date not null,                                -- primer día del mes (UTC)
  service_category     text not null check (service_category in ('voice','claudia_ai','marketing_ai','storage',
                                                                     'processing','infrastructure','publishing')),
  provider             text not null check (provider ~ '^[a-z0-9_]{1,40}$'),
  model                text check (model is null or length(model) <= 120),
  operation            text not null check (length(operation) between 1 and 80),
  estimated_cost_cents integer not null check (estimated_cost_cents >= 0),
  reserved_cost_cents  integer not null check (reserved_cost_cents >= 0),
  actual_cost_cents    integer check (actual_cost_cents is null or actual_cost_cents >= 0),
  currency             text not null default 'USD' check (currency = 'USD'),
  idempotency_key      text not null check (length(idempotency_key) between 8 and 200),
  status               text not null check (status in ('reserved','committed','released')),
  created_at           timestamptz not null default now(),
  reconciled_at        timestamptz,
  unique (tenant_id, idempotency_key),                                -- un reintento nunca reserva ni cobra dos veces
  check ((status = 'reserved') = (reconciled_at is null and actual_cost_cents is null))
);
create index if not exists aita_cost_ledger_period_idx on public.aita_cost_ledger(tenant_id, period, service_category);

-- Inmutable: solo una conciliación reserved → committed/released (con costo real y fecha). Sin borrados.
create or replace function private.aita_cost_ledger_guard()
returns trigger language plpgsql set search_path = '' as $$
begin
  if tg_op = 'DELETE' then
    raise exception 'cost ledger is immutable' using errcode = '42501';
  end if;
  if old.status <> 'reserved' or new.status = 'reserved'
     or (new.tenant_id, new.period, new.service_category, new.provider, new.model, new.operation, new.estimated_cost_cents,
         new.reserved_cost_cents, new.currency, new.idempotency_key, new.created_at)
        is distinct from (old.tenant_id, old.period, old.service_category, old.provider, old.model, old.operation,
         old.estimated_cost_cents, old.reserved_cost_cents, old.currency, old.idempotency_key, old.created_at) then
    raise exception 'cost ledger is immutable (single reconciliation only)' using errcode = '42501';
  end if;
  return new;
end $$;
drop trigger if exists aita_cost_ledger_guard on public.aita_cost_ledger;
create trigger aita_cost_ledger_guard before update or delete on public.aita_cost_ledger
  for each row execute function private.aita_cost_ledger_guard();

-- categoría → columna de subpresupuesto
create or replace function private.aita_cost_category_limit(b public.aita_cost_budgets, p_category text)
returns integer language sql immutable set search_path = '' as $$
  select case p_category
    when 'voice' then b.monthly_voice_cost_limit_cents
    when 'claudia_ai' then b.monthly_voice_cost_limit_cents
    when 'marketing_ai' then b.monthly_marketing_ai_cost_limit_cents
    when 'storage' then b.monthly_storage_cost_limit_cents
    else b.monthly_infrastructure_allocation_cents end $$;

create or replace function private.aita_cost_category_group(p_category text)
returns text[] language sql immutable set search_path = '' as $$
  select case when p_category in ('voice','claudia_ai') then array['voice','claudia_ai']
              when p_category in ('marketing_ai','storage') then array[p_category]
              else array['processing','infrastructure','publishing'] end $$;

-- Reserva atómica. Devuelve {status: reserved|duplicate|rejected, reason, ledger_id, available_cents}.
create or replace function public.aita_cost_reserve(p_tenant uuid, p_category text, p_provider text, p_model text,
  p_operation text, p_estimated_cents integer, p_idempotency_key text)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare b public.aita_cost_budgets; existing public.aita_cost_ledger; v_period date := date_trunc('month', now())::date;
        used_total bigint; used_cat bigint; cat_limit integer; v_id uuid;
begin
  if p_estimated_cents is null or p_estimated_cents < 0 then
    return jsonb_build_object('status', 'rejected', 'reason', 'cost_not_estimable');
  end if;
  select * into b from public.aita_cost_budgets where tenant_id = p_tenant for update;   -- serializa por tenant
  if not found then
    return jsonb_build_object('status', 'rejected', 'reason', 'budget_not_configured');
  end if;
  select * into existing from public.aita_cost_ledger where tenant_id = p_tenant and idempotency_key = p_idempotency_key;
  if found then
    return jsonb_build_object('status', 'duplicate', 'ledger_id', existing.id, 'ledger_status', existing.status);
  end if;
  select coalesce(sum(coalesce(actual_cost_cents, reserved_cost_cents)), 0) into used_total
    from public.aita_cost_ledger where tenant_id = p_tenant and period = v_period and status <> 'released';
  select coalesce(sum(coalesce(actual_cost_cents, reserved_cost_cents)), 0) into used_cat
    from public.aita_cost_ledger where tenant_id = p_tenant and period = v_period and status <> 'released'
     and service_category = any (private.aita_cost_category_group(p_category));
  cat_limit := private.aita_cost_category_limit(b, p_category);
  if used_cat + p_estimated_cents > cat_limit then
    return jsonb_build_object('status', 'rejected', 'reason', 'category_budget_exceeded',
                              'available_cents', greatest(cat_limit - used_cat, 0));
  end if;
  if used_total + p_estimated_cents > b.monthly_total_cost_limit_cents - b.monthly_reserve_cents then
    return jsonb_build_object('status', 'rejected', 'reason', 'total_budget_exceeded',
                              'available_cents', greatest(b.monthly_total_cost_limit_cents - b.monthly_reserve_cents - used_total, 0));
  end if;
  insert into public.aita_cost_ledger (tenant_id, period, service_category, provider, model, operation,
    estimated_cost_cents, reserved_cost_cents, idempotency_key, status)
  values (p_tenant, v_period, p_category, p_provider, p_model, p_operation, p_estimated_cents, p_estimated_cents,
          p_idempotency_key, 'reserved') returning id into v_id;
  return jsonb_build_object('status', 'reserved', 'ledger_id', v_id,
                            'available_cents', least(cat_limit - used_cat, b.monthly_total_cost_limit_cents
                                                     - b.monthly_reserve_cents - used_total) - p_estimated_cents);
end $$;

-- Conciliación única: costo real (la diferencia con la reserva queda libre). actual 0 y sin uso → released.
create or replace function public.aita_cost_reconcile(p_tenant uuid, p_idempotency_key text, p_actual_cents integer,
  p_release boolean default false)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare e public.aita_cost_ledger;
begin
  select * into e from public.aita_cost_ledger where tenant_id = p_tenant and idempotency_key = p_idempotency_key for update;
  if not found then return jsonb_build_object('status', 'not_found'); end if;
  if e.status <> 'reserved' then return jsonb_build_object('status', e.status, 'duplicate', true); end if;
  if p_actual_cents is null or p_actual_cents < 0 then
    raise exception 'actual cost required' using errcode = '23514';
  end if;
  update public.aita_cost_ledger set status = case when p_release and p_actual_cents = 0 then 'released' else 'committed' end,
         actual_cost_cents = p_actual_cents, reconciled_at = now() where id = e.id;
  return jsonb_build_object('status', case when p_release and p_actual_cents = 0 then 'released' else 'committed' end);
end $$;

-- Resumen del mes para la interfaz (sin proveedores, modelos ni costos de otros tenants).
create or replace function public.aita_cost_summary(p_tenant uuid)
returns jsonb language sql stable security definer set search_path = '' as $$
  with b as (select * from public.aita_cost_budgets where tenant_id = p_tenant),
       l as (select service_category c, status, coalesce(actual_cost_cents, 0) a, reserved_cost_cents r
               from public.aita_cost_ledger where tenant_id = p_tenant and period = date_trunc('month', now())::date)
  select jsonb_build_object(
    'configured', exists (select 1 from b),
    'total_limit_cents', (select monthly_total_cost_limit_cents - monthly_reserve_cents from b),
    'marketing_ai_limit_cents', (select monthly_marketing_ai_cost_limit_cents from b),
    'consumed_cents', coalesce((select sum(a) from l where status = 'committed'), 0),
    'reserved_cents', coalesce((select sum(r) from l where status = 'reserved'), 0),
    'marketing_ai_consumed_cents', coalesce((select sum(a) from l where status = 'committed' and c = 'marketing_ai'), 0),
    'marketing_ai_reserved_cents', coalesce((select sum(r) from l where status = 'reserved' and c = 'marketing_ai'), 0)) $$;

-- Permisos: nadie salvo el backend toca presupuestos ni ledger, ni ejecuta las funciones.
revoke all on function private.aita_cost_ledger_guard(), private.aita_cost_category_limit(public.aita_cost_budgets, text),
  private.aita_cost_category_group(text) from public, anon, authenticated;
revoke all on function public.aita_cost_reserve(uuid, text, text, text, text, integer, text),
  public.aita_cost_reconcile(uuid, text, integer, boolean), public.aita_cost_summary(uuid)
  from public, anon, authenticated;
grant execute on function public.aita_cost_reserve(uuid, text, text, text, text, integer, text),
  public.aita_cost_reconcile(uuid, text, integer, boolean), public.aita_cost_summary(uuid) to service_role;
do $$
declare t text;
begin
  foreach t in array array['aita_cost_budgets','aita_cost_ledger'] loop
    execute format('alter table public.%I enable row level security', t);
    execute format('revoke all privileges on public.%I from public, anon, authenticated, service_role', t);
    execute format('grant select, insert, update on public.%I to service_role', t);
  end loop;
end $$;

-- Cada tenant existente recibe el techo de 80 USD con subpresupuestos cerrados (los abre el operador).
insert into public.aita_cost_budgets (tenant_id) select id from public.tenants on conflict (tenant_id) do nothing;
