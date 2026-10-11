// AITA Marketing — ampliaciones mensuales de publicaciones. PGlite, solo datos de prueba.
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
let checks = 0;
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
async function visible(db, user) {
  await db.exec('begin; set local role authenticated');
  await db.query("select set_config('request.jwt.claims',$1,true)", [JSON.stringify({ sub: user })]);
  try { return (await db.query('select tenant_id,additional_publications from marketing_publication_addons')).rows; }
  finally { await db.exec('rollback'); }
}

const db = await setup();
await db.exec(MIG);
await db.exec(MIG); // repetible
const add = async (n, key, tenant = TA, user = O) => (await db.query(`insert into marketing_publication_addons
  (tenant_id,cycle_start,additional_publications,requested_by,authorized_by,idempotency_key)
  values ($1,'2026-10-01',$2,$3,$3,$4) returning *`, [tenant, n, user, key])).rows[0];
for (const [n, amount, b8, b4, b1] of [[1,600,0,0,1],[4,2000,0,1,0],[8,3500,1,0,0],[22,10200,2,1,2],[37,16600,4,1,1]]) {
  const r = await add(n, `addon-sql-${String(n).padStart(8,'0')}`);
  ok(r.amount_cents === amount && r.blocks_8 === b8 && r.blocks_4 === b4 && r.blocks_1 === b1, `precio ${n}`);
}
await expect(db, `insert into marketing_publication_addons(tenant_id,cycle_start,additional_publications,requested_by,authorized_by,idempotency_key)
  values ('${TA}','2026-10-02',1,'${O}','${O}','addon-invalid-date')`, '23514');
await expect(db, `insert into marketing_publication_addons(tenant_id,cycle_start,additional_publications,requested_by,authorized_by,idempotency_key)
  values ('${TA}','2026-10-01',0,'${O}','${O}','addon-invalid-zero')`, '23514');
await expect(db, `insert into marketing_publication_addons(tenant_id,cycle_start,additional_publications,requested_by,authorized_by,idempotency_key)
  values ('${TA}','2026-10-01',1,'${O}','${O}','addon-sql-00000001')`, '23505');
await expect(db, `update marketing_publication_addons set additional_publications=2`, '42501');
await expect(db, `delete from marketing_publication_addons`, '42501');
await add(1, 'addon-other-tenant', TB, OB);
ok((await visible(db, O)).every(r => r.tenant_id === TA), 'owner ve solo su tenant');
ok((await visible(db, M)).length === 5, 'manager ve su tenant');
ok((await visible(db, S)).length === 0, 'staff no ve ampliaciones');
ok((await visible(db, OB)).length === 1, 'otro owner ve solo su tenant');
for (const role of ['anon']) {
  const p = (await db.query(`select has_table_privilege($1,'public.marketing_publication_addons','SELECT') ok`, [role])).rows[0].ok;
  ok(!p, `${role} sin lectura`);
}
ok(!(await db.query(`select coalesce(bool_or(a.grantee=0 and a.privilege_type='SELECT'),false) ok
  from pg_class c left join lateral aclexplode(c.relacl) a on true
  where c.oid='public.marketing_publication_addons'::regclass`)).rows[0].ok, 'PUBLIC sin lectura');
ok((await db.query(`select has_table_privilege('authenticated','public.marketing_publication_addons','SELECT') ok`)).rows[0].ok,
  'authenticated solo lectura');
ok(!(await db.query(`select has_table_privilege('authenticated','public.marketing_publication_addons','INSERT') ok`)).rows[0].ok,
  'authenticated sin escritura');
ok((await db.query(`select has_table_privilege('service_role','public.marketing_publication_addons','INSERT') ok`)).rows[0].ok,
  'backend inserta');
ok(!(await db.query(`select has_table_privilege('service_role','public.marketing_publication_addons','UPDATE') ok`)).rows[0].ok,
  'backend no altera autorizaciones');

// Revisión: precio generado, moneda, clave, rango y escritura del tenant bloqueada aunque sea owner.
const ins = (cols, vals) => `insert into marketing_publication_addons(tenant_id,cycle_start,additional_publications,requested_by,authorized_by,idempotency_key${cols})
  values ('${TA}','2026-10-01',1,'${O}','${O}','addon-review-${String(++checks).padStart(6,'0')}'${vals})`;
await expect(db, ins(',amount_cents', ',1'), '428C9');
await expect(db, ins(',blocks_1', ',9'), '428C9');
await expect(db, ins(',currency', ",'EUR'"), '23514');
await expect(db, `insert into marketing_publication_addons(tenant_id,cycle_start,additional_publications,requested_by,authorized_by,idempotency_key)
  values ('${TA}','2026-10-01',501,'${O}','${O}','addon-review-toomany')`, '23514');
await expect(db, `insert into marketing_publication_addons(tenant_id,cycle_start,additional_publications,requested_by,authorized_by,idempotency_key)
  values ('${TA}','2026-10-01',1,'${O}','${O}','short')`, '23514');
for (const [sql, what] of [[`insert into marketing_publication_addons(tenant_id,cycle_start,additional_publications,requested_by,authorized_by,idempotency_key)
    values ('${TA}','2026-10-01',1,'${O}','${O}','addon-owner-direct-01')`, 'insert'],
  ['update marketing_publication_addons set additional_publications = 1', 'update'],
  ['delete from marketing_publication_addons', 'delete']]) {
  await db.exec('begin; set local role authenticated');
  await db.query("select set_config('request.jwt.claims',$1,true)", [JSON.stringify({ sub: O })]);
  try { await db.query(sql); assert.fail(`owner ${what} directo`); } catch (e) { assert.equal(e.code, '42501', `${what}: ${e.message}`); checks++; }
  finally { await db.exec('rollback'); }
}
for (const p of ['UPDATE', 'DELETE', 'TRUNCATE']) {
  ok(!(await db.query(`select has_table_privilege('service_role','public.marketing_publication_addons','${p}') ok`)).rows[0].ok,
    `service_role sin ${p}`);
  ok(!(await db.query(`select has_table_privilege('authenticated','public.marketing_publication_addons','${p}') ok`)).rows[0].ok,
    `authenticated sin ${p}`);
}
ok((await db.query(`select count(*)::int n from marketing_publication_addons where amount_cents <> (blocks_8*3500 + blocks_4*2000 + blocks_1*600)
  or blocks_8*8 + blocks_4*4 + blocks_1 <> additional_publications`)).rows[0].n === 0, 'bloques exactos y precio coherente');

const db2 = await setup();
try { await db2.exec(`${MIG}\nselect 1/0;`); assert.fail('debía fallar'); } catch { checks++; }
ok((await db2.query(`select count(*)::int n from pg_class where relname='marketing_publication_addons'`)).rows[0].n === 0,
  'migración atómica');
console.log(`ALL MARKETING PUBLICATION ADDON SQL TESTS PASSED (${checks} checks)`);
