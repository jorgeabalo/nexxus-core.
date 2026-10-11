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

// 5. publicación: expires_at = mínimo(publicación + 30 días, subida + 90 días); protección: máx. 14 días más
const M6 = '20000000-0000-0000-0000-0000000000a6';
await add(M6, TA, '6');
const pub = (p, e) => `update marketing_media set published_at = created_at + interval '${p} days',
  expires_at = created_at + interval '${e} days' where id = '${M6}'`;
await sys(pub(5, 36), '23514');                        // publicado el día 5: como mucho día 35
await sys(pub(5, 35));
await sys(pub(75, 91), '23514');                       // publicado el día 75: el tope absoluto es el día 90, no 105
await sys(pub(75, 105), '23514');
await sys(pub(75, 90));
const M7 = '20000000-0000-0000-0000-0000000000a7';
await add(M7, TA, 'b');
await sys(`update marketing_media set expires_at = created_at + interval '61 days' where id = '${M7}'`, '23514');   // sin publicar: máximo del plan (60)
await sys(`update marketing_media set protected_until = created_at + interval '105 days' where id = '${M6}'`, '23514');
await sys(`update marketing_media set protected_until = created_at + interval '104 days' where id = '${M6}'`);

// 6. cuota: todo cuenta (originales, derivados, resultados, reservas y trabajos pendientes) y la reserva es atómica
await sys(`update marketing_settings set library_storage_limit_bytes = 0 where tenant_id = '${TB}'`);
ok((await sys(`select public.marketing_reserve_storage('${TB}','upload:k-000001','upload',10) r`))[0].r.reason === 'library_disabled',
  'cuota 0: Biblioteca no habilitada');
await sys(`update marketing_settings set library_storage_limit_bytes = 100 where tenant_id = '${TB}'`);
const MB2 = '20000000-0000-0000-0000-0000000000b9';
await sys(`insert into marketing_media (id, tenant_id, storage_path, media_type, mime_type, byte_size, checksum, uploaded_by)
  values ('${MB2}','${TB}','${TB}/originals/${MB2}/f.png','image','image/png',30,'${'7'.repeat(64)}','${U.ownerB}')`);
await sys(`insert into marketing_media_derivatives (tenant_id, media_id, kind, created_by, byte_size) values ('${TB}','${MB2}','thumbnail','${U.ownerB}',20)`);
const used = async () => Number((await sys(`select public.marketing_storage_used('${TB}') u`))[0].u);
ok(await used() === 50, 'originales + derivados');
const JQ = (await sys(`insert into marketing_generation_jobs (tenant_id, created_by, task_type, real_media_percent, ai_media_percent,
  idempotency_key) values ('${TB}','${U.ownerB}','reel',0,100,'job-quota-key-000001') returning id`))[0].id;
await sys(`insert into marketing_generation_outputs (tenant_id, job_id, kind, byte_size) values ('${TB}','${JQ}','render',7)`);
await sys(`insert into marketing_generation_outputs (tenant_id, job_id, kind, byte_size, purged_at) values ('${TB}','${JQ}','render',99, now())`);
ok(await used() === 57, 'resultados/temporales también cuentan (los purgados no)');
await sys(`update marketing_generation_outputs set purged_at = now() where byte_size = 7`);
ok(await used() === 50, 'purgado: deja de contar');
ok((await sys(`select public.marketing_reserve_storage('${TB}','upload:k-000002','upload',40) r`))[0].r.status === 'reserved', 'reserva');
ok(await used() === 90, 'la reserva cuenta');
const rr = (await sys(`select public.marketing_reserve_storage('${TB}','upload:k-000003','upload',20) r`))[0].r;
ok(rr.status === 'rejected' && rr.reason === 'limit_library_storage' && Number(rr.available) === 10, 'dos reservas no comparten espacio');
ok((await sys(`select public.marketing_reserve_storage('${TB}','upload:k-000002','upload',40) r`))[0].r.status === 'duplicate', 'idempotente');
await sys(`select public.marketing_release_storage('${TB}','upload:k-000002', false)`);
ok(await used() === 50, 'liberada: deja de contar');
// subida registrada: su reserva no cuenta dos veces
const MB3 = '20000000-0000-0000-0000-0000000000ba';
await sys(`select public.marketing_reserve_storage('${TB}','upload:${MB3}','upload',25)`);
await sys(`insert into marketing_media (id, tenant_id, storage_path, media_type, mime_type, byte_size, checksum, uploaded_by)
  values ('${MB3}','${TB}','${TB}/originals/${MB3}/f.png','image','image/png',25,'${'8'.repeat(64)}','${U.ownerB}')`);
ok(await used() === 75, 'sin doble conteo entre la reserva y el archivo');
ok(Number((await sys(`select public.marketing_storage_used('${TA}') u`))[0].u) >= 0
   && (await sys(`select public.marketing_storage_used('${TA}') u`))[0].u !== (await sys(`select public.marketing_storage_used('${TB}') u`))[0].u,
   'cada tenant cuenta lo suyo');
// reservas abandonadas: caducan una sola vez y nunca liberan bytes dos veces
ok((await sys(`select public.marketing_reserve_storage('${TB}','upload:k-000004','upload',10) r`))[0].r.status === 'reserved', 'reserva 10');
ok(await used() === 85, 'cuenta mientras está viva');
await sys(`update marketing_storage_reservations set expires_at = now() - interval '1 second' where reservation_key = 'upload:k-000004'`);
ok(await used() === 75, 'subida interrumpida: caducada deja de contar');
ok((await sys(`select public.marketing_release_storage('${TB}','upload:k-000004', false) r`)).every((x) => x.r === null),
  'una reserva caducada no se libera');
ok((await sys(`select public.marketing_expire_storage_reservations() n`))[0].n >= 1, 'barrido: pasa a expired');
ok((await sys(`select public.marketing_expire_storage_reservations() n`))[0].n === 0, 'idempotente');
ok((await sys(`select status from marketing_storage_reservations where reservation_key = 'upload:k-000004'`))[0].status === 'expired', 'expired');
await sys(`update marketing_storage_reservations set status = 'released' where reservation_key = 'upload:k-000004'`, '23514');
await sys(`update marketing_storage_reservations set status = 'reserved' where reservation_key = 'upload:k-000002'`, '23514');
await sys(`update marketing_storage_reservations set bytes = 1 where reservation_key = 'upload:k-000002'`, '23514');
ok(await used() === 75, 'nada se cuenta ni se libera dos veces');
// reserva de un trabajo: se cierra sola cuando el trabajo termina (cancelado, fallido, timeout, consentimiento, purga)
const job = async (k) => (await sys(`insert into marketing_generation_jobs (tenant_id, created_by, task_type, real_media_percent,
  ai_media_percent, idempotency_key) values ('${TB}','${U.ownerB}','reel',0,100,'job-quota-key-${k}') returning id`))[0].id;
const JR = await job('000002');
await sys(`insert into marketing_storage_reservations (tenant_id, reservation_key, kind, bytes, expires_at)
  values ('${TB}','job:${JR}','generation',5, now() + interval '24 hours')`);
ok(await used() === 80, 'trabajo pendiente: su reserva cuenta');
await sys(`update marketing_generation_jobs set status = 'cancelled' where id = '${JR}'`);
ok((await sys(`select status from marketing_storage_reservations where reservation_key = 'job:${JR}'`))[0].status === 'released',
  'trabajo cancelado: reserva liberada');
ok(await used() === 75, 'y deja de contar');
// confirmar resultado: tamaño real frente a lo reservado + lo disponible
const JC = await job('000003');
await sys(`insert into marketing_storage_reservations (tenant_id, reservation_key, kind, bytes, expires_at)
  values ('${TB}','job:${JC}','generation',5, now() + interval '24 hours')`);
const conf = async (j, b) => (await sys(`select public.marketing_confirm_output_storage('${TB}','${j}',${b}) r`))[0].r;
ok((await conf(JC, 26)).reason === 'storage_quota_exceeded', 'resultado real más grande de lo que cabe: rechazado');
ok((await sys(`select bytes from marketing_storage_reservations where reservation_key = 'job:${JC}'`))[0].bytes == 5, 'rechazo sin cambios');
ok((await conf(JC, 0)).reason === 'invalid_size', 'tamaño inválido');
ok((await conf(JR, 1)).reason === 'reservation_expired', 'sin reserva viva no se confirma');
ok((await conf(JC, 25)).status === 'ok' && await used() === 100, 'cabe justo: la reserva pasa al tamaño real');
await sys(`update marketing_generation_jobs set status = 'cancelled' where id = '${JC}'`);
ok(await used() === 75, 'fallido/cancelado: liberado una vez');
const fsrc = (await sys(`select prosrc from pg_proc where proname = 'marketing_reserve_storage'`))[0].prosrc;
ok(/from public\.marketing_settings where tenant_id = p_tenant for update/i.test(fsrc), 'reserva serializada por tenant');

// 7. tokens de streaming: solo hash, caducidad corta, nadie más que el backend los lee
await sys(`insert into marketing_stream_tokens (tenant_id, user_id, media_id, token_hash, expires_at)
  values ('${TB}','${U.ownerB}','${MB2}','${'a'.repeat(64)}', now() + interval '11 minutes')`, '23514');   // sesión ≤ 10 min
await sys(`insert into marketing_stream_tokens (tenant_id, user_id, media_id, token_hash, expires_at)
  values ('${TB}','${U.ownerB}','${MB2}','plaintext-token', now() + interval '5 minutes')`, '23514');
await sys(`insert into marketing_stream_tokens (tenant_id, user_id, media_id, token_hash, expires_at)
  values ('${TA}','${U.ownerB}','${MB2}','${'b'.repeat(64)}', now() + interval '5 minutes')`, '23503');   // archivo de otro tenant
await sys(`insert into marketing_stream_tokens (tenant_id, user_id, media_id, token_hash, expires_at)
  values ('${TB}','${U.ownerB}','${MB2}','${'c'.repeat(64)}', now() + interval '5 minutes')`);
for (const role of ['anon', 'authenticated']) {
  for (const t of ['marketing_stream_tokens', 'marketing_storage_reservations']) {
    ok(!(await sys(`select has_table_privilege('${role}', 'public.${t}', 'SELECT') x`))[0].x, `${role} no lee ${t}`);
  }
  for (const f of ['marketing_storage_used(uuid)', 'marketing_reserve_storage(uuid,text,text,bigint)', 'marketing_release_storage(uuid,text,boolean)',
    'marketing_expire_storage_reservations()', 'marketing_confirm_output_storage(uuid,uuid,bigint)']) {
    ok(!(await sys(`select has_function_privilege('${role}', 'public.${f}', 'EXECUTE') x`))[0].x, `${role} no ejecuta ${f}`);
  }
}

// 8. el tenant no puede alargar su retención máxima
await db.exec(`begin; set local role authenticated`);
await db.query("select set_config('request.jwt.claims', $1, true)", [JSON.stringify({ sub: U.ownerA })]);
try { await db.query(`update marketing_settings set max_retention_days = 90`); assert.fail('debía fallar'); }
catch (e) { assert.equal(e.code, '42501'); checks++; }
await db.exec('rollback');

console.log(`ALL MARKETING RETENTION SQL TESTS PASSED (${checks} checks)`);
