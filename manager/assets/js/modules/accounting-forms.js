// Formularios (modales) del módulo Contabilidad.
import { el, clear, openModal, field, input, select, toast, money, todayISO, empty, badge } from '../ui.js';
import { api } from '../api.js';
import { t, errText, getLang, fmtDay } from './accounting-i18n.js';

export const METHODS = ['cash', 'card', 'bank', 'check', 'other'];
const A = api.accounting;

function saveButton(label, onSave) {
  return (close) => el('button', {
    class: 'btn btn-primary', type: 'button', onclick: async (e) => {
      e.target.disabled = true;
      try { await onSave(close); } finally { e.target.disabled = false; }
    },
  }, label);
}

// ---------- registrar / editar movimiento ----------
export function transactionModal(ctx, { type, existing = null, categories }, reload) {
  const typ = existing ? existing.type : type;
  const cats = categories.filter(c => c.type === typ && (c.active || c.id === existing?.category_id));
  const date = input({ type: 'date', value: existing?.date || todayISO(), required: true });
  const category = select([{ value: '', label: t('noCategory') },
    ...cats.map(c => ({ value: c.id, label: c.name, selected: c.id === existing?.category_id }))]);
  const desc = input({ autocomplete: 'off', maxlength: '300', placeholder: t('descPh'), value: existing?.description || '' });
  const amount = input({ type: 'number', min: '0.01', step: '0.01', inputmode: 'decimal', value: existing ? String(existing.amount) : '' });
  const method = select(METHODS.map(m => ({ value: m, label: t(`m_${m}`), selected: m === (existing?.payment_method || 'cash') })));
  const status = select([{ value: 'paid', label: t('paidNow'), selected: (existing?.status || 'paid') === 'paid' },
    { value: 'pending', label: t('pendingOpt'), selected: existing?.status === 'pending' }]);
  const notes = el('textarea', { class: 'input', maxlength: '2000' }, existing?.notes || '');
  const photo = input({ type: 'file', accept: 'image/jpeg,image/png,image/webp,image/heic,application/pdf' });
  const err = el('p', { class: 'form-error full', role: 'alert' });

  openModal({
    closeLabel: t('close'),
    title: existing ? t('editMovement') : t(typ === 'income' ? 'newIncome' : 'newExpense'),
    body: el('form', { class: 'form-grid', onsubmit: (e) => e.preventDefault() },
      field(`${t('date')} *`, date), field(`${t('amount')} (USD) *`, amount),
      field(`${t('description')} *`, desc, { full: true }),
      field(t('category'), category), field(t('method'), method),
      field(t('status'), status), field(t('photo'), photo, { hint: t('photoHint') }),
      field(t('notes'), notes, { full: true }), err),
    actions: [saveButton(t('save'), async (close) => {
      err.textContent = '';
      if (!date.value || !desc.value.trim() || amount.value === '') { err.textContent = t('required'); return; }
      if (!(Number(amount.value) > 0)) { err.textContent = t('badAmount'); return; }
      const f = photo.files[0];
      if (f && f.size > 10 * 1024 * 1024) { err.textContent = errText({ code: 'file_too_large' }); return; }
      const values = { type: typ, transaction_date: date.value, description: desc.value.trim(), amount: amount.value,
        category_id: category.value || null, payment_method: method.value, status: status.value, notes: notes.value.trim() || null };
      let row;
      try {
        row = existing ? await A.updateTransaction(ctx.tenantId, existing.id, values) : await A.createTransaction(ctx.tenantId, values);
      } catch (ex) { err.textContent = errText(ex); return; }
      if (f) {
        try { await A.uploadReceipt(ctx.tenantId, row.id || existing.id, f); } catch (ex) { toast(`${t('receiptFail')} ${errText(ex)}`, 'error'); }
      }
      close(); toast(t('saved')); reload();
    })],
  });
}

export function attachReceiptModal(ctx, m, reload) {
  const photo = input({ type: 'file', accept: 'image/jpeg,image/png,image/webp,image/heic,application/pdf' });
  const err = el('p', { class: 'form-error', role: 'alert' });
  openModal({
    closeLabel: t('close'),
    title: t('photo'),
    body: el('div', { class: 'form-grid' }, el('p', { class: 'hint full' }, `${fmtDay(m.date)} · ${m.description} · ${money(m.amount)}`),
      field(t('photo'), photo, { full: true, hint: t('photoHint') }), err),
    actions: [saveButton(t('save'), async (close) => {
      if (!photo.files[0]) { err.textContent = t('required'); return; }
      try { await A.uploadReceipt(ctx.tenantId, m.id, photo.files[0]); } catch (ex) { err.textContent = errText(ex); return; }
      close(); toast(t('saved')); reload();
    })],
  });
}

export function cancelModal(ctx, m, reload) {
  openModal({
    closeLabel: t('close'),
    title: t('cancelTitle'),
    body: el('div', {}, el('p', { class: 'strong m0' }, `${fmtDay(m.date)} · ${m.description} · ${money(m.amount)}`),
      el('p', { class: 'hint mt6' }, t('cancelText'))),
    actions: [saveButton(t('cancelBtn'), async (close) => {
      try { await A.cancelTransaction(ctx.tenantId, m.id); } catch (ex) { toast(errText(ex), 'error'); return; }
      close(); toast(t('cancelled')); reload();
    })],
  });
}

// ---------- pendientes ----------
export function obligationModal(ctx, reload) {
  const kind = select([{ value: 'receivable', label: t('toCollect') }, { value: 'payable', label: t('toPay') }]);
  const who = input({ autocomplete: 'off', maxlength: '120' });
  const desc = input({ autocomplete: 'off', maxlength: '300' });
  const amount = input({ type: 'number', min: '0.01', step: '0.01', inputmode: 'decimal' });
  const due = input({ type: 'date', value: todayISO(7) });
  const err = el('p', { class: 'form-error full', role: 'alert' });
  openModal({
    closeLabel: t('close'),
    title: t('newPending'),
    body: el('form', { class: 'form-grid', onsubmit: (e) => e.preventDefault() },
      field(`${t('pendingKind')} *`, kind, { full: true }), field(`${t('who')} *`, who), field(`${t('amount')} (USD) *`, amount),
      field(`${t('due')} *`, due), field(t('description'), desc), err),
    actions: [saveButton(t('save'), async (close) => {
      err.textContent = '';
      if (!who.value.trim() || !due.value || amount.value === '') { err.textContent = t('required'); return; }
      if (!(Number(amount.value) > 0)) { err.textContent = t('badAmount'); return; }
      try {
        await A.createObligation(ctx.tenantId, { obligation_type: kind.value, counterparty: who.value.trim(),
          description: desc.value.trim() || null, amount: amount.value, due_date: due.value });
      } catch (ex) { err.textContent = errText(ex); return; }
      close(); toast(t('saved')); reload();
    })],
  });
}

export function payModal(ctx, o, reload) {
  const method = select(METHODS.map(m => ({ value: m, label: t(`m_${m}`) })));
  openModal({
    closeLabel: t('close'),
    title: t('payTitle'),
    body: el('div', { class: 'form-grid' },
      el('p', { class: 'strong full m0' }, `${o.counterparty} · ${money(o.amount)} · ${t('due')} ${fmtDay(o.due_date)}`),
      el('p', { class: 'hint full' }, t('payText')), field(t('method'), method, { full: true })),
    actions: [saveButton(t('markPaid'), async (close) => {
      try { await A.payObligation(ctx.tenantId, o.id, { payment_method: method.value }); } catch (ex) { toast(errText(ex), 'error'); return; }
      close(); toast(t('paid')); reload();
    })],
  });
}

// ---------- exportar ----------
export function exportModal(ctx, range) {
  const download = async (fmt, btn) => {
    btn.disabled = true;
    try {
      const b = await A.exportReport(ctx.tenantId, range.start, range.end, fmt, getLang());
      const url = URL.createObjectURL(b);
      const a = el('a', { href: url, download: `contabilidad-${range.start}_${range.end}.${fmt}` });
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 2000);
    } catch (ex) { toast(errText(ex), 'error'); } finally { btn.disabled = false; }
  };
  openModal({
    closeLabel: t('close'),
    title: t('exportTitle'),
    body: el('div', {}, el('p', { class: 'strong m0' }, `${fmtDay(range.start)} → ${fmtDay(range.end)}`),
      el('p', { class: 'hint mt6' }, t('exportText'))),
    actions: ['csv', 'pdf'].map(fmt => () => el('button', { class: 'btn btn-primary', type: 'button',
      onclick: (e) => download(fmt, e.target) }, t(fmt))),
  });
}

// ---------- categorías ----------
export function categoriesModal(ctx, categories, reload) {
  const list = el('div');
  const draw = () => {
    clear(list);
    for (const typ of ['income', 'expense']) {
      const rows = categories.filter(c => c.type === typ);
      list.appendChild(el('h3', { class: 'm0' }, t(typ === 'income' ? 'income' : 'expenses')));
      if (!rows.length) list.appendChild(empty());
      list.appendChild(el('ul', { class: 'cat-list' }, rows.map(c => el('li', {},
        el('span', { class: c.active ? '' : 'muted' }, c.name), c.active ? null : badge('inactive', t('inactive')),
        el('button', { class: 'btn btn-sm', type: 'button', onclick: async (e) => {
          e.target.disabled = true;
          try { const r = await A.updateCategory(ctx.tenantId, c.id, { active: !c.active }); Object.assign(c, r); draw(); reload(); }
          catch (ex) { e.target.disabled = false; toast(errText(ex), 'error'); }
        } }, c.active ? t('hide') : t('show'))))));
    }
  };
  draw();
  const name = input({ maxlength: '80', autocomplete: 'off' });
  const typ = select([{ value: 'expense', label: t('t_expense') }, { value: 'income', label: t('t_income') }]);
  const err = el('p', { class: 'form-error full', role: 'alert' });
  const add = el('button', { class: 'btn', type: 'button', onclick: async () => {
    err.textContent = '';
    if (!name.value.trim()) { err.textContent = t('required'); return; }
    add.disabled = true;
    try { categories.push(await A.createCategory(ctx.tenantId, { name: name.value.trim(), type: typ.value })); name.value = ''; draw(); reload(); }
    catch (ex) { err.textContent = errText(ex); } finally { add.disabled = false; }
  } }, t('addCat'));
  openModal({
    closeLabel: t('close'),
    title: t('catTitle'),
    body: el('div', { class: 'stack' }, list,
      el('div', { class: 'form-grid' }, field(t('catName'), name), field(t('type'), typ), el('div', { class: 'full' }, add), err)),
  });
}
