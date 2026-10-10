// Pruebas de AITA Marketing (RLS, restricciones y reglas de estado). Base PGlite en memoria, datos solo de prueba.
// Ejecutar con NODE_PATH apuntando a una instalación de desarrollo de @electric-sql/pglite:
//   NODE_PATH=/ruta/node_modules node tests/sql/marketing.mjs
import { createRequire } from 'node:module';
import { readFile } from 'node:fs/promises';
import assert from 'node:assert/strict';
const require = createRequire(import.meta.url);
const { PGlite } = require('@electric-sql/pglite');

const db = new PGlite();
const file = (p) => readFile(new URL(p, import.meta.url), 'utf8');
const MIG = '../../supabase/migrations/20261009120000_aita_marketing.sql';
await db.exec(await file('./bootstrap_local.sql'));

const U = { ownerA: '00000000-0000-0000-0000-00000000000a', mgrA: '00000000-0000-0000-0000-00000000000b',
            staffA: '00000000-0000-0000-0000-00000000000c', member: '00000000-0000-0000-0000-00000000000d',
            ownerB: '00000000-0000-0000-0000-00000000000e' };
const TA = '10000000-0000-0000-0000-00000000000a', TB = '10000000-0000-0000-0000-00000000000b';
await db.exec(`
  insert into auth.users (id) values ('${U.ownerA}'),('${U.mgrA}'),('${U.staffA}'),('${U.member}'),('${U.ownerB}');
  insert into tenants (id, slug, name) values ('${TA}','ta','Tenant A'),('${TB}','tb','Tenant B');
  insert into tenant_users (tenant_id, user_id, role) values
    ('${TA}','${U.ownerA}','owner'),('${TA}','${U.mgrA}','manager'),('${TA}','${U.staffA}','staff'),('${TB}','${U.ownerB}','owner');
  insert into members (tenant_id, first_name, user_id) values ('${TA}','Socio','${U.member}');`);
// dos veces: idempotente
await db.exec(await file(MIG));
await db.exec(await file(MIG));

let checks = 0;
const ok = (cond, msg) => { assert.ok(cond, msg); checks++; };
async function as(user, sql, args = [], expected) {
  await db.exec('begin; set local role authenticated');
  await db.query("select set_config('request.jwt.claims', $1, true)", [JSON.stringify({ sub: user })]);
  try {
    const r = await db.query(sql, args);
    if (expected) assert.fail(`expected SQLSTATE ${expected}`);
    await db.exec('commit');
    return r.rows;
  } catch (e) {
    await db.exec('rollback');
    if (!expected || e.code !== expected) throw e;
    checks++;
    return null;
  }
}
// Escrituras del backend (service role; ignora RLS pero no los triggers ni los checks)
async function sys(sql, args = [], expected) {
  try {
    const r = await db.query(sql, args);
    if (expected) assert.fail(`expected SQLSTATE ${expected}`);
    return r.rows;
  } catch (e) {
    if (!expected || e.code !== expected) throw e;
    checks++;
    return null;
  }
}

// 1. Configuración inicial: módulo apagado y aprobación obligatoria, sin tocar tenants.modules
const st = await sys('select * from marketing_settings order by tenant_id');
ok(st.length === 2 && st.every(s => s.marketing_enabled === false && s.approval_required === true), 'settings por defecto');
ok((await sys(`select modules from tenants where id = '${TA}'`))[0].modules.marketing === undefined, 'no activa el módulo');
await sys(`update marketing_settings set marketing_enabled = true, monthly_post_limit = 8, plan_code = 'starter' where tenant_id = '${TA}'`);
// Límites: ningún negativo; null = sin límite y 0 = nada permitido siguen siendo válidos
for (const col of ['monthly_post_limit', 'monthly_image_limit', 'monthly_reel_limit', 'connected_channel_limit', 'competitor_limit']) {
  await sys(`update marketing_settings set ${col} = -1 where tenant_id = '${TA}'`, [], '23514');
  await sys(`insert into marketing_settings (tenant_id, ${col}) values ('${TB}', -5) on conflict (tenant_id) do update set ${col} = -5`, [], '23514');
  await sys(`update marketing_settings set ${col} = null where tenant_id = '${TB}'`);
  ok((await sys(`select ${col} v from marketing_settings where tenant_id = '${TB}'`))[0].v === null, `${col} null = sin límite`);
  await sys(`update marketing_settings set ${col} = 0 where tenant_id = '${TB}'`);
  await sys(`update marketing_settings set ${col} = null where tenant_id = '${TB}'`);
}
ok((await sys(`select monthly_post_limit v from marketing_settings where tenant_id = '${TA}'`))[0].v === 8, 'el valor válido no cambió');

// 2. Datos del tenant A creados por el backend
const camp = (await sys(`insert into marketing_campaigns (tenant_id, name, created_by) values ('${TA}','Octubre','${U.ownerA}') returning id`))[0].id;
const campB = (await sys(`insert into marketing_campaigns (tenant_id, name, created_by) values ('${TB}','Ajena','${U.ownerB}') returning id`))[0].id;
const ins = (tenant, campaign = null, expected) => sys(`insert into marketing_content (tenant_id, title, format, channels, campaign_id, created_by)
  values ($1, 'Post', 'reel', array['instagram'], $2, $3) returning id, status`, [tenant, campaign, U.ownerA], expected);
const c1 = (await ins(TA, camp))[0];
ok(c1.status === 'draft', 'empieza en draft');
await ins(TA, campB, '23503');                                                                         // campaña de otro tenant
await sys(`insert into marketing_content (tenant_id, title, format, status, created_by) values ('${TA}','x','reel','approved','${U.ownerA}')`, [], '23514');
await sys(`insert into marketing_content (tenant_id, title, format, created_by) values ('${TA}','x','podcast','${U.ownerA}')`, [], '23514');
await sys(`insert into marketing_content (tenant_id, title, format, channels, created_by) values ('${TA}','x','reel',array['myspace'],'${U.ownerA}')`, [], '23514');

// 3. Transiciones: no se programa sin aprobar, no se salta la revisión, no se cambia de tenant
const setStatus = (id, s, extra = '') => sys(`update marketing_content set status = '${s}'${extra} where id = '${id}' returning status`);
await sys(`update marketing_content set status = 'scheduled', scheduled_at = now() + interval '1 day' where id = '${c1.id}'`, [], '23514');
await sys(`update marketing_content set status = 'approved' where id = '${c1.id}'`, [], '23514');      // approval_required
await sys(`update marketing_content set status = 'published' where id = '${c1.id}'`, [], '23514');
await sys(`update marketing_content set tenant_id = '${TB}' where id = '${c1.id}'`, [], '23514');
await setStatus(c1.id, 'review');
await sys(`update marketing_content set status = 'scheduled', scheduled_at = now() + interval '1 day' where id = '${c1.id}'`, [], '23514');
await setStatus(c1.id, 'approved');
await sys(`update marketing_content set status = 'scheduled' where id = '${c1.id}'`, [], '23514');      // sin fecha
ok((await setStatus(c1.id, 'scheduled', `, scheduled_at = now() + interval '1 day'`))[0].status === 'scheduled', 'aprobado → programado');
await sys(`update marketing_content set status = 'draft' where id = '${c1.id}'`, [], '23514');
// con aprobación desactivada, draft → approved es posible
const c2 = (await ins(TA))[0];
await sys(`update marketing_settings set approval_required = false where tenant_id = '${TA}'`);
ok((await setStatus(c2.id, 'approved'))[0].status === 'approved', 'sin aprobación obligatoria');
await sys(`update marketing_settings set approval_required = true where tenant_id = '${TA}'`);

// 4. Historial inmutable
await sys(`insert into marketing_approval_events (tenant_id, content_id, action, from_status, to_status, actor_id, actor_role, comment)
           values ('${TA}','${c1.id}','approve','review','approved','${U.ownerA}','owner','OK')`);
const evBefore = await sys('select id, comment, to_status from marketing_approval_events order by id');
await sys(`update marketing_approval_events set comment = 'cambiado'`, [], '42501');          // ni el service role
await sys(`update marketing_approval_events set to_status = 'published', actor_id = '${U.ownerB}'`, [], '42501');
await sys(`delete from marketing_approval_events`, [], '42501');
await sys(`delete from marketing_approval_events where content_id = '${c1.id}'`, [], '42501');
await as(U.ownerA, `update marketing_approval_events set comment = 'x'`, [], '42501');             // ni el owner
await as(U.ownerA, `delete from marketing_approval_events`, [], '42501');
assert.deepEqual(await sys('select id, comment, to_status from marketing_approval_events order by id'), evBefore); checks++;
ok(evBefore.length === 1 && evBefore[0].comment === 'OK', 'historial intacto');
await sys(`insert into marketing_approval_events (tenant_id, content_id, action, to_status, actor_id)
           values ('${TB}','${c1.id}','approve','approved','${U.ownerB}')`, [], '23503');               // contenido de otro tenant

// 5. Publicaciones: idempotency_key única por tenant
await sys(`insert into marketing_publications (tenant_id, content_id, channel, idempotency_key) values ('${TA}','${c1.id}','instagram','${c1.id}:instagram:k')`);
await sys(`insert into marketing_publications (tenant_id, content_id, channel, idempotency_key) values ('${TA}','${c1.id}','instagram','${c1.id}:instagram:k')`, [], '23505');
await sys(`insert into marketing_publications (tenant_id, content_id, channel, idempotency_key) values ('${TA}','${c1.id}','facebook','${c1.id}:instagram:k')`, [], '23505');
// un reintento con upsert por la misma clave actualiza, no duplica
await sys(`insert into marketing_publications (tenant_id, content_id, channel, idempotency_key, attempts) values ('${TA}','${c1.id}','instagram','${c1.id}:instagram:k', 1)
           on conflict (tenant_id, idempotency_key) do update set attempts = marketing_publications.attempts + 1`);
const pubs = await sys(`select attempts from marketing_publications where idempotency_key = '${c1.id}:instagram:k'`);
ok(pubs.length === 1 && pubs[0].attempts === 1, 'una sola fila por idempotency_key');
const uq = await sys(`select pg_get_constraintdef(c.oid) def from pg_constraint c
  where c.conrelid = 'public.marketing_publications'::regclass and c.contype = 'u'`);
ok(uq.some(r => r.def === 'UNIQUE (tenant_id, idempotency_key)'), 'restricción única en la base');
await sys(`insert into marketing_publications (tenant_id, content_id, channel, idempotency_key) values ('${TA}','${c1.id}','instagram','corta')`, [], '23514');

// 6. Brand Kit y archivos: sin binarios ni URLs inseguras
const brand = (logo) => sys(`insert into marketing_brand_profiles (tenant_id, logo_url) values ('${TA}', $1)
  on conflict (tenant_id) do update set logo_url = excluded.logo_url`, [logo]);
await brand('/media/logo.png');
for (const bad of ['//evil.example/x.png', '/\\evil', 'https://evil.example/x.png', 'data:image/png;base64,AAAA']) {
  await sys(`update marketing_brand_profiles set logo_url = $1 where tenant_id = '${TA}'`, [bad], '23514');
}
await sys(`update marketing_brand_profiles set color_primary = 'red' where tenant_id = '${TA}'`, [], '23514');
await sys(`insert into marketing_assets (tenant_id, kind, storage_path) values ('${TA}','image','data:image/png;base64,AAAA')`, [], '23514');
await sys(`insert into marketing_assets (tenant_id, kind, storage_path) values ('${TA}','image','../x.png')`, [], '23514');
await sys(`insert into marketing_assets (tenant_id, content_id, kind, storage_path) values ('${TA}','${c1.id}','image','${TA}/c1/a.png')`);

// 7. RLS: owner/manager leen su tenant; staff, socio y otro tenant no ven nada
const tables = ['marketing_settings', 'marketing_brand_profiles', 'marketing_campaigns', 'marketing_content',
  'marketing_assets', 'marketing_approval_events', 'marketing_publications'];
for (const t of tables) {
  const own = await as(U.ownerA, `select tenant_id from ${t}`);
  ok(own.length > 0 && own.every(r => r.tenant_id === TA), `owner A lee solo A en ${t}`);
  ok((await as(U.mgrA, `select 1 from ${t}`)).length === own.length, `manager A en ${t}`);
  ok((await as(U.staffA, `select 1 from ${t}`)).length === 0, `staff sin acceso a ${t}`);
  ok((await as(U.member, `select 1 from ${t}`)).length === 0, `socio sin acceso a ${t}`);
  ok((await as(U.ownerB, `select tenant_id from ${t}`)).every(r => r.tenant_id === TB), `owner B no ve A en ${t}`);
}
// 8. Nadie escribe directamente (solo el backend): ni el owner, ni otro tenant, ni anon
await as(U.ownerA, `insert into marketing_content (tenant_id, title, format, created_by) values ('${TA}','x','reel','${U.ownerA}')`, [], '42501');
await as(U.ownerA, `update marketing_content set status = 'scheduled' where id = '${c2.id}'`, [], '42501');
await as(U.ownerA, `update marketing_settings set monthly_post_limit = 999`, [], '42501');
await as(U.ownerA, `delete from marketing_content`, [], '42501');
await as(U.ownerB, `update marketing_brand_profiles set tone = 'x'`, [], '42501');
await db.exec('begin; set local role anon');
await db.query('select 1 from marketing_content').then(() => assert.fail('anon'), e => { ok(e.code === '42501', 'anon bloqueado'); });
await db.exec('rollback');

console.log(`ALL MARKETING SQL TESTS PASSED (${checks} checks)`);
