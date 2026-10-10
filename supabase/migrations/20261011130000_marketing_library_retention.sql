-- =====================================================================
-- AITA Marketing — Fase 2: retención y eliminación automática de la Biblioteca.
-- Migración NUEVA (después de 20261011120000_marketing_reel_studio.sql). No ejecuta ninguna purga:
-- la purga la hará un worker / tarea programada (services/marketing_retention.py), que en este PR
-- NO está configurado en producción.
--
-- * Ningún archivo se guarda indefinidamente: expires_at es obligatorio y nunca supera la retención
--   máxima del plan (max_retention_days, 7–90; la fija el operador, el tenant no puede cambiarla).
-- * Política unificada: un trabajo activo protege sus archivos como máximo 14 días (después se cancela
--   y se purga). Al publicarse, expires_at = mínimo(published_at + 30 días, subida + 90 días).
--   Máximo absoluto del piloto: 90 días desde la subida. Nada es permanente.
-- * Cuota: originales + derivados + resultados/temporales + reservas pendientes (subidas y trabajos),
--   con reserva atómica (marketing_reserve_storage) para que operaciones simultáneas no la superen.
-- * marketing_stream_tokens: sesión opaca (cookie HttpOnly) para reproducir con HTTP Range.
-- * Al purgar se conservan solo metadatos mínimos de auditoría (id, tenant, hash, tipo, tamaño,
--   quién lo subió, fechas y motivo): sin nombre original, sin metadatos, ruta neutralizada.
-- Idempotente. No borra datos.
-- =====================================================================

alter table public.marketing_settings
  add column if not exists max_retention_days integer not null default 30
      check (max_retention_days between 7 and 90);

alter table public.marketing_media
  add column if not exists retention_days     integer not null default 30 check (retention_days in (7, 30, 60, 90)),
  add column if not exists expires_at         timestamptz,
  add column if not exists retention_status   text not null default 'active'
      check (retention_status in ('active','protected_by_workflow','expiring','purge_pending','purged','purge_failed')),
  add column if not exists protected_until    timestamptz,
  add column if not exists published_at       timestamptz,
  add column if not exists purge_requested_at timestamptz,
  add column if not exists purged_at          timestamptz,
  add column if not exists purge_reason       text check (purge_reason is null or purge_reason in
      ('expired','user_deleted','consent_revoked','publication_done','workflow_timeout')),
  add column if not exists purge_attempts     integer not null default 0 check (purge_attempts between 0 and 100),
  add column if not exists last_purge_error   text check (last_purge_error is null or last_purge_error ~ '^[a-z0-9_]{1,60}$');

-- Nunca retención ilimitada: todo archivo vence, como máximo 90 días después de subirse.
update public.marketing_media set expires_at = created_at + make_interval(days => retention_days) where expires_at is null;
alter table public.marketing_media alter column expires_at set default (now() + interval '30 days');
alter table public.marketing_media alter column expires_at set not null;
alter table public.marketing_media drop constraint if exists marketing_media_retention_check;
alter table public.marketing_media add constraint marketing_media_retention_check check (
  expires_at <= created_at + interval '90 days'
  -- publicado: nunca más allá de published_at + 30 días (y siempre dentro de los 90 de arriba)
  and (published_at is null or expires_at <= published_at + interval '30 days')
  -- protección por un trabajo activo: 14 días como máximo (90 + 14 en el peor caso)
  and (protected_until is null or protected_until <= created_at + interval '104 days')
  and (retention_status <> 'purged' or (purged_at is not null and processing_status = 'deleted'
                                        and original_filename is null and metadata = '{}'::jsonb))
  and (processing_status <> 'deleted' or retention_status = 'purged'));

alter table public.marketing_media_derivatives
  add column if not exists expires_at timestamptz,
  add column if not exists purged_at  timestamptz;
update public.marketing_media_derivatives set expires_at = created_at + interval '7 days' where expires_at is null;
alter table public.marketing_media_derivatives alter column expires_at set default (now() + interval '7 days');
alter table public.marketing_media_derivatives alter column expires_at set not null;
alter table public.marketing_media_derivatives drop constraint if exists marketing_media_derivatives_expiry_check;
alter table public.marketing_media_derivatives add constraint marketing_media_derivatives_expiry_check
  check (expires_at <= created_at + interval '90 days' and (not is_mock or expires_at <= created_at + interval '7 days'));

alter table public.marketing_generation_outputs
  add column if not exists expires_at timestamptz,
  add column if not exists purged_at  timestamptz,
  add column if not exists byte_size  bigint check (byte_size is null or byte_size >= 0);
alter table public.marketing_generation_jobs
  add column if not exists reserved_storage_bytes bigint not null default 0 check (reserved_storage_bytes >= 0);

-- El original no se sobrescribe. La purga (y solo ella) neutraliza la ruta y borra nombre y metadatos.
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

-- ---------- cuota de almacenamiento: todo cuenta, y lo pendiente se reserva ----------
create table if not exists public.marketing_storage_reservations (
  id              uuid primary key default gen_random_uuid(),
  tenant_id       uuid not null references public.tenants(id) on delete restrict,
  reservation_key text not null check (length(reservation_key) between 8 and 200),
  kind            text not null check (kind in ('upload','derivative','generation')),
  bytes           bigint not null check (bytes > 0),
  status          text not null default 'reserved' check (status in ('reserved','consumed','released','expired')),
  expires_at      timestamptz not null default (now() + interval '15 minutes'),
  created_at      timestamptz not null default now(),
  unique (tenant_id, reservation_key)
);
-- Una reserva cerrada (consumed/released/expired) es definitiva: no se reabre ni libera bytes otra vez.
create or replace function private.marketing_reservation_guard()
returns trigger language plpgsql set search_path = '' as $$
begin
  if old.status <> 'reserved' then
    raise exception 'storage reservation already closed' using errcode = '23514';
  end if;
  if new.tenant_id <> old.tenant_id or new.reservation_key <> old.reservation_key or new.kind <> old.kind then
    raise exception 'storage reservation identity is immutable' using errcode = '23514';
  end if;
  return new;
end $$;
revoke all on function private.marketing_reservation_guard() from public, anon, authenticated;
drop trigger if exists marketing_reservation_guard on public.marketing_storage_reservations;
create trigger marketing_reservation_guard before update on public.marketing_storage_reservations
  for each row execute function private.marketing_reservation_guard();

create or replace function public.marketing_storage_used(p_tenant uuid)
returns bigint language sql stable security definer set search_path = '' as $$
  select (coalesce((select sum(byte_size) from public.marketing_media
                    where tenant_id = p_tenant and processing_status <> 'deleted'), 0)
       + coalesce((select sum(byte_size) from public.marketing_media_derivatives
                    where tenant_id = p_tenant and status <> 'deleted'), 0)
       + coalesce((select sum(byte_size) from public.marketing_generation_outputs
                    where tenant_id = p_tenant and purged_at is null), 0)
       + coalesce((select sum(r.bytes) from public.marketing_storage_reservations r   -- subidas y trabajos pendientes
                    where r.tenant_id = p_tenant and r.status = 'reserved' and r.expires_at > now()
                      -- una subida ya registrada cuenta por su archivo, no dos veces
                      and not exists (select 1 from public.marketing_media m where m.tenant_id = p_tenant
                                        and r.reservation_key = 'upload:' || m.id::text)), 0)
       )::bigint $$;

-- Reserva atómica (bloquea la configuración del tenant). Idempotente por clave.
create or replace function public.marketing_reserve_storage(p_tenant uuid, p_key text, p_kind text, p_bytes bigint)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare s public.marketing_settings; used bigint; existing public.marketing_storage_reservations;
begin
  if p_bytes is null or p_bytes <= 0 then
    return jsonb_build_object('status', 'rejected', 'reason', 'invalid_size');
  end if;
  select * into s from public.marketing_settings where tenant_id = p_tenant for update;
  if not found or s.library_storage_limit_bytes = 0 then
    return jsonb_build_object('status', 'rejected', 'reason', 'library_disabled');
  end if;
  select * into existing from public.marketing_storage_reservations where tenant_id = p_tenant and reservation_key = p_key;
  if found then
    return jsonb_build_object('status', 'duplicate', 'reservation_status', existing.status);
  end if;
  used := public.marketing_storage_used(p_tenant);
  if s.library_storage_limit_bytes is not null and used + p_bytes > s.library_storage_limit_bytes then
    return jsonb_build_object('status', 'rejected', 'reason', 'limit_library_storage',
                              'available', greatest(s.library_storage_limit_bytes - used, 0));
  end if;
  insert into public.marketing_storage_reservations (tenant_id, reservation_key, kind, bytes)
  values (p_tenant, p_key, p_kind, p_bytes);
  return jsonb_build_object('status', 'reserved');
end $$;

-- Liberación idempotente: solo una reserva 'reserved' cambia; una expirada o ya liberada no libera dos veces.
create or replace function public.marketing_release_storage(p_tenant uuid, p_key text, p_consumed boolean)
returns jsonb language sql security definer set search_path = '' as $$
  update public.marketing_storage_reservations set status = case when p_consumed then 'consumed' else 'released' end
   where tenant_id = p_tenant and reservation_key = p_key and status = 'reserved' and expires_at > now()
  returning jsonb_build_object('status', status) $$;

-- Reservas abandonadas (subida interrumpida, trabajo atascado): pasan a 'expired' una sola vez.
create or replace function public.marketing_expire_storage_reservations()
returns integer language sql security definer set search_path = '' as $$
  with x as (update public.marketing_storage_reservations set status = 'expired'
              where status = 'reserved' and expires_at <= now() returning 1)
  select count(*)::int from x $$;

-- Cuando un trabajo termina (completado, cancelado, fallido, timeout, consentimiento retirado), su
-- reserva de espacio se cierra una sola vez: el resultado real ya cuenta por sí mismo.
create or replace function private.marketing_job_storage_release()
returns trigger language plpgsql security definer set search_path = '' as $$
begin
  if new.status in ('succeeded','failed','cancelled') and old.status is distinct from new.status then
    update public.marketing_storage_reservations
       set status = case when new.status = 'succeeded' then 'consumed' else 'released' end
     where tenant_id = new.tenant_id and reservation_key = 'job:' || new.id::text and status = 'reserved';
  end if;
  return new;
end $$;
revoke all on function private.marketing_job_storage_release() from public, anon, authenticated;
drop trigger if exists marketing_job_storage_release on public.marketing_generation_jobs;
create trigger marketing_job_storage_release after update on public.marketing_generation_jobs
  for each row execute function private.marketing_job_storage_release();

-- Antes de registrar un resultado: ¿cabe su tamaño REAL en lo reservado + lo disponible?
create or replace function public.marketing_confirm_output_storage(p_tenant uuid, p_job uuid, p_bytes bigint)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare s public.marketing_settings; held bigint; used bigint;
begin
  if p_bytes is null or p_bytes <= 0 then
    return jsonb_build_object('status', 'rejected', 'reason', 'invalid_size');
  end if;
  select * into s from public.marketing_settings where tenant_id = p_tenant for update;
  if not found then
    return jsonb_build_object('status', 'rejected', 'reason', 'library_disabled');
  end if;
  select bytes into held from public.marketing_storage_reservations
   where tenant_id = p_tenant and reservation_key = 'job:' || p_job::text and status = 'reserved' and expires_at > now()
   for update;
  if not found then
    return jsonb_build_object('status', 'rejected', 'reason', 'reservation_expired');
  end if;
  used := public.marketing_storage_used(p_tenant);                       -- ya incluye esta reserva
  if s.library_storage_limit_bytes is not null and used - held + p_bytes > s.library_storage_limit_bytes then
    return jsonb_build_object('status', 'rejected', 'reason', 'storage_quota_exceeded');
  end if;
  -- la reserva pasa a ser el tamaño real: el espacio queda retenido hasta que el trabajo termine
  update public.marketing_storage_reservations set bytes = p_bytes
   where tenant_id = p_tenant and reservation_key = 'job:' || p_job::text and status = 'reserved';
  return jsonb_build_object('status', 'ok', 'reserved', held, 'bytes', p_bytes);
end $$;

-- ---------- sesión de reproducción (cookie HttpOnly; HTTP Range desde <video>) ----------
-- Se guarda solo el hash. Ligada a tenant, usuario y archivo; dura como máximo 10 minutos; revocable.
create table if not exists public.marketing_stream_tokens (
  id            uuid primary key default gen_random_uuid(),
  tenant_id     uuid not null references public.tenants(id) on delete restrict,
  user_id       uuid not null,
  media_id      uuid not null,
  derivative_id uuid,
  token_hash    text not null unique check (token_hash ~ '^[0-9a-f]{64}$'),
  expires_at    timestamptz not null check (expires_at <= created_at + interval '10 minutes'),
  revoked_at    timestamptz,
  created_at    timestamptz not null default now(),
  constraint marketing_stream_tokens_media_fk foreign key (media_id, tenant_id)
    references public.marketing_media(id, tenant_id) on delete restrict
);
create index if not exists marketing_stream_tokens_media_idx on public.marketing_stream_tokens(tenant_id, media_id);

-- Solo el backend: ni el navegador ni authenticated leen tokens o reservas.
revoke all on function public.marketing_storage_used(uuid), public.marketing_reserve_storage(uuid, text, text, bigint),
  public.marketing_release_storage(uuid, text, boolean), public.marketing_expire_storage_reservations(),
  public.marketing_confirm_output_storage(uuid, uuid, bigint) from public, anon, authenticated;
grant execute on function public.marketing_storage_used(uuid), public.marketing_reserve_storage(uuid, text, text, bigint),
  public.marketing_release_storage(uuid, text, boolean), public.marketing_expire_storage_reservations(),
  public.marketing_confirm_output_storage(uuid, uuid, bigint) to service_role;
do $$
declare t text;
begin
  foreach t in array array['marketing_storage_reservations','marketing_stream_tokens'] loop
    execute format('alter table public.%I enable row level security', t);
    execute format('revoke all privileges on public.%I from public, anon, authenticated, service_role', t);
    execute format('grant select, insert, update on public.%I to service_role', t);
  end loop;
end $$;
