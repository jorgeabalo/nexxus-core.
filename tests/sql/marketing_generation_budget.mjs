// AITA Marketing — costo de los trabajos (20261011140000_marketing_generation_budget.sql). PGlite, datos de prueba.
// Límite del operador cerrado por defecto, aprobación + reserva atómica, reserva inmutable, liberación al terminar,
// permisos. Ejecutar: NODE_PATH=/ruta/node_modules node tests/sql/marketing_generation_budget.mjs
import { createRequire } from 'node:module';
import { readFile } from 'node:fs/promises';
import assert from 'node:assert/strict';
const require = createRequire(import.meta.url);
const { PGlite } = require('@electric-sql/pglite');

const file = (p) => readFile(new URL(p, import.meta.url), 'utf8');
const MIGS = ['20261009120000_aita_marketing.sql', '20261011120000_marketing_reel_studio.sql',
  '20261011130000_marketing_library_retention.sql', '20261011140000_marketing_generation_budget.sql'];
const U = { ownerA: '00000000-0000-0000-0000-00000000000a', ownerB: '00000000-0000-0000-0000-00000000000e' };
const TA = '10000000-0000-0000-0000-00000000000a', TB = '10000000-0000-0000-0000-00000000000b';
let checks = 0;
const ok = (c, m) => { assert.ok(c, m); checks++; };

const db = new PGlite();
await db.exec(await file('./bootstrap_local.sql'));
await db.exec(`insert into auth.users (id) values ('${U.ownerA}'),('${U.ownerB}');
  insert into tenants (id, slug, name) values ('${TA}','ta','A'),('${TB}','tb','B');
  insert into tenant_users (tenant_id, user_id, role) values ('${TA}','${U.ownerA}','owner'),('${TB}','${U.ownerB}','owner');`);
for (const m of MIGS) await db.exec(await file(`../../supabase/migrations/${m}`));
await db.exec(await file(`../../supabase/migrations/${MIGS[3]}`));                       // idempotente

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
let n = 0;
const job = async (t = TA) => (await q(`insert into marketing_generation_jobs (tenant_id, created_by, task_type, real_media_percent,
  ai_media_percent, maximum_cost, idempotency_key, status) values ('${t}','${U.ownerA}','reel',0,100,50,'job-budget-key-${String(++n).padStart(4, '0')}','draft')
  returning id`))[0].id;
const awaiting = async (t = TA) => { const id = await job(t);
  await q(`update marketing_generation_jobs set status = 'awaiting_generation_approval', estimated_cost = 1 where id = '${id}'`); return id; };
const approve = async (t, id, cost) => (await q(`select public.marketing_approve_generation('${t}','${id}','${U.ownerA}',
  ${cost === null ? 'null' : cost}) r`))[0].r;

// 1. cerrado por defecto: límite 0 e IA apagada → nada se aprueba, ni simulado
const s = (await q(`select monthly_ai_cost_limit l, ai_generation_enabled e from marketing_settings where tenant_id = '${TA}'`))[0];
ok(Number(s.l) === 0 && s.e === false, 'límite de IA 0 e IA apagada por defecto');
const J1 = await awaiting();
ok((await approve(TA, J1, 0)).reason === 'generation_disabled', 'límite 0: ni el mock');
await q(`update marketing_settings set monthly_ai_cost_limit = -1 where tenant_id = '${TA}'`, '23514');
await q(`update marketing_settings set ai_generation_enabled = true, monthly_ai_cost_limit = 20 where tenant_id = '${TA}'`);

// 2. aprobar = reservar el costo máximo en la misma transacción
ok((await approve(TA, J1, null)).reason === 'cost_not_estimable', 'sin estimación no se aprueba');
let r = await approve(TA, J1, 15);
ok(r.status === 'approved' && r.job.status === 'queued' && Number(r.job.reserved_cost) === 15, 'aprobado y reservado');
ok((await q(`select count(*)::int c from marketing_generation_job_events where job_id = '${J1}' and action = 'approve'`))[0].c === 1, 'evento');
ok((await approve(TA, J1, 15)).reason === 'conflict', 'no se aprueba dos veces');
await q(`update marketing_generation_jobs set reserved_cost = 1 where id = '${J1}'`, '23514');           // reserva inmutable
const J2 = await awaiting();
r = await approve(TA, J2, 10);                                                                          // 15 + 10 > 20
ok(r.status === 'rejected' && r.reason === 'budget_exceeded' && Number(r.available) === 5, 'el saldo no se usa dos veces');
ok((await q(`select status from marketing_generation_jobs where id = '${J2}'`))[0].status === 'awaiting_generation_approval', 'sigue pendiente');
// al terminar sin gasto, lo reservado deja de contar
await q(`update marketing_generation_jobs set status = 'cancelled', actual_cost = 0 where id = '${J1}'`);
ok((await approve(TA, J2, 10)).status === 'approved', 'saldo liberado');
// no se puede poner en cola sin reserva
const J3 = await awaiting();
await q(`update marketing_generation_jobs set status = 'queued', approved_at = now(), approved_by = '${U.ownerA}' where id = '${J3}'`, '23514');

// 3. aislamiento: el consumo de A no afecta a B (y B sigue cerrado)
const JB = await awaiting(TB);
ok((await approve(TB, JB, 0)).reason === 'generation_disabled', 'B cerrado por defecto');
ok((await approve(TB, J3, 1)).reason !== 'approved' && (await q(`select status from marketing_generation_jobs where id = '${J3}'`))[0].status
  === 'awaiting_generation_approval', 'no se aprueba un trabajo de otro tenant');

// 4. serialización y permisos
const src = (await q(`select prosrc from pg_proc where proname = 'marketing_approve_generation'`))[0].prosrc;
ok(/from public\.marketing_settings where tenant_id = p_tenant for update/i.test(src), 'bloquea la configuración del tenant (SELECT … FOR UPDATE)');
ok(/status = 'awaiting_generation_approval' for update/i.test(src), 'bloquea también el trabajo');
for (const role of ['anon', 'authenticated']) {
  ok(!(await q(`select has_function_privilege('${role}', 'public.marketing_approve_generation(uuid,uuid,uuid,numeric)', 'EXECUTE') x`))[0].x,
    `${role} no ejecuta la aprobación`);
}
ok((await q(`select has_function_privilege('service_role', 'public.marketing_approve_generation(uuid,uuid,uuid,numeric)', 'EXECUTE') x`))[0].x,
  'solo el backend');
await db.exec(`begin; set local role authenticated`);
await db.query("select set_config('request.jwt.claims', $1, true)", [JSON.stringify({ sub: U.ownerA })]);
try { await db.query(`update marketing_settings set monthly_ai_cost_limit = 999`); assert.fail('debía fallar'); }
catch (e) { assert.equal(e.code, '42501'); checks++; }
await db.exec('rollback');

console.log(`ALL MARKETING GENERATION BUDGET SQL TESTS PASSED (${checks} checks)`);
