-- Household utility registration only. No provider credentials or physical control.
begin;
create table public.domus_utilities (
 id uuid primary key default gen_random_uuid(),
 home_id uuid not null references public.domus_homes(id) on delete cascade,
 kind text not null check(kind in ('electricity','water','gas','internet','phone')),
 provider text not null check(length(btrim(provider)) between 1 and 100),
 country_code text not null check(country_code ~ '^[A-Z]{2}$'),
 currency text not null check(currency ~ '^[A-Z]{3}$'),
 source text not null default 'invoice' check(source in ('invoice','meter','provider','router')),
 connection_status text not null default 'pending' check(connection_status in ('pending','connected','attention')),
 created_at timestamptz not null default now(),
 unique(home_id,kind)
);
alter table public.domus_utilities enable row level security;
revoke all on public.domus_utilities from anon, authenticated;
grant select on public.domus_utilities to authenticated;
grant insert(home_id,kind,provider,country_code,currency,source) on public.domus_utilities to authenticated;
grant update(provider,country_code,currency,source) on public.domus_utilities to authenticated;
create policy domus_utilities_read on public.domus_utilities for select to authenticated using(private.domus_has_role(home_id,array['owner','member','guest']));
create policy domus_utilities_insert on public.domus_utilities for insert to authenticated with check(private.domus_has_role(home_id,array['owner']));
create policy domus_utilities_update on public.domus_utilities for update to authenticated using(private.domus_has_role(home_id,array['owner'])) with check(private.domus_has_role(home_id,array['owner']));
commit;
