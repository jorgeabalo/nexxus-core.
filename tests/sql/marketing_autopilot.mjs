// AITA Marketing — modo de control (manual / piloto automático) e idempotencia de «Planear contenido».
// PGlite, solo datos de prueba. Owner autoriza; manager solo con permiso delegado propio (no el de compras);
// cualquier owner/manager pausa; historial inmutable; aprobación automática atribuida al sistema bajo la
// autorización del owner, con versión de reglas; un hueco por día; RLS, ACL, atomicidad y repetibilidad.
import { createRequire } from 'node:module';
import { readFile } from 'node:fs/promises';
import assert from 'node:assert/strict';
const require = createRequire(import.meta.url);
const { PGlite } = require('@electric-sql/pglite');
const file = (p) => readFile(new URL(p, import.meta.url), 'utf8');
const BASE = await file('../../supabase/migrations/20261009120000_aita_marketing.sql');
const ADDONS = await file('../../supabase/migrations/20261013120000_marketing_publication_addons.sql');
const MIG = await file('../../supabase/migrations/20261014120000_marketing_autopilot.sql');
const TA = '10000000-0000-0000-0000-00000000000a', TB = '10000000-0000-0000-0000-00000000000b';
const O = '00000000-0000-0000-0000-00000000000a', M = '00000000-0000-0000-0000-00000000000b';
const S = '00000000-0000-0000-0000-00000000000c', OB = '00000000-0000-0000-0000-00000000000e';
let checks = 0, k = 0;
const ok = (v, msg) => { assert.ok(v, msg); checks++; };

async function setup() {
  const db = new PGlite();
  await db.exec(await file('./bootstrap_local.sql'));
  await db.exec(`insert into auth.users(id) values ('${O}'),('${M}'),('${S}'),('${OB}');
    insert into tenants(id,slug,name,timezone) values ('${TA}','a','A','America/Chicago'),('${TB}','b','B','America/New_York');
    insert into tenant_users(tenant_id,user_id,role) values ('${TA}','${O}','owner'),('${TA}','${M}','manager'),
      ('${TA}','${S}','staff'),('${TB}','${OB}','owner');`);
  await db.exec(BASE);
  await db.exec(ADDONS);
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
const one = async (db, sql) => (await db.query(sql)).rows[0];
const RULES = `'{"objective":"calls","platforms":["instagram"],"formats":["reel","image"],"days":[0,3],"hours":{"from":"10:00","to":"12:00"},"mode":"fully_ai"}'::jsonb`;
const set = async (db, who, action, rules = 'null', start = 'null', t = TA) =>
  (await one(db, `select public.marketing_autopilot_set('${t}','${who}','${action}',${rules},${start}) r`)).r;
const START = `(current_date + 1)`;
const key = () => `plan-sql-key-${String(++k).padStart(8, '0')}`;
const HASH = (c) => c.repeat(64);
const plan = async (db, kk, { who = O, t = TA, hash = HASH('a'), title = 'Post' } = {}) =>
  (await one(db, `select public.marketing_plan_content('${t}','${who}','owner','${kk}','${hash}',
     '{"title":"${title}","format":"reel","channels":["instagram"],"language":"es"}'::jsonb) r`)).r;

const db = await setup();
await db.exec(MIG);
await db.exec(MIG); // repetible
await db.exec(`insert into marketing_settings(tenant_id, marketing_enabled, monthly_post_limit) values ('${TA}', true, 2), ('${TB}', true, 8)
  on conflict (tenant_id) do update set marketing_enabled = true, monthly_post_limit = excluded.monthly_post_limit`);

// 1. manual por defecto; activar exige owner o manager con su propio permiso delegado (no el de compras)
ok((await one(db, `select count(*)::int n from marketing_autopilot_settings`)).n === 0, 'sin fila: modo manual');
await db.exec(`insert into marketing_purchase_permissions(tenant_id, managers_can_authorize, updated_by) values ('${TA}', true, '${O}')`);
ok((await set(db, M, 'activate', RULES, START)).error === 'autopilot_authorization_required', 'el permiso de compras no sirve');
ok((await set(db, S, 'activate', RULES, START)).error === 'forbidden', 'staff bloqueado');
ok((await set(db, M, 'grant')).error === 'owner_only', 'el manager no se delega a sí mismo');
const a1 = await set(db, O, 'activate', RULES, START);
ok(a1.result === 'activated' && a1.settings.mode === 'autopilot' && a1.settings.rules_version === 1
  && a1.settings.authorized_by === O, 'owner activa: versión 1');
ok((await set(db, O, 'activate', RULES, START)).result === 'unchanged', 'mismas reglas: nada nuevo');
// 2. pausa: cualquier manager; vuelve a manual
const p1 = await set(db, M, 'pause');
ok(p1.result === 'paused' && p1.settings.mode === 'manual' && p1.settings.paused_by === M, 'el manager pausa al instante');
ok((await set(db, M, 'pause')).result === 'unchanged', 'pausar dos veces no duplica');
ok((await set(db, O, 'grant')).result === 'delegation_granted', 'owner delega');
const r2 = await set(db, M, 'activate', RULES, START);
ok(r2.result === 'reactivated' && r2.settings.rules_version === 1 && r2.settings.authorized_by === M, 'manager delegado reactiva');
const RULES2 = RULES.replace('"days":[0,3]', '"days":[1,4]');
const c2 = await set(db, M, 'activate', RULES2, START);
ok(c2.result === 'rules_changed' && c2.settings.rules_version === 2, 'cambio de reglas: versión 2');
const evs = (await db.query(`select event, actor_role, rules_version from marketing_autopilot_events where tenant_id='${TA}' order by id`)).rows;
ok(evs.map(e => e.event).join() === 'activated,paused,delegation_granted,reactivated,rules_changed', 'historial completo');
await expect(db, `update marketing_autopilot_events set event='paused'`, '42501');
await expect(db, `delete from marketing_autopilot_events`, '42501');

// 3. un hueco por día y aprobación automática atribuida al sistema
// Huecos dentro del mes siguiente: la prueba no depende de la fecha en que se ejecute.
const NEXT = `(date_trunc('month', now() at time zone 'America/Chicago') + interval '1 month')::date`;
const day = (n) => `(${NEXT} + ${n})`;
const at = (n) => `((${NEXT} + ${n})::timestamp + interval '10 hour') at time zone 'America/Chicago'`;
const newContent = async (title) => (await one(db, `insert into marketing_content(tenant_id,title,format,channels,created_by)
  values ('${TA}','${title}','reel',array['instagram'],'${O}') returning id`)).id;
const toReview = async (id) => {
  await db.exec(`update marketing_content set status='generating' where id='${id}'; update marketing_content set status='review' where id='${id}'`);
};
const claim = async (id, n, v = 2) => (await one(db, `select public.marketing_autopilot_claim('${TA}','${id}',${at(n)},${day(n)},${v}) r`)).r;
const approve = async (id, v = 2, t = TA) => (await one(db, `select public.marketing_autopilot_approve('${t}','${id}',${v}) r`)).r;
const c1 = await newContent('a1'), c1b = await newContent('a1b');
ok((await claim(c1, 2)).result === 'claimed', 'hueco reservado');
ok((await claim(c1, 2)).result === 'existing', 'reservar dos veces: idempotente');
ok((await claim(c1b, 2)).error === 'slot_taken', 'máximo una por día');
await expect(db, `insert into marketing_autopilot_items(content_id, tenant_id, slot_at, slot_day, rules_version)
  values ('${c1b}','${TA}',${at(2)},${day(2)},2)`, '23505');                     // la base lo exige aunque se salte la función
ok((await claim(c1b, 3, 1)).error === 'autopilot_paused', 'versión antigua no reserva');
ok((await approve(c1)).error === 'invalid_transition', 'solo desde Listo para revisar');
await toReview(c1);
ok((await approve(c1, 1)).error === 'rules_changed', 'reglas cambiadas: falla');
ok((await approve(c1, 2, TB)).error === 'tenant_mismatch' || (await approve(c1, 2, TB)).error === 'autopilot_paused', 'otro tenant: falla');
await db.exec(`update marketing_content set format='video' where id='${c1}'`);
ok((await approve(c1)).error === 'format_not_authorized', 'formato no autorizado');
await db.exec(`update marketing_content set format='reel', channels=array['tiktok'] where id='${c1}'`);
ok((await approve(c1)).error === 'platform_not_authorized', 'plataforma no autorizada');
await db.exec(`update marketing_content set channels=array['instagram'] where id='${c1}'`);
const ap = await approve(c1);
ok(ap.result === 'approved' && ap.item.approved_rules_version === 2 && ap.item.authorized_by === M, 'aprobado con versión y autorización');
const ev = await one(db, `select actor_role, actor_id, comment from marketing_approval_events where content_id='${c1}' and action='approve'`);
ok(ev.actor_role === 'system' && ev.comment === 'Aprobado automáticamente bajo la autorización del owner.', 'atribuido al sistema');
ok((await one(db, `select approved_by from marketing_content where id='${c1}'`)).approved_by === null, 'sin aprobación personal falsa');
// cuota: límite 2; uno ya aprobado + uno programado → el tercero falla
const c3 = await newContent('a3'), c4 = await newContent('a4');
await claim(c3, 3); await toReview(c3);
await db.exec(`update marketing_content set status='approved' where id='${c4}' and false`);
const manual = await newContent('m1');
await db.exec(`update marketing_content set status='review' where id='${manual}'; update marketing_content set status='approved' where id='${manual}';
  update marketing_content set status='scheduled', scheduled_at=${at(4)} where id='${manual}'`);
ok((await approve(c3)).error === 'quota_exhausted', 'cuota agotada: se detiene');
// autorización retirada: el manager que autorizó pierde el permiso delegado
await db.exec(`update marketing_content set status='archived' where id='${manual}' and false`);
ok((await set(db, O, 'revoke')).result === 'delegation_revoked', 'owner retira el permiso');
await db.exec(`update marketing_settings set monthly_post_limit = 8 where tenant_id='${TA}'`);
ok((await approve(c3)).error === 'authorization_withdrawn', 'autorización retirada: falla');
await set(db, O, 'grant');
// 4. pausa: lo futuro aprobado/programado vuelve a revisión; borradores y publicados intactos
await db.exec(`update marketing_content set status='scheduled', scheduled_at=${at(2)} where id='${c1}'`);
await db.exec(`insert into marketing_publications(tenant_id, content_id, channel, provider, status, scheduled_at, idempotency_key)
  values ('${TA}','${c1}','instagram','disabled','pending',${at(2)},'k-${c1}')`);
const draft = await newContent('d1'); await claim(draft, 5);
const p2 = await set(db, O, 'pause');
ok(p2.held === 1, 'una publicación futura vuelve a revisión');
ok((await one(db, `select status, scheduled_at from marketing_content where id='${c1}'`)).status === 'review', 'programada → Listo para revisar');
ok((await one(db, `select status from marketing_publications where content_id='${c1}'`)).status === 'cancelled', 'cola cancelada');
ok((await one(db, `select status from marketing_content where id='${draft}'`)).status === 'draft', 'borrador conservado');
ok((await approve(c3)).error === 'autopilot_paused', 'en pausa: no aprueba');

// 5. idempotencia de «Planear contenido»
const kk = key();
const x1 = await plan(db, kk), x2 = await plan(db, kk);
ok(x1.result === 'created' && x2.result === 'existing' && x1.content_id === x2.content_id, 'misma clave: mismo contenido');
ok((await plan(db, kk, { hash: HASH('b') })).error === 'idempotency_conflict', 'misma clave, otro cuerpo: conflicto');
ok((await plan(db, kk, { who: M })).result === 'created', 'la clave es por usuario');
ok((await plan(db, key(), { who: S })).error === 'forbidden', 'staff no crea');
ok((await one(db, `select count(*)::int n from marketing_approval_events where content_id='${x1.content_id}' and action='create'`)).n === 1, 'un alta');
await expect(db, `delete from marketing_plan_requests`, '42501');

// 6. RLS y permisos mínimos
await set(db, OB, 'activate', RULES, START, TB);
ok((await as(db, O, 'select tenant_id from marketing_autopilot_settings')).every(r => r.tenant_id === TA), 'owner: su tenant');
ok((await as(db, M, 'select tenant_id from marketing_autopilot_events')).length > 0, 'manager lee el historial de su tenant');
ok((await as(db, S, 'select tenant_id from marketing_autopilot_settings')).length === 0, 'staff no ve nada');
ok((await as(db, OB, 'select tenant_id from marketing_autopilot_settings')).every(r => r.tenant_id === TB), 'otro tenant aislado');
for (const [sql, what] of [['select * from marketing_plan_requests', 'claves'],
  [`select public.marketing_autopilot_set('${TA}','${O}','pause',null,null)`, 'función'],
  ["update marketing_autopilot_settings set mode = 'autopilot'", 'update'], ['delete from marketing_autopilot_items', 'delete']]) {
  await db.exec('begin; set local role authenticated');
  await db.query("select set_config('request.jwt.claims',$1,true)", [JSON.stringify({ sub: O })]);
  try { await db.query(sql); assert.fail(`owner ${what} directo`); } catch (e) { assert.equal(e.code, '42501', `${what}: ${e.message}`); checks++; }
  finally { await db.exec('rollback'); }
}
const priv = async (role, t, p) => (await one(db, `select has_table_privilege('${role}','public.${t}','${p}') ok`)).ok;
for (const t of ['marketing_autopilot_settings', 'marketing_autopilot_events', 'marketing_autopilot_items', 'marketing_plan_requests']) {
  for (const p of ['INSERT', 'UPDATE', 'DELETE', 'TRUNCATE']) ok(!(await priv('service_role', t, p)), `service_role sin ${p} en ${t}`);
  ok(!(await priv('anon', t, 'SELECT')), `anon sin lectura de ${t}`);
}
for (const f of ['marketing_autopilot_set(uuid,uuid,text,jsonb,date)', 'marketing_autopilot_claim(uuid,uuid,timestamptz,date,integer)',
  'marketing_autopilot_approve(uuid,uuid,integer)', 'marketing_plan_content(uuid,uuid,text,text,text,jsonb)']) {
  const d = await one(db, `select has_function_privilege('authenticated','public.${f}','EXECUTE') a,
    has_function_privilege('service_role','public.${f}','EXECUTE') s, prosecdef sd, proconfig @> array['search_path=""'] sp
    from pg_proc where oid = 'public.${f}'::regprocedure`);
  ok(!d.a && d.s && d.sd && d.sp, `${f}: solo service_role, definer, search_path vacío`);
}

// 7. atomicidad
const db2 = await setup();
try { await db2.exec(`${MIG}\nselect 1/0;`); assert.fail('debía fallar'); } catch { checks++; }
ok((await one(db2, `select count(*)::int n from pg_class where relname like 'marketing_autopilot%' or relname = 'marketing_plan_requests'`)).n === 0,
  'migración atómica');
console.log(`ALL MARKETING AUTOPILOT SQL TESTS PASSED (${checks} checks)`);
