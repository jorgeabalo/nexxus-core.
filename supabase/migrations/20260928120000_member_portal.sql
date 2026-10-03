-- =====================================================================
-- 7) Member Panel (portal del socio)
--    * members.user_id vincula al socio con su usuario de Supabase Auth.
--    * El enlace/QR personal NO se guarda: se deriva con HMAC en el
--      servidor a partir de (member_id, portal_token_version). Regenerar el
--      QR = subir la versión; el enlace anterior deja de servir al instante.
--    * Un socio solo puede LEER sus propias filas (políticas extra, que se
--      suman a las del staff). Toda escritura del socio pasa por funciones
--      que validan qué puede cambiar.
-- =====================================================================

alter table public.members
  add column if not exists user_id               uuid unique references auth.users(id) on delete set null,
  add column if not exists portal_token_version  integer not null default 1,
  add column if not exists portal_invited_at     timestamptz,
  add column if not exists portal_activated_at   timestamptz,
  add column if not exists portal_last_used_at   timestamptz;

-- ---------- helpers del socio (esquema privado, no expuesto por la API) ----------
create or replace function private.my_member_ids()
returns setof uuid language sql stable security definer set search_path = '' as $$
  select m.id from public.members m where m.user_id = (select auth.uid())
$$;
create or replace function private.my_member_tenant_ids()
returns setof uuid language sql stable security definer set search_path = '' as $$
  select m.tenant_id from public.members m where m.user_id = (select auth.uid())
$$;
revoke all on function private.my_member_ids()        from public, anon;
revoke all on function private.my_member_tenant_ids() from public, anon;
grant execute on function private.my_member_ids()        to authenticated;
grant execute on function private.my_member_tenant_ids() to authenticated;

-- ---------- lecturas propias del socio (se suman a las políticas del staff) ----------
create policy members_self_select on public.members for select to authenticated
  using (id in (select private.my_member_ids()));
create policy appointments_self_select on public.appointments for select to authenticated
  using (member_id in (select private.my_member_ids()));
create policy payments_self_select on public.payments for select to authenticated
  using (member_id in (select private.my_member_ids()));
create policy check_ins_self_select on public.check_ins for select to authenticated
  using (member_id in (select private.my_member_ids()));
create policy measurements_self_select on public.measurements for select to authenticated
  using (member_id in (select private.my_member_ids()));
create policy services_member_select on public.services for select to authenticated
  using (active and tenant_id in (select private.my_member_tenant_ids()));
create policy tenants_member_select on public.tenants for select to authenticated
  using (id in (select private.my_member_tenant_ids()));

-- ---------- vista agregada del portal ----------
create or replace function public.member_portal()
returns jsonb language plpgsql stable security definer set search_path = '' as $$
declare
  m public.members%rowtype; t public.tenants%rowtype; d date; m0 timestamptz;
begin
  select * into m from public.members where user_id = (select auth.uid()) limit 1;
  if m.id is null then
    raise exception 'not a member' using errcode = '42501';
  end if;
  select * into t from public.tenants where id = m.tenant_id;
  d  := (now() at time zone t.timezone)::date;
  m0 := date_trunc('month', d::timestamp) at time zone t.timezone;

  return jsonb_build_object(
    'tenant', jsonb_build_object('name', t.name, 'timezone', t.timezone, 'branding', t.branding,
                                 'phone', t.settings->>'public_phone', 'address', t.settings->>'address'),
    'today', d,
    'member', jsonb_build_object(
      'id', m.id, 'member_code', m.member_id, 'first_name', m.first_name, 'last_name', m.last_name,
      'email', m.email, 'phone', m.phone,
      'emergency_contact_name', m.emergency_contact_name, 'emergency_contact_phone', m.emergency_contact_phone,
      'membership_type', m.membership_type, 'membership_status', lower(coalesce(m.membership_status, 'active')),
      'start_date', m.start_date, 'next_payment_date', m.next_payment_date),
    'appointments', coalesce((
      select jsonb_agg(jsonb_build_object(
        'id', a.id, 'date', a.appointment_date, 'start_time', a.start_time, 'end_time', a.end_time,
        'service', a.service, 'status', a.status, 'source', a.source, 'notes', a.notes,
        'staff_first_name', s.first_name,
        'can_cancel', a.status in ('scheduled','confirmed') and (a.appointment_date + a.start_time) > (now() at time zone t.timezone))
        order by a.appointment_date desc, a.start_time desc)
      from public.appointments a left join public.staff s on s.id = a.staff_id
      where a.member_id = m.id and a.appointment_date >= d - 180), '[]'::jsonb),
    'payments', coalesce((
      select jsonb_agg(jsonb_build_object(
        'id', p.id, 'amount', p.amount, 'due_date', p.due_date, 'payment_date', p.payment_date,
        'status', case when p.payment_status = 'pending' and p.due_date < d then 'overdue' else p.payment_status end,
        'method', p.payment_method, 'description', p.description)
        order by coalesce(p.payment_date, p.due_date::timestamptz, p.created_at) desc)
      from public.payments p where p.member_id = m.id), '[]'::jsonb),
    'visits', jsonb_build_object(
      'this_month', (select count(*) from public.check_ins c where c.member_id = m.id and c.check_in_time >= m0),
      'last_visit', (select max(c.check_in_time) from public.check_ins c where c.member_id = m.id),
      'recent', coalesce((select jsonb_agg(x.check_in_time order by x.check_in_time desc)
                          from (select c.check_in_time from public.check_ins c where c.member_id = m.id
                                order by c.check_in_time desc limit 20) x), '[]'::jsonb)),
    'services', coalesce((
      select jsonb_agg(jsonb_build_object('name', sv.name, 'duration_minutes', sv.duration_minutes) order by sv.name)
      from public.services sv where sv.tenant_id = m.tenant_id and sv.active), '[]'::jsonb)
  );
end $$;

-- ---------- acciones del socio ----------
create or replace function public.member_request_appointment(p_service text, p_date date, p_time time, p_notes text default null)
returns uuid language plpgsql volatile security definer set search_path = '' as $$
declare
  m public.members%rowtype; tz text; dur int; new_id uuid; pending int;
begin
  select * into m from public.members where user_id = (select auth.uid()) limit 1;
  if m.id is null then raise exception 'not a member' using errcode = '42501'; end if;
  if lower(coalesce(m.membership_status, 'active')) not in ('active') then
    raise exception 'membership not active' using errcode = '42501';
  end if;
  select timezone into tz from public.tenants where id = m.tenant_id;

  select duration_minutes into dur from public.services
   where tenant_id = m.tenant_id and active and name = p_service;
  if not found then raise exception 'unknown service' using errcode = '22023'; end if;
  if p_date is null or p_time is null or (p_date + p_time) <= (now() at time zone tz) then
    raise exception 'date must be in the future' using errcode = '22023';
  end if;
  if p_date > (now() at time zone tz)::date + 90 then
    raise exception 'date too far ahead' using errcode = '22023';
  end if;
  select count(*) into pending from public.appointments
   where member_id = m.id and status = 'scheduled' and source = 'member_portal'
     and (appointment_date + start_time) > (now() at time zone tz);
  if pending >= 3 then raise exception 'too many pending requests' using errcode = '22023'; end if;

  insert into public.appointments (tenant_id, member_id, service, appointment_date, start_time, end_time,
                                   status, source, notes)
  values (m.tenant_id, m.id, p_service, p_date, p_time,
          case when dur is not null then (p_time + make_interval(mins => dur))::time end,
          'scheduled', 'member_portal', nullif(left(trim(coalesce(p_notes, '')), 500), ''))
  returning id into new_id;

  insert into public.alerts (tenant_id, type, severity, title, detail, member_id, appointment_id)
  values (m.tenant_id, 'appointment_confirmation', 'warning',
          'Appointment request: ' || trim(concat_ws(' ', m.first_name, m.last_name)),
          concat_ws(' · ', p_service, to_char(p_date, 'Mon DD'), to_char(p_time, 'HH12:MI AM'), 'Member Portal'),
          m.id, new_id);
  return new_id;
end $$;

create or replace function public.member_cancel_appointment(p_appointment uuid)
returns void language plpgsql volatile security definer set search_path = '' as $$
declare m public.members%rowtype; tz text; n int;
begin
  select * into m from public.members where user_id = (select auth.uid()) limit 1;
  if m.id is null then raise exception 'not a member' using errcode = '42501'; end if;
  select timezone into tz from public.tenants where id = m.tenant_id;
  update public.appointments
     set status = 'cancelled', cancelled_at = now(), cancel_reason = 'Cancelled by member (portal)'
   where id = p_appointment and member_id = m.id and status in ('scheduled','confirmed')
     and (appointment_date + start_time) > (now() at time zone tz);
  get diagnostics n = row_count;
  if n = 0 then raise exception 'appointment cannot be cancelled' using errcode = '22023'; end if;
  update public.alerts set status = 'resolved', resolved_at = now()
   where appointment_id = p_appointment and status = 'open';
end $$;

create or replace function public.member_update_contact(p_phone text, p_email text,
                                                        p_emergency_name text, p_emergency_phone text)
returns void language plpgsql volatile security definer set search_path = '' as $$
declare uid uuid := (select auth.uid()); n int;
begin
  if p_email is not null and p_email <> '' and p_email !~* '^[^@\s]+@[^@\s]+\.[^@\s]+$' then
    raise exception 'invalid email' using errcode = '22023';
  end if;
  update public.members set
    phone                   = nullif(left(trim(coalesce(p_phone, '')), 30), ''),
    email                   = nullif(lower(left(trim(coalesce(p_email, '')), 200)), ''),
    emergency_contact_name  = nullif(left(trim(coalesce(p_emergency_name, '')), 120), ''),
    emergency_contact_phone = nullif(left(trim(coalesce(p_emergency_phone, '')), 30), ''),
    updated_at              = now()
  where user_id = uid;
  get diagnostics n = row_count;
  if n = 0 then raise exception 'not a member' using errcode = '42501'; end if;
end $$;

revoke all on function public.member_portal()                                   from public, anon;
revoke all on function public.member_request_appointment(text, date, time, text) from public, anon;
revoke all on function public.member_cancel_appointment(uuid)                    from public, anon;
revoke all on function public.member_update_contact(text, text, text, text)      from public, anon;
grant execute on function public.member_portal()                                   to authenticated;
grant execute on function public.member_request_appointment(text, date, time, text) to authenticated;
grant execute on function public.member_cancel_appointment(uuid)                    to authenticated;
grant execute on function public.member_update_contact(text, text, text, text)      to authenticated;

-- Datos públicos de contacto del gym para el portal (no sensibles)
update public.tenants
   set settings = settings || '{"public_phone":"281-352-4784","address":"1914 Gessner Rd, Houston, TX"}'::jsonb
 where slug = 'golden_age' and not (settings ? 'public_phone');

-- Las RPC del Manager Panel exigen ser staff del tenant (tenant_users).
-- Un socio no puede llamarlas aunque pertenezca al mismo tenant.
do $$
declare f text;
begin
  for f in select pg_get_functiondef(p.oid) from pg_proc p join pg_namespace n on n.oid = p.pronamespace
           where n.nspname = 'public' and p.proname in ('manager_dashboard','manager_activity','manager_alerts')
  loop
    if position('private.user_tenant_ids' in f) = 0 then
      execute replace(f,
        'select timezone into tz from public.tenants where id = p_tenant;',
        'if p_tenant not in (select private.user_tenant_ids()) then raise exception ''tenant not accessible'' using errcode = ''42501''; end if;
  select timezone into tz from public.tenants where id = p_tenant;');
    end if;
  end loop;
end $$;
