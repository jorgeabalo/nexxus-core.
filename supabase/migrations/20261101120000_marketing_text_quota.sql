-- AITA Marketing — cupo diario de «Escribir con IA» reservado de forma atómica.
--
-- Antes: el backend contaba las redacciones de las últimas 24 h, llamaba a Claude y solo después registraba el
-- intento; dos peticiones simultáneas podían leer el mismo cupo libre y superar el límite.
-- Ahora: marketing_reserve_text_generation cuenta y reserva (fila 'pending') en una sola transacción, bajo un
-- candado por tenant, ANTES de llamar a Claude. El backend liquida la fila después (succeeded/failed/rejected).
-- Una reserva que no llega a liquidarse sigue contando hasta que pasan 24 h: el cupo falla cerrado.
alter table public.marketing_text_generations
  drop constraint if exists marketing_text_generations_status_check;
alter table public.marketing_text_generations
  add constraint marketing_text_generations_status_check
  check (status in ('pending', 'succeeded', 'failed', 'rejected'));

create or replace function public.marketing_reserve_text_generation(
  p_tenant uuid, p_content uuid, p_actor uuid, p_model text, p_limit integer)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare used integer; new_id bigint;
begin
  if p_limit is null or p_limit <= 0 then
    return jsonb_build_object('status', 'rejected', 'reason', 'limit_text_ai');
  end if;
  -- candado de transacción (no de sesión): se libera al terminar esta llamada, válido con el pool de PostgREST
  perform pg_advisory_xact_lock(hashtextextended('marketing_text_generations:' || p_tenant::text, 0));
  select count(*) into used from public.marketing_text_generations
   where tenant_id = p_tenant and created_at >= now() - interval '1 day';
  if used >= p_limit then
    return jsonb_build_object('status', 'rejected', 'reason', 'limit_text_ai');
  end if;
  insert into public.marketing_text_generations (tenant_id, content_id, actor_id, model, status)
  values (p_tenant, p_content, p_actor, left(p_model, 100), 'pending')
  returning id into new_id;
  return jsonb_build_object('status', 'reserved', 'id', new_id);
end $$;

revoke all on function public.marketing_reserve_text_generation(uuid, uuid, uuid, text, integer)
  from public, anon, authenticated;
grant execute on function public.marketing_reserve_text_generation(uuid, uuid, uuid, text, integer) to service_role;
