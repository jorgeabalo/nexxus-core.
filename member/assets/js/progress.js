// "Mi progreso": compara el punto de partida con la situación actual usando
// SOLO datos registrados. Nada se inventa: si falta un dato se muestra
// "Pendiente de registrar". Los cambios se presentan como observados durante
// el programa (no se atribuyen al ejercicio) y bajar de peso no se trata como
// éxito por sí mismo.
import {
  S, el, clear, t, fmtDay, num, card, empty, pending, openModal, toast, errText, store, svgEl,
  showWeight, showLength, showHeight, toLb, toIn, metric, LB_TO_KG, IN_TO_CM,
} from './util.js';

// ---------------------------------------------------------------------------
// Cálculos
// ---------------------------------------------------------------------------
const BODY_FIELDS = ['weight_lb', 'waist_in', 'left_arm_in', 'right_arm_in', 'left_leg_in', 'right_leg_in', 'height_in'];

/** Para un campo corporal: registro inicial (línea base si existe) y actual. */
export function bodyPair(field) {
  const rows = (S.data.measurements || []).filter(m => m[field] !== null && m[field] !== undefined);
  if (!rows.length) return null;
  const base = rows.find(m => m.is_baseline) || rows[0];
  const cur = rows[rows.length - 1];
  return { base, cur, baseVal: Number(base[field]), curVal: Number(cur[field]), same: base.id === cur.id,
           isBaseline: Boolean(base.is_baseline), history: rows.map(r => ({ date: r.date, v: Number(r[field]) })) };
}
/** Brazo / pierna: se compara el mismo lado en ambos registros. */
function sidePair(left, right) {
  const l = bodyPair(left), r = bodyPair(right);
  if (l && r) return (l.same && !r.same) ? r : l;
  return l || r;
}
export const bodyMetrics = () => ({
  weight: bodyPair('weight_lb'), waist: bodyPair('waist_in'),
  arm: sidePair('left_arm_in', 'right_arm_in'), leg: sidePair('left_leg_in', 'right_leg_in'),
  height: bodyPair('height_in'),
});

/** Indicadores agrupados por metric_key: base (línea base o primer registro) y actual. */
export function indicatorGroups() {
  const by = new Map();
  for (const p of S.data.progress || []) {
    if (!by.has(p.metric_key)) by.set(p.metric_key, []);
    by.get(p.metric_key).push(p);
  }
  const out = [];
  for (const [key, rows] of by) {
    const base = rows.find(r => r.is_baseline) || rows[0];
    const cur = rows[rows.length - 1];
    out.push({ key, category: base.category, metric: base.metric, exercise: base.exercise, base, cur,
               same: base.id === cur.id, rows });
  }
  const order = { strength: 0, mobility: 1, endurance: 2, wellbeing: 3 };
  return out.sort((a, b) => (order[a.category] - order[b.category]) || a.key.localeCompare(b.key));
}

const indicatorName = (g) => {
  if (g.category === 'strength') return g.exercise || t('prog.exercise');
  if (g.metric === 'other') return g.exercise || t('metric.other');
  return t(`metric.${g.metric}`);
};

/** Mensaje de apoyo basado en datos reales; null si los datos no demuestran nada. */
export function insightFor(g) {
  if (g.same) return { kind: 'single', text: t('ins.single') };
  const b = g.base, c = g.cur;
  const diffCond = b.conditions && c.conditions && b.conditions.trim().toLowerCase() !== c.conditions.trim().toLowerCase();
  if (diffCond) return { kind: 'conditions', text: t('ins.conditions') };
  const dv = Number(c.value) - Number(b.value);
  if (g.category === 'strength') {
    const dr = (c.reps || 0) - (b.reps || 0);
    if (dv === 0 && dr > 0) return { kind: 'up', text: t('ins.repsSameLoad', { n: dr }) };
    if (dv > 0 && dr >= 0) return { kind: 'up', text: t('ins.moreLoad', { x: showWeight(dv), reps: c.reps }) };
    return { kind: 'flat', text: t('ins.review') };
  }
  if (g.category === 'endurance') {
    if (dv >= 1) return { kind: 'up', text: t(g.metric === 'walk' ? 'ins.walkMore' : g.metric === 'bike' ? 'ins.bikeMore' : 'ins.activityMore', { n: num(dv, 0), activity: indicatorName(g) }) };
    return { kind: 'flat', text: t('ins.review') };
  }
  if (g.category === 'mobility') {
    if (g.metric === 'chair_stand_30s') return dv > 0 ? { kind: 'up', text: t('ins.chairStand', { n: num(dv, 0) }) } : { kind: 'flat', text: t('ins.review') };
    return dv > 0 ? { kind: 'up', text: t('ins.easier', { what: t(`metric.${g.metric}.lower`) }) } : { kind: 'flat', text: t('ins.review') };
  }
  if (g.category === 'wellbeing') {
    return dv > 0 ? { kind: 'up', text: t('ins.wellbeingUp', { what: t(`metric.${g.metric}.lower`) }) } : { kind: 'flat', text: t('ins.reviewSoft') };
  }
  return null;
}

/** Constancia a partir de asistencia real. */
export function consistencyInsight() {
  const c = S.data.consistency || {};
  const last = Number(c.last_30_days_visits || 0);
  const first = c.first_30_days_visits;
  if (!c.program_start && !last) return null;
  if (first !== null && first !== undefined && last > first && S.data.today > addDays(c.program_start, 45))
    return { kind: 'up', text: t('ins.moreVisits', { n: last, m: first }) };
  if (last > 0) return { kind: 'up', text: t('ins.visits30', { n: last }) };
  return null;
}
const addDays = (iso, n) => { const d = new Date(`${iso}T00:00:00Z`); d.setUTCDate(d.getUTCDate() + n); return d.toISOString().slice(0, 10); };

/** Los mejores mensajes reales para el inicio (máx. n). */
export function topInsights(n = 2) {
  const out = [];
  for (const g of indicatorGroups()) {
    const i = insightFor(g);
    if (i && i.kind === 'up') out.push(i.text);
  }
  const c = consistencyInsight();
  if (c) out.push(c.text);
  const w = bodyMetrics().weight;
  if (w && !w.same && Math.abs(w.curVal - w.baseVal) < 1 && out.length) out.push(t('ins.weightStable'));
  return out.slice(0, n);
}

// ---------------------------------------------------------------------------
// Figura corporal (ilustrativa): ver figure.js
// ---------------------------------------------------------------------------
export { figureSvg } from './figure.js';
import { figureSvg } from './figure.js';

function figureLabels() {
  const m = bodyMetrics();
  const lab = (pair, title) => ({
    title,
    value: pair ? showLength(pair.curVal) : null,
    delta: pair && !pair.same ? t('prog.vsStart', { d: showLength(pair.curVal - pair.baseVal, { signed: true }) }) : null,
  });
  return { arm: lab(m.arm, t('body.arm')), waist: lab(m.waist, t('body.waist')), leg: lab(m.leg, t('body.leg')) };
}

// Mini gráfico de línea (valores reales, sin suavizar)
function sparkline(points) {
  if (!points || points.length < 2) return null;
  const W = 140, H = 36, pad = 4;
  const vs = points.map(p => p.v);
  const lo = Math.min(...vs), hi = Math.max(...vs), span = hi - lo || 1;
  const xy = points.map((p, i) => [pad + (i * (W - 2 * pad)) / (points.length - 1), H - pad - ((p.v - lo) * (H - 2 * pad)) / span]);
  return svgEl('svg', { viewBox: `0 0 ${W} ${H}`, class: 'spark', 'aria-hidden': 'true' },
    svgEl('polyline', { points: xy.map(p => p.join(',')).join(' '), class: 'spark-line' }),
    ...xy.map(([x, yv], i) => svgEl('circle', { cx: x, cy: yv, r: i === xy.length - 1 ? 3.2 : 2.2, class: 'spark-dot' })));
}

// ---------------------------------------------------------------------------
// Vista "Mi progreso"
// ---------------------------------------------------------------------------
export function viewProgress(main, rerender) {
  const m = S.data.member;
  const unitToggle = el('div', { class: 'seg', role: 'group', 'aria-label': t('prog.units') },
    el('button', { type: 'button', class: metric() ? '' : 'on', 'aria-pressed': String(!metric()), onclick: () => setUnits('imperial', rerender) }, 'lb · in'),
    el('button', { type: 'button', class: metric() ? 'on' : '', 'aria-pressed': String(metric()), onclick: () => setUnits('metric', rerender) }, 'kg · cm'));

  main.appendChild(el('div', { class: 'row-head' }, el('h2', { class: 'section-title' }, t('prog.title')), unitToggle));
  main.appendChild(el('p', { class: 'muted small m0' }, t('prog.lead')));
  main.appendChild(el('button', { class: 'btn btn-primary btn-block', type: 'button', onclick: () => logModal(rerender) }, t('prog.log')));

  // Mensajes basados en datos reales
  const ins = topInsights(4);
  if (ins.length) main.appendChild(el('section', { class: 'card support' }, el('h2', {}, t('prog.observed')),
    el('ul', { class: 'insights' }, ins.map(x => el('li', {}, x))), el('p', { class: 'muted small m0' }, t('prog.observedNote'))));

  const hasAny = (S.data.measurements || []).length || (S.data.progress || []).length;
  if (!hasAny) {
    main.appendChild(el('section', { class: 'card callout' }, el('h2', {}, t('prog.startTitle')), el('p', {}, t('prog.startText')),
      el('div', { class: 'btn-col' },
        el('a', { class: 'btn btn-primary btn-block', href: '#/evaluation' }, t('prog.startEval')),
        el('button', { class: 'btn btn-block', type: 'button', onclick: () => logModal(rerender) }, t('prog.startQuick')))));
  }

  // Indicadores funcionales (primero, importantes para adultos mayores)
  const groups = indicatorGroups();
  const sections = [['strength', t('cat.strength')], ['mobility', t('cat.mobility')], ['endurance', t('cat.endurance')], ['wellbeing', t('cat.wellbeing')]];
  for (const [cat, title] of sections) {
    const gs = groups.filter(g => g.category === cat);
    const body = gs.length ? el('div', { class: 'stack' }, gs.map(indicatorCard))
      : el('div', { class: 'empty-cta' }, el('p', { class: 'm0' }, t(`cat.${cat}.empty`)),
        el('button', { class: 'btn btn-sm', type: 'button', onclick: () => logModal(rerender, cat) }, t('prog.addStart')));
    main.appendChild(card(title, body, el('span', { class: 'cat-hint' }, t(`cat.${cat}.hint`))));
  }
  main.appendChild(consistencyCard());

  // Figura corporal + medidas
  const sexNote = !m.sex ? el('div', { class: 'sex-ask' },
    el('p', { class: 'm0 small' }, t('fig.askSex')),
    el('div', { class: 'btn-row' },
      el('button', { class: 'btn btn-sm', type: 'button', onclick: () => setSex('female', rerender) }, t('fig.female')),
      el('button', { class: 'btn btn-sm', type: 'button', onclick: () => setSex('male', rerender) }, t('fig.male')))) : null;
  main.appendChild(card(t('prog.body'), el('div', {},
    el('p', { class: 'muted small m0' }, t('fig.note')),
    el('div', { class: 'figure-wrap' }, figureSvg(m.sex, figureLabels())),
    el('p', { class: 'small muted center m0' }, t('fig.legend')),
    sexNote, bodyTable()), null, 'body-card'));

  main.appendChild(historyCard());
}

function setUnits(u, rerender) { S.units = u; store.set('aita-member-units', u); rerender(); }
async function setSex(sex, rerender) {
  const { error } = await S.sb.rpc('member_set_sex', { p_sex: sex });
  if (error) return toast(errText(error), 'error');
  S.data.member.sex = sex; toast(t('fig.saved')); rerender();
}

function indicatorCard(g) {
  const fmt = (r) => {
    if (g.category === 'strength') return `${showWeight(r.value)} × ${r.reps}`;
    if (g.category === 'endurance') return `${num(r.value, 0)} min`;
    if (g.metric === 'chair_stand_30s') return `${num(r.value, 0)} ${t('prog.reps')}`;
    return `${num(r.value, 0)}/5 · ${t(`scale.${g.category === 'wellbeing' ? 'wb' : 'mob'}.${Math.round(r.value)}`)}`;
  };
  let change = '—';
  if (!g.same) {
    const dv = Number(g.cur.value) - Number(g.base.value);
    if (g.category === 'strength') {
      const dr = (g.cur.reps || 0) - (g.base.reps || 0);
      change = [dv ? showWeight(dv, { signed: true }) : null, dr ? `${dr > 0 ? '+' : ''}${dr} ${t('prog.reps')}` : null].filter(Boolean).join(' · ') || t('prog.noChange');
    } else if (g.category === 'endurance') change = dv ? `${dv > 0 ? '+' : ''}${num(dv, 0)} min` : t('prog.noChange');
    else change = dv ? `${dv > 0 ? '+' : ''}${num(dv, 0)}` : t('prog.noChange');
  }
  const ins = insightFor(g);
  const who = (r) => `${fmtDay(r.date)} · ${r.source === 'staff' ? t('src.staff', { name: r.recorded_by_name || '' }) : t('src.member')}`;
  return el('div', { class: 'ind' },
    el('div', { class: 'ind-head' }, el('span', { class: 'strong' }, indicatorName(g)), sparkline(g.rows.map(r => ({ v: Number(r.value) + (g.category === 'strength' ? (r.reps || 0) / 1000 : 0) })))),
    el('div', { class: 'bnc' },
      el('div', {}, el('span', { class: 'k' }, g.base.is_baseline ? t('prog.before') : t('prog.first')), el('span', { class: 'v' }, fmt(g.base)), el('span', { class: 'd' }, who(g.base))),
      el('div', { class: 'arrow', 'aria-hidden': 'true' }, '→'),
      el('div', {}, el('span', { class: 'k' }, t('prog.now')), el('span', { class: 'v' }, g.same ? '—' : fmt(g.cur)), el('span', { class: 'd' }, g.same ? t('prog.noUpdate') : who(g.cur))),
      el('div', { class: 'arrow', 'aria-hidden': 'true' }, '→'),
      el('div', {}, el('span', { class: 'k' }, t('prog.change')), el('span', { class: 'v' }, change))),
    g.base.conditions || g.cur.conditions ? el('p', { class: 'small muted m0' }, `${t('prog.conditions')}: ${[g.base.conditions, g.same ? null : g.cur.conditions].filter(Boolean).join(' → ')}`) : null,
    ins ? el('p', { class: `ins ins-${ins.kind}` }, ins.text) : null);
}

function consistencyCard() {
  const c = S.data.consistency || {};
  const v = S.data.visits || {};
  const months = (c.monthly_visits || []).slice(-6);
  const max = Math.max(1, ...months.map(x => x.visits));
  const bars = months.length ? el('div', { class: 'bars', 'aria-label': t('cons.chart') },
    months.map(x => el('div', { class: 'bar' },
      el('span', { class: 'bar-v' }, String(x.visits)),
      el('span', { class: `bar-fill h${Math.max(1, Math.round((x.visits / max) * 10))}` }),
      el('span', { class: 'bar-k' }, monthShort(x.month))))) : null;
  const ins = consistencyInsight();
  const body = (v.total || c.sessions_completed)
    ? el('div', {},
      el('div', { class: 'bnc' },
        el('div', {}, el('span', { class: 'k' }, t('cons.first30')), el('span', { class: 'v' }, c.first_30_days_visits ?? '—'), el('span', { class: 'd' }, c.program_start ? t('cons.since', { d: fmtDay(c.program_start) }) : '')),
        el('div', { class: 'arrow', 'aria-hidden': 'true' }, '→'),
        el('div', {}, el('span', { class: 'k' }, t('cons.last30')), el('span', { class: 'v' }, c.last_30_days_visits ?? 0), el('span', { class: 'd' }, t('cons.visits'))),
        el('div', { class: 'arrow', 'aria-hidden': 'true' }, '→'),
        el('div', {}, el('span', { class: 'k' }, t('cons.sessions')), el('span', { class: 'v' }, c.sessions_completed ?? 0), el('span', { class: 'd' }, t('cons.sessionsHint')))),
      bars, ins ? el('p', { class: 'ins ins-up' }, ins.text) : null)
    : empty(t('cons.none'));
  return card(t('cat.consistency'), body, el('span', { class: 'cat-hint' }, t('cat.consistency.hint')));
}
const monthShort = (ym) => { const [y, m] = ym.split('-').map(Number); return new Intl.DateTimeFormat(S.lang === 'es' ? 'es-US' : 'en-US', { month: 'short', timeZone: 'UTC' }).format(new Date(Date.UTC(y, m - 1, 1))); };

function bodyTable() {
  const m = bodyMetrics();
  const rows = [
    ['weight', t('body.weight'), m.weight, showWeight],
    ['height', t('body.height'), m.height, showHeight],
    ['waist', t('body.waist'), m.waist, showLength],
    ['arm', t('body.arm'), m.arm, showLength],
    ['leg', t('body.leg'), m.leg, showLength],
  ];
  const tr = rows.map(([k, label, p, f]) => {
    if (!p) return el('tr', {}, el('th', {}, label), el('td', { colspan: '3' }, pending()));
    const diff = p.same ? '—' : (k === 'height' ? showLength(p.curVal - p.baseVal, { signed: true }) : f(p.curVal - p.baseVal, { signed: true }));
    return el('tr', {},
      el('th', {}, label),
      el('td', {}, el('span', { class: 'v' }, f(p.baseVal)), el('span', { class: 'd' }, `${fmtDay(p.base.date)}${p.isBaseline ? '' : ' · ' + t('prog.firstShort')}`)),
      el('td', {}, el('span', { class: 'v' }, p.same ? '—' : f(p.curVal)), el('span', { class: 'd' }, p.same ? '' : fmtDay(p.cur.date))),
      el('td', { class: 'num' }, diff));
  });
  return el('div', { class: 'table-wrap' }, el('table', { class: 'bt' },
    el('thead', {}, el('tr', {}, el('th', {}, ''), el('th', {}, t('prog.start')), el('th', {}, t('prog.now')), el('th', {}, t('prog.change')))),
    el('tbody', {}, tr)),
    el('p', { class: 'small muted m0' }, t('prog.neutral')));
}

function historyCard() {
  const items = [
    ...(S.data.measurements || []).map(r => ({ date: r.date, at: r.created_at, kind: 'body', r })),
    ...(S.data.progress || []).map(r => ({ date: r.date, at: r.created_at, kind: 'ind', r })),
  ].sort((a, b) => String(b.at).localeCompare(String(a.at))).slice(0, 30);
  if (!items.length) return card(t('prog.history'), empty(t('prog.historyEmpty')));
  return card(t('prog.history'), el('div', {}, items.map(({ kind, r }) => {
    const what = kind === 'body'
      ? [r.weight_lb != null ? `${t('body.weight')} ${showWeight(r.weight_lb)}` : null, r.waist_in != null ? `${t('body.waist')} ${showLength(r.waist_in)}` : null,
         (r.left_arm_in ?? r.right_arm_in) != null ? `${t('body.arm')} ${showLength(r.left_arm_in ?? r.right_arm_in)}` : null,
         (r.left_leg_in ?? r.right_leg_in) != null ? `${t('body.leg')} ${showLength(r.left_leg_in ?? r.right_leg_in)}` : null,
         r.height_in != null ? `${t('body.height')} ${showHeight(r.height_in)}` : null].filter(Boolean).join(' · ')
      : `${indicatorName({ ...r })}: ${r.category === 'strength' ? `${showWeight(r.value)} × ${r.reps}` : r.category === 'endurance' ? `${num(r.value, 0)} min` : num(r.value, 0)}`;
    return el('div', { class: 'row' },
      el('div', { class: 'main' }, el('span', { class: 'strong' }, what),
        el('span', { class: 'muted small' }, `${fmtDay(r.date)} · ${r.source === 'staff' ? t('src.staff', { name: r.recorded_by_name || '' }) : t('src.member')}`)),
      r.is_baseline ? el('span', { class: 'badge gold' }, t('prog.baseline')) : null);
  })));
}

// ---------------------------------------------------------------------------
// Registrar nueva medición (actualización breve durante el mes)
// ---------------------------------------------------------------------------
export function logModal(rerender, initialCat = 'body') {
  const u = metric() ? { w: 'kg', l: 'cm' } : { w: 'lb', l: 'in' };
  const inp = (attrs) => el('input', { class: 'input', inputmode: 'decimal', type: 'number', step: 'any', ...attrs });
  const field = (label, input) => el('label', { class: 'field' }, el('span', {}, label), input);
  const scale = (kind) => el('select', { class: 'input' }, el('option', { value: '' }, '—'),
    [1, 2, 3, 4, 5].map(v => el('option', { value: String(v) }, `${v} · ${t(`scale.${kind}.${v}`)}`)));
  const F = {
    weight: inp({ min: 0 }), height: inp({ min: 0 }), waist: inp({ min: 0 }), arm: inp({ min: 0 }), leg: inp({ min: 0 }),
    ex: el('input', { class: 'input', maxlength: 80, placeholder: t('log.exPh'), list: 'known-ex' }), load: inp({ min: 0 }), reps: inp({ min: 1, step: 1, inputmode: 'numeric' }),
    cond: el('input', { class: 'input', maxlength: 200, placeholder: t('log.condPh') }),
    act: el('select', { class: 'input' }, ['walk', 'bike', 'other'].map(v => el('option', { value: v }, t(`metric.${v}`)))),
    actName: el('input', { class: 'input', maxlength: 80, placeholder: t('log.actPh') }), mins: inp({ min: 1, step: 1, inputmode: 'numeric' }),
    endCond: el('input', { class: 'input', maxlength: 200, placeholder: t('log.endCondPh') }),
    chair: scale('mob'), stairs: scale('mob'), walking: scale('mob'), chair30: inp({ min: 0, step: 1, inputmode: 'numeric' }),
    energy: scale('wb'), sleep: scale('wb'), overall: scale('wb'),
  };
  const known = [...new Set((S.data.progress || []).filter(p => p.category === 'strength').map(p => p.exercise))];
  const panes = {
    body: el('div', { class: 'form' }, el('p', { class: 'small muted m0' }, t('log.bodyHint')),
      el('div', { class: 'grid-2' }, field(`${t('body.weight')} (${u.w})`, F.weight), field(`${t('body.height')} (${u.l})`, F.height)),
      el('div', { class: 'grid-2' }, field(`${t('body.waist')} (${u.l})`, F.waist), field(`${t('body.arm')} (${u.l})`, F.arm)),
      field(`${t('body.leg')} (${u.l})`, F.leg)),
    strength: el('div', { class: 'form' }, el('p', { class: 'small muted m0' }, t('log.strengthHint')),
      field(t('log.exercise'), F.ex), el('datalist', { id: 'known-ex' }, known.map(k => el('option', { value: k }))),
      el('div', { class: 'grid-2' }, field(`${t('log.load')} (${u.w})`, F.load), field(t('log.reps'), F.reps)),
      field(t('prog.conditions'), F.cond)),
    endurance: el('div', { class: 'form' }, el('p', { class: 'small muted m0' }, t('log.endHint')),
      field(t('log.activity'), F.act), field(t('log.activityName'), F.actName), field(t('log.minutes'), F.mins), field(t('prog.conditions'), F.endCond)),
    mobility: el('div', { class: 'form' }, el('p', { class: 'small muted m0' }, t('log.mobHint')),
      field(t('metric.chair_rise'), F.chair), field(t('metric.stairs'), F.stairs), field(t('metric.walking'), F.walking),
      field(t('metric.chair_stand_30s'), F.chair30)),
    wellbeing: el('div', { class: 'form' }, el('p', { class: 'small muted m0' }, t('log.wbHint')),
      field(t('metric.energy'), F.energy), field(t('metric.sleep'), F.sleep), field(t('metric.overall'), F.overall)),
  };
  let cat = initialCat;
  const tabs = el('div', { class: 'chips', role: 'tablist' });
  const holder = el('div');
  const draw = () => {
    clear(tabs).append(...Object.keys(panes).map(k => el('button', { type: 'button', role: 'tab', class: `chip ${k === cat ? 'on' : ''}`, 'aria-selected': String(k === cat), onclick: () => { cat = k; draw(); } }, t(`log.tab.${k}`))));
    clear(holder).appendChild(panes[cat]);
    F.actName.closest('label').hidden = F.act.value !== 'other';
  };
  F.act.addEventListener('change', draw);
  const err = el('p', { class: 'form-error', role: 'alert' });
  const nOrNull = (x) => (x.value === '' ? null : Number(x.value));
  openModal(t('prog.log'), el('div', {}, tabs, holder, el('p', { class: 'small muted' }, t('log.source')), err), [(close) => el('button', {
    class: 'btn btn-primary', type: 'button', onclick: async (e) => {
      err.textContent = '';
      const body = { weight_lb: toLb(F.weight.value), height_in: toIn(F.height.value), waist_in: toIn(F.waist.value),
                     left_arm_in: toIn(F.arm.value), left_leg_in: toIn(F.leg.value) };
      Object.keys(body).forEach(k => body[k] === null && delete body[k]);
      const items = [];
      if (F.ex.value.trim() || F.load.value || F.reps.value) {
        if (!F.ex.value.trim() || F.load.value === '' || !F.reps.value) { err.textContent = t('log.strengthIncomplete'); cat = 'strength'; draw(); return; }
        items.push({ category: 'strength', metric: 'exercise', exercise: F.ex.value.trim(), value: toLb(F.load.value), reps: Math.round(Number(F.reps.value)), conditions: F.cond.value.trim() || null });
      }
      if (F.mins.value) items.push({ category: 'endurance', metric: F.act.value, exercise: F.act.value === 'other' ? (F.actName.value.trim() || null) : null, value: Math.round(Number(F.mins.value)), conditions: F.endCond.value.trim() || null });
      for (const [k, x] of [['chair_rise', F.chair], ['stairs', F.stairs], ['walking', F.walking]]) if (x.value) items.push({ category: 'mobility', metric: k, value: Number(x.value) });
      if (F.chair30.value !== '') items.push({ category: 'mobility', metric: 'chair_stand_30s', value: nOrNull(F.chair30) });
      for (const [k, x] of [['energy', F.energy], ['sleep', F.sleep], ['overall', F.overall]]) if (x.value) items.push({ category: 'wellbeing', metric: k, value: Number(x.value) });
      if (!Object.keys(body).length && !items.length) { err.textContent = t('err.noValues'); return; }
      e.target.disabled = true;
      const { error } = await S.sb.rpc('member_log_progress', { p_body: Object.keys(body).length ? body : null, p_items: items });
      if (error) { e.target.disabled = false; err.textContent = errText(error); return; }
      close(); toast(t('log.saved'));
      await rerender(true);
    },
  }, t('log.save'))]);
  draw();
}
