-- Pruebas SQL (base local desechable). Datos creados solo aquí.
\set ON_ERROR_STOP 1
insert into auth.users (id, email) values
 ('00000000-0000-0000-0000-00000000000a','owner@a.test'),
 ('00000000-0000-0000-0000-00000000000b','ana@a.test'),
 ('00000000-0000-0000-0000-00000000000c','luis@a.test'),
 ('00000000-0000-0000-0000-00000000000d','owner@b.test'),
 ('00000000-0000-0000-0000-00000000000e','zoe@b.test');
insert into tenants (id, slug, name) values ('10000000-0000-0000-0000-00000000000a','ta','Tenant A'),('10000000-0000-0000-0000-00000000000b','tb','Tenant B');
insert into staff (id, tenant_id, first_name) values ('30000000-0000-0000-0000-00000000000a','10000000-0000-0000-0000-00000000000a','Edgar');
insert into tenant_users (tenant_id, user_id, role, staff_id) values
 ('10000000-0000-0000-0000-00000000000a','00000000-0000-0000-0000-00000000000a','owner','30000000-0000-0000-0000-00000000000a'),
 ('10000000-0000-0000-0000-00000000000b','00000000-0000-0000-0000-00000000000d','owner',null);
insert into members (id, tenant_id, first_name, user_id, gender) values
 ('20000000-0000-0000-0000-00000000000b','10000000-0000-0000-0000-00000000000a','Ana','00000000-0000-0000-0000-00000000000b','F'),
 ('20000000-0000-0000-0000-00000000000c','10000000-0000-0000-0000-00000000000a','Luis','00000000-0000-0000-0000-00000000000c',null),
 ('20000000-0000-0000-0000-00000000000e','10000000-0000-0000-0000-00000000000b','Zoe','00000000-0000-0000-0000-00000000000e','M');

create or replace function pg_temp.as_user(u text) returns void language plpgsql as $$
begin perform set_config('request.jwt.claims', json_build_object('sub', u)::text, false); execute 'set role authenticated'; end $$;
create or replace function pg_temp.reset() returns void language plpgsql as $$
begin execute 'reset role'; perform set_config('request.jwt.claims', '', false); end $$;

-- ===== 1. Sin cuestionario cargado: evaluación inicial solo con medidas e indicadores =====
select pg_temp.as_user('00000000-0000-0000-0000-00000000000b');
do $$ declare r jsonb; p jsonb; begin
  p := public.member_portal();
  assert p->'evaluation'->'questionnaire' = 'null'::jsonb, 'no debe haber cuestionario';
  assert p->'member'->>'sex' = 'female', 'sexo F -> female';
  assert jsonb_array_length(p->'measurements') = 0 and jsonb_array_length(p->'progress') = 0, 'sin datos inventados';

  perform public.member_save_evaluation_draft('initial', '{"body":{"weight_lb":160},"step":1}'::jsonb, 1);
  assert (public.member_portal()->'evaluation'->'drafts'->'initial'->>'step')::int = 1, 'borrador guardado';

  r := public.member_submit_evaluation('initial', jsonb_build_object(
        'body', jsonb_build_object('weight_lb', 160, 'waist_in', 34, 'left_arm_in', 12, 'left_leg_in', 21, 'height_in', 64),
        'progress', jsonb_build_array(
          jsonb_build_object('category','strength','metric','exercise','exercise','Leg press','value',90,'reps',10,'conditions','Máquina 2'),
          jsonb_build_object('category','endurance','metric','walk','value',15),
          jsonb_build_object('category','mobility','metric','chair_rise','value',2),
          jsonb_build_object('category','wellbeing','metric','energy','value',2))),
       '40000000-0000-0000-0000-000000000001');
  assert (r->>'duplicate')::boolean = false and (r->>'items')::int = 4, 'evaluación guardada';
  -- reintento con el mismo client_ref: no duplica
  r := public.member_submit_evaluation('initial', '{}'::jsonb, '40000000-0000-0000-0000-000000000001');
  assert (r->>'duplicate')::boolean = true, 'reintento idempotente';
  p := public.member_portal();
  assert jsonb_array_length(p->'evaluation'->'submitted') = 1, 'una sola evaluación';
  assert p->'evaluation'->'drafts' = '{}'::jsonb, 'borrador eliminado al enviar';
  assert (p->'measurements'->0->>'is_baseline')::boolean, 'medida inicial = línea base';
  assert p->'measurements'->0->>'source' = 'member' and p->'measurements'->0->>'recorded_by_name' = 'Ana', 'declarada por la socia';
  assert (select count(*) from jsonb_array_elements(p->'progress') x where (x->>'is_baseline')::boolean) = 4, 'indicadores base';
  assert p->'member'->>'next_evaluation_due' = ((now() at time zone 'America/Chicago')::date + 90)::text, 'próxima a 90 días';
  assert (p->'member'->>'onboarding_completed')::boolean = false, 'onboarding NO completo sin el cuestionario original';
  -- segunda inicial: rechazada
  begin
    perform public.member_submit_evaluation('initial', '{"body":{"weight_lb":150}}'::jsonb, '40000000-0000-0000-0000-000000000002');
    raise exception 'debió fallar';
  exception when sqlstate '22023' then null; end;
end $$;

-- ===== 2. Actualización breve y reevaluación: la línea base no cambia =====
do $$ declare p jsonb; r jsonb; begin
  perform public.member_log_progress('{"weight_lb":158,"waist_in":33.5}'::jsonb,
     '[{"category":"strength","metric":"exercise","exercise":"leg  press","value":90,"reps":13},
       {"category":"endurance","metric":"walk","value":25}]'::jsonb);
  r := public.member_submit_evaluation('reevaluation', '{"body":{"weight_lb":157},"progress":[{"category":"wellbeing","metric":"energy","value":4}]}'::jsonb,
       '40000000-0000-0000-0000-000000000003');
  p := public.member_portal();
  assert jsonb_array_length(p->'evaluation'->'submitted') = 2, 'inicial y trimestral por separado';
  assert (select count(*) from jsonb_array_elements(p->'measurements') x where (x->>'is_baseline')::boolean) = 1, 'una sola línea base corporal';
  assert (select (x->>'weight_lb')::numeric from jsonb_array_elements(p->'measurements') x where (x->>'is_baseline')::boolean) = 160, 'línea base intacta';
  assert (select count(*) from jsonb_array_elements(p->'progress') x where x->>'metric_key' = 'strength:exercise:leg press') = 2, 'mismo ejercicio normalizado';
  assert (select count(*) from jsonb_array_elements(p->'progress') x where (x->>'is_baseline')::boolean) = 4, 'bases de indicadores intactas';
end $$;

-- ===== 3. El socio no puede escribir ni leer lo de otros =====
do $$ declare n int; begin
  begin insert into public.measurements (tenant_id, member_id, weight_lb) values ('10000000-0000-0000-0000-00000000000a','20000000-0000-0000-0000-00000000000c',100); raise exception 'debió fallar';
  exception when insufficient_privilege then null; end;
  begin insert into public.progress_entries (tenant_id, member_id, category, metric, value, metric_key) values ('10000000-0000-0000-0000-00000000000a','20000000-0000-0000-0000-00000000000b','endurance','walk',99,'x'); raise exception 'debió fallar';
  exception when insufficient_privilege then null; end;
  begin update public.member_evaluations set answers = '{}' ; exception when insufficient_privilege then null; end;
  select count(*) into n from public.member_evaluations; assert n = 2, 'solo sus evaluaciones';
  select count(*) into n from public.progress_entries where member_id <> '20000000-0000-0000-0000-00000000000b'; assert n = 0, 'no ve indicadores ajenos';
  select count(*) into n from public.sms_messages; assert n = 0, 'socio no ve SMS';
end $$;
select pg_temp.reset();

-- ===== 4. Staff: registra medición tomada por el staff; no puede alterar la línea base =====
select pg_temp.as_user('00000000-0000-0000-0000-00000000000a');
do $$ declare n int; nm text; src text; begin
  insert into public.measurements (tenant_id, member_id, weight_lb, source) values ('10000000-0000-0000-0000-00000000000a','20000000-0000-0000-0000-00000000000b',156,'staff');
  select recorded_by_name, source into nm, src from public.measurements where weight_lb = 156;
  assert nm = 'Edgar' and src = 'staff', 'registrada por Edgar (staff)';
  begin update public.measurements set weight_lb = 1 where is_baseline; raise exception 'debió fallar';
  exception when insufficient_privilege then null; end;
  begin delete from public.measurements where is_baseline; raise exception 'debió fallar';
  exception when insufficient_privilege then null; end;
  begin insert into public.measurements (tenant_id, member_id, weight_lb, is_baseline) values ('10000000-0000-0000-0000-00000000000a','20000000-0000-0000-0000-00000000000b',150,true); raise exception 'debió fallar';
  exception when unique_violation then null; end;
  insert into public.progress_entries (tenant_id, member_id, category, metric, value, metric_key, conditions)
    values ('10000000-0000-0000-0000-00000000000a','20000000-0000-0000-0000-00000000000b','mobility','chair_stand_30s',11,'x','Silla estándar, brazos cruzados');
  select count(*) into n from public.member_evaluations where member_id = '20000000-0000-0000-0000-00000000000b'; assert n = 2, 'staff ve historial del socio';
  select count(*) into n from public.members where tenant_id = '10000000-0000-0000-0000-00000000000b'; assert n = 0, 'no ve otro gym';
  begin insert into public.progress_entries (tenant_id, member_id, category, metric, value, metric_key) values ('10000000-0000-0000-0000-00000000000b','20000000-0000-0000-0000-00000000000e','endurance','walk',10,'x'); raise exception 'debió fallar';
  exception when insufficient_privilege then null; end;
end $$;
select pg_temp.reset();

-- ===== 5. Otro gym aislado =====
select pg_temp.as_user('00000000-0000-0000-0000-00000000000d');
do $$ declare n int; begin
  select count(*) into n from public.measurements; assert n = 0, 'owner B no ve mediciones de A';
  select count(*) into n from public.member_evaluations; assert n = 0, 'owner B no ve evaluaciones de A';
  select count(*) into n from public.progress_entries; assert n = 0, 'owner B no ve indicadores de A';
end $$;
select pg_temp.reset();

-- ===== 6. Con cuestionario cargado (DEFINICIÓN DE PRUEBA, no es la de Golden Age) =====
insert into public.questionnaires (tenant_id, version, title, definition, active) values
 ('10000000-0000-0000-0000-00000000000a', 1, '{"es":"PRUEBA"}',
  '{"sections":[{"id":"s1","questions":[{"id":"q1","type":"single","required":true,"options":[{"value":"a"},{"value":"b"}]},{"id":"q2","type":"number","min":0,"max":10}]}]}', true);
select pg_temp.as_user('00000000-0000-0000-0000-00000000000c');
do $$ declare r jsonb; begin
  begin perform public.member_save_evaluation_draft('initial', '{"q":{"zz":1}}'::jsonb, 0); raise exception 'debió fallar';
  exception when sqlstate '22023' then null; end;
  begin perform public.member_submit_evaluation('initial', '{"q":{"q2":3}}'::jsonb, '40000000-0000-0000-0000-000000000010'); raise exception 'debió fallar (falta requerida)';
  exception when sqlstate '22023' then null; end;
  begin perform public.member_submit_evaluation('initial', '{"q":{"q1":"c"}}'::jsonb, '40000000-0000-0000-0000-000000000011'); raise exception 'debió fallar (opción inválida)';
  exception when sqlstate '22023' then null; end;
  r := public.member_submit_evaluation('initial', '{"q":{"q1":"a","q2":3}}'::jsonb, '40000000-0000-0000-0000-000000000012');
  assert (r->>'questionnaire_included')::boolean, 'incluye cuestionario';
  assert (public.member_portal()->'member'->>'onboarding_completed')::boolean, 'onboarding completo solo con cuestionario';
  assert public.member_portal()->'member'->>'sex' is null, 'sin sexo registrado: no se infiere';
  perform public.member_set_sex('male');
  assert public.member_portal()->'member'->>'sex' = 'male', 'socio completa su dato';
  -- intentar reutilizar el client_ref de otra socia
  begin perform public.member_submit_evaluation('reevaluation', '{}'::jsonb, '40000000-0000-0000-0000-000000000001'); raise exception 'debió fallar';
  exception when insufficient_privilege then null; end;
end $$;
select pg_temp.reset();
-- una versión ya usada no se puede editar
do $$ begin
  begin update public.questionnaires set definition = '{"sections":[]}' where version = 1; raise exception 'debió fallar';
  exception when insufficient_privilege then null; end;
end $$;
select 'ALL SQL TESTS PASSED' as result;
