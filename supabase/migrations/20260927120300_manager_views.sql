-- =====================================================================
-- 4) Vistas y funciones de lectura del Manager Panel.
--    Todas son SECURITY INVOKER: se ejecutan con los permisos del usuario
--    que consulta, así que RLS se aplica siempre. Nada se inventa: si no
--    hay filas, los contadores dan 0 y las listas vienen vacías.
--    "Hoy" se calcula en la zona horaria del tenant (tenants.timezone).
-- =====================================================================

-- ---------- estado de pago efectivo de un pago ----------
-- pending + due_date vencida  => overdue (se calcula, no se guarda)

create or replace view public.member_overview with (security_invoker = true) as
select
  m.id, m.tenant_id, m.member_id as member_code,
  m.first_name, m.last_name,
  trim(concat_ws(' ', m.first_name, m.last_name)) as full_name,
  m.email, m.phone, m.membership_type,
  lower(coalesce(m.membership_status, 'active'))  as membership_status,
  m.start_date, m.next_payment_date, m.notes, m.created_at,
  lv.last_visit,
  coalesce(vm.visits_this_month, 0)               as visits_this_month,
  na.next_appointment_date, na.next_appointment_time, na.next_appointment_service,
  pay.payment_status,
  (m.start_date is not null and m.start_date >= (now() at time zone t.timezone)::date - 30) as is_new,
  (pay.payment_status = 'overdue')                as is_past_due
from public.members m
join public.tenants t on t.id = m.tenant_id
left join lateral (
  select max(c.check_in_time) as last_visit
  from public.check_ins c where c.member_id = m.id
) lv on true
left join lateral (
  select count(*) as visits_this_month
  from public.check_ins c
  where c.member_id = m.id
    and c.check_in_time >= (date_trunc('month', now() at time zone t.timezone) at time zone t.timezone)
) vm on true
left join lateral (
  select a.appointment_date, a.start_time, a.service
  from public.appointments a
  where a.member_id = m.id and a.status in ('scheduled','confirmed')
    and (a.appointment_date + a.start_time) >= (now() at time zone t.timezone)
  order by a.appointment_date, a.start_time
  limit 1
) na(next_appointment_date, next_appointment_time, next_appointment_service) on true
left join lateral (
  select case
    when exists (select 1 from public.payments p where p.member_id = m.id and p.payment_status = 'pending'
                 and p.due_date < (now() at time zone t.timezone)::date) then 'overdue'
    when exists (select 1 from public.payments p where p.member_id = m.id and p.payment_status = 'pending') then 'pending'
    when exists (select 1 from public.payments p where p.member_id = m.id and p.payment_status = 'paid') then 'paid'
    else 'none'
  end as payment_status
) pay on true;

create or replace view public.appointment_overview with (security_invoker = true) as
select
  a.*,
  coalesce(nullif(trim(concat_ws(' ', m.first_name, m.last_name)), ''), a.client_name) as client_display,
  coalesce(m.phone, a.client_phone) as client_phone_display,
  m.member_id as member_code,
  nullif(trim(concat_ws(' ', s.first_name, s.last_name)), '') as staff_name
from public.appointments a
left join public.members m on m.id = a.member_id
left join public.staff   s on s.id = a.staff_id;

create or replace view public.payment_overview with (security_invoker = true) as
select
  p.*,
  case when p.payment_status = 'pending' and p.due_date < (now() at time zone t.timezone)::date
       then 'overdue' else p.payment_status end as effective_status,
  trim(concat_ws(' ', m.first_name, m.last_name)) as member_name,
  m.member_id as member_code,
  (select max(p2.payment_date) from public.payments p2
    where p2.member_id = p.member_id and p2.payment_status = 'paid') as member_last_payment
from public.payments p
join public.tenants t on t.id = p.tenant_id
left join public.members m on m.id = p.member_id;

create or replace view public.call_overview with (security_invoker = true) as
select
  c.*,
  coalesce(c.caller_name, l.name, nullif(trim(concat_ws(' ', m.first_name, m.last_name)), '')) as caller_display,
  l.name as lead_name, l.email as lead_email, l.reason as lead_reason, l.status as lead_status
from public.calls c
left join public.leads   l on l.id = c.lead_id
left join public.members m on m.id = c.member_id;

-- ---------- KPIs del Dashboard ----------
create or replace function public.manager_dashboard(p_tenant uuid)
returns jsonb language plpgsql stable security invoker set search_path = '' as $$
declare
  tz text; d date; t0 timestamptz; t1 timestamptz; m0 timestamptz;
begin
  select timezone into tz from public.tenants where id = p_tenant;   -- RLS: null si no es tu tenant
  if tz is null then raise exception 'tenant not accessible' using errcode = '42501'; end if;

  d  := (now() at time zone tz)::date;
  t0 := d::timestamp at time zone tz;
  t1 := (d + 1)::timestamp at time zone tz;
  m0 := date_trunc('month', d::timestamp) at time zone tz;

  return jsonb_build_object(
    'date', d,
    'timezone', tz,
    'kpis', jsonb_build_object(
      'members_today',      (select count(distinct member_id) from public.check_ins
                              where tenant_id = p_tenant and check_in_time >= t0 and check_in_time < t1),
      'appointments_today', (select count(*) from public.appointments
                              where tenant_id = p_tenant and appointment_date = d
                                and status not in ('cancelled','rescheduled')),
      'calls_today',        (select count(*) from public.calls
                              where tenant_id = p_tenant and started_at >= t0 and started_at < t1),
      'new_leads',          (select count(*) from public.leads
                              where tenant_id = p_tenant and created_at >= t0 and created_at < t1),
      'payments_today',     (select jsonb_build_object('count', count(*), 'amount', coalesce(sum(amount), 0))
                              from public.payments where tenant_id = p_tenant and payment_status = 'paid'
                                and payment_date >= t0 and payment_date < t1),
      'pending_payments',   (select jsonb_build_object('count', count(*), 'amount', coalesce(sum(amount), 0))
                              from public.payments where tenant_id = p_tenant and payment_status = 'pending')
    ),
    'claudia', jsonb_build_object(
      'calls_answered',         (select count(*) from public.calls where tenant_id = p_tenant and handled_by = 'claudia'
                                   and started_at >= t0 and started_at < t1 and status in ('in_progress','completed')),
      'avg_duration_seconds',   (select round(avg(duration_seconds)) from public.calls where tenant_id = p_tenant
                                   and started_at >= t0 and started_at < t1 and duration_seconds is not null),
      'new_leads',              (select count(*) from public.leads where tenant_id = p_tenant and source = 'claudia'
                                   and created_at >= t0 and created_at < t1),
      'appointments_generated', (select count(*) from public.appointments where tenant_id = p_tenant and source = 'claudia'
                                   and created_at >= t0 and created_at < t1),
      'transfers',              (select count(*) from public.calls where tenant_id = p_tenant and transferred
                                   and started_at >= t0 and started_at < t1),
      'follow_ups_required',    (select count(*) from public.calls c where c.tenant_id = p_tenant and c.follow_up_required
                                   and not exists (select 1 from public.leads l where l.id = c.lead_id and l.status <> 'new'))
    ),
    'payments', jsonb_build_object(
      'paid_month',  (select jsonb_build_object('count', count(*), 'amount', coalesce(sum(amount), 0))
                       from public.payments where tenant_id = p_tenant and payment_status = 'paid' and payment_date >= m0),
      'pending',     (select jsonb_build_object('count', count(*), 'amount', coalesce(sum(amount), 0))
                       from public.payments where tenant_id = p_tenant and payment_status = 'pending'
                         and (due_date is null or due_date >= d)),
      'overdue',     (select jsonb_build_object('count', count(*), 'amount', coalesce(sum(amount), 0))
                       from public.payments where tenant_id = p_tenant and payment_status = 'pending' and due_date < d)
    )
  );
end $$;

-- ---------- Actividad de hoy (timeline) ----------
create or replace function public.manager_activity(p_tenant uuid, p_limit int default 50)
returns table (occurred_at timestamptz, kind text, title text, detail text, ref_id uuid)
language plpgsql stable security invoker set search_path = '' as $$
declare tz text; d date; t0 timestamptz; t1 timestamptz;
begin
  select timezone into tz from public.tenants where id = p_tenant;
  if tz is null then raise exception 'tenant not accessible' using errcode = '42501'; end if;
  d := (now() at time zone tz)::date; t0 := d::timestamp at time zone tz; t1 := (d + 1)::timestamp at time zone tz;

  return query
  select * from (
    select c.check_in_time, 'check_in'::text,
           trim(concat_ws(' ', m.first_name, m.last_name)), 'Check-in'::text, c.id
      from public.check_ins c join public.members m on m.id = c.member_id
     where c.tenant_id = p_tenant and c.check_in_time >= t0 and c.check_in_time < t1
    union all
    select a.created_at, 'appointment'::text,
           coalesce(nullif(trim(concat_ws(' ', m.first_name, m.last_name)), ''), a.client_name),
           concat_ws(' · ', a.service, to_char(a.appointment_date, 'Mon DD'), to_char(a.start_time, 'HH12:MI AM'), a.source), a.id
      from public.appointments a left join public.members m on m.id = a.member_id
     where a.tenant_id = p_tenant and a.created_at >= t0 and a.created_at < t1
    union all
    select cl.started_at, 'call'::text,
           coalesce(cl.caller_name, cl.caller_phone, 'Unknown caller'),
           coalesce(cl.intent, cl.status), cl.id
      from public.calls cl
     where cl.tenant_id = p_tenant and cl.started_at >= t0 and cl.started_at < t1
    union all
    select p.payment_date, 'payment'::text,
           trim(concat_ws(' ', m.first_name, m.last_name)),
           concat('$', p.amount::text, coalesce(' · ' || p.payment_method, '')), p.id
      from public.payments p left join public.members m on m.id = p.member_id
     where p.tenant_id = p_tenant and p.payment_status = 'paid' and p.payment_date >= t0 and p.payment_date < t1
    union all
    select m.created_at, 'new_member'::text,
           trim(concat_ws(' ', m.first_name, m.last_name)), coalesce(m.membership_type, 'New member'), m.id
      from public.members m
     where m.tenant_id = p_tenant and m.created_at >= t0 and m.created_at < t1
    union all
    select l.created_at, 'lead'::text, coalesce(l.name, l.phone, 'New lead'), l.reason, l.id
      from public.leads l
     where l.tenant_id = p_tenant and l.created_at >= t0 and l.created_at < t1
    union all
    select al.created_at, 'alert'::text, al.title, al.detail, al.id
      from public.alerts al
     where al.tenant_id = p_tenant and al.created_at >= t0 and al.created_at < t1
  ) x
  order by 1 desc
  limit greatest(1, least(p_limit, 200));
end $$;

-- ---------- Alertas / acción requerida ----------
-- Guardadas (alerts.status = open) + derivadas en vivo de datos reales.
create or replace function public.manager_alerts(p_tenant uuid)
returns table (alert_id uuid, kind text, severity text, title text, detail text,
               ref_table text, ref_id uuid, created_at timestamptz, stored boolean)
language plpgsql stable security invoker set search_path = '' as $$
declare tz text; d date; tracks_checkins boolean;
begin
  select timezone into tz from public.tenants where id = p_tenant;
  if tz is null then raise exception 'tenant not accessible' using errcode = '42501'; end if;
  d := (now() at time zone tz)::date;
  -- "socio inactivo" solo tiene sentido si el gym ya registra check-ins;
  -- si no hay ninguno, no se marca a todos como inactivos.
  tracks_checkins := exists (select 1 from public.check_ins where tenant_id = p_tenant);

  return query
  select * from (
    select al.id, al.type, al.severity, al.title, al.detail,
           case when al.lead_id is not null then 'leads' when al.call_id is not null then 'calls'
                when al.appointment_id is not null then 'appointments' when al.payment_id is not null then 'payments'
                when al.member_id is not null then 'members' end,
           coalesce(al.lead_id, al.call_id, al.appointment_id, al.payment_id, al.member_id),
           al.created_at, true
      from public.alerts al
     where al.tenant_id = p_tenant and al.status = 'open'
    union all
    select null::uuid, 'payment_overdue', 'critical',
           'Payment overdue: ' || coalesce(trim(concat_ws(' ', m.first_name, m.last_name)), 'member'),
           concat('$', p.amount::text, ' due ', to_char(p.due_date, 'Mon DD')),
           'payments', p.id, p.due_date::timestamptz, false
      from public.payments p left join public.members m on m.id = p.member_id
     where p.tenant_id = p_tenant and p.payment_status = 'pending' and p.due_date < d
    union all
    select null::uuid, 'appointment_confirmation', 'warning',
           'Confirm appointment: ' || coalesce(nullif(trim(concat_ws(' ', m.first_name, m.last_name)), ''), a.client_name),
           concat_ws(' · ', a.service, to_char(a.appointment_date, 'Mon DD'), to_char(a.start_time, 'HH12:MI AM')),
           'appointments', a.id, a.created_at, false
      from public.appointments a left join public.members m on m.id = a.member_id
     where a.tenant_id = p_tenant and a.status = 'scheduled' and a.appointment_date between d and d + 1
    union all
    select null::uuid, 'member_inactive', 'info',
           'Inactive member: ' || trim(concat_ws(' ', m.first_name, m.last_name)),
           coalesce('Last visit ' || to_char(lv.last_visit at time zone tz, 'Mon DD'), 'No visits recorded'),
           'members', m.id, coalesce(lv.last_visit, m.created_at), false
      from public.members m
      left join lateral (select max(c.check_in_time) as last_visit from public.check_ins c where c.member_id = m.id) lv on true
     where tracks_checkins and m.tenant_id = p_tenant
       and lower(coalesce(m.membership_status, 'active')) = 'active'
       and coalesce(lv.last_visit, m.start_date::timestamptz) < (d - 14)::timestamp at time zone tz
  ) x
  order by case x.severity when 'critical' then 0 when 'warning' then 1 else 2 end, 8 desc;
end $$;

revoke all on function public.manager_dashboard(uuid)     from public, anon;
revoke all on function public.manager_activity(uuid, int) from public, anon;
revoke all on function public.manager_alerts(uuid)        from public, anon;
grant execute on function public.manager_dashboard(uuid)     to authenticated;
grant execute on function public.manager_activity(uuid, int) to authenticated;
grant execute on function public.manager_alerts(uuid)        to authenticated;

revoke all on public.member_overview, public.appointment_overview, public.payment_overview, public.call_overview from anon;
grant select on public.member_overview, public.appointment_overview, public.payment_overview, public.call_overview to authenticated;
