// Plan de entrenamiento del socio (ficha del socio en Members).
// El staff elige, por día de la semana, máquinas/ejercicios con series x
// repeticiones (o minutos en cardio). Se guarda entero con
// manager_save_training_plan (una transacción; RLS por tenant).
import { el, clear, append, card, empty, errorBox, loading, fmtDate, fmtDateTime, openModal, field, input, select, tabs, toast } from '../ui.js';
import { api } from '../api.js';
import { EXERCISES, exercise, exerciseImg, exerciseName } from '/m/assets/js/exercises.js';
import { historyFor, miniChart, volume } from '/m/assets/js/exchart.js';

const DAYS = [1, 2, 3, 4, 5, 6, 7];
const DAY_SHORT = { 1: 'Mon', 2: 'Tue', 3: 'Wed', 4: 'Thu', 5: 'Fri', 6: 'Sat', 7: 'Sun' };
const DAY_LONG = { 1: 'Monday', 2: 'Tuesday', 3: 'Wednesday', 4: 'Thursday', 5: 'Friday', 6: 'Saturday', 7: 'Sunday' };
const MAX_PER_DAY = 20;

const dose = (it) => it.duration_min ? `${it.duration_min} min` : `${it.sets}×${it.reps}${it.weight_lb !== null && it.weight_lb !== undefined ? ` · ${Number(it.weight_lb)} lb` : ''}`;

export function trainingCard(ctx, m) {
  const holder = el('div', { class: 'card-body stack' }, loading());
  async function load() {
    let plan;
    try { plan = await api.trainingPlan(ctx.tenantId, m.id); } catch (e) { return clear(holder).appendChild(errorBox(e)); }
    const items = plan?.items || [];
    const byDay = {};
    for (const it of items) (byDay[it.day_of_week] ||= []).push(it);
    clear(holder).append(
      items.length
        ? el('div', { class: 'tp-week' }, DAYS.filter(d => byDay[d]).map(d => el('div', { class: 'tp-day' },
            el('div', { class: 'tp-day-h' }, DAY_LONG[d], el('span', { class: 'muted small' }, ` · ${byDay[d].length}`)),
            el('div', { class: 'tp-ex-list' }, byDay[d].map(it => exerciseSummary(it, plan.logs))))))
        : empty('No training plan yet. Create one so the member sees their workout for each day.'),
      plan?.notes ? el('p', { class: 'small m0' }, el('strong', {}, 'Notes: '), plan.notes) : null,
      el('div', { class: 'btn-row' },
        el('button', { class: 'btn btn-primary', type: 'button', onclick: () => editor(ctx, m, plan, load) }, items.length ? 'Edit plan' : 'Create plan'),
        plan?.updated_at ? el('span', { class: 'muted small' }, `Updated ${fmtDateTime(plan.updated_at)}`) : null));
  }
  load();
  return card('Training plan', holder);
}

// Una máquina del plan + gráfica de lo que el socio registró (últimas 8 sesiones).
function exerciseSummary(it, logs) {
  const hist = historyFor(logs, it);
  const last = hist[hist.length - 1];
  const cardio = exercise(it.exercise_key).cardio;
  return el('div', { class: 'tp-ex' },
    el('img', { src: exerciseImg(it.exercise_key), alt: '', width: 64, height: 48 }),
    el('div', { class: 'tp-ex-main' }, el('span', { class: 'strong' }, exerciseName(it, 'en')), el('span', { class: 'muted small' }, `Plan: ${dose(it)}`),
      last ? el('span', { class: 'muted small' }, `Last: ${fmtDate(last.date, { month: 'short', day: 'numeric' })} · ${dose(last)} · ${hist.length} session${hist.length === 1 ? '' : 's'}`)
        : el('span', { class: 'muted small' }, 'Not logged yet')),
    hist.length ? el('div', { class: 'tp-ex-chart', title: cardio ? 'Minutes per session' : 'Total reps per session (sets × reps)' },
      miniChart(hist.map(l => ({ date: l.date, value: volume(l), title: `${fmtDate(l.date)} · ${dose(l)}` })), { locale: 'en-US', label: `${exerciseName(it, 'en')} history` })) : null);
}

function editor(ctx, m, plan, reload) {
  const days = Object.fromEntries(DAYS.map(d => [d, []]));
  for (const it of plan?.items || []) days[it.day_of_week].push({ ...it });
  let day = DAYS.find(d => days[d].length) || 1;

  const title = input({ maxlength: 80, value: plan?.title || '', placeholder: 'e.g. Strength · Phase 1' });
  const notes = el('textarea', { class: 'input', maxlength: 1000, rows: 2, placeholder: 'General notes for the member (optional)' }, plan?.notes || '');
  const tabsBox = el('div');
  const list = el('div', { class: 'tp-rows' });
  const err = el('p', { class: 'form-error', role: 'alert' });

  const copyTo = select([{ value: '', label: 'Copy this day to…', selected: true }, ...DAYS.map(d => ({ value: String(d), label: DAY_LONG[d] }))], { 'aria-label': 'Copy this day to another day' });
  copyTo.addEventListener('change', () => {
    const to = Number(copyTo.value);
    copyTo.value = '';
    if (!to || to === day) return;
    days[to] = days[day].map(it => ({ ...it, day_of_week: to }));
    toast(`${DAY_LONG[day]} copied to ${DAY_LONG[to]}`);
    draw();
  });

  const num = (v, min, max) => { const n = Number(v); return v === '' || v === null || !Number.isFinite(n) ? null : Math.min(max, Math.max(min, Math.round(n))); };

  function row(it, i) {
    const ex = exercise(it.exercise_key);
    const img = el('img', { src: exerciseImg(it.exercise_key), alt: '', width: 64, height: 48 });
    const pick = select(EXERCISES.map(e => ({ value: e.key, label: e.en, selected: e.key === it.exercise_key })), { 'aria-label': 'Machine / exercise' });
    const name = input({ maxlength: 80, value: it.name || '', placeholder: it.exercise_key === 'other' ? 'Exercise name' : 'Custom name (optional)' });
    const sets = input({ type: 'number', min: 1, max: 20, inputmode: 'numeric', value: it.sets ?? '', 'aria-label': 'Sets' });
    const reps = input({ type: 'number', min: 1, max: 200, inputmode: 'numeric', value: it.reps ?? '', 'aria-label': 'Reps' });
    const mins = input({ type: 'number', min: 1, max: 300, inputmode: 'numeric', value: it.duration_min ?? '', 'aria-label': 'Minutes' });
    const weight = input({ type: 'number', min: 0, max: 2000, step: 0.5, inputmode: 'decimal', value: it.weight_lb ?? '', 'aria-label': 'Weight (lb)' });
    const note = input({ maxlength: 300, value: it.notes || '', placeholder: 'Notes (seat height, tempo…)' });
    pick.addEventListener('change', () => {
      it.exercise_key = pick.value;
      const cardio = exercise(it.exercise_key).cardio;
      if (cardio && !it.duration_min) { it.sets = it.reps = null; it.duration_min = 15; }
      if (!cardio && !it.sets) { it.duration_min = null; it.sets = 3; it.reps = 10; }
      draw();
    });
    name.addEventListener('input', () => { it.name = name.value; });
    sets.addEventListener('input', () => { it.sets = num(sets.value, 1, 20); });
    reps.addEventListener('input', () => { it.reps = num(reps.value, 1, 200); });
    mins.addEventListener('input', () => { it.duration_min = num(mins.value, 1, 300); });
    weight.addEventListener('input', () => { it.weight_lb = weight.value === '' ? null : Math.min(2000, Math.max(0, Number(weight.value))); });
    note.addEventListener('input', () => { it.notes = note.value; });
    const move = (dir) => { const j = i + dir; const a = days[day]; [a[i], a[j]] = [a[j], a[i]]; draw(); };
    return el('div', { class: 'tp-row' },
      el('div', { class: 'tp-row-img' }, img, el('span', { class: 'tp-n' }, String(i + 1))),
      el('div', { class: 'tp-row-fields' },
        el('div', { class: 'tp-line' }, field('Machine / exercise', pick), field('Name shown to member', name)),
        el('div', { class: 'tp-line' },
          ex.cardio ? field('Minutes', mins) : [field('Sets', sets), field('Reps', reps), field('Weight (lb)', weight)],
          field('Notes', note))),
      el('div', { class: 'tp-row-actions' },
        el('button', { class: 'icon-btn', type: 'button', title: 'Move up', 'aria-label': 'Move up', disabled: i === 0, onclick: () => move(-1) }, '↑'),
        el('button', { class: 'icon-btn', type: 'button', title: 'Move down', 'aria-label': 'Move down', disabled: i === days[day].length - 1, onclick: () => move(1) }, '↓'),
        el('button', { class: 'icon-btn btn-danger', type: 'button', title: 'Remove', 'aria-label': 'Remove exercise', onclick: () => { days[day].splice(i, 1); draw(); } }, '✕')));
  }

  function draw() {
    clear(tabsBox).appendChild(tabs(DAYS.map(d => ({ value: d, label: days[d].length ? `${DAY_SHORT[d]} (${days[d].length})` : DAY_SHORT[d] })), day, (v) => { day = v; draw(); }));
    append(clear(list), [
      el('div', { class: 'tp-list-head' }, el('h3', { class: 'm0' }, DAY_LONG[day]), days[day].length ? copyTo : null),
      days[day].length ? days[day].map(row) : el('p', { class: 'muted m0' }, 'Rest day — no exercises.'),
      days[day].length < MAX_PER_DAY ? el('button', { class: 'btn', type: 'button', onclick: () => {
        days[day].push({ day_of_week: day, exercise_key: 'leg_press', name: '', sets: 3, reps: 10, duration_min: null, weight_lb: null, notes: '' });
        draw();
      } }, '+ Add exercise') : el('p', { class: 'hint' }, `Maximum ${MAX_PER_DAY} exercises per day.`)]);
  }
  draw();

  const body = el('div', { class: 'stack' },
    el('div', { class: 'form-grid' }, field('Plan title', title), field('Notes for the member', notes)),
    tabsBox, list, err);

  openModal({
    title: `Training plan · ${m.full_name || 'Member'}`, body, actions: [(close) => el('button', {
      class: 'btn btn-primary', type: 'button', onclick: async (e) => {
        err.textContent = '';
        const items = DAYS.flatMap(d => days[d].map(it => ({
          day_of_week: d, exercise_key: it.exercise_key, name: (it.name || '').trim() || null,
          sets: exercise(it.exercise_key).cardio ? null : it.sets, reps: exercise(it.exercise_key).cardio ? null : it.reps,
          duration_min: exercise(it.exercise_key).cardio ? it.duration_min : null,
          weight_lb: exercise(it.exercise_key).cardio ? null : it.weight_lb, notes: (it.notes || '').trim() || null,
        })));
        const bad = items.find(it => it.duration_min === null && (!it.sets || !it.reps));
        if (bad) { day = bad.day_of_week; draw(); err.textContent = `${DAY_LONG[bad.day_of_week]}: every exercise needs sets and reps (or minutes for cardio).`; return; }
        const unnamed = items.find(it => it.exercise_key === 'other' && !it.name);
        if (unnamed) { day = unnamed.day_of_week; draw(); err.textContent = `${DAY_LONG[unnamed.day_of_week]}: write a name for "Other exercise".`; return; }
        e.target.disabled = true;
        try {
          await api.saveTrainingPlan(ctx.tenantId, m.id, { title: title.value, notes: notes.value, items });
          close(); toast('Training plan saved'); reload();
        } catch (x) { e.target.disabled = false; err.textContent = x.message || 'Could not save the plan'; }
      } }, 'Save plan')],
  });
  document.querySelector('#modal-root .modal')?.classList.add('modal-wide');
}
