-- Apply manually after approval. No chat text or audio is stored.
begin;
create table public.mini_ai_quota (
  user_id uuid primary key references auth.users(id) on delete cascade,
  minute_start timestamptz not null,
  minute_count integer not null,
  day_start date not null,
  day_count integer not null
);
alter table public.mini_ai_quota enable row level security;
revoke all on public.mini_ai_quota from public, anon, authenticated;

-- Serialize concurrent requests per user, across Railway workers/replicas.
-- Fixed budget: 6 attempts/minute, 100/day UTC, including provider failures.
create function public.mini_ai_take_quota(p_user uuid)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare
  q public.mini_ai_quota%rowtype;
  t timestamptz := clock_timestamp();
  d date := (t at time zone 'UTC')::date;
begin
  insert into public.mini_ai_quota values(p_user, t, 0, d, 0)
    on conflict (user_id) do nothing;
  select * into q from public.mini_ai_quota where user_id = p_user for update;
  if q.minute_start <= t - interval '1 minute' then
    q.minute_start := t; q.minute_count := 0;
  end if;
  if q.day_start <> d then q.day_start := d; q.day_count := 0; end if;
  if q.minute_count >= 6 or q.day_count >= 100 then
    return jsonb_build_object('allowed', false);
  end if;
  update public.mini_ai_quota set minute_start = q.minute_start,
    minute_count = q.minute_count + 1, day_start = q.day_start,
    day_count = q.day_count + 1 where user_id = p_user;
  return jsonb_build_object('allowed', true);
end $$;
revoke all on function public.mini_ai_take_quota(uuid) from public, anon, authenticated;
grant execute on function public.mini_ai_take_quota(uuid) to service_role;
commit;
