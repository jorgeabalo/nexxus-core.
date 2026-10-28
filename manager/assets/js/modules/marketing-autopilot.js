// Contenido → «¿Cómo quieres controlar las publicaciones?»: Revisar antes de publicar (predeterminado) o Piloto
// automático de AITA. Activar exige ver el calendario preliminar y marcar la autorización; pausar es inmediato.
// Hoy no hay canales conectados: se puede configurar y previsualizar, pero nada se envía ni se publica.
import { el, clear, openModal, field, input, select, toast } from '../ui.js';
import { api } from '../api.js';
import { ap, has } from './marketing-autopilot-i18n.js';
import { pl } from './marketing-planner-i18n.js';
import { t, errText } from './marketing-i18n.js';
import { CHANNELS, PLAN_OBJECTIVES, PLAN_MODES, PLAN_FORMATS, autopilotRules, autopilotStepError } from './marketing-state.js';
import { channelLabel } from './marketing-forms.js';
import { when, day } from './marketing-ui.js';

const M = api.marketing;
const msg = (e) => (e && e.code && has(`e_${e.code}`) ? ap(`e_${e.code}`) : errText(e));

function checks(options, selected, labelOf) {
  const box = el('div', { class: 'mk-checks' }, options.map(o => el('label', { class: 'mk-check' },
    el('input', { type: 'checkbox', value: String(o), checked: selected.includes(o) }), el('span', {}, labelOf(o)))));
  box.values = () => [...box.querySelectorAll('input:checked')].map(i => i.value);
  return box;
}

// Tarjeta del modo de control (dentro de Contenido).
export async function controlCard(ctx, reload) {
  const st = await M.autopilot(ctx.tenantId);
  const active = st.mode === 'autopilot';
  const option = (mode) => el('div', { class: `card mk-mode${(mode === 'autopilot') === active ? ' selected' : ''}`,
    dataset: { mode } }, el('strong', {}, ap(mode === 'manual' ? 'manual' : 'auto')),
  el('span', { class: 'hint' }, ap(mode === 'manual' ? 'manual_desc' : 'auto_desc')));
  const err = el('p', { class: 'form-error', role: 'alert' });
  const pause = el('button', { class: 'btn btn-danger', type: 'button', onclick: async (e) => {
    e.target.disabled = true; err.textContent = '';
    try { await M.autopilotPause(ctx.tenantId); toast(ap('paused_ok')); reload(); }
    catch (x) { err.textContent = msg(x); e.target.disabled = false; }
  } }, ap('pause'));
  const setup = el('button', { class: 'btn btn-sm', type: 'button', disabled: !st.can_manage,
    onclick: () => autopilotModal(ctx, st, reload) }, ap(active ? 'change' : 'configure'));
  const delegate = el('input', { type: 'checkbox', checked: st.managers_can_manage, onchange: async (e) => {
    err.textContent = '';
    try { await M.autopilotPermission(ctx.tenantId, e.target.checked); toast(t('saved')); }
    catch (x) { err.textContent = msg(x); e.target.checked = !e.target.checked; }
  } });
  const status = active ? el('div', { class: 'mk-note', role: 'status' }, el('strong', {}, ap('active')), ' · ',
    ap('version', st.rules_version), st.starts_on ? ` · ${ap('start')}: ${day(st.starts_on)}` : '',
    st.message ? el('div', {}, ap(st.message === 'quota_exhausted' ? 'quota' : 'waiting')) : null)
    : st.paused_at ? el('p', { class: 'hint' }, ap('paused'), ` (${when(st.paused_at)})`) : null;
  return el('section', { class: 'card mk-control', dataset: { mode: st.mode } },
    el('div', { class: 'card-head' }, el('h2', {}, ap('title'))),
    el('div', { class: 'card-body' },
      el('div', { class: 'grid-2 mk-mode-grid' }, option('manual'), option('autopilot')), status,
      el('div', { class: 'btn-row mk-control-actions' }, setup, active && st.can_pause ? pause : null),
      st.can_manage ? null : el('p', { class: 'hint' }, ap('no_permission')),
      st.can_delegate ? el('label', { class: 'mk-check' }, delegate,
        el('span', {}, ap('delegate'), el('span', { class: 'hint' }, ` — ${ap('delegate_note')}`))) : null, err));
}

// Configuración, calendario preliminar y autorización explícita.
export function autopilotModal(ctx, st, reload) {
  const r = st.rules || {};
  const tomorrow = new Date(Date.now() + 86400000).toISOString().slice(0, 10);
  const f = {
    objective: select(PLAN_OBJECTIVES.map(o => ({ value: o, label: pl(`ob_${o}`), selected: o === (r.objective || 'calls') }))),
    platforms: checks(CHANNELS, r.platforms || ['instagram'], channelLabel),
    days: checks([0, 1, 2, 3, 4, 5, 6], r.days || [0, 3], d => t('weekdays')[d]),
    from: input({ type: 'time', value: (r.hours || {}).from || '10:00' }),
    to: input({ type: 'time', value: (r.hours || {}).to || '18:00' }),
    formats: checks(PLAN_FORMATS, r.formats || ['image', 'reel'], x => pl(`f_${x}`)),
    mode: select(PLAN_MODES.map(m => ({ value: m, label: ap(`mode_${m}`), selected: m === (r.mode || 'fully_ai') }))),
    cta: input({ maxlength: '200', value: r.cta || '' }),
    banned: el('textarea', { class: 'input', rows: '2', maxlength: '2000' }, (r.banned_topics || []).join('\n')),
    start: input({ type: 'date', value: st.starts_on || tomorrow, min: tomorrow }),
  };
  const auth = el('input', { type: 'checkbox' });
  const cal = el('div', { class: 'mk-ap-calendar' });
  const err = el('p', { class: 'form-error', role: 'alert' });
  let seen = null;
  const rules = () => autopilotRules({
    objective: f.objective.value, platforms: f.platforms.values(), days: f.days.values().map(Number),
    from: f.from.value, to: f.to.value, formats: f.formats.values(), mode: f.mode.value, cta: f.cta.value,
    banned: f.banned.value, starts_on: f.start.value });
  const check = () => { const e = autopilotStepError(rules()); if (e) err.textContent = msg({ code: e }); return !e; };

  async function preview(btn) {
    err.textContent = '';
    if (!check()) return;
    btn.disabled = true;
    try {
      const body = rules();
      const p = await M.autopilotPreview(ctx.tenantId, body);
      seen = JSON.stringify(body);
      clear(cal).append(el('h4', {}, ap('preview_title')),
        el('p', { class: 'hint' }, p.available ? ap('planned', p.planned, p.available) : ap('none_available')),
        el('ol', { class: 'mk-flow' }, ...p.slots.map(s => el('li', {}, `${when(s.at)} · ${pl(`f_${s.format}`)} · `
          + s.platforms.map(channelLabel).join(', ')))),
        p.unplaced ? el('p', { class: 'hint' }, ap('unplaced', p.unplaced)) : el('span'),
        el('p', { class: 'mk-note' }, ap('waiting')));
    } catch (e) { err.textContent = msg(e); } finally { btn.disabled = false; }
  }

  const close = openModal({
    closeLabel: t('close'), title: ap('configure'),
    body: el('div', { class: 'mk-autopilot' },
      el('form', { class: 'form-grid', onsubmit: (e) => e.preventDefault() },
        field(ap('objective'), f.objective), field(ap('mode'), f.mode),
        field(ap('platforms'), f.platforms, { full: true }), field(ap('days'), f.days, { full: true }),
        field(`${ap('hours')} · ${ap('from')}`, f.from), field(`${ap('hours')} · ${ap('to')}`, f.to),
        field(ap('formats'), f.formats, { full: true }), field(ap('cta'), f.cta), field(ap('start'), f.start),
        field(ap('banned'), f.banned, { full: true, hint: ap('banned_hint') })),
      el('p', { class: 'hint' }, ap('rules_note')),
      el('div', { class: 'btn-row' }, el('button', { class: 'btn', type: 'button', onclick: (e) => preview(e.target) }, ap('preview'))),
      cal,
      el('label', { class: 'mk-check mk-ap-auth' }, auth, el('span', {}, ap('auth_text'))), err),
    actions: [(done) => el('button', { class: 'btn btn-primary', type: 'button', onclick: async (e) => {
      err.textContent = '';
      if (!check()) return;
      const body = rules();
      if (seen !== JSON.stringify(body)) { err.textContent = ap('need_preview'); return; }   // calendario de estas reglas
      if (!auth.checked) { err.textContent = ap('need_auth'); return; }
      e.target.disabled = true;
      try { await M.autopilotActivate(ctx.tenantId, body); toast(ap('activated')); done(); reload(); }
      catch (x) { err.textContent = msg(x); e.target.disabled = false; }
    } }, ap('activate'))],
  });
  return close;
}

