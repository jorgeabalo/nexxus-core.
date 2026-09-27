-- =====================================================================
-- 5) Trazabilidad call -> lead -> appointment -> payment / alert
--    + coherencia de tenant en cada enlace.
-- =====================================================================
alter table public.payments
  add column if not exists appointment_id uuid references public.appointments(id) on delete set null,
  add column if not exists lead_id        uuid references public.leads(id)        on delete set null,
  add column if not exists call_id        uuid references public.calls(id)        on delete set null;
create index if not exists payments_appointment_idx on public.payments(appointment_id) where appointment_id is not null;
create index if not exists payments_lead_idx        on public.payments(lead_id)        where lead_id is not null;
create index if not exists appointments_call_idx    on public.appointments(call_id)    where call_id is not null;
create index if not exists appointments_lead_idx    on public.appointments(lead_id)    where lead_id is not null;
create index if not exists leads_call_idx           on public.leads(call_id)           where call_id is not null;
create index if not exists calls_lead_idx           on public.calls(lead_id)           where lead_id is not null;
create index if not exists alerts_call_idx          on public.alerts(call_id)          where call_id is not null;
create index if not exists alerts_lead_idx          on public.alerts(lead_id)          where lead_id is not null;

-- Un pago puede venir de un lead (p. ej. clase de prueba) antes de existir
-- el socio. Debe tener al menos uno de los dos.
alter table public.payments alter column member_id drop not null;
alter table public.payments add constraint payments_payer_chk
  check (member_id is not null or lead_id is not null);

-- No se puede enlazar una fila a una llamada / lead / cita de OTRO tenant.
create or replace function public.enforce_same_tenant()
returns trigger language plpgsql set search_path = '' as $$
declare
  col text; ref_table text; ref_id uuid; ref_tenant uuid;
begin
  foreach col in array tg_argv loop
    ref_table := split_part(col, ':', 2);
    execute format('select ($1).%I', split_part(col, ':', 1)) into ref_id using new;
    if ref_id is not null then
      execute format('select tenant_id from public.%I where id = $1', ref_table) into ref_tenant using ref_id;
      if ref_tenant is distinct from new.tenant_id then
        raise exception 'cross-tenant reference %.% -> %', tg_table_name, split_part(col, ':', 1), ref_table
          using errcode = '23514';
      end if;
    end if;
  end loop;
  return new;
end $$;

drop trigger if exists calls_same_tenant on public.calls;
create trigger calls_same_tenant before insert or update on public.calls
  for each row execute function public.enforce_same_tenant('lead_id:leads', 'appointment_id:appointments');
drop trigger if exists leads_same_tenant on public.leads;
create trigger leads_same_tenant before insert or update on public.leads
  for each row execute function public.enforce_same_tenant('call_id:calls');
drop trigger if exists appointments_same_tenant on public.appointments;
create trigger appointments_same_tenant before insert or update on public.appointments
  for each row execute function public.enforce_same_tenant('call_id:calls', 'lead_id:leads', 'rescheduled_from:appointments');
drop trigger if exists payments_same_tenant on public.payments;
create trigger payments_same_tenant before insert or update on public.payments
  for each row execute function public.enforce_same_tenant('appointment_id:appointments', 'lead_id:leads', 'call_id:calls');
drop trigger if exists alerts_same_tenant on public.alerts;
create trigger alerts_same_tenant before insert or update on public.alerts
  for each row execute function public.enforce_same_tenant('call_id:calls', 'lead_id:leads', 'appointment_id:appointments', 'payment_id:payments');

-- payment_overview se recrea para incluir las columnas nuevas de payments
drop view if exists public.payment_overview;
create view public.payment_overview with (security_invoker = true) as
select
  p.*,
  case when p.payment_status = 'pending' and p.due_date < (now() at time zone t.timezone)::date
       then 'overdue' else p.payment_status end as effective_status,
  coalesce(nullif(trim(concat_ws(' ', m.first_name, m.last_name)), ''), l.name) as member_name,
  m.member_id as member_code,
  (select max(p2.payment_date) from public.payments p2
    where p2.member_id = p.member_id and p2.payment_status = 'paid') as member_last_payment
from public.payments p
join public.tenants t on t.id = p.tenant_id
left join public.members m on m.id = p.member_id
left join public.leads   l on l.id = p.lead_id;
revoke all on public.payment_overview from anon;
grant select on public.payment_overview to authenticated;
