-- =====================================================================
-- 9) Inicio del portal por paneles: datos personales, métricas del gym
--    (asistencia, antigüedad, récords) y anuncio del gimnasio.
--    Solo lectura de las filas del propio socio. No cambia tablas.
-- =====================================================================
create or replace function public.member_home()
returns jsonb language plpgsql stable security definer set search_path = '' as $$
declare m public.members%rowtype; t public.tenants%rowtype;
begin
  select * into m from public.members where user_id = (select auth.uid()) limit 1;
  if m.id is null then raise exception 'not a member' using errcode = '42501'; end if;
  select * into t from public.tenants where id = m.tenant_id;
  return jsonb_build_object(
    'date_of_birth', m.date_of_birth,
    'announcement', t.settings->'announcement',
    'total_visits', (select count(*) from public.check_ins c where c.member_id = m.id),
    'first_visit', (select min((c.check_in_time at time zone t.timezone)::date) from public.check_ins c where c.member_id = m.id),
    'best_month', (select jsonb_build_object('month', x.mo, 'visits', x.n) from (
        select to_char(c.check_in_time at time zone t.timezone, 'YYYY-MM') mo, count(*) n
          from public.check_ins c where c.member_id = m.id group by 1 order by 2 desc, 1 desc limit 1) x),
    'longest_session_min', (select round(max(extract(epoch from (c.check_out_time - c.check_in_time)) / 60))
        from public.check_ins c where c.member_id = m.id and c.check_out_time > c.check_in_time
          and c.check_out_time - c.check_in_time < interval '12 hours'),
    'visit_weeks', coalesce((select jsonb_agg(w order by w) from (
        select distinct date_trunc('week', c.check_in_time at time zone t.timezone)::date w
          from public.check_ins c where c.member_id = m.id and c.check_in_time >= now() - interval '6 years') x), '[]'::jsonb),
    'sessions_completed', (select count(*) from public.appointments a where a.member_id = m.id and a.status = 'completed'),
    'initial_evaluation_at', (select min(submitted_at) from public.member_evaluations e
        where e.member_id = m.id and e.kind = 'initial' and e.status = 'submitted'));
end $$;
revoke all on function public.member_home() from public, anon;
grant execute on function public.member_home() to authenticated;

-- El socio puede completar su fecha de nacimiento si falta (validada).
create or replace function public.member_set_birthdate(p_date date)
returns void language plpgsql volatile security definer set search_path = '' as $$
declare n int;
begin
  if p_date is null or p_date > current_date - interval '14 years' or p_date < date '1900-01-01' then
    raise exception 'invalid birthdate' using errcode = '22023';
  end if;
  update public.members set date_of_birth = p_date, updated_at = now() where user_id = (select auth.uid());
  get diagnostics n = row_count;
  if n = 0 then raise exception 'not a member' using errcode = '42501'; end if;
end $$;
revoke all on function public.member_set_birthdate(date) from public, anon;
grant execute on function public.member_set_birthdate(date) to authenticated;
