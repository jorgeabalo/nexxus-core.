// AITA Marketing — mínimo privilegio de la migración (PGlite en memoria, datos solo de prueba).
// Simula los privilegios por defecto de Supabase (ALL para anon/authenticated/service_role en tablas
// nuevas de public) y, como peor caso, también para PUBLIC. Comprueba en el catálogo y en la práctica:
//   PUBLIC/anon: nada · authenticated: solo SELECT · service_role: SELECT/INSERT/UPDATE/DELETE.
// Ejecutar: NODE_PATH=/ruta/node_modules node tests/sql/marketing_privileges.mjs
import { createRequire } from 'node:module';
import { readFile } from 'node:fs/promises';
import assert from 'node:assert/strict';
const require = createRequire(import.meta.url);
const { PGlite } = require('@electric-sql/pglite');

const file = (p) => readFile(new URL(p, import.meta.url), 'utf8');
const MIG = await file('../../supabase/migrations/20261009120000_aita_marketing.sql');
const TABLES = ['marketing_settings', 'marketing_brand_profiles', 'marketing_campaigns', 'marketing_content',
  'marketing_assets', 'marketing_approval_events', 'marketing_publications'];
const PRIVS = ['SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE', 'REFERENCES', 'TRIGGER'];
const U = { ownerA: '00000000-0000-0000-0000-00000000000a', mgrA: '00000000-0000-0000-0000-00000000000b',
  staffA: '00000000-0000-0000-0000-00000000000c', ownerB: '00000000-0000-0000-0000-00000000000e' };
const TA = '10000000-0000-0000-0000-00000000000a', TB = '10000000-0000-0000-0000-00000000000b';
let checks = 0;
const ok = (cond, msg) => { assert.ok(cond, msg); checks++; };

async function prodLike() {
  const db = new PGlite();
  await db.exec(await file('./bootstrap_local.sql'));
  await db.exec(`
    alter default privileges in schema public grant all on tables to anon, authenticated, service_role, public;
    insert into auth.users (id) values ('${U.ownerA}'),('${U.mgrA}'),('${U.staffA}'),('${U.ownerB}');
    insert into tenants (id, slug, name) values ('${TA}','ta','Tenant A'),('${TB}','tb','Tenant B');
    insert into tenant_users (tenant_id, user_id, role) values
      ('${TA}','${U.ownerA}','owner'),('${TA}','${U.mgrA}','manager'),('${TA}','${U.staffA}','staff'),('${TB}','${U.ownerB}','owner');`);
  return db;
}
const pgVersion = async (db) => Number((await db.query('show server_version_num')).rows[0].server_version_num);

// privilegios exactos por rol leyendo el ACL de cada tabla (grantee 0 = PUBLIC)
async function acl(db, table) {
  const rows = (await db.query(`select coalesce(r.rolname, 'PUBLIC') grantee, a.privilege_type p
    from pg_class c, aclexplode(c.relacl) a left join pg_roles r on r.oid = a.grantee
    where c.oid = $1::regclass`, [`public.${table}`])).rows;
  const out = {};
  for (const r of rows) (out[r.grantee] = out[r.grantee] || new Set()).add(r.p);
  return out;
}
async function checkAcl(db, label) {
  const v = await pgVersion(db);
  for (const t of TABLES) {
    const a = await acl(db, t);
    ok(!a.PUBLIC, `${label}: PUBLIC sin privilegios en ${t}`);
    ok(!a.anon, `${label}: anon sin privilegios en ${t}`);
    assert.deepEqual([...(a.authenticated || [])].sort(), ['SELECT'], `${label}: authenticated solo SELECT en ${t}`); checks++;
    assert.deepEqual([...(a.service_role || [])].sort(), ['DELETE', 'INSERT', 'SELECT', 'UPDATE'], `${label}: service_role en ${t}`); checks++;
    for (const p of PRIVS) {
      ok(!(await db.query(`select has_table_privilege('anon', 'public.${t}', '${p}') x`)).rows[0].x, `anon ${p} ${t}`);
      const expectAuth = p === 'SELECT';
      ok((await db.query(`select has_table_privilege('authenticated', 'public.${t}', '${p}') x`)).rows[0].x === expectAuth,
        `authenticated ${p} ${t}`);
    }
    if (v >= 170000) {   // MAINTAIN existe desde PostgreSQL 17 (producción: 17.6)
      for (const role of ['anon', 'authenticated', 'service_role']) {
        ok(!(await db.query(`select has_table_privilege('${role}', 'public.${t}', 'MAINTAIN') x`)).rows[0].x, `${role} MAINTAIN ${t}`);
      }
    }
  }
}

async function as(db, role, user, sql, expected) {
  await db.exec(`begin; set local role ${role}`);
  if (user) await db.query("select set_config('request.jwt.claims', $1, true)", [JSON.stringify({ sub: user })]);
  try {
    const r = await db.query(sql);
    if (expected) assert.fail(`${role}: se esperaba SQLSTATE ${expected} en: ${sql}`);
    await db.exec('commit');
    return r.rows;
  } catch (e) {
    await db.exec('rollback');
    if (!expected || e.code !== expected) throw e;
    checks++;
    return null;
  }
}

// ------------------------------------------------------------ 1. aplicar dos veces (repetible)
const db = await prodLike();
await db.exec(MIG);
await checkAcl(db, '1ª vez');
await db.exec(MIG);
await checkAcl(db, '2ª vez');

// datos del backend (service_role conserva las escrituras necesarias)
await db.exec(`update marketing_settings set marketing_enabled = true where tenant_id = '${TA}'`);
const camp = (await as(db, 'service_role', null,
  `insert into marketing_campaigns (tenant_id, name, created_by) values ('${TA}','Octubre','${U.ownerA}') returning id`))[0].id;
const content = (await as(db, 'service_role', null,
  `insert into marketing_content (tenant_id, campaign_id, title, format, created_by) values ('${TA}','${camp}','Post','reel','${U.ownerA}') returning id`))[0].id;
await as(db, 'service_role', null, `update marketing_content set title = 'Post 2' where id = '${content}'`);
await as(db, 'service_role', null, `insert into marketing_brand_profiles (tenant_id, tone) values ('${TA}','Claro')
  on conflict (tenant_id) do update set tone = excluded.tone`);
await as(db, 'service_role', null, `insert into marketing_approval_events (tenant_id, content_id, action, to_status, actor_id)
  values ('${TA}','${content}','create','draft','${U.ownerA}')`);
await as(db, 'service_role', null, `insert into marketing_publications (tenant_id, content_id, channel, idempotency_key)
  values ('${TA}','${content}','instagram','${content}:instagram:k')`);
ok((await as(db, 'service_role', null, `select count(*)::int n from marketing_content`))[0].n === 1, 'service_role lee');
await as(db, 'service_role', null, `delete from marketing_brand_profiles where tenant_id = '${TA}'`);
// pero ni el backend puede truncar ni saltarse la inmutabilidad del historial
await as(db, 'service_role', null, `truncate marketing_content cascade`, '42501');
await as(db, 'service_role', null, `delete from marketing_approval_events`, '42501');
await as(db, 'service_role', null, `insert into marketing_brand_profiles (tenant_id) values ('${TA}')`);

// ------------------------------------------------------------ 2. authenticated: solo leer
for (const t of TABLES) {
  await as(db, 'authenticated', U.ownerA, `insert into ${t} (tenant_id) values ('${TA}')`, '42501');
  await as(db, 'authenticated', U.ownerA, `update ${t} set tenant_id = tenant_id`, '42501');
  await as(db, 'authenticated', U.ownerA, `delete from ${t}`, '42501');
  await as(db, 'authenticated', U.ownerA, `truncate ${t} cascade`, '42501');
  await as(db, 'authenticated', U.ownerA, `create trigger zz_${t} before insert on ${t} for each row execute function public.touch_updated_at()`, '42501');
  await as(db, 'authenticated', U.ownerA, `lock table ${t} in exclusive mode`, '42501');      // bloqueo/mantenimiento
  await as(db, 'anon', null, `select 1 from ${t}`, '42501');
}

// ------------------------------------------------------------ 3. RLS: aislamiento y owner/manager
const visible = async (user, t) => (await as(db, 'authenticated', user, `select tenant_id from ${t}`));
for (const t of ['marketing_settings', 'marketing_campaigns', 'marketing_content', 'marketing_approval_events', 'marketing_publications']) {
  const own = await visible(U.ownerA, t);
  ok(own.length > 0 && own.every(r => r.tenant_id === TA), `owner A ve solo A en ${t}`);
  ok((await visible(U.mgrA, t)).length === own.length, `manager A ve lo mismo en ${t}`);
  ok((await visible(U.staffA, t)).length === 0, `staff no ve ${t}`);
  ok((await visible(U.ownerB, t)).every(r => r.tenant_id === TB), `owner B no ve A en ${t}`);
}

// ------------------------------------------------------------ 4. atómica: un error al final no deja nada aplicado
const db2 = await prodLike();
let failed = false;
try { await db2.exec(`${MIG}\nselect 1/0;`); } catch (e) { failed = true; }
ok(failed, 'el script con error falla');
ok((await db2.query(`select count(*)::int n from pg_class where relname like 'marketing%'`)).rows[0].n === 0, 'no queda nada aplicado');

console.log(`ALL MARKETING PRIVILEGE TESTS PASSED (${checks} checks, PostgreSQL ${await pgVersion(db)})`);
