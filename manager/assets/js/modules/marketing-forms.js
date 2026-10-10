// Formularios del módulo AITA Marketing: contenido, detalle con aprobación,
// campañas y Brand Kit. Todo se guarda a través del backend, que valida rol,
// tenant y reglas de estado.
import { el, clear, openModal, field, input, select, toast, badge, fmtDateTime, loading, errorBox, tz } from '../ui.js';
import { api } from '../api.js';
import { tr } from '../i18n.js';
import { t, errText } from './marketing-i18n.js';
import { FORMATS, CHANNELS, LANGUAGES, EDITABLE, brandSwatches, localInput } from './marketing-state.js';

const M = api.marketing;

function saveButton(label, onSave, cls = 'btn-primary') {
  return (close) => el('button', {
    class: `btn ${cls}`, type: 'button', onclick: async (e) => {
      e.target.disabled = true;
      try { await onSave(close); } finally { e.target.disabled = false; }
    },
  }, label);
}
const textarea = (value, max, rows = 3) => el('textarea', { class: 'input', maxlength: String(max), rows: String(rows) }, value || '');
const lines = (v) => String(v || '').split('\n').map(s => s.trim()).filter(Boolean);
function checks(options, selected, labelOf) {
  const box = el('div', { class: 'mk-checks' }, options.map(o => el('label', { class: 'mk-check' },
    el('input', { type: 'checkbox', value: o, checked: (selected || []).includes(o) }), el('span', {}, labelOf(o)))));
  box.values = () => [...box.querySelectorAll('input:checked')].map(i => i.value);
  return box;
}
export const channelLabel = (c) => ({ x: 'X', google_business: 'Google Business', tiktok: 'TikTok', linkedin: 'LinkedIn', youtube: 'YouTube' }[c]
  || c.charAt(0).toUpperCase() + c.slice(1));
export const statusBadge = (s) => el('span', { class: `badge mk-s-${s}` }, t(`s_${s}`));

// ---------- crear / editar contenido ----------
export function contentModal(ctx, { existing = null, campaigns = [], defaults = {} }, reload) {
  const x = existing || defaults;
  const title = input({ maxlength: '160', value: x.title || '', autocomplete: 'off' });
  const objective = input({ maxlength: '300', value: x.objective || '' });
  const topic = input({ maxlength: '300', value: x.topic || '' });
  const format = select(FORMATS.map(f => ({ value: f, label: t(`f_${f}`), selected: f === (x.format || 'image') })));
  const language = select(LANGUAGES.map(l => ({ value: l, label: t(`l_${l}`), selected: l === (x.language || ctx.profile.language || 'en') })));
  const campaign = select([{ value: '', label: t('noCampaign') },
    ...campaigns.filter(c => c.status !== 'archived' || c.id === x.campaign_id)
      .map(c => ({ value: c.id, label: c.name, selected: c.id === x.campaign_id }))]);
  const channels = checks(CHANNELS, x.channels, channelLabel);
  const caption = textarea(x.caption, 5000, 4);
  const script = textarea(x.script, 10000, 4);
  const cta = input({ maxlength: '200', value: x.cta || '' });
  const hashtags = input({ maxlength: '1000', value: (x.hashtags || []).join(' ') });
  const planned = input({ type: 'datetime-local', value: localInput(x.planned_at, tz()) });
  const notes = textarea(x.notes, 2000, 2);
  const err = el('p', { class: 'form-error full', role: 'alert' });

  openModal({
    closeLabel: t('close'),
    title: existing ? t('editContent') : t('newContent'),
    body: el('form', { class: 'form-grid', onsubmit: (e) => e.preventDefault() },
      field(`${t('titleF')} *`, title, { full: true }),
      field(t('objective'), objective), field(t('topic'), topic),
      field(`${t('format')} *`, format), field(t('language'), language),
      field(t('campaign'), campaign), field(t('planned'), planned),
      field(t('channels'), channels, { full: true }),
      field(t('caption'), caption, { full: true }), field(t('script'), script, { full: true }),
      field(t('cta'), cta), field(t('hashtags'), hashtags, { hint: t('hashtagsHint') }),
      field(t('notes'), notes, { full: true }), err),
    actions: [saveButton(t('save'), async (close) => {
      err.textContent = '';
      if (!title.value.trim()) { err.textContent = t('required'); return; }
      const values = {
        title: title.value.trim(), objective: objective.value.trim() || null, topic: topic.value.trim() || null,
        format: format.value, language: language.value, campaign_id: campaign.value || null, channels: channels.values(),
        caption: caption.value.trim() || null, script: script.value.trim() || null, cta: cta.value.trim() || null,
        hashtags: hashtags.value, planned_at: planned.value || null, notes: notes.value.trim() || null,
      };
      try {
        if (existing) await M.updateContent(ctx.tenantId, existing.id, values); else await M.createContent(ctx.tenantId, values);
      } catch (ex) { err.textContent = errText(ex); return; }
      close(); toast(t('saved')); reload();
    })],
  });
}

// ---------- detalle: estado, aprobación, historial y cola ----------
export async function contentDetail(ctx, id, { campaigns = [], reload }) {
  const body = el('div', { class: 'mk-detail' }, loading());
  const close = openModal({ closeLabel: t('close'), title: t('content'), body });
  let data;
  try { data = await M.content(ctx.tenantId, id); } catch (e) { clear(body).appendChild(errorBox({ message: errText(e) })); return; }
  const c = data.content;
  const camp = campaigns.find(x => x.id === c.campaign_id);
  const row = (label, value) => value ? el('div', { class: 'mk-row' }, el('span', { class: 'mk-k' }, label), el('span', {}, value)) : null;
  const comment = textarea('', 2000, 2);
  const when = input({ type: 'datetime-local', value: localInput(c.planned_at, tz()) });
  const err = el('p', { class: 'form-error', role: 'alert' });

  const act = async (to) => {
    err.textContent = '';
    if (to === 'rejected' && !comment.value.trim()) { err.textContent = t('commentReq'); return; }
    try {
      await M.transition(ctx.tenantId, c.id, to, { comment: comment.value.trim(), scheduledAt: to === 'scheduled' ? when.value : null });
    } catch (ex) { err.textContent = errText(ex); return; }
    toast(t('saved')); close(); reload();
  };
  const label = (to) => (c.status === 'scheduled' && to === 'approved' ? t('a_unschedule')
    : c.status === 'archived' && to === 'draft' ? t('a_restore') : t(`a_${to}`));
  const buttons = data.allowed.map(to => el('button', {
    type: 'button', onclick: () => act(to),
    class: `btn btn-sm${to === 'approved' && c.status !== 'scheduled' ? ' btn-primary' : ''}${to === 'rejected' ? ' btn-danger' : ''}${to === 'scheduled' ? ' btn-accent' : ''}`,
  }, label(to)));

  clear(body).appendChild(el('div', { class: 'mk-detail' },
    el('div', { class: 'mk-detail-head' }, el('h3', {}, c.title), statusBadge(c.status),
      EDITABLE.includes(c.status)
        ? el('button', { class: 'btn btn-sm', type: 'button', onclick: () => { close(); contentModal(ctx, { existing: c, campaigns }, reload); } }, t('edit'))
        : null),
    el('div', { class: 'mk-rows' },
      row(t('format'), t(`f_${c.format}`)), row(t('channels'), (c.channels || []).map(channelLabel).join(', ')),
      row(t('language'), t(`l_${c.language}`)), row(t('campaign'), camp ? camp.name : null),
      row(t('planned'), c.planned_at ? fmtDateTime(c.planned_at) : null),
      row(t('k_scheduled'), c.scheduled_at ? fmtDateTime(c.scheduled_at) : null),
      row(t('objective'), c.objective), row(t('topic'), c.topic),
      row(t('caption'), c.caption), row(t('script'), c.script), row(t('cta'), c.cta),
      row(t('hashtags'), (c.hashtags || []).join(' ')), row(t('notes'), c.notes)),
    data.allowed.length ? el('div', { class: 'mk-actions' },
      field(t('comment'), comment, { full: true }),
      data.allowed.includes('scheduled') ? field(t('when'), when) : null,
      el('div', { class: 'btn-row' }, buttons), err) : null,
    el('h4', {}, t('history')),
    el('ol', { class: 'mk-history' }, data.history.map(h => el('li', {},
      el('strong', {}, t(`ev_${h.action}`)), ' · ', fmtDateTime(h.created_at), ' · ',
      h.actor_id === ctx.user.id ? t('you') : tr(`role_${h.actor_role}`),
      h.from_status ? el('span', { class: 'muted' }, ` (${t(`s_${h.from_status}`)} → ${t(`s_${h.to_status}`)})`) : null,
      h.comment ? el('div', { class: 'mk-comment' }, `“${h.comment}”`) : null))),
    data.publications.length ? el('div', {}, el('h4', {}, t('publications')),
      el('ul', { class: 'mk-history' }, data.publications.map(p => el('li', {},
        channelLabel(p.channel), ' · ', badge(p.status, t(`p_${p.status}`)), p.scheduled_at ? ` · ${fmtDateTime(p.scheduled_at)}` : '')))) : null));
}

// ---------- campañas ----------
export function campaignModal(ctx, existing, reload) {
  const x = existing || {};
  const name = input({ maxlength: '120', value: x.name || '', autocomplete: 'off' });
  const objective = input({ maxlength: '300', value: x.objective || '' });
  const description = textarea(x.description, 2000);
  const start = input({ type: 'date', value: x.start_date || '' });
  const end = input({ type: 'date', value: x.end_date || '' });
  const budget = input({ type: 'number', min: '0', step: '0.01', inputmode: 'decimal', value: x.budget ?? '' });
  const status = select(['planned', 'active', 'paused', 'completed', 'archived']
    .map(s => ({ value: s, label: t(`c_${s}`), selected: s === (x.status || 'planned') })));
  const err = el('p', { class: 'form-error full', role: 'alert' });
  openModal({
    closeLabel: t('close'),
    title: existing ? t('editCampaign') : t('newCampaign'),
    body: el('form', { class: 'form-grid', onsubmit: (e) => e.preventDefault() },
      field(`${t('name')} *`, name, { full: true }), field(t('objective'), objective, { full: true }),
      field(t('description'), description, { full: true }),
      field(t('start'), start), field(t('end'), end), field(t('budget'), budget), field(t('status'), status), err),
    actions: [saveButton(t('save'), async (close) => {
      err.textContent = '';
      if (!name.value.trim()) { err.textContent = t('required'); return; }
      const values = { name: name.value.trim(), objective: objective.value.trim() || null, description: description.value.trim() || null,
        start_date: start.value || null, end_date: end.value || null, budget: budget.value === '' ? null : budget.value, status: status.value };
      try {
        if (existing) await M.updateCampaign(ctx.tenantId, existing.id, values); else await M.createCampaign(ctx.tenantId, values);
      } catch (ex) { err.textContent = errText(ex); return; }
      close(); toast(t('saved')); reload();
    })],
  });
}

// ---------- Brand Kit ----------
export function brandForm(root, ctx, data, isCurrent) {
  const p = data.profile || {};
  const f = {
    business_name: input({ maxlength: '120', value: p.business_name || '' }),
    description: textarea(p.description, 2000),
    target_audience: textarea(p.target_audience, 1000, 2),
    tone: input({ maxlength: '500', value: p.tone || '' }),
    languages: checks(LANGUAGES, p.languages, l => t(`l_${l}`)),
    priority_services: textarea((p.priority_services || []).join('\n'), 3000),
    cta: input({ maxlength: '200', value: p.cta || '' }),
    phone: input({ maxlength: '25', type: 'tel', value: p.phone || '' }),
    website: input({ maxlength: '300', type: 'url', placeholder: 'https://', value: p.website || '' }),
    logo_url: input({ maxlength: '500', placeholder: '/media/logo.png', value: p.logo_url || '' }),
    banned_topics: textarea((p.banned_topics || []).join('\n'), 5000),
    compliance_notes: textarea(p.compliance_notes, 2000),
    ai_instructions: textarea(p.ai_instructions, 4000, 4),
  };
  const colors = brandSwatches(p).map(s => {
    const picker = el('input', { type: 'color', class: 'mk-color', value: s.value || '#FFFFFF' });
    const text = input({ maxlength: '7', placeholder: '#RRGGBB', value: s.value || '' });
    picker.addEventListener('input', () => { text.value = picker.value.toUpperCase(); });
    text.addEventListener('input', () => { if (/^#[0-9A-Fa-f]{6}$/.test(text.value)) picker.value = text.value; });
    return { key: s.key, node: el('div', { class: 'mk-swatch' }, picker, text), text };
  });
  const logoPreview = data.tenant_logo_url
    ? el('div', { class: 'mk-logo' }, el('img', { src: data.tenant_logo_url, alt: '' }), el('span', { class: 'hint' }, t('tenantLogo')))
    : null;
  const err = el('p', { class: 'form-error full', role: 'alert' });
  const save = el('button', {
    class: 'btn btn-primary', type: 'button', onclick: async () => {
      err.textContent = ''; save.disabled = true;
      const values = {
        business_name: f.business_name.value.trim() || null, description: f.description.value.trim() || null,
        target_audience: f.target_audience.value.trim() || null, tone: f.tone.value.trim() || null,
        languages: f.languages.values(), priority_services: lines(f.priority_services.value),
        cta: f.cta.value.trim() || null, phone: f.phone.value.trim() || null, website: f.website.value.trim() || null,
        logo_url: f.logo_url.value.trim() || null, banned_topics: lines(f.banned_topics.value),
        compliance_notes: f.compliance_notes.value.trim() || null, ai_instructions: f.ai_instructions.value.trim() || null,
      };
      for (const c of colors) values[c.key] = c.text.value.trim() || null;
      try {
        await api.marketing.saveBrand(ctx.tenantId, values);
        if (isCurrent()) { toast(t('saved')); hint.remove(); }
      } catch (ex) { err.textContent = errText(ex); } finally { save.disabled = false; }
    },
  }, t('save'));
  const hint = data.inherited ? el('p', { class: 'hint mk-inherited' }, t('brandInherited')) : el('span');
  const labels = { color_primary: t('primary'), color_accent: t('accent'), color_secondary: t('secondary') };
  root.appendChild(el('section', { class: 'card' }, el('div', { class: 'card-body mk-brand' },
    el('p', { class: 'hint' }, t('brandIntro')), hint,
    el('form', { class: 'form-grid', onsubmit: (e) => e.preventDefault() },
      field(t('businessName'), f.business_name), field(t('tone'), f.tone),
      field(t('description'), f.description, { full: true }), field(t('audience'), f.target_audience, { full: true }),
      field(t('languages'), f.languages), field(t('cta'), f.cta),
      field(t('services'), f.priority_services, { hint: t('servicesHint') }),
      field(t('banned'), f.banned_topics, { hint: t('bannedHint') }),
      field(t('phone'), f.phone), field(t('website'), f.website),
      ...colors.map(c => field(`${t('colors')} · ${labels[c.key]}`, c.node)),
      field(t('logo'), f.logo_url, { hint: t('logoHint') }), logoPreview ? el('div', { class: 'field' }, logoPreview) : el('span'),
      field(t('compliance'), f.compliance_notes, { full: true }), field(t('aiNotes'), f.ai_instructions, { full: true }),
      err, el('div', { class: 'btn-row full' }, save)))));
}
