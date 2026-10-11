// AITA Marketing Fase 2 (frontend): mezcla real/IA, privacidad, etapas del Reel y textos ES/EN.
// Ejecutar: node --test tests/js/marketing-studio.test.mjs
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { MIX_PRESETS, normalizeMix, approxScenes, privacyClass, usableAsSource, reelStage, fmtBytes, fmtCost,
  newIdempotencyKey, WIZARD_STEPS, ACCEPT } from '../../manager/assets/js/modules/marketing-studio-state.js';
import { STUDIO_TEXT } from '../../manager/assets/js/modules/marketing-studio-i18n.js';
import { TABS } from '../../manager/assets/js/modules/marketing-state.js';

test('presets y mezcla personalizada siempre suman 100', () => {
  assert.deepEqual(MIX_PRESETS, [[100, 0], [75, 25], [50, 50], [25, 75], [0, 100]]);
  for (const v of [0, 3, 37, 50, 98, 100, -20, 140, 'x']) {
    const m = normalizeMix(v);
    assert.equal(m.real + m.ai, 100);
    assert.equal(m.real % 5, 0);
    assert.ok(m.real >= 0 && m.real <= 100);
  }
  assert.deepEqual(normalizeMix(37), { real: 35, ai: 65 });
  assert.deepEqual(normalizeMix(38, 10), { real: 40, ai: 60 });
});

test('aproximación a escenas y segundos antes de generar', () => {
  const a = approxScenes(50, 6, 30);
  assert.equal(a.realScenes + a.aiScenes, 6);
  assert.equal(a.realSeconds + a.aiSeconds, 30);
  assert.equal(a.realScenes, 3);
  assert.equal(approxScenes(25, 4, 30).realScenes, 1);
  assert.equal(approxScenes(0, 4, 30).realScenes, 0);
  assert.equal(approxScenes(100, 4, 30).aiScenes, 0);
  assert.equal(approxScenes(50, 4, 20, true).realOrigin, 'client_ai_adapted');
});

test('privacidad: desconocido, menores, revocado, simulado o sin validar nunca es utilizable', () => {
  const base = { processing_status: 'ready', validation_status: 'passed' };
  assert.equal(privacyClass({ ...base, contains_people: null, people_policy: 'exclude' }), 'restricted');
  assert.equal(usableAsSource({ ...base, contains_people: null, people_policy: 'exclude' }), false);
  assert.equal(usableAsSource({ ...base, contains_people: false, people_policy: 'no_people' }), true);
  assert.equal(usableAsSource({ ...base, contains_people: false, people_policy: 'no_people', validation_status: 'pending' }), false);
  const ok = { ...base, contains_people: true, contains_minors: false, people_policy: 'consented', consent_status: 'granted' };
  assert.equal(usableAsSource(ok), true);
  assert.equal(usableAsSource({ ...ok, consent_status: 'pending' }), false);
  assert.equal(usableAsSource({ ...ok, contains_minors: null }), false);            // no se sabe → excluido
  assert.equal(usableAsSource({ ...ok, contains_minors: true }), false);
  assert.equal(usableAsSource({ ...ok, consent_status: 'revoked', people_policy: 'exclude' }), false);
  const anon = { ...base, contains_people: true, contains_minors: false, people_policy: 'anonymize' };
  assert.equal(usableAsSource(anon), false);
  assert.equal(usableAsSource({ ...anon, derivatives: [{ kind: 'anonymized', status: 'mock_only', is_mock: true }] }), false);
  assert.equal(usableAsSource({ ...anon, derivatives: [{ kind: 'anonymized', status: 'ready', is_mock: true }] }), false);
  assert.equal(usableAsSource({ ...anon, derivatives: [{ kind: 'anonymized', status: 'awaiting_processing' }] }), false);
  assert.equal(usableAsSource({ ...anon, derivatives: [{ kind: 'anonymized', status: 'ready', is_mock: false }] }), true);
  assert.equal(usableAsSource({ contains_people: false, people_policy: 'no_people', processing_status: 'archived' }), false);
});

test('textos: "Generación no habilitada en este plan" y estados simulados', () => {
  assert.equal(STUDIO_TEXT.es.err.generation_disabled, 'Generación no habilitada en este plan');
  assert.equal(STUDIO_TEXT.es.genNotEnabled, 'Generación no habilitada en este plan');
  assert.match(STUDIO_TEXT.es.ds_mock_only, /NO anonimizado/);
  assert.match(STUDIO_TEXT.en.ds_mock_only, /NOT anonymized/);
  assert.ok(!/limpio|clean/i.test(STUDIO_TEXT.es.scan_unavailable + STUDIO_TEXT.en.scan_unavailable));
});

test('etapas del Reel distintas y nunca "publicado" sin serlo', () => {
  const ok = { status: 'succeeded' };
  assert.equal(reelStage({ status: 'processing' }, null), 'generating');
  assert.equal(reelStage({ status: 'failed' }, null), 'failed');
  assert.equal(reelStage(ok, null), 'generated');
  assert.equal(reelStage(ok, { status: 'review' }), 'in_review');
  assert.equal(reelStage(ok, { status: 'approved' }), 'approved');
  assert.equal(reelStage(ok, { status: 'scheduled' }), 'scheduled');
  assert.equal(reelStage(ok, { status: 'scheduled' }, [{ status: 'pending' }]), 'pending_publication');
  assert.equal(reelStage(ok, { status: 'published' }), 'published');
  const all = new Set(['generating', 'generated', 'in_review', 'approved', 'scheduled', 'pending_publication', 'published', 'failed']);
  for (const st of ['idea', 'draft', 'review', 'approved', 'rejected', 'scheduled', 'publishing', 'failed', 'archived']) {
    const r = reelStage(ok, { status: st });
    assert.ok(all.has(r));
    if (st !== 'published') assert.notEqual(r, 'published');
  }
});

test('textos ES/EN completos para pestañas, pasos, estados y errores', () => {
  const es = STUDIO_TEXT.es, en = STUDIO_TEXT.en;
  assert.deepEqual(Object.keys(es).sort(), Object.keys(en).sort());
  assert.deepEqual(Object.keys(es.err).sort(), Object.keys(en.err).sort());
  for (const k of ['library', 'studio', 'jobs']) { assert.ok(TABS.includes(k)); assert.ok(es[`tab_${k}`] && en[`tab_${k}`]); }
  for (const k of WIZARD_STEPS) assert.ok(es[`step_${k}`] && en[`step_${k}`]);
  for (const k of ['draft', 'awaiting_generation_approval', 'queued', 'processing', 'succeeded', 'failed', 'cancelled']) assert.ok(es[`js_${k}`]);
  for (const k of ['generated', 'in_review', 'approved', 'scheduled', 'pending_publication', 'published', 'failed']) assert.ok(es[`rs_${k}`] && en[`rs_${k}`]);
  assert.ok(TABS.includes('brand') && TABS.includes('overview'));
});

test('ningún append() nativo recibe hijos condicionales (escribiría "null" en pantalla)', async () => {
  for (const f of ['marketing-library.js', 'marketing-studio.js']) {
    const src = await readFile(new URL(`../../manager/assets/js/modules/${f}`, import.meta.url), 'utf8');
    for (const m of src.matchAll(/\.append\(([\s\S]*?)\);\n/g)) assert.ok(!/:\s*null/.test(m[1]), `${f}: ${m[1].slice(0, 80)}`);
  }
});

test('formatos y utilidades', () => {
  assert.ok(!ACCEPT.includes('svg') && ACCEPT.includes('video/webm'));
  assert.equal(fmtBytes(52428800), '50.0 MB');
  assert.equal(fmtCost(0), 'USD 0.00');
  const k = newIdempotencyKey();
  assert.ok(k.length >= 16 && k.length <= 120 && /^[a-z0-9-]+$/.test(k));
  assert.notEqual(k, newIdempotencyKey());
});

test('el frontend de Fase 2 no contiene claves, URLs de proveedores ni nombres de empresa', async () => {
  const files = ['marketing-library.js', 'marketing-studio.js', 'marketing-studio-state.js', 'marketing-studio-i18n.js'];
  const src = (await Promise.all(files.map(f => readFile(new URL(`../../manager/assets/js/modules/${f}`, import.meta.url), 'utf8')))).join('\n');
  for (const w of ['api_key', 'apiKey', 'SERVICE_ROLE', 'Bearer sk', 'Golden Age', 'localStorage', 'innerHTML', 'OMNIROUTE_']) {
    assert.ok(!src.includes(w), w);
  }
});

test('archivo vencido (410 media_expired): mensaje completo en el idioma activo, sin prefijos', async () => {
  const { useTenantDefault } = await import('../../manager/assets/js/i18n.js');
  const { sErr } = await import('../../manager/assets/js/modules/marketing-studio-i18n.js');
  useTenantDefault('es');
  assert.equal(sErr({ code: 'media_expired', status: 410 }), 'El archivo ya venció.');
  useTenantDefault('en');
  assert.equal(sErr({ code: 'media_expired', status: 410 }), 'The file has expired.');
  const ui = await readFile(new URL('../../manager/assets/js/modules/marketing-ui.js', import.meta.url), 'utf8');
  assert.ok(!/Could not load|No se pudo/.test(ui.replace(/^\/\/.*$/gm, '')), 'errorNotice sin prefijo');
  for (const f of ['marketing.js', 'marketing-forms.js', 'marketing-library.js', 'marketing-studio.js']) {
    const src = await readFile(new URL(`../../manager/assets/js/modules/${f}`, import.meta.url), 'utf8');
    assert.ok(!/errorBox/.test(src) && /errorNotice/.test(src), `${f}: usa errorNotice, no errorBox`);
  }
});

test('presupuesto: solo IA de Marketing, nunca el costo total de AITA', () => {
  assert.equal(STUDIO_TEXT.es.budgetScopeNote, 'Presupuesto de IA de Marketing; no representa el costo total de AITA.');
  assert.equal(STUDIO_TEXT.en.budgetScopeNote, 'Marketing AI budget; it does not represent the total cost of AITA.');
  for (const lang of ['es', 'en']) assert.ok(!/80/.test(STUDIO_TEXT[lang].budgetScopeNote), lang);
});

test('almacenamiento: activo, vencido pendiente de eliminación, reservado y disponible (ES/EN)', () => {
  for (const lang of ['es', 'en']) {
    for (const k of ['stActive', 'stExpired', 'stReserved', 'stAvailable', 'stExpiredHint']) assert.ok(STUDIO_TEXT[lang][k], `${lang}.${k}`);
  }
  assert.match(STUDIO_TEXT.es.stExpiredHint, /ocupando espacio/);
  assert.match(STUDIO_TEXT.en.stExpiredHint, /still use space/);
});
