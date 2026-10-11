// AITA Marketing (PR #21): plan de publicaciones, ampliaciones (+1, +4, +8, personalizadas; mensual o semanal),
// frases exactas de confirmación, objetivos, Biblioteca incluida y lenguaje sin promesas.
// Ejecutar: node --test tests/js/marketing-plan.test.mjs
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile, readdir } from 'node:fs/promises';
import { addonQuote, addonSpec, addonBody, fmtPrice, confirmSignature, blocksText, ADDON_PRESETS, WEEKLY_PRESETS }
  from '../../manager/assets/js/modules/marketing-addons-state.js';
import { PLAN_TEXT } from '../../manager/assets/js/modules/marketing-plan-i18n.js';
import { STUDIO_TEXT } from '../../manager/assets/js/modules/marketing-studio-i18n.js';
import { OBJECTIVES } from '../../manager/assets/js/modules/marketing-studio-state.js';

test('+1, +4, +8 y personalizadas: combinación exacta más económica', () => {
  const cases = { 1: [6, '1'], 4: [20, '4'], 8: [35, '8'], 12: [55, '8 + 4'], 22: [102, '8 + 8 + 4 + 1 + 1'],
    37: [166, '8 + 8 + 8 + 8 + 4 + 1'] };
  for (const [n, [amount, text]] of Object.entries(cases)) {
    const q = addonQuote(Number(n));
    assert.equal(q.amount, amount, n);
    assert.equal(blocksText(q.blocks), text, n);
  }
  const best = [0];
  for (let n = 1; n <= 500; n++) best[n] = Math.min(...[[8, 35], [4, 20], [1, 6]].filter(([s]) => s <= n).map(([s, p]) => p + best[n - s]));
  for (let n = 1; n <= 500; n++) assert.equal(addonQuote(n).amount, best[n], `n=${n}`);
  for (const bad of [0, -1, 501, 1.5, 'x', null]) assert.equal(addonQuote(bad), null);
  assert.deepEqual(ADDON_PRESETS, [1, 4, 8]);
});

test('frecuencia semanal: 1/sem = 4/mes = $20; 2/sem = 8/mes = $35; personalizada con su equivalente', () => {
  assert.deepEqual(WEEKLY_PRESETS, [1, 2]);
  const one = addonSpec('weekly', 1), two = addonSpec('weekly', 2), three = addonSpec('weekly', 3);
  assert.deepEqual([one.additional, one.amount, one.per_week], [4, 20, 1]);
  assert.deepEqual([two.additional, two.amount], [8, 35]);
  assert.deepEqual([three.additional, three.amount], [12, 55]);
  assert.deepEqual(addonBody(three), { cadence: 'weekly', per_week: 3 });
  assert.deepEqual(addonBody(addonSpec('monthly', 12)), { cadence: 'monthly', additional_publications: 12 });
  for (const bad of [0, 126, 1.5]) assert.equal(addonSpec('weekly', bad), null);
  assert.ok(!('amount' in addonBody(three)) && !('price' in addonBody(three)), 'el navegador nunca envía el precio');
});

test('la confirmación cambia si cambia la cantidad, la frecuencia o el precio', () => {
  const a = confirmSignature(addonSpec('monthly', 4)), b = confirmSignature(addonSpec('weekly', 1));
  assert.notEqual(a, b, 'misma cantidad mensual, distinta frecuencia');
  assert.notEqual(confirmSignature(addonSpec('monthly', 12)), confirmSignature(addonSpec('monthly', 13)));
  assert.equal(confirmSignature(null), '');
  const src = await_read('../../manager/assets/js/modules/marketing-addons.js');
  return src.then((code) => {
    assert.match(code, /if \(sig !== st\.sig\) \{ st\.sig = sig; st\.key = newIdempotencyKey\(\); \}/);
    assert.match(code, /confirmSignature\(spec\(\)\) !== confirmSignature\(q\)/, 'revalida antes de enviar');
  });
});

function await_read(rel) { return readFile(new URL(rel, import.meta.url), 'utf8'); }

test('frases exactas de confirmación (ES/EN, singular y plural)', () => {
  const es = PLAN_TEXT.es, en = PLAN_TEXT.en;
  assert.equal(es.confirm_authorize(12, fmtPrice(55)), 'Autorizo 12 publicaciones adicionales por $55 al mes.');
  assert.equal(es.confirm_authorize(1, fmtPrice(6)), 'Autorizo 1 publicación adicional por $6 al mes.');
  assert.equal(es.confirm_request(4, fmtPrice(20)), 'Solicito 4 publicaciones adicionales por $20 al mes.');
  assert.equal(en.confirm_authorize(12, fmtPrice(55)), 'I authorize 12 additional posts for $55 per month.');
  assert.equal(en.confirm_authorize(1, fmtPrice(6)), 'I authorize 1 additional post for $6 per month.');
  assert.equal(fmtPrice(102), '$102');
  for (const lang of ['es', 'en']) {
    for (const k of ['s_included', 's_added', 's_total', 's_price', 's_start', 'recurring', 'ad_title', 'owner_note']) {
      assert.ok(PLAN_TEXT[lang][k], `${lang}.${k}`);
    }
  }
  assert.match(es.recurring, /recurrente hasta que lo canceles/);
  assert.match(en.recurring, /recurring monthly charge until you cancel/);
  assert.equal(es.ad_title, '¿Necesitas más publicaciones?');
  assert.equal(es.weekly_note, 'Se distribuirán durante cuatro semanas del ciclo mensual. Una quinta semana no añade publicaciones automáticamente.');
  assert.equal(en.weekly_note, 'They will be spread over four weeks of the monthly cycle. A fifth week does not add posts automatically.');
  assert.match(es.change_note('1 de noviembre de 2026'), /^Reemplaza tu ampliación actual a partir del 1 de noviembre de 2026\./);
});

test('ES y EN tienen las mismas claves', () => {
  for (const T of [PLAN_TEXT]) assert.deepEqual(Object.keys(T.es).sort(), Object.keys(T.en).sort());
  assert.deepEqual(Object.keys(PLAN_TEXT.es.err).sort(), Object.keys(PLAN_TEXT.en.err).sort());
});

test('dos modos de creación, seis objetivos y Biblioteca incluida', () => {
  assert.equal(STUDIO_TEXT.es.mode_library_ai, 'Usar mi Biblioteca y mejorar con IA');
  assert.equal(STUDIO_TEXT.es.mode_fully_ai, 'Crear completamente con IA');
  assert.deepEqual(OBJECTIVES, ['calls', 'messages', 'trial_visit', 'memberships', 'reactivation', 'reviews']);
  const es = OBJECTIVES.map(o => STUDIO_TEXT.es[`ob_${o}`]);
  assert.deepEqual(es, ['Conseguir llamadas', 'Conseguir mensajes', 'Reservar una visita o clase de prueba',
    'Conseguir membresías', 'Reactivar clientes', 'Obtener reseñas']);
  for (const o of OBJECTIVES) assert.ok(STUDIO_TEXT.en[`ob_${o}`], o);
  assert.equal(STUDIO_TEXT.es.libDisabled, 'Biblioteca incluida — pendiente de activación');
  assert.equal(STUDIO_TEXT.en.libDisabled, 'Library included — pending activation');
  assert.equal(STUDIO_TEXT.es.libUsage('120 MB', '1 GB'), 'Biblioteca incluida — 120 MB utilizados de 1 GB');
  assert.equal(STUDIO_TEXT.en.libUsage('120 MB', '1 GB'), 'Library included — 120 MB used of 1 GB');
  for (const lang of ['es', 'en']) assert.ok(!/no disponible|not available in this plan/i.test(STUDIO_TEXT[lang].libDisabled));
  for (const lang of ['es', 'en']) {
    assert.ok(!/no habilitada|not enabled/i.test(STUDIO_TEXT[lang].libDisabled + STUDIO_TEXT[lang].err.library_disabled), lang);
    assert.match(STUDIO_TEXT[lang].libTemporary(30), /30/);
    assert.ok(!/permanente|permanent|para siempre|forever/i.test(JSON.stringify(STUDIO_TEXT[lang])), `${lang}: nada permanente`);
  }
});

test('ningún archivo del Manager promete viralidad, vistas o likes', async () => {
  const root = new URL('../../manager/', import.meta.url);
  const files = [];
  const walk = async (dir) => {
    for (const e of await readdir(dir, { withFileTypes: true })) {
      const u = new URL(e.name + (e.isDirectory() ? '/' : ''), dir);
      if (e.isDirectory()) await walk(u); else if (/\.(html|js)$/.test(e.name)) files.push(u);
    }
  };
  await walk(root);
  const promise = [/\bviral/i, /(garantiza(mos)?|aseguramos|te aseguramos)\s+(m[aá]s\s+)?(alcance|vistas|likes|seguidores|clientes|ventas)/i,
    /guarantee[sd]?\s+(more\s+)?(reach|views|likes|followers|customers|sales)/i, /millones de (vistas|views)/i, /million views/i];
  for (const f of files) {
    const src = await readFile(f, 'utf8');
    for (const re of promise) assert.ok(!re.test(src), `${f.pathname.split('/manager/')[1]}: ${re}`);
  }
  assert.equal(PLAN_TEXT.es.flow_note.split('.')[0],
    'AITA analiza los resultados para aumentar la probabilidad de alcance, interacción y conversiones');
});

test('estados comerciales para owner y manager, sin decir que se pagó o cobró', () => {
  const es = PLAN_TEXT.es, en = PLAN_TEXT.en;
  assert.deepEqual([es.st_pending, es.st_active, es.st_declined, es.st_cancel_scheduled],
    ['Pendiente de autorización', 'Autorizada', 'Rechazada', 'Cancelación programada']);
  assert.deepEqual([en.st_pending, en.st_active, en.st_declined, en.st_cancel_scheduled],
    ['Pending authorization', 'Authorized', 'Declined', 'Cancellation scheduled']);
  for (const s of ['upcoming', 'changing']) assert.equal(es[`st_${s}`], 'Autorizada');
  assert.equal(es.invoice_note, 'Autorizada: se añadirá a tu factura mensual.');
  assert.equal(en.invoice_note, 'Authorized: it will be added to your monthly invoice.');
  assert.match(es.renews, /Se renueva cada mes/);
  assert.match(es.cancel_scheduled_line('1 de noviembre de 2026'), /^Termina el 1 de noviembre de 2026; el mes en curso se mantiene\./);
  for (const lang of ['es', 'en']) {
    const all = JSON.stringify(PLAN_TEXT[lang]) + Object.values(PLAN_TEXT[lang]).filter(v => typeof v === 'function')
      .map(f => String(f(12, '$55', '1 de noviembre'))).join(' ');
    assert.ok(!/\b(pagad[oa]s?|cobrad[oa]s?|paid|was charged|has been charged|payment (received|captured))\b/i.test(all), `${lang}: nada pagado/cobrado`);
    assert.ok(!/uninvoiced|billing_line|amount_cents|margen|margin/i.test(all), `${lang}: sin términos internos`);
  }
});
