-- =====================================================================
-- AITA Marketing — Fase 2: finalización atómica del worker y cierre de purga con lease.
-- Migración NUEVA (después de 20261012120000_marketing_runtime_safety.sql, que define el guardián de
-- transiciones que estas funciones usan). No activa nada.
--
-- * marketing_finalize_generation: costo real, resultados, succeeded y reserva en UNA transacción.
-- * marketing_fail_generation: el worker falla su trabajo (lease vigente y mismo worker_id) registrando costos.
-- * marketing_cancel_generation: cancelación externa (usuario, consentimiento, purga); nunca éxito.
-- * marketing_close_purge: cierre de una purga solo con el lease vigente del purgador (o borrado de owner/manager).
-- Solo service_role ejecuta estas funciones. Idempotente.
-- =====================================================================

-- ---------- finalización atómica del worker ----------
-- Un resultado por trabajo/tipo/escena/archivo: un reintento nunca duplica resultados.
create unique index if not exists marketing_generation_outputs_unique_idx
  on public.marketing_generation_outputs(tenant_id, job_id, kind, coalesce(scene_index, -1), coalesce(storage_path, ''));

-- TODO en una transacción: bloquea el trabajo; comprueba dueño, lease vigente y processing; vuelve a validar
-- presupuesto (generación habilitada, costo real ≤ mínimo(máximo, reservado)) y almacenamiento (tamaño real
-- frente a la reserva y la cuota; temporales solo de {tenant}/derivatives/{trabajo}/{worker}-…); registra el
-- costo real de forma idempotente (idempotency_key); publica TODOS los resultados; marca succeeded y la
-- reserva se consume una sola vez (disparador). Si algo falla, no queda nada: ni resultados ni estado parcial.
create or replace function public.marketing_finalize_generation(p_tenant uuid, p_job uuid, p_worker uuid,
  p_usage jsonb, p_outputs jsonb, p_result jsonb, p_selected jsonb)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare j public.marketing_generation_jobs; s public.marketing_settings; old_cost numeric; new_cost numeric;
        v_bytes bigint; held bigint; used bigint;
begin
  select * into j from public.marketing_generation_jobs where id = p_job and tenant_id = p_tenant for update;
  if not found or j.status <> 'processing' or j.lease_owner is distinct from p_worker
     or j.lease_expires_at is null or j.lease_expires_at <= now() then
    return jsonb_build_object('status', 'rejected', 'reason', 'lease_lost');
  end if;
  select * into s from public.marketing_settings where tenant_id = p_tenant for update;
  if not found or s.ai_generation_enabled is not true or coalesce(s.monthly_ai_cost_limit, 0) <= 0 then
    return jsonb_build_object('status', 'rejected', 'reason', 'budget_exceeded');
  end if;
  select coalesce(sum(actual_cost), 0) into old_cost from public.marketing_model_usage
   where tenant_id = p_tenant and job_id = p_job;
  select coalesce(sum((x->>'actual_cost')::numeric), 0) into new_cost
    from jsonb_array_elements(coalesce(p_usage, '[]'::jsonb)) x
   where not exists (select 1 from public.marketing_model_usage m
                      where m.tenant_id = p_tenant and m.idempotency_key = x->>'idempotency_key');
  if old_cost + new_cost > least(j.maximum_cost, coalesce(j.reserved_cost, 0)) then
    return jsonb_build_object('status', 'rejected', 'reason', 'budget_exceeded');
  end if;
  if exists (select 1 from jsonb_array_elements(coalesce(p_outputs, '[]'::jsonb)) o
              where o->>'storage_path' is not null
                and (split_part(o->>'storage_path', '/', 1) <> p_tenant::text
                     or split_part(o->>'storage_path', '/', 2) <> 'derivatives'
                     or split_part(o->>'storage_path', '/', 3) <> p_job::text
                     or split_part(o->>'storage_path', '/', 4) not like p_worker::text || '-%'
                     or split_part(o->>'storage_path', '/', 5) <> ''
                     or coalesce((o->>'byte_size')::bigint, 0) <= 0)) then
    return jsonb_build_object('status', 'rejected', 'reason', 'invalid_output');
  end if;
  select coalesce(sum((o->>'byte_size')::bigint), 0) into v_bytes
    from jsonb_array_elements(coalesce(p_outputs, '[]'::jsonb)) o where o->>'storage_path' is not null;
  if v_bytes > 0 then
    select r.bytes into held from public.marketing_storage_reservations r
     where r.tenant_id = p_tenant and r.reservation_key = 'job:' || p_job::text and r.status = 'reserved'
       and r.expires_at > now() for update;
    if not found then
      return jsonb_build_object('status', 'rejected', 'reason', 'reservation_expired');
    end if;
    used := public.marketing_storage_used(p_tenant);
    if s.library_storage_limit_bytes is not null and used - held + v_bytes > s.library_storage_limit_bytes then
      return jsonb_build_object('status', 'rejected', 'reason', 'storage_quota_exceeded');
    end if;
    update public.marketing_storage_reservations set bytes = v_bytes
     where tenant_id = p_tenant and reservation_key = 'job:' || p_job::text and status = 'reserved';
  end if;
  insert into public.marketing_model_usage (tenant_id, job_id, provider, model_id, task_type, catalog_version, billing_unit,
         units, estimated_cost, actual_cost, currency, idempotency_key, provider_job_id, status, worker_id)
  select p_tenant, p_job, x.provider, x.model_id, x.task_type, x.catalog_version, x.billing_unit, x.units,
         x.estimated_cost, x.actual_cost, coalesce(x.currency, 'USD'), x.idempotency_key, x.provider_job_id, x.status, p_worker
    from jsonb_to_recordset(coalesce(p_usage, '[]'::jsonb)) as x(provider text, model_id text, task_type text,
         catalog_version text, billing_unit text, units numeric, estimated_cost numeric, actual_cost numeric,
         currency text, idempotency_key text, provider_job_id text, status text)
  on conflict (tenant_id, idempotency_key) do nothing;
  insert into public.marketing_generation_outputs (tenant_id, job_id, worker_id, kind, scene_index, origin, duration_ms,
         storage_path, mime_type, byte_size, review_status, metadata, expires_at)
  select p_tenant, p_job, p_worker, o.kind, o.scene_index, o.origin, o.duration_ms, o.storage_path, o.mime_type,
         o.byte_size, 'generated', coalesce(o.metadata, '{}'::jsonb), o.expires_at
    from jsonb_to_recordset(coalesce(p_outputs, '[]'::jsonb)) as o(kind text, scene_index integer, origin text,
         duration_ms integer, storage_path text, mime_type text, byte_size bigint, metadata jsonb, expires_at timestamptz);
  perform set_config('marketing.transition', 'worker:' || p_worker::text, true);
  update public.marketing_generation_jobs
     set status = 'succeeded', completed_at = now(), result_metadata = coalesce(p_result, '{}'::jsonb),
         actual_cost = (select coalesce(sum(actual_cost), 0) from public.marketing_model_usage
                         where tenant_id = p_tenant and job_id = p_job),
         selected_provider = coalesce(p_selected->>'provider', selected_provider),
         selected_model = coalesce(p_selected->>'model', selected_model),
         provider_job_id = coalesce(p_selected->>'provider_job_id', provider_job_id)
   where id = p_job and tenant_id = p_tenant returning * into j;
  perform set_config('marketing.transition', '', true);
  insert into public.marketing_generation_job_events (tenant_id, job_id, action, from_status, to_status, detail,
                                                      actor_id, actor_role)
  values (p_tenant, p_job, 'succeed', 'processing', 'succeeded',
          jsonb_build_object('worker', p_worker, 'attempt', j.attempts), null, 'system');
  return jsonb_build_object('status', 'succeeded', 'job', to_jsonb(j));
end $$;

-- El worker falla SU trabajo (lease vigente y mismo worker_id): registra los costos reales ya incurridos
-- (idempotente) y marca failed en la misma transacción. Un worker vencido no puede.
create or replace function public.marketing_fail_generation(p_tenant uuid, p_job uuid, p_worker uuid, p_error_code text,
  p_usage jsonb)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare j public.marketing_generation_jobs;
begin
  select * into j from public.marketing_generation_jobs where id = p_job and tenant_id = p_tenant for update;
  if not found or j.status <> 'processing' or j.lease_owner is distinct from p_worker
     or j.lease_expires_at is null or j.lease_expires_at <= now() then
    return jsonb_build_object('status', 'rejected', 'reason', 'lease_lost');
  end if;
  insert into public.marketing_model_usage (tenant_id, job_id, provider, model_id, task_type, catalog_version, billing_unit,
         units, estimated_cost, actual_cost, currency, idempotency_key, provider_job_id, status, worker_id)
  select p_tenant, p_job, x.provider, x.model_id, x.task_type, x.catalog_version, x.billing_unit, x.units,
         x.estimated_cost, x.actual_cost, coalesce(x.currency, 'USD'), x.idempotency_key, x.provider_job_id, x.status, p_worker
    from jsonb_to_recordset(coalesce(p_usage, '[]'::jsonb)) as x(provider text, model_id text, task_type text,
         catalog_version text, billing_unit text, units numeric, estimated_cost numeric, actual_cost numeric,
         currency text, idempotency_key text, provider_job_id text, status text)
  on conflict (tenant_id, idempotency_key) do nothing;
  perform set_config('marketing.transition', 'worker:' || p_worker::text, true);
  update public.marketing_generation_jobs
     set status = 'failed', error_code = p_error_code, completed_at = now(),
         actual_cost = (select coalesce(sum(actual_cost), 0) from public.marketing_model_usage
                         where tenant_id = p_tenant and job_id = p_job)
   where id = p_job and tenant_id = p_tenant returning * into j;
  perform set_config('marketing.transition', '', true);
  insert into public.marketing_generation_job_events (tenant_id, job_id, action, from_status, to_status, detail,
                                                      actor_id, actor_role)
  values (p_tenant, p_job, 'fail', 'processing', 'failed', jsonb_build_object('error_code', p_error_code), null, 'system');
  return jsonb_build_object('status', 'failed', 'job', to_jsonb(j));
end $$;

-- Cancelación EXTERNA (usuario, consentimiento retirado, purga): camino separado del worker. Nunca marca éxito;
-- bloquea los resultados del trabajo para que no se reutilicen. Idempotente.
create or replace function public.marketing_cancel_generation(p_tenant uuid, p_job uuid, p_to text, p_error_code text,
  p_actor uuid, p_role text)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare j public.marketing_generation_jobs; frm text;
begin
  if p_to not in ('failed','cancelled') then
    return jsonb_build_object('status', 'rejected', 'reason', 'invalid_transition');
  end if;
  select * into j from public.marketing_generation_jobs where id = p_job and tenant_id = p_tenant for update;
  if not found then
    return jsonb_build_object('status', 'rejected', 'reason', 'not_found');
  end if;
  if j.status in ('succeeded','failed','cancelled') then
    return jsonb_build_object('status', 'unchanged', 'job', to_jsonb(j));
  end if;
  frm := j.status;
  perform set_config('marketing.transition', 'cancel', true);
  update public.marketing_generation_jobs set status = p_to, error_code = p_error_code, completed_at = now()
   where id = p_job and tenant_id = p_tenant returning * into j;
  perform set_config('marketing.transition', '', true);
  update public.marketing_generation_outputs
     set review_status = 'rejected', metadata = metadata || jsonb_build_object('blocked', p_error_code)
   where job_id = p_job and tenant_id = p_tenant;
  insert into public.marketing_generation_job_events (tenant_id, job_id, action, from_status, to_status, detail,
                                                      actor_id, actor_role)
  values (p_tenant, p_job, case when p_to = 'failed' then 'fail' else 'cancel' end, frm, p_to,
          jsonb_build_object('error_code', p_error_code), p_actor,
          case when p_role in ('owner','manager') then p_role else 'system' end);
  return jsonb_build_object('status', p_to, 'job', to_jsonb(j));
end $$;

-- Cerrar una purga: en UNA transacción y solo si el purgador sigue teniendo el lease vigente (o, sin lease, una
-- eliminación pedida por owner/manager). Cierre condicional: un archivo nunca se cierra dos veces.
create or replace function public.marketing_close_purge(p_tenant uuid, p_media uuid, p_holder uuid, p_reason text,
  p_actor uuid, p_role text)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare m public.marketing_media;
begin
  if p_holder is not null then
    perform 1 from public.marketing_runtime_leases
     where name = 'retention_runner' and holder = p_holder and expires_at > now() for update;
    if not found then
      return jsonb_build_object('status', 'lease_lost');
    end if;
  elsif p_actor is null or p_role not in ('owner','manager') or p_reason not in ('user_deleted','consent_revoked') then
    return jsonb_build_object('status', 'rejected', 'reason', 'lease_required');
  end if;
  update public.marketing_media
     set processing_status = 'deleted', retention_status = 'purged', purged_at = now(), deleted_at = now(),
         deleted_by = p_actor, purge_reason = coalesce(purge_reason, p_reason), original_filename = null,
         metadata = '{}'::jsonb, storage_path = tenant_id::text || '/originals/' || id::text || '/purged',
         last_purge_error = null
   where id = p_media and tenant_id = p_tenant and processing_status <> 'deleted'
     and retention_status = 'purge_pending'
  returning * into m;
  if not found then
    return jsonb_build_object('status', 'already_closed');
  end if;
  update public.marketing_media_derivatives set status = 'deleted', purged_at = now(), metadata = '{}'::jsonb
   where media_id = p_media and tenant_id = p_tenant and status <> 'deleted';
  insert into public.marketing_media_events (tenant_id, media_id, action, actor_id, actor_role, detail)
  values (p_tenant, p_media, 'purged', p_actor, case when p_role in ('owner','manager') then p_role else 'system' end,
          jsonb_build_object('reason', m.purge_reason));
  return jsonb_build_object('status', 'closed');
end $$;

revoke all on function public.marketing_finalize_generation(uuid, uuid, uuid, jsonb, jsonb, jsonb, jsonb),
  public.marketing_fail_generation(uuid, uuid, uuid, text, jsonb),
  public.marketing_cancel_generation(uuid, uuid, text, text, uuid, text),
  public.marketing_close_purge(uuid, uuid, uuid, text, uuid, text) from public, anon, authenticated;
grant execute on function public.marketing_finalize_generation(uuid, uuid, uuid, jsonb, jsonb, jsonb, jsonb),
  public.marketing_fail_generation(uuid, uuid, uuid, text, jsonb),
  public.marketing_cancel_generation(uuid, uuid, text, text, uuid, text),
  public.marketing_close_purge(uuid, uuid, uuid, text, uuid, text) to service_role;
