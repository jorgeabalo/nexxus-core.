// AITA Marketing — ampliaciones mensuales de publicaciones (recurrentes hasta cancelación). PGlite, solo datos de prueba.
// Precio generado por la base, solicitud (manager) y autorización (owner o manager delegado), transiciones de un
// solo sentido, nada se borra, RLS por tenant y rol, permisos mínimos, atomicidad y repetibilidad.
import { createRequire } from 'node:module';
import { readFile } from 'node:fs/promises';
import assert from 'node:assert/strict';
const require = createRequire(import.meta.url);
const { PGlite } = require('@electric-sql/pglite');
const file = (p) => readFile(new URL(p, import.meta.url), 'utf8');
const BASE = await file('../../supabase/migrations/20261009120000_aita_marketing.sql');
const MIG = await file('../../supabase/migrations/20261013120000_marketing_publication_addons.sql');
const TA = '10000000-0000-0000-0000-00000000000a', TB = '10000000-0000-0000-0000-00000000000b';
const O = '00000000-0000-0000-0000-00000000000a', M = '00000000-0000-0000-0000-00000000000b';
const S = '00000000-0000-0000-0000-00000000000c', OB = '00000000-0000-0000-0000-00000000000e';
let checks = 0, k = 0;
const ok = (v, msg) => { assert.ok(v, msg); checks++; };

async function setup() {
  const db = new PGlite();
  await db.exec(await file('./bootstrap_local.sql'));
  await db.exec(`insert into auth.users(id) values ('${O}'),('${M}'),('${S}'),('${OB}');
    insert into tenants(id,slug,name) values ('${TA}','a','A'),('${TB}','b','B');
    insert into tenant_users(tenant_id,user_id,role) values ('${TA}','${O}','owner'),('${TA}','${M}','manager'),
      ('${TA}','${S}','staff'),('${TB}','${OB}','owner');`);
  await db.exec(BASE);
  return db;
}
async function expect(db, sql, code) {
  try { await db.query(sql); assert.fail(`expected ${code}: ${sql}`); } catch (e) { if (e.code !== code) throw e; checks++; }
}
async function as(db, user, sql) {
  await db.exec('begin; set local role authenticated');
  await db.query("select set_config('request.jwt.claims',$1,true)", [JSON.stringify({ sub: user })]);
  try { return (await db.query(sql)).rows; } finally { await db.exec('rollback'); }
}
const key = () => `addon-sql-${String(++k).padStart(8, '0')}`;
const COLS = 'tenant_id,cadence,per_week,additional_publications,starts_cycle,status,requested_by,decided_by,decided_at,idempotency_key';
const row = (vals) => `insert into marketing_publication_addons(${COLS}) values (${vals})`;
const add = async (db, { n = 1, tenant = TA, by = O, status = 'authorized', cadence = 'monthly', perWeek = null } = {}) =>
  (await db.query(`${row('$1,$2,$3,$4,\'2026-10-01\',$5,$6,$7,$8,$9')} returning *`,
    [tenant, cadence, perWeek, n, status, by, status === 'requested' ? null : by,
      status === 'requested' ? null : new Date().toISOString(), key()])).rows[0];

const db = await setup();
await db.exec(MIG);
await db.exec(MIG); // repetible

// 1. precio exacto más económico, calculado por la base
for (const [n, amount, b8, b4, b1] of [[1, 600, 0, 0, 1], [4, 2000, 0, 1, 0], [8, 3500, 1, 0, 0], [12, 5500, 1, 1, 0],
  [22, 10200, 2, 1, 2], [37, 16600, 4, 1, 1]]) {
  const r = await add(db, { n });
  ok(r.amount_cents === amount && r.blocks_8 === b8 && r.blocks_4 === b4 && r.blocks_1 === b1, `precio ${n}`);
}
const best = [0];
for (let i = 1; i <= 500; i++) best[i] = Math.min(...[[8, 3500], [4, 2000], [1, 600]].filter(([s]) => s <= i).map(([s, p]) => p + best[i - s]));
const formula = (await db.query(`select n, (n/8)*3500 + ((n%8)/4)*2000 + (n%4)*600 c from generate_series(1,500) n`)).rows;
ok(formula.every(r => r.c === best[r.n]), 'la fórmula generada es el óptimo exacto para 1..500');
const w = await add(db, { cadence: 'weekly', perWeek: 2, n: 8 });
ok(w.amount_cents === 3500 && w.per_week === 2, 'semanal: 2/sem = 8/mes = $35');

// 2. restricciones
const v = (o = {}) => {
  const x = { t: TA, cad: "'monthly'", pw: 'null', n: 1, st: "'2026-10-01'", s: "'authorized'", rb: `'${O}'`, db: `'${O}'`, da: 'now()', k: `'${key()}'`, ...o };
  return row(`'${x.t}',${x.cad},${x.pw},${x.n},${x.st},${x.s},${x.rb},${x.db},${x.da},${x.k}`);
};
await expect(db, `insert into marketing_publication_addons(${COLS},amount_cents) values ('${TA}','monthly',null,1,'2026-10-01','authorized','${O}','${O}',now(),'${key()}',1)`, '428C9');
await expect(db, `insert into marketing_publication_addons(${COLS},blocks_1) values ('${TA}','monthly',null,1,'2026-10-01','authorized','${O}','${O}',now(),'${key()}',9)`, '428C9');
await expect(db, `insert into marketing_publication_addons(${COLS},currency) values ('${TA}','monthly',null,1,'2026-10-01','authorized','${O}','${O}',now(),'${key()}','EUR')`, '23514');
for (const [o, why] of [[{ st: "'2026-10-02'" }, 'ciclo que no empieza el día 1'], [{ n: 0 }, 'cero'], [{ n: 501 }, '501'],
  [{ cad: "'weekly'", pw: 2, n: 9 }, 'semanal distinto de 4×'], [{ cad: "'weekly'", n: 8 }, 'semanal sin per_week'],
  [{ k: "'short'" }, 'clave'], [{ cad: "'daily'" }, 'frecuencia']]) {
  await expect(db, v(o), '23514'); ok(true, why);
}
await expect(db, v({ k: "'addon-sql-00000001'" }), '23505');
await expect(db, v({ db: 'null', da: 'null' }), '42501');                                  // autorizada sin quien decida

// 3. quién solicita y quién autoriza (la base también lo exige)
await expect(db, v({ rb: `'${M}'`, db: `'${M}'` }), '42501');                              // manager sin permiso
await expect(db, v({ s: "'requested'", rb: `'${S}'`, db: 'null', da: 'null' }), '42501');   // staff
await expect(db, v({ s: "'requested'", rb: `'${OB}'`, db: 'null', da: 'null' }), '42501');  // owner de otro tenant
await expect(db, v({ s: "'declined'" }), '23514');                                         // solo se nace requested/authorized
const req = await add(db, { n: 12, by: M, status: 'requested' });
ok(req.status === 'requested' && req.amount_cents === 5500, 'el manager solicita');
await expect(db, `update marketing_publication_addons set status='authorized', decided_by='${M}', decided_at=now() where id='${req.id}'`, '42501');
await expect(db, `insert into marketing_purchase_permissions(tenant_id, managers_can_authorize, updated_by) values ('${TA}', true, '${M}')`, '42501');
await db.query(`insert into marketing_purchase_permissions(tenant_id, managers_can_authorize, updated_by) values ('${TA}', true, '${O}')`);
await db.query(`update marketing_publication_addons set status='authorized', decided_by='${M}', decided_at=now() where id='${req.id}'`);
ok((await db.query(`select status from marketing_publication_addons where id='${req.id}'`)).rows[0].status === 'authorized', 'manager delegado autoriza');
await expect(db, `update marketing_purchase_permissions set managers_can_authorize=false, updated_by='${M}' where tenant_id='${TA}'`, '42501');
await db.query(`update marketing_purchase_permissions set managers_can_authorize=false, updated_by='${O}', updated_at=now() where tenant_id='${TA}'`);
const req2 = await add(db, { n: 4, by: M, status: 'requested' });
await expect(db, `update marketing_publication_addons set status='authorized', decided_by='${M}', decided_at=now() where id='${req2.id}'`, '42501');
await db.query(`update marketing_publication_addons set status='declined', decided_by='${O}', decided_at=now() where id='${req2.id}'`);
ok(true, 'el owner rechaza');

// 4. transiciones de un solo sentido; nada se borra ni se altera
await expect(db, `update marketing_publication_addons set status='authorized', decided_by='${O}' where id='${req2.id}'`, '42501');
await expect(db, `update marketing_publication_addons set additional_publications=2 where id='${req.id}'`, '42501');
await expect(db, `update marketing_publication_addons set idempotency_key='${key()}' where id='${req.id}'`, '42501');
await expect(db, `update marketing_publication_addons set tenant_id='${TB}' where id='${req.id}'`, '42501');
await expect(db, `delete from marketing_publication_addons where id='${req.id}'`, '42501');
await expect(db, `delete from marketing_purchase_permissions`, '42501');
await expect(db, `update marketing_publication_addons set status='cancelled', cancelled_by='${M}', cancelled_at=now(), ends_cycle='2099-01-01' where id='${req.id}'`, '42501');
await expect(db, `update marketing_publication_addons set status='cancelled', cancelled_by='${O}', cancelled_at=now(), ends_cycle='2026-10-01' where id='${req.id}'`, '23514');
const old = (await db.query(`${row(`'${TA}','monthly',null,4,'2026-01-01','authorized','${O}','${O}',now(),'${key()}'`)} returning id`)).rows[0];
await expect(db, `update marketing_publication_addons set status='cancelled', cancelled_by='${O}', cancelled_at=now(), ends_cycle='2026-05-01' where id='${old.id}'`, '23514');   // retroactivo
await db.query(`update marketing_publication_addons set status='cancelled', cancelled_by='${O}', cancelled_at=now(), ends_cycle='2099-01-01' where id='${req.id}'`);
await expect(db, `update marketing_publication_addons set status='authorized' where id='${req.id}'`, '42501');

// 5. RLS y permisos
await add(db, { n: 1, tenant: TB, by: OB });
ok((await as(db, O, 'select tenant_id from marketing_publication_addons')).every(r => r.tenant_id === TA), 'owner: su tenant');
ok((await as(db, M, 'select tenant_id from marketing_publication_addons')).length > 0, 'manager lee su tenant');
ok((await as(db, S, 'select tenant_id from marketing_publication_addons')).length === 0, 'staff no ve nada');
ok((await as(db, OB, 'select tenant_id from marketing_publication_addons')).every(r => r.tenant_id === TB), 'otro tenant aislado');
ok((await as(db, S, 'select * from marketing_purchase_permissions')).length === 0, 'staff no ve permisos');
for (const [sql, what] of [[v(), 'insert'], ['update marketing_publication_addons set status = status', 'update'],
  ['delete from marketing_publication_addons', 'delete'], ['update marketing_purchase_permissions set managers_can_authorize = true', 'permiso']]) {
  await db.exec('begin; set local role authenticated');
  await db.query("select set_config('request.jwt.claims',$1,true)", [JSON.stringify({ sub: O })]);
  try { await db.query(sql); assert.fail(`owner ${what} directo`); } catch (e) { assert.equal(e.code, '42501', `${what}: ${e.message}`); checks++; }
  finally { await db.exec('rollback'); }
}
const priv = async (role, table, p) => (await db.query(`select has_table_privilege('${role}','public.${table}','${p}') ok`)).rows[0].ok;
for (const t of ['marketing_publication_addons', 'marketing_purchase_permissions']) {
  ok(!(await priv('anon', t, 'SELECT')), `anon sin lectura ${t}`);
  ok(await priv('authenticated', t, 'SELECT') && !(await priv('authenticated', t, 'INSERT')), `authenticated solo lectura ${t}`);
  ok(await priv('service_role', t, 'INSERT') && !(await priv('service_role', t, 'DELETE')) && !(await priv('service_role', t, 'TRUNCATE')),
    `service_role sin borrar ${t}`);
}
const colUpd = async (c) => (await db.query(`select has_column_privilege('service_role','public.marketing_publication_addons','${c}','UPDATE') ok`)).rows[0].ok;
ok(await colUpd('status') && !(await colUpd('additional_publications')) && !(await colUpd('tenant_id')) && !(await colUpd('idempotency_key')),
  'service_role solo actualiza columnas de transición');
ok((await db.query(`select count(*)::int n from marketing_publication_addons where amount_cents <> blocks_8*3500 + blocks_4*2000 + blocks_1*600
  or blocks_8*8 + blocks_4*4 + blocks_1 <> additional_publications`)).rows[0].n === 0, 'bloques y precio coherentes');

// 6. atomicidad
const db2 = await setup();
try { await db2.exec(`${MIG}\nselect 1/0;`); assert.fail('debía fallar'); } catch { checks++; }
ok((await db2.query(`select count(*)::int n from pg_class where relname in ('marketing_publication_addons','marketing_purchase_permissions')`)).rows[0].n === 0,
  'migración atómica');
console.log(`ALL MARKETING PUBLICATION ADDON SQL TESTS PASSED (${checks} checks)`);
