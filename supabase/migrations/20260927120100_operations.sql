-- =====================================================================
-- 2) Operación: extensión de appointments / payments y tablas nuevas
--    calls (Claudia), leads, alerts.
-- =====================================================================

-- ---------- appointments ----------
alter table public.appointments
  add column if not exists source           text not null default 'manager',
  add column if not exists client_name      text,          -- para no-socios (leads)
  add column if not exists client_phone     text,
  add column if not exists call_id          uuid,
  add column if not exists lead_id          uuid,
  add column if not exists confirmed_at     timestamptz,
  add column if not exists cancelled_at     timestamptz,
  add column if not exists cancel_reason    text,
  add column if not exists rescheduled_from uuid references public.appointments(id) on delete set null,
  add column if not exists created_by       uuid references auth.users(id) on delete set null,
  add column if not exists updated_at       timestamptz not null default now();

update public.appointments set status = lower(status) where status is not null and status <> lower(status);
alter table public.appointments
  add constraint appointments_source_chk check (source in ('claudia','manager','member_portal','web')),
  add constraint appointments_status_chk check (status in ('scheduled','confirmed','completed','cancelled','no_show','rescheduled')),
  add constraint appointments_who_chk    check (member_id is not null or client_name is not null);

-- ---------- payments ----------
alter table public.payments
  add column if not exists due_date     date,
  add column if not exists provider     text,      -- 'stripe' | 'paypal' | 'cash' ... (futuro)
  add column if not exists provider_ref text,
  add column if not exists updated_at   timestamptz not null default now();

-- payment_date = fecha en que SE PAGÓ. Un pago pendiente no tiene fecha de pago.
alter table public.payments alter column payment_date drop default;
alter table public.payments alter column payment_status set default 'pending';
update public.payments set payment_status = lower(payment_status) where payment_status <> lower(payment_status);
alter table public.payments
  add constraint payments_status_chk check (payment_status in ('paid','pending','failed','refunded','void')),
  add constraint payments_amount_chk check (amount >= 0);
create index if not exists payments_tenant_status_idx on public.payments(tenant_id, payment_status, due_date);
create index if not exists payments_tenant_paid_idx   on public.payments(tenant_id, payment_date desc);

create or replace function public.payments_set_paid_date()
returns trigger language plpgsql set search_path = '' as $$
begin
  if new.payment_status = 'paid' and new.payment_date is null then
    new.payment_date := now();
  end if;
  new.updated_at := now();
  return new;
end $$;
drop trigger if exists payments_set_paid_date on public.payments;
create trigger payments_set_paid_date before insert or update on public.payments
  for each row execute function public.payments_set_paid_date();

-- ---------- calls (registro de llamadas de Claudia) ----------
create table if not exists public.calls (
  id                 uuid primary key default gen_random_uuid(),
  tenant_id          uuid not null references public.tenants(id) on delete restrict,
  call_sid           text not null unique,
  direction          text not null default 'inbound' check (direction in ('inbound','outbound')),
  handled_by         text not null default 'claudia',
  caller_phone       text,
  called_phone       text,
  caller_name        text,
  member_id          uuid,
  lead_id            uuid,
  started_at         timestamptz not null default now(),
  ended_at           timestamptz,
  duration_seconds   integer check (duration_seconds is null or duration_seconds >= 0),
  intent             text,
  summary            text,          -- resumen generado al final; NO es transcript
  outcome            text,
  appointment_id     uuid references public.appointments(id) on delete set null,
  transferred        boolean not null default false,
  follow_up_required boolean not null default false,
  status             text not null default 'in_progress'
                     check (status in ('in_progress','completed','busy','no_answer','failed','canceled','abandoned')),
  created_at         timestamptz not null default now(),
  updated_at         timestamptz not null default now(),
  constraint calls_member_fk foreign key (member_id, tenant_id) references public.members(id, tenant_id) on delete set null (member_id)
);
create index if not exists calls_tenant_started_idx on public.calls(tenant_id, started_at desc);
create index if not exists calls_tenant_followup_idx on public.calls(tenant_id) where follow_up_required;

-- ---------- leads (contactos que deja la gente) ----------
create table if not exists public.leads (
  id           uuid primary key default gen_random_uuid(),
  tenant_id    uuid not null references public.tenants(id) on delete restrict,
  call_id      uuid references public.calls(id) on delete set null,
  name         text,
  phone        text,
  email        text,
  reason       text,
  source       text not null default 'claudia' check (source in ('claudia','manager','member_portal','web')),
  status       text not null default 'new' check (status in ('new','contacted','converted','lost')),
  member_id    uuid,
  contacted_at timestamptz,
  notes        text,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now(),
  constraint leads_member_fk foreign key (member_id, tenant_id) references public.members(id, tenant_id) on delete set null (member_id)
);
create index if not exists leads_tenant_created_idx on public.leads(tenant_id, created_at desc);
create index if not exists leads_tenant_status_idx  on public.leads(tenant_id, status);

alter table public.calls        add constraint calls_lead_fk        foreign key (lead_id) references public.leads(id) on delete set null;
alter table public.appointments add constraint appointments_call_fk foreign key (call_id) references public.calls(id) on delete set null;
alter table public.appointments add constraint appointments_lead_fk foreign key (lead_id) references public.leads(id) on delete set null;

-- ---------- alerts (acciones requeridas guardadas) ----------
-- Las alertas "derivadas" (pago vencido, socio inactivo, cita sin confirmar)
-- se calculan en vivo en manager_alerts(); aquí se guardan las que nacen
-- de un evento (ej. Claudia capta un lead que hay que llamar).
create table if not exists public.alerts (
  id             uuid primary key default gen_random_uuid(),
  tenant_id      uuid not null references public.tenants(id) on delete restrict,
  type           text not null check (type in ('follow_up','call_manager','payment_overdue','member_inactive','appointment_confirmation','lead','other')),
  severity       text not null default 'warning' check (severity in ('info','warning','critical')),
  title          text not null,
  detail         text,
  member_id      uuid,
  call_id        uuid references public.calls(id) on delete cascade,
  lead_id        uuid references public.leads(id) on delete cascade,
  appointment_id uuid references public.appointments(id) on delete cascade,
  payment_id     uuid references public.payments(id) on delete cascade,
  status         text not null default 'open' check (status in ('open','resolved','dismissed')),
  due_at         timestamptz,
  resolved_at    timestamptz,
  resolved_by    uuid references auth.users(id) on delete set null,
  created_at     timestamptz not null default now(),
  constraint alerts_member_fk foreign key (member_id, tenant_id) references public.members(id, tenant_id) on delete cascade
);
create index if not exists alerts_tenant_open_idx on public.alerts(tenant_id, status, created_at desc);

-- ---------- updated_at genérico ----------
create or replace function public.touch_updated_at()
returns trigger language plpgsql set search_path = '' as $$
begin new.updated_at := now(); return new; end $$;

do $$
declare t text;
begin
  foreach t in array array['tenants','appointments','calls','leads'] loop
    execute format('drop trigger if exists %I on public.%I', t || '_touch', t);
    execute format('create trigger %I before update on public.%I for each row execute function public.touch_updated_at()', t || '_touch', t);
  end loop;
end $$;
