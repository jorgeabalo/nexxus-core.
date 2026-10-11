// Contenido → Planear contenido: objetivo, cómo crear, formato, instrucciones, propuesta y revisión.
// El backend prepara la propuesta (si el plan lo permite) y la deja como «Listo para revisar». Nada se publica
// automáticamente: aprobar, solicitar cambios o rechazar siguen las reglas de aprobación de siempre.
import { el, clear, openModal, field, input, select, fmtDateTime, tz } from '../ui.js';
import { api } from '../api.js';
import { getLang } from '../i18n.js';
import { pl } from './marketing-planner-i18n.js';
import { t, errText } from './marketing-i18n.js';
import { usableAsSource, newIdempotencyKey } from './marketing-studio-state.js';
import { CHANNELS, PLAN_STEPS, PLAN_OBJECTIVES, PLAN_MODES, PLAN_FORMATS, TONES, planStepError, planBody, localInput }
  from './marketing-state.js';
import { channelLabel, statusBadge } from './marketing-forms.js';

const M = api.marketing;

function choiceGrid(values, current, label, desc, onPick) {
  return el('div', { class: 'grid-2 mk-mode-grid mk-plan-choices' }, ...values.map(v =>
    el('button', { type: 'button', class: `card mk-mode${current === v ? ' selected' : ''}`, 'aria-pressed': String(current === v),
      onclick: () => onPick(v) }, el('strong', {}, label(v)), desc ? el('span', { class: 'hint' }, desc(v)) : null)));
}

export async function plannerModal(ctx, reload) {
  const body = el('div', { class: 'mk-planner' });
  const close = openModal({ closeLabel: t('close'), title: pl('title'), body });
  let files = [];
  try { files = ((await M.library(ctx.tenantId)).items || []).filter(usableAsSource); } catch (e) { files = []; }
  const w = { step: 0, objective: null, mode: null, format: null, media: [], topic: '', audience: '', offer: '',
    tone: 'professional', cta: '', platforms: ['instagram'], date: '', language: getLang(), key: newIdempotencyKey(),
    result: null, comment: '', when: '' };
  const err = el('p', { class: 'form-error', role: 'alert' });
  const done = () => { close(); reload(); };

  const text = (key, max) => input({ maxlength: String(max), value: w[key], oninput: e => { w[key] = e.target.value; } });
  const views = {
    objective: () => el('div', {}, el('p', {}, pl('q_objective')),
      choiceGrid(PLAN_OBJECTIVES, w.objective, v => pl(`ob_${v}`), null, v => { w.objective = v; render(); })),
    mode: () => el('div', {}, el('p', {}, pl('q_mode')),
      choiceGrid(PLAN_MODES, w.mode, v => pl(`mode_${v}`), v => pl(`mode_${v}_desc`),
        v => { w.mode = v; if (v === 'fully_ai') w.media = []; render(); }),
      w.mode === 'library_ai' ? el('div', { class: 'mk-plan-files' }, el('h3', {}, pl('pickFiles')),
        files.length ? el('div', { class: 'mk-checks' }, files.map(m => el('label', { class: 'mk-check' },
          el('input', { type: 'checkbox', checked: w.media.includes(m.id), onchange: e => {
            w.media = e.target.checked ? [...w.media, m.id] : w.media.filter(x => x !== m.id); } }),
          el('span', { class: 'mk-file' }, m.original_filename || m.id.slice(0, 8),
            m.expires_at ? el('span', { class: 'hint' }, ` · ${pl('expiresOn', fmtDateTime(m.expires_at))}`) : null))))
          : el('p', { class: 'hint' }, pl('noFiles'))) : null),
    format: () => el('div', {}, el('p', {}, pl('q_format')),
      choiceGrid(PLAN_FORMATS, w.format, v => pl(`f_${v}`), null, v => { w.format = v; render(); })),
    instructions: () => {
      const plats = el('div', { class: 'mk-checks' }, CHANNELS.map(c => el('label', { class: 'mk-check' },
        el('input', { type: 'checkbox', checked: w.platforms.includes(c), onchange: e => {
          w.platforms = e.target.checked ? [...w.platforms, c] : w.platforms.filter(x => x !== c); } }),
        el('span', {}, channelLabel(c)))));
      const tone = select(TONES.map(v => ({ value: v, label: pl(`tone_${v}`), selected: v === w.tone })),
        { onchange: e => { w.tone = e.target.value; } });
      const date = input({ type: 'datetime-local', value: w.date, onchange: e => { w.date = e.target.value; } });
      return el('div', {}, el('p', { class: 'hint' }, pl('q_instructions')),
        el('div', { class: 'form-grid' },
          field(pl('topic'), text('topic', 300), { full: true }), field(pl('audience'), text('audience', 300)),
          field(pl('offer'), text('offer', 300)), field(pl('tone'), tone), field(pl('cta'), text('cta', 200)),
          field(pl('date'), date), field(pl('platforms'), plats, { full: true })));
    },
    proposal: () => {
      const r = w.result, c = r.content;
      const row = (k, v) => (v ? [el('dt', {}, k), el('dd', {}, v)] : []);
      const state = c.display_status === 'preparing' ? el('p', { class: 'mk-note', role: 'status' }, pl('preparingLater'))
        : c.display_status === 'could_not_prepare' ? el('p', { class: 'error-box', role: 'status' }, pl('failed'))
          : r.preparation === 'unavailable' ? el('p', { class: 'mk-note', role: 'status' }, pl('unavailable')) : null;
      return el('div', {}, el('p', {}, statusBadge(c.display_status), ' ', pl('proposalNote')), state,
        el('dl', { class: 'mk-summary' },
          ...row(pl('p_text'), c.caption), ...row(pl('p_cta'), c.cta), ...row(pl('p_format'), pl(`f_${c.format}`)),
          ...row(pl('p_visuals'), w.mode === 'library_ai' ? pl('visualsLib', w.media.length) : pl('visualsAi')),
          ...row(pl('p_platforms'), (c.channels || []).map(channelLabel).join(', ')),
          ...row(pl('p_date'), c.planned_at ? fmtDateTime(c.planned_at) : null), ...row(pl('p_objective'), pl(`ob_${w.objective}`))));
    },
    review: () => {
      const c = w.result.content;
      const comment = el('textarea', { class: 'input', maxlength: '2000', rows: '2', oninput: e => { w.comment = e.target.value; } }, w.comment);
      const when = input({ type: 'datetime-local', value: w.when || localInput(c.planned_at, tz()), onchange: e => { w.when = e.target.value; } });
      const ready = c.display_status === 'ready_for_review', approved = c.display_status === 'approved';
      return el('div', {}, el('p', {}, statusBadge(c.display_status), ' ', pl('q_review')),
        ready ? field(pl('comment'), comment, { full: true }) : null,
        ready ? el('div', { class: 'btn-row mk-plan-review' },
          el('button', { class: 'btn btn-primary', type: 'button', onclick: e => act(e.target, 'approved') }, pl('approve')),
          el('button', { class: 'btn', type: 'button', onclick: e => act(e.target, 'draft') }, pl('changes')),
          el('button', { class: 'btn btn-danger', type: 'button', onclick: e => act(e.target, 'rejected') }, pl('reject'))) : null,
        ready ? el('p', { class: 'hint' }, pl('scheduleAfter')) : null,
        approved ? el('div', { class: 'form-grid' }, field(pl('when'), when),
          el('div', { class: 'btn-row' }, el('button', { class: 'btn btn-accent', type: 'button',
            onclick: e => act(e.target, 'scheduled') }, pl('schedule')))) : null);
    },
  };

  async function act(btn, to) {
    err.textContent = '';
    if ((to === 'draft' || to === 'rejected') && !w.comment.trim()) { err.textContent = pl('e_comment_required'); return; }
    btn.disabled = true;
    try {
      await M.transition(ctx.tenantId, w.result.content.id, to, { comment: w.comment.trim(), scheduledAt: to === 'scheduled' ? w.when : null });
      w.result = { ...w.result, content: (await M.content(ctx.tenantId, w.result.content.id)).content };
      w.comment = '';
      if (to !== 'approved') { done(); return; }
      render();
    } catch (e) { err.textContent = errText(e); } finally { btn.disabled = false; }
  }

  async function prepare(btn) {
    btn.disabled = true; err.textContent = '';
    try {
      w.result = await M.planContent(ctx.tenantId, planBody(w, { objective: pl(`ob_${w.objective}`) }));
      w.step += 1; render();
    } catch (e) { err.textContent = errText(e); btn.disabled = false; }
  }

  async function retry(btn) {
    btn.disabled = true; err.textContent = '';
    try { w.result = await M.retryContent(ctx.tenantId, w.result.content.id); render(); }
    catch (e) { err.textContent = errText(e); btn.disabled = false; }
  }

  function render() {
    const key = PLAN_STEPS[w.step];
    const nav = el('div', { class: 'btn-row mk-wiz-nav' });
    const back = el('button', { class: 'btn', type: 'button', disabled: w.step === 0 || w.step >= 4,
      onclick: () => { w.step -= 1; err.textContent = ''; render(); } }, pl('back'));
    const next = (label, fn) => el('button', { class: 'btn btn-primary', type: 'button', onclick: fn }, label);
    const forward = () => {
      const e = planStepError(key, w);
      if (e) { err.textContent = pl(`e_${e}`); return; }
      err.textContent = ''; w.step += 1; render();
    };
    if (key === 'instructions') nav.append(back, next(pl('prepare'), e => { if (!planStepError(key, w)) prepare(e.target); else forward(); }));
    else if (key === 'proposal') {
      const cd = w.result.content.display_status;
      nav.append(back, cd === 'could_not_prepare' ? next(pl('retry'), e => retry(e.target))
        : cd === 'ready_for_review' ? next(pl('toReview'), () => { w.step += 1; render(); }) : next(pl('done'), done));
    } else if (key === 'review') nav.append(back, el('button', { class: 'btn', type: 'button', onclick: done }, pl('done')));
    else nav.append(back, next(pl('next'), forward));
    clear(body).append(
      el('ol', { class: 'mk-steps' }, ...PLAN_STEPS.map((k, i) => el('li', { class: i === w.step ? 'active' : i < w.step ? 'done' : '',
        'aria-current': i === w.step ? 'step' : undefined }, `${i + 1}. ${pl(`st_${k}`)}`))),
      el('h3', {}, `${pl('step', w.step + 1, PLAN_STEPS.length)} · ${pl(`st_${key}`)}`), views[key](), err, nav);
  }
  render();
}
