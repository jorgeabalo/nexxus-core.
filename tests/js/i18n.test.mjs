// Regla de idioma del Manager: aita.lang es preferencia GLOBAL del usuario en este
// navegador; sin preferencia, idioma de la empresa activa y, si no define, el del navegador.
// Ejecutar: node --test tests/js/i18n.test.mjs
import { test } from 'node:test';
import assert from 'node:assert/strict';

const store = new Map();
globalThis.localStorage = { getItem: (k) => (store.has(k) ? store.get(k) : null), setItem: (k, v) => store.set(k, String(v)) };
globalThis.window = new EventTarget();
Object.defineProperty(globalThis, 'navigator', { value: { language: 'en-US' }, configurable: true });
const { getLang, setLang, useTenantDefault, tr } = await import('../../manager/assets/js/i18n.js');

test('sin preferencia: manda el idioma de la empresa activa', () => {
  useTenantDefault('es');
  assert.equal(getLang(), 'es');
  useTenantDefault('en');
  assert.equal(getLang(), 'en');
});

test('sin preferencia: una empresa sin idioma vuelve al del navegador (no arrastra el de la anterior)', () => {
  useTenantDefault('es');
  assert.equal(getLang(), 'es');
  useTenantDefault(null);
  assert.equal(getLang(), 'en');
  useTenantDefault('fr');                         // valor no soportado = sin idioma
  assert.equal(getLang(), 'en');
});

test('preferencia elegida por el usuario: es global y se mantiene al cambiar de empresa', () => {
  let events = 0;
  window.addEventListener('aita:lang', () => { events++; });
  setLang('es');
  assert.equal(store.get('aita.lang'), 'es');
  assert.equal(events, 1);
  for (const tenantLang of ['en', null, 'es', 'en']) {
    useTenantDefault(tenantLang);
    assert.equal(getLang(), 'es', `empresa con idioma ${tenantLang}`);
  }
  setLang('xx');                                   // idiomas no soportados se ignoran
  assert.equal(getLang(), 'es');
});

test('"Volver al sitio web" / "Back to website" traducido en ES y EN', () => {
  setLang('es');
  assert.equal(tr('websiteTitle'), 'Volver al sitio web');
  setLang('en');
  assert.equal(tr('websiteTitle'), 'Back to website');
  setLang('es');
});
