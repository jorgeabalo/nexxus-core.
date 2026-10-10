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

test('perfil de la empresa desde tenants, con fallback genérico (sin nombre de cliente)', () => {
  const p = tenantProfile(golden);
  assert.equal(p.name, 'Golden Age Fitness & Training');
  assert.equal(p.initials, 'GA');
  assert.equal(p.phone, '(346) 245-7940');
  assert.equal(p.address, '123 Main St, Houston, TX');
  assert.equal(p.language, 'en');
  assert.deepEqual(p.colors, { primary: '#0B1F3A', accent: '#C9A227' });
  assert.equal(tenantProfile({}).name, 'Nexxus');
  assert.equal(tenantProfile(null).name, 'Nexxus');
  assert.equal(tenantProfile({ name: 'X', branding: { business_name: 'Golden Age Fitness', language: 'es' } }).name, 'Golden Age Fitness');
  assert.equal(tenantProfile({ address: { line1: '1 A St', city: 'Houston', state: 'TX' } }).address, '1 A St, Houston, TX');
});

// Firmas reales de cada formato en base64
const b64 = (bin) => Buffer.from(bin, 'binary').toString('base64');
const JPEG = `data:image/jpeg;base64,${b64('\xff\xd8\xff\xe0\x00\x10JFIF\x00')}`;
const WEBP = `data:image/webp;base64,${b64('RIFF\x24\x00\x00\x00WEBPVP8 ')}`;
const GIF = `data:image/gif;base64,${b64('GIF89a\x01\x00\x01\x00')}`;
const SVG_AS_PNG = `data:image/png;base64,${b64('<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"/>')}`;
// PNG real de 1x1 en base64
const PNG = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=';
const logo = (url) => tenantProfile({ branding: { logo_url: url } }).logo;

test('logo: acepta ruta local /media/logo.png', () => {
  assert.equal(logo('/media/logo.png'), '/media/logo.png');
});

test('logo: acepta data:image/png;base64 válido (y JPEG, WebP, GIF)', () => {
  assert.equal(logo(PNG), PNG);
  for (const ok of [JPEG, WEBP, GIF]) assert.equal(safeLogoUrl(ok), ok);
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

test('logo: acepta rutas locales rasterizadas (png, jpg, jpeg, webp, gif) con o sin versión', () => {
  for (const ok of ['/media/logo.png', '/media/logo.jpg', '/media/logo.jpeg', '/media/logo.webp', '/media/logo.gif',
    '/media/LOGO.PNG', '/media/brand/logo-2026_v2.png', '/media/logo.png?v=3']) {
    assert.equal(safeLogoUrl(ok), ok, ok);
  }
});

test('logo: rechaza cualquier SVG, también local, en mayúsculas, con versión o codificado', () => {
  for (const bad of ['/media/logo.svg', '/media/LOGO.SVG', '/media/logo.svg?version=1', '/media/logo.Svg',
    '/media/logo.%73vg', '/media/logo%2Esvg', '/media/logo.svg%3Fx.png', '/media/logo.svgz',
    'data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=', 'data:image/svg+xml,<svg/>', SVG_AS_PNG]) {
    assert.equal(safeLogoUrl(bad), null, bad);
  }
});

test('logo: rechaza backslash, "..", controles, espacios, barras codificadas y rutas sin extensión', () => {
  for (const bad of ['/media\\logo.png', '/media/../logo.png', '/media/%2e%2e/logo.png', '/media/..%2Flogo.png',
    '/media/logo .png', '/media/logo.png ', ' /media/logo.png', '/media/\tlogo.png', '/media/%00logo.png', '/media/%20logo.png',
    '/%2F%2Fevil.example/logo.png', '/%2f%2fevil.example/logo.png', '/media/%5Clogo.png', '/media//logo.png', '/', '/media/logo',
    '/media/logo.png#x', '/media/logo.php', '/media/logo.png.exe']) {
    assert.equal(safeLogoUrl(bad), null, JSON.stringify(bad));
  }
});

test('logo: data image solo si el contenido coincide con el formato declarado', () => {
  assert.equal(safeLogoUrl(SVG_AS_PNG), null);
  assert.equal(safeLogoUrl(`data:image/png;base64,${JPEG.split(',')[1]}`), null);   // JPEG declarado como PNG
  assert.equal(safeLogoUrl('data:image/png;base64,AAAA'), null);
  assert.equal(safeLogoUrl('data:image/gif;base64,R0lG'), null);                      // firma incompleta
});

test('colores: solo #RRGGBB; url(), incompletos, espacios o vacíos se descartan y vuelve el valor por defecto', () => {
  const props = new Map();
  const style = { setProperty: (k, v) => props.set(k, v), removeProperty: (k) => props.delete(k) };
  for (const bad of ['url(https://evil.example/x.png)', 'url(/media/x.png)', '#0B1F3', '#0B1F3AA', '0B1F3A', ' #0B1F3A',
    '#0B1F3A ', '# 0B1F3A', '#GGGGGG', 'red', 'rgb(0,0,0)', '#0B1F3A;background:red', '', null, 42]) {
    applyBrandColors(style, tenantProfile(golden));
    applyBrandColors(style, tenantProfile({ name: 'X', branding: { color_primary: bad, color_accent: bad } }));
    assert.equal(props.has('--brand-primary'), false, String(bad));
    assert.equal(props.has('--brand-accent'), false, String(bad));
  }
  // también si alguien llama a applyBrandColors con un perfil construido a mano
  applyBrandColors(style, { colors: { primary: 'url(x)', accent: '#c9a227' } });
  assert.equal(props.has('--brand-primary'), false);
  assert.equal(props.get('--brand-accent'), '#c9a227');
  applyBrandColors(style, tenantProfile({ branding: { color_primary: '#0b1f3a', color_accent: '#C9A227' } }));
  assert.deepEqual([props.get('--brand-primary'), props.get('--brand-accent')], ['#0b1f3a', '#C9A227']);
});

test('role_modules solo restringe: nunca amplía lo que permite el rol', () => {
  const all = { modules: {} };
  const t = { ...all, role_modules: { staff: ['dashboard', 'team', 'settings', 'agents', 'inventory'], manager: ['members', 'settings'] } };
  assert.deepEqual(keys(t, 'staff'), ['dashboard']);                       // team/settings/inventory fuera de su máximo
  assert.deepEqual(keys(t, 'manager'), ['members']);                       // settings no está en el máximo de manager
  for (const k of ['team', 'settings', 'agents', 'inventory', 'marketing']) {
    assert.equal(canOpen(t, 'staff', k), false, `URL directa #/${k} para staff`);
  }
  assert.equal(canOpen(t, 'manager', 'settings'), false, 'URL directa #/settings para manager');
  assert.equal(canOpen(t, 'staff', 'dashboard'), true);
  assert.equal(keys(t, 'owner').length, 11, 'owner no se limita con role_modules');
  // sin role_modules, el máximo del rol; con lista vacía, nada
  assert.ok(!keys(all, 'staff').includes('team') && !keys(all, 'staff').includes('settings'));
  assert.deepEqual(keys({ ...all, role_modules: { staff: [] } }, 'staff'), []);
  // la intersección también respeta lo apagado en el tenant
  const off = { modules: { members: false }, role_modules: { staff: ['dashboard', 'members'] } };
  assert.deepEqual(keys(off, 'staff'), ['dashboard']);
  assert.equal(canOpen(off, 'staff', 'members'), false);
});
