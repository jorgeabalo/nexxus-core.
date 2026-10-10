// AITA Marketing Fase 2 — migración 20261011120000_marketing_reel_studio.sql (PGlite en memoria, datos de prueba).
// RLS, mínimo privilegio, inmutabilidad del original, reglas de los trabajos, aislamiento entre tenants,
// límites cerrados por defecto y bucket. Ejecutar: NODE_PATH=/ruta/node_modules node tests/sql/marketing_studio.mjs
import { createRequire } from 'node:module';
import { readFile } from 'node:fs/promises';
import assert from 'node:assert/strict';
const require = createRequire(import.meta.url);
const { PGlite } = require('@electric-sql/pglite');

const file = (p) => readFile(new URL(p, import.meta.url), 'utf8');
const MIG1 = await file('../../supabase/migrations/20261009120000_aita_marketing.sql');
const MIG2 = await file('../../supabase/migrations/20261011120000_marketing_reel_studio.sql');
const TABLES = ['marketing_media', 'marketing_media_derivatives', 'marketing_media_events', 'marketing_generation_jobs',
  'marketing_generation_job_events', 'marketing_generation_inputs', 'marketing_generation_outputs', 'marketing_model_usage'];
const U = { ownerA: '00000000-0000-0000-0000-00000000000a', mgrA: '00000000-0000-0000-0000-00000000000b',
  staffA: '00000000-0000-0000-0000-00000000000c', member: '00000000-0000-0000-0000-00000000000d',
  ownerB: '00000000-0000-0000-0000-00000000000e' };
const TA = '10000000-0000-0000-0000-00000000000a', TB = '10000000-0000-0000-0000-00000000000b';
let checks = 0;
const ok = (cond, msg) => { assert.ok(cond, msg); checks++; };

async function fresh(withStorage = false) {
  const db = new PGlite();
  await db.exec(await file('./bootstrap_local.sql'));
  if (withStorage) await db.exec(`create schema storage; create table storage.buckets (id text primary key, name text,
    public boolean, file_size_limit bigint, allowed_mime_types text[]);`);
  await db.exec(`
    alter default privileges in schema public grant all on tables to anon, authenticated, service_role, public;
    insert into auth.users (id) values ('${U.ownerA}'),('${U.mgrA}'),('${U.staffA}'),('${U.member}'),('${U.ownerB}');
    insert into tenants (id, slug, name) values ('${TA}','ta','Tenant A'),('${TB}','tb','Tenant B');
    insert into tenant_users (tenant_id, user_id, role) values ('${TA}','${U.ownerA}','owner'),('${TA}','${U.mgrA}','manager'),
      ('${TA}','${U.staffA}','staff'),('${TB}','${U.ownerB}','owner');
    insert into members (tenant_id, first_name, user_id) values ('${TA}','Socio','${U.member}');`);
  return db;
}
const db = await fresh(true);
await db.exec(MIG1);
// límites de Fase 1 de un tenant piloto (no deben cambiar)
await db.exec(`update marketing_settings set marketing_enabled = true, plan_code = 'pilot', monthly_post_limit = 8,
  monthly_image_limit = 8, monthly_reel_limit = 4, connected_channel_limit = 0, competitor_limit = 0 where tenant_id = '${TA}'`);
await db.exec(MIG2);
await db.exec(MIG2);                                   // idempotente

async function sys(sql, args = [], expected) {
  try {
    const r = await db.query(sql, args);
    if (expected) assert.fail(`expected SQLSTATE ${expected}: ${sql}`);
    return r.rows;
  } catch (e) {
    if (!expected || e.code !== expected) throw e;
    checks++;
    return null;
  }
}
async function as(role, user, sql, expected) {
  await db.exec(`begin; set local role ${role}`);
  if (user) await db.query("select set_config('request.jwt.claims', $1, true)", [JSON.stringify({ sub: user })]);
  try {
    const r = await db.query(sql);
    if (expected) assert.fail(`${role}: expected ${expected}: ${sql}`);
    await db.exec('commit');
    return r.rows;
  } catch (e) {
    await db.exec('rollback');
    if (!expected || e.code !== expected) throw e;
    checks++;
    return null;
  }
}

// ---------------------------------------------------------------- 1. límites: cerrados por defecto, los de Fase 1 intactos
const s = (await sys(`select * from marketing_settings where tenant_id = '${TA}'`))[0];
ok(s.monthly_post_limit === 8 && s.monthly_reel_limit === 4 && s.plan_code === 'pilot', 'límites de Fase 1 intactos');
ok(s.ai_generation_enabled === false, 'IA apagada por defecto');
for (const k of ['monthly_generation_job_limit', 'monthly_regeneration_limit',
  'monthly_generated_image_limit', 'monthly_generated_video_seconds_limit']) ok(Number(s[k]) === 0, `${k} = 0 por defecto`);
ok(Number(s.library_storage_limit_bytes) === 1073741824, 'Biblioteca: 1 GiB por defecto (no consume generación)');
ok(Number(s.monthly_ai_cost_limit) === 0, 'coste IA = 0 por defecto');
await sys(`update marketing_settings set monthly_generation_job_limit = -1 where tenant_id = '${TA}'`, [], '23514');
await sys(`update marketing_settings set max_upload_bytes = 999999999999 where tenant_id = '${TA}'`, [], '23514');
const bucket = (await sys(`select * from storage.buckets where id = 'marketing-assets'`))[0];
ok(bucket.public === false && bucket.allowed_mime_types.includes('video/webm') && !bucket.allowed_mime_types.includes('image/svg+xml'),
  'bucket privado, con WebM y sin SVG');

// ---------------------------------------------------------------- 2. originales
const MA = '20000000-0000-0000-0000-00000000000a', MB = '20000000-0000-0000-0000-00000000000b';
const CK = 'a'.repeat(64);
const media = (id, t, path) => sys(`insert into marketing_media (id, tenant_id, storage_path, media_type, mime_type,
  byte_size, checksum, uploaded_by) values ('${id}','${t}','${path}','image','image/png',100,'${CK}','${U.ownerA}')`);
await media(MA, TA, `${TA}/originals/${MA}/foto.png`);
await media(MB, TB, `${TB}/originals/${MB}/foto.png`);
// rutas: otro tenant, otro id, traversal, absoluta, URL
for (const p of [`${TB}/originals/${MA}/x.png`, `${TA}/originals/${MB}/x.png`, `${TA}/originals/${MA}/../x.png`,
  `/${TA}/originals/${MA}/x.png`, `https://evil/x.png`, `${TA}/derivatives/${MA}/x.png`]) {
  await sys(`insert into marketing_media (id, tenant_id, storage_path, media_type, mime_type, byte_size, checksum, uploaded_by)
    values (gen_random_uuid(),'${TA}','${p}','image','image/png',1,'${'b'.repeat(64)}','${U.ownerA}')`, [], '23514');
}
await sys(`insert into marketing_media (id, tenant_id, storage_path, media_type, mime_type, byte_size, checksum, uploaded_by)
  values ('20000000-0000-0000-0000-0000000000cc','${TA}','${TA}/originals/20000000-0000-0000-0000-0000000000cc/a.svg','image','image/svg+xml',1,'${'c'.repeat(64)}','${U.ownerA}')`, [], '23514');
await sys(`insert into marketing_media (id, tenant_id, storage_path, media_type, mime_type, byte_size, checksum, uploaded_by, processing_status)
  values ('20000000-0000-0000-0000-0000000000dd','${TA}','${TA}/originals/20000000-0000-0000-0000-0000000000dd/a.png','image','image/png',1,'${'d'.repeat(64)}','${U.ownerA}','ready')`, [], '23514');
const m0 = (await sys(`select * from marketing_media where id = '${MA}'`))[0];
ok(m0.people_policy === 'exclude' && m0.consent_status === 'unknown' && m0.contains_people === null, 'personas: exclude por defecto');
ok(m0.contains_minors === null && m0.malware_scan_status === 'not_scanned' && m0.validation_status === 'pending', 'menores y escaneo: desconocidos');
// inmutable y nunca borrado
for (const set of [`storage_path = '${TA}/originals/${MA}/otra.png'`, `checksum = '${'e'.repeat(64)}'`, `byte_size = 5`,
  `mime_type = 'image/gif'`, `uploaded_by = '${U.mgrA}'`, `tenant_id = '${TB}'`]) {
  await sys(`update marketing_media set ${set} where id = '${MA}'`, [], '42501');
}
await sys(`delete from marketing_media where id = '${MA}'`, [], '42501');
await sys(`update marketing_media set processing_status = 'processing' where id = '${MA}'`, [], '23514');   // uploaded → processing no
await sys(`update marketing_media set processing_status = 'ready' where id = '${MA}'`, [], '23514');      // sin validar
await sys(`update marketing_media set validation_status = 'passed', processing_status = 'ready' where id = '${MA}'`);
await sys(`update marketing_media set people_policy = 'no_people' where id = '${MA}'`, [], '23514');       // contains_people null
await sys(`update marketing_media set people_policy = 'consented', contains_people = true where id = '${MA}'`, [], '23514');
await sys(`update marketing_media set people_policy = 'no_people', contains_people = false, consent_status = 'not_required' where id = '${MA}'`);
await sys(`insert into marketing_media (id, tenant_id, storage_path, media_type, mime_type, byte_size, checksum, uploaded_by)
  values (gen_random_uuid(),'${TA}','${TA}/originals/20000000-0000-0000-0000-0000000000ee/a.png','image','image/png',1,'${CK}','${U.ownerA}')`, [], '23514');

// ---------------------------------------------------------------- 3. derivados
const DA = '30000000-0000-0000-0000-00000000000a';
await sys(`insert into marketing_media_derivatives (id, tenant_id, media_id, kind, method, storage_path, created_by, status)
  values ('${DA}','${TA}','${MA}','anonymized','pixelate_faces','${TA}/derivatives/${MA}/${DA}.png','${U.ownerA}','needs_review')`);
await sys(`update marketing_media_derivatives set status = 'ready' where id = '${DA}'`, [], '23514');     // sin revisión humana
await sys(`update marketing_media_derivatives set status = 'ready', reviewed_by = '${U.ownerA}', reviewed_at = now() where id = '${DA}'`);
await sys(`update marketing_media_derivatives set storage_path = '${TA}/derivatives/${MA}/${MB}.png' where id = '${DA}'`, [], '42501');
await sys(`delete from marketing_media_derivatives where id = '${DA}'`, [], '42501');
await sys(`insert into marketing_media_derivatives (tenant_id, media_id, kind, created_by) values ('${TA}','${MB}','thumbnail','${U.ownerA}')`, [], '23503');
await sys(`insert into marketing_media_derivatives (tenant_id, media_id, kind, storage_path, created_by)
  values ('${TA}','${MA}','thumbnail','${TB}/derivatives/${MA}/${DA}.png','${U.ownerA}')`, [], '23514');

// ---------------------------------------------------------------- 4. trabajos
const J = (await sys(`insert into marketing_generation_jobs (tenant_id, created_by, task_type, real_media_percent, ai_media_percent,
  maximum_cost, idempotency_key) values ('${TA}','${U.ownerA}','reel',50,50,5,'job-key-000000000001') returning id`))[0].id;
for (const [r, a] of [[60, 30], [101, -1], [-5, 105], [100, 100]]) {
  await sys(`insert into marketing_generation_jobs (tenant_id, created_by, task_type, real_media_percent, ai_media_percent,
    idempotency_key) values ('${TA}','${U.ownerA}','reel',${r},${a},'job-bad-${r}-${a}-00000000')`, [], '23514');
}
await sys(`insert into marketing_generation_jobs (tenant_id, created_by, task_type, real_media_percent, ai_media_percent,
  idempotency_key, status) values ('${TA}','${U.ownerA}','reel',50,50,'job-key-000000000002','queued')`, [], '23514');
await sys(`insert into marketing_generation_jobs (tenant_id, created_by, task_type, real_media_percent, ai_media_percent,
  idempotency_key) values ('${TA}','${U.ownerA}','reel',50,50,'job-key-000000000001')`, [], '23505');     // idempotency_key única
await sys(`update marketing_generation_jobs set status = 'queued' where id = '${J}'`, [], '23514');         // draft → queued no
await sys(`update marketing_generation_jobs set status = 'awaiting_generation_approval', estimated_cost = 0 where id = '${J}'`);
await sys(`update marketing_generation_jobs set status = 'queued' where id = '${J}'`, [], '23514');         // sin aprobación
await sys(`update marketing_generation_jobs set status = 'queued', approved_at = now(), approved_by = '${U.mgrA}' where id = '${J}'`);
await sys(`update marketing_generation_jobs set real_media_percent = 75, ai_media_percent = 25 where id = '${J}'`, [], '23514');
await sys(`update marketing_generation_jobs set maximum_cost = 50 where id = '${J}'`, [], '23514');
await sys(`update marketing_generation_jobs set status = 'processing', selected_provider = 'mock', selected_model = 'mock-media-v1',
  provider_job_id = 'mock-1', actual_cost = 0.5 where id = '${J}'`);
await sys(`update marketing_generation_jobs set actual_cost = 0.1 where id = '${J}'`, [], '23514');          // el coste no baja
await sys(`update marketing_generation_jobs set selected_provider = null where id = '${J}'`, [], '23514');
await sys(`update marketing_generation_jobs set status = 'cancelled', error_code = 'cancelled_by_user', completed_at = now() where id = '${J}'`);
const jc = (await sys(`select * from marketing_generation_jobs where id = '${J}'`))[0];
ok(jc.selected_provider === 'mock' && Number(jc.actual_cost) === 0.5, 'cancelar conserva coste y proveedor');
for (const st of ['draft', 'queued', 'processing', 'succeeded']) {
  await sys(`update marketing_generation_jobs set status = '${st}' where id = '${J}'`, [], '23514');           // final
}
await sys(`update marketing_generation_jobs set error_code = 'Traceback secret' where id = '${J}'`, [], '23514');
await sys(`delete from marketing_generation_jobs where id = '${J}'`, [], '42501');
await sys(`insert into marketing_generation_jobs (tenant_id, created_by, task_type, real_media_percent, ai_media_percent,
  idempotency_key) values ('${TA}','${U.ownerA}','publish_now',50,50,'job-key-000000000003')`, [], '23514');

// ---------------------------------------------------------------- 5. entradas, uso, historial: sin cruces de tenant, solo inserción
await sys(`update marketing_media set validation_status = 'passed', processing_status = 'ready', contains_people = false,
  people_policy = 'no_people' where id = '${MB}'`);                  // MB es válido, pero de OTRO tenant
await sys(`insert into marketing_generation_inputs (tenant_id, job_id, media_id, privacy_class) values ('${TA}','${J}','${MB}','synthetic_only')`, [], '23514');
await sys(`insert into marketing_generation_inputs (tenant_id, job_id, media_id, privacy_class) values ('${TA}','${J}','${MA}','public')`, [], '23514');
await sys(`insert into marketing_generation_inputs (tenant_id, job_id, media_id, privacy_class) values ('${TA}','${J}','${MA}','business_media_no_people')`);
await sys(`insert into marketing_model_usage (tenant_id, job_id, provider, model_id, task_type, catalog_version, billing_unit, units,
  estimated_cost, actual_cost, idempotency_key, status) values ('${TA}','${J}','mock','mock-media-v1','text_to_video','v1','second',10,0,0,'usage-key-0000000001','not_charged')`);
await sys(`insert into marketing_model_usage (tenant_id, job_id, provider, model_id, task_type, catalog_version, billing_unit, units,
  estimated_cost, idempotency_key, status) values ('${TA}','${J}','mock','m','t','v1','second',10,0,'usage-key-0000000001','charged')`, [], '23505');
await sys(`insert into marketing_generation_job_events (tenant_id, job_id, action, to_status) values ('${TA}','${J}','cancel','cancelled')`);
for (const t of ['marketing_generation_inputs', 'marketing_model_usage', 'marketing_generation_job_events']) {
  await sys(`update ${t} set created_at = now() where tenant_id = '${TA}'`, [], '42501');
  await sys(`delete from ${t} where tenant_id = '${TA}'`, [], '42501');
}
await sys(`insert into marketing_generation_job_events (tenant_id, job_id, action, to_status) values ('${TB}','${J}','cancel','cancelled')`, [], '23503');
await sys(`insert into marketing_generation_outputs (tenant_id, job_id, kind, origin, storage_path) values ('${TA}','${J}','scene','ai_generated','${TB}/derivatives/x.png')`, [], '23514');
await sys(`insert into marketing_generation_outputs (tenant_id, job_id, kind, origin) values ('${TA}','${J}','scene','stock_photo')`, [], '23514');
await sys(`insert into marketing_generation_outputs (tenant_id, job_id, kind, origin, scene_index) values ('${TA}','${J}','scene','client_original',0)`);

// ---------------------------------------------------------------- 6a. endurecimiento: menores, consentimiento, antivirus, borrado
const mk = async (id, ck, extra = '') => {
  await sys(`insert into marketing_media (id, tenant_id, storage_path, media_type, mime_type, byte_size, checksum, uploaded_by)
    values ('${id}','${TA}','${TA}/originals/${id}/f.png','image','image/png',10,'${ck.repeat(64)}','${U.ownerA}')`);
  await sys(`update marketing_media set validation_status = 'passed', processing_status = 'ready' ${extra} where id = '${id}'`);
};
const MC = '20000000-0000-0000-0000-0000000000c1', MD = '20000000-0000-0000-0000-0000000000d1';
await mk(MC, '1');
await mk(MD, '2', `, contains_people = false, people_policy = 'no_people', consent_status = 'not_required'`);
// menores: desconocido o sí → siempre excluido
await sys(`update marketing_media set contains_people = true, people_policy = 'consented', consent_status = 'granted' where id = '${MC}'`, [], '23514');
await sys(`update marketing_media set contains_people = true, contains_minors = true, people_policy = 'anonymize' where id = '${MC}'`, [], '23514');
await sys(`update marketing_media set contains_people = null, contains_minors = null, people_policy = 'anonymize' where id = '${MC}'`, [], '23514');
await sys(`update marketing_media set contains_people = false, contains_minors = true, people_policy = 'no_people' where id = '${MC}'`, [], '23514');
await sys(`update marketing_media set contains_people = true, contains_minors = false, people_policy = 'consented', consent_status = 'granted' where id = '${MC}'`);
// consentimiento retirado → excluido
await sys(`update marketing_media set consent_status = 'revoked' where id = '${MC}'`, [], '23514');
await sys(`update marketing_media set people_policy = 'anonymize', consent_status = 'revoked' where id = '${MC}'`, [], '23514');
// antivirus: nunca "clean" sin escáner
await sys(`update marketing_media set malware_scan_status = 'clean' where id = '${MD}'`, [], '23514');
await sys(`update marketing_media set malware_scan_status = 'unavailable' where id = '${MD}'`);
// entradas: solo archivos permitidos, nunca un derivado simulado
const J2 = (await sys(`insert into marketing_generation_jobs (tenant_id, created_by, task_type, real_media_percent, ai_media_percent,
  maximum_cost, idempotency_key) values ('${TA}','${U.ownerA}','reel',50,50,5,'job-key-000000000010') returning id`))[0].id;
await sys(`insert into marketing_generation_inputs (tenant_id, job_id, media_id, privacy_class) values ('${TA}','${J2}','${MD}','business_media_no_people')`);
await sys(`insert into marketing_generation_inputs (tenant_id, job_id, media_id, privacy_class) values ('${TA}','${J2}','${MC}','consented_people')`);
const MU = '20000000-0000-0000-0000-0000000000e1';
await sys(`insert into marketing_media (id, tenant_id, storage_path, media_type, mime_type, byte_size, checksum, uploaded_by)
  values ('${MU}','${TA}','${TA}/originals/${MU}/f.png','image','image/png',10,'${'3'.repeat(64)}','${U.ownerA}')`);
await sys(`insert into marketing_generation_inputs (tenant_id, job_id, media_id, privacy_class) values ('${TA}','${J2}','${MU}','restricted')`, [], '23514');
await sys(`update marketing_media set validation_status = 'passed', processing_status = 'ready' where id = '${MU}'`);
await sys(`insert into marketing_generation_inputs (tenant_id, job_id, media_id, privacy_class) values ('${TA}','${J2}','${MU}','restricted')`, [], '23514'); // menores desconocido
const DM = '30000000-0000-0000-0000-0000000000b1';
await sys(`insert into marketing_media_derivatives (id, tenant_id, media_id, kind, method, created_by, is_mock, status)
  values (gen_random_uuid(),'${TA}','${MC}','anonymized','blur_faces','${U.ownerA}',true,'ready')`, [], '23514');
await sys(`insert into marketing_media_derivatives (id, tenant_id, media_id, kind, created_by, is_mock, status)
  values (gen_random_uuid(),'${TA}','${MC}','thumbnail','${U.ownerA}',true,'ready')`, [], '23514');
await sys(`insert into marketing_media_derivatives (id, tenant_id, media_id, kind, method, created_by, is_mock, status)
  values ('${DM}','${TA}','${MC}','anonymized','blur_faces','${U.ownerA}',true,'mock_only')`);
await sys(`update marketing_media_derivatives set status = 'ready', reviewed_by = '${U.ownerA}', reviewed_at = now() where id = '${DM}'`, [], '23514');
await sys(`update marketing_media_derivatives set is_mock = false where id = '${DM}'`, [], '42501');
await sys(`insert into marketing_generation_inputs (tenant_id, job_id, derivative_id, privacy_class) values ('${TA}','${J2}','${DM}','anonymized_people')`, [], '23514');
// consentimiento retirado después de crear el trabajo → no puede entrar en cola
await sys(`update marketing_generation_jobs set status = 'awaiting_generation_approval' where id = '${J2}'`);
await sys(`update marketing_media set consent_status = 'revoked', people_policy = 'exclude' where id = '${MC}'`);
await sys(`update marketing_generation_jobs set status = 'queued', approved_at = now(), approved_by = '${U.ownerA}' where id = '${J2}'`, [], '23514');
// borrado controlado: bloqueado mientras un trabajo activo lo use; con auditoría; la fila queda
await sys(`delete from marketing_media where id = '${MD}'`, [], '42501');
await sys(`update marketing_media set processing_status = 'deleted' where id = '${MD}'`, [], '23514');               // sin quién/cuándo
await sys(`update marketing_media set processing_status = 'deleted', deleted_by = '${U.ownerA}', deleted_at = now() where id = '${MD}'`, [], '23514');
await sys(`update marketing_generation_jobs set status = 'cancelled' where id = '${J2}'`);
await sys(`update marketing_media set processing_status = 'deleted', deleted_by = '${U.ownerA}', deleted_at = now(), delete_reason = 'pedido' where id = '${MD}'`);
await sys(`update marketing_media set processing_status = 'ready' where id = '${MD}'`, [], '42501');
await sys(`update marketing_media set metadata = '{"x":1}' where id = '${MD}'`, [], '42501');
await mk('20000000-0000-0000-0000-0000000000d2', '2');                     // mismo contenido, tras borrar: permitido
await sys(`insert into marketing_media_events (tenant_id, media_id, action, actor_id, actor_role, detail)
  values ('${TA}','${MD}','delete','${U.ownerA}','owner','{"reason":"pedido"}')`);
await sys(`insert into marketing_media_events (tenant_id, media_id, action) values ('${TA}','${MB}','delete')`, [], '23503');
await sys(`update marketing_media_events set action = 'upload'`, [], '42501');
await sys(`delete from marketing_media_events`, [], '42501');
// el tenant no puede elevar sus propios límites
await as('authenticated', U.ownerA, `update marketing_settings set monthly_generation_job_limit = 999, ai_generation_enabled = true
  where tenant_id = '${TA}'`, '42501');
ok(Number((await sys(`select monthly_generation_job_limit v from marketing_settings where tenant_id = '${TA}'`))[0].v) === 0,
  'límites sin cambios');

// ---------------------------------------------------------------- 6. privilegios y RLS
for (const t of TABLES) {
  const acl = (await db.query(`select coalesce(r.rolname,'PUBLIC') g, a.privilege_type p from pg_class c, aclexplode(c.relacl) a
    left join pg_roles r on r.oid = a.grantee where c.oid = 'public.${t}'::regclass`)).rows;
  const by = (g) => acl.filter(x => x.g === g).map(x => x.p).sort();
  ok(by('PUBLIC').length === 0 && by('anon').length === 0, `${t}: PUBLIC/anon nada`);
  assert.deepEqual(by('authenticated'), ['SELECT'], `${t} authenticated`); checks++;
  assert.deepEqual(by('service_role'), ['DELETE', 'INSERT', 'SELECT', 'UPDATE'], `${t} service_role`); checks++;
  await as('authenticated', U.ownerA, `insert into ${t} (tenant_id) values ('${TA}')`, '42501');
  await as('authenticated', U.ownerA, `update ${t} set tenant_id = tenant_id`, '42501');
  await as('authenticated', U.ownerA, `delete from ${t}`, '42501');
  await as('anon', null, `select 1 from ${t}`, '42501');
  const seen = async (u) => (await as('authenticated', u, `select tenant_id from ${t}`));
  const own = await seen(U.ownerA);
  ok(own.every(r => r.tenant_id === TA), `owner A solo ve A en ${t}`);
  ok((await seen(U.mgrA)).length === own.length, `manager A ve lo mismo en ${t}`);
  ok((await seen(U.staffA)).length === 0, `staff no ve ${t}`);
  ok((await seen(U.member)).length === 0, `socio no ve ${t}`);
  ok((await seen(U.ownerB)).every(r => r.tenant_id === TB), `owner B no ve A en ${t}`);
}
ok((await as('authenticated', U.ownerB, `select id from marketing_media`)).length === 1, 'owner B ve solo su archivo');

// ---------------------------------------------------------------- 7. atómica
const db2 = await fresh();
await db2.exec(MIG1);
let failed = false;
try { await db2.exec(`${MIG2}\nselect 1/0;`); } catch { failed = true; }
ok(failed, 'el script con error falla');
ok((await db2.query(`select count(*)::int n from pg_class where relname = 'marketing_media'`)).rows[0].n === 0, 'no queda nada aplicado');
ok((await db2.query(`select count(*)::int n from information_schema.columns where table_name = 'marketing_settings'
  and column_name = 'ai_generation_enabled'`)).rows[0].n === 0, 'ni columnas nuevas');

console.log(`ALL MARKETING STUDIO SQL TESTS PASSED (${checks} checks)`);
