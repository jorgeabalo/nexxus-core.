// AITA/Nexxus — Portal del socio.
//
// Seguridad:
//   * El servidor solo entrega SUPABASE_URL y la clave pública (anon).
//   * El socio entra con (a) su QR / enlace personal: el servidor valida la
//     firma y devuelve un token de un solo uso (#login=...) que aquí se canjea
//     con verifyOtp; o (b) enlace mágico por email.
//   * Todo dato llega por RPC/consultas con el JWT del socio: RLS y las
//     funciones member_* solo devuelven/modifican SUS filas.
//   * Sesión guardada con una clave propia para no mezclarse con /manager.
import { I18N } from './i18n.js';

const app = document.getElementById('app');
const S = { sb: null, session: null, data: null, lang: 'es', route: 'home', notice: null };

// ---------------------------------------------------------------------------
// utilidades
// ---------------------------------------------------------------------------
function el(tag, attrs = {}, ...children) {
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
const clear = (n) => { while (n.firstChild) n.removeChild(n.firstChild); return n; };
const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* sin almacenamiento */ } },
};
function t(key, vars = {}) {
  const s = (I18N[S.lang] && I18N[S.lang][key]) ?? I18N.es[key] ?? key;
  return s.replace(/\{(\w+)\}/g, (_, k) => vars[k] ?? '');
}
const locale = () => (S.lang === 'es' ? 'es-US' : 'en-US');
const tz = () => S.data?.tenant?.timezone || 'America/Chicago';
const currency = () => S.data?.tenant?.branding?.currency || 'USD';

function fmtDate(iso, opts = { weekday: 'short', month: 'short', day: 'numeric' }) {
  if (!iso) return '—';
  const [y, m, d] = String(iso).slice(0, 10).split('-').map(Number);
  return new Intl.DateTimeFormat(locale(), { ...opts, timeZone: 'UTC' }).format(new Date(Date.UTC(y, m - 1, d)));
}
function fmtClock(tm) {
  if (!tm) return '';
  const [h, mi] = String(tm).split(':').map(Number);
  return new Intl.DateTimeFormat(locale(), { hour: 'numeric', minute: '2-digit', timeZone: 'UTC' }).format(new Date(Date.UTC(2000, 0, 1, h, mi)));
}
function fmtDateTime(iso) {
  if (!iso) return '—';
  return new Intl.DateTimeFormat(locale(), { timeZone: tz(), weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }).format(new Date(iso));
}
function money(v) {
  const n = Number(v || 0);
  return new Intl.NumberFormat(locale(), { style: 'currency', currency: currency(), maximumFractionDigits: n % 1 ? 2 : 0 }).format(n);
}
const TONE = { active: 'ok', paid: 'ok', confirmed: 'ok', completed: 'ok', scheduled: 'warn', pending: 'warn', overdue: 'bad', past_due: 'bad', cancelled: 'muted', no_show: 'bad', inactive: 'muted', paused: 'warn' };
function badge(status) {
  const k = String(status || '').toLowerCase();
  return el('span', { class: `badge ${TONE[k] || ''}` }, t(`status.${k}`) === `status.${k}` ? k.replace(/_/g, ' ') : t(`status.${k}`));
}
function toast(msg, kind = '') {
  const n = el('div', { class: `toast ${kind}` }, msg);
  document.getElementById('toast-root').appendChild(n);
  setTimeout(() => n.remove(), 3500);
}
function openModal(title, body, actions = []) {
  const root = document.getElementById('modal-root');
  clear(root);
  const close = () => { clear(root); document.removeEventListener('keydown', onKey); };
  const onKey = (e) => { if (e.key === 'Escape') close(); };
  document.addEventListener('keydown', onKey);
  const dialog = el('div', { class: 'modal', role: 'dialog', 'aria-modal': 'true', 'aria-label': title },
    el('div', { class: 'modal-head' }, el('h2', {}, title),
      el('button', { class: 'icon-btn', type: 'button', 'aria-label': t('close'), onclick: close }, '✕')),
    body,
    el('div', { class: 'modal-foot' }, el('button', { class: 'btn', type: 'button', onclick: close }, t('cancel')), ...actions.map(a => a(close))));
  const backdrop = el('div', { class: 'modal-backdrop', onclick: (e) => { if (e.target === backdrop) close(); } }, dialog);
  root.appendChild(backdrop);
  return close;
}
function errText(e) {
  const m = String(e?.message || e || '');
  const map = [
    [/membership not active/i, 'err.membership'], [/unknown service/i, 'err.service'],
    [/must be in the future/i, 'err.future'], [/too far/i, 'err.far'], [/too many pending/i, 'err.pending'],
    [/cannot be cancelled/i, 'err.cancel'], [/invalid email/i, 'err.email'], [/not a member/i, 'err.notMember'],
    [/JWT|expired|401/i, 'err.session'],
  ];
  for (const [re, key] of map) if (re.test(m)) return t(key);
  return t('err.generic');
}

// Íconos de la barra inferior
const PATHS = {
  home: 'M12 3 2 12h3v8h6v-5h2v5h6v-8h3L12 3z',
  appts: 'M7 2v2H5a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6a2 2 0 0 0-2-2h-2V2h-2v2H9V2H7zm-2 7h14v10H5V9z',
  pay: 'M3 5h18a1 1 0 0 1 1 1v12a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1zm1 4v8h16V9H4zm0-2h16V7H4z',
  visits: 'M9 16.2 4.8 12l-1.4 1.4L9 19 21 7l-1.4-1.4z',
  me: 'M12 12a4 4 0 1 0-4-4 4 4 0 0 0 4 4zm0 2c-3 0-8 1.5-8 4.5V21h16v-2.5c0-3-5-4.5-8-4.5z',
};
function icon(name) {
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('viewBox', '0 0 24 24'); svg.setAttribute('width', 22); svg.setAttribute('height', 22);
  svg.setAttribute('fill', 'currentColor'); svg.setAttribute('aria-hidden', 'true');
  const p = document.createElementNS(ns, 'path'); p.setAttribute('d', PATHS[name]); svg.appendChild(p);
  return svg;
}

// ---------------------------------------------------------------------------
// arranque
// ---------------------------------------------------------------------------
async function boot() {
  S.lang = store.get('aita-member-lang') === 'en' ? 'en' : 'es';  // español por defecto
  document.documentElement.lang = S.lang;

  const hash = new URLSearchParams(location.hash.replace(/^#/, ''));
  const loginToken = hash.get('login');
  const linkError = hash.get('error') || hash.get('error_code');
  if (loginToken || linkError) history.replaceState(null, '', '/m/');

  let cfg;
  try {
    const r = await fetch('/api/manager/config', { cache: 'no-store' });
    if (!r.ok) throw new Error('config');
    cfg = await r.json();
  } catch {
    return renderFatal(t('err.config'));
  }
  S.sb = window.supabase.createClient(cfg.supabaseUrl, cfg.supabaseAnonKey, {
    auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: true, flowType: 'implicit', storageKey: 'aita-member-auth' },
  });
  S.sb.auth.onAuthStateChange((event, session) => {
    S.session = session;
    if (event === 'SIGNED_OUT') { S.data = null; renderLogin(); }
  });

  if (loginToken) {
    // Enlace / QR personal: canjear el token de un solo uso.
    let { data, error } = await S.sb.auth.verifyOtp({ token_hash: loginToken, type: 'magiclink' });
    if (error) ({ data, error } = await S.sb.auth.verifyOtp({ token_hash: loginToken, type: 'email' }));
    if (error || !data?.session) S.notice = { kind: 'bad', text: t('err.linkUsed') };
  } else if (linkError) {
    S.notice = { kind: 'bad', text: t(`link.${linkError}`) === `link.${linkError}` ? t('err.linkInvalid') : t(`link.${linkError}`) };
  }

  const { data: { session } } = await S.sb.auth.getSession();
  S.session = session;
  if (!session) return renderLogin();
  await loadAndRender();
}

async function loadAndRender() {
  try {
    const { data, error } = await S.sb.rpc('member_portal');
    if (error) throw error;
    S.data = data;
  } catch (e) {
    if (/not a member/i.test(e?.message || '')) {
      await S.sb.auth.signOut();
      S.notice = { kind: 'bad', text: t('err.notMember') };
      return renderLogin();
    }
    return renderFatal(errText(e), true);
  }
  applyBranding();
  window.removeEventListener('hashchange', route);
  window.addEventListener('hashchange', route);
  route();
}

function applyBranding() {
  const b = S.data?.tenant?.branding || {};
  const root = document.documentElement.style;
  if (/^#[0-9a-f]{6}$/i.test(b.color_primary || '')) root.setProperty('--brand-primary', b.color_primary);
  if (/^#[0-9a-f]{6}$/i.test(b.color_accent || '')) root.setProperty('--brand-accent', b.color_accent);
  document.title = `${t('portal')} · ${brandName()}`;
}
const brandName = () => S.data?.tenant?.branding?.display_name || S.data?.tenant?.name || '';

function setLang(l) {
  S.lang = l; store.set('aita-member-lang', l); document.documentElement.lang = l;
  if (S.data) { applyBranding(); route(); } else renderLogin();
}
const langToggle = (cls) => el('button', { class: cls, type: 'button', onclick: () => setLang(S.lang === 'es' ? 'en' : 'es') }, S.lang === 'es' ? 'English' : 'Español');

// ---------------------------------------------------------------------------
// pantallas sin sesión
// ---------------------------------------------------------------------------
function renderFatal(msg, withRetry = false) {
  clear(app).appendChild(el('div', { class: 'login-wrap' }, el('div', { class: 'login' },
    el('p', { class: 'notice bad' }, msg),
    withRetry ? el('button', { class: 'btn btn-block', type: 'button', onclick: () => location.reload() }, t('retry')) : null)));
}

function renderLogin() {
  const email = el('input', { class: 'input', type: 'email', autocomplete: 'email', inputmode: 'email', required: true, placeholder: 'tu@email.com', 'aria-label': t('login.email') });
  const err = el('p', { class: 'form-error', role: 'alert' });
  const btn = el('button', { class: 'btn btn-primary btn-block', type: 'submit' }, t('login.send'));
  const form = el('form', { class: 'form', novalidate: true, onsubmit: async (e) => {
    e.preventDefault();
    err.textContent = '';
    const v = email.value.trim().toLowerCase();
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(v)) { err.textContent = t('err.email'); return; }
    btn.disabled = true;
    const { error } = await S.sb.auth.signInWithOtp({ email: v, options: { shouldCreateUser: false, emailRedirectTo: `${location.origin}/m/` } });
    btn.disabled = false;
    // Misma respuesta exista o no el email (no revelar quién es socio).
    if (error && /rate|security purposes|too many/i.test(error.message)) { err.textContent = t('err.rate'); return; }
    S.notice = { kind: 'ok', text: t('login.sent', { email: v }) };
    renderLogin();
  } }, el('label', { class: 'field' }, el('span', {}, t('login.email')), email), err, btn);

  clear(app).appendChild(el('div', { class: 'login-wrap' }, el('div', { class: 'login' },
    el('div', { class: 'brand' }, t('portal').toUpperCase()),
    el('h1', {}, t('login.title')),
    el('p', { class: 'lead' }, t('login.lead')),
    S.notice ? el('p', { class: `notice ${S.notice.kind}` }, S.notice.text) : null,
    form,
    el('p', { class: 'muted small center', text: t('login.qrHint') }),
    el('div', { class: 'lang-row' }, langToggle('link-btn')))));
  S.notice = null;
}

// ---------------------------------------------------------------------------
// layout con sesión
// ---------------------------------------------------------------------------
const ROUTES = [
  { key: 'home', icon: 'home', view: viewHome },
  { key: 'appts', icon: 'appts', view: viewAppointments },
  { key: 'pay', icon: 'pay', view: viewPayments },
  { key: 'visits', icon: 'visits', view: viewVisits },
  { key: 'me', icon: 'me', view: viewMe },
];

function route() {
  const key = (location.hash.match(/^#\/(\w+)/) || [])[1] || 'home';
  const r = ROUTES.find(x => x.key === key) || ROUTES[0];
  S.route = r.key;
  const m = S.data.member;
  const main = el('main', { class: 'stack' });
  clear(app).append(
    el('header', { class: 'top' },
      el('div', { class: 'top-row' },
        el('div', { class: 'brand' }, brandName()),
        el('div', { class: 'top-actions' }, langToggle('chip-btn'),
          el('button', { class: 'chip-btn', type: 'button', onclick: () => S.sb.auth.signOut() }, t('signout')))),
      el('div', { class: 'hello' }, el('h1', {}, t('hello', { name: m.first_name || '' })),
        el('p', {}, [m.membership_type, m.member_code ? `ID ${m.member_code}` : null].filter(Boolean).join(' · ') || t('portal')))),
    main,
    el('nav', { class: 'nav', 'aria-label': t('portal') }, ROUTES.map(x =>
      el('a', { href: `#/${x.key}`, class: x.key === r.key ? 'active' : '', 'aria-current': x.key === r.key ? 'page' : null }, icon(x.icon), t(`nav.${x.key}`)))));
  r.view(main);
  window.scrollTo(0, 0);
}

async function refresh() {
  const { data, error } = await S.sb.rpc('member_portal');
  if (error) throw error;
  S.data = data;
  route();
}

const card = (title, body, extra = null) => el('section', { class: 'card' }, el('div', { class: 'card-head' }, el('h2', {}, title), extra), body);
const empty = (text) => el('div', { class: 'empty' }, text);
const upcoming = () => S.data.appointments
  .filter(a => ['scheduled', 'confirmed'].includes(a.status) && `${a.date}T${a.start_time}` >= `${S.data.today}T00:00`)
  .sort((a, b) => `${a.date}${a.start_time}`.localeCompare(`${b.date}${b.start_time}`));
const apptStatus = (a) => (a.status === 'scheduled' && a.source === 'member_portal' ? 'pending_confirmation' : a.status);

// ---------------------------------------------------------------------------
// vistas
// ---------------------------------------------------------------------------
function viewHome(main) {
  const m = S.data.member;
  const next = upcoming()[0];
  const overdue = S.data.payments.filter(p => p.status === 'overdue');

  main.appendChild(card(t('home.membership'), el('div', {},
    el('div', { class: 'row' }, el('div', { class: 'main' }, el('span', { class: 'strong' }, m.membership_type || t('home.membership')),
      el('span', { class: 'muted small' }, m.start_date ? t('home.since', { date: fmtDate(m.start_date, { month: 'short', day: 'numeric', year: 'numeric' }) }) : '')), badge(m.membership_status)),
    m.next_payment_date ? el('div', { class: 'row' }, el('span', {}, t('home.nextPayment')), el('span', { class: 'strong' }, fmtDate(m.next_payment_date))) : null,
    overdue.length ? el('div', { class: 'row' }, el('span', {}, t('home.overdue')), el('span', { class: 'badge bad' }, money(overdue.reduce((s, p) => s + Number(p.amount || 0), 0)))) : null)));

  main.appendChild(card(t('home.next'), next
    ? el('div', { class: 'row' }, el('div', { class: 'main' }, el('span', { class: 'strong' }, next.service || t('appt.appointment')),
        el('span', { class: 'muted small' }, `${fmtDate(next.date)} · ${fmtClock(next.start_time)}${next.staff_first_name ? ` · ${next.staff_first_name}` : ''}`)), badge(apptStatus(next)))
    : el('div', {}, empty(t('home.noNext')), el('button', { class: 'btn btn-block', type: 'button', onclick: requestModal }, t('appt.request')))));

  const qrBox = el('div', {}, el('div', { class: 'spinner' }));
  main.appendChild(el('section', { class: 'card qr-card' }, el('h2', {}, t('home.qr')), qrBox,
    el('p', { class: 'qr-note' }, t('home.qrNote'))));
  loadQr(qrBox);

  main.appendChild(el('div', { class: 'kpis' },
    el('div', { class: 'kpi' }, el('div', { class: 'k' }, t('visits.month')), el('div', { class: 'v' }, String(S.data.visits.this_month ?? 0))),
    el('div', { class: 'kpi' }, el('div', { class: 'k' }, t('visits.last')), el('div', { class: 'v small' }, S.data.visits.last_visit ? fmtDateTime(S.data.visits.last_visit) : '—'))));

  const tn = S.data.tenant;
  if (tn.phone || tn.address) {
    main.appendChild(card(t('home.contact'), el('div', {},
      tn.phone ? el('div', { class: 'row' }, el('span', {}, t('home.phone')), el('a', { href: `tel:${tn.phone.replace(/[^\d+]/g, '')}` }, tn.phone)) : null,
      tn.address ? el('div', { class: 'row' }, el('span', {}, t('home.address')), el('span', { class: 'strong' }, tn.address)) : null)));
  }
}

async function loadQr(box) {
  try {
    const get = async () => fetch('/api/member/qr', {
      headers: { Authorization: `Bearer ${(await S.sb.auth.getSession()).data.session?.access_token || ''}` }, cache: 'no-store' });
    let r = await get();
    if (r.status === 401 && !(await S.sb.auth.refreshSession()).error) r = await get();
    const d = await r.json().catch(() => ({}));
    if (!r.ok || !d.link) throw new Error(d.error || 'qr');
    const qr = window.qrcode(0, 'M');
    qr.addData(d.link); qr.make();
    clear(box).appendChild(el('img', { class: 'qr-img', src: qr.createDataURL(8, 2), alt: t('home.qr') }));
  } catch {
    clear(box).appendChild(empty(t('home.qrError')));
  }
}

function viewAppointments(main) {
  const up = upcoming();
  const past = S.data.appointments.filter(a => !up.includes(a));
  main.appendChild(el('button', { class: 'btn btn-primary btn-block', type: 'button', onclick: requestModal }, t('appt.request')));
  const row = (a, withCancel) => el('div', { class: 'row' },
    el('div', { class: 'main' }, el('span', { class: 'strong' }, a.service || t('appt.appointment')),
      el('span', { class: 'muted small' }, `${fmtDate(a.date)} · ${fmtClock(a.start_time)}${a.staff_first_name ? ` · ${a.staff_first_name}` : ''}`)),
    el('div', { class: 'center' }, badge(apptStatus(a)),
      withCancel && a.can_cancel ? el('div', {}, el('button', { class: 'link-btn', type: 'button', onclick: () => cancelModal(a) }, t('appt.cancel'))) : null));
  main.appendChild(card(t('appt.upcoming'), up.length ? el('div', {}, up.map(a => row(a, true))) : empty(t('appt.noneUpcoming'))));
  main.appendChild(card(t('appt.history'), past.length ? el('div', {}, past.slice(0, 30).map(a => row(a, false))) : empty(t('appt.noneHistory'))));
}

function requestModal() {
  if (S.data.member.membership_status !== 'active') return toast(t('err.membership'), 'error');
  const services = S.data.services || [];
  if (!services.length) return toast(t('appt.noServices'), 'error');
  const svc = el('select', { class: 'input', 'aria-label': t('appt.service') }, services.map(s => el('option', { value: s.name }, s.duration_minutes ? `${s.name} · ${s.duration_minutes} min` : s.name)));
  const date = el('input', { class: 'input', type: 'date', min: S.data.today, required: true });
  const time = el('input', { class: 'input', type: 'time', step: 900, required: true });
  const notes = el('textarea', { class: 'input', maxlength: 500, placeholder: t('appt.notesPh') });
  const err = el('p', { class: 'form-error', role: 'alert' });
  const body = el('div', { class: 'form' },
    el('p', { class: 'muted small m0' }, t('appt.requestHint')),
    el('label', { class: 'field' }, el('span', {}, t('appt.service')), svc),
    el('div', { class: 'grid-2' }, el('label', { class: 'field' }, el('span', {}, t('appt.date')), date), el('label', { class: 'field' }, el('span', {}, t('appt.time')), time)),
    el('label', { class: 'field' }, el('span', {}, t('appt.notes')), notes), err);
  openModal(t('appt.request'), body, [(close) => el('button', { class: 'btn btn-primary', type: 'button', onclick: async (e) => {
    err.textContent = '';
    if (!date.value || !time.value) { err.textContent = t('appt.pick'); return; }
    e.target.disabled = true;
    const { error } = await S.sb.rpc('member_request_appointment', { p_service: svc.value, p_date: date.value, p_time: time.value, p_notes: notes.value || null });
    if (error) { e.target.disabled = false; err.textContent = errText(error); return; }
    close(); toast(t('appt.requested'));
    location.hash = '#/appts';
    await refresh().catch(() => {});
  } }, t('appt.send'))]);
}

function cancelModal(a) {
  openModal(t('appt.cancelTitle'), el('p', {}, t('appt.cancelConfirm', { service: a.service || '', date: `${fmtDate(a.date)} · ${fmtClock(a.start_time)}` })),
    [(close) => el('button', { class: 'btn btn-primary', type: 'button', onclick: async (e) => {
      e.target.disabled = true;
      const { error } = await S.sb.rpc('member_cancel_appointment', { p_appointment: a.id });
      if (error) { e.target.disabled = false; toast(errText(error), 'error'); return; }
      close(); toast(t('appt.cancelled'));
      await refresh().catch(() => {});
    } }, t('appt.cancelYes'))]);
}

function viewPayments(main) {
  const pays = S.data.payments || [];
  const owed = pays.filter(p => ['pending', 'overdue'].includes(p.status));
  main.appendChild(el('div', { class: 'kpis' },
    el('div', { class: 'kpi' }, el('div', { class: 'k' }, t('pay.due')), el('div', { class: 'v' }, money(owed.reduce((s, p) => s + Number(p.amount || 0), 0)))),
    el('div', { class: 'kpi' }, el('div', { class: 'k' }, t('home.nextPayment')), el('div', { class: 'v small' }, S.data.member.next_payment_date ? fmtDate(S.data.member.next_payment_date) : '—'))));
  main.appendChild(card(t('pay.history'), pays.length ? el('div', {}, pays.map(p => el('div', { class: 'row' },
    el('div', { class: 'main' }, el('span', { class: 'strong' }, money(p.amount)),
      el('span', { class: 'muted small' }, [p.description, p.payment_date ? t('pay.paidOn', { date: fmtDateTime(p.payment_date) }) : p.due_date ? t('pay.dueOn', { date: fmtDate(p.due_date) }) : null].filter(Boolean).join(' · '))),
    badge(p.status)))) : empty(t('pay.none'))));
  main.appendChild(el('p', { class: 'muted small center' }, t('pay.help')));
}

function viewVisits(main) {
  const v = S.data.visits;
  main.appendChild(el('div', { class: 'kpis' },
    el('div', { class: 'kpi' }, el('div', { class: 'k' }, t('visits.month')), el('div', { class: 'v' }, String(v.this_month ?? 0))),
    el('div', { class: 'kpi' }, el('div', { class: 'k' }, t('visits.last')), el('div', { class: 'v small' }, v.last_visit ? fmtDateTime(v.last_visit) : '—'))));
  main.appendChild(card(t('visits.recent'), (v.recent || []).length
    ? el('div', {}, v.recent.map(x => el('div', { class: 'row' }, el('span', {}, fmtDateTime(x)))))
    : empty(t('visits.none'))));
}

function viewMe(main) {
  const m = S.data.member;
  const f = (label, attrs) => { const i = el('input', { class: 'input', ...attrs }); return [i, el('label', { class: 'field' }, el('span', {}, label), i)]; };
  const [phone, phoneF] = f(t('me.phone'), { type: 'tel', autocomplete: 'tel', value: m.phone || '', maxlength: 30 });
  const [email, emailF] = f(t('me.email'), { type: 'email', autocomplete: 'email', value: m.email || '', maxlength: 200 });
  const [ename, enameF] = f(t('me.emName'), { autocomplete: 'off', value: m.emergency_contact_name || '', maxlength: 120 });
  const [ephone, ephoneF] = f(t('me.emPhone'), { type: 'tel', autocomplete: 'off', value: m.emergency_contact_phone || '', maxlength: 30 });
  const err = el('p', { class: 'form-error', role: 'alert' });
  const save = el('button', { class: 'btn btn-primary btn-block', type: 'submit' }, t('me.save'));
  const form = el('form', { class: 'form', novalidate: true, onsubmit: async (e) => {
    e.preventDefault(); err.textContent = '';
    const ev = email.value.trim();
    if (ev && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(ev)) { err.textContent = t('err.email'); return; }
    save.disabled = true;
    const { error } = await S.sb.rpc('member_update_contact', { p_phone: phone.value, p_email: ev, p_emergency_name: ename.value, p_emergency_phone: ephone.value });
    save.disabled = false;
    if (error) { err.textContent = errText(error); return; }
    toast(t('me.saved'));
    await refresh().catch(() => {});
  } }, phoneF, emailF, el('h3', {}, t('me.emergency')), enameF, ephoneF, err, save);

  main.appendChild(card(t('me.title'), el('div', {},
    el('div', { class: 'row' }, el('span', {}, t('me.name')), el('span', { class: 'strong' }, [m.first_name, m.last_name].filter(Boolean).join(' '))),
    m.member_code ? el('div', { class: 'row' }, el('span', {}, 'ID'), el('span', { class: 'strong' }, m.member_code)) : null)));
  main.appendChild(card(t('me.contact'), form));
  main.appendChild(el('p', { class: 'muted small center' }, t('me.help')));
}

boot();
