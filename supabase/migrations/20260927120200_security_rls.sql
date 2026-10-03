-- =====================================================================
-- 3) Seguridad: helpers de tenant, alta por invitación y RLS.
--    Regla: un usuario solo lee/modifica filas de los tenants a los que
--    pertenece (tenant_users). Borrar: solo owner/manager.
--    El backend (Claudia) escribe con service_role, que ignora RLS.
-- =====================================================================

-- ---------- helpers (security definer para evitar recursión de RLS) ----------
create or replace function public.user_tenant_ids()
returns setof uuid language sql stable security definer set search_path = '' as $$
  select tu.tenant_id from public.tenant_users tu
  where tu.user_id = (select auth.uid()) and tu.active
$$;

create or replace function public.has_tenant_role(p_tenant uuid, p_roles text[])
returns boolean language sql stable security definer set search_path = '' as $$
  select exists (
    select 1 from public.tenant_users tu
    where tu.user_id = (select auth.uid()) and tu.active
      and tu.tenant_id = p_tenant and tu.role = any (p_roles)
  )
$$;

revoke all on function public.user_tenant_ids()                from public, anon;
revoke all on function public.has_tenant_role(uuid, text[])   from public, anon;
grant execute on function public.user_tenant_ids()              to authenticated;
grant execute on function public.has_tenant_role(uuid, text[]) to authenticated;

-- ---------- alta por invitación ----------
-- El owner registra el email en tenant_invites; cuando esa persona acepta la
-- invitación de Supabase Auth (o ya existía), queda vinculada con su rol.
create or replace function public.link_invites_for_user(p_user uuid, p_email text)
returns void language plpgsql security definer set search_path = '' as $$
begin
  insert into public.tenant_users (tenant_id, user_id, role, staff_id)
  select i.tenant_id, p_user, i.role, i.staff_id
  from public.tenant_invites i
  where i.email = lower(p_email) and i.accepted_at is null
  on conflict (tenant_id, user_id) do nothing;

  update public.tenant_invites set accepted_at = now()
  where email = lower(p_email) and accepted_at is null;
end $$;
revoke all on function public.link_invites_for_user(uuid, text) from public, anon, authenticated;

create or replace function public.on_auth_user_created()
returns trigger language plpgsql security definer set search_path = '' as $$
begin
  if new.email is not null then
    perform public.link_invites_for_user(new.id, new.email);
  end if;
  return new;
end $$;
drop trigger if exists aita_on_auth_user_created on auth.users;
create trigger aita_on_auth_user_created after insert on auth.users
  for each row execute function public.on_auth_user_created();

create or replace function public.on_tenant_invite_created()
returns trigger language plpgsql security definer set search_path = '' as $$
declare u uuid;
begin
  select id into u from auth.users where lower(email) = new.email limit 1;
  if u is not null then
    perform public.link_invites_for_user(u, new.email);
  end if;
  return new;
end $$;
drop trigger if exists tenant_invites_link on public.tenant_invites;
create trigger tenant_invites_link after insert on public.tenant_invites
  for each row execute function public.on_tenant_invite_created();

revoke all on function public.on_auth_user_created()     from public, anon, authenticated;
revoke all on function public.on_tenant_invite_created() from public, anon, authenticated;

-- ---------- RLS ----------
alter table public.tenants        enable row level security;
alter table public.tenant_users   enable row level security;
alter table public.tenant_invites enable row level security;
alter table public.calls          enable row level security;
alter table public.leads          enable row level security;
alter table public.alerts         enable row level security;

-- tenants: ver los propios; editar solo owner
create policy tenants_select on public.tenants for select to authenticated
  using (id in (select public.user_tenant_ids()));
create policy tenants_update on public.tenants for update to authenticated
  using (public.has_tenant_role(id, array['owner'])) with check (public.has_tenant_role(id, array['owner']));

-- tenant_users: cada uno ve su equipo; solo owner gestiona
create policy tenant_users_select on public.tenant_users for select to authenticated
  using (tenant_id in (select public.user_tenant_ids()));
create policy tenant_users_write on public.tenant_users for all to authenticated
  using (public.has_tenant_role(tenant_id, array['owner'])) with check (public.has_tenant_role(tenant_id, array['owner']));

-- tenant_invites: owner/manager ven; solo owner crea/borra
create policy tenant_invites_select on public.tenant_invites for select to authenticated
  using (public.has_tenant_role(tenant_id, array['owner','manager']));
create policy tenant_invites_write on public.tenant_invites for all to authenticated
  using (public.has_tenant_role(tenant_id, array['owner'])) with check (public.has_tenant_role(tenant_id, array['owner']));

-- tablas de negocio: mismo patrón para todas
do $$
declare t text;
begin
  foreach t in array array['members','staff','services','check_ins','appointments','payments',
                           'messages','measurements','member_onboarding','calls','leads','alerts']
  loop
    execute format('alter table public.%I enable row level security', t);
    execute format('drop policy if exists %I on public.%I', t || '_select', t);
    execute format('drop policy if exists %I on public.%I', t || '_insert', t);
    execute format('drop policy if exists %I on public.%I', t || '_update', t);
    execute format('drop policy if exists %I on public.%I', t || '_delete', t);
    execute format($p$create policy %I on public.%I for select to authenticated
                     using (tenant_id in (select public.user_tenant_ids()))$p$, t || '_select', t);
    execute format($p$create policy %I on public.%I for insert to authenticated
                     with check (tenant_id in (select public.user_tenant_ids()))$p$, t || '_insert', t);
    execute format($p$create policy %I on public.%I for update to authenticated
                     using (tenant_id in (select public.user_tenant_ids()))
                     with check (tenant_id in (select public.user_tenant_ids()))$p$, t || '_update', t);
    execute format($p$create policy %I on public.%I for delete to authenticated
                     using (public.has_tenant_role(tenant_id, array['owner','manager']))$p$, t || '_delete', t);
    -- el rol anónimo no necesita ningún acceso a estas tablas
    execute format('revoke all on public.%I from anon', t);
  end loop;
end $$;

revoke all on public.tenants, public.tenant_users, public.tenant_invites from anon;
