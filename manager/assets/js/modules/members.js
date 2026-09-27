import { el, clear, card, table, tabs, badge, empty, errorBox, loading, fmtDate, fmtDateTime, fmtClock, money, num, icon, debounce, openModal, field, input, select, toast } from '../ui.js';
import { api } from '../api.js';

const FILTERS = [
  { value: 'all', label: 'All' },
  { value: 'active', label: 'Active' },
  { value: 'inactive', label: 'Inactive' },
  { value: 'past_due', label: 'Past due' },
  { value: 'new', label: 'New' },
];
const state = { search: '', filter: 'all' };

export async function render(root, ctx) {
  if (ctx.params[0]) return renderDetail(root, ctx, ctx.params[0]);

  root.appendChild(el('div', { class: 'page-head' },
    el('div', {}, el('h1', {}, 'Members'), el('p', {}, 'Search by name, member ID or phone.')),
    el('button', { class: 'btn btn-primary', type: 'button', onclick: () => newMemberModal(ctx) }, '+ New member')));

  const search = el('input', {
    class: 'input search', type: 'search', placeholder: 'Search name, member ID or phone', value: state.search,
    'aria-label': 'Search members',
    oninput: debounce((e) => { state.search = e.target.value; load(); }, 300),
  });
  const tabsHolder = el('div');
  const body = el('div', {}, loading());
  const count = el('span', { class: 'muted small' });
  root.appendChild(el('section', { class: 'card' }, el('div', { class: 'toolbar' }, search, tabsHolder, count), body));

  const drawTabs = () => clear(tabsHolder).appendChild(tabs(FILTERS, state.filter, (v) => { state.filter = v; drawTabs(); load(); }));
  drawTabs();

  let seq = 0;
  async function load() {
    const mine = ++seq;
    clear(body).appendChild(loading());
    try {
      const rows = await api.members(ctx.tenantId, state);
      if (mine !== seq || !ctx.isCurrent()) return;
      count.textContent = `${num(rows.length)} ${rows.length === 1 ? 'member' : 'members'}`;
      clear(body).appendChild(table([
        { label: 'Name', render: r => el('span', { class: 'strong' }, r.full_name || '—') },
        { label: 'Member ID', key: 'member_code' },
        { label: 'Status', render: r => badge(r.membership_status) },
        { label: 'Last visit', render: r => r.last_visit ? fmtDateTime(r.last_visit) : null },
        { label: 'Visits this month', num: true, render: r => num(r.visits_this_month) },
        { label: 'Next appointment', render: r => r.next_appointment_date ? `${fmtDate(r.next_appointment_date, { month: 'short', day: 'numeric' })} · ${fmtClock(r.next_appointment_time)}` : null },
        { label: 'Payment', render: r => badge(r.payment_status, r.payment_status === 'none' ? 'no payments' : null) },
      ], rows, {
        onRowClick: (r) => { location.hash = `#/members/${r.id}`; },
        emptyText: state.search || state.filter !== 'all' ? 'No members match this search' : 'No members yet',
      }));
    } catch (e) {
      if (mine === seq) clear(body).appendChild(errorBox(e));
    }
  }
  await load();
}

async function renderDetail(root, ctx, id) {
  root.appendChild(el('a', { class: 'back-link', href: '#/members' }, icon('back', 18), 'Members'));
  const holder = el('div', { class: 'stack' }, loading());
  root.appendChild(holder);
  let m, act;
  try {
    [m, act] = await Promise.all([api.member(ctx.tenantId, id), api.memberActivity(ctx.tenantId, id)]);
  } catch (e) { return clear(holder).appendChild(errorBox(e)); }
  if (!ctx.isCurrent()) return;
  clear(holder);
  if (!m) return holder.appendChild(card('Member', empty('Member not found')));

  holder.appendChild(el('div', { class: 'page-head' },
    el('div', {}, el('h1', {}, m.full_name || 'Member'),
      el('p', {}, [m.member_code ? `ID ${m.member_code}` : null, m.membership_type].filter(Boolean).join(' · ') || ' ')),
    el('div', { class: 'btn-row' }, badge(m.membership_status), badge(m.payment_status, m.payment_status === 'none' ? 'no payments' : null))));

  const dt = (k, v) => el('div', { class: 'dt' }, el('div', { class: 'k' }, k), el('div', { class: 'v' }, v ?? '—'));
  holder.appendChild(card('Profile', el('div', { class: 'detail-grid' },
    dt('Phone', m.phone ? el('a', { href: `tel:${m.phone}` }, m.phone) : null),
    dt('Email', m.email ? el('a', { href: `mailto:${m.email}` }, m.email) : null),
    dt('Member since', m.start_date ? fmtDate(m.start_date) : null),
    dt('Last visit', m.last_visit ? fmtDateTime(m.last_visit) : null),
    dt('Visits this month', num(m.visits_this_month)),
    dt('Next payment date', m.next_payment_date ? fmtDate(m.next_payment_date) : null),
    dt('Next appointment', m.next_appointment_date ? `${fmtDate(m.next_appointment_date)} · ${fmtClock(m.next_appointment_time)}` : null),
    dt('Service', m.next_appointment_service),
    dt('Notes', m.notes))));

  holder.appendChild(el('div', { class: 'grid-2' },
    card('Appointments', table([
      { label: 'Date', render: a => `${fmtDate(a.appointment_date, { month: 'short', day: 'numeric' })} · ${fmtClock(a.start_time)}` },
      { label: 'Service', key: 'service' },
      { label: 'Staff', key: 'staff_name' },
      { label: 'Status', render: a => badge(a.status) },
    ], act.appointments, { emptyText: 'No appointments' })),
    card('Payments', table([
      { label: 'Amount', num: true, render: p => money(p.amount) },
      { label: 'Due', render: p => p.due_date ? fmtDate(p.due_date) : null },
      { label: 'Status', render: p => badge(p.effective_status) },
      { label: 'Paid on', render: p => p.payment_date ? fmtDateTime(p.payment_date) : null },
    ], act.payments, { emptyText: 'No payments recorded' }))));

  holder.appendChild(card('Recent check-ins', table([
    { label: 'Date', render: c => fmtDateTime(c.check_in_time) },
    { label: 'Method', render: c => badge(c.method) },
  ], act.checkins, { emptyText: 'No check-ins recorded' })));
}

function newMemberModal(ctx) {
  const first = input({ required: true, autocomplete: 'off' });
  const last = input({ autocomplete: 'off' });
  const phone = input({ type: 'tel', autocomplete: 'off' });
  const email = input({ type: 'email', autocomplete: 'off' });
  const code = input({ autocomplete: 'off' });
  const type = input({ autocomplete: 'off', placeholder: 'e.g. Monthly' });
  const status = select([{ value: 'active', label: 'Active', selected: true }, { value: 'inactive', label: 'Inactive' }, { value: 'paused', label: 'Paused' }]);
  const err = el('p', { class: 'form-error', role: 'alert' });
  const form = el('form', { class: 'form-grid', onsubmit: (e) => e.preventDefault() },
    field('First name *', first), field('Last name', last),
    field('Phone', phone), field('Email', email),
    field('Member ID', code, { hint: 'Leave empty if you do not use member codes' }), field('Membership type', type),
    field('Status', status), err);
  openModal({
    title: 'New member', body: form, actions: [(close) => el('button', {
      class: 'btn btn-primary', type: 'button', onclick: async (e) => {
        err.textContent = '';
        if (!first.value.trim()) { err.textContent = 'First name is required.'; return; }
        e.target.disabled = true;
        try {
          const row = await api.createMember(ctx.tenantId, {
            first_name: first.value.trim(), last_name: last.value.trim() || null,
            phone: phone.value.trim() || null, email: email.value.trim().toLowerCase() || null,
            member_id: code.value.trim() || null, membership_type: type.value.trim() || null,
            membership_status: status.value,
          });
          close(); toast('Member created'); location.hash = `#/members/${row.id}`;
        } catch (ex) {
          e.target.disabled = false;
          err.textContent = /duplicate|unique/i.test(ex.message) ? 'That member ID is already in use.' : ex.message;
        }
      },
    }, 'Save member')],
  });
}
