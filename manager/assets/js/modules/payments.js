import { el, clear, card, table, tabs, badge, empty, errorBox, loading, money, num, fmtDate, fmtDateTime, humanize, openModal, field, input, select, toast, todayISO } from '../ui.js';
import { api } from '../api.js';

const FILTERS = [
  { value: 'all', label: 'All' },
  { value: 'paid', label: 'Paid' },
  { value: 'pending', label: 'Pending' },
  { value: 'overdue', label: 'Overdue' },
];
const METHODS = ['cash', 'card', 'zelle', 'check', 'transfer', 'other'];
const state = { filter: 'all' };

export async function render(root, ctx) {
  root.appendChild(el('div', { class: 'page-head' },
    el('div', {}, el('h1', {}, 'Payments'), el('p', {}, 'Recorded payments and charges. Online processors (Stripe / PayPal) will connect here later.')),
    el('button', { class: 'btn btn-primary', type: 'button', onclick: () => paymentModal(ctx, reload) }, '+ Record payment')));

  const summary = el('div', {}, loading());
  root.appendChild(card('Summary', summary));

  const tabsHolder = el('div');
  const count = el('span', { class: 'muted small' });
  const body = el('div', {}, loading());
  root.appendChild(el('section', { class: 'card' }, el('div', { class: 'toolbar' }, tabsHolder, count), body));
  const drawTabs = () => clear(tabsHolder).appendChild(tabs(FILTERS, state.filter, (v) => { state.filter = v; drawTabs(); loadTable(); }));
  drawTabs();

  async function loadSummary() {
    try {
      const d = await api.dashboard(ctx.tenantId);
      if (!ctx.isCurrent()) return;
      const p = d.payments;
      const stat = (label, value, meta) => el('div', { class: 'stat' }, el('div', { class: 'label' }, label), el('div', { class: 'value' }, value), el('div', { class: 'meta' }, meta));
      clear(summary).appendChild(el('div', { class: 'stats cols-3' },
        stat('Paid', money(p.paid_month.amount), `${num(p.paid_month.count)} this month`),
        stat('Pending', money(p.pending.amount), `${num(p.pending.count)} not yet due`),
        stat('Overdue', money(p.overdue.amount), `${num(p.overdue.count)} past due`)));
    } catch (e) { clear(summary).appendChild(errorBox(e)); }
  }

  async function loadTable() {
    clear(body).appendChild(loading());
    try {
      const rows = await api.payments(ctx.tenantId, state.filter);
      if (!ctx.isCurrent()) return;
      count.textContent = `${num(rows.length)} ${rows.length === 1 ? 'record' : 'records'}`;
      clear(body).appendChild(table([
        { label: 'Member', render: p => el('span', {}, el('span', { class: 'strong' }, p.member_name || '—'), p.member_code ? el('span', { class: 'muted' }, ` · ${p.member_code}`) : null) },
        { label: 'Amount', num: true, render: p => money(p.amount) },
        { label: 'Due date', render: p => p.due_date ? fmtDate(p.due_date) : null },
        { label: 'Status', render: p => badge(p.effective_status) },
        { label: 'Payment method', render: p => p.payment_method ? humanize(p.payment_method) : null },
        { label: 'Last payment', render: p => p.member_last_payment ? fmtDateTime(p.member_last_payment) : null },
        { label: 'Actions', render: p => p.payment_status === 'pending' ? el('div', { class: 'row-actions' },
          el('button', { class: 'btn btn-sm', type: 'button', onclick: () => markPaidModal(ctx, p, reload) }, 'Mark paid')) : null },
      ], rows, { onRowClick: p => { if (p.member_id) location.hash = `#/members/${p.member_id}`; }, emptyText: 'No payments recorded' }));
    } catch (e) { clear(body).appendChild(errorBox(e)); }
  }

  async function reload() { await Promise.all([loadSummary(), loadTable()]); }
  await reload();
}

async function paymentModal(ctx, reload) {
  let members = [];
  try { members = await api.memberOptions(ctx.tenantId); } catch (e) { return toast(e.message, 'error'); }
  if (!members.length) return toast('Add a member first', 'error');

  const member = select([{ value: '', label: 'Select a member…' }, ...members.map(m => ({ value: m.id, label: [m.first_name, m.last_name].filter(Boolean).join(' ') + (m.member_id ? ` · ${m.member_id}` : '') }))]);
  const amount = input({ type: 'number', min: '0', step: '0.01', inputmode: 'decimal', required: true });
  const status = select([{ value: 'paid', label: 'Paid now', selected: true }, { value: 'pending', label: 'Pending (charge due)' }]);
  const due = input({ type: 'date', value: todayISO() });
  const method = select([{ value: '', label: '—' }, ...METHODS.map(m => ({ value: m, label: humanize(m) }))]);
  const desc = input({ autocomplete: 'off', placeholder: 'e.g. Monthly membership — October' });
  const err = el('p', { class: 'form-error', role: 'alert' });

  openModal({
    title: 'Record payment',
    body: el('form', { class: 'form-grid', onsubmit: (e) => e.preventDefault() },
      field('Member *', member, { full: true }), field('Amount (USD) *', amount), field('Status', status),
      field('Due date', due), field('Payment method', method), field('Description', desc, { full: true }), err),
    actions: [(close) => el('button', {
      class: 'btn btn-primary', type: 'button', onclick: async (e) => {
        err.textContent = '';
        const value = Number(amount.value);
        if (!member.value) { err.textContent = 'Select a member.'; return; }
        if (!(value >= 0) || amount.value === '') { err.textContent = 'Enter a valid amount.'; return; }
        e.target.disabled = true;
        try {
          await api.createPayment(ctx.tenantId, {
            member_id: member.value, amount: value, payment_status: status.value,
            payment_date: status.value === 'paid' ? new Date().toISOString() : null,
            due_date: due.value || null, payment_method: method.value || null,
            description: desc.value.trim() || null, provider: 'manual',
          });
          close(); toast('Payment recorded'); reload();
        } catch (ex) { e.target.disabled = false; err.textContent = ex.message; }
      },
    }, 'Save')],
  });
}

function markPaidModal(ctx, p, reload) {
  const method = select([{ value: '', label: '—' }, ...METHODS.map(m => ({ value: m, label: humanize(m), selected: m === p.payment_method }))]);
  openModal({
    title: 'Mark as paid',
    body: el('div', { class: 'form-grid' },
      el('p', { class: 'hint full' }, `${p.member_name || ''} · ${money(p.amount)}${p.due_date ? ` · due ${fmtDate(p.due_date)}` : ''}`),
      field('Payment method', method, { full: true })),
    actions: [(close) => el('button', {
      class: 'btn btn-primary', type: 'button', onclick: async (e) => {
        e.target.disabled = true;
        try { await api.markPaid(ctx.tenantId, p.id, method.value); close(); toast('Marked as paid'); reload(); }
        catch (ex) { e.target.disabled = false; toast(ex.message, 'error'); }
      },
    }, 'Mark paid')],
  });
}
