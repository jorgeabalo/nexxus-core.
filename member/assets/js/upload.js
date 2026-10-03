// Cuestionario en papel o PDF: descargar el PDF rellenable, subir el documento
// (PDF, escaneo o fotos), revisar lo que se leyó y confirmar.
// * Nada leído se guarda como dato confirmado hasta que el socio lo revisa.
// * Lo ilegible o dudoso NO se completa solo: el socio escribe la respuesta,
//   o la marca "en blanco" / "ilegible". Un espacio vacío nunca se vuelve "No".
// * El original se ve solo a través del servidor con la sesión del socio.
import { S, el, clear, t, fmtDateTime, card, toast, errText, authFetch, uuid4, openModal, LB_TO_KG, IN_TO_CM } from './util.js';

const put = (node, ...kids) => { node.append(...kids.flat().filter(k => k !== null && k !== undefined && k !== false)); return node; };
const L = (obj) => (obj && typeof obj === 'object') ? (obj[S.lang] || obj.es || obj.en || '') : (obj || '');
const ss = {
  get(k) { try { return sessionStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { sessionStorage.setItem(k, v); } catch { /* */ } },
  del(k) { try { sessionStorage.removeItem(k); } catch { /* */ } },
};
const ACCEPT = 'application/pdf,image/jpeg,image/png,image/webp,image/heic,image/heif,.heic,.heif';
const TONE = { answered: 'ok', blank: '', illegible: 'bad', uncertain: 'warn', pending: 'warn' };
const BODY = [['weight', 'w'], ['height', 'l'], ['waist', 'l'], ['arm', 'l'], ['leg', 'l']];
const BODY_KEY = { weight: 'weight_lb', height: 'height_in', waist: 'waist_in', arm: 'left_arm_in', leg: 'left_leg_in' };

export async function loadDocs() {
  const { data, error } = await S.sb.from('evaluation_documents')
    .select('id, kind, status, files, form_ref_ok, questionnaire_version, extraction, extraction_error, evaluation_id, created_at, confirmed_at')
    .eq('member_id', S.data.member.id).order('created_at', { ascending: false });
  if (error) throw error;
  S.docs = data || [];
  return S.docs;
}

async function apiJson(path, opts) {
  const r = await authFetch(path, opts);
  const body = await r.json().catch(() => ({}));
  if (!r.ok) { const e = new Error(body.error || `HTTP ${r.status}`); e.code = body.error; throw e; }
  return body;
}
const upErr = (e) => (e?.code && t(`up.err.${e.code}`) !== `up.err.${e.code}`) ? t(`up.err.${e.code}`) : errText(e);

// ---------- tarjeta "¿Lo tienes en papel?" ----------
export function uploadCard({ qAvailable }) {
  const input = el('input', { type: 'file', accept: ACCEPT, multiple: true, class: 'sr-only', id: 'up-file' });
  const status = el('p', { class: 'small muted m0', role: 'status' });
  const btn = el('label', { class: 'btn btn-primary btn-block', for: 'up-file' }, t('up.upload'));
  input.addEventListener('change', async () => {
    const files = [...input.files];
    input.value = '';
    if (!files.length) return;
    if (files.length > 10) { toast(t('up.err.too_many_files'), 'error'); return; }
    if (files.some(f => f.size > 15 * 1024 * 1024)) { toast(t('up.err.file_too_large'), 'error'); return; }
    btn.classList.add('disabled'); status.textContent = t('up.uploading');
    const fd = new FormData();
    files.forEach(f => fd.append('files', f, f.name));
    try {
      const doc = await apiJson('/api/member/evaluation-documents', { method: 'POST', body: fd });
      location.hash = `#/review/${doc.id}`;
    } catch (e) {
      status.textContent = ''; btn.classList.remove('disabled');
      toast(upErr(e), 'error');
    }
  });
  // El cuestionario se llena en línea; el PDF rellenable no se ofrece (solo si el gimnasio lo activa).
  const dl = qAvailable && S.data?.tenant?.settings?.evaluation_pdf_form
    ? el('button', { class: 'btn btn-block', type: 'button', onclick: (e) => downloadForm(e.target) }, t('up.downloadForm'))
    : null;
  return el('section', { class: 'card', id: 'upload-card' },
    el('div', { class: 'card-head' }, el('h2', {}, t('up.title'))),
    el('p', { class: 'm0' }, t('up.lead')),
    el('ol', { class: 'steps small' }, el('li', {}, t('up.step1')), el('li', {}, t('up.step2')), el('li', {}, t('up.step3'))),
    dl, input, btn, status,
    el('p', { class: 'small muted m0' }, t('up.privacy')));
}

async function downloadForm(btn) {
  btn.disabled = true;
  try {
    const r = await authFetch(`/api/member/evaluation-form.pdf?lang=${S.lang}`);
    if (!r.ok) { const b = await r.json().catch(() => ({})); const e = new Error(b.error); e.code = b.error; throw e; }
    saveBlob(await r.blob(), 'cuestionario.pdf');
  } catch (e) { toast(upErr(e), 'error'); }
  btn.disabled = false;
}
function saveBlob(blob, name) {
  const url = URL.createObjectURL(blob);
  const a = el('a', { href: url, download: name });
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}

// ---------- documentos en curso (evaluación) ----------
export function pendingDocsCard(docs) {
  const open = docs.filter(d => d.status !== 'confirmed');
  if (!open.length) return null;
  return card(t('up.pendingTitle'), el('div', {}, open.map(d => el('div', { class: 'row doc-row' },
    el('div', { class: 'main' }, el('span', { class: 'strong' }, t(`up.status.${d.status}`)),
      el('span', { class: 'muted small' }, `${fmtDateTime(d.created_at)} · ${t('up.files', { n: (d.files || []).length })}`)),
    el('a', { class: 'btn btn-sm', href: `#/review/${d.id}` }, d.status === 'extracting' ? t('up.view') : t('up.review'))))));
}

// ---------- ver original ----------
export async function openOriginal(docId, f, doc = null) {
  try {
    const r = await authFetch(`/api/member/evaluation-documents/${encodeURIComponent(docId)}/files/${f.n}`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const blob = await r.blob();
    if ((f.mime || '').startsWith('image/') && f.mime !== 'image/heic') {
      const url = URL.createObjectURL(blob);
      const close = openModal(f.name || t('up.original'), el('div', { class: 'doc-view' }, el('img', { src: url, alt: t('up.originalAlt', { n: f.n }) })), [], { cancelLabel: t('close') });
      void close;
      setTimeout(() => URL.revokeObjectURL(url), 120000);
    } else {
      saveBlob(blob, f.name || `documento-${f.n}`);
    }
  } catch (e) { toast(errText(e), 'error'); }
  void doc;
}
export const originalButtons = (doc) => el('div', { class: 'doc-files' }, (doc.files || []).map(f =>
  el('button', { class: 'btn btn-sm', type: 'button', onclick: () => openOriginal(doc.id, f) },
    `${t('up.original')} ${doc.files.length > 1 ? f.n : ''}`.trim())));

// ---------- vista #/review/<id> ----------
export async function viewReview(main, rerender, docId) {
  const box = el('div', { class: 'stack' }, el('section', { class: 'card' }, el('p', { class: 'muted m0' }, t('loading'))));
  main.appendChild(box);
  let doc;
  for (let i = 0; i < 60; i++) {        // hasta ~2 min mientras se lee el documento
    try { doc = await apiJson(`/api/member/evaluation-documents/${encodeURIComponent(docId)}`); }
    catch (e) { clear(box).appendChild(card(t('up.reviewTitle'), el('p', { class: 'notice bad' }, upErr(e)))); return; }
    if (S.route !== 'review' || !document.body.contains(box)) return;
    if (doc.status !== 'extracting') break;
    clear(box).appendChild(card(t('up.reviewTitle'), el('div', { class: 'stack' },
      el('p', { class: 'm0', role: 'status' }, t('up.reading')), el('div', { class: 'spinner', 'aria-hidden': 'true' }),
      originalButtons(doc))));
    await new Promise(r => setTimeout(r, 2000));
  }
  if (doc.status === 'extracting') {
    clear(box).appendChild(card(t('up.reviewTitle'), el('div', { class: 'stack' }, el('p', { class: 'm0' }, t('up.slow')),
      el('button', { class: 'btn', type: 'button', onclick: () => retry(doc, rerender) }, t('up.retry')))));
    return;
  }
  if (doc.status === 'confirmed') {
    clear(box).appendChild(card(t('up.reviewTitle'), el('div', { class: 'stack' }, el('p', { class: 'notice ok' }, t('up.alreadyConfirmed')),
      originalButtons(doc), el('a', { class: 'btn btn-block', href: '#/evaluation' }, t('up.toHistory')))));
    return;
  }
  clear(box).appendChild(reviewForm(doc, rerender));
}

async function retry(doc, rerender) {
  try { await apiJson(`/api/member/evaluation-documents/${encodeURIComponent(doc.id)}/retry`, { method: 'POST' }); rerender(false); }
  catch (e) { toast(upErr(e), 'error'); }
}

function definitionFor(doc) {
  const q = S.data.evaluation?.questionnaire;
  if (q && (!doc.questionnaire_version || q.version === doc.questionnaire_version)) return q.definition;
  return null;
}

function reviewForm(doc, rerender) {
  const ex = doc.extraction || {};
  const def = definitionFor(doc);
  const qById = {};
  for (const s of def?.sections || []) for (const q of s.questions || []) qById[q.id] = q;
  const KEY = `aita-doc-review:${doc.id}`;
  let saved = null;
  try { saved = JSON.parse(ss.get(KEY) || 'null'); } catch { saved = null; }
  // estado de edición: una entrada por pregunta
  const items = saved?.items || (ex.items || []).map((it) => ({
    ref: it.ref || null, type: it.type || 'text', question: it.question || '', orig: { status: it.status, value: it.value ?? null, answer: it.answer ?? null },
    state: ['answered', 'blank', 'illegible'].includes(it.status) ? it.status : null,
    value: it.status === 'answered' ? (it.value ?? it.answer ?? null) : (it.status === 'uncertain' && (!it.ref || it.type === 'text') ? it.answer : null),
  }));
  const body = saved?.body || Object.fromEntries(BODY.map(([k, dim]) => {
    const b = (ex.body || {})[k] || {};
    return [k, { value: b.value ?? '', unit: b.unit || (S.units === 'metric' ? (dim === 'w' ? 'kg' : 'cm') : (dim === 'w' ? 'lb' : 'in')), orig: b.status || 'blank', unitKnown: Boolean(b.unit) }];
  }));
  const clientRef = saved?.client_ref || uuid4();
  const persist = () => ss.set(KEY, JSON.stringify({ items, body, client_ref: clientRef }));
  persist();

  const wrap = el('section', { class: 'card review' });
  const methodNote = { pdf_form: 'up.m.pdf_form', ai_vision: 'up.m.ai_vision', manual: 'up.m.manual', failed: 'up.m.failed' }[ex.method] || 'up.m.manual';
  const render = () => {
    clear(wrap);
    put(wrap,
      el('div', { class: 'card-head' }, el('h2', {}, t('up.reviewTitle')), el('span', { class: 'badge warn' }, t('up.notSaved'))),
      el('p', { class: `notice ${ex.method === 'failed' ? 'bad' : 'warn'}` }, t(methodNote)),
      (ex.warnings || []).includes('form_empty') ? el('p', { class: 'notice warn' }, t('up.formEmpty')) : null,
      ex.method === 'failed' ? el('button', { class: 'btn btn-sm', type: 'button', onclick: () => retry(doc, rerender) }, t('up.retryRead')) : null,
      el('div', { class: 'row' }, el('span', { class: 'small muted' }, t('up.compareWith')), originalButtons(doc)),
      el('p', { class: 'small m0' }, t('up.legend')),
      !def && items.some(i => i.ref) ? el('p', { class: 'notice warn' }, t('up.defChanged')) : null,
      items.length ? null : el('p', { class: 'muted' }, t('up.noItems')),
      el('div', { class: 'stack' }, items.map((it, i) => itemEditor(it, i))),
      el('button', { class: 'btn btn-sm', type: 'button', onclick: () => { items.push({ ref: null, type: 'text', question: '', orig: { status: 'added' }, state: null, value: null, added: true }); persist(); render(); } }, t('up.addItem')),
      el('h3', {}, t('up.body')), el('p', { class: 'small muted m0' }, t('up.bodyHint')),
      el('div', { class: 'stack' }, BODY.map(([k, dim]) => bodyEditor(k, dim))),
      el('label', { class: 'opt' }, el('input', { type: 'checkbox', id: 'up-ok' }), el('span', {}, t('up.checked'))),
      el('p', { class: 'form-error', role: 'alert', id: 'up-err' }),
      el('button', { class: 'btn btn-primary btn-block', type: 'button', id: 'up-send', onclick: submit }, t('up.confirm')),
      el('p', { class: 'small muted m0' }, t('up.confirmNote')));
  };

  function stateBtns(it, onChange) {
    return el('div', { class: 'seg3', role: 'radiogroup', 'aria-label': t('up.stateLabel') }, ['answered', 'blank', 'illegible'].map(s =>
      el('button', { type: 'button', role: 'radio', 'aria-checked': String(it.state === s), class: `seg-btn ${it.state === s ? 'on' : ''}`,
        onclick: () => { it.state = s; if (s !== 'answered') it.value = null; persist(); onChange(); } }, t(`up.state.${s}`))));
  }

  function itemEditor(it, i) {
    const q = it.ref ? qById[it.ref] : null;
    const box = el('div', { class: `subcard item ${it.state ? '' : 'todo'}`, id: `it-${i}` });
    const draw = () => {
      clear(box);
      box.className = `subcard item ${it.state ? '' : 'todo'}`;
      const read = it.orig.status;
      const editQ = !q && (it.added || it.editQ);
      put(box,
        el('div', { class: 'row' },
          !editQ ? el('span', { class: 'strong grow' }, q ? L(q.label) : it.question) : null,
          it.added ? null : el('span', { class: `badge ${TONE[read] || ''}` }, t(`up.read.${read}`))),
        editQ ? el('label', { class: 'field' }, el('span', { class: 'small muted' }, t('up.questionAsPrinted')),
          el('input', { class: 'input', value: it.question, maxlength: 500, oninput: (e) => { it.question = e.target.value; persist(); } })) : null,
        !q && !editQ ? el('button', { class: 'link-btn small left', type: 'button', onclick: () => { it.editQ = true; persist(); draw(); } }, t('up.editQ')) : null,
        read === 'uncertain' && it.orig.answer ? el('p', { class: 'small m0' }, t('up.readAs', { a: it.orig.answer })) : null,
        stateBtns(it, draw),
        it.state === 'answered' ? answerInput(it, q) : null,
        it.state === 'blank' ? el('p', { class: 'small muted m0' }, t('up.blankNote')) : null,
        it.state === 'illegible' ? el('p', { class: 'small muted m0' }, t('up.illegibleNote')) : null);
    };
    draw();
    return box;
  }

  function answerInput(it, q) {
    const set = (v) => { it.value = v; persist(); };
    const type = q ? (q.type || 'text') : 'text';
    const name = `a-${Math.random().toString(36).slice(2)}`;
    if (type === 'single') {
      return el('div', { class: 'opts' }, (q.options || []).map(o => el('label', { class: 'opt' },
        el('input', { type: 'radio', name, checked: it.value === o.value, onchange: () => set(o.value) }), el('span', {}, L(o.label) || o.value))));
    }
    if (type === 'multi') {
      const cur = new Set(Array.isArray(it.value) ? it.value : []);
      return el('div', { class: 'opts' }, (q.options || []).map(o => el('label', { class: 'opt' },
        el('input', { type: 'checkbox', checked: cur.has(o.value), onchange: (e) => { e.target.checked ? cur.add(o.value) : cur.delete(o.value); set([...cur]); } }),
        el('span', {}, L(o.label) || o.value))));
    }
    if (type === 'yesno') {
      return el('div', { class: 'opts row2' }, [[true, t('yes')], [false, t('no')]].map(([v, lab]) => el('label', { class: 'opt' },
        el('input', { type: 'radio', name, checked: it.value === v, onchange: () => set(v) }), el('span', {}, lab))));
    }
    if (type === 'number') return el('input', { class: 'input', type: 'number', step: 'any', value: it.value ?? '', oninput: (e) => set(e.target.value === '' ? null : Number(e.target.value)) });
    if (type === 'date') return el('input', { class: 'input', type: 'date', value: it.value ?? '', oninput: (e) => set(e.target.value || null) });
    return el('textarea', { class: 'input', maxlength: 2000, oninput: (e) => set(e.target.value) }, it.value ?? '');
  }

  function bodyEditor(k, dim) {
    const b = body[k];
    const units = dim === 'w' ? ['lb', 'kg'] : ['in', 'cm'];
    return el('div', { class: 'grid-2 body-row' },
      el('label', { class: 'field' }, el('span', {}, t(`up.b.${k}`)),
        el('input', { class: 'input', type: 'number', step: 'any', min: 0, inputmode: 'decimal', value: b.value, oninput: (e) => { b.value = e.target.value; persist(); } })),
      el('label', { class: 'field' }, el('span', {}, t('up.unit')), el('select', { class: 'input', onchange: (e) => { b.unit = e.target.value; b.unitKnown = true; persist(); } },
        units.map(u => el('option', { value: u, selected: b.unit === u }, u)))),
      ['uncertain', 'illegible'].includes(b.orig) ? el('p', { class: 'small warn-text m0 span2' }, t('up.bodyCheck')) : null);
  }

  const displayOf = (it, q) => {
    if (!q) return String(it.value ?? '').trim();
    const lab = (v) => L((q.options || []).find(o => o.value === v)?.label) || v;
    if (q.type === 'multi') return (it.value || []).map(lab).join(', ');
    if (q.type === 'single') return lab(it.value);
    if (q.type === 'yesno') return it.value ? t('yes') : t('no');
    return String(it.value ?? '').trim();
  };
  const filled = (it, q) => {
    const v = it.value;
    if (v === null || v === undefined || v === '' || (Array.isArray(v) && !v.length)) return false;
    if (q && q.type === 'number' && (typeof v !== 'number' || Number.isNaN(v))) return false;
    return !(typeof v === 'string' && !v.trim());
  };

  async function submit() {
    const err = document.getElementById('up-err');
    err.textContent = '';
    const bad = items.findIndex(it => !it.state || (it.state === 'answered' && !filled(it, it.ref ? qById[it.ref] : null)) || (!it.ref && !it.question.trim()));
    if (bad >= 0) {
      const left = items.filter(it => !it.state || (it.state === 'answered' && !filled(it, it.ref ? qById[it.ref] : null)) || (!it.ref && !it.question.trim())).length;
      err.textContent = t('up.todo', { n: left });
      document.getElementById(`it-${bad}`)?.scrollIntoView({ block: 'center' });
      return;
    }
    if (!document.getElementById('up-ok').checked) { err.textContent = t('up.mustCheck'); return; }
    const qa = {};
    const outItems = items.filter(it => it.ref ? true : it.question.trim()).map(it => {
      const q = it.ref ? qById[it.ref] : null;
      const answered = it.state === 'answered';
      if (answered && q) qa[it.ref] = it.value;
      const answer = answered ? displayOf(it, q) : null;
      const corrected = Boolean(it.added) || it.orig.status !== it.state || (answered && JSON.stringify(it.orig.value ?? it.orig.answer) !== JSON.stringify(it.value));
      return { ref: q ? it.ref : null, question: (q ? L(q.label) : it.question.trim()).slice(0, 500), answer, status: it.state, corrected };
    });
    const bodyOut = {};
    for (const [k, dim] of BODY) {
      const v = Number(body[k].value);
      if (body[k].value === '' || body[k].value === null || !(v > 0)) continue;
      const canon = dim === 'w' ? (body[k].unit === 'kg' ? v / LB_TO_KG : v) : (body[k].unit === 'cm' ? v / IN_TO_CM : v);
      bodyOut[BODY_KEY[k]] = Math.round(canon * 10) / 10;
    }
    const btn = document.getElementById('up-send');
    btn.disabled = true; btn.textContent = t('ev.sending');
    try {
      const { data, error } = await S.sb.rpc('member_confirm_document', {
        p_document: doc.id, p_payload: { q: def ? qa : {}, items: outItems, body: bodyOut }, p_client_ref: clientRef });
      if (error) throw error;
      if (!data || !data.evaluation_id) throw new Error('no confirmation');
      ss.del(KEY);
      location.hash = '#/evaluation';
      await rerender(true, () => {
        const main = document.querySelector('main');
        main.insertBefore(el('section', { class: 'card callout', role: 'status' }, el('h2', {}, t('up.okTitle')),
          el('p', { class: 'm0' }, t('up.okText', { n: data.answered ?? 0 }))), main.firstChild);
        window.scrollTo(0, 0);
      });
    } catch (e) {
      btn.disabled = false; btn.textContent = t('ev.retry');
      err.textContent = `${errText(e)} ${t('up.kept')}`;
    }
  }

  render();
  return wrap;
}

// ---------- comparación inicial vs reevaluaciones ----------
const norm = (s) => String(s || '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/[^a-z0-9]+/g, ' ').trim();

export function answerMap(ev) {
  const out = new Map();
  const a = ev.answers || {};
  const status = {};
  for (const it of a.items || []) if (it.ref) status[it.ref] = it.status;
  const def = ev.definition;
  if (def) for (const s of def.sections || []) for (const q of s.questions || []) {
    const v = (a.q || {})[q.id];
    let text = null;
    if (!(v === undefined || v === null || v === '' || (Array.isArray(v) && !v.length))) {
      const lab = (x) => L((q.options || []).find(o => o.value === x)?.label) || x;
      text = Array.isArray(v) ? v.map(lab).join(', ') : typeof v === 'boolean' ? (v ? t('yes') : t('no')) : q.type === 'single' ? lab(v) : String(v);
    }
    out.set(`q:${q.id}`, { label: L(q.label), text, status: text ? 'answered' : (status[q.id] === 'illegible' ? 'illegible' : 'blank') });
  }
  for (const it of a.items || []) {
    if (it.ref && def) continue;
    const key = it.ref ? `q:${it.ref}` : `t:${norm(it.question)}`;
    if (!out.has(key)) out.set(key, { label: it.question, text: it.status === 'answered' ? it.answer : null, status: it.status });
  }
  return out;
}

export function compareCard(submitted) {
  const initial = submitted.find(e => e.kind === 'initial');
  const later = submitted.filter(e => e !== initial).sort((a, b) => String(b.submitted_at).localeCompare(String(a.submitted_at)));
  if (!initial || !later.length) return null;
  const wrap = el('section', { class: 'card' });
  let current = later[0];
  const cell = (x) => x ? (x.status === 'answered' ? x.text : el('span', { class: 'muted' }, t(`up.state.${x.status}`))) : el('span', { class: 'muted' }, t('cmp.notAsked'));
  const draw = () => {
    clear(wrap);
    const A = answerMap(initial), B = answerMap(current);
    const keys = [...A.keys(), ...[...B.keys()].filter(k => !A.has(k))];
    const changed = keys.filter(k => (A.get(k)?.text || null) !== (B.get(k)?.text || null) || (A.get(k)?.status) !== (B.get(k)?.status));
    put(wrap,
      el('div', { class: 'card-head' }, el('h2', {}, t('cmp.title')),
        later.length > 1 ? el('select', { class: 'input input-sm', 'aria-label': t('cmp.pick'), onchange: (e) => { current = later[Number(e.target.value)]; draw(); } },
          later.map((ev, i) => el('option', { value: String(i), selected: ev === current }, fmtDateTime(ev.submitted_at)))) : null),
      el('p', { class: 'small muted m0' }, t('cmp.lead', { a: fmtDateTime(initial.submitted_at), b: fmtDateTime(current.submitted_at), n: changed.length })),
      keys.length ? el('div', { class: 'table-wrap' }, el('table', { class: 'cmp' },
        el('thead', {}, el('tr', {}, el('th', {}, t('cmp.q')), el('th', {}, t('cmp.initial')), el('th', {}, t('cmp.now')))),
        el('tbody', {}, keys.map(k => el('tr', { class: changed.includes(k) ? 'changed' : '' },
          el('td', {}, (A.get(k) || B.get(k)).label, changed.includes(k) ? el('span', { class: 'badge gold ml6' }, t('cmp.changed')) : null),
          el('td', {}, cell(A.get(k))), el('td', {}, cell(B.get(k)))))))) : el('p', { class: 'muted m0' }, t('cmp.none')),
      el('p', { class: 'small muted m0' }, t('cmp.note')));
  };
  draw();
  return wrap;
}
