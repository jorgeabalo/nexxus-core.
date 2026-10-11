// AITA Marketing — finalización atómica y caminos terminales (20261012130000_marketing_worker_finalize.sql). PGlite.
// marketing_finalize_generation (todo o nada), marketing_fail_generation, marketing_cancel_generation,
// guardián de transiciones, marketing_close_purge ligado al lease, índice único y permisos.
// Ejecutar: NODE_PATH=/ruta/node_modules node tests/sql/marketing_worker_finalize.mjs
import { createRequire } from 'node:module';
import { readFile } from 'node:fs/promises';
import assert from 'node:assert/strict';
const require = createRequire(import.meta.url);
const { PGlite } = require('@electric-sql/pglite');

const file = (p) => readFile(new URL(p, import.meta.url), 'utf8');
const MIGS = ['20261009120000_aita_marketing.sql', '20261011120000_marketing_reel_studio.sql',
  '20261011130000_marketing_library_retention.sql', '20261011140000_marketing_generation_budget.sql',
  '20261012120000_marketing_runtime_safety.sql', '20261012130000_marketing_worker_finalize.sql'];
const OWNER = '00000000-0000-0000-0000-00000000000a';
const TA = '10000000-0000-0000-0000-00000000000a';
const W1 = '30000000-0000-0000-0000-000000000001', W2 = '30000000-0000-0000-0000-000000000002';
let checks = 0;
const ok = (c, m) => { assert.ok(c, m); checks++; };

const db = new PGlite();
await db.exec(await file('./bootstrap_local.sql'));
await db.exec(`insert into auth.users (id) values ('${OWNER}'); insert into tenants (id, slug, name) values ('${TA}','ta','A');
  insert into tenant_users (tenant_id, user_id, role) values ('${TA}','${OWNER}','owner');`);
for (const m of MIGS) await db.exec(await file(`../../supabase/migrations/${m}`));
for (const m of MIGS.slice(4)) await db.exec(await file(`../../supabase/migrations/${m}`));   // idempotentes

async function q(sql, expected) {
  try {
    const r = await db.query(sql);
    if (expected) assert.fail(`expected ${expected}: ${sql}`);
    return r.rows;
  } catch (e) {
    if (!expected || e.code !== expected) throw e;
    checks++;
    return null;
  }
}
const one = async (sql) => (await q(sql))[0];
const r = async (sql) => (await one(`select ${sql} r`)).r;
await q(`update marketing_settings set marketing_enabled = true, ai_generation_enabled = true, monthly_ai_cost_limit = 20,
  library_storage_limit_bytes = 1000 where tenant_id = '${TA}'`);
let k = 0;
const job = async () => {
  const id = (await one(`insert into marketing_generation_jobs (tenant_id, created_by, task_type, real_media_percent,
    ai_media_percent, maximum_cost, idempotency_key) values ('${TA}','${OWNER}','reel',0,100,5,'job-finalize-key-${String(++k).padStart(4, '0')}')
    returning id`)).id;
  await q(`update marketing_generation_jobs set status = 'awaiting_generation_approval', estimated_cost = 1 where id = '${id}'`);
  ok((await r(`public.marketing_approve_generation('${TA}','${id}','${OWNER}', 1, 10)`)).status === 'approved', 'aprobado');
  return id;
};
const claim = async (w) => r(`public.marketing_claim_job('${w}', 60)`);
const usage = (j, key, cost = 0) => JSON.stringify([{ provider: 'mock', model_id: 'm', task_type: 'reel', catalog_version: 'v1',
  billing_unit: 'request', units: 1, estimated_cost: 0, actual_cost: cost, idempotency_key: `usage-${j}-${key}`, status: 'not_charged' }]);
const outs = (j, w, extra = []) => JSON.stringify([{ kind: 'script', metadata: {} }, { kind: 'scene', scene_index: 0, metadata: {} },
  { kind: 'render', storage_path: `${TA}/derivatives/${j}/${w}-render.mp4`, byte_size: 5, mime_type: 'video/mp4', metadata: {} }, ...extra]);
const fin = (j, w, u, o) => r(`public.marketing_finalize_generation('${TA}','${j}','${w}','${u}'::jsonb,'${o}'::jsonb,'{}'::jsonb,'{}'::jsonb)`);
const st = (j) => one(`select (select status from marketing_generation_jobs where id = '${j}') job,
  (select count(*)::int from marketing_generation_outputs where job_id = '${j}') outs,
  (select count(*)::int from marketing_model_usage where job_id = '${j}') uses,
  (select status || ':' || bytes from marketing_storage_reservations where reservation_key = 'job:${j}') res`);
const same = (a, b, m) => ok(JSON.stringify(a) === JSON.stringify(b), `${m}: ${JSON.stringify(a)}`);

// 1. finalización: rechazos sin efectos
const J1 = await job();
ok((await claim(W1)).status === 'claimed', 'W1 reclama');
ok((await fin(J1, W2, usage(J1, 0), outs(J1, W2))).reason === 'lease_lost', 'otro worker: no');
for (const [o, m] of [[`${TA}/derivatives/${J1}/${W2}-x.mp4`, 'temporal de otro intento'], [`${TA}/originals/${J1}/${W1}-x.mp4`, 'original'],
  [`${TA}/derivatives/otro/${W1}-x.mp4`, 'otro trabajo'], [`${TA}/derivatives/${J1}/${W1}-x/y.mp4`, 'subcarpeta']]) {
  ok((await fin(J1, W1, usage(J1, 0), JSON.stringify([{ kind: 'render', storage_path: o, byte_size: 5 }]))).reason === 'invalid_output', m);
}
ok((await fin(J1, W1, usage(J1, 0), JSON.stringify([{ kind: 'render', storage_path: `${TA}/derivatives/${J1}/${W1}-x.mp4`, byte_size: 0 }]))).reason
  === 'invalid_output', 'tamaño 0');
ok((await fin(J1, W1, usage(J1, 0, 2), outs(J1, W1))).reason === 'budget_exceeded', 'costo > reservado');
await q(`update marketing_settings set library_storage_limit_bytes = 2 where tenant_id = '${TA}'`);
ok((await fin(J1, W1, usage(J1, 0), outs(J1, W1))).reason === 'storage_quota_exceeded', 'no cabe');
await q(`update marketing_settings set library_storage_limit_bytes = 1000 where tenant_id = '${TA}'`);
await q(`update marketing_storage_reservations set expires_at = now() - interval '1 second' where reservation_key = 'job:${J1}'`);
ok((await fin(J1, W1, usage(J1, 0), outs(J1, W1))).reason === 'reservation_expired', 'reserva vencida');
await q(`update marketing_storage_reservations set expires_at = now() + interval '1 hour' where reservation_key = 'job:${J1}'`);
await q(`update marketing_settings set ai_generation_enabled = false where tenant_id = '${TA}'`);
ok((await fin(J1, W1, usage(J1, 0), outs(J1, W1))).reason === 'budget_exceeded', 'generación cerrada');
await q(`update marketing_settings set ai_generation_enabled = true where tenant_id = '${TA}'`);
same(await st(J1), { job: 'processing', outs: 0, uses: 0, res: 'reserved:10' }, 'nada escrito tras los rechazos');

// 2. todo o nada: duplicado y fallos inyectados en cada paso
await q(`update marketing_generation_jobs set lease_expires_at = now() + interval '60 seconds' where id = '${J1}'`);
await q(`select public.marketing_finalize_generation('${TA}','${J1}','${W1}','${usage(J1, 0)}'::jsonb,
  '${outs(J1, W1, [{ kind: 'script', metadata: {} }])}'::jsonb,'{}'::jsonb,'{}'::jsonb)`, '23505');
same(await st(J1), { job: 'processing', outs: 0, uses: 0, res: 'reserved:10' }, 'duplicado: se revierte todo');
await db.exec(`create function public.test_fault() returns trigger language plpgsql as $$ begin
    if current_setting('test.fault', true) = tg_argv[0] then raise exception 'fallo %', tg_argv[0]; end if; return new; end $$;
  create trigger f1 after update on marketing_storage_reservations for each row execute function public.test_fault('after_storage');
  create trigger f2 after insert on marketing_generation_outputs for each row execute function public.test_fault('after_first_output');
  create trigger f3 before update on marketing_generation_jobs for each row when (new.status = 'succeeded')
    execute function public.test_fault('before_succeeded');`);
for (const step of ['after_storage', 'after_first_output', 'before_succeeded']) {
  await q(`select set_config('test.fault', '${step}', false)`);
  await q(`select public.marketing_finalize_generation('${TA}','${J1}','${W1}','${usage(J1, 0)}'::jsonb,'${outs(J1, W1)}'::jsonb,
    '{}'::jsonb,'{}'::jsonb)`, 'P0001');
  same(await st(J1), { job: 'processing', outs: 0, uses: 0, res: 'reserved:10' }, `fallo ${step}: nada`);
}
await q(`select set_config('test.fault', '', false)`);
// B retoma con la MISMA idempotency_key: un resultado por clave, un costo
await q(`update marketing_generation_jobs set lease_expires_at = now() - interval '1 second' where id = '${J1}'`);
ok((await fin(J1, W1, usage(J1, 0), outs(J1, W1))).reason === 'lease_lost', 'vencido: no finaliza');
ok((await claim(W2)).status === 'claimed', 'W2 retoma');
const done = await fin(J1, W2, usage(J1, 0), outs(J1, W2));
ok(done.status === 'succeeded' && done.job.status === 'succeeded', 'W2 finaliza');
same(await st(J1), { job: 'succeeded', outs: 3, uses: 1, res: 'consumed:5' }, 'una vez cada cosa; tamaño real');
ok((await fin(J1, W2, usage(J1, 0), outs(J1, W2))).reason === 'lease_lost', 'no se finaliza dos veces');
ok((await one(`select count(*)::int c from marketing_generation_job_events where job_id = '${J1}' and action = 'succeed'`)).c === 1, '1 evento');

// 3. fallo del worker y transiciones terminales
const J2 = await job();
ok((await claim(W1)).status === 'claimed', 'W1 reclama J2');
ok((await r(`public.marketing_fail_generation('${TA}','${J2}','${W2}','render_failed','[]'::jsonb)`)).reason === 'lease_lost', 'fail ajeno: no');
for (const [flag, to] of [['', 'failed'], ['', 'cancelled'], ['', 'succeeded'], [`worker:${W2}`, 'failed'], ['worker', 'failed'],
  [`worker:${W1}`, 'cancelled'], ['expired', 'failed'], ['cancel', 'succeeded'], ['expired', 'succeeded']]) {
  await db.exec('begin');
  await q(`select set_config('marketing.transition', '${flag}', true)`);
  await q(`update marketing_generation_jobs set status = '${to}' where id = '${J2}'`, '23514');
  await db.exec('rollback');
}
await q(`update marketing_generation_jobs set lease_expires_at = now() - interval '1 second' where id = '${J2}'`);
ok((await r(`public.marketing_fail_generation('${TA}','${J2}','${W1}','render_failed','[]'::jsonb)`)).reason === 'lease_lost', 'vencido: no falla');
await q(`update marketing_generation_jobs set lease_expires_at = now() + interval '60 seconds' where id = '${J2}'`);
const f2 = await r(`public.marketing_fail_generation('${TA}','${J2}','${W1}','render_failed','${usage(J2, 0, 0.5)}'::jsonb)`);
ok(f2.status === 'failed' && Number(f2.job.actual_cost) === 0.5, 'falla con su costo real');
same(await st(J2), { job: 'failed', outs: 0, uses: 1, res: 'released:10' }, 'reserva liberada una vez');

// 4. cancelación externa: camino propio, nunca éxito
const J3 = await job();
ok((await r(`public.marketing_cancel_generation('${TA}','${J3}','succeeded','x',null,'system')`)).reason === 'invalid_transition', 'nunca éxito');
ok((await r(`public.marketing_cancel_generation('${TA}','${J3}','cancelled','cancelled_by_user','${OWNER}','owner')`)).status === 'cancelled', 'cancela');
ok((await r(`public.marketing_cancel_generation('${TA}','${J3}','cancelled','cancelled_by_user','${OWNER}','owner')`)).status === 'unchanged', 'idempotente');

const J4 = await job();
ok((await claim(W1)).status === 'claimed', 'W1 reclama J4');
const twice = JSON.stringify([...JSON.parse(usage(J4, 0)), ...JSON.parse(usage(J4, 0))]);
ok((await fin(J4, W1, twice, outs(J4, W1))).status === 'succeeded', 'misma clave dos veces en una llamada');
ok((await st(J4)).uses === 1, 'un solo costo por idempotency_key');

// 5. cierre de purga ligado al lease vigente del purgador
const MID = '20000000-0000-0000-0000-000000000001';
await q(`insert into marketing_media (id, tenant_id, storage_path, original_filename, media_type, mime_type, byte_size, checksum,
  uploaded_by, contains_people, contains_minors, people_policy) values ('${MID}','${TA}','${TA}/originals/${MID}/a.png','a.png','image',
  'image/png',10,'${'1'.repeat(64)}','${OWNER}', false, false, 'no_people')`);
await q(`insert into marketing_media_derivatives (tenant_id, media_id, kind, created_by, is_mock, status)
  values ('${TA}','${MID}','thumbnail','${OWNER}',true,'mock_only')`);
const live = () => one(`select count(*)::int c from marketing_media_derivatives where media_id = '${MID}' and status <> 'deleted'`);
ok((await r(`public.marketing_acquire_runtime_lease('retention_runner','${W1}', 60)`)) === true, 'W1 purgador');
ok((await r(`public.marketing_close_purge('${TA}','${MID}','${W1}','expired',null,'system')`)).status === 'already_closed', 'activo: no');
ok((await live()).c === 1, 'sin cierre no se toca ningún derivado');
await q(`select public.marketing_release_runtime_lease('retention_runner','${W1}')`);
await q(`update marketing_media set retention_status = 'purge_pending', purge_reason = 'expired' where id = '${MID}'`);
const close = (h, actor = 'null', role = 'system', why = 'expired') =>
  r(`public.marketing_close_purge('${TA}','${MID}',${h ? `'${h}'` : 'null'},'${why}',${actor},'${role}')`);
ok((await close(null)).reason === 'lease_required', 'sin lease ni owner/manager: no');
ok((await close(null, `'${OWNER}'`, 'staff', 'user_deleted')).reason === 'lease_required', 'staff: no');
ok((await r(`public.marketing_acquire_runtime_lease('retention_runner','${W1}', 60)`)) === true, 'W1 purgador');
ok((await close(W2)).status === 'lease_lost', 'no es el holder: no cierra');
ok((await one(`select retention_status s from marketing_media where id = '${MID}'`)).s === 'purge_pending', 'sigue pendiente');
ok((await close(W1)).status === 'closed', 'el holder cierra');
ok((await live()).c === 0, 'derivados cerrados con el archivo');
ok((await close(W1)).status === 'already_closed', 'una sola vez');
ok((await one(`select count(*)::int c from marketing_media_events where media_id = '${MID}' and action = 'purged'`)).c === 1, '1 evento');

// 6. permisos y definiciones
const FNS = ['marketing_finalize_generation(uuid,uuid,uuid,jsonb,jsonb,jsonb,jsonb)', 'marketing_fail_generation(uuid,uuid,uuid,text,jsonb)',
  'marketing_cancel_generation(uuid,uuid,text,text,uuid,text)', 'marketing_close_purge(uuid,uuid,uuid,text,uuid,text)'];
for (const f of FNS) {
  for (const role of ['anon', 'authenticated', 'public']) {
    ok(!(await one(`select has_function_privilege('${role}', 'public.${f}', 'EXECUTE') x`)).x, `${role} no ejecuta ${f}`);
  }
  ok((await one(`select has_function_privilege('service_role', 'public.${f}', 'EXECUTE') x`)).x, `service_role ejecuta ${f}`);
  const d = await one(`select prosecdef s, proconfig @> array['search_path=""'] c from pg_proc where oid = 'public.${f}'::regprocedure`);
  ok(d.s === true && d.c === true, `${f}: definer con search_path vacío`);
}
ok((await one(`select count(*)::int c from pg_indexes where indexname = 'marketing_generation_outputs_unique_idx'`)).c === 1, 'índice único');

console.log(`ALL MARKETING WORKER FINALIZE SQL TESTS PASSED (${checks} checks)`);
