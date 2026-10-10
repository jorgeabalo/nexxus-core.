-- =====================================================================
-- AITA Marketing — Fase 2: "Marketing AI budget" (costo de los trabajos de generación de Marketing).
-- Migración NUEVA. No se ejecuta en producción desde este PR. No activa nada.
-- IMPORTANTE: esto NO es el presupuesto global de 80 USD por tenant (voz, IA, infraestructura…);
-- ese presupuesto global irá en otro PR. Aquí solo se limita el gasto de IA de Marketing.
--
-- * marketing_settings.monthly_ai_cost_limit (USD/mes) lo fija SOLO el operador. 0 por defecto:
--   ninguna generación, ni siquiera simulada. null = sin límite.
-- * marketing_generation_jobs.reserved_cost: costo reservado al aprobar (= costo máximo estimado).
--   El trabajo ya guardaba estimated_cost, actual_cost, selected_provider/model e idempotency_key.
-- * public.marketing_approve_generation(): aprueba y reserva de forma ATÓMICA. Bloquea la fila de
--   configuración del tenant (SELECT … FOR UPDATE): dos aprobaciones simultáneas nunca usan el mismo
--   saldo. Consumo del mes = reservado de los trabajos en cola/procesando + costo real de los terminados
--   (al cancelar o fallar, lo no gastado queda libre automáticamente).
-- Idempotente.
-- =====================================================================

alter table public.marketing_settings
  add column if not exists monthly_ai_cost_limit numeric(10,2) default 0
      check (monthly_ai_cost_limit is null or monthly_ai_cost_limit between 0 and 100000);

alter table public.marketing_generation_jobs
  add column if not exists reserved_cost numeric(10,4) check (reserved_cost is null or reserved_cost >= 0);
alter table public.marketing_generation_jobs drop constraint if exists marketing_generation_jobs_reserved_check;
alter table public.marketing_generation_jobs add constraint marketing_generation_jobs_reserved_check
  check (status in ('draft','awaiting_generation_approval') or reserved_cost is not null);

-- La reserva no cambia después de aprobar.
create or replace function private.marketing_job_reserved_guard()
returns trigger language plpgsql set search_path = '' as $$
begin
  if old.approved_at is not null and new.reserved_cost is distinct from old.reserved_cost then
    raise exception 'reserved cost is immutable after approval' using errcode = '23514';
  end if;
  return new;
end $$;
revoke all on function private.marketing_job_reserved_guard() from public, anon, authenticated;
drop trigger if exists marketing_job_reserved_guard on public.marketing_generation_jobs;
create trigger marketing_job_reserved_guard before update on public.marketing_generation_jobs
  for each row execute function private.marketing_job_reserved_guard();

-- Aprobar + reservar en una sola transacción. Devuelve {status: approved|rejected, reason, job}.
drop function if exists public.marketing_approve_generation(uuid, uuid, uuid, numeric);
-- Además del costo, reserva los bytes estimados de los resultados (cuenta para la cuota de almacenamiento).
create or replace function public.marketing_approve_generation(p_tenant uuid, p_job uuid, p_user uuid, p_reserved numeric,
  p_storage_bytes bigint)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare s public.marketing_settings; tz text; month_start timestamptz; used numeric; j public.marketing_generation_jobs;
        used_bytes bigint;
begin
  if p_reserved is null or p_reserved < 0 then
    return jsonb_build_object('status', 'rejected', 'reason', 'cost_not_estimable');
  end if;
  select * into s from public.marketing_settings where tenant_id = p_tenant for update;      -- serializa por tenant
  if not found or s.ai_generation_enabled is not true or s.monthly_ai_cost_limit = 0 then
    return jsonb_build_object('status', 'rejected', 'reason', 'generation_disabled');
  end if;
  select * into j from public.marketing_generation_jobs
   where id = p_job and tenant_id = p_tenant and status = 'awaiting_generation_approval' for update;
  if not found then
    return jsonb_build_object('status', 'rejected', 'reason', 'conflict');       -- otro tenant, ya aprobado o cambiado
  end if;
  select coalesce(t.timezone, 'America/Chicago') into tz from public.tenants t where t.id = p_tenant;
  month_start := date_trunc('month', now() at time zone tz) at time zone tz;
  select coalesce(sum(case when status in ('queued','processing') then coalesce(reserved_cost, 0)
                           else coalesce(actual_cost, 0) end), 0) into used
    from public.marketing_generation_jobs where tenant_id = p_tenant and approved_at >= month_start;
  if s.monthly_ai_cost_limit is not null and used + p_reserved > s.monthly_ai_cost_limit then
    return jsonb_build_object('status', 'rejected', 'reason', 'budget_exceeded',
                              'available', greatest(s.monthly_ai_cost_limit - used, 0));
  end if;
  if p_storage_bytes is null or p_storage_bytes < 0 then
    return jsonb_build_object('status', 'rejected', 'reason', 'storage_not_estimable');
  end if;
  if p_storage_bytes > 0 then
    if s.library_storage_limit_bytes = 0 then
      return jsonb_build_object('status', 'rejected', 'reason', 'library_disabled');
    end if;
    used_bytes := public.marketing_storage_used(p_tenant);
    if s.library_storage_limit_bytes is not null and used_bytes + p_storage_bytes > s.library_storage_limit_bytes then
      return jsonb_build_object('status', 'rejected', 'reason', 'limit_library_storage');
    end if;
  end if;
  update public.marketing_generation_jobs
     set status = 'queued', approved_at = now(), approved_by = p_user, reserved_cost = p_reserved,
         reserved_storage_bytes = p_storage_bytes
   where id = p_job and tenant_id = p_tenant and status = 'awaiting_generation_approval'
  returning * into j;
  if not found then
    return jsonb_build_object('status', 'rejected', 'reason', 'conflict');
  end if;
  -- la reserva de espacio del trabajo es una fila (se cierra sola cuando el trabajo termina; caduca en 24 h)
  if p_storage_bytes > 0 then
    insert into public.marketing_storage_reservations (tenant_id, reservation_key, kind, bytes, expires_at)
    values (p_tenant, 'job:' || p_job::text, 'generation', p_storage_bytes, now() + interval '24 hours')
    on conflict (tenant_id, reservation_key) do nothing;
  end if;
  insert into public.marketing_generation_job_events (tenant_id, job_id, action, from_status, to_status, detail,
                                                      actor_id, actor_role)
  values (p_tenant, p_job, 'approve', 'awaiting_generation_approval', 'queued',
          jsonb_build_object('reserved_cost', p_reserved, 'reserved_storage_bytes', p_storage_bytes), p_user,
          (select tu.role from public.tenant_users tu where tu.tenant_id = p_tenant and tu.user_id = p_user
             and tu.role in ('owner','manager') limit 1));
  return jsonb_build_object('status', 'approved', 'job', to_jsonb(j));
end $$;
revoke all on function public.marketing_approve_generation(uuid, uuid, uuid, numeric, bigint) from public, anon, authenticated;
grant execute on function public.marketing_approve_generation(uuid, uuid, uuid, numeric, bigint) to service_role;
