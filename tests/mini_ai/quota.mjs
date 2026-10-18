// Actual PostgreSQL engine in WASM; isolated in memory, no production credentials.
import { PGlite } from '@electric-sql/pglite';
import { readFile } from 'node:fs/promises';
import assert from 'node:assert/strict';
const db = new PGlite();
const user = '82efb75b-e775-4b8e-8608-12b1ccbe0ec5';
const other = '82efb75b-e775-4b8e-8608-12b1ccbe0ec6';
try {
  await db.exec(`create schema auth; create table auth.users(id uuid primary key);
    create role anon; create role authenticated; create role service_role;
    insert into auth.users values ('${user}'), ('${other}');`);
  await db.exec(await readFile(new URL('../../supabase/migrations/20261018120000_mini_ai_quota.sql', import.meta.url), 'utf8'));
  const take = async id => (await db.query('select public.mini_ai_take_quota($1) as quota', [id])).rows[0].quota.allowed;
  // Requests queued together must still reserve only six attempts.
  const attempts = await Promise.all(Array.from({length: 20}, () => take(user)));
  assert.equal(attempts.filter(Boolean).length, 6);
  assert.equal(await take(other), true, 'Users have independent quotas');
  await db.query(`update public.mini_ai_quota set minute_start = now() - interval '2 minutes' where user_id = $1`, [user]);
  assert.equal(await take(user), true, 'Minute budget resets');
  await db.query('update public.mini_ai_quota set day_count = 100 where user_id = $1', [user]);
  assert.equal(await take(user), false, 'Daily budget cannot be bypassed by minute reset');
  await db.query(`update public.mini_ai_quota set day_start = current_date - 1 where user_id = $1`, [user]);
  assert.equal(await take(user), true, 'New day resets daily budget');
  for (const role of ['anon', 'authenticated']) {
    await db.exec(`set role ${role}`);
    await assert.rejects(() => take(user), /permission denied/);
    await assert.rejects(() => db.query('select * from public.mini_ai_quota'), /permission denied/);
    await db.exec('reset role');
  }
  await db.exec('set role service_role');
  assert.equal(await take(other), true, 'Service role can execute security-definer function');
  await db.exec('reset role');
  console.log('Quota SQL: limits, resets, user isolation and role permissions passed.');
} finally {
  await db.close();
}
