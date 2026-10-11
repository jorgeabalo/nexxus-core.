// AITA Marketing (frontend): navegación simple, Planear contenido, estados comerciales, Biblioteca y textos ES/EN.
// Ejecutar: node --test tests/js/marketing-studio.test.mjs
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile, readdir, access } from 'node:fs/promises';
import { privacyClass, usableAsSource, fmtBytes, newIdempotencyKey, ACCEPT }
  from '../../manager/assets/js/modules/marketing-studio-state.js';
import { addonQuote } from '../../manager/assets/js/modules/marketing-addons-state.js';
import { STUDIO_TEXT } from '../../manager/assets/js/modules/marketing-studio-i18n.js';
import { PLANNER_TEXT } from '../../manager/assets/js/modules/marketing-planner-i18n.js';
import { TABS, route, DISPLAY_STATUSES, shownStatus, PLAN_STEPS, PLAN_MODES, PLAN_FORMATS, planStepError, planBody }
  from '../../manager/assets/js/modules/marketing-state.js';

const MOD = (f) => new URL(`../../manager/assets/js/modules/${f}`, import.meta.url);
const read = (f) => readFile(MOD(f), 'utf8');
const TECH = /queued|\blease|worker|generation_job|reserved_cost|provider|\bmodels?\b|strategy|worker_id|attempts|estimated_cost|actual_cost|maximum_cost|error_code|awaiting_generation_approval/i;
const MARKETING_FILES = ['marketing.js', 'marketing-forms.js', 'marketing-planner.js', 'marketing-planner-i18n.js', 'marketing-i18n.js',
  'marketing-state.js', 'marketing-library.js', 'marketing-studio-state.js', 'marketing-studio-i18n.js', 'marketing-addons.js',
  'marketing-addons-state.js', 'marketing-plan-i18n.js', 'marketing-ui.js'];

// ------------------------------------------------------------------ navegación
test('navegación visible: Resumen, Contenido, Calendario, Campañas y Biblioteca (sin Estudio ni Trabajos)', async () => {
  const { T } = { T: (await import('../../manager/assets/js/modules/marketing-i18n.js')) };
  assert.deepEqual(TABS, ['overview', 'content', 'calendar', 'campaigns', 'library']);
  for (const gone of ['studio', 'jobs', 'brand', 'crm', 'competition']) assert.ok(!TABS.includes(gone), gone);
  const { useTenantDefault } = await import('../../manager/assets/js/i18n.js');
  useTenantDefault('es');
  assert.deepEqual(TABS.map(v => T.t(`tab_${v}`)), ['Resumen', 'Contenido', 'Calendario', 'Campañas', 'Biblioteca']);
  useTenantDefault('en');
  assert.deepEqual(TABS.map(v => T.t(`tab_${v}`)), ['Overview', 'Content', 'Calendar', 'Campaigns', 'Library']);
  await assert.rejects(access(MOD('marketing-studio.js')), 'el módulo del Estudio ya no se sirve');
  for (const f of MARKETING_FILES) {
    const src = await read(f);
    assert.ok(!/marketing-studio\.js|studioView|jobsView|tab_studio|tab_jobs|Estudio de Reels|Reel Studio|Trabajos de generación|Generation jobs/.test(src), f);
  }
});

test('enlaces antiguos: Estudio → Contenido con el planificador; Trabajos → Contenido', () => {
  assert.deepEqual(route('studio'), { tab: 'content', planner: true, brand: false, redirected: true });
  assert.deepEqual(route('jobs'), { tab: 'content', planner: false, brand: false, redirected: true });
  assert.deepEqual(route('content'), { tab: 'content', planner: false, brand: false });
  assert.deepEqual(route('brand'), { tab: 'overview', planner: false, brand: true });
  assert.equal(route('crm'), null);
});

test('el panel redirige los enlaces antiguos sin dejar enlaces huérfanos', async () => {
  const src = await read('marketing.js');
  assert.match(src, /route\(ctx\.params\[0\]\)/);
  assert.match(src, /history\.replaceState\(null, '', `#\/marketing\/\$\{r\.tab\}`\)/);
  for (const f of MARKETING_FILES) assert.ok(!/#\/marketing\/(studio|jobs)/.test(await read(f)), f);
  const api = await readFile(new URL('../../manager/assets/js/api.js', import.meta.url), 'utf8');
  assert.ok(!/marketing\/(studio|jobs)/.test(api) && /content\/plan/.test(api) && /\/retry/.test(api));
});

// ------------------------------------------------------------------ Planear contenido
test('planificador: 6 pasos, solo 2 modos y 4 formatos', () => {
  assert.deepEqual(PLAN_STEPS, ['objective', 'mode', 'format', 'instructions', 'proposal', 'review']);
  assert.deepEqual(PLAN_MODES, ['library_ai', 'fully_ai']);
  assert.deepEqual(PLAN_FORMATS, ['image', 'carousel', 'reel', 'video']);
  for (const lang of ['es', 'en']) {
    for (const k of PLAN_STEPS) assert.ok(PLANNER_TEXT[lang][`st_${k}`], `${lang}.st_${k}`);
    for (const f of PLAN_FORMATS) assert.ok(PLANNER_TEXT[lang][`f_${f}`], `${lang}.f_${f}`);
  }
  assert.deepEqual(PLAN_FORMATS.map(f => PLANNER_TEXT.es[`f_${f}`]), ['Imagen', 'Carrusel', 'Reel', 'Video']);
  assert.equal(PLANNER_TEXT.es.title, 'Planear contenido');
  assert.equal(PLANNER_TEXT.en.title, 'Plan content');
});

test('planificador: Biblioteca exige archivos; completamente con IA no admite ninguno', () => {
  assert.equal(planStepError('objective', {}), 'pick_objective');
  assert.equal(planStepError('mode', { mode: 'library_ai', media: [] }), 'real_media_required');
  assert.equal(planStepError('mode', { mode: 'fully_ai', media: ['m1'] }), 'fully_ai_uses_no_media');
  assert.equal(planStepError('mode', { mode: 'mix', media: [] }), 'pick_mode');
  assert.equal(planStepError('format', { format: 'story' }), 'pick_format');
  assert.equal(planStepError('instructions', { platforms: [] }), 'channels_required');
  const body = planBody({ objective: 'calls', mode: 'fully_ai', format: 'reel', media: ['m1'], platforms: ['instagram'], key: 'k' });
  assert.deepEqual(body.media_ids, []);
  assert.ok(!/percent|model|provider|cost|quality|strategy|budget/i.test(JSON.stringify(body)), JSON.stringify(body));
  assert.deepEqual(planBody({ objective: 'calls', mode: 'library_ai', format: 'image', media: ['m1'], platforms: ['x'] }).media_ids, ['m1']);
});

test('revisión: Rechazar y Solicitar cambios exigen comentario; Programar solo tras aprobar', async () => {
  const planner = await read('marketing-planner.js');
  assert.match(planner, /\(to === 'draft' \|\| to === 'rejected'\) && !w\.comment\.trim\(\)/);
  assert.match(planner, /approved \? el\('div'[\s\S]*?'scheduled'/, 'Programar solo aparece aprobado');
  const forms = await read('marketing-forms.js');
  assert.match(forms, /\(to === 'rejected' \|\| asksChanges\(to\)\) && !comment\.value\.trim\(\)/);
  assert.match(forms, /asksChanges = \(to\) => to === 'draft' && c\.status === 'review'/);
  for (const lang of ['es', 'en']) for (const k of ['approve', 'changes', 'reject', 'schedule', 'retry']) assert.ok(PLANNER_TEXT[lang][k]);
  assert.equal(PLANNER_TEXT.es.changes, 'Solicitar cambios');
  assert.equal(PLANNER_TEXT.es.retry, 'Reintentar');
});

// ------------------------------------------------------------------ estados comerciales
test('estados visibles: solo comerciales, en ES y EN', async () => {
  const { t } = await import('../../manager/assets/js/modules/marketing-i18n.js');
  const { useTenantDefault } = await import('../../manager/assets/js/i18n.js');
  const main = ['draft', 'preparing', 'ready_for_review', 'changes_requested', 'approved', 'scheduled', 'published', 'could_not_prepare'];
  for (const s of main) assert.ok(DISPLAY_STATUSES.includes(s), s);
  useTenantDefault('es');
  assert.deepEqual(main.map(s => t(`s_${s}`)), ['Borrador', 'Preparando', 'Listo para revisar', 'Cambios solicitados', 'Aprobado',
    'Programado', 'Publicado', 'No se pudo preparar']);
  useTenantDefault('en');
  assert.deepEqual(main.map(s => t(`s_${s}`)), ['Draft', 'Preparing', 'Ready for review', 'Changes requested', 'Approved',
    'Scheduled', 'Published', 'Could not prepare']);
  for (const raw of ['idea', 'review', 'generating', 'failed', 'publishing', 'queued', 'whatever']) {
    assert.ok(DISPLAY_STATUSES.includes(shownStatus(raw)), raw);
  }
  assert.equal(shownStatus('review'), 'ready_for_review');
  assert.equal(shownStatus('failed'), 'could_not_prepare');
});

test('ningún término técnico en los archivos servidos de Marketing ni en sus traducciones', async () => {
  for (const f of MARKETING_FILES) assert.ok(!TECH.test(await read(f)), `${f}: ${(await read(f)).match(TECH)}`);
  const html = await readFile(new URL('../../manager/index.html', import.meta.url), 'utf8');
  assert.ok(!TECH.test(html));
  const { T } = { T: await import('../../manager/assets/js/modules/marketing-i18n.js') };
  for (const lang of ['es', 'en']) {
    const texts = [PLANNER_TEXT[lang], STUDIO_TEXT[lang]].map(o => JSON.stringify(o) + Object.values(o)
      .filter(v => typeof v === 'function').map(f => String(f(3, 6))).join(' ')).join(' ');
    assert.ok(!TECH.test(texts), `${lang}: ${texts.match(TECH)}`);
  }
  assert.ok(T.t);
});

test('ES y EN tienen las mismas claves (planificador y Biblioteca)', () => {
  for (const X of [PLANNER_TEXT, STUDIO_TEXT]) assert.deepEqual(Object.keys(X.es).sort(), Object.keys(X.en).sort());
  assert.deepEqual(Object.keys(STUDIO_TEXT.es.err).sort(), Object.keys(STUDIO_TEXT.en.err).sort());
});

// ------------------------------------------------------------------ Biblioteca
test('privacidad: desconocido, menores, revocado, simulado, sin validar o vencido nunca es utilizable', () => {
  const base = { processing_status: 'ready', validation_status: 'passed' };
  assert.equal(privacyClass({ ...base, contains_people: null, people_policy: 'exclude' }), 'restricted');
  assert.equal(usableAsSource({ ...base, contains_people: null, people_policy: 'exclude' }), false);
  assert.equal(usableAsSource({ ...base, contains_people: false, people_policy: 'no_people' }), true);
  assert.equal(usableAsSource({ ...base, contains_people: false, people_policy: 'no_people', validation_status: 'pending' }), false);
  const now = Date.parse('2026-10-15T12:00:00Z');
  const media = { ...base, contains_people: false, people_policy: 'no_people' };
  assert.equal(usableAsSource({ ...media, expires_at: '2026-10-15T11:59:59Z' }, now), false);
  assert.equal(usableAsSource({ ...media, expires_at: '2026-10-16T00:00:00Z' }, now), true);
  const ok = { ...base, contains_people: true, contains_minors: false, people_policy: 'consented', consent_status: 'granted' };
  assert.equal(usableAsSource(ok), true);
  assert.equal(usableAsSource({ ...ok, consent_status: 'pending' }), false);
  assert.equal(usableAsSource({ ...ok, contains_minors: null }), false);
  assert.equal(usableAsSource({ ...ok, contains_minors: true }), false);
  assert.equal(usableAsSource({ ...ok, consent_status: 'revoked', people_policy: 'exclude' }), false);
  const anon = { ...base, contains_people: true, contains_minors: false, people_policy: 'anonymize' };
  assert.equal(usableAsSource(anon), false);
  assert.equal(usableAsSource({ ...anon, derivatives: [{ kind: 'anonymized', status: 'ready', is_mock: true }] }), false);
  assert.equal(usableAsSource({ ...anon, derivatives: [{ kind: 'anonymized', status: 'ready', is_mock: false }] }), true);
});

test('textos de Biblioteca y estados simulados', () => {
  assert.match(STUDIO_TEXT.es.ds_mock_only, /NO anonimizado/);
  assert.match(STUDIO_TEXT.en.ds_mock_only, /NOT anonymized/);
  assert.ok(!/limpio|clean/i.test(STUDIO_TEXT.es.scan_unavailable + STUDIO_TEXT.en.scan_unavailable));
  for (const lang of ['es', 'en']) {
    for (const k of ['stActive', 'stExpired', 'stReserved', 'stAvailable', 'stExpiredHint', 'minorsShort', 'consent', 'expires']) {
      assert.ok(STUDIO_TEXT[lang][k], `${lang}.${k}`);
    }
  }
  assert.match(STUDIO_TEXT.es.stExpiredHint, /ocupando espacio/);
  assert.match(STUDIO_TEXT.en.stExpiredHint, /still use space/);
});

test('ningún append() nativo recibe hijos condicionales (escribiría "null" en pantalla)', async () => {
  for (const f of ['marketing-library.js', 'marketing-planner.js']) {
    const src = await read(f);
    for (const m of src.matchAll(/\.append\(([\s\S]*?)\);\n/g)) assert.ok(!/:\s*null/.test(m[1]), `${f}: ${m[1].slice(0, 80)}`);
  }
});

test('formatos y utilidades', () => {
  assert.ok(!ACCEPT.includes('svg') && ACCEPT.includes('video/webm'));
  assert.equal(fmtBytes(52428800), '50.0 MB');
  const k = newIdempotencyKey();
  assert.ok(k.length >= 16 && k.length <= 100 && /^[a-z0-9-]+$/.test(k));
  assert.notEqual(k, newIdempotencyKey());
  assert.equal(addonQuote(1).amount, 6);
});

test('el frontend de Marketing no contiene claves, URLs de proveedores ni nombres de empresa', async () => {
  const src = (await Promise.all(MARKETING_FILES.map(read))).join('\n');
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
  for (const f of ['marketing.js', 'marketing-forms.js', 'marketing-library.js']) {
    const src = await read(f);
    assert.ok(!/errorBox/.test(src) && /errorNotice/.test(src), `${f}: usa errorNotice, no errorBox`);
  }
});

test('ningún archivo servido al Manager menciona topes internos, presupuestos globales ni márgenes', async () => {
  const root = new URL('../../manager/', import.meta.url);
  const files = [];
  const walk = async (dir) => {
    for (const e of await readdir(dir, { withFileTypes: true })) {
      const u = new URL(e.name + (e.isDirectory() ? '/' : ''), dir);
      if (e.isDirectory()) await walk(u); else if (/\.(html|js|mjs|css|json)$/.test(e.name)) files.push(u);
    }
  };
  await walk(root);
  assert.ok(files.length > 10);
  const banned = [/\b80\s*USD\b/i, /\bUSD\s*80\b/i, /US?\$\s*80\b/i, /presupuesto global/i, /global budget/i, /AITA global/i,
    /costo total de AITA/i, /total cost of AITA/i, /margen de AITA/i, /AITA('s)? margin/i, /profit margin/i, /omniroute/i,
    /price_source/i, /unit_price/i, /precio unitario/i, /unit price/i];
  for (const f of files) {
    const src = await readFile(f, 'utf8');
    for (const re of banned) assert.ok(!re.test(src), `${f.pathname.split('/manager/')[1]}: ${re}`);
  }
});

test('Marketing (y el planificador) solo para owner y manager en el frontend', async () => {
  const { CATALOG, canOpen } = await import('../../manager/assets/js/nav.js');
  assert.deepEqual(CATALOG.find(m => m.key === 'marketing').roles, ['owner', 'manager']);
  const all = { modules: {} };
  for (const role of ['staff', 'member', 'socio', '']) assert.equal(canOpen(all, role, 'marketing'), false, role);
  for (const role of ['owner', 'manager']) assert.equal(canOpen(all, role, 'marketing'), true, role);
});
