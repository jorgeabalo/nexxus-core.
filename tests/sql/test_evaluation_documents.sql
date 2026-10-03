-- Pruebas SQL: documentos de evaluación subidos (base local desechable, datos solo de prueba).
\set ON_ERROR_STOP 1
insert into auth.users (id, email) values
 ('00000000-0000-0000-0000-0000000000a1','owner@x.test'),
 ('00000000-0000-0000-0000-0000000000a2','mgr@x.test'),
 ('00000000-0000-0000-0000-0000000000a3','trainer@x.test'),
 ('00000000-0000-0000-0000-0000000000b1','eva@x.test'),
 ('00000000-0000-0000-0000-0000000000b2','raul@x.test'),
 ('00000000-0000-0000-0000-0000000000c1','owner@y.test');
insert into tenants (id, slug, name) values ('11000000-0000-0000-0000-00000000000a','tx','Tenant X'),('11000000-0000-0000-0000-00000000000b','ty','Tenant Y');
insert into tenant_users (tenant_id, user_id, role) values
 ('11000000-0000-0000-0000-00000000000a','00000000-0000-0000-0000-0000000000a1','owner'),
 ('11000000-0000-0000-0000-00000000000a','00000000-0000-0000-0000-0000000000a2','manager'),
 ('11000000-0000-0000-0000-00000000000a','00000000-0000-0000-0000-0000000000a3','staff'),
 ('11000000-0000-0000-0000-00000000000b','00000000-0000-0000-0000-0000000000c1','owner');
insert into members (id, tenant_id, first_name, user_id) values
 ('21000000-0000-0000-0000-0000000000b1','11000000-0000-0000-0000-00000000000a','Eva','00000000-0000-0000-0000-0000000000b1'),
 ('21000000-0000-0000-0000-0000000000b2','11000000-0000-0000-0000-00000000000a','Raul','00000000-0000-0000-0000-0000000000b2');
-- el backend (service role) crea los documentos; aquí se simula
insert into evaluation_documents (id, tenant_id, member_id, kind, files, uploaded_by, status, extraction) values
 ('51000000-0000-0000-0000-000000000001','11000000-0000-0000-0000-00000000000a','21000000-0000-0000-0000-0000000000b1','initial',
  '[{"n":1,"path":"x/y/1.jpg","mime":"image/jpeg","size":10,"sha256":"ab"}]','00000000-0000-0000-0000-0000000000b1','needs_review',
  '{"method":"ai_vision","items":[{"question":"¿Lesiones?","answer":null,"status":"illegible"}]}'),
 ('51000000-0000-0000-0000-000000000002','11000000-0000-0000-0000-00000000000a','21000000-0000-0000-0000-0000000000b2','initial',
  '[{"n":1,"path":"x/z/1.pdf","mime":"application/pdf","size":10,"sha256":"cd"}]','00000000-0000-0000-0000-0000000000b2','extracting','{}');

create or replace function pg_temp.as_user(u text) returns void language plpgsql as $$
begin perform set_config('request.jwt.claims', json_build_object('sub', u)::text, false); execute 'set role authenticated'; end $$;
create or replace function pg_temp.reset() returns void language plpgsql as $$
begin execute 'reset role'; perform set_config('request.jwt.claims', '', false); end $$;

-- 1. Aislamiento: cada socio ve solo lo suyo; owner/manager sí; staff raso y otro gimnasio no
select pg_temp.as_user('00000000-0000-0000-0000-0000000000b1');
do $$ begin assert (select count(*) from evaluation_documents) = 1, 'socia ve solo su documento'; end $$;
select pg_temp.as_user('00000000-0000-0000-0000-0000000000a2');
do $$ begin assert (select count(*) from evaluation_documents) = 2, 'manager ve los de su gimnasio'; end $$;
select pg_temp.as_user('00000000-0000-0000-0000-0000000000a3');
do $$ begin assert (select count(*) from evaluation_documents) = 0, 'staff raso no ve datos de salud'; end $$;
select pg_temp.as_user('00000000-0000-0000-0000-0000000000c1');
do $$ begin assert (select count(*) from evaluation_documents) = 0, 'otro gimnasio no ve nada'; end $$;
-- escritura directa bloqueada para usuarios (solo backend)
select pg_temp.as_user('00000000-0000-0000-0000-0000000000b1');
do $$ begin
  update evaluation_documents set status = 'confirmed';
  assert not found, 'el socio no puede modificar el documento directamente';
  begin
    insert into evaluation_documents (tenant_id, member_id, kind, files, uploaded_by) values
      ('11000000-0000-0000-0000-00000000000a','21000000-0000-0000-0000-0000000000b1','initial','[{"n":1}]','00000000-0000-0000-0000-0000000000b1');
    raise exception 'debió fallar';
  exception when insufficient_privilege then null; end;
end $$;

-- 2. Confirmación: validaciones, no se adivina, idempotente
do $$ declare r jsonb; r2 jsonb; ev record; begin
  -- documento ajeno
  begin perform public.member_confirm_document('51000000-0000-0000-0000-000000000002', '{"items":[]}', '41000000-0000-0000-0000-000000000001'); raise exception 'debió fallar';
  exception when insufficient_privilege then null; end;
  -- ilegible con respuesta inventada -> rechazo
  begin perform public.member_confirm_document('51000000-0000-0000-0000-000000000001',
      '{"items":[{"question":"¿Lesiones?","answer":"No","status":"illegible"}]}', '41000000-0000-0000-0000-000000000002'); raise exception 'debió fallar';
  exception when sqlstate '22023' then null; end;
  -- "respondida" sin texto -> rechazo
  begin perform public.member_confirm_document('51000000-0000-0000-0000-000000000001',
      '{"items":[{"question":"¿Lesiones?","answer":"  ","status":"answered"}]}', '41000000-0000-0000-0000-000000000003'); raise exception 'debió fallar';
  exception when sqlstate '22023' then null; end;
  -- respuestas q sin definición cargada -> rechazo (no se inventan preguntas)
  begin perform public.member_confirm_document('51000000-0000-0000-0000-000000000001',
      '{"q":{"p1":"a"},"items":[{"question":"A","answer":"b","status":"answered"}]}', '41000000-0000-0000-0000-000000000004'); raise exception 'debió fallar';
  exception when sqlstate '22023' then null; end;
  -- todo en blanco -> vacío
  begin perform public.member_confirm_document('51000000-0000-0000-0000-000000000001',
      '{"items":[{"question":"A","answer":null,"status":"blank"}]}', '41000000-0000-0000-0000-000000000005'); raise exception 'debió fallar';
  exception when sqlstate '22023' then null; end;

  r := public.member_confirm_document('51000000-0000-0000-0000-000000000001', jsonb_build_object(
        'items', jsonb_build_array(
          jsonb_build_object('ref', null, 'question', '¿Lesiones?', 'answer', 'Rodilla izquierda', 'status', 'answered', 'corrected', true),
          jsonb_build_object('question', '¿Fuma?', 'answer', null, 'status', 'blank'),
          jsonb_build_object('question', 'Medicamentos', 'answer', null, 'status', 'illegible')),
        'body', jsonb_build_object('weight_lb', 150, 'height_in', 63)),
       '41000000-0000-0000-0000-000000000006');
  assert not (r->>'duplicate')::boolean and (r->>'answered')::int = 1, 'confirmada con 1 respuesta';
  assert not (r->>'questionnaire_included')::boolean, 'sin definición: no marca onboarding completo';
  r2 := public.member_confirm_document('51000000-0000-0000-0000-000000000001', '{}', '41000000-0000-0000-0000-000000000006');
  assert (r2->>'duplicate')::boolean and r2->>'evaluation_id' = r->>'evaluation_id', 'reintento idempotente';
  r2 := public.member_confirm_document('51000000-0000-0000-0000-000000000001', '{}', '41000000-0000-0000-0000-000000000007');
  assert (r2->>'duplicate')::boolean and r2->>'evaluation_id' = r->>'evaluation_id', 'documento ya confirmado: mismo resultado';

  select * into ev from member_evaluations where id = (r->>'evaluation_id')::uuid;
  assert ev.answers->>'source' = 'document' and ev.answers->>'document_id' = '51000000-0000-0000-0000-000000000001', 'vinculada al documento';
  assert ev.answers->'items'->1->'answer' = 'null'::jsonb and ev.answers->'items'->1->>'status' = 'blank', 'en blanco sigue en blanco (nunca "No")';
  assert ev.answers->'items'->2->>'status' = 'illegible', 'ilegible se conserva';
  assert (select is_baseline from measurements where evaluation_id = ev.id), 'medidas iniciales = línea base';
  assert (select status from evaluation_documents where id = '51000000-0000-0000-0000-000000000001') = 'confirmed', 'documento confirmado';
  assert (select extraction->'items'->0->>'status' from evaluation_documents where id = '51000000-0000-0000-0000-000000000001') = 'illegible',
         'lo extraído se conserva aparte de lo confirmado';
end $$;
select pg_temp.reset();

-- 3. Inmutabilidad y retención
do $$ begin
  begin update evaluation_documents set extraction = '{}' where id = '51000000-0000-0000-0000-000000000001'; raise exception 'debió fallar';
  exception when insufficient_privilege then null; end;
  begin update evaluation_documents set files = '[{"n":9}]' where id = '51000000-0000-0000-0000-000000000002'; raise exception 'debió fallar';
  exception when insufficient_privilege then null; end;
  begin delete from evaluation_documents where id = '51000000-0000-0000-0000-000000000002'; raise exception 'debió fallar';
  exception when insufficient_privilege then null; end;
  -- el backend sí puede avanzar el estado de un documento no confirmado
  update evaluation_documents set status = 'needs_review', extraction = '{"items":[]}' where id = '51000000-0000-0000-0000-000000000002';
  assert (select count(*) from document_access_log where action = 'confirm') = 1, 'auditoría de la confirmación';
end $$;

-- 4. Documento en extracción no se puede confirmar; auditoría solo visible al owner
insert into evaluation_documents (id, tenant_id, member_id, kind, files, uploaded_by, status) values
 ('51000000-0000-0000-0000-000000000003','11000000-0000-0000-0000-00000000000a','21000000-0000-0000-0000-0000000000b1','reevaluation',
  '[{"n":1,"path":"a","mime":"image/png","size":1,"sha256":"e"}]','00000000-0000-0000-0000-0000000000b1','extracting');
select pg_temp.as_user('00000000-0000-0000-0000-0000000000b1');
do $$ begin
  begin perform public.member_confirm_document('51000000-0000-0000-0000-000000000003',
      '{"items":[{"question":"A","answer":"b","status":"answered"}]}', '41000000-0000-0000-0000-000000000008'); raise exception 'debió fallar';
  exception when sqlstate '22023' then null; end;
  assert (select count(*) from document_access_log) = 0, 'el socio no ve la auditoría';
end $$;
select pg_temp.as_user('00000000-0000-0000-0000-0000000000a2');
do $$ begin assert (select count(*) from document_access_log) = 0, 'manager no ve la auditoría'; end $$;
select pg_temp.as_user('00000000-0000-0000-0000-0000000000a1');
do $$ begin assert (select count(*) from document_access_log) = 1, 'owner ve la auditoría'; end $$;
select pg_temp.reset();

-- 5. Con cuestionario cargado (DEFINICIÓN DE PRUEBA): en papel una obligatoria puede quedar sin respuesta
insert into public.questionnaires (id, tenant_id, version, title, definition, active) values
 ('61000000-0000-0000-0000-000000000001','11000000-0000-0000-0000-00000000000a', 1, '{"es":"PRUEBA"}',
  '{"sections":[{"id":"s1","questions":[{"id":"q1","type":"single","required":true,"options":[{"value":"a"},{"value":"b"}]},{"id":"q2","type":"yesno"}]}]}', true);
update evaluation_documents set status = 'needs_review' where id = '51000000-0000-0000-0000-000000000002';
select pg_temp.as_user('00000000-0000-0000-0000-0000000000b2');
do $$ declare r jsonb; begin
  begin perform public.member_confirm_document('51000000-0000-0000-0000-000000000002', '{"q":{"q1":"z"}}', '41000000-0000-0000-0000-000000000009'); raise exception 'debió fallar';
  exception when sqlstate '22023' then null; end;
  r := public.member_confirm_document('51000000-0000-0000-0000-000000000002', '{"q":{"q2":true}}', '41000000-0000-0000-0000-000000000010');
  assert (r->>'questionnaire_included')::boolean, 'con definición';
  assert (select onboarding_completed from members where id = '21000000-0000-0000-0000-0000000000b2'), 'onboarding completo';
end $$;
select pg_temp.reset();
select 'ALL DOCUMENT SQL TESTS PASSED' as result;
