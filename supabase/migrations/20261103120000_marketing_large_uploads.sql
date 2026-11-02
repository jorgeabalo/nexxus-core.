-- AITA Marketing — subida por partes de vídeos grandes a la Biblioteca (bucket S3 de Railway).
--
-- El navegador envía el vídeo en partes de 8 MB al backend, que las pasa al bucket como una subida
-- multipart. Esta tabla guarda el estado de cada subida; solo el servidor (service_role) la usa.
-- La cuota se reserva al empezar (clave 'upload:<media_id>', la misma que la subida normal) y cada
-- parte recibida alarga la reserva: una subida abandonada caduca sola y libera su espacio.
create table if not exists public.marketing_upload_sessions (
  id                uuid primary key default gen_random_uuid(),
  tenant_id         uuid not null references public.tenants(id) on delete restrict,
  media_id          uuid not null unique,
  created_by        uuid not null,
  storage_path      text not null,
  multipart_id      text not null check (length(multipart_id) between 1 and 1024),
  original_filename text not null,
  safe_filename     text not null,
  mime_type         text not null check (mime_type in ('video/mp4','video/quicktime','video/webm')),
  byte_size         bigint not null check (byte_size > 0 and byte_size <= 2147483648),
  part_size         integer not null check (part_size between 5242880 and 67108864),
  parts_done        integer not null default 0 check (parts_done >= 0),
  etags             jsonb not null default '[]'::jsonb,
  reservation_key   text not null,
  status            text not null default 'uploading'
                    check (status in ('uploading','completing','completed','aborted','failed','expired')),
  error             text,
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now(),
  expires_at        timestamptz not null default (now() + interval '24 hours')
);
create index if not exists marketing_upload_sessions_tenant on public.marketing_upload_sessions (tenant_id, status);
create index if not exists marketing_upload_sessions_expiry on public.marketing_upload_sessions (status, expires_at);

alter table public.marketing_upload_sessions enable row level security;
revoke all privileges on public.marketing_upload_sessions from public, anon, authenticated, service_role;
grant select, insert, update on public.marketing_upload_sessions to service_role;

-- Alarga una reserva ABIERTA (nunca reabre una cerrada ni una ya caducada). Máximo 60 minutos por llamada.
create or replace function public.marketing_extend_storage_reservation(p_tenant uuid, p_key text, p_minutes integer)
returns boolean language sql security definer set search_path = '' as $$
  with x as (
    update public.marketing_storage_reservations
       set expires_at = greatest(expires_at, now() + make_interval(mins => least(greatest(coalesce(p_minutes, 15), 1), 60)))
     where tenant_id = p_tenant and reservation_key = p_key and status = 'reserved' and expires_at > now()
    returning 1)
  select exists (select 1 from x) $$;
revoke all on function public.marketing_extend_storage_reservation(uuid, text, integer) from public, anon, authenticated;
grant execute on function public.marketing_extend_storage_reservation(uuid, text, integer) to service_role;
