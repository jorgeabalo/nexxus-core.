-- =====================================================================
-- 10) Plan de entrenamiento personal.
--     El staff del gym (owner/manager/staff) diseña un plan por socio:
--     por día de la semana, una lista de máquinas/ejercicios con series y
--     repeticiones (o minutos en cardio). Un socio tiene como mucho un plan
--     activo. El socio solo LEE su plan, a través de member_training_plan().
--     exercise_key apunta al catálogo de member/assets/js/exercises.js.
-- =====================================================================

create table if not exists public.training_plans (
  id          uuid primary key default gen_random_uuid(),
  tenant_id   uuid not null references public.tenants(id) on delete cascade,
  member_id   uuid not null,
  title       text not null default '' check (char_length(title) <= 80),
  notes       text check (char_length(notes) <= 1000),
  active      boolean not null default true,
  created_by  uuid default auth.uid(),
  updated_by  uuid default auth.uid(),
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now(),
  constraint training_plans_member_fk foreign key (member_id, tenant_id) references public.members(id, tenant_id) on delete cascade,
  constraint training_plans_id_tenant_key unique (id, tenant_id)
);
create unique index if not exists training_plans_one_active on public.training_plans(member_id) where active;
create index if not exists training_plans_tenant_idx on public.training_plans(tenant_id);

create table if not exists public.training_plan_items (
  id            uuid primary key default gen_random_uuid(),
  tenant_id     uuid not null,
  plan_id       uuid not null,
  day_of_week   smallint not null check (day_of_week between 1 and 7),   -- 1 = lunes ... 7 = domingo
  position      smallint not null default 0 check (position between 0 and 99),
  exercise_key  text not null check (exercise_key ~ '^[a-z_]{2,40}$'),
  name          text check (char_length(name) <= 80),
  sets          smallint check (sets between 1 and 20),
  reps          smallint check (reps between 1 and 200),
  duration_min  smallint check (duration_min between 1 and 300),
  weight_lb     numeric(6,1) check (weight_lb >= 0 and weight_lb <= 2000),
  notes         text check (char_length(notes) <= 300),
  constraint training_plan_items_plan_fk foreign key (plan_id, tenant_id) references public.training_plans(id, tenant_id) on delete cascade,
  constraint training_plan_items_measure check ((sets is not null and reps is not null) or duration_min is not null)
);
create index if not exists training_plan_items_plan_idx on public.training_plan_items(plan_id, day_of_week, position);

-- ---------- RLS: solo el staff del gym; el socio lee por la RPC ----------
alter table public.training_plans      enable row level security;
alter table public.training_plan_items enable row level security;
do $$
declare t text;
begin
  foreach t in array array['training_plans','training_plan_items'] loop
    execute format('drop policy if exists %I on public.%I', t || '_staff', t);
    execute format($p$create policy %I on public.%I for all to authenticated
                     using (tenant_id in (select private.user_tenant_ids()))
                     with check (tenant_id in (select private.user_tenant_ids()))$p$, t || '_staff', t);
    execute format('revoke all on public.%I from anon', t);
    execute format('grant select, insert, update, delete on public.%I to authenticated', t);
  end loop;
end $$;

-- ---------- guardar el plan completo (staff) ----------
-- SECURITY INVOKER: RLS de arriba sigue aplicando. Reemplaza los ejercicios del
-- plan activo en una sola transacción (no quedan planes a medias).
create or replace function public.manager_save_training_plan(
  p_tenant uuid, p_member uuid, p_title text, p_notes text, p_items jsonb)
returns uuid language plpgsql volatile security invoker set search_path = '' as $$
declare plan uuid; n int;
begin
  if p_tenant is null or p_tenant not in (select private.user_tenant_ids()) then
    raise exception 'tenant not accessible' using errcode = '42501';
  end if;
  if not exists (select 1 from public.members where id = p_member and tenant_id = p_tenant) then
    raise exception 'member not found' using errcode = '22023';
  end if;
  if p_items is null or jsonb_typeof(p_items) <> 'array' then
    raise exception 'items must be an array' using errcode = '22023';
  end if;
  n := jsonb_array_length(p_items);
  if n > 140 then raise exception 'too many exercises' using errcode = '22023'; end if;

  select id into plan from public.training_plans where member_id = p_member and tenant_id = p_tenant and active for update;
  if plan is null then
    insert into public.training_plans (tenant_id, member_id, title, notes)
    values (p_tenant, p_member, left(trim(coalesce(p_title, '')), 80), nullif(left(trim(coalesce(p_notes, '')), 1000), ''))
    returning id into plan;
  else
    update public.training_plans
       set title = left(trim(coalesce(p_title, '')), 80), notes = nullif(left(trim(coalesce(p_notes, '')), 1000), ''),
           updated_at = now(), updated_by = (select auth.uid())
     where id = plan;
    delete from public.training_plan_items where plan_id = plan;
  end if;

  insert into public.training_plan_items (tenant_id, plan_id, day_of_week, position, exercise_key, name,
                                          sets, reps, duration_min, weight_lb, notes)
  select p_tenant, plan, x.day_of_week, (row_number() over (partition by x.day_of_week order by x.ord) - 1)::smallint,
         x.exercise_key, nullif(left(trim(coalesce(x.name, '')), 80), ''), x.sets, x.reps, x.duration_min, x.weight_lb,
         nullif(left(trim(coalesce(x.notes, '')), 300), '')
    from rows from (jsonb_to_recordset(p_items) as (day_of_week smallint, exercise_key text, name text, sets smallint,
                                                    reps smallint, duration_min smallint, weight_lb numeric, notes text))
         with ordinality as x(day_of_week, exercise_key, name, sets, reps, duration_min, weight_lb, notes, ord);
  return plan;
end $$;
revoke all on function public.manager_save_training_plan(uuid, uuid, text, text, jsonb) from public, anon;
grant execute on function public.manager_save_training_plan(uuid, uuid, text, text, jsonb) to authenticated;


-- =====================================================================
-- Registro de lo que el socio hace de verdad (para las gráficas por máquina).
-- Una fila por máquina y día (exercise_key + nombre). No depende de
-- training_plan_items (que se reemplazan al editar el plan): el historial
-- sobrevive a los cambios de plan. El socio escribe solo por las RPC.
-- =====================================================================
create table if not exists public.training_logs (
  id            uuid primary key default gen_random_uuid(),
  tenant_id     uuid not null,
  member_id     uuid not null,
  log_date      date not null,
  exercise_key  text not null check (exercise_key ~ '^[a-z_]{2,40}$'),
  name          text not null default '' check (char_length(name) <= 80),
  sets          smallint check (sets between 1 and 20),
  reps          smallint check (reps between 1 and 200),
  duration_min  smallint check (duration_min between 1 and 300),
  weight_lb     numeric(6,1) check (weight_lb >= 0 and weight_lb <= 2000),
  source        text not null default 'member' check (source in ('member','staff')),
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),
  constraint training_logs_member_fk foreign key (member_id, tenant_id) references public.members(id, tenant_id) on delete cascade,
  constraint training_logs_measure check ((sets is not null and reps is not null) or duration_min is not null),
  constraint training_logs_one_per_day unique (member_id, log_date, exercise_key, name)
);
create index if not exists training_logs_member_date_idx on public.training_logs(member_id, log_date desc);

alter table public.training_logs enable row level security;
drop policy if exists training_logs_staff on public.training_logs;
create policy training_logs_staff on public.training_logs for all to authenticated
  using (tenant_id in (select private.user_tenant_ids()))
  with check (tenant_id in (select private.user_tenant_ids()));
revoke all on public.training_logs from anon;
grant select, insert, update, delete on public.training_logs to authenticated;

-- El socio marca una máquina como hecha (o corrige lo que hizo). Solo hoy y
-- los 7 días anteriores, en la zona horaria del gym.
create or replace function public.member_log_exercise(p_date date, p_exercise_key text, p_name text,
                                                      p_sets int, p_reps int, p_weight_lb numeric, p_duration_min int)
returns void language plpgsql volatile security definer set search_path = '' as $$
declare m public.members%rowtype; d date;
begin
  select * into m from public.members where user_id = (select auth.uid()) limit 1;
  if m.id is null then raise exception 'not a member' using errcode = '42501'; end if;
  select (now() at time zone t.timezone)::date into d from public.tenants t where t.id = m.tenant_id;
  if p_date is null or p_date > d or p_date < d - 7 then
    raise exception 'date out of range' using errcode = '22023';
  end if;
  insert into public.training_logs (tenant_id, member_id, log_date, exercise_key, name, sets, reps, weight_lb, duration_min, source)
  values (m.tenant_id, m.id, p_date, p_exercise_key, left(trim(coalesce(p_name, '')), 80), p_sets, p_reps, p_weight_lb, p_duration_min, 'member')
  on conflict (member_id, log_date, exercise_key, name) do update
    set sets = excluded.sets, reps = excluded.reps, weight_lb = excluded.weight_lb,
        duration_min = excluded.duration_min, updated_at = now();
end $$;

create or replace function public.member_unlog_exercise(p_date date, p_exercise_key text, p_name text)
returns void language plpgsql volatile security definer set search_path = '' as $$
declare m public.members%rowtype; d date;
begin
  select * into m from public.members where user_id = (select auth.uid()) limit 1;
  if m.id is null then raise exception 'not a member' using errcode = '42501'; end if;
  select (now() at time zone t.timezone)::date into d from public.tenants t where t.id = m.tenant_id;
  if p_date is null or p_date > d or p_date < d - 7 then
    raise exception 'date out of range' using errcode = '22023';
  end if;
  delete from public.training_logs
   where member_id = m.id and log_date = p_date and exercise_key = p_exercise_key
     and name = left(trim(coalesce(p_name, '')), 80);
end $$;

-- Lectura del socio: plan + historial de los últimos 120 días.
create or replace function public.member_training_plan()
returns jsonb language plpgsql stable security definer set search_path = '' as $$
declare m public.members%rowtype; p public.training_plans%rowtype; d date;
begin
  select * into m from public.members where user_id = (select auth.uid()) limit 1;
  if m.id is null then raise exception 'not a member' using errcode = '42501'; end if;
  select (now() at time zone t.timezone)::date into d from public.tenants t where t.id = m.tenant_id;
  select * into p from public.training_plans where member_id = m.id and tenant_id = m.tenant_id and active;
  if p.id is null then return null; end if;
  return jsonb_build_object(
    'title', p.title, 'notes', p.notes, 'updated_at', p.updated_at, 'today', d,
    'items', coalesce((
      select jsonb_agg(jsonb_build_object(
        'day_of_week', i.day_of_week, 'exercise_key', i.exercise_key, 'name', i.name, 'sets', i.sets,
        'reps', i.reps, 'duration_min', i.duration_min, 'weight_lb', i.weight_lb, 'notes', i.notes)
        order by i.day_of_week, i.position)
      from public.training_plan_items i where i.plan_id = p.id), '[]'::jsonb),
    'logs', coalesce((
      select jsonb_agg(jsonb_build_object(
        'date', l.log_date, 'exercise_key', l.exercise_key, 'name', l.name, 'sets', l.sets, 'reps', l.reps,
        'duration_min', l.duration_min, 'weight_lb', l.weight_lb) order by l.log_date)
      from public.training_logs l where l.member_id = m.id and l.log_date >= d - 120), '[]'::jsonb));
end $$;

revoke all on function public.member_log_exercise(date, text, text, int, int, numeric, int) from public, anon;
revoke all on function public.member_unlog_exercise(date, text, text)                     from public, anon;
grant execute on function public.member_log_exercise(date, text, text, int, int, numeric, int) to authenticated;
grant execute on function public.member_unlog_exercise(date, text, text)                     to authenticated;
revoke all on function public.member_training_plan() from public, anon;
grant execute on function public.member_training_plan() to authenticated;
