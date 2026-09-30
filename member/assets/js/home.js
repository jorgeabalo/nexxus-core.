// Inicio del portal con estilo "cabina de mando": paneles estructurados en
// cuadrícula, solo dos colores (azul marino y dorado). Todos los valores salen
// de datos reales; lo que falta se muestra como "PENDIENTE".
import { S, el, t, fmtDay, fmtDate, fmtClock, num, svgEl, toast, errText, openModal, showWeight, showLength, showHeight, store, metric } from './util.js';
import { figureSvg, bodyMetrics, topInsights } from './progress.js';
import { evalState } from './evaluation.js';
import { welcomeKind } from './welcome.js';

// ---------- piezas del tablero ----------
const panel = (code, title, body, { cls = '', extra = null } = {}) =>
  el('section', { class: `panel ${cls}` },
    el('header', { class: 'panel-h' }, el('span', { class: 'panel-code' }, code), el('h2', {}, title), extra),
    body);
const readout = (label, value, unit = null, { big = false, pending = false } = {}) =>
  el('div', { class: `ro ${big ? 'ro-big' : ''} ${pending ? 'ro-pending' : ''}` },
    el('span', { class: 'ro-k' }, label),
    el('span', { class: 'ro-v' }, pending ? t('ck.pending') : value, unit && !pending ? el('small', {}, unit) : null));

function gauge(value, max, label, sub) {
  const pct = Math.max(0, Math.min(1, max ? value / max : 0));
  const R = 42, C = 2 * Math.PI * R, arc = C * 0.75;
  const svg = svgEl('svg', { viewBox: '0 0 100 100', class: 'gauge', role: 'img', 'aria-label': `${label}: ${value}` },
    svgEl('circle', { cx: 50, cy: 50, r: R, class: 'g-track', 'stroke-dasharray': `${arc} ${C}`, transform: 'rotate(135 50 50)' }),
    svgEl('circle', { cx: 50, cy: 50, r: R, class: 'g-fill', 'stroke-dasharray': `${arc * pct} ${C}`, transform: 'rotate(135 50 50)' }),
    svgEl('text', { x: 50, y: 54, class: 'g-val', 'text-anchor': 'middle' }, String(value)),
    svgEl('text', { x: 50, y: 70, class: 'g-sub', 'text-anchor': 'middle' }, sub || ''));
  return el('div', { class: 'gauge-box' }, svg, el('span', { class: 'ro-k center' }, label));
}

// ---------- cálculos con datos reales ----------
function age(dob) {
  if (!dob) return null;
  const [y, m, d] = dob.split('-').map(Number);
  const [ty, tm, td] = S.data.today.split('-').map(Number);
  return ty - y - ((tm < m || (tm === m && td < d)) ? 1 : 0);
}
function tenure() {
  const start = S.data.member.start_date;
  if (!start) return null;
  const days = (new Date(`${S.data.today}T00:00:00Z`) - new Date(`${start}T00:00:00Z`)) / 86400000;
  const years = days / 365.25;
  const level = years >= 3 ? 3 : years >= 2 ? 2 : years >= 1 ? 1 : 0;
  const months = Math.max(0, Math.floor(days / 30.44));
  return { years, level, months, nextIn: level < 3 ? Math.max(0, Math.ceil(((level + 1) * 365.25 - days) / 30.44)) : 0 };
}
function weekStreaks(weeks) {
  // semanas (lunes) con al menos una visita -> racha máxima y actual
  const ws = (weeks || []).map(w => Date.parse(`${w}T00:00:00Z`)).sort((a, b) => a - b);
  let best = 0, run = 0, prev = null;
  for (const w of ws) { run = prev !== null && w - prev === 7 * 86400000 ? run + 1 : 1; best = Math.max(best, run); prev = w; }
  const thisWeek = (() => { const d = new Date(`${S.data.today}T00:00:00Z`); const wd = (d.getUTCDay() + 6) % 7; return d.getTime() - wd * 86400000; })();
  const current = prev !== null && (thisWeek - prev) <= 7 * 86400000 ? run : 0;
  return { best, current };
}
function medals(h, streak) {
  const bm = h.best_month?.visits || 0;
  const ls = h.longest_session_min || 0;
  const tv = h.total_visits || 0;
  const ten = tenure();
  return [
    { id: 'first', got: tv >= 1, v: tv >= 1 ? fmtDay(h.first_visit) : null },
    { id: 'v25', got: tv >= 25, v: `${Math.min(tv, 25)}/25` },
    { id: 'v100', got: tv >= 100, v: `${Math.min(tv, 100)}/100` },
    { id: 'month12', got: bm >= 12, v: `${bm}/12` },
    { id: 'streak4', got: streak.best >= 4, v: `${Math.min(streak.best, 4)}/4` },
    { id: 'streak12', got: streak.best >= 12, v: `${Math.min(streak.best, 12)}/12` },
    { id: 'long90', got: ls >= 90, v: ls ? `${ls}/90 min` : null },
    { id: 'eval', got: Boolean(h.initial_evaluation_at), v: h.initial_evaluation_at ? fmtDay(String(h.initial_evaluation_at).slice(0, 10)) : null },
    { id: 'year1', got: (ten?.level || 0) >= 1, v: null },
    { id: 'year3', got: (ten?.level || 0) >= 3, v: null },
  ];
}
const MEDAL_ICON = {
  first: 'M12 3l2.6 5.3 5.9.9-4.3 4.1 1 5.8L12 16.4 6.8 19.1l1-5.8L3.5 9.2l5.9-.9z',
  v25: 'M5 4h14v4a7 7 0 0 1-14 0zM9 15h6v2H9zm-2 3h10v2H7z', v100: 'M5 4h14v4a7 7 0 0 1-14 0zM9 15h6v2H9zm-2 3h10v2H7z',
  month12: 'M7 2v2H5a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6a2 2 0 0 0-2-2h-2V2h-2v2H9V2zM5 9h14v10H5z',
  streak4: 'M13 2 4 14h7l-1 8 9-12h-7z', streak12: 'M13 2 4 14h7l-1 8 9-12h-7z',
  long90: 'M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm1 5v5.4l4 2.4-.8 1.3L11 13V7z',
  eval: 'M9 16.2 4.8 12l-1.4 1.4L9 19 21 7l-1.4-1.4z',
  year1: 'M12 2 3 7v6c0 5 3.8 9.3 9 10 5.2-.7 9-5 9-10V7z', year3: 'M12 2 3 7v6c0 5 3.8 9.3 9 10 5.2-.7 9-5 9-10V7z',
};
const medalIcon = (id) => svgEl('svg', { viewBox: '0 0 24 24', class: 'medal-ic', 'aria-hidden': 'true' }, svgEl('path', { d: MEDAL_ICON[id] }));

// ---------- tablero ----------
export function viewCockpit(main, { rerender, requestModal, loadQr, upcoming, apptStatus }) {
  const m = S.data.member;
  const h = S.home || {};
  main.classList.add('cockpit');

  // 01 · ANUNCIO
  const kind = welcomeKind();
  const seed = [...String(m.id)].reduce((a, c) => a + c.charCodeAt(0), 0);
  const n = { first: 3, onboardingNew: 3, onboardingExisting: 3, consistency: 3, return: 3, general: 4 }[kind];
  const i = (Math.floor(Date.now() / 86400000) + seed) % n;
  const ann = h.announcement ? (h.announcement[S.lang] || h.announcement.es || h.announcement.en || (typeof h.announcement === 'string' ? h.announcement : null)) : null;
  main.appendChild(panel('01', t('ck.announce'), el('div', { class: 'ann' },
    el('p', { class: 'ann-msg' }, t(`wel.${kind}.${i}`, { name: m.first_name || '', n: S.data.visits?.last_30_days || 0 })),
    ann ? el('p', { class: 'ann-gym' }, el('span', { class: 'tag' }, t('ck.gymNews')), ann) : null,
    kind === 'first' ? el('p', { class: 'ann-sub' }, t('wel.first.guide')) : null)));

  // 02 · SOCIO
  const a = age(h.date_of_birth);
  const dobBtn = !h.date_of_birth ? el('button', { class: 'btn btn-sm', type: 'button', onclick: () => dobModal(rerender) }, t('ck.addDob')) : null;
  main.appendChild(panel('02', t('ck.member'), el('div', {},
    el('div', { class: 'ro-grid g3' },
      readout(t('ck.name'), [m.first_name, m.last_name].filter(Boolean).join(' '), null, { big: true }),
      readout(t('ck.age'), a ?? '', a !== null ? t('ck.years') : null, { pending: a === null }),
      readout(t('ck.dob'), h.date_of_birth ? fmtDay(h.date_of_birth) : '', null, { pending: !h.date_of_birth }),
      readout(t('ck.id'), m.member_code || '—'),
      readout(t('ck.plan'), m.membership_type || '—'),
      readout(t('ck.status'), t(`status.${m.membership_status}`) || m.membership_status)),
    dobBtn)));

  // 03 · ANTROPOMETRÍA
  main.appendChild(anthroPanel(rerender));

  // 04 · MÉTRICAS DEL GYM
  const ten = tenure();
  const streak = weekStreaks(h.visit_weeks);
  const levels = ['ck.lvl0', 'ck.lvl1', 'ck.lvl2', 'ck.lvl3'];
  const lvlBar = el('div', { class: 'lvl-bar', 'aria-hidden': 'true' }, [1, 2, 3].map(k => el('span', { class: ten && ten.level >= k ? 'on' : '' })));
  main.appendChild(panel('04', t('ck.gym'), el('div', {},
    el('div', { class: 'gauges' },
      gauge(S.data.visits?.last_30_days || 0, 20, t('ck.att30'), t('ck.visits')),
      gauge(streak.current, Math.max(4, streak.best), t('ck.streakNow'), t('ck.weeks')),
      gauge(h.sessions_completed || 0, Math.max(10, h.sessions_completed || 0), t('ck.sessions'), t('ck.done'))),
    el('div', { class: 'tenure' },
      el('div', {}, el('span', { class: 'ro-k' }, t('ck.tenure')),
        el('span', { class: 'lvl-name' }, ten ? t(levels[ten.level]) : t('ck.pending')),
        el('span', { class: 'ro-sub' }, ten ? t('ck.since', { d: fmtDay(m.start_date), m: ten.months }) : '')),
      lvlBar,
      ten && ten.level < 3 ? el('span', { class: 'ro-sub' }, t('ck.nextLevel', { lvl: t(levels[ten.level + 1]), m: ten.nextIn })) : null),
    el('div', { class: 'ro-grid g2 records' },
      readout(t('ck.recMonth'), h.best_month ? h.best_month.visits : '', h.best_month ? t('ck.visitsIn', { m: monthName(h.best_month.month) }) : null, { pending: !h.best_month }),
      readout(t('ck.recStreak'), streak.best || '', streak.best ? t('ck.weeks') : null, { pending: !streak.best }),
      readout(t('ck.recSession'), h.longest_session_min || '', 'min', { pending: !h.longest_session_min }),
      readout(t('ck.recComp'), '', null, { pending: true })),
    el('p', { class: 'ro-sub m0' }, t('ck.compNote')))));

  // 05 · MEDALLAS
  const md = medals(h, streak);
  main.appendChild(panel('05', t('ck.medals'), el('div', { class: 'medals' }, md.map(x =>
    el('div', { class: `medal ${x.got ? 'got' : ''}`, title: t(`md.${x.id}.d`) },
      medalIcon(x.id), el('span', { class: 'medal-n' }, t(`md.${x.id}`)), el('span', { class: 'medal-v' }, x.got ? (x.v && !String(x.v).includes('/') ? x.v : t('ck.earned')) : (x.v || t('ck.locked')))))),
    { extra: el('span', { class: 'panel-meta' }, `${md.filter(x => x.got).length}/${md.length}`) }));

  // 06 · PRÓXIMOS PASOS (evaluación + cita)
  const st = evalState();
  const next = upcoming()[0];
  main.appendChild(panel('06', t('ck.next'), el('div', { class: 'ro-grid g2' },
    el('a', { class: 'ctl', href: '#/evaluation' },
      el('span', { class: 'ro-k' }, t('ck.evaluation')),
      el('span', { class: 'ctl-v' }, st.initial ? (st.dueNow ? t('ck.evalDue') : t('ck.evalNext', { d: fmtDate(st.due, { month: 'short', day: 'numeric' }) })) : t('ev.pending')),
      el('span', { class: 'ctl-go' }, st.initial ? t('ev.view') : (st.draft ? t('ev.continue') : t('ev.complete')))),
    next
      ? el('a', { class: 'ctl', href: '#/appts' }, el('span', { class: 'ro-k' }, t('home.next')),
          el('span', { class: 'ctl-v' }, `${fmtDate(next.date, { weekday: 'short', day: 'numeric', month: 'short' })} · ${fmtClock(next.start_time)}`),
          el('span', { class: 'ctl-go' }, next.service || t('appt.appointment')))
      : el('button', { class: 'ctl', type: 'button', onclick: requestModal }, el('span', { class: 'ro-k' }, t('home.next')),
          el('span', { class: 'ctl-v' }, t('ck.none')), el('span', { class: 'ctl-go' }, t('appt.request'))))));

  // 07 · ACCESO QR
  const qrBox = el('div', { class: 'qr-frame' }, el('div', { class: 'spinner' }));
  main.appendChild(panel('07', t('home.qr'), el('div', { class: 'qr-wrap' }, qrBox, el('p', { class: 'ro-sub center m0' }, t('home.qrNote')))));
  loadQr(qrBox);

  // 08 · GIMNASIO
  const tn = S.data.tenant;
  if (tn.phone || tn.address) {
    main.appendChild(panel('08', t('home.contact'), el('div', { class: 'ro-grid g2' },
      tn.phone ? el('a', { class: 'ctl', href: `tel:${tn.phone.replace(/[^\d+]/g, '')}` }, el('span', { class: 'ro-k' }, t('home.phone')), el('span', { class: 'ctl-v' }, tn.phone)) : null,
      tn.address ? el('div', { class: 'ctl' }, el('span', { class: 'ro-k' }, t('home.address')), el('span', { class: 'ctl-v small' }, tn.address)) : null)));
  }
}

const monthName = (ym) => { const [y, mo] = ym.split('-').map(Number); return new Intl.DateTimeFormat(S.lang === 'es' ? 'es-US' : 'en-US', { month: 'long', year: 'numeric', timeZone: 'UTC' }).format(new Date(Date.UTC(y, mo - 1, 1))); };

function anthroPanel(rerender) {
  const m = S.data.member;
  const b = bodyMetrics();
  const units = el('div', { class: 'seg', role: 'group', 'aria-label': t('prog.units') },
    el('button', { type: 'button', class: metric() ? '' : 'on', onclick: () => { S.units = 'imperial'; store.set('aita-member-units', 'imperial'); rerender(); } }, 'LB · IN'),
    el('button', { type: 'button', class: metric() ? 'on' : '', onclick: () => { S.units = 'metric'; store.set('aita-member-units', 'metric'); rerender(); } }, 'KG · CM'));
  const lab = (p, title) => ({ title, value: p ? showLength(p.curVal) : null, delta: p && !p.same ? `(${showLength(p.curVal - p.baseVal, { signed: true })})` : null });
  const row = (label, p, f) => el('div', { class: 'ab-row' },
    el('span', { class: 'ro-k' }, label),
    p ? el('span', { class: 'ab-v' }, f(p.baseVal)) : el('span', { class: 'ab-v pend' }, t('ck.pending')),
    p && !p.same ? el('span', { class: 'ab-v' }, f(p.curVal)) : el('span', { class: 'ab-v dim' }, '—'),
    p && !p.same ? el('span', { class: 'ab-v chg' }, (label === t('body.height') ? showLength : f)(p.curVal - p.baseVal, { signed: true })) : el('span', { class: 'ab-v dim' }, '—'));
  const sexAsk = !m.sex ? el('div', { class: 'sex-ask' }, el('span', { class: 'ro-sub' }, t('fig.askSex')),
    el('div', { class: 'btn-row' },
      el('button', { class: 'btn btn-sm', type: 'button', onclick: () => setSex('female', rerender) }, t('fig.female')),
      el('button', { class: 'btn btn-sm', type: 'button', onclick: () => setSex('male', rerender) }, t('fig.male')))) : null;
  return panel('03', t('ck.anthro'), el('div', {},
    el('div', { class: 'anthro' },
      el('div', { class: 'anthro-fig' }, figureSvg(m.sex, { arm: lab(b.arm, t('body.arm')), waist: lab(b.waist, t('body.waist')), leg: lab(b.leg, t('body.leg')) })),
      el('div', { class: 'anthro-w' },
        readout(t('ck.wStart'), b.weight ? showWeight(b.weight.baseVal) : '', null, { big: true, pending: !b.weight }),
        el('span', { class: 'ro-sub' }, b.weight ? fmtDay(b.weight.base.date) : ''),
        readout(t('ck.wNow'), b.weight && !b.weight.same ? showWeight(b.weight.curVal) : '—', null, { big: true }),
        el('span', { class: 'ro-sub' }, b.weight && !b.weight.same ? fmtDay(b.weight.cur.date) : ''),
        readout(t('body.height'), b.height ? showHeight(b.height.curVal) : '', null, { pending: !b.height }))),
    el('div', { class: 'ab' },
      el('div', { class: 'ab-row ab-head' }, el('span', {}, ''), el('span', {}, t('prog.start')), el('span', {}, t('prog.now')), el('span', {}, t('prog.change'))),
      row(t('body.weight'), b.weight, showWeight), row(t('body.waist'), b.waist, showLength),
      row(t('body.arm'), b.arm, showLength), row(t('body.leg'), b.leg, showLength)),
    sexAsk,
    el('p', { class: 'ro-sub m0' }, t('fig.note')),
    el('div', { class: 'btn-row' }, el('a', { class: 'btn btn-sm', href: '#/progress' }, t('prog.open')))),
  { extra: units });
}

async function setSex(sex, rerender) {
  const { error } = await S.sb.rpc('member_set_sex', { p_sex: sex });
  if (error) return toast(errText(error), 'error');
  S.data.member.sex = sex; toast(t('fig.saved')); rerender();
}

function dobModal(rerender) {
  const input = el('input', { class: 'input', type: 'date', max: S.data.today, min: '1900-01-01' });
  const err = el('p', { class: 'form-error', role: 'alert' });
  openModal(t('ck.addDob'), el('div', { class: 'form' }, el('label', { class: 'field' }, el('span', {}, t('ck.dob')), input), err),
    [(close) => el('button', { class: 'btn btn-primary', type: 'button', onclick: async (e) => {
      if (!input.value) { err.textContent = t('ck.dobInvalid'); return; }
      e.target.disabled = true;
      const { error } = await S.sb.rpc('member_set_birthdate', { p_date: input.value });
      if (error) { e.target.disabled = false; err.textContent = /invalid/i.test(error.message) ? t('ck.dobInvalid') : errText(error); return; }
      close(); toast(t('fig.saved')); await rerender(true);
    } }, t('log.save'))]);
}
