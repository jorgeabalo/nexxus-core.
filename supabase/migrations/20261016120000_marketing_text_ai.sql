-- AITA Marketing — redactor con IA (texto) y versiones por plataforma.
--
-- platform_variants: el texto y la medida del visual de cada red elegida, p. ej.
--   {"instagram": {"text": "…", "aspect": "4:5", "limit": 2200}}. Se recalcula al escribir con IA.
-- marketing_text_generations: registro de cada redacción con IA (para el límite diario y el coste).
-- No guarda el texto ni el prompt: solo quién, cuándo, con qué modelo y cuántos tokens.
alter table public.marketing_content
  add column if not exists platform_variants jsonb not null default '{}'::jsonb;
alter table public.marketing_content
  drop constraint if exists marketing_content_platform_variants_check;
alter table public.marketing_content
  add constraint marketing_content_platform_variants_check
  check (jsonb_typeof(platform_variants) = 'object' and length(platform_variants::text) <= 40000);

create table if not exists public.marketing_text_generations (
  id             bigint generated always as identity primary key,
  tenant_id      uuid not null references public.tenants(id) on delete cascade,
  content_id     uuid references public.marketing_content(id) on delete set null,
  actor_id       uuid not null,
  model          text not null check (length(model) between 1 and 100),
  status         text not null check (status in ('succeeded', 'failed', 'rejected')),
  input_tokens   integer not null default 0 check (input_tokens >= 0),
  output_tokens  integer not null default 0 check (output_tokens >= 0),
  created_at     timestamptz not null default now()
);
create index if not exists marketing_text_generations_tenant_idx
  on public.marketing_text_generations(tenant_id, created_at);

alter table public.marketing_text_generations enable row level security;
drop policy if exists marketing_text_generations_read on public.marketing_text_generations;
create policy marketing_text_generations_read on public.marketing_text_generations
  for select to authenticated using (private.has_tenant_role(tenant_id, array['owner','manager']));

-- Solo el backend (service role) escribe; los paneles solo pueden leer su propio tenant.
revoke all privileges on table public.marketing_text_generations from public, anon, authenticated;
grant select on table public.marketing_text_generations to authenticated;
