// Contabilidad básica: resumen, gráficas, movimientos y pendientes.
// Los datos vienen del backend (/api/manager/accounting/*), que valida rol y
// tenant. Los pagos de socios (módulo Payments) se muestran aquí como ingresos
// sin duplicarlos; se editan desde Payments.
import { el, clear, card, kpi, table, tabs, badge, empty, errorBox, loading, money, fmtDate, todayISO, toast } from '../ui.js';
import { api } from '../api.js';
import { t, getLang, setLang, errText } from './accounting-i18n.js';
import { transactionModal, attachReceiptModal, cancelModal, obligationModal, payModal, exportModal, categoriesModal } from './accounting-forms.js';

const A = api.accounting;
const state = { start: null, end: null, filter: 'all' };

export const navLabel = () => t('nav');

function monthStart(iso) { return `${iso.slice(0, 8)}01`; }
function shiftMonth(iso, n) {
  const [y, m] = iso.split('-').map(Number);
  const d = new Date(Date.UTC(y, m - 1 + n, 1));
  return d.toISOString().slice(0, 10);
}
function monthEnd(iso) { return new Date(Date.parse(shiftMonth(iso, 1)) - 86400000).toISOString().slice(0, 10); }

export async function render(root, ctx) {
  const canEdit = ['owner', 'manager'].includes(ctx.role);
  if (!state.start) { state.start = monthStart(todayISO()); state.end = todayISO(); }
  let categories = [];

  // ----- encabezado -----
  const from = el('input', { class: 'input', type: 'date', value: state.start, 'aria-label': t('from') });
  const to = el('input', { class: 'input', type: 'date', value: state.end, 'aria-label': t('to') });
  const apply = () => {
    if (!from.value || !to.value) return;
    if (from.value > to.value) { toast(errText({ code: 'invalid_range' }), 'error'); return; }
    state.start = from.value; state.end = to.value; reload();
  };
  from.addEventListener('change', apply); to.addEventListener('change', apply);
  const preset = (label, s, e) => el('button', { class: 'btn btn-sm', type: 'button', onclick: () => { from.value = s; to.value = e; apply(); } }, label);
  const today = todayISO();
  const langBtn = el('button', { class: 'btn btn-sm', type: 'button', 'aria-label': 'Language / Idioma',
    onclick: () => { setLang(getLang() === 'es' ? 'en' : 'es'); render(clear(root), ctx); } }, getLang() === 'es' ? 'English' : 'Español');

  root.appendChild(el('div', { class: 'page-head' },
    el('div', {}, el('h1', {}, t('title')), el('p', {}, t('subtitle'))),
    el('div', { class: 'btn-row' },
      el('button', { class: 'btn btn-accent', type: 'button', onclick: () => transactionModal(ctx, { type: 'income', categories }, reload) }, `+ ${t('addIncome')}`),
      el('button', { class: 'btn btn-primary', type: 'button', onclick: () => transactionModal(ctx, { type: 'expense', categories }, reload) }, `+ ${t('addExpense')}`),
      canEdit ? el('button', { class: 'btn', type: 'button', onclick: () => exportModal(ctx, { start: state.start, end: state.end }) }, t('export')) : null)));

  root.appendChild(el('section', { class: 'card acc-range' },
    el('label', { class: 'acc-date' }, el('span', {}, t('from')), from),
    el('label', { class: 'acc-date' }, el('span', {}, t('to')), to),
    el('div', { class: 'btn-row' },
      preset(t('thisMonth'), monthStart(today), today),
      preset(t('lastMonth'), shiftMonth(today, -1), monthEnd(shiftMonth(today, -1))),
      preset(t('thisYear'), `${today.slice(0, 4)}-01-01`, today),
      canEdit ? el('button', { class: 'btn btn-sm', type: 'button', onclick: () => categoriesModal(ctx, categories, reload) }, t('categories')) : null,
      langBtn)));
  if (!canEdit) root.appendChild(el('p', { class: 'hint' }, t('staffNote')));

  const kpis = el('div', { class: 'kpis acc-kpis' }, loading());
  const monthlyBody = el('div', { class: 'card-body' }, loading());
  const catBody = el('div', { class: 'card-body' }, loading());
  const tabsHolder = el('div');
  const movBody = el('div', {}, loading());
  const pendBody = el('div', {}, loading());
  root.append(kpis,
    el('div', { class: 'grid-2' }, card(t('chartMonthly'), monthlyBody), card(t('chartCategories'), catBody)),
    el('section', { class: 'card' }, el('div', { class: 'card-head' }, el('h2', {}, t('movements'))),
      el('div', { class: 'toolbar' }, tabsHolder), movBody),
    card(t('pendingTitle'), pendBody, canEdit ? el('button', { class: 'btn btn-sm', type: 'button', onclick: () => obligationModal(ctx, reload) }, t('addPending')) : null));

  let movements = [];
  const drawTabs = () => clear(tabsHolder).appendChild(tabs(
    [{ value: 'all', label: t('all') }, { value: 'income', label: t('income') }, { value: 'expense', label: t('expenses') }],
    state.filter, (v) => { state.filter = v; drawTabs(); drawMovements(); }));
  drawTabs();

  function drawMovements() {
    const rows = state.filter === 'all' ? movements : movements.filter(m => m.type === state.filter);
    clear(movBody).appendChild(table([
      { label: t('date'), render: m => fmtDate(m.date) },
      { label: t('type'), render: m => el('span', { class: `acc-type ${m.type}` }, t(`t_${m.type}`)) },
      { label: t('category'), render: m => m.category_name || el('span', { class: 'muted' }, t('noCategory')) },
      { label: t('description'), render: m => el('span', {}, m.description, m.kind === 'payment' ? el('span', { class: 'badge gold' }, t('fromPayments')) : null) },
      { label: t('method'), render: m => t(`m_${m.payment_method}`) },
      { label: t('status'), render: m => badge(m.status, t(`s_${m.status}`)) },
      { label: t('amount'), num: true, render: m => el('span', { class: m.status === 'cancelled' ? 'muted strike' : `acc-amt ${m.type}` }, `${m.type === 'expense' ? '−' : ''}${money(m.amount)}`) },
      { label: t('receipt'), render: m => m.has_receipt
        ? el('button', { class: 'btn btn-sm', type: 'button', onclick: () => openReceipt(ctx, m) }, t('view'))
        : (m.kind === 'transaction' && m.status !== 'cancelled' && (canEdit || m.created_by === ctx.user.id)
          ? el('button', { class: 'btn btn-sm', type: 'button', onclick: () => attachReceiptModal(ctx, m, reload) }, t('attach')) : null) },
      { label: t('actions'), render: m => {
        if (m.kind === 'payment') return el('a', { class: 'btn btn-sm', href: '#/payments' }, t('fromPayments'));
        if (!canEdit || m.status === 'cancelled') return null;
        return el('div', { class: 'row-actions' },
          el('button', { class: 'btn btn-sm', type: 'button', onclick: () => transactionModal(ctx, { existing: m, categories }, reload) }, t('edit')),
          el('button', { class: 'btn btn-sm btn-danger', type: 'button', onclick: () => cancelModal(ctx, m, reload) }, t('cancel')));
      } },
    ], rows, { emptyText: t('noMovements') }));
  }

  async function reload() {
    if (!ctx.isCurrent()) return;
    [kpis, monthlyBody, catBody, movBody, pendBody].forEach(n => clear(n).appendChild(loading()));
    const [sum, tx, ob, cats] = await Promise.allSettled([
      A.summary(ctx.tenantId, state.start, state.end), A.transactions(ctx.tenantId, state.start, state.end),
      A.obligations(ctx.tenantId), A.categories(ctx.tenantId)]);
    if (!ctx.isCurrent()) return;
    if (cats.status === 'fulfilled') categories = cats.value.categories;
    const fail = (node, r) => clear(node).appendChild(errorBox({ message: errText(r.reason) }));

    if (sum.status === 'rejected') { [kpis, monthlyBody, catBody].forEach(n => fail(n, sum)); } else {
      const s = sum.value;
      const delta = (k) => s.change_pct[k] === null ? t('noPrev') : `${s.change_pct[k] > 0 ? '+' : ''}${s.change_pct[k]}% ${t('vsPrev')}`;
      const bal = (b) => b.overdue_count ? t('overdueN', b.overdue_count) : t('openN', b.count);
      clear(kpis).append(
        kpi(t('income'), money(s.income), delta('income')),
        kpi(t('expenses'), money(s.expenses), delta('expenses')),
        kpi(t('net'), money(s.net), delta('net'), true),
        kpi(t('receivable'), money(s.balances.receivable.total), bal(s.balances.receivable)),
        kpi(t('payable'), money(s.balances.payable.total), bal(s.balances.payable)));
      if (s.net < 0) kpis.children[2].classList.add('negative');
      clear(monthlyBody).appendChild(monthlyChart(s.monthly));
      clear(catBody).appendChild(categoryChart(s.by_category.expense));
    }
    if (tx.status === 'rejected') fail(movBody, tx); else { movements = tx.value.movements; drawMovements(); }
    if (ob.status === 'rejected') fail(pendBody, ob); else drawPending(pendBody, ob.value, ctx, canEdit, reload);
  }
  await reload();
}

async function openReceipt(ctx, m) {
  const w = window.open('', '_blank');
  try {
    const b = await A.receipt(ctx.tenantId, m.id);
    const url = URL.createObjectURL(b);
    if (w) w.location = url; else window.location.assign(url);
    setTimeout(() => URL.revokeObjectURL(url), 60000);
  } catch (e) { if (w) w.close(); toast(errText(e), 'error'); }
}

function drawPending(body, data, ctx, canEdit, reload) {
  const rows = [
    ...data.obligations.map(o => ({ ...o, kind: 'obligation' })),
    ...data.member_payments.map(p => ({ id: p.id, kind: 'payment', obligation_type: 'receivable', counterparty: p.member_name || t('memberPayment'),
      description: p.description || t('memberPayment'), amount: p.amount, due_date: p.due_date, effective_status: p.effective_status })),
  ].sort((a, b) => String(a.due_date || '9999').localeCompare(String(b.due_date || '9999')));
  if (!rows.length) { clear(body).appendChild(empty(t('noPending'))); return; }
  clear(body).appendChild(table([
    { label: t('who'), render: o => el('span', { class: 'strong' }, o.counterparty) },
    { label: t('type'), render: o => el('span', { class: `badge ${o.obligation_type === 'receivable' ? 'info' : 'gold'}` }, t(o.obligation_type === 'receivable' ? 'toCollect' : 'toPay')) },
    { label: t('description'), key: 'description' },
    { label: t('due'), render: o => o.due_date ? el('span', { class: o.effective_status === 'overdue' ? 'acc-late' : '' }, fmtDate(o.due_date)) : null },
    { label: t('status'), render: o => badge(o.effective_status, o.effective_status === 'overdue' ? `⚠ ${t('late')}` : t('s_pending')) },
    { label: t('amount'), num: true, render: o => money(o.amount) },
    { label: t('actions'), render: o => o.kind === 'payment'
      ? el('a', { class: 'btn btn-sm', href: '#/payments' }, t('fromPayments'))
      : (canEdit ? el('button', { class: 'btn btn-sm', type: 'button', onclick: () => payModal(ctx, o, reload) }, t('markPaid')) : null) },
  ], rows));
}

// ---------- gráficas (SVG propio: la CSP no permite librerías externas) ----------
const NS = 'http://www.w3.org/2000/svg';
function svg(tag, attrs = {}, text) {
  const n = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  if (text !== undefined) n.textContent = text;
  return n;
}
function shortMoney(v) {
  return Math.abs(v) >= 1000 ? `$${(v / 1000).toFixed(v >= 10000 ? 0 : 1)}k` : `$${Math.round(v)}`;
}
function monthName(ym) {
  const [y, m] = ym.split('-').map(Number);
  return new Intl.DateTimeFormat(getLang() === 'es' ? 'es-US' : 'en-US', { month: 'short', timeZone: 'UTC' }).format(new Date(Date.UTC(y, m - 1, 1)));
}

function monthlyChart(series) {
  const max = Math.max(0, ...series.flatMap(s => [s.income, s.expense]));
  if (!max) return empty(t('noChart'));
  const W = 600, H = 240, top = 16, bottom = 28, left = 8, slot = (W - left) / series.length, bw = Math.min(28, slot / 3);
  const chart = svg('svg', { viewBox: `0 0 ${W} ${H}`, class: 'acc-chart', role: 'img', 'aria-label': t('chartMonthly') });
  const y = (v) => top + (H - top - bottom) * (1 - v / max);
  chart.appendChild(svg('line', { x1: 0, x2: W, y1: H - bottom, y2: H - bottom, class: 'axis' }));
  series.forEach((s, i) => {
    const cx = left + slot * i + slot / 2;
    [['income', -bw - 2], ['expense', 2]].forEach(([k, dx]) => {
      const g = svg('g');
      g.appendChild(svg('title', {}, `${monthName(s.month)} · ${t(k === 'income' ? 'income' : 'expenses')}: ${money(s[k])}`));
      g.appendChild(svg('rect', { x: cx + dx, y: y(s[k]), width: bw, height: Math.max(0, H - bottom - y(s[k])), rx: 3, class: `bar ${k}` }));
      chart.appendChild(g);
    });
    chart.appendChild(svg('text', { x: cx, y: H - 8, 'text-anchor': 'middle', class: 'lbl' }, monthName(s.month)));
  });
  chart.appendChild(svg('text', { x: W - 4, y: 12, 'text-anchor': 'end', class: 'lbl' }, shortMoney(max)));
  return el('div', {}, chart, el('div', { class: 'acc-legend' },
    el('span', { class: 'dot income' }), t('income'), el('span', { class: 'dot expense' }), t('expenses')));
}

function categoryChart(rows) {
  if (!rows.length) return empty(t('noChart'));
  const max = rows[0].total;
  const total = rows.reduce((a, r) => a + r.total, 0);
  return el('ul', { class: 'acc-cats' }, rows.slice(0, 8).map(r => {
    const bar = svg('svg', { viewBox: '0 0 100 8', preserveAspectRatio: 'none', class: 'acc-catbar', 'aria-hidden': 'true' });
    bar.appendChild(svg('rect', { x: 0, y: 0, width: 100, height: 8, rx: 4, class: 'track' }));
    bar.appendChild(svg('rect', { x: 0, y: 0, width: Math.max(2, (r.total / max) * 100), height: 8, rx: 4, class: 'bar expense' }));
    return el('li', {},
      el('div', { class: 'acc-catrow' }, el('span', {}, r.name),
        el('span', { class: 'strong' }, `${money(r.total)} · ${Math.round((r.total / total) * 100)}%`)),
      bar);
  }));
}
