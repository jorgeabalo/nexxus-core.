// AITA Marketing — cupo de «Escribir con IA» (20261101120000). PGlite en memoria, datos de prueba.
// La reserva cuenta y registra en una sola llamada; las reservas sin liquidar cuentan; el límite es por tenant;
// solo el backend (service_role) puede reservar.
// Ejecutar: NODE_PATH=/ruta/node_modules node tests/sql/marketing_text_quota.mjs
import { createRequire } from 'node:module';
import { readFile } from 'node:fs/promises';
import assert from 'node:assert/strict';
const require = createRequire(import.meta.url);
const { PGlite } = require('@electric-sql/pglite');

const file = (p) => readFile(new URL(p, import.meta.url), 'utf8');
const MIGS = ['20261009120000_aita_marketing.sql', '20261016120000_marketing_text_ai.sql',
  '20261101120000_marketing_text_quota.sql'];
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
await db.exec(await file(`../../supabase/migrations/${MIGS[2]}`));                         // idempotente

const reserve = async (t, limit, actor = U.ownerA) => (await db.query(
  `select public.marketing_reserve_text_generation('${t}', null, '${actor}', 'claude-opus-5-5', ${limit}) r`)).rows[0].r;

// 1. reserva dentro del cupo: fila 'pending' con id
const a = await reserve(TA, 2);
ok(a.status === 'reserved' && Number.isInteger(a.id), 'primera reserva');
const row = (await db.query(`select status, input_tokens from marketing_text_generations where id = ${a.id}`)).rows[0];
ok(row.status === 'pending' && row.input_tokens === 0, 'queda pendiente, sin tokens');
// 2. las pendientes cuentan: con límite 2 cabe una más y la tercera se rechaza
ok((await reserve(TA, 2)).status === 'reserved', 'segunda reserva');
const third = await reserve(TA, 2);
ok(third.status === 'rejected' && third.reason === 'limit_text_ai', 'la tercera supera el cupo');
ok(Number((await db.query(`select count(*) n from marketing_text_generations where tenant_id = '${TA}'`)).rows[0].n) === 2,
  'el rechazo no inserta');
// 3. liquidar no libera cupo (cuenta igual) y solo vale sobre 'pending'
await db.exec(`update marketing_text_generations set status = 'succeeded', input_tokens = 900 where id = ${a.id}`);
ok((await reserve(TA, 2)).status === 'rejected', 'una redacción terminada sigue contando');
// 4. lo de hace más de 24 h ya no cuenta
await db.exec(`update marketing_text_generations set created_at = now() - interval '25 hours' where tenant_id = '${TA}'`);
ok((await reserve(TA, 2)).status === 'reserved', 'pasadas 24 h vuelve a haber cupo');
// 5. por tenant: el cupo de A no afecta a B
ok((await reserve(TB, 1)).status === 'reserved', 'B tiene su propio cupo');
ok((await reserve(TB, 1)).status === 'rejected', 'y también se agota');
// 6. límite 0 o nulo: cerrado
ok((await reserve(TA, 0)).status === 'rejected', 'límite 0 cerrado');
ok((await reserve(TA, 'null')).status === 'rejected', 'límite nulo cerrado');
// 7. el estado se valida
await assert.rejects(db.exec(`update marketing_text_generations set status = 'raro' where id = ${a.id}`),
  (e) => e.code === '23514'); checks++;
// 8. privilegios: ni anon ni authenticated pueden reservar
for (const role of ['anon', 'authenticated']) {
  const r = (await db.query(`select has_function_privilege('${role}',
    'public.marketing_reserve_text_generation(uuid, uuid, uuid, text, integer)', 'execute') p`)).rows[0].p;
  ok(r === false, `${role} no puede reservar`);
}

console.log(`marketing_text_quota: ${checks} checks OK`);
