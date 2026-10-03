-- =====================================================================
-- 8) Member Panel: progreso corporal, evaluaciones (onboarding) y SMS
--    * Solo AÑADE columnas/tablas/funciones. No borra ni renombra nada.
--    * measurements sigue guardando en unidades imperiales (lb / in), como
--      ya estaba; la conversión kg/cm se hace al mostrar.
--    * La línea base (is_baseline) es única por socio e inmutable.
--    * Los cuestionarios se guardan versionados en `questionnaires`. Esta
--      migración NO carga ningún cuestionario: el original de Golden Age
--      se carga cuando esté disponible (ver supabase/questionnaires/README.md).
-- =====================================================================

-- ---------- socios ----------
alter table public.members
  add column if not exists joined_as           text check (joined_as in ('new','existing')),
  add column if not exists next_evaluation_due date,
  add column if not exists portal_last_seen_at timestamptz;

comment on column public.members.joined_as is
  'new = alta nueva (se le presenta el onboarding); existing = "Soy miembro existente" (entra sin onboarding; queda pendiente hasta la evaluación trimestral). NULL se trata como existing.';

-- ---------- mediciones ----------
alter table public.measurements
  add column if not exists height_in        numeric,
  add column if not exists source           text not null default 'staff',
  add column if not exists recorded_by      uuid references auth.users(id) on delete set null,
  add column if not exists recorded_by_name text,
  add column if not exists is_baseline      boolean not null default false,
  add column if not exists evaluation_id    uuid;

do $$ begin
  if not exists (select 1 from pg_constraint where conname = 'measurements_source_chk') then
    alter table public.measurements add constraint measurements_source_chk check (source in ('member','staff'));
  end if;
  if not exists (select 1 from pg_constraint where conname = 'measurements_ranges_chk') then
    alter table public.measurements add constraint measurements_ranges_chk check (
      (weight_lb   is null or weight_lb   between 40 and 900) and
      (height_in   is null or height_in   between 36 and 96)  and
      (waist_in    is null or waist_in    between 10 and 90)  and
      (left_arm_in is null or left_arm_in between 4 and 40)   and
      (right_arm_in is null or right_arm_in between 4 and 40) and
      (left_leg_in is null or left_leg_in between 6 and 60)   and
      (right_leg_in is null or right_leg_in between 6 and 60));
  end if;
end $$;

create unique index if not exists measurements_one_baseline on public.measurements (member_id) where is_baseline;
create index if not exists measurements_member_date on public.measurements (member_id, measurement_date, created_at);

-- Quién registró la medición: lo decide el servidor, no el cliente.
create or replace function private.measurements_before_insert()
returns trigger language plpgsql security definer set search_path = '' as $$
declare uid uuid := (select auth.uid()); is_staff boolean; is_self boolean; sname text;
begin
  if new.measurement_date is null then new.measurement_date := current_date; end if;
  if uid is not null then
    new.recorded_by := uid;
    is_staff := new.tenant_id in (select private.user_tenant_ids());
    is_self  := exists (select 1 from public.members x where x.id = new.member_id and x.user_id = uid);
    if is_self or not is_staff then
      new.source := 'member';          -- lo que registra el propio socio siempre es "declarado"
      select m.first_name into sname from public.members m where m.id = new.member_id;
    else
      select coalesce(s.first_name, u.email) into sname
        from auth.users u
        left join public.tenant_users tu on tu.user_id = u.id and tu.tenant_id = new.tenant_id
        left join public.staff s on s.id = tu.staff_id
       where u.id = uid;
    end if;
    new.recorded_by_name := left(coalesce(sname, 'Staff'), 80);
  end if;
  return new;
end $$;

create or replace function private.measurements_guard()
returns trigger language plpgsql security definer set search_path = '' as $$
begin
  if tg_op = 'UPDATE' then
    if old.is_baseline then
      raise exception 'baseline measurement is immutable' using errcode = '42501';
    end if;
    if new.is_baseline or new.recorded_by is distinct from old.recorded_by
       or new.source is distinct from old.source or new.member_id is distinct from old.member_id then
      raise exception 'measurement authorship cannot be changed' using errcode = '42501';
    end if;
    return new;
  end if;
  -- DELETE: la línea base no se borra a mano (sí en cascada si se elimina el socio)
  if old.is_baseline and pg_trigger_depth() = 1 then
    raise exception 'baseline measurement is immutable' using errcode = '42501';
  end if;
  return old;
end $$;

drop trigger if exists measurements_before_insert on public.measurements;
create trigger measurements_before_insert before insert on public.measurements
  for each row execute function private.measurements_before_insert();
drop trigger if exists measurements_guard on public.measurements;
create trigger measurements_guard before update or delete on public.measurements
  for each row execute function private.measurements_guard();

-- ---------- cuestionarios (definición versionada) ----------
create table if not exists public.questionnaires (
  id          uuid primary key default gen_random_uuid(),
  tenant_id   uuid not null references public.tenants(id) on delete restrict,
  code        text not null default 'onboarding',
  version     integer not null,
  title       jsonb not null,
  definition  jsonb not null,
  source_note text,
  active      boolean not null default false,
  created_at  timestamptz not null default now(),
  unique (tenant_id, code, version),
  unique (id, tenant_id)
);
create unique index if not exists questionnaires_one_active on public.questionnaires (tenant_id, code) where active;
alter table public.questionnaires enable row level security;
create policy questionnaires_staff_select on public.questionnaires for select to authenticated
  using (tenant_id in (select private.user_tenant_ids()));
create policy questionnaires_member_select on public.questionnaires for select to authenticated
  using (active and tenant_id in (select private.my_member_tenant_ids()));
create policy questionnaires_owner_write on public.questionnaires for insert to authenticated
  with check (private.has_tenant_role(tenant_id, array['owner']));
create policy questionnaires_owner_update on public.questionnaires for update to authenticated
  using (private.has_tenant_role(tenant_id, array['owner']))
  with check (private.has_tenant_role(tenant_id, array['owner']));

-- Una versión ya respondida no se puede modificar (se crea una versión nueva).
create or replace function private.questionnaires_guard()
returns trigger language plpgsql security definer set search_path = '' as $$
begin
  if (new.definition is distinct from old.definition or new.version is distinct from old.version
      or new.code is distinct from old.code)
     and exists (select 1 from public.member_evaluations e where e.questionnaire_id = old.id) then
    raise exception 'questionnaire version already in use; create a new version' using errcode = '42501';
  end if;
  return new;
end $$;

-- ---------- indicadores de progreso (fuerza, resistencia, movilidad, bienestar) ----------
-- Una fila por dato registrado. Nunca se sobrescribe: la línea base es la fila
-- marcada is_baseline (una por indicador) o, si no la hay, el primer registro.
create table if not exists public.progress_entries (
  id               uuid primary key default gen_random_uuid(),
  tenant_id        uuid not null,
  member_id        uuid not null,
  category         text not null check (category in ('strength','endurance','mobility','wellbeing')),
  metric           text not null,
  exercise         text,                           -- fuerza: nombre del ejercicio; resistencia "other": actividad
  metric_key       text not null,                  -- category:metric[:ejercicio normalizado] (para comparar lo mismo)
  value            numeric not null,               -- fuerza: carga en lb · resistencia: minutos · escalas 1-5 · prueba: repeticiones
  reps             integer,                        -- fuerza: repeticiones
  conditions       text,                           -- condiciones de la prueba (máquina, ajuste, ritmo...)
  entry_date       date not null,
  source           text not null default 'staff' check (source in ('member','staff')),
  recorded_by      uuid references auth.users(id) on delete set null,
  recorded_by_name text,
  is_baseline      boolean not null default false,
  evaluation_id    uuid,
  notes            text,
  created_at       timestamptz not null default now(),
  foreign key (member_id, tenant_id) references public.members(id, tenant_id) on delete cascade,
  check (
    (category = 'strength'  and metric = 'exercise' and exercise is not null and value between 0 and 2000 and reps between 1 and 200) or
    (category = 'endurance' and metric in ('walk','bike','other') and value between 1 and 600 and reps is null) or
    (category = 'mobility'  and metric in ('chair_rise','stairs','walking') and value between 1 and 5 and reps is null) or
    (category = 'mobility'  and metric = 'chair_stand_30s' and value between 0 and 60 and reps is null) or
    (category = 'wellbeing' and metric in ('energy','sleep','overall') and value between 1 and 5 and reps is null)
  )
);
create unique index if not exists progress_entries_one_baseline on public.progress_entries (member_id, metric_key) where is_baseline;
create index if not exists progress_entries_member on public.progress_entries (member_id, metric_key, entry_date, created_at);
alter table public.progress_entries enable row level security;
create policy progress_entries_staff_select on public.progress_entries for select to authenticated
  using (tenant_id in (select private.user_tenant_ids()));
create policy progress_entries_staff_insert on public.progress_entries for insert to authenticated
  with check (tenant_id in (select private.user_tenant_ids()));
create policy progress_entries_self_select on public.progress_entries for select to authenticated
  using (member_id in (select private.my_member_ids()));
-- sin políticas de UPDATE/DELETE: el historial no se edita (errores = nuevo registro + nota)

create or replace function private.progress_before_insert()
returns trigger language plpgsql security definer set search_path = '' as $$
declare uid uuid := (select auth.uid()); is_staff boolean; is_self boolean; sname text;
begin
  new.exercise := nullif(left(trim(regexp_replace(coalesce(new.exercise, ''), '\s+', ' ', 'g')), 80), '');
  new.metric_key := new.category || ':' || new.metric ||
                    case when new.exercise is not null and new.metric in ('exercise','other')
                         then ':' || lower(new.exercise) else '' end;
  new.conditions := nullif(left(trim(coalesce(new.conditions, '')), 200), '');
  new.notes := nullif(left(trim(coalesce(new.notes, '')), 500), '');
  if new.entry_date is null then new.entry_date := current_date; end if;
  if uid is not null then
    new.recorded_by := uid;
    is_staff := new.tenant_id in (select private.user_tenant_ids());
    is_self  := exists (select 1 from public.members x where x.id = new.member_id and x.user_id = uid);
    if is_self or not is_staff then
      new.source := 'member';
      select m.first_name into sname from public.members m where m.id = new.member_id;
    else
      select coalesce(s.first_name, u.email) into sname
        from auth.users u
        left join public.tenant_users tu on tu.user_id = u.id and tu.tenant_id = new.tenant_id
        left join public.staff s on s.id = tu.staff_id
       where u.id = uid;
    end if;
    new.recorded_by_name := left(coalesce(sname, 'Staff'), 80);
  end if;
  return new;
end $$;
drop trigger if exists progress_before_insert on public.progress_entries;
create trigger progress_before_insert before insert on public.progress_entries
  for each row execute function private.progress_before_insert();

-- ---------- evaluaciones (inicial y trimestrales) ----------
-- Cada evaluación es un registro independiente. La inicial nunca se
-- sobrescribe con la trimestral. questionnaire_id es NULL mientras el
-- cuestionario original del gym no esté cargado.
create table if not exists public.member_evaluations (
  id                    uuid primary key default gen_random_uuid(),
  tenant_id             uuid not null,
  member_id             uuid not null,
  questionnaire_id      uuid,
  questionnaire_version integer,
  kind                  text not null check (kind in ('initial','reevaluation')),
  status                text not null default 'draft' check (status in ('draft','submitted')),
  answers               jsonb not null default '{}'::jsonb,
  current_step          integer not null default 0,
  client_ref            uuid unique,
  measurement_id        uuid references public.measurements(id) on delete set null,
  started_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now(),
  submitted_at          timestamptz,
  next_due_date         date,
  foreign key (member_id, tenant_id) references public.members(id, tenant_id) on delete cascade,
  foreign key (questionnaire_id, tenant_id) references public.questionnaires(id, tenant_id) on delete restrict,
  check (pg_column_size(answers) < 65536)
);
create unique index if not exists member_evaluations_one_draft on public.member_evaluations (member_id, kind) where status = 'draft';
create unique index if not exists member_evaluations_one_initial on public.member_evaluations (member_id) where kind = 'initial' and status = 'submitted';
create index if not exists member_evaluations_member on public.member_evaluations (member_id, submitted_at);
alter table public.member_evaluations enable row level security;
create policy member_evaluations_staff_select on public.member_evaluations for select to authenticated
  using (tenant_id in (select private.user_tenant_ids()));
create policy member_evaluations_self_select on public.member_evaluations for select to authenticated
  using (member_id in (select private.my_member_ids()));
-- Escrituras solo por las funciones member_* (security definer).

drop trigger if exists questionnaires_guard on public.questionnaires;
create trigger questionnaires_guard before update on public.questionnaires
  for each row execute function private.questionnaires_guard();

create or replace function private.member_evaluations_guard()
returns trigger language plpgsql security definer set search_path = '' as $$
begin
  if old.status = 'submitted' then
    raise exception 'submitted evaluation is immutable' using errcode = '42501';
  end if;
  return new;
end $$;
drop trigger if exists member_evaluations_guard on public.member_evaluations;
create trigger member_evaluations_guard before update on public.member_evaluations
  for each row execute function private.member_evaluations_guard();

-- ---------- invitaciones a evaluar (cada 90 días) ----------
create table if not exists public.evaluation_invitations (
  id           uuid primary key default gen_random_uuid(),
  tenant_id    uuid not null,
  member_id    uuid not null,
  kind         text not null check (kind in ('initial','reevaluation')),
  due_date     date not null,
  channel      text not null check (channel in ('sms','email','sms+email')),
  auto         boolean not null default false,
  sms_status   text,
  email_status text,
  created_by   uuid references auth.users(id) on delete set null,
  created_at   timestamptz not null default now(),
  foreign key (member_id, tenant_id) references public.members(id, tenant_id) on delete cascade
);
create unique index if not exists evaluation_invitations_auto_once on public.evaluation_invitations (member_id, due_date) where auto;
alter table public.evaluation_invitations enable row level security;
create policy evaluation_invitations_staff_select on public.evaluation_invitations for select to authenticated
  using (tenant_id in (select private.user_tenant_ids()));

-- ---------- SMS enviados (estado de entrega) ----------
create table if not exists public.sms_messages (
  id            uuid primary key default gen_random_uuid(),
  tenant_id     uuid not null references public.tenants(id) on delete restrict,
  member_id     uuid,
  purpose       text not null,
  to_masked     text not null,
  message_sid   text unique,
  status        text not null check (status in ('queued','sent','delivered','failed')),
  twilio_status text,
  error_code    integer,
  error_message text,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),
  delivered_at  timestamptz,
  foreign key (member_id, tenant_id) references public.members(id, tenant_id) on delete set null (member_id)
);
create index if not exists sms_messages_member on public.sms_messages (member_id, created_at desc);
alter table public.sms_messages enable row level security;
create policy sms_messages_staff_select on public.sms_messages for select to authenticated
  using (private.has_tenant_role(tenant_id, array['owner','manager']));
-- Escrituras solo desde el servidor (service_role). El cuerpo del SMS (lleva
-- el enlace de acceso) NUNCA se guarda.

-- ---------- validación de respuestas contra la definición ----------
create or replace function private.validate_answers(p_def jsonb, p_answers jsonb, p_final boolean)
returns void language plpgsql immutable set search_path = '' as $$
declare q jsonb; v jsonb; qtype text; opts jsonb; item jsonb;
begin
  if jsonb_typeof(p_answers) <> 'object' then
    raise exception 'answers must be an object' using errcode = '22023';
  end if;
  if exists (select 1 from jsonb_object_keys(p_answers) k
              where k not in (select qq->>'id' from jsonb_array_elements(p_def->'sections') s,
                                                    jsonb_array_elements(s->'questions') qq)) then
    raise exception 'unknown question' using errcode = '22023';
  end if;
  for q in select qq from jsonb_array_elements(p_def->'sections') s, jsonb_array_elements(s->'questions') qq loop
    v := p_answers -> (q->>'id');
    qtype := q->>'type';
    opts := coalesce(q->'options', '[]'::jsonb);
    if v is null or v = 'null'::jsonb or v = '""'::jsonb or v = '[]'::jsonb then
      if p_final and coalesce((q->>'required')::boolean, false) then
        raise exception 'required question missing: %', q->>'id' using errcode = '22023';
      end if;
      continue;
    end if;
    if qtype = 'single' then
      if jsonb_typeof(v) <> 'string' or not exists (select 1 from jsonb_array_elements(opts) o where o->>'value' = v#>>'{}') then
        raise exception 'invalid option for %', q->>'id' using errcode = '22023';
      end if;
    elsif qtype = 'multi' then
      if jsonb_typeof(v) <> 'array' then raise exception 'invalid answer for %', q->>'id' using errcode = '22023'; end if;
      for item in select * from jsonb_array_elements(v) loop
        if not exists (select 1 from jsonb_array_elements(opts) o where o->>'value' = item#>>'{}') then
          raise exception 'invalid option for %', q->>'id' using errcode = '22023';
        end if;
      end loop;
    elsif qtype = 'number' then
      if jsonb_typeof(v) <> 'number'
         or (q ? 'min' and (v#>>'{}')::numeric < (q->>'min')::numeric)
         or (q ? 'max' and (v#>>'{}')::numeric > (q->>'max')::numeric) then
        raise exception 'invalid number for %', q->>'id' using errcode = '22023';
      end if;
    elsif qtype = 'yesno' then
      if jsonb_typeof(v) <> 'boolean' then raise exception 'invalid answer for %', q->>'id' using errcode = '22023'; end if;
    elsif qtype = 'date' then
      if jsonb_typeof(v) <> 'string' or (v#>>'{}') !~ '^\d{4}-\d{2}-\d{2}$' then
        raise exception 'invalid date for %', q->>'id' using errcode = '22023';
      end if;
    else
      if jsonb_typeof(v) <> 'string' or length(v#>>'{}') > 2000 then
        raise exception 'invalid text for %', q->>'id' using errcode = '22023';
      end if;
    end if;
  end loop;
end $$;

create or replace function private.num_or_null(p jsonb, k text)
returns numeric language sql immutable set search_path = '' as $$
  select case when jsonb_typeof(p->k) = 'number' then (p->>k)::numeric end
$$;

-- Inserta los indicadores de una evaluación o de una actualización breve.
-- p_items: [{category, metric, exercise?, value, reps?, conditions?, notes?}]
create or replace function private.insert_progress(p_member uuid, p_tenant uuid, p_items jsonb, p_date date,
                                                   p_eval uuid, p_mark_baseline boolean)
returns integer language plpgsql volatile security definer set search_path = '' as $$
declare it jsonb; n int := 0; new_id uuid; k text;
begin
  if p_items is null or jsonb_typeof(p_items) <> 'array' then return 0; end if;
  if jsonb_array_length(p_items) > 40 then raise exception 'too many items' using errcode = '22023'; end if;
  for it in select * from jsonb_array_elements(p_items) loop
    if jsonb_typeof(it->'value') <> 'number' then continue; end if;
    insert into public.progress_entries (tenant_id, member_id, category, metric, exercise, value, reps,
                                         conditions, notes, entry_date, source, evaluation_id, metric_key)
    values (p_tenant, p_member, it->>'category', it->>'metric', it->>'exercise', (it->>'value')::numeric,
            case when jsonb_typeof(it->'reps') = 'number' then (it->>'reps')::integer end,
            it->>'conditions', it->>'notes', p_date, 'member', p_eval, '-')
    returning id, metric_key into new_id, k;
    if p_mark_baseline and not exists (select 1 from public.progress_entries
                                        where member_id = p_member and metric_key = k and is_baseline) then
      update public.progress_entries set is_baseline = true where id = new_id;
    end if;
    n := n + 1;
  end loop;
  return n;
end $$;

-- ---------- vista agregada del portal (mismas claves que antes + nuevas) ----------
create or replace function public.member_portal()
returns jsonb language plpgsql stable security definer set search_path = '' as $$
declare
  m public.members%rowtype; t public.tenants%rowtype; d date; m0 timestamptz; q public.questionnaires%rowtype;
  program_start date;
begin
  select * into m from public.members where user_id = (select auth.uid()) limit 1;
  if m.id is null then
    raise exception 'not a member' using errcode = '42501';
  end if;
  select * into t from public.tenants where id = m.tenant_id;
  d  := (now() at time zone t.timezone)::date;
  m0 := date_trunc('month', d::timestamp) at time zone t.timezone;
  select * into q from public.questionnaires where tenant_id = m.tenant_id and code = 'onboarding' and active limit 1;
  program_start := least(m.start_date,
                         (select min(submitted_at)::date from public.member_evaluations where member_id = m.id and status = 'submitted'),
                         (select min((check_in_time at time zone t.timezone)::date) from public.check_ins where member_id = m.id));

  return jsonb_build_object(
    'tenant', jsonb_build_object('name', t.name, 'timezone', t.timezone, 'branding', t.branding,
                                 'phone', t.settings->>'public_phone', 'address', t.settings->>'address'),
    'today', d,
    'member', jsonb_build_object(
      'id', m.id, 'member_code', m.member_id, 'first_name', m.first_name, 'last_name', m.last_name,
      'email', m.email, 'phone', m.phone,
      'emergency_contact_name', m.emergency_contact_name, 'emergency_contact_phone', m.emergency_contact_phone,
      'membership_type', m.membership_type, 'membership_status', lower(coalesce(m.membership_status, 'active')),
      'start_date', m.start_date, 'next_payment_date', m.next_payment_date,
      'sex', case when upper(left(coalesce(m.gender,''),1)) = 'M' then 'male'
                  when upper(left(coalesce(m.gender,''),1)) = 'F' then 'female' end,
      'joined_as', coalesce(m.joined_as, 'existing'),
      'onboarding_completed', coalesce(m.onboarding_completed, false),
      'next_evaluation_due', m.next_evaluation_due,
      'portal_last_seen_at', m.portal_last_seen_at),
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
      'last_30_days', (select count(*) from public.check_ins c where c.member_id = m.id and c.check_in_time >= now() - interval '30 days'),
      'total', (select count(*) from public.check_ins c where c.member_id = m.id),
      'last_visit', (select max(c.check_in_time) from public.check_ins c where c.member_id = m.id),
      'recent', coalesce((select jsonb_agg(x.check_in_time order by x.check_in_time desc)
                          from (select c.check_in_time from public.check_ins c where c.member_id = m.id
                                order by c.check_in_time desc limit 20) x), '[]'::jsonb)),
    'consistency', jsonb_build_object(
      'program_start', program_start,
      'first_30_days_visits', case when program_start is null then null else
          (select count(*) from public.check_ins c where c.member_id = m.id
             and (c.check_in_time at time zone t.timezone)::date between program_start and program_start + 29) end,
      'last_30_days_visits', (select count(*) from public.check_ins c where c.member_id = m.id and c.check_in_time >= now() - interval '30 days'),
      'sessions_completed', (select count(*) from public.appointments a where a.member_id = m.id and a.status = 'completed'),
      'sessions_completed_last_30', (select count(*) from public.appointments a where a.member_id = m.id and a.status = 'completed'
                                       and a.appointment_date >= d - 29),
      'monthly_visits', coalesce((select jsonb_agg(jsonb_build_object('month', x.mo, 'visits', x.n) order by x.mo)
                          from (select to_char((c.check_in_time at time zone t.timezone), 'YYYY-MM') mo, count(*) n
                                  from public.check_ins c where c.member_id = m.id
                                   and c.check_in_time >= now() - interval '12 months'
                                 group by 1) x), '[]'::jsonb)),
    'services', coalesce((
      select jsonb_agg(jsonb_build_object('name', sv.name, 'duration_minutes', sv.duration_minutes) order by sv.name)
      from public.services sv where sv.tenant_id = m.tenant_id and sv.active), '[]'::jsonb),
    'measurements', coalesce((
      select jsonb_agg(jsonb_build_object(
        'id', x.id, 'date', x.measurement_date, 'created_at', x.created_at,
        'weight_lb', x.weight_lb, 'height_in', x.height_in, 'waist_in', x.waist_in,
        'left_arm_in', x.left_arm_in, 'right_arm_in', x.right_arm_in,
        'left_leg_in', x.left_leg_in, 'right_leg_in', x.right_leg_in,
        'source', x.source, 'recorded_by_name', x.recorded_by_name, 'is_baseline', x.is_baseline,
        'notes', x.notes)
        order by x.measurement_date, x.created_at)
      from public.measurements x where x.member_id = m.id), '[]'::jsonb),
    'progress', coalesce((
      select jsonb_agg(jsonb_build_object(
        'id', p.id, 'category', p.category, 'metric', p.metric, 'exercise', p.exercise, 'metric_key', p.metric_key,
        'value', p.value, 'reps', p.reps, 'conditions', p.conditions, 'date', p.entry_date, 'created_at', p.created_at,
        'source', p.source, 'recorded_by_name', p.recorded_by_name, 'is_baseline', p.is_baseline, 'notes', p.notes)
        order by p.entry_date, p.created_at)
      from public.progress_entries p where p.member_id = m.id), '[]'::jsonb),
    'evaluation', jsonb_build_object(
      'questionnaire', case when q.id is null then null else jsonb_build_object(
                         'id', q.id, 'version', q.version, 'title', q.title, 'definition', q.definition) end,
      'drafts', coalesce((select jsonb_object_agg(e.kind, jsonb_build_object(
                           'answers', e.answers, 'step', e.current_step, 'updated_at', e.updated_at,
                           'questionnaire_version', e.questionnaire_version))
                         from public.member_evaluations e where e.member_id = m.id and e.status = 'draft'), '{}'::jsonb),
      'submitted', coalesce((select jsonb_agg(jsonb_build_object(
                           'id', e.id, 'kind', e.kind, 'submitted_at', e.submitted_at,
                           'questionnaire_version', e.questionnaire_version, 'answers', e.answers,
                           'definition', qq.definition, 'title', qq.title, 'next_due_date', e.next_due_date)
                           order by e.submitted_at)
                         from public.member_evaluations e left join public.questionnaires qq on qq.id = e.questionnaire_id
                         where e.member_id = m.id and e.status = 'submitted'), '[]'::jsonb))
  );
end $$;

-- ---------- acciones del socio ----------
create or replace function public.member_mark_seen()
returns void language sql volatile security definer set search_path = '' as $$
  update public.members set portal_last_seen_at = now() where user_id = (select auth.uid())
$$;

create or replace function public.member_set_sex(p_sex text)
returns void language plpgsql volatile security definer set search_path = '' as $$
declare n int;
begin
  if p_sex is not null and p_sex not in ('male','female') then
    raise exception 'invalid sex' using errcode = '22023';
  end if;
  update public.members
     set gender = case p_sex when 'male' then 'M' when 'female' then 'F' end, updated_at = now()
   where user_id = (select auth.uid());
  get diagnostics n = row_count;
  if n = 0 then raise exception 'not a member' using errcode = '42501'; end if;
end $$;

-- Actualización breve (durante el mes): medidas corporales y/o indicadores.
-- Nunca marca línea base: eso solo lo hace la evaluación inicial (o el staff).
create or replace function public.member_log_progress(p_body jsonb, p_items jsonb)
returns jsonb language plpgsql volatile security definer set search_path = '' as $$
declare m public.members%rowtype; tz text; d date; meas uuid; n int := 0;
begin
  select * into m from public.members where user_id = (select auth.uid()) limit 1;
  if m.id is null then raise exception 'not a member' using errcode = '42501'; end if;
  select timezone into tz from public.tenants where id = m.tenant_id;
  d := (now() at time zone tz)::date;
  if p_body is not null and coalesce(private.num_or_null(p_body,'weight_lb'), private.num_or_null(p_body,'height_in'),
         private.num_or_null(p_body,'waist_in'), private.num_or_null(p_body,'left_arm_in'),
         private.num_or_null(p_body,'right_arm_in'), private.num_or_null(p_body,'left_leg_in'),
         private.num_or_null(p_body,'right_leg_in')) is not null then
    insert into public.measurements (tenant_id, member_id, measurement_date, source, is_baseline,
           weight_lb, height_in, waist_in, left_arm_in, right_arm_in, left_leg_in, right_leg_in, notes)
    values (m.tenant_id, m.id, d, 'member', false,
            private.num_or_null(p_body,'weight_lb'), private.num_or_null(p_body,'height_in'), private.num_or_null(p_body,'waist_in'),
            private.num_or_null(p_body,'left_arm_in'), private.num_or_null(p_body,'right_arm_in'),
            private.num_or_null(p_body,'left_leg_in'), private.num_or_null(p_body,'right_leg_in'),
            nullif(left(trim(coalesce(p_body->>'notes','')), 500), ''))
    returning id into meas;
  end if;
  n := private.insert_progress(m.id, m.tenant_id, p_items, d, null, false);
  if meas is null and n = 0 then raise exception 'no values' using errcode = '22023'; end if;
  return jsonb_build_object('measurement_id', meas, 'items', n);
end $$;

create or replace function public.member_save_evaluation_draft(p_kind text, p_answers jsonb, p_step integer)
returns timestamptz language plpgsql volatile security definer set search_path = '' as $$
declare m public.members%rowtype; q public.questionnaires%rowtype; ts timestamptz := now();
begin
  select * into m from public.members where user_id = (select auth.uid()) limit 1;
  if m.id is null then raise exception 'not a member' using errcode = '42501'; end if;
  if p_kind not in ('initial','reevaluation') then raise exception 'invalid kind' using errcode = '22023'; end if;
  if jsonb_typeof(coalesce(p_answers, '{}'::jsonb)) <> 'object' then raise exception 'invalid draft' using errcode = '22023'; end if;
  if p_kind = 'initial' and exists (select 1 from public.member_evaluations
                                     where member_id = m.id and kind = 'initial' and status = 'submitted') then
    raise exception 'initial evaluation already submitted' using errcode = '22023';
  end if;
  select * into q from public.questionnaires where tenant_id = m.tenant_id and code = 'onboarding' and active limit 1;
  if q.id is not null and p_answers ? 'q' then
    perform private.validate_answers(q.definition, p_answers->'q', false);
  end if;

  update public.member_evaluations
     set answers = coalesce(p_answers, '{}'::jsonb), current_step = greatest(0, coalesce(p_step, 0)),
         updated_at = ts, questionnaire_id = q.id, questionnaire_version = q.version
   where member_id = m.id and kind = p_kind and status = 'draft';
  if not found then
    insert into public.member_evaluations (tenant_id, member_id, questionnaire_id, questionnaire_version, kind,
                                           answers, current_step, updated_at)
    values (m.tenant_id, m.id, q.id, q.version, p_kind, coalesce(p_answers, '{}'::jsonb),
            greatest(0, coalesce(p_step, 0)), ts);
  end if;
  return ts;
end $$;

-- Envío final. Idempotente por p_client_ref: reintentar no duplica.
-- p_payload: {q: {respuestas del cuestionario}, body: {medidas en lb/in}, progress: [indicadores]}
create or replace function public.member_submit_evaluation(p_kind text, p_payload jsonb, p_client_ref uuid)
returns jsonb language plpgsql volatile security definer set search_path = '' as $$
declare
  m public.members%rowtype; q public.questionnaires%rowtype; tz text; d date; e public.member_evaluations%rowtype;
  eval_id uuid; meas_id uuid; has_baseline boolean; next_due date; body jsonb; n_items int; with_q boolean;
begin
  select * into m from public.members where user_id = (select auth.uid()) limit 1;
  if m.id is null then raise exception 'not a member' using errcode = '42501'; end if;
  if p_client_ref is null then raise exception 'client_ref required' using errcode = '22023'; end if;

  -- reintento del mismo envío: devolver el resultado ya guardado
  select * into e from public.member_evaluations where client_ref = p_client_ref;
  if e.id is not null then
    if e.member_id <> m.id then raise exception 'not allowed' using errcode = '42501'; end if;
    return jsonb_build_object('evaluation_id', e.id, 'measurement_id', e.measurement_id,
                              'next_due_date', e.next_due_date, 'duplicate', true);
  end if;

  if p_kind not in ('initial','reevaluation') then raise exception 'invalid kind' using errcode = '22023'; end if;
  if jsonb_typeof(coalesce(p_payload, '{}'::jsonb)) <> 'object' then raise exception 'invalid payload' using errcode = '22023'; end if;
  if p_kind = 'initial' and exists (select 1 from public.member_evaluations
                                     where member_id = m.id and kind = 'initial' and status = 'submitted') then
    raise exception 'initial evaluation already submitted' using errcode = '22023';
  end if;

  select * into q from public.questionnaires where tenant_id = m.tenant_id and code = 'onboarding' and active limit 1;
  with_q := q.id is not null;
  if with_q then
    perform private.validate_answers(q.definition, coalesce(p_payload->'q', '{}'::jsonb), true);
  end if;

  select timezone into tz from public.tenants where id = m.tenant_id;
  d := (now() at time zone tz)::date;
  next_due := d + 90;
  eval_id := gen_random_uuid();
  body := p_payload->'body';

  if body is not null and coalesce(
      private.num_or_null(body,'weight_lb'), private.num_or_null(body,'height_in'),
      private.num_or_null(body,'waist_in'), private.num_or_null(body,'left_arm_in'),
      private.num_or_null(body,'right_arm_in'), private.num_or_null(body,'left_leg_in'),
      private.num_or_null(body,'right_leg_in')) is not null then
    select exists (select 1 from public.measurements where member_id = m.id and is_baseline) into has_baseline;
    insert into public.measurements (tenant_id, member_id, measurement_date, source, is_baseline, evaluation_id,
           weight_lb, height_in, waist_in, left_arm_in, right_arm_in, left_leg_in, right_leg_in)
    values (m.tenant_id, m.id, d, 'member', (p_kind = 'initial' and not has_baseline), eval_id,
            private.num_or_null(body,'weight_lb'), private.num_or_null(body,'height_in'),
            private.num_or_null(body,'waist_in'), private.num_or_null(body,'left_arm_in'),
            private.num_or_null(body,'right_arm_in'), private.num_or_null(body,'left_leg_in'),
            private.num_or_null(body,'right_leg_in'))
    returning id into meas_id;
  end if;
  n_items := private.insert_progress(m.id, m.tenant_id, p_payload->'progress', d, eval_id, p_kind = 'initial');

  if not with_q and meas_id is null and n_items = 0 then
    raise exception 'empty evaluation' using errcode = '22023';
  end if;

  delete from public.member_evaluations where member_id = m.id and kind = p_kind and status = 'draft';
  insert into public.member_evaluations (id, tenant_id, member_id, questionnaire_id, questionnaire_version, kind,
         status, answers, client_ref, measurement_id, submitted_at, next_due_date)
  values (eval_id, m.tenant_id, m.id, q.id, q.version, p_kind, 'submitted',
          jsonb_build_object('q', coalesce(p_payload->'q', '{}'::jsonb), 'body', coalesce(body, '{}'::jsonb),
                             'progress', coalesce(p_payload->'progress', '[]'::jsonb)),
          p_client_ref, meas_id, now(), next_due);

  update public.members
     set onboarding_completed = (coalesce(onboarding_completed, false) or with_q),
         next_evaluation_due = next_due, updated_at = now()
   where id = m.id;

  insert into public.alerts (tenant_id, type, severity, title, detail, member_id)
  values (m.tenant_id, 'other', 'info',
          case p_kind when 'initial' then 'Initial evaluation: ' else 'Re-evaluation: ' end
            || trim(concat_ws(' ', m.first_name, m.last_name)),
          'Member Portal' || case when with_q then ' · questionnaire v' || q.version else ' · questionnaire pending' end,
          m.id);

  return jsonb_build_object('evaluation_id', eval_id, 'measurement_id', meas_id, 'next_due_date', next_due,
                            'items', n_items, 'questionnaire_included', with_q, 'duplicate', false);
end $$;

revoke all on function public.member_mark_seen()                                 from public, anon;
revoke all on function public.member_set_sex(text)                               from public, anon;
revoke all on function public.member_log_progress(jsonb, jsonb)                  from public, anon;
revoke all on function public.member_save_evaluation_draft(text, jsonb, integer) from public, anon;
revoke all on function public.member_submit_evaluation(text, jsonb, uuid)        from public, anon;
grant execute on function public.member_mark_seen()                                 to authenticated;
grant execute on function public.member_set_sex(text)                               to authenticated;
grant execute on function public.member_log_progress(jsonb, jsonb)                  to authenticated;
grant execute on function public.member_save_evaluation_draft(text, jsonb, integer) to authenticated;
grant execute on function public.member_submit_evaluation(text, jsonb, uuid)        to authenticated;
revoke all on function private.validate_answers(jsonb, jsonb, boolean)             from public, anon, authenticated;
revoke all on function private.num_or_null(jsonb, text)                            from public, anon, authenticated;
revoke all on function private.insert_progress(uuid, uuid, jsonb, date, uuid, boolean) from public, anon, authenticated;
