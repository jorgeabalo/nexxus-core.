-- =====================================================================
-- 6) Mueve los helpers SECURITY DEFINER fuera del esquema expuesto por la
--    API REST (public) a un esquema "private". Las políticas RLS siguen
--    funcionando (referencian la función por OID). Recomendación del
--    security advisor de Supabase.
-- =====================================================================
create schema if not exists private;
revoke all on schema private from public, anon;
grant usage on schema private to authenticated;
alter function public.user_tenant_ids() set schema private;
alter function public.has_tenant_role(uuid, text[]) set schema private;
alter function public.link_invites_for_user(uuid, text) set schema private;

create or replace function public.on_auth_user_created()
returns trigger language plpgsql security definer set search_path = '' as $$
begin
  if new.email is not null then
    perform private.link_invites_for_user(new.id, new.email);
  end if;
  return new;
end $$;

create or replace function public.on_tenant_invite_created()
returns trigger language plpgsql security definer set search_path = '' as $$
declare u uuid;
begin
  select id into u from auth.users where lower(email) = new.email limit 1;
  if u is not null then
    perform private.link_invites_for_user(u, new.email);
  end if;
  return new;
end $$;
revoke all on function public.on_auth_user_created()     from public, anon, authenticated;
revoke all on function public.on_tenant_invite_created() from public, anon, authenticated;
