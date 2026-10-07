// Helpers de interfaz. Todo el texto se inserta con textContent (nunca
// innerHTML con datos), para que ningún dato de la base pueda inyectar HTML.

export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class') node.className = v;
    else if (k === 'dataset') Object.assign(node.dataset, v);
    else if (k.startsWith('on') && typeof v === 'function') node.addEventListener(k.slice(2), v);
    else if (k === 'text') node.textContent = v;
    else node.setAttribute(k, v === true ? '' : v);
  }
  append(node, children);
  return node;
}

export function append(node, children) {
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    node.appendChild(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

export function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); return node; }

// Íconos SVG (trazos simples, sin librerías externas)
const PATHS = {
  dashboard: 'M3 13h8V3H3v10zm0 8h8v-6H3v6zm10 0h8V11h-8v10zm0-18v6h8V3h-8z',
  members: 'M16 11a4 4 0 1 0-4-4 4 4 0 0 0 4 4zm-8 0a3 3 0 1 0-3-3 3 3 0 0 0 3 3zm0 2c-2.7 0-6 1.3-6 4v2h8v-2c0-1 .4-2 1.1-2.8A10 10 0 0 0 8 13zm8 0c-3 0-7 1.5-7 4.5V20h14v-2.5c0-3-4-4.5-7-4.5z',
  schedule: 'M7 2v2H5a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6a2 2 0 0 0-2-2h-2V2h-2v2H9V2H7zm-2 7h14v10H5V9zm2 2v2h2v-2H7zm4 0v2h2v-2h-2zm4 0v2h2v-2h-2z',
  claudia: 'M6.6 10.8a15.2 15.2 0 0 0 6.6 6.6l2.2-2.2a1 1 0 0 1 1-.25 11.4 11.4 0 0 0 3.6.57 1 1 0 0 1 1 1V20a1 1 0 0 1-1 1A17 17 0 0 1 3 4a1 1 0 0 1 1-1h3.5a1 1 0 0 1 1 1c0 1.25.2 2.45.57 3.57a1 1 0 0 1-.25 1l-2.2 2.23z',
  payments: 'M3 5h18a1 1 0 0 1 1 1v12a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1zm1 4v8h16V9H4zm0-2h16V7H4v0zm2 6h5v2H6v-2z',
  accounting: 'M4 3h16v18H4V3zm2 2v4h12V5H6zm0 6v2h2v-2H6zm4 0v2h2v-2h-2zm4 0v6h4v-6h-4zM6 15v2h2v-2H6zm4 0v2h2v-2h-2z',
  marketing: 'M3 10v4h3l5 4V6L6 10H3zm13.5 2A4.5 4.5 0 0 0 14 8v8a4.5 4.5 0 0 0 2.5-4zM14 3.2v2.1a7 7 0 0 1 0 13.4v2.1a9 9 0 0 0 0-17.6z',
  inventory: 'M20 7l-8-4-8 4v10l8 4 8-4V7zm-8-1.8L17.5 8 12 10.8 6.5 8 12 5.2zM6 9.6l5 2.5v6.3l-5-2.5V9.6zm7 8.8v-6.3l5-2.5v6.3l-5 2.5z',
  agents: 'M12 2a3 3 0 0 1 3 3v1h3a2 2 0 0 1 2 2v9a4 4 0 0 1-4 4H8a4 4 0 0 1-4-4V8a2 2 0 0 1 2-2h3V5a3 3 0 0 1 3-3zm-3 9a1.5 1.5 0 1 0 0 3 1.5 1.5 0 0 0 0-3zm6 0a1.5 1.5 0 1 0 0 3 1.5 1.5 0 0 0 0-3zm-6 5v2h6v-2H9z',
  settings: 'M19.4 13a7.5 7.5 0 0 0 0-2l2.1-1.6-2-3.4-2.5 1a7.4 7.4 0 0 0-1.7-1L15 3.3h-4l-.4 2.7a7.4 7.4 0 0 0-1.7 1l-2.5-1-2 3.4L6.6 11a7.5 7.5 0 0 0 0 2l-2.1 1.6 2 3.4 2.5-1a7.4 7.4 0 0 0 1.7 1l.4 2.7h4l.4-2.7a7.4 7.4 0 0 0 1.7-1l2.5 1 2-3.4L19.4 13zM13 15.5a3.5 3.5 0 1 1 0-7 3.5 3.5 0 0 1 0 7z',
  menu: 'M3 6h18v2H3V6zm0 5h18v2H3v-2zm0 5h18v2H3v-2z',
  close: 'M18.3 5.7 12 12l6.3 6.3-1.4 1.4L10.6 13.4 4.3 19.7l-1.4-1.4L9.2 12 2.9 5.7l1.4-1.4 6.3 6.3 6.3-6.3z',
  check: 'M9 16.2 4.8 12l-1.4 1.4L9 19 21 7l-1.4-1.4z',
  user: 'M12 12a4 4 0 1 0-4-4 4 4 0 0 0 4 4zm0 2c-3 0-8 1.5-8 4.5V21h16v-2.5c0-3-5-4.5-8-4.5z',
  cash: 'M3 6h18v12H3V6zm9 3a3 3 0 1 0 3 3 3 3 0 0 0-3-3z',
  alert: 'M12 2 1 21h22L12 2zm1 15h-2v-2h2v2zm0-4h-2V9h2v4z',
  lead: 'M20 4H4a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V6a2 2 0 0 0-2-2zm0 4-8 5-8-5V6l8 5 8-5v2z',
  back: 'M20 11H7.8l5.6-5.6L12 4l-8 8 8 8 1.4-1.4L7.8 13H20v-2z',
};
export function icon(name, size = 20) {
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('width', size); svg.setAttribute('height', size);
  svg.setAttribute('aria-hidden', 'true'); svg.setAttribute('fill', 'currentColor');
  const p = document.createElementNS(ns, 'path');
  p.setAttribute('d', PATHS[name] || PATHS.dashboard);
  svg.appendChild(p);
  return svg;
}

// ---------- formato (zona horaria y moneda del tenant) ----------
let TZ = 'America/Chicago';
let LOCALE = 'en-US';
let CURRENCY = 'USD';
export function setFormatContext({ timezone, locale, currency }) {
  if (timezone) TZ = timezone;
  if (locale) LOCALE = locale;
  if (currency) CURRENCY = currency;
}
export const tz = () => TZ;

export function money(v) {
  const n = Number(v || 0);
  return new Intl.NumberFormat(LOCALE, { style: 'currency', currency: CURRENCY, maximumFractionDigits: n % 1 ? 2 : 0 }).format(n);
}
export function num(v) { return new Intl.NumberFormat(LOCALE).format(Number(v || 0)); }

export function fmtDateTime(iso) {
  if (!iso) return '—';
  return new Intl.DateTimeFormat(LOCALE, { timeZone: TZ, month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }).format(new Date(iso));
}
export function fmtTime(iso) {
  if (!iso) return '—';
  return new Intl.DateTimeFormat(LOCALE, { timeZone: TZ, hour: 'numeric', minute: '2-digit' }).format(new Date(iso));
}
// Fechas "date" de Postgres (YYYY-MM-DD): no tienen zona, se muestran tal cual.
export function fmtDate(d, opts = { month: 'short', day: 'numeric', year: 'numeric' }) {
  if (!d) return '—';
  const [y, m, day] = String(d).slice(0, 10).split('-').map(Number);
  return new Intl.DateTimeFormat(LOCALE, { ...opts, timeZone: 'UTC' }).format(new Date(Date.UTC(y, m - 1, day)));
}
// "time" de Postgres (HH:MM:SS)
export function fmtClock(t) {
  if (!t) return '—';
  const [h, m] = String(t).split(':').map(Number);
  return new Intl.DateTimeFormat(LOCALE, { hour: 'numeric', minute: '2-digit', timeZone: 'UTC' }).format(new Date(Date.UTC(2000, 0, 1, h, m)));
}
export function fmtDuration(sec) {
  if (sec === null || sec === undefined) return '—';
  const s = Math.round(Number(sec));
  const m = Math.floor(s / 60);
  return m ? `${m}m ${String(s % 60).padStart(2, '0')}s` : `${s}s`;
}
export function fmtLongToday() {
  return new Intl.DateTimeFormat(LOCALE, { timeZone: TZ, weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' }).format(new Date());
}

// Fecha de "hoy" en la zona del tenant, como YYYY-MM-DD
export function todayISO(offsetDays = 0) {
  const parts = new Intl.DateTimeFormat('en-CA', { timeZone: TZ, year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date());
  if (!offsetDays) return parts;
  const [y, m, d] = parts.split('-').map(Number);
  const dt = new Date(Date.UTC(y, m - 1, d + offsetDays));
  return dt.toISOString().slice(0, 10);
}
export function weekdayOf(isoDate) {
  const [y, m, d] = isoDate.split('-').map(Number);
  return new Date(Date.UTC(y, m - 1, d)).getUTCDay(); // 0=domingo
}
// Inicio del día en la zona del tenant, en UTC ISO (para filtrar timestamptz)
export function dayStartUTC(isoDate) {
  const [y, m, d] = isoDate.split('-').map(Number);
  const guess = Date.UTC(y, m - 1, d, 12);
  const f = new Intl.DateTimeFormat('en-US', { timeZone: TZ, hour12: false, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
  const p = Object.fromEntries(f.formatToParts(new Date(guess)).map(x => [x.type, x.value]));
  const asLocal = Date.UTC(+p.year, +p.month - 1, +p.day, +p.hour % 24, +p.minute);
  const offset = asLocal - guess;
  return new Date(Date.UTC(y, m - 1, d) - offset).toISOString();
}

// ---------- badges ----------
const TONE = {
  active: 'ok', paid: 'ok', confirmed: 'ok', completed: 'ok', converted: 'ok', resolved: 'ok',
  pending: 'warn', scheduled: 'info', in_progress: 'info', new: 'gold', contacted: 'info',
  overdue: 'bad', past_due: 'bad', cancelled: 'muted', canceled: 'muted', failed: 'bad', no_show: 'bad',
  inactive: 'muted', paused: 'warn', lost: 'muted', abandoned: 'muted', rescheduled: 'muted',
  busy: 'muted', no_answer: 'muted', none: 'muted', refunded: 'muted', void: 'muted',
  critical: 'bad', warning: 'warn', info: 'info',
};
export function badge(value, label) {
  if (value === null || value === undefined || value === '') return el('span', { class: 'muted' }, '—');
  const key = String(value).toLowerCase();
  return el('span', { class: `badge ${TONE[key] || ''}` }, label || humanize(key));
}
export function humanize(s) { return String(s || '').replace(/_/g, ' '); }

// ---------- estados ----------
export function empty(text = 'No data yet') { return el('div', { class: 'empty' }, text); }
export function loading() { return el('div', { class: 'empty' }, el('div', { class: 'spinner spinner-inline' }), 'Loading…'); }
export function errorBox(err) {
  console.error(err);
  const msg = (err && (err.message || err.error_description)) || 'Something went wrong';
  return el('div', { class: 'error-box', role: 'alert' }, `Could not load data: ${msg}`);
}

// ---------- toast ----------
export function toast(message, kind = '') {
  const root = document.getElementById('toast-root');
  const t = el('div', { class: `toast ${kind}` }, message);
  root.appendChild(t);
  setTimeout(() => t.remove(), 3500);
}

// ---------- modal ----------
export function openModal({ title, body, actions = [], closeLabel = 'Cancel' }) {
  const root = document.getElementById('modal-root');
  clear(root);
  const close = () => { clear(root); document.removeEventListener('keydown', onKey); };
  const onKey = (e) => { if (e.key === 'Escape') close(); };
  document.addEventListener('keydown', onKey);
  const foot = el('div', { class: 'modal-foot' },
    el('button', { class: 'btn', type: 'button', onclick: close }, closeLabel),
    ...actions.map(a => a(close)));
  const dialog = el('div', { class: 'modal', role: 'dialog', 'aria-modal': 'true', 'aria-label': title },
    el('div', { class: 'modal-head' }, el('h2', {}, title),
      el('button', { class: 'icon-btn', type: 'button', 'aria-label': 'Close', onclick: close }, icon('close'))),
    el('div', { class: 'modal-body' }, body),
    foot);
  const backdrop = el('div', { class: 'modal-backdrop', onclick: (e) => { if (e.target === backdrop) close(); } }, dialog);
  root.appendChild(backdrop);
  const first = dialog.querySelector('input, select, textarea, button.btn-primary');
  if (first) first.focus();
  return close;
}

export function field(label, input, { full = false, hint } = {}) {
  const id = input.id || `f_${Math.random().toString(36).slice(2, 9)}`;
  input.id = id;
  return el('div', { class: `field${full ? ' full' : ''}` }, el('label', { for: id }, label), input, hint ? el('p', { class: 'hint' }, hint) : null);
}
export function input(attrs = {}) { return el('input', { class: 'input', ...attrs }); }
export function select(options, attrs = {}) {
  const s = el('select', { class: 'input', ...attrs });
  for (const o of options) {
    const opt = el('option', { value: o.value }, o.label);
    if (o.selected) opt.selected = true;
    s.appendChild(opt);
  }
  return s;
}

export function kpi(label, value, meta = '', accent = false) {
  return el('div', { class: `card kpi${accent ? ' accent' : ''}` },
    el('div', { class: 'label' }, label),
    el('div', { class: 'value' }, value),
    el('div', { class: 'meta' }, meta));
}

export function card(title, body, headExtra = null) {
  return el('section', { class: 'card' },
    el('div', { class: 'card-head' }, el('h2', {}, title), headExtra),
    body);
}

// Tabla que se convierte en tarjetas en móvil (data-label)
export function table(columns, rows, { onRowClick, emptyText } = {}) {
  if (!rows || !rows.length) return empty(emptyText);
  const thead = el('thead', {}, el('tr', {}, columns.map(c => el('th', { class: c.num ? 'num' : '' }, c.label))));
  const tbody = el('tbody', {}, rows.map(r => {
    const tr = el('tr', { class: onRowClick ? 'clickable' : '', tabindex: onRowClick ? '0' : null },
      columns.map((c, i) => {
        const v = c.render ? c.render(r) : r[c.key];
        return el('td', { 'data-label': c.label, class: [c.num ? 'num' : '', i === 0 ? 'primary-cell' : ''].join(' ').trim() },
          v === null || v === undefined || v === '' ? el('span', { class: 'muted' }, '—') : v);
      }));
    if (onRowClick) {
      tr.addEventListener('click', (e) => { if (!e.target.closest('button, a')) onRowClick(r); });
      tr.addEventListener('keydown', (e) => { if (e.key === 'Enter') onRowClick(r); });
    }
    return tr;
  }));
  return el('div', { class: 'table-wrap' }, el('table', { class: 'data' }, thead, tbody));
}

export function tabs(items, current, onChange) {
  return el('div', { class: 'tabs', role: 'tablist' }, items.map(i =>
    el('button', { class: 'tab', role: 'tab', type: 'button', 'aria-selected': String(i.value === current), onclick: () => onChange(i.value) }, i.label)));
}

export function debounce(fn, ms = 250) {
  let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}
