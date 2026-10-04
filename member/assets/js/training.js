// Plan de entrenamiento del socio: lo diseña el staff en el Manager Panel.
// El plan es de solo lectura (member_training_plan devuelve únicamente el propio).
// El socio marca cada máquina como hecha (member_log_exercise) y ve una gráfica
// con sus últimas sesiones en esa máquina.
import { S, el, clear, t, card, empty, fmtDay, locale, showWeight, metric, LB_TO_KG, openModal, toast, errText } from './util.js';
import { exercise, exerciseImg, exerciseName } from './exercises.js';
import { historyFor, miniChart, volume } from './exchart.js';

let selected = null;   // día elegido (1 = lunes ... 7 = domingo); se recuerda entre vistas

// 1..7 (lunes..domingo) a partir de una fecha YYYY-MM-DD
const isoWeekday = (iso) => {
  const [y, m, d] = String(iso).slice(0, 10).split('-').map(Number);
  return ((new Date(Date.UTC(y, m - 1, d)).getUTCDay() + 6) % 7) + 1;
};
// 2024-01-01 fue lunes: sirve para nombrar los días en el idioma del socio
const dayName = (n, style) => new Intl.DateTimeFormat(locale(), { weekday: style, timeZone: 'UTC' }).format(new Date(Date.UTC(2024, 0, n)));
const dose = (x) => x.duration_min ? t('train.minutes', { n: x.duration_min })
  : (x.sets && x.reps) ? t('train.sets', { sets: x.sets, reps: x.reps }) : null;

export async function viewTraining(main) {
  const box = el('div', { class: 'stack' }, el('div', { class: 'boot-inline' }, el('div', { class: 'spinner' })));
  main.appendChild(box);
  const { data, error } = await S.sb.rpc('member_training_plan');
  if (!box.isConnected) return;
  if (error) { clear(box).appendChild(card(t('train.title'), empty(t('train.error')))); return; }
  if (!data || !(data.items || []).length) { clear(box).appendChild(card(t('train.title'), empty(t('train.none')))); return; }
  render(box, data);
}

function render(box, plan) {
  const todayIso = plan.today || S.data.today;
  const today = isoWeekday(todayIso);
  plan.logs ||= [];
  const byDay = {};
  for (const it of plan.items) (byDay[it.day_of_week] ||= []).push(it);
  if (!selected) selected = byDay[today] ? today : Number(Object.keys(byDay)[0]);

  const draw = () => {
    const items = byDay[selected] || [];
    clear(box).append(
      el('section', { class: 'card train-head' },
        el('div', { class: 'card-head' }, el('h2', {}, plan.title || t('train.title'))),
        plan.notes ? el('p', { class: 'muted' }, plan.notes) : null,
        plan.updated_at ? el('p', { class: 'small muted m0' }, t('train.updated', { date: fmtDay(plan.updated_at) })) : null),
      el('div', { class: 'days', role: 'tablist', 'aria-label': t('train.title') }, [1, 2, 3, 4, 5, 6, 7].map(n =>
        el('button', {
          class: `day${n === selected ? ' on' : ''}${byDay[n] ? ' has' : ''}${n === today ? ' today' : ''}`,
          type: 'button', role: 'tab', 'aria-selected': String(n === selected), 'aria-label': dayName(n, 'long'),
          onclick: () => { selected = n; draw(); },
        }, el('span', { class: 'day-n' }, dayName(n, 'short').replace('.', '')), n === today ? el('span', { class: 'day-t' }, t('train.today')) : null))),
      el('h3', { class: 'day-title' }, el('span', { class: 'day-name' }, dayName(selected, 'long')),
        items.length ? el('span', { class: 'muted small' }, ` · ${t('train.count', { n: items.length })}`) : null),
      items.length ? el('div', { class: 'ex-list' }, items.map((it, i) => exerciseCard(it, i + 1))) : el('section', { class: 'card' }, empty(t('train.rest'))));
  };

  // Guarda (o deshace) el registro de hoy y vuelve a pintar sin recargar todo.
  async function saveLog(it, values) {
    const { error } = await S.sb.rpc('member_log_exercise', {
      p_date: todayIso, p_exercise_key: it.exercise_key, p_name: it.name || '',
      p_sets: values.sets ?? null, p_reps: values.reps ?? null, p_weight_lb: values.weight_lb ?? null, p_duration_min: values.duration_min ?? null,
    });
    if (error) { toast(errText(error), 'error'); return false; }
    const name = String(it.name || '').trim();
    plan.logs = plan.logs.filter(l => !(l.date === todayIso && historyFor([l], it).length));
    plan.logs.push({ date: todayIso, exercise_key: it.exercise_key, name, ...values });
    toast(t('train.saved'));
    draw();
    return true;
  }
  async function undoLog(it) {
    const { error } = await S.sb.rpc('member_unlog_exercise', { p_date: todayIso, p_exercise_key: it.exercise_key, p_name: it.name || '' });
    if (error) { toast(errText(error), 'error'); return; }
    plan.logs = plan.logs.filter(l => !(l.date === todayIso && historyFor([l], it).length));
    draw();
  }

  function adjustModal(it, current) {
    const cardio = exercise(it.exercise_key).cardio;
    const base = current || it;
    const inp = (attrs) => el('input', { class: 'input', type: 'number', inputmode: 'decimal', ...attrs });
    const sets = inp({ min: 1, max: 20, value: base.sets ?? '' });
    const reps = inp({ min: 1, max: 200, value: base.reps ?? '' });
    const mins = inp({ min: 1, max: 300, value: base.duration_min ?? '' });
    const wShown = base.weight_lb === null || base.weight_lb === undefined ? '' : Math.round((metric() ? base.weight_lb * LB_TO_KG : Number(base.weight_lb)) * 2) / 2;
    const weight = inp({ min: 0, max: 2000, step: 0.5, value: wShown });
    const err = el('p', { class: 'form-error', role: 'alert' });
    const lab = (txt, i) => el('label', { class: 'field' }, el('span', {}, txt), i);
    const body = el('div', { class: 'form' }, el('p', { class: 'muted small m0' }, t('train.adjustHint')),
      cardio ? lab(t('train.minL'), mins)
        : [el('div', { class: 'grid-2' }, lab(t('train.setsL'), sets), lab(t('train.repsL'), reps)), lab(t('train.weightL', { u: metric() ? 'kg' : 'lb' }), weight)],
      err);
    const intIn = (v, lo, hi) => { const n = Number(v); return Number.isInteger(n) && n >= lo && n <= hi ? n : null; };
    openModal(`${t('train.adjustTitle')} · ${exerciseName(it, S.lang)}`, body, [(close) => el('button', { class: 'btn btn-primary', type: 'button', onclick: async (e) => {
      err.textContent = '';
      let values;
      if (cardio) {
        const m = intIn(mins.value, 1, 300);
        if (m === null) { err.textContent = t('train.invalid'); return; }
        values = { duration_min: m, sets: null, reps: null, weight_lb: null };
      } else {
        const s = intIn(sets.value, 1, 20), r = intIn(reps.value, 1, 200);
        if (s === null || r === null) { err.textContent = t('train.invalid'); return; }
        const w = weight.value === '' ? null : Number(weight.value);
        if (w !== null && !(w >= 0)) { err.textContent = t('train.invalid'); return; }
        values = { sets: s, reps: r, duration_min: null, weight_lb: w === null ? null : Math.round((metric() ? w / LB_TO_KG : w) * 10) / 10 };
      }
      e.target.disabled = true;
      if (await saveLog(it, values)) close(); else e.target.disabled = false;
    } }, t('train.save'))]);
  }

  function exerciseCard(it, n) {
    const name = exerciseName(it, S.lang);
    const hist = historyFor(plan.logs, it);
    const todayLog = hist.find(l => l.date === todayIso);
    const past = hist.filter(l => l.date !== todayIso);
    const last = past[past.length - 1];
    const cardio = exercise(it.exercise_key).cardio;
    const planned = { sets: it.sets ?? null, reps: it.reps ?? null, weight_lb: it.weight_lb ?? null, duration_min: it.duration_min ?? null };
    const isToday = selected === isoWeekday(todayIso);
    const pts = hist.map(l => ({ date: l.date, value: volume(l),
      title: `${fmtDay(l.date)} · ${dose(l)}${l.weight_lb !== null && l.weight_lb !== undefined ? ` · ${showWeight(l.weight_lb)}` : ''}` }));

    return el('article', { class: `ex${todayLog ? ' done' : ''}` },
      el('div', { class: 'ex-img' }, el('img', { src: exerciseImg(it.exercise_key), alt: exercise(it.exercise_key)[S.lang === 'en' ? 'en' : 'es'], width: 160, height: 120, loading: 'lazy' }),
        el('span', { class: 'ex-n' }, todayLog ? '✓' : String(n))),
      el('div', { class: 'ex-body' },
        el('h3', {}, name),
        dose(it) ? el('p', { class: 'ex-dose' }, dose(it)) : null,
        it.weight_lb !== null && it.weight_lb !== undefined ? el('p', { class: 'small muted m0' }, t('train.weight', { w: showWeight(it.weight_lb) })) : null,
        it.notes ? el('p', { class: 'small m0 ex-notes' }, it.notes) : null),
      el('div', { class: 'ex-track' },
        el('div', { class: 'ex-chart' },
          el('p', { class: 'ex-chart-h' }, t('train.history'), el('span', { class: 'muted' }, ` · ${cardio ? t('train.historyCardio') : t('train.historyReps')}`)),
          pts.length ? miniChart(pts, { locale: locale(), label: t('train.history') }) : el('p', { class: 'small muted m0' }, t('train.noHistory')),
          last ? el('p', { class: 'small muted m0' }, t('train.last', { d: fmtDay(last.date), v: [dose(last), last.weight_lb != null ? showWeight(last.weight_lb) : null].filter(Boolean).join(' · ') })) : null),
        isToday ? el('div', { class: 'ex-actions' }, todayLog
          ? [el('span', { class: 'badge ok' }, `✓ ${t('train.doneToday')}`),
             el('button', { class: 'btn btn-sm', type: 'button', onclick: () => adjustModal(it, todayLog) }, t('train.adjust')),
             el('button', { class: 'link-btn', type: 'button', onclick: () => undoLog(it) }, t('train.undo'))]
          : [el('button', { class: 'btn btn-primary btn-sm', type: 'button', onclick: async (e) => { e.target.disabled = true; if (!await saveLog(it, planned)) e.target.disabled = false; } }, t('train.markDone')),
             el('button', { class: 'btn btn-sm', type: 'button', onclick: () => adjustModal(it, null) }, t('train.adjust'))]) : null));
  }

  draw();
}

