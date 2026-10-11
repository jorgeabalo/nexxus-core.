-- =====================================================================
-- AITA Marketing — Fase 2: seguridad de ejecución (antes de habilitar Biblioteca o generación).
-- Migración NUEVA (después de 20261011140000_marketing_generation_budget.sql). No activa nada.
--
-- 1. Archivo vencido (expires_at <= now()) = inaccesible aunque el purgador no haya corrido:
--    fuera del SELECT de authenticated (RLS), nunca entrada de un trabajo, nunca en cola/proceso,
--    ninguna sesión de reproducción nueva y su vencimiento ya no puede ampliarse.
-- 2. Worker: reclamo atómico con lease (FOR UPDATE SKIP LOCKED), heartbeat, reintentos limitados que
--    conservan la idempotency_key y timeout que libera reservas una sola vez y bloquea los resultados.
-- 3. Purgador: un solo RetentionRunner a la vez (lease en marketing_runtime_leases). No se usa
--    pg_advisory_lock porque PostgREST reparte las llamadas entre conexiones del pool.
-- 4. "Marketing AI budget" (SOLO IA de Marketing): desconocido o 0 = cerrado; máximo TEMPORAL de 20 USD/mes
--    mientras no exista un ledger global (voz, infraestructura, almacenamiento e IA). Esto NO garantiza por sí
--    solo el objetivo de costo total de AITA (80 USD por tenant).
-- Idempotente. No borra datos.
-- =====================================================================

-- ---------- 1. archivos vencidos ----------
drop policy if exists marketing_media_select on public.marketing_media;
create policy marketing_media_select on public.marketing_media for select to authenticated
  using (private.has_tenant_role(tenant_id, array['owner','manager']) and expires_at > now()
         and retention_status not in ('purge_pending','purged','purge_failed'));
drop policy if exists marketing_media_derivatives_select on public.marketing_media_derivatives;
create policy marketing_media_derivatives_select on public.marketing_media_derivatives for select to authenticated
  using (private.has_tenant_role(tenant_id, array['owner','manager']) and expires_at > now()
         and exists (select 1 from public.marketing_media m
                      where m.id = marketing_media_derivatives.media_id
                        and m.tenant_id = marketing_media_derivatives.tenant_id
                        and m.expires_at > now()
                        and m.retention_status not in ('purge_pending','purged','purge_failed')));

-- Entradas de un trabajo: nunca un archivo vencido o pendiente de purga.
create or replace function private.marketing_input_guard()
returns trigger language plpgsql set search_path = '' as $$
begin
  if new.media_id is not null and not exists (
       select 1 from public.marketing_media m where m.id = new.media_id and m.tenant_id = new.tenant_id
          and m.processing_status = 'ready' and m.validation_status = 'passed'
          and m.expires_at > now() and m.retention_status not in ('purge_pending','purged','purge_failed')
          and not (m.people_policy = 'exclude' and m.contains_people is not false)
          and not (m.contains_people is not false and m.contains_minors is not false)) then
    raise exception 'media not allowed as generation input' using errcode = '23514';
  end if;
  if new.derivative_id is not null and not exists (
       select 1 from public.marketing_media_derivatives d where d.id = new.derivative_id and d.tenant_id = new.tenant_id
          and d.status = 'ready' and not d.is_mock) then
    raise exception 'derivative not allowed as generation input' using errcode = '23514';
  end if;
  return new;
end $$;
revoke all on function private.marketing_input_guard() from public, anon, authenticated;

-- En cola o en proceso: las entradas siguen permitidas (también: no vencidas).
create or replace function private.marketing_job_transition()
returns trigger language plpgsql set search_path = '' as $$
declare allowed text[];
begin
  if tg_op = 'INSERT' then
    if new.status <> 'draft' or new.approved_at is not null or new.approved_by is not null then
      raise exception 'new generation job must start as unapproved draft' using errcode = '23514';
    end if;
    return new;
  end if;
  if (new.tenant_id, new.created_by, new.idempotency_key, new.task_type, new.created_at, new.regeneration_of)
     is distinct from (old.tenant_id, old.created_by, old.idempotency_key, old.task_type, old.created_at, old.regeneration_of) then
    raise exception 'generation job identity is immutable' using errcode = '23514';
  end if;
  -- una vez aprobada, la mezcla, la calidad, la política y el presupuesto no cambian
  if old.approved_at is not null and (new.real_media_percent, new.ai_media_percent, new.quality_tier, new.people_policy,
       new.maximum_cost, new.approved_at, new.approved_by)
     is distinct from (old.real_media_percent, old.ai_media_percent, old.quality_tier, old.people_policy,
       old.maximum_cost, old.approved_at, old.approved_by) then
    raise exception 'approved generation parameters are immutable' using errcode = '23514';
  end if;
  -- la evidencia de coste y proveedor nunca se borra ni disminuye
  if (old.actual_cost is not null and (new.actual_cost is null or new.actual_cost < old.actual_cost))
     or (old.selected_provider is not null and new.selected_provider is distinct from old.selected_provider)
     or (old.selected_model is not null and new.selected_model is distinct from old.selected_model)
     or (old.provider_job_id is not null and new.provider_job_id is distinct from old.provider_job_id) then
    raise exception 'generation cost/provider evidence is immutable' using errcode = '23514';
  end if;
  if new.status is distinct from old.status then
    allowed := case old.status
      when 'draft'                        then array['awaiting_generation_approval','cancelled']
      when 'awaiting_generation_approval' then array['queued','draft','cancelled']
      when 'queued'                       then array['processing','failed','cancelled']
      when 'processing'                   then array['succeeded','failed','cancelled']
      else array[]::text[] end;                       -- succeeded / failed / cancelled son finales
    if not (new.status = any (allowed)) then
      raise exception 'invalid generation job transition % -> %', old.status, new.status using errcode = '23514';
    end if;
    -- nada entra en cola sin aprobación explícita de owner/manager
    if new.status = 'queued' and (new.approved_at is null or new.approved_by is null) then
      raise exception 'generation approval required' using errcode = '23514';
    end if;
    -- al entrar en cola o empezar: las entradas siguen permitidas (consentimiento retirado, menores,
    -- archivo eliminado o excluido → bloqueo inmediato)
    if new.status in ('queued','processing') and exists (
         select 1 from public.marketing_generation_inputs i
           join public.marketing_media m on m.id = i.media_id and m.tenant_id = i.tenant_id
          where i.job_id = new.id and i.tenant_id = new.tenant_id
            and (m.processing_status <> 'ready' or m.expires_at <= now()
                 or m.retention_status in ('purge_pending','purged','purge_failed')
                 or (m.people_policy = 'exclude' and m.contains_people is not false)
                 or (m.people_policy = 'consented' and m.consent_status <> 'granted')
                 or (m.contains_people is not false and m.contains_minors is not false))) then
      raise exception 'generation inputs are no longer allowed' using errcode = '23514';
    end if;
  elsif old.status in ('succeeded','failed','cancelled') and (new.real_media_percent, new.ai_media_percent,
        new.result_metadata, new.error_code, new.completed_at)
        is distinct from (old.real_media_percent, old.ai_media_percent, old.result_metadata, old.error_code, old.completed_at) then
    raise exception 'finished generation job is immutable' using errcode = '23514';
  end if;
  return new;
end $$;
revoke all on function private.marketing_job_transition() from public, anon, authenticated;

create or replace function private.marketing_media_guard()
returns trigger language plpgsql set search_path = '' as $$
declare allowed text[];
begin
  if tg_op = 'DELETE' then
    raise exception 'use controlled deletion (purge) to keep the audit trail' using errcode = '42501';
  end if;
  if tg_op = 'INSERT' then
    if new.processing_status <> 'uploaded' or new.deleted_at is not null or new.retention_status <> 'active' then
      raise exception 'new media must start as uploaded and active' using errcode = '23514';
    end if;
    return new;
  end if;
  if old.processing_status = 'deleted' then
    raise exception 'purged media cannot change' using errcode = '42501';
  end if;
  if (new.tenant_id, new.storage_bucket, new.mime_type, new.media_type, new.byte_size, new.checksum, new.uploaded_by,
      new.created_at) is distinct from (old.tenant_id, old.storage_bucket, old.mime_type, old.media_type, old.byte_size,
      old.checksum, old.uploaded_by, old.created_at) then
    raise exception 'marketing original is immutable' using errcode = '42501';
  end if;
  if new.storage_path is distinct from old.storage_path or new.original_filename is distinct from old.original_filename then
    if not (new.retention_status = 'purged' and new.processing_status = 'deleted'
            and new.storage_path = new.tenant_id::text || '/originals/' || new.id::text || '/purged'
            and new.original_filename is null) then
      raise exception 'marketing original is immutable' using errcode = '42501';
    end if;
  end if;
  -- Un archivo vencido no "resucita": su vencimiento ya no puede ampliarse.
  if old.expires_at <= now() and new.expires_at > old.expires_at then
    raise exception 'expired media cannot be extended' using errcode = '23514';
  end if;
  -- Extender solo dentro del máximo del plan; tras publicarse, hasta mínimo(publicación + 30, subida + 90).
  if new.expires_at > old.expires_at
     and new.expires_at > old.created_at + make_interval(days => coalesce((select s.max_retention_days
       from public.marketing_settings s where s.tenant_id = new.tenant_id), 30))
     and not (new.published_at is not null
              and new.expires_at <= least(new.published_at + interval '30 days', old.created_at + interval '90 days')) then
    raise exception 'retention exceeds the plan maximum' using errcode = '23514';
  end if;
  if new.processing_status is distinct from old.processing_status then
    allowed := case old.processing_status
      when 'uploaded'   then array['scanning','ready','rejected','deleted']
      when 'scanning'   then array['ready','rejected','failed','deleted']
      when 'ready'      then array['processing','archived','deleted']
      when 'processing' then array['ready','failed']
      when 'failed'     then array['ready','archived','deleted']
      when 'rejected'   then array['archived','deleted']
      when 'archived'   then array['ready','deleted']
      else array[]::text[] end;
    if not (new.processing_status = any (allowed)) then
      raise exception 'invalid media status % -> %', old.processing_status, new.processing_status using errcode = '23514';
    end if;
    -- un trabajo activo protege el archivo, salvo que el límite de seguridad haya vencido
    if new.processing_status = 'deleted' and exists (
         select 1 from public.marketing_generation_inputs i
           join public.marketing_generation_jobs j on j.id = i.job_id and j.tenant_id = i.tenant_id
          where i.tenant_id = new.tenant_id and i.media_id = new.id
            and j.status in ('draft','awaiting_generation_approval','queued','processing')) then
      raise exception 'media is used by an active generation job' using errcode = '23514';
    end if;
  end if;
  return new;
end $$;
revoke all on function private.marketing_media_guard() from public, anon, authenticated;

-- Sesión de reproducción: solo para un archivo accesible, de un owner/manager activo del tenant, y
-- nunca más allá del vencimiento del archivo.
create or replace function private.marketing_stream_token_guard()
returns trigger language plpgsql set search_path = '' as $$
declare exp timestamptz;
begin
  select m.expires_at into exp from public.marketing_media m
   where m.id = new.media_id and m.tenant_id = new.tenant_id
     and m.validation_status = 'passed' and m.processing_status not in ('deleted','rejected')
     and m.retention_status not in ('purge_pending','purged','purge_failed') and m.expires_at > now();
  if not found then
    raise exception 'media is not accessible' using errcode = '23514';
  end if;
  if not exists (select 1 from public.tenant_users tu where tu.tenant_id = new.tenant_id and tu.user_id = new.user_id
                   and tu.active and tu.role in ('owner','manager')) then
    raise exception 'stream session requires an active owner or manager' using errcode = '42501';
  end if;
  new.expires_at := least(new.expires_at, exp);
  return new;
end $$;
revoke all on function private.marketing_stream_token_guard() from public, anon, authenticated;
drop trigger if exists marketing_stream_token_guard on public.marketing_stream_tokens;
create trigger marketing_stream_token_guard before insert on public.marketing_stream_tokens
  for each row execute function private.marketing_stream_token_guard();

-- ---------- 2. worker: lease, heartbeat, reintentos y timeout ----------
alter table public.marketing_generation_jobs
  add column if not exists lease_owner      uuid,
  add column if not exists lease_expires_at timestamptz,
  add column if not exists heartbeat_at     timestamptz,
  add column if not exists attempts         integer not null default 0 check (attempts between 0 and 20);
create index if not exists marketing_generation_jobs_claim_idx
  on public.marketing_generation_jobs(status, approved_at) where status in ('queued','processing');

-- Solo quien tiene el lease vigente puede terminar con éxito; nadie roba un lease vigente.
create or replace function private.marketing_job_lease_guard()
returns trigger language plpgsql set search_path = '' as $$
begin
  if old.status = 'processing' and new.status = 'succeeded'
     and (old.lease_owner is null or old.lease_expires_at is null or old.lease_expires_at <= now()
          or new.lease_owner is distinct from old.lease_owner) then
    raise exception 'generation job lease expired or not held' using errcode = '23514';
  end if;
  if old.status = 'processing' and new.lease_owner is distinct from old.lease_owner
     and old.lease_owner is not null and old.lease_expires_at > now() then
    raise exception 'generation job lease is held by another worker' using errcode = '23514';
  end if;
  return new;
end $$;
revoke all on function private.marketing_job_lease_guard() from public, anon, authenticated;
drop trigger if exists marketing_job_lease_guard on public.marketing_generation_jobs;
create trigger marketing_job_lease_guard before update on public.marketing_generation_jobs
  for each row execute function private.marketing_job_lease_guard();

-- Resultados: solo mientras el trabajo está en proceso con lease vigente (un trabajo vencido no produce nada).
create or replace function private.marketing_output_guard()
returns trigger language plpgsql set search_path = '' as $$
begin
  if not exists (select 1 from public.marketing_generation_jobs j where j.id = new.job_id and j.tenant_id = new.tenant_id
                   and j.status = 'processing' and j.lease_expires_at > now()) then
    raise exception 'outputs require a processing job with a live lease' using errcode = '23514';
  end if;
  return new;
end $$;
revoke all on function private.marketing_output_guard() from public, anon, authenticated;
drop trigger if exists marketing_output_guard on public.marketing_generation_outputs;
create trigger marketing_output_guard before insert on public.marketing_generation_outputs
  for each row execute function private.marketing_output_guard();

-- Reclamar un trabajo: el más antiguo en cola (o uno en proceso con lease vencido y reintentos
-- disponibles). FOR UPDATE SKIP LOCKED: dos workers nunca toman el mismo. Si sus entradas ya no
-- están permitidas (vencidas, consentimiento retirado…), falla con un código público y no bloquea la cola.
create or replace function public.marketing_claim_job(p_worker uuid, p_lease_seconds integer, p_job uuid default null,
  p_max_attempts integer default 3)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare j public.marketing_generation_jobs; lease interval; retry boolean := false; maxa integer;
begin
  if p_worker is null then
    return jsonb_build_object('status', 'rejected', 'reason', 'invalid_worker');
  end if;
  lease := make_interval(secs => least(greatest(coalesce(p_lease_seconds, 120), 30), 900));
  maxa := least(greatest(coalesce(p_max_attempts, 3), 1), 10);
  select * into j from public.marketing_generation_jobs
   where status = 'queued' and approved_at is not null and approved_by is not null and reserved_cost is not null
     and approved_at > now() - interval '24 hours' and (p_job is null or id = p_job)
   order by approved_at, id limit 1 for update skip locked;
  if not found then
    select * into j from public.marketing_generation_jobs
     where status = 'processing' and lease_expires_at is not null and lease_expires_at <= now()
       and attempts < maxa and approved_at > now() - interval '24 hours' and (p_job is null or id = p_job)
     order by lease_expires_at, id limit 1 for update skip locked;
    if not found then
      return jsonb_build_object('status', 'empty');
    end if;
    retry := true;
  end if;
  begin
    update public.marketing_generation_jobs
       set status = 'processing', lease_owner = p_worker, lease_expires_at = now() + lease, heartbeat_at = now(),
           attempts = attempts + 1
     where id = j.id returning * into j;
  exception when check_violation then                  -- entradas ya no permitidas: falla, no bloquea la cola
    update public.marketing_generation_jobs
       set status = 'failed', error_code = 'media_not_ready', completed_at = now(), lease_owner = null,
           lease_expires_at = null
     where id = j.id returning * into j;
    insert into public.marketing_generation_job_events (tenant_id, job_id, action, from_status, to_status, detail,
                                                        actor_id, actor_role)
    values (j.tenant_id, j.id, 'fail', case when retry then 'processing' else 'queued' end, 'failed',
            jsonb_build_object('error_code', 'media_not_ready'), null, 'system');
    return jsonb_build_object('status', 'skipped', 'job_id', j.id);
  end;
  insert into public.marketing_generation_job_events (tenant_id, job_id, action, from_status, to_status, detail,
                                                      actor_id, actor_role)
  values (j.tenant_id, j.id, 'start', case when retry then 'processing' else 'queued' end, 'processing',
          jsonb_build_object('attempt', j.attempts, 'retry', retry), null, 'system');
  return jsonb_build_object('status', 'claimed', 'retry', retry, 'job', to_jsonb(j));
end $$;

-- Heartbeat: solo el dueño de un lease vigente lo renueva. Devuelve true o null (lease perdido).
create or replace function public.marketing_job_heartbeat(p_job uuid, p_worker uuid, p_lease_seconds integer)
returns boolean language sql security definer set search_path = '' as $$
  update public.marketing_generation_jobs
     set heartbeat_at = now(),
         lease_expires_at = now() + make_interval(secs => least(greatest(coalesce(p_lease_seconds, 120), 30), 900))
   where id = p_job and status = 'processing' and lease_owner = p_worker and lease_expires_at > now()
  returning true $$;

-- Timeout: en proceso con lease vencido y sin reintentos, o aprobado hace más de 24 h sin terminar →
-- failed/timeout. Sus reservas se cierran una sola vez (costo: deja de contar lo reservado; espacio:
-- disparador marketing_job_storage_release) y sus resultados quedan bloqueados.
create or replace function public.marketing_timeout_jobs(p_max_attempts integer default 3)
returns integer language plpgsql security definer set search_path = '' as $$
declare j record; n integer := 0; maxa integer := least(greatest(coalesce(p_max_attempts, 3), 1), 10);
begin
  for j in select id, tenant_id, status from public.marketing_generation_jobs
            where (status = 'processing' and (lease_expires_at is null or lease_expires_at <= now())
                   and (attempts >= maxa or approved_at <= now() - interval '24 hours'))
               or (status = 'queued' and approved_at <= now() - interval '24 hours')
            order by approved_at limit 500
            for update skip locked loop
    update public.marketing_generation_jobs
       set status = 'failed', error_code = 'timeout', completed_at = now(), lease_owner = null, lease_expires_at = null
     where id = j.id;
    update public.marketing_generation_outputs
       set review_status = 'rejected', metadata = metadata || jsonb_build_object('blocked', 'timeout')
     where job_id = j.id and tenant_id = j.tenant_id;
    insert into public.marketing_generation_job_events (tenant_id, job_id, action, from_status, to_status, detail,
                                                        actor_id, actor_role)
    values (j.tenant_id, j.id, 'fail', j.status, 'failed', jsonb_build_object('error_code', 'timeout'), null, 'system');
    n := n + 1;
  end loop;
  return n;
end $$;

-- ---------- 3. un solo purgador a la vez ----------
create table if not exists public.marketing_runtime_leases (
  name        text primary key check (name in ('retention_runner')),
  holder      uuid not null,
  acquired_at timestamptz not null default now(),
  expires_at  timestamptz not null
);
create or replace function public.marketing_acquire_runtime_lease(p_name text, p_holder uuid, p_ttl_seconds integer)
returns boolean language sql security definer set search_path = '' as $$
  insert into public.marketing_runtime_leases as l (name, holder, acquired_at, expires_at)
  values (p_name, p_holder, now(), now() + make_interval(secs => least(greatest(coalesce(p_ttl_seconds, 300), 30), 3600)))
  on conflict (name) do update set holder = excluded.holder, acquired_at = excluded.acquired_at,
                                   expires_at = excluded.expires_at
   where l.expires_at <= now() or l.holder = excluded.holder
  returning true $$;
create or replace function public.marketing_release_runtime_lease(p_name text, p_holder uuid)
returns boolean language sql security definer set search_path = '' as $$
  delete from public.marketing_runtime_leases where name = p_name and holder = p_holder returning true $$;
alter table public.marketing_runtime_leases enable row level security;
revoke all privileges on public.marketing_runtime_leases from public, anon, authenticated, service_role;
grant select, insert, update, delete on public.marketing_runtime_leases to service_role;

-- ---------- 4. "Marketing AI budget": desconocido o 0 = cerrado; máximo temporal 20 USD ----------
update public.marketing_settings set monthly_ai_cost_limit = 0 where monthly_ai_cost_limit is null;
alter table public.marketing_settings alter column monthly_ai_cost_limit set not null;
alter table public.marketing_settings drop constraint if exists marketing_settings_ai_budget_cap;
alter table public.marketing_settings add constraint marketing_settings_ai_budget_cap
  check (monthly_ai_cost_limit between 0 and 20);
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
  if not found or s.ai_generation_enabled is not true or coalesce(s.monthly_ai_cost_limit, 0) <= 0 then
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
  if used + p_reserved > s.monthly_ai_cost_limit then
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

revoke all on function public.marketing_claim_job(uuid, integer, uuid, integer),
  public.marketing_job_heartbeat(uuid, uuid, integer), public.marketing_timeout_jobs(integer),
  public.marketing_acquire_runtime_lease(text, uuid, integer), public.marketing_release_runtime_lease(text, uuid),
  public.marketing_approve_generation(uuid, uuid, uuid, numeric, bigint) from public, anon, authenticated;
grant execute on function public.marketing_claim_job(uuid, integer, uuid, integer),
  public.marketing_job_heartbeat(uuid, uuid, integer), public.marketing_timeout_jobs(integer),
  public.marketing_acquire_runtime_lease(text, uuid, integer), public.marketing_release_runtime_lease(text, uuid),
  public.marketing_approve_generation(uuid, uuid, uuid, numeric, bigint) to service_role;
