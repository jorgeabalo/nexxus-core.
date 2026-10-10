// AITA — control global de costos (20261011140000). PGlite en memoria, datos de prueba.
// Techo de 80 USD, subpresupuestos, reserva atómica e idempotente, conciliación única, ledger inmutable,
// aislamiento y permisos. Ejecutar: NODE_PATH=/ruta/node_modules node tests/sql/aita_cost_control.mjs
import { createRequire } from 'node:module';
import { readFile } from 'node:fs/promises';
import assert from 'node:assert/strict';
const require = createRequire(import.meta.url);
const { PGlite } = require('@electric-sql/pglite');

const file = (p) => readFile(new URL(p, import.meta.url), 'utf8');
const MIG = await file('../../supabase/migrations/20261011140000_aita_cost_control.sql');
const U = { ownerA: '00000000-0000-0000-0000-00000000000a' };
const TA = '10000000-0000-0000-0000-00000000000a', TB = '10000000-0000-0000-0000-00000000000b';
let checks = 0;
const ok = (c, m) => { assert.ok(c, m); checks++; };

const db = new PGlite();
await db.exec(await file('./bootstrap_local.sql'));
await db.exec(await file('../../supabase/migrations/20261009120000_aita_marketing.sql'));   // public.touch_updated_at
await db.exec(`insert into auth.users (id) values ('${U.ownerA}');
  insert into tenants (id, slug, name) values ('${TA}','ta','A'),('${TB}','tb','B');
  insert into tenant_users (tenant_id, user_id, role) values ('${TA}','${U.ownerA}','owner');`);
await db.exec(MIG);
await db.exec(MIG);                                                     // idempotente

async function q(sql, expected) {
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
const reserve = async (t, cat, cents, key) => (await q(`select public.aita_cost_reserve('${t}','${cat}','mock',null,'op',
  ${cents === null ? 'null' : cents},'${key}') r`))[0].r;
const reconcile = async (t, key, cents, rel = false) => (await q(`select public.aita_cost_reconcile('${t}','${key}',${cents},${rel}) r`))[0].r;
const summary = async (t) => (await q(`select public.aita_cost_summary('${t}') s`))[0].s;

// 1. presupuestos: techo 8000, subpresupuestos cerrados por defecto, suma ≤ total
const b = (await q(`select * from aita_cost_budgets where tenant_id = '${TA}'`))[0];
ok(b.monthly_total_cost_limit_cents === 8000 && b.monthly_marketing_ai_cost_limit_cents === 0 && b.monthly_voice_cost_limit_cents === 0,
  'techo 80 USD y subpresupuestos en 0');
await q(`update aita_cost_budgets set monthly_total_cost_limit_cents = 8001 where tenant_id = '${TA}'`, '23514');
await q(`update aita_cost_budgets set monthly_voice_cost_limit_cents = 9000 where tenant_id = '${TA}'`, '23514');
await q(`update aita_cost_budgets set monthly_marketing_ai_cost_limit_cents = -1 where tenant_id = '${TA}'`, '23514');
await q(`update aita_cost_budgets set currency = 'EUR' where tenant_id = '${TA}'`, '23514');
// propuesta del piloto (la aplicaría el operador; aquí solo en la base de prueba)
await q(`update aita_cost_budgets set monthly_voice_cost_limit_cents = 3500, monthly_marketing_ai_cost_limit_cents = 2000,
  monthly_storage_cost_limit_cents = 750, monthly_infrastructure_allocation_cents = 750, monthly_reserve_cents = 1000
  where tenant_id in ('${TA}','${TB}')`);

// 2. reservas
ok((await reserve(TA, 'marketing_ai', null, 'k-null-0001')).reason === 'cost_not_estimable', 'sin estimación no se ejecuta');
ok((await reserve(TA, 'marketing_ai', -5, 'k-neg-00001')).reason === 'cost_not_estimable', 'negativo rechazado');
let r = await reserve(TA, 'marketing_ai', 1500, 'job:aaaaaaaa-1');
ok(r.status === 'reserved', 'primera reserva');
ok((await reserve(TA, 'marketing_ai', 1500, 'job:aaaaaaaa-1')).status === 'duplicate', 'reintento: no reserva dos veces');
ok((await q(`select count(*)::int n from aita_cost_ledger where tenant_id = '${TA}'`))[0].n === 1, 'una sola fila');
r = await reserve(TA, 'marketing_ai', 1500, 'job:aaaaaaaa-2');           // "segundo trabajo": no cabe en el saldo
ok(r.status === 'rejected' && r.reason === 'category_budget_exceeded' && r.available_cents === 500, 'saldo no reservable dos veces');
ok((await reserve(TA, 'voice', 3500, 'call:0000001')).status === 'reserved', 'voz dentro de su presupuesto');
ok((await reserve(TA, 'claudia_ai', 1, 'call:0000002')).reason === 'category_budget_exceeded', 'Claudia comparte el de voz');
await q(`update aita_cost_budgets set monthly_storage_cost_limit_cents = 1500, monthly_infrastructure_allocation_cents = 0
  where tenant_id = '${TA}'`);
await q(`select public.aita_cost_reconcile('${TA}','call:0000001',null,false)`, '23514');
ok((await reconcile(TA, 'call:0000001', 4500)).status === 'committed', 'el proveedor cobró más de lo reservado');
r = await reserve(TA, 'storage', 1500, 'storage:000001');                // 1500 + 4500 + 1500 > 8000 − 1000
ok(r.status === 'rejected' && r.reason === 'total_budget_exceeded', 'el total (sin la reserva) también manda');
ok((await reserve(TA, 'processing', 1, 'proc:0000001')).reason === 'category_budget_exceeded', 'infraestructura en 0');
ok((await reserve('10000000-0000-0000-0000-0000000000ff', 'marketing_ai', 1, 'x:00000001')).reason === 'budget_not_configured',
  'tenant sin presupuesto');

// 3. conciliación única: costo real y liberación de la diferencia
ok((await reconcile(TA, 'job:aaaaaaaa-1', 300)).status === 'committed', 'costo real registrado');
ok((await reconcile(TA, 'job:aaaaaaaa-1', 900)).duplicate === true, 'no se cobra dos veces');
let s = await summary(TA);
ok(s.marketing_ai_consumed_cents === 300 && s.marketing_ai_reserved_cents === 0, 'la diferencia quedó libre');
ok((await reserve(TA, 'marketing_ai', 1500, 'job:aaaaaaaa-3')).status === 'reserved', 'el saldo liberado se puede usar');
ok((await reconcile(TA, 'job:aaaaaaaa-3', 0, true)).status === 'released', 'cancelado: se libera');
ok((await reconcile(TA, 'no-existe-001', 1)).status === 'not_found', 'clave desconocida');

// 4. ledger inmutable
await q(`update aita_cost_ledger set actual_cost_cents = 1 where idempotency_key = 'job:aaaaaaaa-1'`, '42501');
await q(`update aita_cost_ledger set reserved_cost_cents = 1 where idempotency_key = 'call:0000001'`, '42501');
await q(`update aita_cost_ledger set status = 'reserved' where idempotency_key = 'job:aaaaaaaa-3'`, '42501');
await q(`delete from aita_cost_ledger`, '42501');
await q(`insert into aita_cost_ledger (tenant_id, period, service_category, provider, operation, estimated_cost_cents,
  reserved_cost_cents, idempotency_key, status) values ('${TA}', current_date, 'marketing_ai','mock','x',1,1,'job:aaaaaaaa-1','reserved')`, '23505');

// 5. aislamiento: el resumen solo ve su tenant y no expone proveedores ni modelos
await reserve(TB, 'marketing_ai', 700, 'job:bbbbbbbb-1');
s = await summary(TA);
ok(s.marketing_ai_reserved_cents === 0 && !JSON.stringify(s).includes('mock'), 'sin datos de otros tenants ni proveedores');
ok((await summary(TB)).marketing_ai_reserved_cents === 700, 'B ve lo suyo');

// 6. serialización: la reserva bloquea la fila del presupuesto del tenant
const src = (await q(`select prosrc from pg_proc where proname = 'aita_cost_reserve'`))[0].prosrc;
ok(/for update/i.test(src), 'aita_cost_reserve usa SELECT … FOR UPDATE');

// 7. permisos: solo el backend (service_role)
for (const role of ['anon', 'authenticated']) {
  for (const t of ['aita_cost_budgets', 'aita_cost_ledger']) {
    ok(!(await q(`select has_table_privilege('${role}', 'public.${t}', 'SELECT') x`))[0].x, `${role} sin acceso a ${t}`);
  }
  for (const f of ['aita_cost_reserve(uuid,text,text,text,text,integer,text)', 'aita_cost_reconcile(uuid,text,integer,boolean)',
    'aita_cost_summary(uuid)']) {
    ok(!(await q(`select has_function_privilege('${role}', 'public.${f}', 'EXECUTE') x`))[0].x, `${role} no ejecuta ${f}`);
  }
}
ok((await q(`select has_function_privilege('service_role', 'public.aita_cost_reserve(uuid,text,text,text,text,integer,text)', 'EXECUTE') x`))[0].x,
  'service_role ejecuta la reserva');
ok(!(await q(`select has_table_privilege('service_role', 'public.aita_cost_ledger', 'DELETE') x`))[0].x, 'nadie borra el ledger');
await db.exec(`begin; set local role authenticated`);
await db.query("select set_config('request.jwt.claims', $1, true)", [JSON.stringify({ sub: U.ownerA })]);
try { await db.query(`update aita_cost_budgets set monthly_total_cost_limit_cents = 8000`); assert.fail('debía fallar'); }
catch (e) { assert.equal(e.code, '42501'); checks++; }
await db.exec('rollback');

console.log(`ALL AITA COST CONTROL SQL TESTS PASSED (${checks} checks)`);
