// AITA Marketing — seguridad de ejecución (20261012120000_marketing_runtime_safety.sql). PGlite, datos de prueba.
// Vencidos inaccesibles (RLS, entradas, cola, sesiones), lease/heartbeat/timeout del worker, un solo
// purgador, presupuesto cerrado y máximo temporal de 20 USD (solo IA de Marketing), permisos. Ejecutar: NODE_PATH=/ruta/node_modules node tests/sql/marketing_runtime_safety.mjs
import { createRequire } from 'node:module';
import { readFile } from 'node:fs/promises';
import assert from 'node:assert/strict';
const require = createRequire(import.meta.url);
const { PGlite } = require('@electric-sql/pglite');

const file = (p) => readFile(new URL(p, import.meta.url), 'utf8');
const MIGS = ['20261009120000_aita_marketing.sql', '20261011120000_marketing_reel_studio.sql',
  '20261011130000_marketing_library_retention.sql', '20261011140000_marketing_generation_budget.sql',
  '20261012120000_marketing_runtime_safety.sql'];
const U = { ownerA: '00000000-0000-0000-0000-00000000000a', staffA: '00000000-0000-0000-0000-00000000000b',
  ownerB: '00000000-0000-0000-0000-00000000000e' };
const TA = '10000000-0000-0000-0000-00000000000a', TB = '10000000-0000-0000-0000-00000000000b';
const W1 = '30000000-0000-0000-0000-000000000001', W2 = '30000000-0000-0000-0000-000000000002';
let checks = 0;
const ok = (c, m) => { assert.ok(c, m); checks++; };

const db = new PGlite();
await db.exec(await file('./bootstrap_local.sql'));
await db.exec(`insert into auth.users (id) values ('${U.ownerA}'),('${U.staffA}'),('${U.ownerB}');
  insert into tenants (id, slug, name) values ('${TA}','ta','A'),('${TB}','tb','B');
  insert into tenant_users (tenant_id, user_id, role) values ('${TA}','${U.ownerA}','owner'),('${TA}','${U.staffA}','staff'),
    ('${TB}','${U.ownerB}','owner');`);
for (const m of MIGS) await db.exec(await file(`../../supabase/migrations/${m}`));
await db.exec(await file(`../../supabase/migrations/${MIGS[4]}`));                       // idempotente

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
await q(`update marketing_settings set marketing_enabled = true, ai_generation_enabled = true, monthly_ai_cost_limit = 20,
  library_storage_limit_bytes = 1000000 where tenant_id = '${TA}'`);
let n = 0;
const media = async (t = TA) => {
  const id = `20000000-0000-0000-0000-${String(++n).padStart(12, '0')}`;
  await q(`insert into marketing_media (id, tenant_id, storage_path, original_filename, media_type, mime_type, byte_size,
    checksum, uploaded_by, contains_people, contains_minors, people_policy) values ('${id}','${t}','${t}/originals/${id}/a.png','a.png','image','image/png',10,
    '${String(n).padStart(64, '0')}','${U.ownerA}', false, false, 'no_people')`);
  await q(`update marketing_media set validation_status = 'passed', processing_status = 'ready' where id = '${id}'`);
  return id;
};
const expire = (id) => q(`update marketing_media set expires_at = now() - interval '1 second' where id = '${id}'`);
let k = 0;
const job = async (mediaId) => {
  const id = (await one(`insert into marketing_generation_jobs (tenant_id, created_by, task_type, real_media_percent,
    ai_media_percent, maximum_cost, idempotency_key) values ('${TA}','${U.ownerA}','reel',50,50,5,'job-runtime-key-${String(++k).padStart(4, '0')}')
    returning id`)).id;
  if (mediaId) await q(`insert into marketing_generation_inputs (tenant_id, job_id, media_id, privacy_class)
    values ('${TA}','${id}','${mediaId}','business_media_no_people')`);
  await q(`update marketing_generation_jobs set status = 'awaiting_generation_approval', estimated_cost = 1 where id = '${id}'`);
  return id;
};
const approve = async (id) => (await one(`select public.marketing_approve_generation('${TA}','${id}','${U.ownerA}', 1, 0) r`)).r;
const asUser = async (uid, sql) => {
  await db.exec('begin; set local role authenticated');
  await db.query("select set_config('request.jwt.claims', $1, true)", [JSON.stringify({ sub: uid })]);
  try { return (await db.query(sql)).rows; } finally { await db.exec('rollback'); }
};

// 1. vencidos: invisibles por RLS, sin resurrección, nunca entradas ni en cola, sin sesiones
const M1 = await media(), MX = await media();
await expire(MX);
ok((await asUser(U.ownerA, `select id from marketing_media order by id`)).map((r) => r.id).join() === M1, 'el owner no ve el vencido');
ok((await asUser(U.staffA, `select id from marketing_media`)).length === 0, 'staff no ve nada');
ok((await asUser(U.ownerB, `select id from marketing_media`)).length === 0, 'otro tenant no ve nada');
await q(`insert into marketing_media_derivatives (tenant_id, media_id, kind, created_by, is_mock, status)
  values ('${TA}','${MX}','thumbnail','${U.ownerA}',true,'mock_only')`);
ok((await asUser(U.ownerA, `select id from marketing_media_derivatives`)).length === 0, 'derivado de un vencido: invisible');
await q(`update marketing_media set expires_at = now() + interval '1 day' where id = '${MX}'`, '23514');   // no resucita
const Jx = (await one(`insert into marketing_generation_jobs (tenant_id, created_by, task_type, real_media_percent,
  ai_media_percent, idempotency_key) values ('${TA}','${U.ownerA}','reel',50,50,'job-runtime-expired-01') returning id`)).id;
await q(`insert into marketing_generation_inputs (tenant_id, job_id, media_id, privacy_class)
  values ('${TA}','${Jx}','${MX}','business_media_no_people')`, '23514');
const M2 = await media();
const J1 = await job(M2);
await expire(M2);                                                     // vence después de crear el trabajo
await q(`select public.marketing_approve_generation('${TA}','${J1}','${U.ownerA}', 1, 0)`, '23514');   // no entra en cola
await q(`insert into marketing_stream_tokens (tenant_id, user_id, media_id, token_hash, expires_at)
  values ('${TA}','${U.ownerA}','${MX}','${'a'.repeat(64)}', now() + interval '5 minutes')`, '23514');
await q(`insert into marketing_stream_tokens (tenant_id, user_id, media_id, token_hash, expires_at)
  values ('${TA}','${U.staffA}','${M1}','${'b'.repeat(64)}', now() + interval '5 minutes')`, '42501');   // staff
await q(`update marketing_media set expires_at = now() + interval '2 minutes' where id = '${M1}'`);
await q(`insert into marketing_stream_tokens (tenant_id, user_id, media_id, token_hash, expires_at)
  values ('${TA}','${U.ownerA}','${M1}','${'c'.repeat(64)}', now() + interval '10 minutes')`);
const tk = await one(`select (t.expires_at = m.expires_at) same from marketing_stream_tokens t join marketing_media m on m.id = t.media_id
  where t.token_hash = '${'c'.repeat(64)}'`);
ok(tk.same, 'la sesión nunca dura más que el archivo');

// 2. worker: reclamo, heartbeat, lease, resultados, reintento y timeout
const M3 = await media();
const J2 = await job(M3);
ok((await approve(J2)).status === 'approved', 'aprobado');
const c1 = (await one(`select public.marketing_claim_job('${W1}', 60) r`)).r;
ok(c1.status === 'claimed' && c1.job.id === J2 && c1.job.lease_owner === W1 && c1.job.attempts === 1, 'W1 reclama');
ok((await one(`select public.marketing_claim_job('${W2}', 60) r`)).r.status === 'empty', 'W2 no lo toma');
ok((await one(`select public.marketing_job_heartbeat('${J2}','${W2}', 60) r`)).r === null, 'heartbeat ajeno: no');
ok((await one(`select public.marketing_job_heartbeat('${J2}','${W1}', 60) r`)).r === true, 'heartbeat propio: sí');
await q(`update marketing_generation_jobs set lease_owner = '${W2}' where id = '${J2}'`, '23514');   // no se roba
await q(`insert into marketing_generation_outputs (tenant_id, job_id, kind) values ('${TA}','${J2}','script')`);   // lease vigente
await q(`update marketing_generation_jobs set lease_expires_at = now() - interval '1 second' where id = '${J2}'`);
await q(`update marketing_generation_jobs set status = 'succeeded' where id = '${J2}'`, '23514');   // lease vencido: no termina
await q(`insert into marketing_generation_outputs (tenant_id, job_id, kind) values ('${TA}','${J2}','scene')`, '23514');
const c2 = (await one(`select public.marketing_claim_job('${W2}', 60) r`)).r;
ok(c2.status === 'claimed' && c2.retry === true && c2.job.attempts === 2 && c2.job.lease_owner === W2, 'reintento');
ok((await one(`select idempotency_key k from marketing_generation_jobs where id = '${J2}'`)).k === 'job-runtime-key-0002', 'misma clave');
await q(`update marketing_generation_jobs set lease_expires_at = now() - interval '1 second', attempts = 3 where id = '${J2}'`);
ok((await one(`select public.marketing_claim_job('${W1}', 60) r`)).r.status === 'empty', 'sin reintentos disponibles');
ok((await one(`select public.marketing_timeout_jobs(3) r`)).r === 1, 'timeout');
const t2 = await one(`select status, error_code, lease_owner from marketing_generation_jobs where id = '${J2}'`);
ok(t2.status === 'failed' && t2.error_code === 'timeout' && t2.lease_owner === null, 'failed/timeout');
ok((await one(`select bool_and(review_status = 'rejected' and metadata->>'blocked' = 'timeout') b from marketing_generation_outputs
  where job_id = '${J2}'`)).b, 'resultados bloqueados');
ok((await one(`select public.marketing_timeout_jobs(3) r`)).r === 0, 'una sola vez');
// trabajo cuyas entradas vencieron en cola: falla sin bloquear la cola
const M4 = await media();
const J3 = await job(M4);
await approve(J3);
await expire(M4);
const c3 = (await one(`select public.marketing_claim_job('${W1}', 60) r`)).r;
ok(c3.status === 'skipped' && c3.job_id === J3, 'saltado');
ok((await one(`select error_code e from marketing_generation_jobs where id = '${J3}'`)).e === 'media_not_ready', 'código público');
ok((await one(`select public.marketing_claim_job('${W1}', 60) r`)).r.status === 'empty', 'la cola sigue libre');
// en cola demasiado tiempo
const M5 = await media();
const J4 = await job(M5);
await approve(J4);
await q(`update marketing_generation_jobs set approved_at = now() - interval '25 hours' where id = '${J4}'`, '23514');   // inmutable
ok((await one(`select count(*)::int c from marketing_generation_jobs where status = 'queued'`)).c === 1, 'J4 en cola');

// 3. un solo purgador
const L = (h, ttl = 60) => one(`select public.marketing_acquire_runtime_lease('retention_runner','${h}', ${ttl}) r`);
ok((await L(W1)).r === true, 'W1 toma el lease');
ok((await L(W2)).r === null, 'W2 no');
ok((await L(W1)).r === true, 'W1 renueva');
ok((await one(`select public.marketing_release_runtime_lease('retention_runner','${W2}') r`)).r === null, 'W2 no lo libera');
await q(`update marketing_runtime_leases set expires_at = now() - interval '1 second'`);
ok((await L(W2)).r === true, 'vencido: W2 lo toma');
await q(`select public.marketing_acquire_runtime_lease('otra_cosa','${W1}', 60)`, '23514');

// 4. presupuesto cerrado y tope
await q(`update marketing_settings set monthly_ai_cost_limit = 80 where tenant_id = '${TA}'`, '23514');   // nunca el techo global
await q(`update marketing_settings set monthly_ai_cost_limit = 20.01 where tenant_id = '${TA}'`, '23514');
await q(`update marketing_settings set monthly_ai_cost_limit = -1 where tenant_id = '${TA}'`, '23514');
await q(`update marketing_settings set monthly_ai_cost_limit = null where tenant_id = '${TA}'`, '23502');
await q(`update marketing_settings set monthly_ai_cost_limit = 20 where tenant_id = '${TA}'`);
await q(`update marketing_settings set monthly_ai_cost_limit = 10 where tenant_id = '${TA}'`);
await q(`update marketing_settings set monthly_ai_cost_limit = 0 where tenant_id = '${TA}'`);
const M6 = await media();
ok((await approve(await job(M6))).reason === 'generation_disabled', 'presupuesto 0: cerrado');
ok((await one(`select count(*)::int c from marketing_settings where monthly_ai_cost_limit is null`)).c === 0, 'nunca desconocido');

// 5. permisos y definiciones
for (const role of ['anon', 'authenticated']) {
  for (const f of ['marketing_claim_job(uuid,integer,uuid,integer)', 'marketing_job_heartbeat(uuid,uuid,integer)',
    'marketing_timeout_jobs(integer)', 'marketing_acquire_runtime_lease(text,uuid,integer)', 'marketing_release_runtime_lease(text,uuid)',
    'marketing_approve_generation(uuid,uuid,uuid,numeric,bigint)']) {
    ok(!(await one(`select has_function_privilege('${role}', 'public.${f}', 'EXECUTE') x`)).x, `${role} no ejecuta ${f}`);
  }
  ok(!(await one(`select has_table_privilege('${role}', 'public.marketing_runtime_leases', 'SELECT') x`)).x, `${role} no lee leases`);
}
for (const f of ['marketing_claim_job', 'marketing_job_heartbeat', 'marketing_timeout_jobs', 'marketing_acquire_runtime_lease',
  'marketing_release_runtime_lease']) {
  const d = await one(`select prosecdef s, proconfig @> array['search_path=""'] c from pg_proc where proname = '${f}'`);
  ok(d.s === true && d.c === true, `${f}: definer con search_path vacío`);
}
ok(/for update skip locked/i.test((await one(`select prosrc s from pg_proc where proname = 'marketing_claim_job'`)).s), 'SKIP LOCKED');

console.log(`ALL MARKETING RUNTIME SAFETY SQL TESTS PASSED (${checks} checks)`);
