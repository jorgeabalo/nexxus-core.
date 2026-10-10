-- =====================================================================
-- AITA Marketing — Fase 2: retención y eliminación automática de la Biblioteca.
-- Migración NUEVA (después de 20261011120000_marketing_reel_studio.sql). No ejecuta ninguna purga:
-- la purga la hará un worker / tarea programada (services/marketing_retention.py), que en este PR
-- NO está configurado en producción.
--
-- * Ningún archivo se guarda indefinidamente: expires_at es obligatorio y nunca supera la retención
--   máxima del plan (max_retention_days, 7–90; la fija el operador, el tenant no puede cambiarla).
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
  and (protected_until is null or protected_until <= created_at + interval '104 days')   -- 90 + 14 de seguridad
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
  add column if not exists purged_at  timestamptz;

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
  if new.expires_at > old.created_at + make_interval(days => coalesce((select s.max_retention_days
       from public.marketing_settings s where s.tenant_id = new.tenant_id), 30)) and new.expires_at > old.expires_at then
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
