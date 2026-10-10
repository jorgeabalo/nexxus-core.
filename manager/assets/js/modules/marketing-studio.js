// AITA Marketing (Fase 2) — Estudio de Reels (asistente de 7 pasos) y Trabajos de generación.
// La mezcla real/IA la elige y confirma owner/manager; la IA solo sugiere. Nada se genera sin
// aprobación explícita y nada se publica desde aquí (se envía a la aprobación de contenido).
import { el, clear, card, table, openModal, field, input, select, toast, badge, fmtDateTime, errorBox, kpi } from '../ui.js';
import { api } from '../api.js';
import { s, sErr } from './marketing-studio-i18n.js';
import { MIX_PRESETS, MIX_STEP, OBJECTIVES, AUDIENCES, STYLES, DURATIONS, TARGETS, QUALITY, WIZARD_STEPS,
  normalizeMix, approxScenes, usableAsSource, reelStage, fmtCost, newIdempotencyKey } from './marketing-studio-state.js';
import { privacyBadge } from './marketing-library.js';

const M = api.marketing;
const opts = (list, prefix, cur) => list.map(v => ({ value: String(v), label: prefix ? s(`${prefix}${v}`) : String(v), selected: String(v) === String(cur) }));
const jobBadge = (st) => badge(st, s(`js_${st}`));

// ------------------------------------------------------------------ asistente
export async function studioView(ctx, goJobs) {
  const [ov, lib] = await Promise.all([M.studio(ctx.tenantId), M.library(ctx.tenantId)]);
  const usable = lib.items.filter(usableAsSource);
  const w = { step: 0, objective: 'new_members', audience: 'general', audience_notes: '', mixReal: 50, scenes: 6,
    adapt_real: false, media: new Set(), style: 'energetic', duration: 30, subtitles: true, targets: new Set(['instagram']),
    cover: 0, hook: '', body: '', cta: '', quality: 'draft', maxCost: '1.00', key: newIdempotencyKey() };
  const root = el('div', { class: 'mk-studio' });
  const notice = el('div', {},
    el('p', {}, s('studioIntro')),
    ov.providers.omniroute_enabled ? null : el('p', { class: 'mk-note' }, s('providersOff')),
    ov.limits.ai_generation_enabled ? null : el('p', { class: 'error-box mk-notice' }, s('limitsClosed')));
  const stepper = el('ol', { class: 'mk-steps' });
  const pane = el('div', { class: 'card-body' });
  const nav = el('div', { class: 'btn-row mk-wiz-nav' });
  root.append(card(s('tab_studio'), el('div', { class: 'card-body' }, notice, stepper)), el('section', { class: 'card' }, pane, nav));

  const mixSummary = el('p', { class: 'hint', 'aria-live': 'polite' });
  const refreshMix = () => {
    const a = approxScenes(w.mixReal, w.scenes, w.duration, w.adapt_real);
    mixSummary.textContent = s('mixExplain', a.realPercent, a.aiPercent, a.realScenes, a.aiScenes, a.realSeconds, a.aiSeconds);
  };
  const views = {
    objective: () => field(s('step_objective'), select(opts(OBJECTIVES, 'ob_', w.objective), { onchange: e => { w.objective = e.target.value; } })),
    audience: () => el('div', { class: 'form-grid' },
      field(s('step_audience'), select(opts(AUDIENCES, 'au_', w.audience), { onchange: e => { w.audience = e.target.value; } })),
      field(s('audienceNotes'), input({ maxlength: '300', value: w.audience_notes, oninput: e => { w.audience_notes = e.target.value; } }))),
    sources: () => {
      const slider = input({ type: 'range', min: '0', max: '100', step: String(MIX_STEP), value: String(w.mixReal),
        'aria-label': s('mix'), oninput: e => { w.mixReal = normalizeMix(e.target.value).real; num.value = String(w.mixReal); refreshMix(); } });
      const num = input({ type: 'number', min: '0', max: '100', step: String(MIX_STEP), value: String(w.mixReal), class: 'input mk-num',
        'aria-label': `${s('real')} %`, onchange: e => { w.mixReal = normalizeMix(e.target.value).real; slider.value = num.value = String(w.mixReal); refreshMix(); } });
      const presets = el('div', { class: 'btn-row' }, MIX_PRESETS.map(([r, a]) => el('button', { class: 'btn btn-sm', type: 'button',
        onclick: () => { w.mixReal = r; slider.value = num.value = String(r); refreshMix(); } }, `${r}/${a}`)));
      const sug = ov.suggestion;
      const list = usable.length ? el('div', { class: 'mk-checks' }, usable.map(m => el('label', { class: 'mk-check' },
        el('input', { type: 'checkbox', checked: w.media.has(m.id), onchange: e => { e.target.checked ? w.media.add(m.id) : w.media.delete(m.id); } }),
        el('span', {}, m.original_filename || m.id.slice(0, 8), ' '), privacyBadge(m.privacy_class))))
        : el('p', { class: 'hint' }, s('noUsable'));
      refreshMix();
      return el('div', {},
        el('h3', {}, s('mix')), presets,
        el('div', { class: 'mk-mix' }, el('span', {}, s('real')), slider, el('span', {}, s('ai')), num),
        mixSummary,
        el('p', { class: 'hint' }, s('suggestion', sug.real_media_percent, sug.ai_media_percent), ' ',
          el('button', { class: 'btn btn-sm', type: 'button', onclick: () => { w.mixReal = sug.real_media_percent; render(); } }, s('applySuggestion'))),
        field(s('scenes'), select(opts([1, 2, 3, 4, 5, 6, 8, 10, 12], '', w.scenes), { onchange: e => { w.scenes = Number(e.target.value); refreshMix(); } })),
        el('label', { class: 'mk-check' }, el('input', { type: 'checkbox', checked: w.adapt_real, onchange: e => { w.adapt_real = e.target.checked; refreshMix(); } }),
          el('span', {}, s('adaptReal'))),
        el('h3', {}, s('pickSources')), list);
    },
    style: () => el('div', { class: 'form-grid' },
      field(s('step_style'), select(opts(STYLES, 'st_', w.style), { onchange: e => { w.style = e.target.value; } })),
      field(s('duration'), select(DURATIONS.map(d => ({ value: String(d), label: `${d} ${s('seconds')}`, selected: d === w.duration })),
        { onchange: e => { w.duration = Number(e.target.value); } })),
      field(s('cover'), select(opts(Array.from({ length: w.scenes }, (_, i) => i), '', w.cover), { onchange: e => { w.cover = Number(e.target.value); } })),
      el('label', { class: 'mk-check' }, el('input', { type: 'checkbox', checked: w.subtitles, onchange: e => { w.subtitles = e.target.checked; } }), el('span', {}, s('subtitles'))),
      el('p', { class: 'hint full' }, s('format916')),
      field(s('targets'), el('div', { class: 'mk-checks' }, TARGETS.map(tg => el('label', { class: 'mk-check' },
        el('input', { type: 'checkbox', checked: w.targets.has(tg), onchange: e => { e.target.checked ? w.targets.add(tg) : w.targets.delete(tg); } }),
        el('span', {}, tg.replace('_', ' '))))), { full: true })),
    script: () => el('div', { class: 'form-grid' },
      field(s('hook'), input({ maxlength: '300', value: w.hook, oninput: e => { w.hook = e.target.value; } }), { full: true }),
      field(s('body'), input({ maxlength: '300', value: w.body, oninput: e => { w.body = e.target.value; } }), { full: true }),
      field(s('ctaS'), input({ maxlength: '300', value: w.cta, oninput: e => { w.cta = e.target.value; } }), { full: true }),
      el('p', { class: 'hint full' }, s('scriptRules'))),
    cost: () => el('div', { class: 'form-grid' },
      field(s('quality'), select(opts(QUALITY, 'q_', w.quality), { onchange: e => { w.quality = e.target.value; } })),
      field(s('maxCost'), input({ type: 'number', min: '0', max: '10000', step: '0.01', value: w.maxCost, oninput: e => { w.maxCost = e.target.value; } })),
      el('p', { class: 'hint full' }, `${s('catalogV')}: ${ov.catalog.catalog_version} · ${ov.catalog.price_source}`)),
    review: () => {
      const a = approxScenes(w.mixReal, w.scenes, w.duration, w.adapt_real);
      return el('dl', { class: 'mk-summary' },
        ...[[s('step_objective'), s(`ob_${w.objective}`)], [s('step_audience'), s(`au_${w.audience}`)],
          [s('mix'), `${a.realPercent}% / ${a.aiPercent}%`], [s('scenes'), `${w.scenes} · ${w.duration}${s('seconds')}`],
          [s('step_sources'), String(w.media.size)], [s('step_style'), s(`st_${w.style}`)], [s('quality'), s(`q_${w.quality}`)],
          [s('maxCost'), w.maxCost]].flatMap(([k, v]) => [el('dt', {}, k), el('dd', {}, v)]));
    },
  };
  const err = el('p', { class: 'form-error', role: 'alert' });
  async function create(btn) {
    btn.disabled = true; err.textContent = '';
    const mix = normalizeMix(w.mixReal);
    try {
      const job = await M.createJob(ctx.tenantId, {
        brief: { objective: w.objective, audience: w.audience, audience_notes: w.audience_notes, style: w.style,
          duration_seconds: w.duration, subtitles: w.subtitles, targets: [...w.targets], cover_scene: w.cover,
          script: { hook: w.hook, body: w.body, cta: w.cta }, language: ctx.profile?.language === 'en' ? 'en' : 'es' },
        real_media_percent: mix.real, ai_media_percent: mix.ai, scenes: w.scenes, adapt_real: w.adapt_real,
        media_ids: [...w.media], quality_tier: w.quality, maximum_cost: Number(w.maxCost), idempotency_key: w.key });
      toast(s('created'));
      goJobs(job.id);
    } catch (e) { err.textContent = sErr(e); } finally { btn.disabled = false; }
  }
  function render() {
    clear(stepper).append(...WIZARD_STEPS.map((k, i) => el('li', { class: i === w.step ? 'active' : i < w.step ? 'done' : '',
      'aria-current': i === w.step ? 'step' : null }, `${i + 1}. ${s(`step_${k}`)}`)));
    clear(pane).append(el('h2', {}, s(`step_${WIZARD_STEPS[w.step]}`)), views[WIZARD_STEPS[w.step]](), err);
    const last = w.step === WIZARD_STEPS.length - 1;
    clear(nav).append(
      el('button', { class: 'btn', type: 'button', disabled: w.step === 0, onclick: () => { w.step--; render(); } }, s('back')),
      last ? el('button', { class: 'btn btn-primary', type: 'button', disabled: !lib.enabled, onclick: e => create(e.target) }, s('create'))
        : el('button', { class: 'btn btn-primary', type: 'button', onclick: () => { w.step++; render(); } }, s('next')));
  }
  render();
  return root;
}

// ------------------------------------------------------------------ trabajos
function jobDetail(ctx, id, reload) {
  const holder = el('div', {}, el('div', { class: 'spinner spinner-inline' }));
  openModal({ title: s('tab_jobs'), closeLabel: s('close'), body: holder });
  const act = (action, extra) => async (e) => {
    e.target.disabled = true;
    try { await M.jobAction(ctx.tenantId, id, action, extra ? extra() : {}); await draw(); reload(); }
    catch (x) { toast(sErr(x), 'error'); } finally { e.target.disabled = false; }
  };
  async function draw() {
    let j;
    try { j = await M.job(ctx.tenantId, id); } catch (e) { clear(holder).appendChild(errorBox({ message: sErr(e) })); return; }
    const est = (j.request_metadata || {}).estimate;
    const confirm = el('input', { type: 'checkbox', id: `mk-ok-${id}` });
    const title = input({ maxlength: '160', placeholder: s('reelTitle') });
    const scenes = j.outputs.filter(o => o.kind === 'scene');
    const by = {};
    for (const o of scenes) { by[o.origin] = by[o.origin] || { n: 0, ms: 0 }; by[o.origin].n++; by[o.origin].ms += o.duration_ms || 0; }
    clear(holder).append(
      el('p', {}, jobBadge(j.status), ' ', el('strong', {}, `${j.real_media_percent}% ${s('real')} / ${j.ai_media_percent}% ${s('ai')}`),
        ` · ${s(`q_${j.quality_tier}`)} · ${s('maxCost')}: ${fmtCost(j.maximum_cost, j.currency)}`),
      j.error_code ? el('p', { class: 'error-box' }, sErr({ code: j.error_code })) : null,
      est ? el('div', {}, el('p', {}, `${s('estimated')}: ${fmtCost(est.estimated_cost, est.currency)} · ${s('catalogV')} ${est.catalog_version}`),
        table([{ label: s('subtasks'), key: 'task_type' }, { label: s('provider'), render: r => `${r.provider} (${r.external ? s('external') : s('local')})` },
          { label: s('privacy'), render: r => privacyBadge(r.privacy_class) }, { label: s('cost'), num: true, render: r => fmtCost(r.estimated_cost, est.currency) }],
        est.subtasks)) : null,
      scenes.length ? el('div', {}, el('h3', {}, s('byOrigin')), el('ul', {}, Object.entries(by).map(([k, v]) =>
        el('li', {}, `${s(`or_${k}`)}: ${v.n} · ${Math.round(v.ms / 1000)}${s('seconds')}`)))) : null,
      el('div', { class: 'btn-row mk-job-actions' },
        j.status === 'draft' ? el('button', { class: 'btn btn-primary', type: 'button', onclick: act('estimate') }, s('estimate')) : null,
        j.status === 'awaiting_generation_approval' ? el('label', { class: 'mk-check', for: confirm.id }, confirm, el('span', {}, s('approveConfirm'))) : null,
        j.status === 'awaiting_generation_approval' ? el('button', { class: 'btn btn-primary', type: 'button', onclick: act('approve', () => ({ confirm: confirm.checked })) }, s('approve')) : null,
        j.status === 'awaiting_generation_approval' ? el('button', { class: 'btn', type: 'button', onclick: act('reopen') }, s('reopen')) : null,
        j.status === 'queued' ? el('button', { class: 'btn btn-primary', type: 'button', onclick: act('process') }, s('process')) : null,
        ['draft', 'awaiting_generation_approval', 'queued', 'processing'].includes(j.status)
          ? el('button', { class: 'btn', type: 'button', onclick: act('cancel') }, s('cancel')) : null,
        j.status === 'succeeded' && !j.content_id ? el('span', { class: 'btn-row' }, title,
          el('button', { class: 'btn btn-accent', type: 'button', onclick: act('send-to-approval', () => ({ title: title.value })) }, s('sendToApproval'))) : null),
      el('h3', {}, s('events')),
      el('ol', { class: 'mk-events' }, j.events.map(e => el('li', {}, `${fmtDateTime(e.created_at)} · ${s(`js_${e.to_status}`)}`))));
  }
  draw();
}

export async function jobsView(ctx, reload, openId) {
  const [list, ov] = await Promise.all([M.jobs(ctx.tenantId), M.studio(ctx.tenantId)]);
  const u = ov.usage, l = ov.limits;
  const lim = (v) => (v === null || v === undefined ? '∞' : String(v));
  if (openId) setTimeout(() => jobDetail(ctx, openId, reload), 0);
  return el('div', {},
    el('div', { class: 'kpis mk-kpis' },
      kpi(s('l_jobs'), `${u.jobs} / ${lim(l.monthly_generation_job_limit)}`),
      kpi(s('l_regenerations'), `${u.regenerations} / ${lim(l.monthly_regeneration_limit)}`),
      kpi(s('l_images'), `${u.images} / ${lim(l.monthly_generated_image_limit)}`),
      kpi(s('l_video_seconds'), `${u.video_seconds} / ${lim(l.monthly_generated_video_seconds_limit)}`),
      kpi(s('l_cost'), `${fmtCost(u.cost)} / ${lim(l.monthly_ai_cost_limit)}`)),
    card(s('tab_jobs'), table([
      { label: s('created_at'), render: j => fmtDateTime(j.created_at) },
      { label: s('status'), render: j => jobBadge(j.status) },
      { label: s('stage'), render: j => el('span', { class: 'badge' }, s(`rs_${reelStage(j, j.content_status ? { status: j.content_status } : null)}`)) },
      { label: s('mixCol'), render: j => `${j.real_media_percent} / ${j.ai_media_percent}` },
      { label: s('quality'), render: j => s(`q_${j.quality_tier}`) },
      { label: s('cost'), num: true, render: j => fmtCost(j.actual_cost ?? j.estimated_cost, j.currency) },
    ], list.items, { onRowClick: j => jobDetail(ctx, j.id, reload), emptyText: s('noJobs') })),
    card(s('admin'), table([
      { label: s('provider'), key: 'provider' }, { label: s('model'), key: 'model_id' },
      { label: s('status'), render: m => (m.enabled ? s('enabled') : s('disabled')) + (m.price_verified ? '' : ` · ${s('priceUnverified')}`) },
      { label: s('cost'), render: m => (m.estimated_cost === null ? '—' : `${m.estimated_cost} / ${m.billing_unit}`) },
    ], ov.catalog.models)));
}
