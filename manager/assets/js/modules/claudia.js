import { el, clear, card, table, tabs, badge, empty, errorBox, loading, fmtDateTime, fmtDate, fmtClock, fmtDuration, humanize, icon, toast, num, todayISO, dayStartUTC } from '../ui.js';
import { api } from '../api.js';

const RANGES = [
  { value: 'today', label: 'Today' },
  { value: '7d', label: 'Last 7 days' },
  { value: '30d', label: 'Last 30 days' },
  { value: 'all', label: 'All' },
];
const state = { range: '7d' };

function since(range) {
  if (range === 'all') return null;
  const offset = range === 'today' ? 0 : range === '7d' ? -6 : -29;
  return dayStartUTC(todayISO(offset));
}
const yesNo = (v) => v ? badge('yes', 'Yes') : el('span', { class: 'muted' }, 'No');

export async function render(root, ctx) {
  if (ctx.params[0]) return renderCall(root, ctx, ctx.params[0]);

  root.appendChild(el('div', { class: 'page-head' },
    el('div', {}, el('h1', {}, 'Claudia · AI Receptionist'),
      el('p', {}, 'Every call Claudia answered. Summaries are generated when the call ends; full transcripts are not stored.'))));

  const tabsHolder = el('div');
  const label = el('span', { class: 'muted small' });
  const body = el('div', {}, loading());
  root.appendChild(el('section', { class: 'card' }, el('div', { class: 'toolbar' }, tabsHolder, label), body));
  const drawTabs = () => clear(tabsHolder).appendChild(tabs(RANGES, state.range, (v) => { state.range = v; drawTabs(); load(); }));
  drawTabs();

  async function load() {
    clear(body).appendChild(loading());
    try {
      const rows = await api.calls(ctx.tenantId, since(state.range));
      if (!ctx.isCurrent()) return;
      label.textContent = `${num(rows.length)} ${rows.length === 1 ? 'call' : 'calls'}`;
      clear(body).appendChild(table([
        { label: 'Date / time', render: c => el('span', { class: 'strong' }, fmtDateTime(c.started_at)) },
        { label: 'Caller', key: 'caller_display' },
        { label: 'Phone', key: 'caller_phone' },
        { label: 'Duration', num: true, render: c => c.duration_seconds === null ? null : fmtDuration(c.duration_seconds) },
        { label: 'Reason', render: c => c.intent ? humanize(c.intent) : null },
        { label: 'Outcome', render: c => c.outcome ? humanize(c.outcome) : null },
        { label: 'Appointment', render: c => c.appointment_id ? badge('yes', 'Booked') : null },
        { label: 'Transfer', render: c => yesNo(c.transferred) },
        { label: 'Follow-up', render: c => c.follow_up_required ? badge('pending', 'Required') : el('span', { class: 'muted' }, 'No') },
        { label: 'Status', render: c => badge(c.status) },
      ], rows, { onRowClick: (c) => { location.hash = `#/claudia/${c.id}`; }, emptyText: 'No calls recorded in this period' }));
    } catch (e) { clear(body).appendChild(errorBox(e)); }
  }
  await load();
}

async function renderCall(root, ctx, id) {
  root.appendChild(el('a', { class: 'back-link', href: '#/claudia' }, icon('back', 18), 'All calls'));
  const holder = el('div', { class: 'stack' }, loading());
  root.appendChild(holder);
  let c, links;
  try {
    c = await api.call(ctx.tenantId, id);
    links = c ? await api.callLinks(ctx.tenantId, c) : null;
  } catch (e) { return clear(holder).appendChild(errorBox(e)); }
  if (!ctx.isCurrent()) return;
  clear(holder);
  if (!c) return holder.appendChild(card('Call', empty('Call not found')));

  holder.appendChild(el('div', { class: 'page-head' },
    el('div', {}, el('h1', {}, c.caller_display || c.caller_phone || 'Unknown caller'),
      el('p', {}, `${fmtDateTime(c.started_at)} · ${c.duration_seconds === null ? 'duration pending' : fmtDuration(c.duration_seconds)}`)),
    el('div', { class: 'btn-row' },
      badge(c.status),
      c.follow_up_required ? el('button', {
        class: 'btn btn-accent', type: 'button', onclick: async (e) => {
          e.target.disabled = true;
          try { await api.markLeadContacted(ctx.tenantId, c); toast('Marked as contacted'); renderCall(clear(root), ctx, id); }
          catch (err) { e.target.disabled = false; toast(err.message, 'error'); }
        },
      }, 'Mark as contacted') : null)));

  holder.appendChild(card('Conversation summary',
    el('div', { class: 'card-body' }, c.summary
      ? el('p', { class: 'summary-text' }, c.summary)
      : el('p', { class: 'muted m0' }, c.status === 'in_progress' ? 'The call is still in progress or has not been closed yet.' : 'No summary available for this call.'))));

  const dt = (k, v) => el('div', { class: 'dt' }, el('div', { class: 'k' }, k), el('div', { class: 'v' }, v ?? '—'));
  holder.appendChild(card('Call details', el('div', { class: 'detail-grid' },
    dt('Phone', c.caller_phone ? el('a', { href: `tel:${c.caller_phone}` }, c.caller_phone) : null),
    dt('Reason', c.intent ? humanize(c.intent) : null),
    dt('Outcome', c.outcome ? humanize(c.outcome) : null),
    dt('Started', fmtDateTime(c.started_at)),
    dt('Ended', c.ended_at ? fmtDateTime(c.ended_at) : null),
    dt('Transferred', c.transferred ? 'Yes' : 'No'),
    dt('Follow-up required', c.follow_up_required ? 'Yes' : 'No'),
    dt('Handled by', humanize(c.handled_by)),
    dt('Call SID', el('span', { class: 'muted small' }, c.call_sid)))));

  holder.appendChild(el('div', { class: 'grid-2' },
    card('Lead', c.lead_id
      ? el('div', { class: 'detail-grid cols-2' },
        dt('Name', c.lead_name), dt('Status', badge(c.lead_status)),
        dt('Email', c.lead_email ? el('a', { href: `mailto:${c.lead_email}` }, c.lead_email) : null),
        dt('Reason', c.lead_reason))
      : empty('No contact details were left on this call')),
    card('Appointment', links.appointments.length
      ? table([
        { label: 'Date', render: a => `${fmtDate(a.appointment_date, { month: 'short', day: 'numeric' })} · ${fmtClock(a.start_time)}` },
        { label: 'Service', key: 'service' },
        { label: 'Status', render: a => badge(a.status) },
      ], links.appointments)
      : empty('No appointment linked to this call'))));

  if (links.alerts.length) {
    holder.appendChild(card('Alerts from this call', table([
      { label: 'Alert', key: 'title' },
      { label: 'Severity', render: a => badge(a.severity) },
      { label: 'Status', render: a => badge(a.status) },
      { label: 'Created', render: a => fmtDateTime(a.created_at) },
    ], links.alerts)));
  }
}
