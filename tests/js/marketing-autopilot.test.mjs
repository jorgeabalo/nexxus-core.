// AITA Marketing (frontend): modo de control y piloto automático, fechas localizadas y resultados sin cifras falsas.
// Ejecutar: node --test tests/js/marketing-autopilot.test.mjs
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { AUTOPILOT_TEXT } from '../../manager/assets/js/modules/marketing-autopilot-i18n.js';
import { PLAN_TEXT } from '../../manager/assets/js/modules/marketing-plan-i18n.js';
import { fmtWhen, fmtDay, autopilotRules, autopilotStepError } from '../../manager/assets/js/modules/marketing-state.js';

const read = (f) => readFile(new URL(`../../manager/assets/js/${f}`, import.meta.url), 'utf8');
const ES = AUTOPILOT_TEXT.es, EN = AUTOPILOT_TEXT.en;

test('dos modos de control con el manual como predeterminado', async () => {
  assert.equal(ES.title, '¿Cómo quieres controlar las publicaciones?');
  assert.deepEqual([ES.manual, ES.auto], ['Revisar antes de publicar', 'Piloto automático de AITA']);
  assert.deepEqual([EN.manual, EN.auto], ['Review before publishing', 'AITA autopilot']);
  const src = await read('modules/marketing-autopilot.js');
  assert.match(src, /const active = st\.mode === 'autopilot'/, 'solo se marca el piloto si el backend dice que está activo');
});

test('textos exactos de autorización, espera de canales y cuota agotada', () => {
  assert.equal(ES.auth_text, 'Autorizo a AITA a crear, aprobar, programar y publicar automáticamente dentro de estas reglas y del número de publicaciones disponible en mi plan.');
  assert.equal(ES.waiting, 'El piloto comenzará cuando conectes y autorices tus canales.');
  assert.equal(ES.quota, 'Utilizaste las publicaciones disponibles de este mes. Puedes esperar al próximo ciclo o solicitar más publicaciones en Campañas.');
  for (const k of ['auth_text', 'waiting', 'quota']) assert.ok(EN[k] && EN[k] !== ES[k], k);
  assert.deepEqual(Object.keys(ES).sort(), Object.keys(EN).sort());
  for (const lang of ['es', 'en']) {
    const all = JSON.stringify(AUTOPILOT_TEXT[lang]);
    assert.ok(!/est[aá] publicando|is publishing|viral|garantiza|guarantee/i.test(all), `${lang}: no afirma que publica`);
  }
});

test('activar exige calendario preliminar de estas reglas y autorización marcada; pausar es un solo clic', async () => {
  const src = await read('modules/marketing-autopilot.js');
  assert.match(src, /if \(seen !== JSON\.stringify\(body\)\) \{ err\.textContent = ap\('need_preview'\); return; \}/);
  assert.match(src, /if \(!auth\.checked\) \{ err\.textContent = ap\('need_auth'\); return; \}/);
  assert.match(src, /await M\.autopilotPause\(ctx\.tenantId\)/);
  assert.ok(!/confirm\(|prompt\(/.test(src), 'la pausa no pide confirmación');
  const api = await read('api.js');
  assert.match(api, /autopilotActivate[\s\S]*?confirm: true/);
});

test('reglas del piloto: objetivo, plataformas, días, horario, formatos, modo y fecha', () => {
  const base = { objective: 'calls', platforms: ['instagram'], days: [3, 0], from: '10:00', to: '18:00',
    formats: ['reel'], mode: 'fully_ai', cta: ' Llámanos ', banned: 'descuento\n\nprecio\n', starts_on: '2026-11-02' };
  const r = autopilotRules(base);
  assert.deepEqual(r.days, [0, 3]);
  assert.deepEqual(r.banned_topics, ['descuento', 'precio']);
  assert.equal(r.cta, 'Llámanos');
  assert.equal(autopilotStepError(r), null);
  assert.equal(autopilotStepError(autopilotRules({ ...base, days: [] })), 'invalid_days');
  assert.equal(autopilotStepError(autopilotRules({ ...base, from: '18:00', to: '10:00' })), 'invalid_hours');
  assert.equal(autopilotStepError(autopilotRules({ ...base, formats: ['story'] })), 'invalid_format');
  assert.equal(autopilotStepError(autopilotRules({ ...base, mode: 'mix' })), 'invalid_mode');
  assert.equal(autopilotStepError(autopilotRules({ ...base, platforms: [] })), 'channels_required');
});

test('fechas: español e inglés en America/Chicago, sin «Nov 20, 10:00 AM» en español', () => {
  const iso = '2026-11-20T16:00:00Z';
  const es = fmtWhen(iso, 'es', 'America/Chicago'), en = fmtWhen(iso, 'en', 'America/Chicago');
  assert.match(es, /20 de nov/);
  assert.match(es, /10:00/);
  assert.ok(!/Nov 20|AM|PM/.test(es), es);
  assert.match(en, /Nov 20/);
  assert.match(en, /10:00\sAM/);
  assert.notEqual(fmtWhen(iso, 'es', 'America/Chicago'), fmtWhen(iso, 'en', 'America/Chicago'), 'cambia con el idioma');
});

test('fechas: cambio de mes y cambio de horario de verano en la zona del tenant', () => {
  // 04:30 UTC del 1 de noviembre todavía es 31 de octubre en Chicago.
  assert.match(fmtWhen('2026-11-01T04:30:00Z', 'es', 'America/Chicago'), /31 de oct/);
  assert.match(fmtWhen('2026-11-01T04:30:00Z', 'en', 'America/Chicago'), /Oct 31/);
  assert.match(fmtWhen('2026-11-01T04:30:00Z', 'en', 'UTC'), /Nov 1/);
  // Fin del horario de verano (1 nov 2026): 06:30Z = 1:30 CDT y 07:30Z = 1:30 CST; 08:30Z = 2:30 CST.
  assert.match(fmtWhen('2026-11-01T06:30:00Z', 'en', 'America/Chicago'), /1:30\sAM/);
  assert.match(fmtWhen('2026-11-01T07:30:00Z', 'en', 'America/Chicago'), /1:30\sAM/);
  assert.match(fmtWhen('2026-11-01T08:30:00Z', 'en', 'America/Chicago'), /2:30\sAM/);
  // Inicio (8 mar 2026): 07:59Z = 1:59 CST y 08:00Z = 3:00 CDT.
  assert.match(fmtWhen('2026-03-08T07:59:00Z', 'es', 'America/Chicago'), /1:59/);
  assert.match(fmtWhen('2026-03-08T08:00:00Z', 'es', 'America/Chicago'), /3:00/);
  assert.match(fmtDay('2026-11-02', 'es'), /2 de nov/);
  assert.match(fmtDay('2026-11-02', 'en'), /Nov 2/);
});

test('todas las fechas visibles de Marketing usan el formateador localizado', async () => {
  for (const f of ['marketing.js', 'marketing-forms.js', 'marketing-planner.js', 'marketing-library.js', 'marketing-autopilot.js']) {
    const src = await read(`modules/${f}`);
    assert.ok(!/fmtDateTime|toLocale(Date|Time)?String/.test(src), `${f}: usa when()/day()`);
  }
  const ui = await read('modules/marketing-ui.js');
  assert.match(ui, /when = \(iso\) => fmtWhen\(iso, getLang\(\), tz\(\)\)/);
});

test('resultados: estado sin cifras inventadas', async () => {
  assert.equal(PLAN_TEXT.es.results_not_connected, 'Los resultados estarán disponibles cuando conectes tus canales.');
  assert.equal(PLAN_TEXT.es.results_none, 'Aún no hay resultados.');
  assert.ok(PLAN_TEXT.en.results_not_connected && PLAN_TEXT.en.results_none);
  const main = await read('modules/marketing.js');
  assert.ok(!/results_available|k_results/.test(main), 'ningún 0 de resultados en el resumen');
  assert.match(main, /resultsCard\(d\.results\)/);
});

test('el planificador envía su Idempotency-Key y la API la manda en la cabecera', async () => {
  const planner = await read('modules/marketing-planner.js');
  assert.match(planner, /M\.planContent\(ctx\.tenantId, planBody\([^)]*\)[^,]*\), w\.key\)/);
  const api = await read('api.js');
  assert.match(api, /'Idempotency-Key': key/);
});
