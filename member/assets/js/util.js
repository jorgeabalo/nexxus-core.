// Utilidades compartidas del portal del socio (DOM seguro, idioma, formato, unidades).
import { I18N } from './i18n.js';

export const S = { sb: null, session: null, data: null, lang: 'es', route: 'home', notice: null, units: 'imperial', next: null };

export function el(tag, attrs = {}, ...children) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class') n.className = v;
    else if (k.startsWith('on') && typeof v === 'function') n.addEventListener(k.slice(2), v);
    else if (k === 'text') n.textContent = v;
    else n.setAttribute(k, v === true ? '' : v);
  }
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    n.appendChild(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return n;
}
export const clear = (n) => { while (n.firstChild) n.removeChild(n.firstChild); return n; };
export const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* sin almacenamiento */ } },
  del(k) { try { localStorage.removeItem(k); } catch { /* */ } },
};
export function t(key, vars = {}) {
  const s = (I18N[S.lang] && I18N[S.lang][key]) ?? I18N.es[key] ?? key;
  return String(s).replace(/\{(\w+)\}/g, (_, k) => vars[k] ?? '');
}
export const has = (key) => Boolean((I18N[S.lang] && key in I18N[S.lang]) || key in I18N.es);
export const locale = () => (S.lang === 'es' ? 'es-US' : 'en-US');
export const tz = () => S.data?.tenant?.timezone || 'America/Chicago';
const currency = () => S.data?.tenant?.branding?.currency || 'USD';

export function fmtDate(iso, opts = { weekday: 'short', month: 'short', day: 'numeric' }) {
  if (!iso) return '—';
  const [y, m, d] = String(iso).slice(0, 10).split('-').map(Number);
  return new Intl.DateTimeFormat(locale(), { ...opts, timeZone: 'UTC' }).format(new Date(Date.UTC(y, m - 1, d)));
}
export const fmtDay = (iso) => fmtDate(iso, { month: 'short', day: 'numeric', year: 'numeric' });
export function fmtClock(tm) {
  if (!tm) return '';
  const [h, mi] = String(tm).split(':').map(Number);
  return new Intl.DateTimeFormat(locale(), { hour: 'numeric', minute: '2-digit', timeZone: 'UTC' }).format(new Date(Date.UTC(2000, 0, 1, h, mi)));
}
export function fmtDateTime(iso) {
  if (!iso) return '—';
  return new Intl.DateTimeFormat(locale(), { timeZone: tz(), weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }).format(new Date(iso));
}
export function money(v) {
  const n = Number(v || 0);
  return new Intl.NumberFormat(locale(), { style: 'currency', currency: currency(), maximumFractionDigits: n % 1 ? 2 : 0 }).format(n);
}
export const num = (v, digits = 1) => new Intl.NumberFormat(locale(), { maximumFractionDigits: digits }).format(v);

// ---------- unidades (la base guarda lb / in) ----------
export const LB_TO_KG = 0.45359237;
export const IN_TO_CM = 2.54;
export const metric = () => S.units === 'metric';
export function showWeight(lb, { signed = false } = {}) {
  if (lb === null || lb === undefined) return null;
  const v = metric() ? Number(lb) * LB_TO_KG : Number(lb);
  return `${signed && v > 0 ? '+' : ''}${num(v)} ${metric() ? 'kg' : 'lb'}`;
}
export function showLength(inch, { signed = false } = {}) {
  if (inch === null || inch === undefined) return null;
  const v = metric() ? Number(inch) * IN_TO_CM : Number(inch);
  return `${signed && v > 0 ? '+' : ''}${num(v)} ${metric() ? 'cm' : 'in'}`;
}
export function showHeight(inch) {
  if (inch === null || inch === undefined) return null;
  if (metric()) return `${num(Number(inch) * IN_TO_CM, 0)} cm`;
  const ft = Math.floor(Number(inch) / 12);
  return `${ft} ft ${num(Number(inch) - ft * 12, 1)} in`;
}
// entrada del usuario en la unidad visible -> lb/in (redondeo a 0.1)
export const toLb = (v) => (v === '' || v === null || v === undefined || isNaN(Number(v))) ? null : Math.round((metric() ? Number(v) / LB_TO_KG : Number(v)) * 10) / 10;
export const toIn = (v) => (v === '' || v === null || v === undefined || isNaN(Number(v))) ? null : Math.round((metric() ? Number(v) / IN_TO_CM : Number(v)) * 10) / 10;

const TONE = { active: 'ok', paid: 'ok', confirmed: 'ok', completed: 'ok', scheduled: 'warn', pending: 'warn', overdue: 'bad', past_due: 'bad', cancelled: 'muted', no_show: 'bad', inactive: 'muted', paused: 'warn' };
export function badge(status, tone) {
  const k = String(status || '').toLowerCase();
  return el('span', { class: `badge ${tone || TONE[k] || ''}` }, has(`status.${k}`) ? t(`status.${k}`) : k.replace(/_/g, ' '));
}
export function toast(msg, kind = '') {
  const n = el('div', { class: `toast ${kind}` }, msg);
  document.getElementById('toast-root').appendChild(n);
  setTimeout(() => n.remove(), 4000);
}
export function openModal(title, body, actions = [], { cancelLabel } = {}) {
  const root = document.getElementById('modal-root');
  clear(root);
  const close = () => { clear(root); document.removeEventListener('keydown', onKey); };
  const onKey = (e) => { if (e.key === 'Escape') close(); };
  document.addEventListener('keydown', onKey);
  const dialog = el('div', { class: 'modal', role: 'dialog', 'aria-modal': 'true', 'aria-label': title },
    el('div', { class: 'modal-head' }, el('h2', {}, title),
      el('button', { class: 'icon-btn', type: 'button', 'aria-label': t('close'), onclick: close }, '✕')),
    body,
    el('div', { class: 'modal-foot' }, el('button', { class: 'btn', type: 'button', onclick: close }, cancelLabel || t('cancel')), ...actions.map(a => a(close))));
  const backdrop = el('div', { class: 'modal-backdrop', onclick: (e) => { if (e.target === backdrop) close(); } }, dialog);
  root.appendChild(backdrop);
  return close;
}
export function errText(e) {
  const m = String(e?.message || e || '');
  const map = [
    [/membership not active/i, 'err.membership'], [/unknown service/i, 'err.service'],
    [/must be in the future/i, 'err.future'], [/too far/i, 'err.far'], [/too many pending/i, 'err.pending'],
    [/cannot be cancelled/i, 'err.cancel'], [/invalid email/i, 'err.email'], [/not a member/i, 'err.notMember'],
    [/already submitted/i, 'err.evalDone'], [/required question/i, 'err.required'], [/no values|empty evaluation/i, 'err.noValues'],
    [/check constraint|violates|range/i, 'err.range'], [/Failed to fetch|NetworkError|network/i, 'err.network'],
    [/JWT|expired|401/i, 'err.session'],
  ];
  for (const [re, key] of map) if (re.test(m)) return t(key);
  return t('err.generic');
}
export const card = (title, body, extra = null, cls = '') => el('section', { class: `card ${cls}` }, el('div', { class: 'card-head' }, el('h2', {}, title), extra), body);
export const empty = (text) => el('div', { class: 'empty' }, text);
export const pending = () => el('span', { class: 'pending' }, t('prog.pending'));

export async function authFetch(path, opts = {}) {
  const get = async () => fetch(path, { ...opts, cache: 'no-store',
    headers: { ...(opts.headers || {}), Authorization: `Bearer ${(await S.sb.auth.getSession()).data.session?.access_token || ''}` } });
  let r = await get();
  if (r.status === 401 && !(await S.sb.auth.refreshSession()).error) r = await get();
  return r;
}

export function uuid4() {
  if (crypto.randomUUID) return crypto.randomUUID();
  const b = crypto.getRandomValues(new Uint8Array(16));
  b[6] = (b[6] & 0x0f) | 0x40; b[8] = (b[8] & 0x3f) | 0x80;
  const h = [...b].map(x => x.toString(16).padStart(2, '0')).join('');
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}

const PATHS = {
  home: 'M12 3 2 12h3v8h6v-5h2v5h6v-8h3L12 3z',
  progress: 'M3 17h2v4H3v-4zm4-5h2v9H7v-9zm4 3h2v6h-2v-6zm4-8h2v14h-2V7zm4-4h2v18h-2V3z',
  appts: 'M7 2v2H5a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6a2 2 0 0 0-2-2h-2V2h-2v2H9V2H7zm-2 7h14v10H5V9z',
  pay: 'M3 5h18a1 1 0 0 1 1 1v12a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1zm1 4v8h16V9H4zm0-2h16V7H4z',
  visits: 'M9 16.2 4.8 12l-1.4 1.4L9 19 21 7l-1.4-1.4z',
  me: 'M12 12a4 4 0 1 0-4-4 4 4 0 0 0 4 4zm0 2c-3 0-8 1.5-8 4.5V21h16v-2.5c0-3-5-4.5-8-4.5z',
  train: 'M3 8h3v8H3zM6 6h3v12H6zm9 0h3v12h-3zm3 2h3v8h-3zM9 11h6v2H9z',
};
export function icon(name, size = 22) {
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('viewBox', '0 0 24 24'); svg.setAttribute('width', size); svg.setAttribute('height', size);
  svg.setAttribute('fill', 'currentColor'); svg.setAttribute('aria-hidden', 'true');
  const p = document.createElementNS(ns, 'path'); p.setAttribute('d', PATHS[name]); svg.appendChild(p);
  return svg;
}
export function svgEl(tag, attrs = {}, ...children) {
  const n = document.createElementNS('http://www.w3.org/2000/svg', tag);
  for (const [k, v] of Object.entries(attrs)) if (v !== null && v !== undefined) n.setAttribute(k, v);
  for (const c of children.flat()) if (c) n.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
  return n;
}
