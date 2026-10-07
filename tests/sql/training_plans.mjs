// Pruebas del plan de entrenamiento (RLS + RPC). Base PGlite en memoria, datos solo de prueba.
// Ejecutar con NODE_PATH apuntando a una instalación de desarrollo de @electric-sql/pglite:
//   NODE_PATH=/ruta/node_modules node tests/sql/training_plans.mjs
import { createRequire } from 'node:module';
import { readFile } from 'node:fs/promises';
import assert from 'node:assert/strict';
const require = createRequire(import.meta.url);
const { PGlite } = require('@electric-sql/pglite');

const db = new PGlite();
const file = (p) => readFile(new URL(p, import.meta.url), 'utf8');
await db.exec(await file('./bootstrap_local.sql'));
await db.exec(await file('../../supabase/migrations/20261004120000_training_plans.sql'));

const U = { ownerA: '00000000-0000-0000-0000-00000000000a', staffA: '00000000-0000-0000-0000-00000000000f',
            ana: '00000000-0000-0000-0000-00000000000b', luis: '00000000-0000-0000-0000-00000000000c',
            ownerB: '00000000-0000-0000-0000-00000000000d' };
const TA = '10000000-0000-0000-0000-00000000000a', TB = '10000000-0000-0000-0000-00000000000b';
const ANA = '20000000-0000-0000-0000-00000000000b', LUIS = '20000000-0000-0000-0000-00000000000c', ZOE = '20000000-0000-0000-0000-00000000000e';
await db.exec(`
  insert into auth.users (id) values ('${U.ownerA}'),('${U.staffA}'),('${U.ana}'),('${U.luis}'),('${U.ownerB}'),('00000000-0000-0000-0000-00000000000e');
  insert into tenants (id, slug, name) values ('${TA}','ta','Tenant A'),('${TB}','tb','Tenant B');
  insert into tenant_users (tenant_id, user_id, role) values ('${TA}','${U.ownerA}','owner'),('${TA}','${U.staffA}','staff'),('${TB}','${U.ownerB}','owner');
  insert into members (id, tenant_id, first_name, user_id) values
    ('${ANA}','${TA}','Ana','${U.ana}'),('${LUIS}','${TA}','Luis','${U.luis}'),('${ZOE}','${TB}','Zoe','00000000-0000-0000-0000-00000000000e');`);

let checks = 0;
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
const save = 'select public.manager_save_training_plan($1, $2, $3, $4, $5::jsonb) as id';
const plan = (items) => JSON.stringify(items);
const mon = [
  { day_of_week: 1, exercise_key: 'leg_press', sets: 3, reps: 10, weight_lb: 90 },
  { day_of_week: 1, exercise_key: 'chest_press', sets: 3, reps: 12 },
  { day_of_week: 3, exercise_key: 'treadmill', duration_min: 15, notes: 'Ritmo suave' },
  { day_of_week: 1, exercise_key: 'other', name: '  Plancha  ', sets: 2, reps: 30 },
];

// 1. El owner crea el plan; el staff raso también puede editarlo (es su trabajo)
const id1 = (await as(U.ownerA, save, [TA, ANA, 'Fuerza', 'Calentar 5 min', plan(mon)]))[0].id;
const id2 = (await as(U.staffA, save, [TA, ANA, 'Fuerza v2', null, plan(mon.slice(0, 2))]))[0].id;
assert.equal(id2, id1, 'reutiliza el plan activo'); checks++;
assert.equal((await as(U.ownerA, 'select count(*)::int n from training_plan_items where plan_id = $1', [id1]))[0].n, 2, 'reemplaza los ejercicios'); checks++;

// 2. El socio ve SOLO su plan, ordenado por día y posición
await as(U.ownerA, save, [TA, ANA, 'Fuerza', null, plan(mon)]);
const p = (await as(U.ana, 'select public.member_training_plan() as p'))[0].p;
assert.equal(p.title, 'Fuerza'); checks++;
assert.deepEqual(p.items.map(i => [i.day_of_week, i.exercise_key]), [[1, 'leg_press'], [1, 'chest_press'], [1, 'other'], [3, 'treadmill']]); checks++;
assert.equal(p.items[2].name, 'Plancha', 'nombre recortado'); checks++;
assert.equal((await as(U.luis, 'select public.member_training_plan() as p'))[0].p, null, 'Luis no tiene plan'); checks++;

// 3. Los socios no pueden leer ni escribir las tablas directamente
assert.equal((await as(U.ana, 'select * from training_plans')).length, 0); checks++;
assert.equal((await as(U.ana, 'select * from training_plan_items')).length, 0); checks++;
await as(U.ana, save, [TA, ANA, 'Hack', null, '[]'], '42501');
await as(U.ana, `insert into training_plans (tenant_id, member_id) values ('${TA}', '${ANA}')`, [], '42501');

// 4. Aislamiento entre gimnasios
await as(U.ownerB, save, [TA, ANA, 'Intruso', null, '[]'], '42501');
await as(U.ownerA, save, [TA, ZOE, 'Cruzado', null, '[]'], '22023');
assert.equal((await as(U.ownerB, 'select * from training_plan_items')).length, 0); checks++;

// 5. Validación de datos
await as(U.ownerA, save, [TA, LUIS, 'x', null, plan([{ day_of_week: 8, exercise_key: 'squat', sets: 3, reps: 10 }])], '23514');
await as(U.ownerA, save, [TA, LUIS, 'x', null, plan([{ day_of_week: 2, exercise_key: 'squat' }])], '23514');
await as(U.ownerA, save, [TA, LUIS, 'x', null, plan([{ day_of_week: 2, exercise_key: 'DROP TABLE', sets: 1, reps: 1 }])], '23514');
await as(U.ownerA, save, [TA, LUIS, 'x', null, '{}'], '22023');
assert.equal((await as(U.ownerA, 'select count(*)::int n from training_plans where member_id = $1', [LUIS]))[0].n, 0, 'un error no deja planes a medias'); checks++;

// 6. Registro de lo hecho (gráficas): una fila por máquina y día, solo hoy y 7 días atrás
const today = (await db.query("select (now() at time zone 'America/Chicago')::date::text d")).rows[0].d;
const log = 'select public.member_log_exercise($1::date, $2, $3, $4, $5, $6, $7)';
await as(U.ana, log, [today, 'leg_press', null, 3, 10, 90, null]);
await as(U.ana, log, [today, 'leg_press', '', 3, 12, 95, null]);   // corrige, no duplica
await as(U.ana, `select public.member_log_exercise(($1::date - 3), 'treadmill', null, null, null, null, 20)`, [today]);
let h = (await as(U.ana, 'select public.member_training_plan() as p'))[0].p;
assert.equal(h.logs.length, 2); checks++;
assert.deepEqual(h.logs.map(l => [l.exercise_key, l.reps ?? l.duration_min]), [['treadmill', 20], ['leg_press', 12]], 'orden por fecha y corrección aplicada'); checks++;
await as(U.ana, `select public.member_log_exercise(($1::date + 1), 'squat', null, 3, 10, null, null)`, [today], '22023');
await as(U.ana, `select public.member_log_exercise(($1::date - 8), 'squat', null, 3, 10, null, null)`, [today], '22023');
await as(U.ana, log, [today, 'squat', null, null, null, null, null], '23514');
await as(U.ana, `insert into training_logs (tenant_id, member_id, log_date, exercise_key, sets, reps) values ('${TA}', '${ANA}', current_date, 'squat', 1, 1)`, [], '42501');
assert.equal((await as(U.luis, 'select * from training_logs')).length, 0, 'otro socio no ve el historial'); checks++;
assert.equal((await as(U.ownerA, 'select * from training_logs')).length, 2, 'el staff del gym sí'); checks++;
assert.equal((await as(U.ownerB, 'select * from training_logs')).length, 0, 'otro gym no'); checks++;
await as(U.ana, 'select public.member_unlog_exercise($1::date, $2, $3)', [today, 'leg_press', null]);
h = (await as(U.ana, 'select public.member_training_plan() as p'))[0].p;
assert.deepEqual(h.logs.map(l => l.exercise_key), ['treadmill'], 'deshacer'); checks++;

// 7. Editar el plan no borra el historial; borrar el socio borra todo
await as(U.ownerA, save, [TA, ANA, 'Nuevo', null, '[]']);
assert.equal((await as(U.ownerA, 'select count(*)::int n from training_logs')).at(0).n, 1); checks++;
await db.exec(`delete from members where id = '${ANA}'`);
assert.equal((await db.query('select count(*)::int n from training_plan_items')).rows[0].n, 0); checks++;
assert.equal((await db.query('select count(*)::int n from training_logs')).rows[0].n, 0); checks++;

console.log(`ALL TRAINING PLAN TESTS PASSED (${checks} checks)`);
