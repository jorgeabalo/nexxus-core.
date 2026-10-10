// AITA Marketing en el Manager: acceso por rol, estado por empresa y calendario.
// Ejecutar: node --test tests/js/
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { CATALOG, visibleModules, canOpen, homeModule } from '../../manager/assets/js/nav.js';
import { createScope, brandSwatches, monthGrid, monthEnd, shiftMonth, usageBar, localInput, localDay }
  from '../../manager/assets/js/modules/marketing-state.js';

const tenant = (extra = {}) => ({ id: 't1', name: 'Demo Studio', modules: { marketing: true }, ...extra });
const keys = (t, r) => visibleModules(t, r).map(m => m.key);

test('Marketing es un módulo real del menú (no "Próximamente")', () => {
  const m = CATALOG.find(x => x.key === 'marketing');
  assert.equal(m.live, true);
  assert.deepEqual(m.roles, ['owner', 'manager']);
});

test('owner y manager pueden entrar; staff y socio no', () => {
  const t = tenant();
  assert.equal(canOpen(t, 'owner', 'marketing'), true);
  assert.equal(canOpen(t, 'manager', 'marketing'), true);
  assert.equal(canOpen(t, 'staff', 'marketing'), false);
  assert.equal(canOpen(t, 'member', 'marketing'), false);
  assert.ok(keys(t, 'manager').includes('marketing'));
  assert.ok(!keys(t, 'staff').includes('marketing'));
});

test('staff sigue sin acceso aunque role_modules lo incluya (hasta que se habilite a propósito)', () => {
  const t = tenant({ role_modules: { staff: ['dashboard', 'marketing'] } });
  assert.equal(canOpen(t, 'staff', 'marketing'), false);
  assert.deepEqual(keys(t, 'staff'), ['dashboard']);
});

test('tenant con marketing apagado: nadie lo ve', () => {
  const t = tenant({ modules: { marketing: false } });
  for (const r of ['owner', 'manager']) assert.equal(canOpen(t, r, 'marketing'), false);
});

test('la navegación del Manager sigue igual para los demás módulos', () => {
  const t = tenant({ modules: {} });
  assert.equal(homeModule(t, 'owner'), 'dashboard');
  assert.equal(keys(t, 'owner').length, 11);
  assert.deepEqual(keys(t, 'staff'), ['dashboard', 'members', 'attendance', 'schedule', 'payments', 'accounting', 'claudia']);
  assert.equal(canOpen(t, 'staff', 'accounting'), true);
  assert.equal(canOpen(t, 'manager', 'settings'), false);
});

test('cambiar de empresa descarta el estado de la anterior', () => {
  const scope = createScope();
  const a = scope.for('tenant-a');
  a.tab = 'brand'; a.month = '2026-10-01'; a.status = 'review'; a.cache = { color_primary: '#0B1F3A' };
  assert.equal(scope.for('tenant-a').tab, 'brand', 'misma empresa: se conserva');
  const b = scope.for('tenant-b');
  assert.deepEqual(b, { tab: 'overview', month: null });
  assert.equal(b.cache, undefined);
  assert.equal(scope.for('tenant-a').tab, 'overview', 'al volver tampoco reaparece nada');
});

test('Brand Kit: colores solo del perfil activo y solo hex válidos', () => {
  assert.deepEqual(brandSwatches({ color_primary: '#0B1F3A', color_accent: '#C9A227' }).map(s => s.value),
    ['#0B1F3A', '#C9A227', null]);
  assert.deepEqual(brandSwatches({ color_primary: 'red', color_accent: 'url(x)' }).map(s => s.value), [null, null, null]);
  assert.deepEqual(brandSwatches(null).map(s => s.value), [null, null, null]);
});

test('calendario mensual: semanas de lunes a domingo en la zona del tenant', () => {
  const items = [
    { id: '1', scheduled_at: '2026-10-21T15:00:00Z', planned_at: '2026-10-20T14:00:00Z' },
    { id: '2', planned_at: '2026-11-01T03:00:00Z' },   // 31 oct, 22:00 en Chicago
  ];
  const weeks = monthGrid('2026-10-01', items, 'America/Chicago');
  assert.equal(weeks[0][0].date, '2026-09-28');
  assert.ok(weeks.every(w => w.length === 7));
  const day = (d) => weeks.flat().find(x => x.date === d);
  assert.deepEqual(day('2026-10-21').items.map(i => i.id), ['1']);   // manda la fecha programada
  assert.deepEqual(day('2026-10-31').items.map(i => i.id), ['2']);
  assert.equal(day('2026-09-28').inMonth, false);
  assert.equal(monthEnd('2026-02-10'), '2026-02-28');
  assert.equal(shiftMonth('2026-12-01', 1), '2027-01-01');
  assert.equal(localDay('2026-11-01T03:00:00Z', 'America/Chicago'), '2026-10-31');
  assert.equal(localInput('2026-10-20T14:00:00Z', 'America/Chicago'), '2026-10-20T09:00');
});

test('uso del plan', () => {
  assert.deepEqual(usageBar(3, null), { used: 3, limit: null, pct: null, full: false });
  assert.deepEqual(usageBar(8, 8), { used: 8, limit: 8, pct: 100, full: true });
  assert.equal(usageBar(2, 8).pct, 25);
  assert.equal(usageBar(0, 0).full, true);
});
