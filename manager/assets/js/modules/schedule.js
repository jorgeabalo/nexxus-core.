import { el, clear, card, table, tabs, badge, empty, errorBox, loading, fmtDate, fmtClock, humanize, openModal, field, input, select, toast, todayISO, weekdayOf, num } from '../ui.js';
import { api } from '../api.js';

const RANGES = [
  { value: 'today', label: 'Today' },
  { value: 'tomorrow', label: 'Tomorrow' },
  { value: 'week', label: 'This week' },
];
const SOURCES = { claudia: 'Claudia', manager: 'Manager', member_portal: 'Member Portal', web: 'Web' };
const state = { range: 'today' };

function rangeDates(range) {
  const today = todayISO();
  if (range === 'today') return [today, today];
  if (range === 'tomorrow') { const t = todayISO(1); return [t, t]; }
  // Semana actual lunes-domingo en la zona del tenant
  const wd = weekdayOf(today);            // 0=domingo
  const toMonday = wd === 0 ? -6 : 1 - wd;
  return [todayISO(toMonday), todayISO(toMonday + 6)];
}

export async function render(root, ctx) {
  root.appendChild(el('div', { class: 'page-head' },
    el('div', {}, el('h1', {}, 'Schedule'), el('p', {}, 'Appointments from every source: Claudia, managers, member portal and web.')),
    el('button', { class: 'btn btn-primary', type: 'button', onclick: () => appointmentModal(ctx, null, reload) }, '+ New appointment')));

  const tabsHolder = el('div');
  const label = el('span', { class: 'muted small' });
  const body = el('div', {}, loading());
  root.appendChild(el('section', { class: 'card' }, el('div', { class: 'toolbar' }, tabsHolder, label), body));
  const drawTabs = () => clear(tabsHolder).appendChild(tabs(RANGES, state.range, (v) => { state.range = v; drawTabs(); reload(); }));
  drawTabs();

  async function reload() {
    const [from, to] = rangeDates(state.range);
    label.textContent = from === to ? fmtDate(from, { weekday: 'long', month: 'short', day: 'numeric' })
      : `${fmtDate(from, { month: 'short', day: 'numeric' })} – ${fmtDate(to, { month: 'short', day: 'numeric' })}`;
    clear(body).appendChild(loading());
    try {
      const rows = await api.appointments(ctx.tenantId, from, to);
      if (!ctx.isCurrent()) return;
      const cols = [
        { label: 'Time', render: a => el('span', { class: 'strong' }, fmtClock(a.start_time) + (a.end_time ? ` – ${fmtClock(a.end_time)}` : '')) },
        { label: 'Member / client', render: a => el('span', {}, a.client_display || '—', a.member_code ? el('span', { class: 'muted' }, ` · ${a.member_code}`) : null) },
        { label: 'Service', key: 'service' },
        { label: 'Trainer / staff', key: 'staff_name' },
        { label: 'Status', render: a => badge(a.status) },
        { label: 'Source', render: a => badge(a.source, SOURCES[a.source] || humanize(a.source)) },
        { label: 'Actions', render: a => actions(ctx, a, reload) },
      ];
      if (state.range === 'week') cols.unshift({ label: 'Day', render: a => fmtDate(a.appointment_date, { weekday: 'short', month: 'short', day: 'numeric' }) });
      clear(body).appendChild(table(cols, rows, { emptyText: 'No appointments in this period' }));
      if (rows.length) label.textContent += ` · ${num(rows.length)} appointments`;
    } catch (e) { clear(body).appendChild(errorBox(e)); }
  }
  await reload();
}

function actions(ctx, a, reload) {
  if (!['scheduled', 'confirmed'].includes(a.status)) return null;
  const btn = (text, onclick, cls = '') => el('button', { class: `btn btn-sm ${cls}`, type: 'button', onclick }, text);
  return el('div', { class: 'row-actions' },
    a.status === 'scheduled' ? btn('Confirm', async (e) => {
      e.target.disabled = true;
      try { await api.updateAppointment(ctx.tenantId, a.id, { status: 'confirmed', confirmed_at: new Date().toISOString() }); toast('Appointment confirmed'); reload(); }
      catch (err) { e.target.disabled = false; toast(err.message, 'error'); }
    }) : null,
    btn('Reschedule', () => rescheduleModal(ctx, a, reload)),
    btn('Cancel', () => cancelModal(ctx, a, reload), 'btn-danger'));
}

async function appointmentModal(ctx, _unused, reload) {
  let members = [], services = [], staff = [];
  try {
    [members, services, staff] = await Promise.all([api.memberOptions(ctx.tenantId), api.services(ctx.tenantId), api.staff(ctx.tenantId)]);
  } catch (e) { return toast(e.message, 'error'); }

  const who = select([{ value: 'member', label: 'Existing member', selected: true }, { value: 'guest', label: 'Non-member (name + phone)' }]);
  const member = select([{ value: '', label: members.length ? 'Select a member…' : 'No members yet' },
    ...members.map(m => ({ value: m.id, label: [m.first_name, m.last_name].filter(Boolean).join(' ') + (m.member_id ? ` · ${m.member_id}` : '') }))]);
  const guestName = input({ autocomplete: 'off' });
  const guestPhone = input({ type: 'tel', autocomplete: 'off' });
  const service = select([{ value: '', label: services.length ? 'Select a service…' : 'No services configured' },
    ...services.map(s => ({ value: s.name, label: s.name + (s.duration_minutes ? ` (${s.duration_minutes} min)` : ''), duration: s.duration_minutes }))]);
  const trainer = select([{ value: '', label: 'Unassigned' }, ...staff.map(s => ({ value: s.id, label: [s.first_name, s.last_name].filter(Boolean).join(' ') + (s.role ? ` · ${s.role}` : '') }))]);
  const date = input({ type: 'date', value: todayISO(), required: true });
  const time = input({ type: 'time', required: true, step: '900' });
  const notes = el('textarea', { class: 'input', rows: '2' });
  const err = el('p', { class: 'form-error', role: 'alert' });

  const memberField = field('Member *', member, { full: true });
  const guestFields = [field('Client name *', guestName), field('Client phone', guestPhone)];
  const toggle = () => {
    const isMember = who.value === 'member';
    memberField.hidden = !isMember; guestFields.forEach(f => { f.hidden = isMember; });
  };
  who.addEventListener('change', toggle);

  const form = el('form', { class: 'form-grid', onsubmit: (e) => e.preventDefault() },
    field('Client type', who, { full: true }), memberField, ...guestFields,
    field('Service *', service), field('Trainer / staff', trainer),
    field('Date *', date), field('Time *', time), field('Notes', notes, { full: true }), err);
  toggle();

  openModal({
    title: 'New appointment', body: form, actions: [(close) => el('button', {
      class: 'btn btn-primary', type: 'button', onclick: async (e) => {
        err.textContent = '';
        const isMember = who.value === 'member';
        if (isMember && !member.value) { err.textContent = 'Select a member.'; return; }
        if (!isMember && !guestName.value.trim()) { err.textContent = 'Client name is required.'; return; }
        if (!service.value || !date.value || !time.value) { err.textContent = 'Service, date and time are required.'; return; }
        const svc = services.find(s => s.name === service.value);
        e.target.disabled = true;
        try {
          await api.createAppointment(ctx.tenantId, {
            member_id: isMember ? member.value : null,
            client_name: isMember ? null : guestName.value.trim(),
            client_phone: isMember ? null : (guestPhone.value.trim() || null),
            service: service.value, staff_id: trainer.value || null,
            appointment_date: date.value, start_time: time.value,
            end_time: svc && svc.duration_minutes ? addMinutes(time.value, svc.duration_minutes) : null,
            notes: notes.value.trim() || null, source: 'manager', status: 'scheduled',
          });
          close(); toast('Appointment created'); reload();
        } catch (ex) { e.target.disabled = false; err.textContent = ex.message; }
      },
    }, 'Create appointment')],
  });
}

function rescheduleModal(ctx, a, reload) {
  const date = input({ type: 'date', value: a.appointment_date, required: true });
  const time = input({ type: 'time', value: String(a.start_time).slice(0, 5), required: true, step: '900' });
  const err = el('p', { class: 'form-error', role: 'alert' });
  openModal({
    title: 'Reschedule appointment',
    body: el('div', { class: 'form-grid' },
      el('p', { class: 'hint full' }, `${a.client_display || ''} · ${a.service || ''}. The original appointment is kept in history as "rescheduled".`),
      field('New date', date), field('New time', time), err),
    actions: [(close) => el('button', {
      class: 'btn btn-primary', type: 'button', onclick: async (e) => {
        if (!date.value || !time.value) { err.textContent = 'Date and time are required.'; return; }
        e.target.disabled = true;
        const minutes = a.end_time ? diffMinutes(a.start_time, a.end_time) : null;
        try {
          await api.rescheduleAppointment(ctx.tenantId, a, {
            appointment_date: date.value, start_time: time.value, end_time: minutes ? addMinutes(time.value, minutes) : null,
          });
          close(); toast('Appointment rescheduled'); reload();
        } catch (ex) { e.target.disabled = false; err.textContent = ex.message; }
      },
    }, 'Save')],
  });
}

function cancelModal(ctx, a, reload) {
  const reason = el('textarea', { class: 'input', rows: '2' });
  openModal({
    title: 'Cancel appointment',
    body: el('div', { class: 'form-grid' },
      el('p', { class: 'hint full' }, `${a.client_display || ''} · ${fmtDate(a.appointment_date)} ${fmtClock(a.start_time)}`),
      field('Reason (optional)', reason, { full: true })),
    actions: [(close) => el('button', {
      class: 'btn btn-primary', type: 'button', onclick: async (e) => {
        e.target.disabled = true;
        try {
          await api.updateAppointment(ctx.tenantId, a.id, { status: 'cancelled', cancelled_at: new Date().toISOString(), cancel_reason: reason.value.trim() || null });
          close(); toast('Appointment cancelled'); reload();
        } catch (ex) { e.target.disabled = false; toast(ex.message, 'error'); }
      },
    }, 'Cancel appointment')],
  });
}

function addMinutes(hhmm, minutes) {
  const [h, m] = String(hhmm).split(':').map(Number);
  const total = Math.min(h * 60 + m + Number(minutes), 23 * 60 + 59);
  return `${String(Math.floor(total / 60)).padStart(2, '0')}:${String(total % 60).padStart(2, '0')}`;
}
function diffMinutes(a, b) {
  const t = (x) => { const [h, m] = String(x).split(':').map(Number); return h * 60 + m; };
  return Math.max(0, t(b) - t(a));
}
