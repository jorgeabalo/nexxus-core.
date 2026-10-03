-- Cuestionario en papel / PDF: el socio sube el documento original (PDF rellenable,
-- escaneo o fotografías), el servidor extrae las respuestas y el socio las revisa
-- y corrige antes de que se guarden como datos confirmados.
--
-- * El original se guarda en un bucket PRIVADO de Supabase Storage sin políticas
--   para anon/authenticated: solo el backend (service role) lo lee o escribe, y lo
--   entrega únicamente al propio socio o a owner/manager de su gimnasio.
-- * El documento y sus archivos son inmutables; una vez confirmado no cambia nada.
-- * Lo que se extrajo (extraction) y lo que el socio confirmó (member_evaluations)
--   se guardan por separado: se puede auditar qué se corrigió.
-- * Nada se adivina: una respuesta ilegible o en blanco queda como tal (nunca "No").

-- ---------- bucket privado ----------
do $$ begin
  if exists (select 1 from pg_namespace where nspname = 'storage') then
    insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
    values ('evaluation-documents', 'evaluation-documents', false, 15728640,
            array['application/pdf','image/jpeg','image/png','image/webp','image/heic','image/heif'])
    on conflict (id) do update set public = false, file_size_limit = excluded.file_size_limit,
                                   allowed_mime_types = excluded.allowed_mime_types;
  end if;
end $$;

-- ---------- documentos ----------
create table if not exists public.evaluation_documents (
  id                    uuid primary key default gen_random_uuid(),
  tenant_id             uuid not null,
  member_id             uuid not null,
  kind                  text not null check (kind in ('initial','reevaluation')),
  questionnaire_id      uuid,
  questionnaire_version integer,
  files                 jsonb not null check (jsonb_typeof(files) = 'array' and jsonb_array_length(files) between 1 and 10),
  form_ref_ok           boolean not null default false,   -- PDF rellenable generado para este socio (firma verificada)
  uploaded_by           uuid not null,
  uploaded_via          text not null default 'member' check (uploaded_via in ('member','staff')),
  status                text not null default 'uploaded'
                          check (status in ('uploaded','extracting','needs_review','confirmed','failed')),
  extraction            jsonb not null default '{}'::jsonb check (pg_column_size(extraction) < 262144),
  extraction_error      text,
  evaluation_id         uuid unique references public.member_evaluations(id) on delete restrict,
  created_at            timestamptz not null default now(),
  extracted_at          timestamptz,
  confirmed_at          timestamptz,
  confirmed_by          uuid,
  foreign key (member_id, tenant_id) references public.members(id, tenant_id) on delete cascade,
  foreign key (questionnaire_id, tenant_id) references public.questionnaires(id, tenant_id) on delete restrict
);
create index if not exists evaluation_documents_member on public.evaluation_documents (member_id, created_at);
alter table public.evaluation_documents enable row level security;
-- Datos de salud: solo owner/manager del gimnasio (no todo el staff) y el propio socio.
create policy evaluation_documents_staff_select on public.evaluation_documents for select to authenticated
  using (private.has_tenant_role(tenant_id, array['owner','manager']));
create policy evaluation_documents_self_select on public.evaluation_documents for select to authenticated
  using (member_id in (select private.my_member_ids()));
-- Sin políticas de escritura: solo el backend (service role) y member_confirm_document.

create or replace function private.evaluation_documents_guard()
returns trigger language plpgsql security definer set search_path = '' as $$
begin
  if tg_op = 'DELETE' then
    raise exception 'evaluation documents are retained' using errcode = '42501';
  end if;
  if old.status = 'confirmed' then
    raise exception 'confirmed document is immutable' using errcode = '42501';
  end if;
  if new.files is distinct from old.files or new.member_id <> old.member_id or new.tenant_id <> old.tenant_id
     or new.uploaded_by <> old.uploaded_by or new.created_at <> old.created_at or new.kind <> old.kind then
    raise exception 'original document is immutable' using errcode = '42501';
  end if;
  return new;
end $$;
drop trigger if exists evaluation_documents_guard on public.evaluation_documents;
create trigger evaluation_documents_guard before update or delete on public.evaluation_documents
  for each row execute function private.evaluation_documents_guard();

-- ---------- registro de accesos (auditoría) ----------
create table if not exists public.document_access_log (
  id           bigint generated always as identity primary key,
  tenant_id    uuid not null,
  member_id    uuid not null,
  document_id  uuid references public.evaluation_documents(id) on delete set null,
  actor_user_id uuid,
  actor_role   text not null check (actor_role in ('member','owner','manager','system')),
  action       text not null check (action in ('upload','view_file','extract','confirm','download_form','download_pdf')),
  file_n       integer,
  detail       text check (length(detail) <= 300),
  created_at   timestamptz not null default now()
);
create index if not exists document_access_log_member on public.document_access_log (member_id, created_at);
alter table public.document_access_log enable row level security;
create policy document_access_log_owner_select on public.document_access_log for select to authenticated
  using (private.has_tenant_role(tenant_id, array['owner']));

-- ---------- confirmar respuestas revisadas ----------
-- p_payload = { items: [{ref, question, answer, status, corrected}], q: {...}, body: {...} }
--   status: answered | blank | illegible.  blank/illegible => answer null (no se adivina).
create or replace function public.member_confirm_document(p_document uuid, p_payload jsonb, p_client_ref uuid)
returns jsonb language plpgsql volatile security definer set search_path = '' as $$
declare
  m public.members%rowtype; doc public.evaluation_documents%rowtype; q public.questionnaires%rowtype;
  e public.member_evaluations%rowtype; tz text; d date; eval_id uuid; meas_id uuid; has_baseline boolean;
  next_due date; body jsonb; items jsonb; it jsonb; qa jsonb; with_q boolean; n_items int := 0; n_answered int := 0;
begin
  select * into m from public.members where user_id = (select auth.uid()) limit 1;
  if m.id is null then raise exception 'not a member' using errcode = '42501'; end if;
  if p_client_ref is null then raise exception 'client_ref required' using errcode = '22023'; end if;

  select * into e from public.member_evaluations where client_ref = p_client_ref;
  if e.id is not null then
    if e.member_id <> m.id then raise exception 'not allowed' using errcode = '42501'; end if;
    return jsonb_build_object('evaluation_id', e.id, 'next_due_date', e.next_due_date, 'duplicate', true);
  end if;

  select * into doc from public.evaluation_documents where id = p_document and member_id = m.id for update;
  if doc.id is null then raise exception 'not allowed' using errcode = '42501'; end if;
  if doc.status = 'confirmed' then
    select * into e from public.member_evaluations where id = doc.evaluation_id;
    return jsonb_build_object('evaluation_id', e.id, 'next_due_date', e.next_due_date, 'duplicate', true);
  end if;
  if doc.status not in ('needs_review','failed') then
    raise exception 'document not ready for review' using errcode = '22023';
  end if;
  if doc.kind = 'initial' and exists (select 1 from public.member_evaluations
                                       where member_id = m.id and kind = 'initial' and status = 'submitted') then
    raise exception 'initial evaluation already submitted' using errcode = '22023';
  end if;
  if jsonb_typeof(coalesce(p_payload, '{}'::jsonb)) <> 'object' then raise exception 'invalid payload' using errcode = '22023'; end if;

  -- Respuestas del cuestionario cargado (si existe): se validan tipos/opciones, pero en papel
  -- una respuesta obligatoria puede venir en blanco o ilegible: no se exige (p_final = false).
  if doc.questionnaire_id is not null then
    select * into q from public.questionnaires where id = doc.questionnaire_id;
  else
    select * into q from public.questionnaires where tenant_id = m.tenant_id and code = 'onboarding' and active limit 1;
  end if;
  with_q := q.id is not null;
  qa := coalesce(p_payload->'q', '{}'::jsonb);
  if with_q then
    perform private.validate_answers(q.definition, qa, false);
  elsif qa <> '{}'::jsonb then
    raise exception 'no questionnaire definition loaded' using errcode = '22023';
  end if;

  items := coalesce(p_payload->'items', '[]'::jsonb);
  if jsonb_typeof(items) <> 'array' or jsonb_array_length(items) > 300 then
    raise exception 'invalid items' using errcode = '22023';
  end if;
  for it in select * from jsonb_array_elements(items) loop
    n_items := n_items + 1;
    if jsonb_typeof(it) <> 'object'
       or coalesce(it->>'status', '') not in ('answered','blank','illegible')
       or length(coalesce(it->>'question', '')) not between 1 and 500
       or length(coalesce(it->>'answer', '')) > 2000
       or (it->>'status' = 'answered' and length(btrim(coalesce(it->>'answer', ''))) = 0)
       or (it->>'status' <> 'answered' and it ? 'answer' and it->'answer' <> 'null'::jsonb) then
      raise exception 'invalid item %', n_items using errcode = '22023';
    end if;
    if it->>'status' = 'answered' then n_answered := n_answered + 1; end if;
  end loop;

  select timezone into tz from public.tenants where id = m.tenant_id;
  d := (now() at time zone tz)::date;
  next_due := d + 90;
  eval_id := gen_random_uuid();
  body := p_payload->'body';

  if body is not null and jsonb_typeof(body) = 'object' and coalesce(
      private.num_or_null(body,'weight_lb'), private.num_or_null(body,'height_in'),
      private.num_or_null(body,'waist_in'), private.num_or_null(body,'left_arm_in'),
      private.num_or_null(body,'right_arm_in'), private.num_or_null(body,'left_leg_in'),
      private.num_or_null(body,'right_leg_in')) is not null then
    select exists (select 1 from public.measurements where member_id = m.id and is_baseline) into has_baseline;
    insert into public.measurements (tenant_id, member_id, measurement_date, source, is_baseline, evaluation_id,
           weight_lb, height_in, waist_in, left_arm_in, right_arm_in, left_leg_in, right_leg_in, notes)
    values (m.tenant_id, m.id, d, 'member', (doc.kind = 'initial' and not has_baseline), eval_id,
            private.num_or_null(body,'weight_lb'), private.num_or_null(body,'height_in'),
            private.num_or_null(body,'waist_in'), private.num_or_null(body,'left_arm_in'),
            private.num_or_null(body,'right_arm_in'), private.num_or_null(body,'left_leg_in'),
            private.num_or_null(body,'right_leg_in'), 'From uploaded questionnaire (reviewed by member)')
    returning id into meas_id;
  end if;

  if n_answered = 0 and qa = '{}'::jsonb and meas_id is null then
    raise exception 'empty evaluation' using errcode = '22023';
  end if;

  delete from public.member_evaluations where member_id = m.id and kind = doc.kind and status = 'draft';
  insert into public.member_evaluations (id, tenant_id, member_id, questionnaire_id, questionnaire_version, kind,
         status, answers, client_ref, measurement_id, submitted_at, next_due_date)
  values (eval_id, m.tenant_id, m.id, q.id, q.version, doc.kind, 'submitted',
          jsonb_build_object('source', 'document', 'document_id', doc.id, 'q', qa,
                             'items', items, 'body', coalesce(body, '{}'::jsonb), 'progress', '[]'::jsonb),
          p_client_ref, meas_id, now(), next_due);

  update public.evaluation_documents
     set status = 'confirmed', evaluation_id = eval_id, confirmed_at = now(), confirmed_by = (select auth.uid())
   where id = doc.id;

  update public.members
     set onboarding_completed = (coalesce(onboarding_completed, false) or with_q),
         next_evaluation_due = next_due, updated_at = now()
   where id = m.id;

  insert into public.document_access_log (tenant_id, member_id, document_id, actor_user_id, actor_role, action)
  values (m.tenant_id, m.id, doc.id, (select auth.uid()), 'member', 'confirm');

  insert into public.alerts (tenant_id, type, severity, title, detail, member_id)
  values (m.tenant_id, 'other', 'info',
          case doc.kind when 'initial' then 'Initial questionnaire uploaded: ' else 'Re-evaluation uploaded: ' end
            || trim(concat_ws(' ', m.first_name, m.last_name)),
          'Member Portal · document reviewed by member · ' || n_answered || ' answers'
            || case when with_q then ' · questionnaire v' || q.version else '' end,
          m.id);

  return jsonb_build_object('evaluation_id', eval_id, 'measurement_id', meas_id, 'next_due_date', next_due,
                            'answered', n_answered, 'questionnaire_included', with_q, 'duplicate', false);
end $$;

revoke all on function public.member_confirm_document(uuid, jsonb, uuid) from public, anon;
grant execute on function public.member_confirm_document(uuid, jsonb, uuid) to authenticated;
revoke all on function private.evaluation_documents_guard() from public, anon, authenticated;
