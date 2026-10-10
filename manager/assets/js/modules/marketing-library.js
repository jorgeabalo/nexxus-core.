// AITA Marketing (Fase 2) — Biblioteca multimedia privada.
// Subir, ver (URL firmada de corta duración → blob en memoria), clasificar personas,
// crear versiones anonimizadas (siempre con revisión humana) y archivar. Nunca se borra nada.
import { el, clear, card, table, openModal, field, select, toast, badge, fmtDateTime, errorBox } from '../ui.js';
import { api } from '../api.js';
import { s, sErr } from './marketing-studio-i18n.js';
import { ACCEPT, PEOPLE_POLICIES, ANON_METHODS, privacyClass, fmtBytes } from './marketing-studio-state.js';

const M = api.marketing;

export function warnings() {
  return el('ul', { class: 'mk-warn' }, ['w_pixel', 'w_identify', 'w_business'].map(k => el('li', {}, s(k))));
}
export const privacyBadge = (cls) => el('span', { class: `badge mk-pc-${cls}` }, s(`pc_${cls}`));
const peopleText = (v) => (v === true ? s('yes') : v === false ? s('no') : s('unknown'));

async function showPreview(ctx, m, derivativeId) {
  const holder = el('div', { class: 'mk-preview' }, el('div', { class: 'spinner spinner-inline' }));
  let objectUrl = null;
  const close = openModal({ title: s('preview'), closeLabel: s('close'), body: holder });
  try {
    const blob = await M.previewBlob(ctx.tenantId, m.id, derivativeId);
    objectUrl = URL.createObjectURL(blob);
    const media = (derivativeId ? 'image' : m.media_type) === 'video'
      ? el('video', { src: objectUrl, controls: true, playsinline: true, preload: 'metadata' })
      : el('img', { src: objectUrl, alt: m.original_filename || '' });
    clear(holder).appendChild(media);
  } catch (e) {
    clear(holder).appendChild(errorBox({ message: sErr(e) }));
  }
  // El blob se libera al cerrar: no queda nada guardado en el navegador.
  const root = document.getElementById('modal-root');
  const obs = new MutationObserver(() => { if (!root.contains(holder)) { if (objectUrl) URL.revokeObjectURL(objectUrl); obs.disconnect(); } });
  obs.observe(root, { childList: true, subtree: true });
  return close;
}

function classifyModal(ctx, m, reload) {
  const people = select([{ value: '', label: s('unknown') }, { value: 'yes', label: s('yes') }, { value: 'no', label: s('no') }]
    .map(o => ({ ...o, selected: o.value === (m.contains_people === true ? 'yes' : m.contains_people === false ? 'no' : '') })));
  const triVal = (v) => (v === true ? 'yes' : v === false ? 'no' : '');
  const minors = select([{ value: '', label: s('unknown') }, { value: 'yes', label: s('yes') }, { value: 'no', label: s('no') }]
    .map(o => ({ ...o, selected: o.value === triVal(m.contains_minors) })));
  const policy = select(PEOPLE_POLICIES.map(p => ({ value: p, label: s(`pp_${p}`), selected: p === m.people_policy })));
  const consent = select(['unknown', 'not_required', 'pending', 'granted', 'revoked']
    .map(c => ({ value: c, label: s(`cs_${c}`), selected: c === m.consent_status })));
  const note = el('textarea', { class: 'input', maxlength: '300', rows: '2' }, (m.metadata || {}).consent_note || '');
  const err = el('p', { class: 'form-error full', role: 'alert' });
  openModal({
    title: s('classify'), closeLabel: s('close'),
    body: el('form', { class: 'form-grid', onsubmit: (e) => e.preventDefault() },
      field(s('containsPeople'), people), field(s('containsMinors'), minors, { hint: s('minorsHint') }),
      field(s('policy'), policy), field(s('consent'), consent),
      field(s('consentNote'), note, { full: true }), el('div', { class: 'full' }, warnings()), err),
    actions: [(close) => el('button', { class: 'btn btn-primary', type: 'button', onclick: async (e) => {
      e.target.disabled = true;
      try {
        const tri = (v) => (v === 'yes' ? true : v === 'no' ? false : null);
        await M.classify(ctx.tenantId, m.id, { contains_people: tri(people.value), contains_minors: tri(minors.value),
          people_policy: policy.value, consent_status: consent.value, consent_note: note.value });
        close(); toast(s('classify')); reload();
      } catch (x) { err.textContent = sErr(x); } finally { e.target.disabled = false; }
    } }, s('classify'))],
  });
}

function anonymizeModal(ctx, m, reload) {
  const method = select(ANON_METHODS.map(x => ({ value: x, label: s(`am_${x}`) })));
  const err = el('p', { class: 'form-error full', role: 'alert' });
  openModal({
    title: s('anonymize'), closeLabel: s('close'),
    body: el('div', {}, field(s('method'), method), el('p', { class: 'hint' }, s('anonNote')), warnings(), err),
    actions: [(close) => el('button', { class: 'btn btn-primary', type: 'button', onclick: async (e) => {
      e.target.disabled = true;
      try {
        const d = await M.anonymize(ctx.tenantId, m.id, method.value);
        close(); toast(d.metadata?.low_confidence ? s('lowConfidence') : s('anonymize')); reload();
      } catch (x) { err.textContent = sErr(x); } finally { e.target.disabled = false; }
    } }, s('anonymize'))],
  });
}

function deleteModal(ctx, m, reload) {
  const reason = el('textarea', { class: 'input', maxlength: '300', rows: '2' });
  const err = el('p', { class: 'form-error full', role: 'alert' });
  openModal({
    title: s('delTitle'), closeLabel: s('close'),
    body: el('div', {}, el('p', {}, m.original_filename || ''), el('p', { class: 'hint' }, s('delNote')),
      field(s('delReason'), reason), err),
    actions: [(close) => el('button', { class: 'btn btn-danger', type: 'button', onclick: async (e) => {
      e.target.disabled = true;
      try {
        const r = await M.deleteMedia(ctx.tenantId, m.id, reason.value);
        close(); toast(r.storage_removed ? s('deleted') : s('delPending')); reload();
      } catch (x) { err.textContent = sErr(x); } finally { e.target.disabled = false; }
    } }, s('del'))],
  });
}

function derivativeRow(ctx, m, d, reload) {
  const act = async (approve) => {
    try { await M.reviewDerivative(ctx.tenantId, d.id, approve); reload(); } catch (x) { toast(sErr(x), 'error'); }
  };
  const simulated = d.is_mock || d.status === 'mock_only' || d.status === 'awaiting_processing';
  return el('li', { class: `mk-der${simulated ? ' mk-der-mock' : ''}` },
    el('span', {}, `${s(`am_${d.method}`)} · `), badge(d.status, s(`ds_${d.status}`)),
    simulated ? el('span', { class: 'hint' }, ` ${s('mockDerNote')}`) : null,
    d.storage_path && !simulated ? el('button', { class: 'btn btn-sm', type: 'button', onclick: () => showPreview(ctx, m, d.id) }, s('preview')) : null,
    d.status === 'needs_review' && !simulated ? el('span', { class: 'btn-row' },
      el('button', { class: 'btn btn-sm btn-primary', type: 'button', disabled: !d.storage_path, onclick: () => act(true) }, s('approveDer')),
      el('button', { class: 'btn btn-sm', type: 'button', onclick: () => act(false) }, s('rejectDer'))) : null,
    d.status === 'mock_only' ? el('button', { class: 'btn btn-sm', type: 'button', onclick: () => act(false) }, s('rejectDer')) : null);
}

export async function libraryView(ctx, reload) {
  const data = await M.library(ctx.tenantId);
  const fileInput = el('input', { type: 'file', accept: ACCEPT, class: 'sr-only', id: `mk-up-${Date.now()}` });
  const status = el('span', { class: 'hint', 'aria-live': 'polite' });
  fileInput.addEventListener('change', async () => {
    const f = fileInput.files[0];
    if (!f) return;
    status.textContent = s('uploading');
    try {
      const r = await M.upload(ctx.tenantId, f);
      toast(r.duplicate ? s('duplicate') : s('uploaded'));
      reload();
    } catch (e) { status.textContent = sErr(e); } finally { fileInput.value = ''; }
  });
  const st = data.storage;
  // Estados distintos: Biblioteca no habilitada (0) · habilitada (usado/límite) · límite alcanzado · sin límite.
  const quota = {
    disabled: el('p', { class: 'error-box mk-notice', role: 'status', dataset: { state: 'disabled' } },
      el('strong', {}, s('libDisabled')), ' ', s('libDisabledHint')),
    full: el('p', { class: 'error-box mk-notice', role: 'status', dataset: { state: 'full' } },
      el('strong', {}, s('libFull')), ` ${fmtBytes(st.used_bytes)} / ${fmtBytes(st.limit_bytes)}`),
    enabled: el('p', { class: 'hint', dataset: { state: 'enabled' } },
      `${s('storage')}: ${fmtBytes(st.used_bytes)} / ${fmtBytes(st.limit_bytes)}`),
    unlimited: el('p', { class: 'hint', dataset: { state: 'unlimited' } }, `${s('storage')}: ${fmtBytes(st.used_bytes)} · ${s('noLimit')}`),
  }[st.state];
  const head = el('div', { class: 'mk-lib-head' },
    el('p', {}, s('libIntro')),
    quota,
    st.state === 'disabled' ? null : el('p', { class: 'hint' }, `${s('formats')} ${fmtBytes(st.max_upload_bytes)}.`),
    data.generation_enabled ? null : el('p', { class: 'hint', dataset: { state: 'generation-off' } }, `${s('genNotEnabled')}. ${s('libWorksAnyway')}`),
    el('p', { class: 'mk-note' }, s('scanNote')),
    el('div', { class: 'btn-row' },
      el('label', { class: `btn btn-accent${data.can_upload ? '' : ' disabled'}`, for: fileInput.id, role: 'button',
        'aria-disabled': String(!data.can_upload) }, `+ ${s('upload')}`), fileInput, status),
    warnings());
  if (!data.can_upload) fileInput.disabled = true;
  const rows = data.items;
  const tbl = table([
    { label: s('file'), render: m => el('span', { class: 'mk-file' }, m.original_filename || m.id.slice(0, 8)) },
    { label: s('type'), render: m => `${m.media_type} · ${m.mime_type.split('/')[1]}` },
    { label: s('size'), num: true, render: m => fmtBytes(m.byte_size) },
    { label: s('people'), render: m => `${peopleText(m.contains_people)} · ${s('minorsShort')}: ${peopleText(m.contains_minors)}` },
    { label: s('privacy'), render: m => privacyBadge(m.privacy_class || privacyClass(m)) },
    { label: s('status'), render: m => badge(m.processing_status, s(`ms_${m.processing_status}`)) },
    { label: s('scan'), render: m => el('span', { class: 'mk-scan' }, m.validation_status === 'passed' ? `${s('formatOk')} · ` : '',
      s(`scan_${m.malware_scan_status || 'not_scanned'}`)) },
    { label: s('created_at'), render: m => fmtDateTime(m.created_at) },
    { label: s('actions'), render: m => el('div', { class: 'btn-row mk-lib-actions' },
      el('button', { class: 'btn btn-sm', type: 'button', onclick: () => showPreview(ctx, m) }, s('preview')),
      m.processing_status === 'archived' ? null
        : el('button', { class: 'btn btn-sm', type: 'button', onclick: () => classifyModal(ctx, m, reload) }, s('classify')),
      m.people_policy === 'anonymize' && m.processing_status === 'ready'
        ? el('button', { class: 'btn btn-sm', type: 'button', onclick: () => anonymizeModal(ctx, m, reload) }, s('anonymize')) : null,
      m.consent_status === 'granted' ? el('button', { class: 'btn btn-sm', type: 'button', onclick: async () => {
        try { await M.revokeConsent(ctx.tenantId, m.id); toast(s('revoked')); reload(); } catch (x) { toast(sErr(x), 'error'); }
      } }, s('revokeConsent')) : null,
      el('button', { class: 'btn btn-sm', type: 'button', onclick: async () => {
        try { await M.archive(ctx.tenantId, m.id, m.processing_status !== 'archived'); reload(); } catch (x) { toast(sErr(x), 'error'); }
      } }, m.processing_status === 'archived' ? s('unarchive') : s('archive')),
      el('button', { class: 'btn btn-sm btn-danger', type: 'button', onclick: () => deleteModal(ctx, m, reload) }, s('del')),
      (m.derivatives || []).length ? el('ul', { class: 'mk-ders', 'aria-label': s('derivatives') },
        m.derivatives.map(d => derivativeRow(ctx, m, d, reload))) : null) },
  ], rows, { emptyText: s('noMedia') });
  return el('div', {}, card(s('tab_library'), el('div', { class: 'card-body' }, head)), card(s('files'), tbl));
}
