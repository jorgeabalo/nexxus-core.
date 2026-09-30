// Evaluación inicial / trimestral: formulario por pasos con borrador.
// * Las preguntas del cuestionario se leen de la definición versionada que el
//   gimnasio cargó en Supabase (tabla questionnaires). Si no hay ninguna, NO se
//   inventan preguntas: se registra solo el punto de partida (medidas e
//   indicadores) y se indica que el cuestionario está pendiente.
// * "Enviar" confirma solo cuando Supabase devolvió OK. Si falla, el borrador
//   se conserva y el reintento usa la misma referencia (sin duplicados).
import {
  S, el, clear, t, fmtDay, fmtDateTime, card, toast, errText, store, uuid4, authFetch, metric, toLb, toIn,
  showWeight, showLength, showHeight, num,
} from './util.js';
import { uploadCard, pendingDocsCard, loadDocs, originalButtons, compareCard } from './upload.js';

const L = (obj) => (obj && typeof obj === 'object') ? (obj[S.lang] || obj.es || obj.en || '') : (obj || '');

export function evalState() {
  const ev = S.data.evaluation || {};
  const submitted = ev.submitted || [];
  const initial = submitted.find(e => e.kind === 'initial') || null;
  const kind = initial ? 'reevaluation' : 'initial';
  const due = S.data.member.next_evaluation_due;
  return {
    questionnaire: ev.questionnaire, submitted, initial, kind, draft: (ev.drafts || {})[kind] || null,
    dueNow: !initial || (due && due <= S.data.today), due,
  };
}

// ---------- tarjeta "Mi evaluación inicial" (inicio) ----------
export function evaluationCard() {
  const st = evalState();
  const m = S.data.member;
  const isNew = m.joined_as === 'new';
  const qMissing = !st.questionnaire;
  let title, text, cta, href = '#/evaluation', cls = '';
  if (!st.initial) {
    title = t('ev.cardTitle');
    text = isNew ? t('ev.newText') : t('ev.existingText');
    cta = st.draft ? t('ev.continue') : t('ev.complete');
    cls = isNew ? 'callout' : '';
  } else if (st.dueNow) {
    title = t('ev.reevalTitle'); text = t('ev.reevalText', { d: fmtDay(st.due) }); cta = st.draft ? t('ev.continue') : t('ev.startReeval'); cls = 'callout';
  } else {
    title = t('ev.cardTitle'); text = t('ev.doneText', { d: fmtDay(st.initial.submitted_at), next: fmtDay(st.due) }); cta = t('ev.view');
  }
  return el('section', { class: `card ${cls}` },
    el('div', { class: 'card-head' }, el('h2', {}, title), st.initial ? el('span', { class: 'badge ok' }, t('ev.saved')) : el('span', { class: 'badge warn' }, t('ev.pending'))),
    el('p', { class: 'm0' }, text),
    qMissing && !st.initial ? el('p', { class: 'small muted' }, t('ev.qMissingShort')) : null,
    el('a', { class: `btn ${cls ? 'btn-primary' : ''} btn-block mt8`, href }, cta),
    st.dueNow ? el('a', { class: 'link-btn center-block', href }, t('up.homeLink')) : null);
}

// ---------- vista #/evaluation ----------
export function viewEvaluation(main, rerender) {
  const st = evalState();
  const pendingSlot = el('div', { class: 'stack' });
  main.appendChild(pendingSlot);
  if (st.dueNow) {
    // Dos caminos: llenarlo en línea o subir el cuestionario (papel/PDF).
    main.appendChild(formCard(st, rerender));
    main.appendChild(el('div', { class: 'choice small muted' }, t('up.or')));
    main.appendChild(uploadCard({ qAvailable: Boolean(st.questionnaire) }));
  } else main.appendChild(card(t('ev.nextTitle'), el('p', { class: 'm0' }, t('ev.nextText', { d: fmtDay(st.due) }))));
  const cmp = compareCard(st.submitted);
  if (cmp) main.appendChild(cmp);
  const hist = historyCard(st, []);
  main.appendChild(hist);
  loadDocs().then(docs => {
    const p = pendingDocsCard(docs);
    if (p) pendingSlot.appendChild(p);
    hist.replaceWith(historyCard(st, docs));
  }, () => {});
}

function historyCard(st, docs) {
  if (!st.submitted.length) return card(t('ev.history'), el('p', { class: 'muted m0' }, t('ev.historyEmpty')));
  return card(t('ev.history'), el('div', {}, [...st.submitted].reverse().map(e => {
    const doc = (docs || []).find(d => d.evaluation_id === e.id);
    const fromDoc = (e.answers || {}).source === 'document';
    return el('div', { class: 'eval-item' },
      el('div', { class: 'row' },
        el('div', { class: 'main' }, el('span', { class: 'strong' }, t(`ev.kind.${e.kind}`)),
          el('span', { class: 'muted small' }, `${fmtDateTime(e.submitted_at)} · ${e.questionnaire_version ? t('ev.qVersion', { v: e.questionnaire_version }) : (fromDoc ? t('up.fromDoc') : t('ev.qNotIncluded'))}`)),
        el('button', { class: 'btn btn-sm', type: 'button', onclick: (ev) => downloadPdf(e, ev.target) }, t('ev.pdf'))),
      doc ? originalButtons(doc) : null,
      el('details', {}, el('summary', {}, t('ev.viewAnswers')), answersView(e)));
  })));
}

function answersView(e) {
  const a = e.answers || {};
  const out = el('div', { class: 'answers' });
  const b = a.body || {};
  const bodyRows = [['weight_lb', t('body.weight'), showWeight], ['height_in', t('body.height'), showHeight], ['waist_in', t('body.waist'), showLength],
    ['left_arm_in', t('body.arm'), showLength], ['left_leg_in', t('body.leg'), showLength]].filter(([k]) => b[k] != null);
  if (bodyRows.length) out.append(el('h3', {}, t('ev.stepBody')), el('ul', {}, bodyRows.map(([k, lab, f]) => el('li', {}, `${lab}: ${f(b[k])}`))));
  if ((a.progress || []).length) out.append(el('h3', {}, t('ev.indicators')), el('ul', {}, a.progress.map(p => el('li', {}, progressLine(p)))));
  const def = e.definition;
  const docStatus = {};
  for (const it of a.items || []) if (it.ref) docStatus[it.ref] = it.status;
  if (def) for (const sec of def.sections || []) {
    out.append(el('h3', {}, L(sec.title)));
    out.append(el('ul', {}, (sec.questions || []).map(q => el('li', {}, `${L(q.label)}: ${docStatus[q.id] === 'illegible' && (a.q || {})[q.id] === undefined ? t('up.state.illegible') : answerText(q, (a.q || {})[q.id])}`))));
  }
  const free = (a.items || []).filter(it => !it.ref || !def);
  if (free.length) {
    out.append(el('h3', {}, t('up.docAnswers')));
    out.append(el('ul', {}, free.map(it => el('li', {}, `${it.question}: ${it.status === 'answered' ? it.answer : t(`up.state.${it.status}`)}${it.corrected ? ` (${t('up.corrected')})` : ''}`))));
  }
  if (!out.childNodes.length) out.append(el('p', { class: 'muted m0' }, t('ev.noData')));
  return out;
}
const progressLine = (p) => {
  const name = p.category === 'strength' ? p.exercise : (p.metric === 'other' && p.exercise ? p.exercise : t(`metric.${p.metric}`));
  const v = p.category === 'strength' ? `${showWeight(p.value)} × ${p.reps}` : p.category === 'endurance' ? `${num(p.value, 0)} min` : num(p.value, 0);
  return `${t(`cat.${p.category}`)} · ${name}: ${v}${p.conditions ? ` (${p.conditions})` : ''}`;
};
function answerText(q, v) {
  if (v === undefined || v === null || v === '' || (Array.isArray(v) && !v.length)) return t('ev.noAnswer');
  const opt = (x) => L((q.options || []).find(o => o.value === x)?.label) || x;
  if (Array.isArray(v)) return v.map(opt).join(', ');
  if (typeof v === 'boolean') return v ? t('yes') : t('no');
  return q.type === 'single' ? opt(v) : String(v);
}

async function downloadPdf(e, btn) {
  btn.disabled = true;
  try {
    const r = await authFetch(`/api/member/evaluations/${encodeURIComponent(e.id)}/pdf?lang=${S.lang}`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const blob = await r.blob();
    const url = URL.createObjectURL(blob);
    const a = el('a', { href: url, download: `evaluacion-${String(e.submitted_at).slice(0, 10)}.pdf` });
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 5000);
  } catch (err) { toast(errText(err), 'error'); }
  btn.disabled = false;
}

// ---------- formulario por pasos ----------
const LOCAL_KEY = (kind) => `aita-eval-draft:${S.data.member.id}:${kind}`;

function emptyForm() {
  return { units: S.units, q: {}, body: {}, strength: [{ exercise: '', load: '', reps: '', conditions: '' }],
           endurance: { activity: 'walk', name: '', minutes: '', conditions: '' },
           mobility: { chair_rise: '', stairs: '', walking: '', chair_stand_30s: '' },
           wellbeing: { energy: '', sleep: '', overall: '' }, client_ref: uuid4() };
}

function formCard(st, rerender) {
  const q = st.questionnaire;
  const sections = q ? (q.definition.sections || []) : [];
  const steps = [{ id: 'intro' }, ...sections.map((s, i) => ({ id: `q${i}`, section: s })),
    { id: 'body' }, { id: 'strength' }, { id: 'endmob' }, { id: 'wellbeing' }, { id: 'review' }];
  // Borrador: servidor (fuente principal) o copia local si el servidor no respondió
  let local = null;
  try { local = JSON.parse(store.get(LOCAL_KEY(st.kind)) || 'null'); } catch { local = null; }
  const server = st.draft?.answers?.form ? { ...st.draft.answers.form, q: st.draft.answers.q || {} } : null;
  let form = (server && local && local.saved_at > (st.draft.updated_at || '')) ? local : (server || local || emptyForm());
  form = { ...emptyForm(), ...form };
  if (form.units && form.units !== S.units) { S.units = form.units; }  // los valores del borrador están en esa unidad
  let step = Math.min(Number(st.draft?.step || form.step || 0), steps.length - 1);
  let saving = false;

  const wrap = el('section', { class: 'card evalform' });
  const render = () => {
    clear(wrap);
    const s = steps[step];
    wrap.append(
      el('div', { class: 'card-head' }, el('h2', {}, t(`ev.kind.${st.kind}`)), el('span', { class: 'muted small' }, t('ev.stepOf', { n: step + 1, total: steps.length }))),
      el('div', { class: 'progressbar', role: 'progressbar', 'aria-valuemin': '1', 'aria-valuemax': String(steps.length), 'aria-valuenow': String(step + 1) },
        el('span', { class: `pb-fill w${Math.round(((step + 1) / steps.length) * 20)}` })),
      stepBody(s),
      el('p', { class: 'form-error', role: 'alert', id: 'ev-err' }),
      el('div', { class: 'ev-nav' },
        step > 0 ? el('button', { class: 'btn', type: 'button', onclick: () => go(step - 1) }, t('ev.back')) : el('span'),
        s.id === 'review'
          ? el('button', { class: 'btn btn-primary', type: 'button', id: 'ev-send', onclick: submit }, t('ev.send'))
          : el('button', { class: 'btn btn-primary', type: 'button', onclick: () => next() }, t('ev.next'))),
      s.id !== 'review' ? el('button', { class: 'link-btn', type: 'button', onclick: async () => { await saveDraft(true); } }, t('ev.saveLater')) : null,
      el('p', { class: 'small muted m0', id: 'ev-saved' }, form.saved_at ? t('ev.draftSaved', { d: fmtDateTime(form.saved_at) }) : ''));
  };

  const u = () => (S.units === 'metric' ? { w: 'kg', l: 'cm' } : { w: 'lb', l: 'in' });
  const inp = (obj, key, attrs = {}) => el('input', { class: 'input', value: obj[key] ?? '', oninput: (e) => { obj[key] = e.target.value; }, ...attrs });
  const numInp = (obj, key, attrs = {}) => inp(obj, key, { type: 'number', step: 'any', inputmode: 'decimal', min: 0, ...attrs });
  const field = (label, input, hint) => el('label', { class: 'field' }, el('span', {}, label), input, hint ? el('small', { class: 'muted' }, hint) : null);
  const scale = (obj, key, kind) => el('select', { class: 'input', onchange: (e) => { obj[key] = e.target.value; } },
    el('option', { value: '' }, t('ev.skip')),
    [1, 2, 3, 4, 5].map(v => el('option', { value: String(v), selected: String(obj[key]) === String(v) }, `${v} · ${t(`scale.${kind}.${v}`)}`)));

  function stepBody(s) {
    if (s.id === 'intro') {
      return el('div', { class: 'stack' },
        el('p', { class: 'm0' }, st.kind === 'initial' ? t('ev.introInitial') : t('ev.introReeval')),
        q ? el('p', { class: 'small muted m0' }, t('ev.qInfo', { title: L(q.title), v: q.version }))
          : el('div', { class: 'notice warn' }, t('ev.qMissing')),
        el('p', { class: 'small muted m0' }, t('ev.privacy')));
    }
    if (s.section) {
      return el('div', { class: 'form' }, el('h3', {}, L(s.section.title)),
        (s.section.questions || []).map(qu => questionInput(qu)));
    }
    if (s.id === 'body') {
      const U = u();
      return el('div', { class: 'form' }, el('h3', {}, t('ev.stepBody')), el('p', { class: 'small muted m0' }, t('ev.bodyHint')),
        el('div', { class: 'grid-2' }, field(`${t('body.weight')} (${U.w})`, numInp(form.body, 'weight')), field(`${t('body.height')} (${U.l})`, numInp(form.body, 'height'))),
        el('div', { class: 'grid-2' }, field(`${t('body.waist')} (${U.l})`, numInp(form.body, 'waist')), field(`${t('body.arm')} (${U.l})`, numInp(form.body, 'arm'))),
        field(`${t('body.leg')} (${U.l})`, numInp(form.body, 'leg')));
    }
    if (s.id === 'strength') {
      const U = u();
      return el('div', { class: 'form' }, el('h3', {}, t('cat.strength')), el('p', { class: 'small muted m0' }, t('ev.strengthHint')),
        form.strength.map((row, i) => el('div', { class: 'subcard' },
          field(t('log.exercise'), inp(row, 'exercise', { maxlength: 80, placeholder: t('log.exPh') })),
          el('div', { class: 'grid-2' }, field(`${t('log.load')} (${U.w})`, numInp(row, 'load')), field(t('log.reps'), numInp(row, 'reps', { step: 1, min: 1, inputmode: 'numeric' }))),
          field(t('prog.conditions'), inp(row, 'conditions', { maxlength: 200, placeholder: t('log.condPh') })),
          form.strength.length > 1 ? el('button', { class: 'link-btn', type: 'button', onclick: () => { form.strength.splice(i, 1); render(); } }, t('ev.remove')) : null)),
        form.strength.length < 4 ? el('button', { class: 'btn btn-sm', type: 'button', onclick: () => { form.strength.push({ exercise: '', load: '', reps: '', conditions: '' }); render(); } }, t('ev.addExercise')) : null);
    }
    if (s.id === 'endmob') {
      const act = el('select', { class: 'input', onchange: (e) => { form.endurance.activity = e.target.value; render(); } },
        ['walk', 'bike', 'other'].map(v => el('option', { value: v, selected: form.endurance.activity === v }, t(`metric.${v}`))));
      return el('div', { class: 'form' },
        el('h3', {}, t('cat.endurance')), el('p', { class: 'small muted m0' }, t('log.endHint')),
        field(t('log.activity'), act),
        form.endurance.activity === 'other' ? field(t('log.activityName'), inp(form.endurance, 'name', { maxlength: 80 })) : null,
        field(t('log.minutes'), numInp(form.endurance, 'minutes', { step: 1, min: 1, inputmode: 'numeric' })),
        field(t('prog.conditions'), inp(form.endurance, 'conditions', { maxlength: 200, placeholder: t('log.endCondPh') })),
        el('h3', {}, t('cat.mobility')), el('p', { class: 'small muted m0' }, t('log.mobHint')),
        field(t('metric.chair_rise'), scale(form.mobility, 'chair_rise', 'mob')),
        field(t('metric.stairs'), scale(form.mobility, 'stairs', 'mob')),
        field(t('metric.walking'), scale(form.mobility, 'walking', 'mob')),
        field(t('metric.chair_stand_30s'), numInp(form.mobility, 'chair_stand_30s', { step: 1, inputmode: 'numeric' }), t('ev.chairHint')));
    }
    if (s.id === 'wellbeing') {
      return el('div', { class: 'form' }, el('h3', {}, t('cat.wellbeing')), el('p', { class: 'small muted m0' }, t('log.wbHint')),
        field(t('metric.energy'), scale(form.wellbeing, 'energy', 'wb')),
        field(t('metric.sleep'), scale(form.wellbeing, 'sleep', 'wb')),
        field(t('metric.overall'), scale(form.wellbeing, 'overall', 'wb')));
    }
    // review
    const p = payload();
    const lines = [];
    const b = p.body;
    if (b.weight_lb != null) lines.push(`${t('body.weight')}: ${showWeight(b.weight_lb)}`);
    if (b.height_in != null) lines.push(`${t('body.height')}: ${showHeight(b.height_in)}`);
    if (b.waist_in != null) lines.push(`${t('body.waist')}: ${showLength(b.waist_in)}`);
    if (b.left_arm_in != null) lines.push(`${t('body.arm')}: ${showLength(b.left_arm_in)}`);
    if (b.left_leg_in != null) lines.push(`${t('body.leg')}: ${showLength(b.left_leg_in)}`);
    p.progress.forEach(x => lines.push(progressLine(x)));
    const qCount = Object.keys(p.q).length;
    return el('div', { class: 'stack' }, el('h3', { class: 'm0' }, t('ev.review')),
      q ? el('p', { class: 'm0' }, t('ev.reviewQ', { n: qCount })) : el('div', { class: 'notice warn' }, t('ev.qMissing')),
      lines.length ? el('ul', { class: 'review-list' }, lines.map(x => el('li', {}, x))) : el('p', { class: 'muted m0' }, t('ev.reviewEmpty')),
      el('p', { class: 'small muted m0' }, t('ev.reviewNote')));
  }

  function questionInput(qu) {
    const id = qu.id; const v = form.q[id];
    const label = `${L(qu.label)}${qu.required ? ' *' : ''}`;
    const set = (x) => { form.q[id] = x; };
    let input;
    if (qu.type === 'single') {
      input = el('div', { class: 'opts', role: 'radiogroup', 'aria-label': L(qu.label) }, (qu.options || []).map(o =>
        el('label', { class: 'opt' }, el('input', { type: 'radio', name: id, value: o.value, checked: v === o.value, onchange: () => set(o.value) }), el('span', {}, L(o.label) || o.value))));
    } else if (qu.type === 'multi') {
      const cur = new Set(Array.isArray(v) ? v : []);
      input = el('div', { class: 'opts' }, (qu.options || []).map(o =>
        el('label', { class: 'opt' }, el('input', { type: 'checkbox', value: o.value, checked: cur.has(o.value), onchange: (e) => { e.target.checked ? cur.add(o.value) : cur.delete(o.value); set([...cur]); } }), el('span', {}, L(o.label) || o.value))));
    } else if (qu.type === 'yesno') {
      input = el('div', { class: 'opts row2' }, [[true, t('yes')], [false, t('no')]].map(([val, lab]) =>
        el('label', { class: 'opt' }, el('input', { type: 'radio', name: id, checked: v === val, onchange: () => set(val) }), el('span', {}, lab))));
    } else if (qu.type === 'number') {
      input = el('input', { class: 'input', type: 'number', step: 'any', value: v ?? '', min: qu.min, max: qu.max, oninput: (e) => set(e.target.value === '' ? null : Number(e.target.value)) });
    } else if (qu.type === 'date') {
      input = el('input', { class: 'input', type: 'date', value: v ?? '', oninput: (e) => set(e.target.value || null) });
    } else {
      input = el('textarea', { class: 'input', maxlength: 2000, oninput: (e) => set(e.target.value) }, v ?? '');
    }
    return el('div', { class: 'field' }, el('span', {}, label), qu.help ? el('small', { class: 'muted' }, L(qu.help)) : null, input);
  }

  function payload() {
    const nOr = (x) => (x === '' || x === null || x === undefined || isNaN(Number(x))) ? null : Number(x);
    const body = { weight_lb: toLb(form.body.weight), height_in: toIn(form.body.height), waist_in: toIn(form.body.waist),
                   left_arm_in: toIn(form.body.arm), left_leg_in: toIn(form.body.leg) };
    Object.keys(body).forEach(k => body[k] === null && delete body[k]);
    const progress = [];
    for (const r of form.strength) if (r.exercise.trim() && nOr(r.load) !== null && nOr(r.reps)) progress.push({ category: 'strength', metric: 'exercise', exercise: r.exercise.trim(), value: toLb(r.load), reps: Math.round(nOr(r.reps)), conditions: r.conditions.trim() || null });
    if (nOr(form.endurance.minutes)) progress.push({ category: 'endurance', metric: form.endurance.activity, exercise: form.endurance.activity === 'other' ? (form.endurance.name.trim() || null) : null, value: Math.round(nOr(form.endurance.minutes)), conditions: form.endurance.conditions.trim() || null });
    for (const k of ['chair_rise', 'stairs', 'walking']) if (nOr(form.mobility[k])) progress.push({ category: 'mobility', metric: k, value: nOr(form.mobility[k]) });
    if (nOr(form.mobility.chair_stand_30s) !== null) progress.push({ category: 'mobility', metric: 'chair_stand_30s', value: nOr(form.mobility.chair_stand_30s) });
    for (const k of ['energy', 'sleep', 'overall']) if (nOr(form.wellbeing[k])) progress.push({ category: 'wellbeing', metric: k, value: nOr(form.wellbeing[k]) });
    const qa = {};
    for (const [k, v] of Object.entries(form.q || {})) if (!(v === null || v === '' || (Array.isArray(v) && !v.length))) qa[k] = v;
    return { q: q ? qa : {}, body, progress };
  }

  function validateStep() {
    const s = steps[step];
    if (!s.section) return null;
    for (const qu of s.section.questions || []) {
      const v = form.q[qu.id];
      if (qu.required && (v === undefined || v === null || v === '' || (Array.isArray(v) && !v.length))) return t('ev.requiredQ', { q: L(qu.label) });
    }
    return null;
  }

  async function saveDraft(announce = false) {
    form.step = step; form.units = S.units; form.saved_at = new Date().toISOString();
    store.set(LOCAL_KEY(st.kind), JSON.stringify(form));
    if (saving) return;
    saving = true;
    const { q: qa, ...rest } = form;
    const { error } = await S.sb.rpc('member_save_evaluation_draft', { p_kind: st.kind, p_answers: { q: q ? payload().q : {}, form: rest }, p_step: step });
    saving = false;
    const lbl = document.getElementById('ev-saved');
    if (error) { if (announce) toast(t('ev.draftLocal'), 'error'); return; }
    if (lbl) lbl.textContent = t('ev.draftSaved', { d: fmtDateTime(form.saved_at) });
    if (announce) toast(t('ev.draftOk'));
  }
  function go(n) { step = Math.max(0, Math.min(steps.length - 1, n)); render(); saveDraft(); window.scrollTo(0, 0); }
  function next() {
    const e = validateStep();
    if (e) { document.getElementById('ev-err').textContent = e; return; }
    go(step + 1);
  }

  async function submit() {
    const btn = document.getElementById('ev-send');
    const errBox = document.getElementById('ev-err');
    errBox.textContent = '';
    const p = payload();
    if (!q && !Object.keys(p.body).length && !p.progress.length) { errBox.textContent = t('ev.reviewEmpty'); return; }
    btn.disabled = true; btn.textContent = t('ev.sending');
    form.saved_at = new Date().toISOString();
    store.set(LOCAL_KEY(st.kind), JSON.stringify(form));   // conserva borrador + client_ref para reintentar
    try {
      const { data, error } = await S.sb.rpc('member_submit_evaluation', { p_kind: st.kind, p_payload: p, p_client_ref: form.client_ref });
      if (error) throw error;
      if (!data || !data.evaluation_id) throw new Error('no confirmation');
      store.del(LOCAL_KEY(st.kind));
      await rerender(true, () => confirmation(data));
    } catch (e) {
      btn.disabled = false; btn.textContent = t('ev.retry');
      errBox.textContent = `${errText(e)} ${t('ev.keptDraft')}`;
    }
  }

  function confirmation(data) {
    const main = document.querySelector('main');
    const box = el('section', { class: 'card callout', role: 'status' },
      el('h2', {}, t('ev.okTitle')),
      el('p', { class: 'm0' }, t('ev.okText', { d: fmtDay(data.next_due_date) })),
      !data.questionnaire_included ? el('p', { class: 'small muted' }, t('ev.okNoQ')) : null,
      el('a', { class: 'btn btn-primary btn-block mt8', href: '#/progress' }, t('ev.seeProgress')));
    main.insertBefore(box, main.firstChild);
    window.scrollTo(0, 0);
  }

  render();
  return wrap;
}
