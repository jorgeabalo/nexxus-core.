// AITA Marketing Fase 2 — migración de retención (20261011130000). PGlite en memoria, datos de prueba.
// Nunca retención ilimitada, máximo del plan, purga que solo neutraliza lo permitido, derivados simulados ≤ 7 días.
// Ejecutar: NODE_PATH=/ruta/node_modules node tests/sql/marketing_retention.mjs
import { createRequire } from 'node:module';
import { readFile } from 'node:fs/promises';
import assert from 'node:assert/strict';
const require = createRequire(import.meta.url);
const { PGlite } = require('@electric-sql/pglite');

const file = (p) => readFile(new URL(p, import.meta.url), 'utf8');
const MIGS = ['20261009120000_aita_marketing.sql', '20261011120000_marketing_reel_studio.sql',
  '20261011130000_marketing_library_retention.sql'];
const U = { ownerA: '00000000-0000-0000-0000-00000000000a', ownerB: '00000000-0000-0000-0000-00000000000e' };
const TA = '10000000-0000-0000-0000-00000000000a', TB = '10000000-0000-0000-0000-00000000000b';
let checks = 0;
const ok = (cond, msg) => { assert.ok(cond, msg); checks++; };

const db = new PGlite();
await db.exec(await file('./bootstrap_local.sql'));
await db.exec(`insert into auth.users (id) values ('${U.ownerA}'),('${U.ownerB}');
  insert into tenants (id, slug, name) values ('${TA}','ta','A'),('${TB}','tb','B');
  insert into tenant_users (tenant_id, user_id, role) values ('${TA}','${U.ownerA}','owner'),('${TB}','${U.ownerB}','owner');`);
for (const m of MIGS) await db.exec(await file(`../../supabase/migrations/${m}`));
for (const m of MIGS.slice(1)) await db.exec(await file(`../../supabase/migrations/${m}`));   // idempotente

async function sys(sql, expected) {
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
const M1 = '20000000-0000-0000-0000-0000000000a1', M2 = '20000000-0000-0000-0000-0000000000a2';
const add = async (id, t, ck, extra = '') => {
  await sys(`insert into marketing_media (id, tenant_id, storage_path, original_filename, media_type, mime_type, byte_size,
    checksum, uploaded_by, metadata) values ('${id}','${t}','${t}/originals/${id}/maria.png','maria.png','image','image/png',10,
    '${ck.repeat(64)}','${U.ownerA}','{"consent_note":"x"}')`);
  await sys(`update marketing_media set validation_status = 'passed', processing_status = 'ready' ${extra} where id = '${id}'`);
};

// 1. valores por defecto: plan 30 días, nada sin vencimiento
const st = (await sys(`select max_retention_days v from marketing_settings where tenant_id = '${TA}'`))[0];
ok(st.v === 30, 'retención máxima por defecto 30 días');
await sys(`update marketing_settings set max_retention_days = 365 where tenant_id = '${TA}'`, '23514');
await sys(`update marketing_settings set max_retention_days = 5 where tenant_id = '${TA}'`, '23514');
await add(M1, TA, '1');
const m = (await sys(`select retention_days, retention_status, expires_at, created_at from marketing_media where id = '${M1}'`))[0];
ok(m.retention_days === 30 && m.retention_status === 'active', 'activo, 30 días');
ok(Math.round((m.expires_at - m.created_at) / 86400000) === 30, 'vence a los 30 días');
await sys(`update marketing_media set expires_at = null where id = '${M1}'`, '23502');                 // nunca ilimitado
await sys(`insert into marketing_media (id, tenant_id, storage_path, media_type, mime_type, byte_size, checksum, uploaded_by, expires_at)
  values ('20000000-0000-0000-0000-0000000000a9','${TA}','${TA}/originals/20000000-0000-0000-0000-0000000000a9/x.png','image',
  'image/png',1,'${'9'.repeat(64)}','${U.ownerA}', now() + interval '365 days')`, '23514');          // ni al insertar
await sys(`update marketing_media set expires_at = created_at + interval '91 days' where id = '${M1}'`, '23514');
await sys(`update marketing_media set retention_days = 365 where id = '${M1}'`, '23514');
// extensión: solo dentro del máximo del plan
await sys(`update marketing_media set retention_days = 60, expires_at = created_at + interval '60 days' where id = '${M1}'`, '23514');
await sys(`update marketing_settings set max_retention_days = 60 where tenant_id = '${TA}'`);
await sys(`update marketing_media set retention_days = 60, expires_at = created_at + interval '60 days' where id = '${M1}'`);
await sys(`update marketing_media set retention_days = 7, expires_at = created_at + interval '7 days' where id = '${M1}'`);   // acortar siempre

// 2. purga: solo neutraliza ruta, nombre y metadatos; conserva hash, tipo, tamaño, quién y fechas
await sys(`update marketing_media set original_filename = null where id = '${M1}'`, '42501');
await sys(`update marketing_media set storage_path = '${TA}/originals/${M1}/otra.png' where id = '${M1}'`, '42501');
await sys(`update marketing_media set processing_status = 'deleted', deleted_at = now() where id = '${M1}'`, '23514');   // sin purged
await sys(`update marketing_media set processing_status = 'deleted', deleted_at = now(), retention_status = 'purged',
  purged_at = now() where id = '${M1}'`, '23514');                                                    // conserva nombre/metadatos
await sys(`update marketing_media set processing_status = 'deleted', deleted_at = now(), retention_status = 'purged',
  purged_at = now(), purge_reason = 'expired', original_filename = null, metadata = '{}',
  storage_path = '${TB}/originals/${M1}/purged' where id = '${M1}'`, '42501');                       // ruta de otro tenant
await sys(`update marketing_media set processing_status = 'deleted', deleted_at = now(), retention_status = 'purged',
  purged_at = now(), purge_reason = 'expired', original_filename = null, metadata = '{}',
  storage_path = '${TA}/originals/${M1}/purged' where id = '${M1}'`);
const p = (await sys(`select * from marketing_media where id = '${M1}'`))[0];
ok(p.checksum === '1'.repeat(64) && p.byte_size === 10 && p.mime_type === 'image/png' && p.uploaded_by === U.ownerA,
  'auditoría mínima conservada');
ok(p.original_filename === null && JSON.stringify(p.metadata) === '{}', 'sin nombre ni metadatos personales');
await sys(`update marketing_media set purge_attempts = 1 where id = '${M1}'`, '42501');               // purgado: inmutable
await sys(`delete from marketing_media where id = '${M1}'`, '42501');
await sys(`update marketing_media set purge_reason = 'forever' where id = '${M1}'`, '42501');

// 3. un trabajo activo protege; terminado, ya no
await add(M2, TA, '2', `, contains_people = false, people_policy = 'no_people'`);
const J = (await sys(`insert into marketing_generation_jobs (tenant_id, created_by, task_type, real_media_percent, ai_media_percent,
  idempotency_key) values ('${TA}','${U.ownerA}','reel',50,50,'job-key-retention-0001') returning id`))[0].id;
await sys(`insert into marketing_generation_inputs (tenant_id, job_id, media_id, privacy_class) values ('${TA}','${J}','${M2}','business_media_no_people')`);
const purge2 = `update marketing_media set processing_status = 'deleted', deleted_at = now(), retention_status = 'purged',
  purged_at = now(), purge_reason = 'expired', original_filename = null, metadata = '{}',
  storage_path = '${TA}/originals/${M2}/purged' where id = '${M2}'`;
await sys(purge2, '23514');
await sys(`update marketing_generation_jobs set status = 'cancelled' where id = '${J}'`);
await sys(purge2);

// 4. derivados: simulados como máximo 7 días; ningún derivado sin vencimiento
await add('20000000-0000-0000-0000-0000000000a3', TA, '3');
await sys(`insert into marketing_media_derivatives (tenant_id, media_id, kind, created_by, is_mock, status, expires_at)
  values ('${TA}','20000000-0000-0000-0000-0000000000a3','anonymized','${U.ownerA}',true,'mock_only', now() + interval '30 days')`, '23514');
await sys(`insert into marketing_media_derivatives (tenant_id, media_id, kind, created_by, is_mock, status)
  values ('${TA}','20000000-0000-0000-0000-0000000000a3','anonymized','${U.ownerA}',true,'mock_only')`);
const d = (await sys(`select expires_at - created_at d from marketing_media_derivatives`))[0];
ok(String(d.d.days ?? d.d).includes('7'), 'derivado simulado vence a los 7 días');
await sys(`update marketing_media_derivatives set expires_at = null`, '23502');

// 5. el tenant no puede alargar su retención máxima
await db.exec(`begin; set local role authenticated`);
await db.query("select set_config('request.jwt.claims', $1, true)", [JSON.stringify({ sub: U.ownerA })]);
try { await db.query(`update marketing_settings set max_retention_days = 90`); assert.fail('debía fallar'); }
catch (e) { assert.equal(e.code, '42501'); checks++; }
await db.exec('rollback');

console.log(`ALL MARKETING RETENTION SQL TESTS PASSED (${checks} checks)`);
