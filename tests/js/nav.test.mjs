// Pruebas de la navegación de Nexxus Manager (módulos por tenant y rol, perfil).
// Ejecutar: node --test tests/js/
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { CATALOG, visibleModules, canOpen, homeModule, tenantProfile, roleAllows, safeLogoUrl, applyBrandColors } from '../../manager/assets/js/nav.js';

// Configuración real de Golden Age en producción (módulos apagados = false)
const golden = {
  id: 't1', name: 'Golden Age Fitness & Training', timezone: 'America/Chicago',
  branding: { locale: 'en-US', currency: 'USD', color_accent: '#C9A227', color_primary: '#0B1F3A', display_name: 'GOLDEN AGE' },
  modules: { agents: false, claudia: true, members: true, payments: true, schedule: true, settings: false,
    dashboard: true, inventory: false, marketing: false, accounting: true },
  public_phone: '(346) 245-7940', address: '123 Main St, Houston, TX',
};
const keys = (t, r) => visibleModules(t, r).map(m => m.key);

test('menú en el orden pedido (11 módulos, agents fuera del menú)', () => {
  const menu = CATALOG.filter(m => m.menu !== false).map(m => m.key);
  assert.deepEqual(menu, ['dashboard', 'members', 'attendance', 'schedule', 'payments', 'accounting', 'team',
    'inventory', 'marketing', 'claudia', 'settings']);
});

test('solo módulos habilitados para el tenant', () => {
  const k = keys(golden, 'owner');
  assert.deepEqual(k, ['dashboard', 'members', 'attendance', 'schedule', 'payments', 'accounting', 'team', 'claudia']);
  for (const off of ['inventory', 'marketing', 'settings', 'agents']) assert.equal(canOpen(golden, 'owner', off), false);
});

test('owner ve todo lo habilitado; manager sin configuración; staff limitado', () => {
  const all = { ...golden, modules: {} };
  assert.equal(keys(all, 'owner').length, 11);
  assert.ok(!keys(all, 'manager').includes('settings'));
  assert.ok(keys(all, 'manager').includes('team'));
  assert.deepEqual(keys(all, 'staff'), ['dashboard', 'members', 'attendance', 'schedule', 'payments', 'accounting', 'claudia']);
  assert.equal(canOpen(all, 'staff', 'team'), false);
  assert.equal(canOpen(all, 'staff', 'settings'), false);
});

test('roles desconocidos (p. ej. socio) no ven nada', () => {
  assert.deepEqual(keys(golden, 'member'), []);
  assert.deepEqual(keys(golden, undefined), []);
  assert.equal(homeModule(golden, 'member'), null);
});

test('el tenant puede ajustar los módulos por rol (tenants.settings.role_modules)', () => {
  const t = { ...golden, role_modules: { staff: ['dashboard', 'schedule'] } };
  assert.deepEqual(keys(t, 'staff'), ['dashboard', 'schedule']);
  assert.equal(roleAllows('owner', 'team', t), true);          // owner nunca se limita
  const off = { ...t, role_modules: { staff: ['inventory'] } };
  assert.deepEqual(keys(off, 'staff'), []);                    // permitido por rol pero apagado en el tenant
});

test('rutas desconocidas no se abren; la portada es el primer módulo visible', () => {
  assert.equal(canOpen(golden, 'owner', 'admin'), false);
  assert.equal(homeModule(golden, 'staff'), 'dashboard');
  assert.equal(homeModule({ modules: { dashboard: false } }, 'staff'), 'members');
});

test('perfil de la empresa desde tenants, con fallback del piloto', () => {
  const p = tenantProfile(golden);
  assert.equal(p.name, 'Golden Age Fitness & Training');
  assert.equal(p.initials, 'GA');
  assert.equal(p.phone, '(346) 245-7940');
  assert.equal(p.address, '123 Main St, Houston, TX');
  assert.equal(p.language, 'en');
  assert.deepEqual(p.colors, { primary: '#0B1F3A', accent: '#C9A227' });
  assert.equal(tenantProfile({}).name, 'Golden Age Fitness');
  assert.equal(tenantProfile({ name: 'X', branding: { business_name: 'Golden Age Fitness', language: 'es' } }).name, 'Golden Age Fitness');
  assert.equal(tenantProfile({ address: { line1: '1 A St', city: 'Houston', state: 'TX' } }).address, '1 A St, Houston, TX');
});

// PNG real de 1x1 en base64
const PNG = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=';
const logo = (url) => tenantProfile({ branding: { logo_url: url } }).logo;

test('logo: acepta ruta local /media/logo.png', () => {
  assert.equal(logo('/media/logo.png'), '/media/logo.png');
});

test('logo: acepta data:image/png;base64 válido (y JPEG, WebP, GIF)', () => {
  assert.equal(logo(PNG), PNG);
  for (const t of ['jpeg', 'webp', 'gif']) assert.ok(safeLogoUrl(`data:image/${t};base64,AAAA`));
});

test('logo: rechaza URL protocol-relative //evil.example/logo.png', () => {
  assert.equal(logo('//evil.example/logo.png'), null);
  assert.equal(logo('/\\evil.example/logo.png'), null);     // el navegador también lo trata como otro dominio
});

test('logo: rechaza data:image/svg+xml', () => {
  assert.equal(logo('data:image/svg+xml;base64,PHN2Zz48L3N2Zz4='), null);
  assert.equal(logo('data:image/svg+xml,<svg onload=alert(1)>'), null);
});

test('logo: rechaza URLs externas, otros esquemas y data sin base64 limpio', () => {
  for (const bad of ['https://evil.example/x.png', 'http://x/y.png', 'javascript:alert(1)', 'media/logo.png',
    'data:image/png,iVBOR', 'data:image/png;base64,AAAA"><script>', 'data:text/html;base64,AAAA', '', null, 42]) {
    assert.equal(safeLogoUrl(bad), null, String(bad));
  }
});

test('colores: al pasar de un tenant con colores a otro sin colores no quedan los anteriores', () => {
  const props = new Map();
  const style = { setProperty: (k, v) => props.set(k, v), removeProperty: (k) => props.delete(k) };
  applyBrandColors(style, tenantProfile(golden));
  assert.equal(props.get('--brand-primary'), '#0B1F3A');
  assert.equal(props.get('--brand-accent'), '#C9A227');
  applyBrandColors(style, tenantProfile({ name: 'Sin colores', branding: {} }));
  assert.equal(props.has('--brand-primary'), false);
  assert.equal(props.has('--brand-accent'), false);
  applyBrandColors(style, tenantProfile({ branding: { color_accent: '#7FB069' } }));   // solo uno propio
  assert.equal(props.get('--brand-accent'), '#7FB069');
  assert.equal(props.has('--brand-primary'), false);
});
