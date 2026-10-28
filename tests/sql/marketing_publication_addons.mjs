// AITA Marketing — ampliación mensual recurrente y límite efectivo de publicaciones. PGlite, solo datos de prueba.
// Funciones atómicas (solicitar, autorizar, aprobar/rechazar, cancelar), una ampliación vigente por ciclo (un cambio
// empieza el ciclo siguiente), límite efectivo exigido al programar, registro 'authorized_uninvoiced' con línea de
// factura e historial inmutable, owner versus manager, RLS, permisos mínimos, atomicidad y repetibilidad.
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
    insert into tenants(id,slug,name,timezone) values ('${TA}','a','A','America/Chicago'),('${TB}','b','B','America/New_York');
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
const one = async (db, sql) => (await db.query(sql)).rows[0];
const submit = async (db, n, { tenant = TA, who = O, auth = true, cadence = 'monthly', pw = null, kk = key() } = {}) =>
  (await one(db, `select public.marketing_submit_publication_addon('${tenant}','${who}','${cadence}',${pw},${n},${auth},'${kk}') r`)).r;
const decide = async (db, id, approve, who = O, tenant = TA) =>
  (await one(db, `select public.marketing_decide_publication_addon('${tenant}','${id}','${who}',${approve}) r`)).r;
const cancel = async (db, id, who = O, tenant = TA) =>
  (await one(db, `select public.marketing_cancel_publication_addon('${tenant}','${id}','${who}') r`)).r;
const limit = async (db, tenant = TA, plus = 0) => (await one(db, `select private.marketing_post_limit('${tenant}',
  (private.marketing_cycle('${tenant}', now()) + interval '${plus} month')::date) l`)).l;

const db = await setup();
await db.exec(MIG);
await db.exec(MIG); // repetible
await db.exec(`insert into marketing_settings(tenant_id, marketing_enabled, monthly_post_limit) values ('${TA}', true, 8), ('${TB}', true, 8)
  on conflict (tenant_id) do update set marketing_enabled = true, monthly_post_limit = 8`);
const CUR = (await one(db, `select private.marketing_cycle('${TA}', now())::text c`)).c;
const NXT = (await one(db, `select (private.marketing_cycle('${TA}', now()) + interval '1 month')::date::text c`)).c;

// 1. precio y línea de factura generados por la base (combinación exacta más económica)
const best = [0];
for (let i = 1; i <= 500; i++) best[i] = Math.min(...[[8, 3500], [4, 2000], [1, 600]].filter(([s]) => s <= i).map(([s, p]) => p + best[i - s]));
const formula = (await db.query(`select n, (n/8)*3500 + ((n%8)/4)*2000 + (n%4)*600 c from generate_series(1,500) n`)).rows;
ok(formula.every(r => r.c === best[r.n]), 'la fórmula generada es el óptimo exacto para 1..500');

// 2. límite efectivo: 8 → +4 = 12; un cambio a 12 empieza el ciclo siguiente (20), nunca se acumula
ok(await limit(db) === 8, 'plan base: 8');
const a4 = await submit(db, 4);
ok(a4.result === 'authorized' && a4.addon.status === 'authorized_uninvoiced' && a4.addon.starts_cycle === CUR, '+4 empieza ya');
ok(a4.addon.amount_cents === 2000 && a4.addon.billing_line === 'Publicaciones adicionales de Marketing: 4 por mes — $20', 'línea de factura');
ok(await limit(db) === 12, '+4: 12');
ok((await submit(db, 4)).result === 'unchanged', 'misma cantidad: nada nuevo');
const a12 = await submit(db, 12);
ok(a12.addon.starts_cycle === NXT && await limit(db) === 12 && await limit(db, TA, 1) === 20, 'cambio: 12 este mes, 20 el siguiente');
const a8 = await submit(db, 8);
ok(await limit(db) === 12 && await limit(db, TA, 1) === 16, 'otro cambio reemplaza el anterior pendiente (16, no 28)');
const rows = (await db.query(`select status, additional_publications n, starts_cycle::text s, ends_cycle::text e
  from marketing_publication_addons where tenant_id='${TA}' order by created_at`)).rows;
ok(rows.map(r => `${r.status}:${r.n}`).join() === 'replaced:4,replaced:12,authorized_uninvoiced:8', 'una vigente; las demás reemplazadas');
ok(rows[1].s === rows[1].e, 'la pendiente reemplazada nunca empieza');
// idempotencia
const kk = key();
const x1 = await submit(db, 1, { kk, tenant: TB, who: OB }), x2 = await submit(db, 1, { kk, tenant: TB, who: OB });
ok(x1.addon.id === x2.addon.id && x2.result === 'existing', 'misma clave: misma autorización');
ok(x1.addon.billing_line === 'Publicaciones adicionales de Marketing: 1 por mes — $6' && x1.addon.amount_cents === 600, 'línea +1');
ok((await submit(db, 4, { kk, tenant: TB, who: OB })).error === 'idempotency_conflict', 'misma clave, otra cantidad: conflicto');
ok((await one(db, `select count(*)::int c from marketing_publication_addon_events where addon_id='${x1.addon.id}'`)).c === 1, 'un solo cargo');
ok(await limit(db, TB) === 9 && await limit(db) === 12, 'tenants aislados');

// 3. el límite efectivo se exige al programar (el disparador de marketing_content)
const approved = async (i, tenant = TA, when = `('${CUR}'::date + interval '20 day' + interval '${i} hour')::timestamptz`) => {
  const id = (await one(db, `insert into marketing_content(tenant_id, title, format, channels, status, scheduled_at, created_by)
    values ('${tenant}', 'p${i}', 'reel', array['instagram'], 'draft', ${when}, '${O}') returning id`)).id;
  await db.query(`update marketing_content set status='review' where id='${id}'`);
  await db.query(`update marketing_content set status='approved' where id='${id}'`);
  return id;
};
const sched = (id) => `update marketing_content set status='scheduled' where id='${id}'`;
for (let i = 0; i < 12; i++) await db.query(sched(await approved(i)));
await expect(db, sched(await approved(12)), '23514');                          // el 13.º no cabe este mes
const nxtWhen = `('${NXT}'::date + interval '10 day')::timestamptz`;
for (let i = 0; i < 16; i++) await db.query(sched(await approved(100 + i, TA, nxtWhen)));   // el mes siguiente: 16 (8 + 8)
await expect(db, sched(await approved(116, TA, nxtWhen)), '23514');
await db.query(`update marketing_content set title = 'renombrado' where title = 'p0'`);   // lo que ya contaba no se recuenta
ok(true, 'editar lo ya programado no cuenta dos veces');
// mover una ya programada a un mes lleno se vuelve a contar (aunque siga 'scheduled')
await db.query(`update marketing_content set status='approved' where title='p11'`);           // libera un hueco este mes
await db.query(`update marketing_content set status='scheduled' where title='p11'`);
await expect(db, `update marketing_content set scheduled_at = ${nxtWhen} where title = 'p11'`, '23514');

// 4. manager solicita; owner aprueba o rechaza; permiso delegado; cancelación
const req = await submit(db, 4, { tenant: TA, who: M, auth: false });
ok(req.result === 'requested' && req.addon.status === 'requested', 'el manager solicita');
ok((await submit(db, 4, { tenant: TA, who: M })).error === 'owner_authorization_required', 'el manager no autoriza');
ok((await decide(db, req.addon.id, true, M)).error === 'owner_authorization_required', 'ni aprueba');
ok((await submit(db, 1, { tenant: TA, who: S, auth: false })).error === 'forbidden', 'staff no solicita');
const dec = await decide(db, req.addon.id, false);
ok(dec.result === 'declined' && dec.addon.declined_by === O, 'el owner rechaza');
ok((await decide(db, req.addon.id, true)).error === 'addon_not_pending', 'rechazada no se aprueba');
await expect(db, `insert into marketing_purchase_permissions(tenant_id, managers_can_authorize, updated_by) values ('${TA}', true, '${M}')`, '42501');
await db.query(`insert into marketing_purchase_permissions(tenant_id, managers_can_authorize, updated_by) values ('${TA}', true, '${O}')`);
const req2 = await submit(db, 1, { tenant: TA, who: M, auth: false });
const ok2 = await decide(db, req2.addon.id, true, M);
ok(ok2.result === 'authorized' && ok2.addon.authorized_by === M, 'manager delegado aprueba');
ok((await decide(db, req2.addon.id, true, M)).result === 'existing', 'doble clic: misma decisión');
await db.query(`update marketing_purchase_permissions set managers_can_authorize=false, updated_by='${O}', updated_at=now() where tenant_id='${TA}'`);
ok(await limit(db, TA, 1) === 9, 'la aprobación también reemplaza (no suma): 8 + 1');
const cur8 = (await one(db, `select id from marketing_publication_addons where tenant_id='${TA}' and status='authorized_uninvoiced'`)).id;
ok((await cancel(db, cur8, M)).error === 'owner_authorization_required', 'el manager no cancela');
const can = await cancel(db, cur8);
ok(can.result === 'cancelled' && can.addon.ends_cycle === can.addon.starts_cycle, 'cancelar una futura: nunca empieza');
ok(await limit(db) === 12 && await limit(db, TA, 1) === 8, 'cancelada: este mes se mantiene; el siguiente vuelve a 8');

// cancelar la vigente: se mantiene el ciclo en curso y termina al inicio del siguiente
const tbActive = (await one(db, `select id from marketing_publication_addons where tenant_id='${TB}' and status='authorized_uninvoiced'`)).id;
const ctb = await cancel(db, tbActive, OB, TB);
ok(ctb.result === 'cancelled' && ctb.addon.ends_cycle === NXT && await limit(db, TB) === 9 && await limit(db, TB, 1) === 8,
  'cancelada: 9 hasta fin de mes, 8 después');

// 5. registro de facturación e historial inmutable
const ev = (await db.query(`select event, actor_id, billing_line from marketing_publication_addon_events where tenant_id='${TA}' order by id`)).rows;
ok(ev[0].event === 'authorized' && ev[0].actor_id === O && ev.some(e => e.event === 'replaced') && ev.some(e => e.event === 'declined'), 'historial completo');
await expect(db, `update marketing_publication_addon_events set event='authorized'`, '42501');
await expect(db, `delete from marketing_publication_addon_events`, '42501');

// 6. nada se altera ni se borra fuera de las funciones
const any = (await one(db, `select id from marketing_publication_addons where tenant_id='${TA}' and status='replaced' limit 1`)).id;
await expect(db, `update marketing_publication_addons set additional_publications=2 where id='${any}'`, '42501');
await expect(db, `update marketing_publication_addons set status='authorized_uninvoiced' where id='${any}'`, '42501');
await expect(db, `delete from marketing_publication_addons where id='${any}'`, '42501');
await expect(db, `delete from marketing_purchase_permissions`, '42501');
await expect(db, `insert into marketing_publication_addons(tenant_id,additional_publications,requested_by,idempotency_key,amount_cents)
  values ('${TA}',1,'${O}','${key()}',1)`, '428C9');
await expect(db, `insert into marketing_publication_addons(tenant_id,cadence,per_week,additional_publications,requested_by,idempotency_key)
  values ('${TA}','weekly',2,9,'${O}','${key()}')`, '23514');                    // semanal = 4N siempre
await expect(db, `insert into marketing_publication_addons(tenant_id,additional_publications,status,starts_cycle,requested_by,authorized_by,authorized_at,idempotency_key)
  values ('${TA}',4,'authorized_uninvoiced','${CUR}','${O}','${O}',now(),'${key()}')`, '23P01');   // solapa con la vigente
await expect(db, `insert into marketing_publication_addons(tenant_id,additional_publications,status,starts_cycle,requested_by,authorized_by,authorized_at,idempotency_key)
  values ('${TB}',4,'authorized_uninvoiced','2020-01-01','${OB}','${OB}',now(),'${key()}')`, '23514');   // no retroactiva

// 7. RLS y permisos mínimos
ok((await as(db, O, 'select tenant_id from marketing_publication_addons')).every(r => r.tenant_id === TA), 'owner: su tenant');
ok((await as(db, M, 'select tenant_id from marketing_publication_addons')).length > 0, 'manager lee su tenant');
ok((await as(db, S, 'select tenant_id from marketing_publication_addons')).length === 0, 'staff no ve nada');
ok((await as(db, OB, 'select tenant_id from marketing_publication_addons')).every(r => r.tenant_id === TB), 'otro tenant aislado');
for (const [sql, what] of [['select * from marketing_publication_addon_events', 'historial'],
  [`select public.marketing_submit_publication_addon('${TA}','${O}','monthly',null,1,true,'${key()}')`, 'función'],
  ['update marketing_publication_addons set status = status', 'update'], ['delete from marketing_publication_addons', 'delete']]) {
  await db.exec('begin; set local role authenticated');
  await db.query("select set_config('request.jwt.claims',$1,true)", [JSON.stringify({ sub: O })]);
  try { await db.query(sql); assert.fail(`owner ${what} directo`); } catch (e) { assert.equal(e.code, '42501', `${what}: ${e.message}`); checks++; }
  finally { await db.exec('rollback'); }
}
const priv = async (role, t, p) => (await one(db, `select has_table_privilege('${role}','public.${t}','${p}') ok`)).ok;
for (const t of ['marketing_publication_addons', 'marketing_publication_addon_events']) {
  for (const p of ['INSERT', 'UPDATE', 'DELETE', 'TRUNCATE']) ok(!(await priv('service_role', t, p)), `service_role sin ${p} en ${t}`);
}
ok(!(await priv('authenticated', 'marketing_publication_addon_events', 'SELECT')), 'el tenant no ve la facturación interna');
ok(!(await priv('anon', 'marketing_publication_addons', 'SELECT')), 'anon sin lectura');
for (const f of ['marketing_submit_publication_addon(uuid,uuid,text,integer,integer,boolean,text)',
  'marketing_decide_publication_addon(uuid,uuid,uuid,boolean)', 'marketing_cancel_publication_addon(uuid,uuid,uuid)']) {
  const d = await one(db, `select has_function_privilege('authenticated','public.${f}','EXECUTE') a,
    has_function_privilege('service_role','public.${f}','EXECUTE') s, prosecdef sd, proconfig @> array['search_path=""'] sp
    from pg_proc where oid = 'public.${f}'::regprocedure`);
  ok(!d.a && d.s && d.sd && d.sp, `${f}: solo service_role, definer, search_path vacío`);
}

// 8. atomicidad
const db2 = await setup();
try { await db2.exec(`${MIG}\nselect 1/0;`); assert.fail('debía fallar'); } catch { checks++; }
ok((await one(db2, `select count(*)::int n from pg_class where relname like 'marketing_p%_addon%' or relname = 'marketing_purchase_permissions'`)).n === 0,
  'migración atómica');
console.log(`ALL MARKETING PUBLICATION ADDON SQL TESTS PASSED (${checks} checks)`);
