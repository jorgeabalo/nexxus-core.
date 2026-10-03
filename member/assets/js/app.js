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
import {
  S, el, clear, store, t, has, fmtDate, fmtClock, fmtDateTime, money, badge, toast, openModal, errText, icon,
  card, empty, authFetch,
} from './util.js';
import { viewProgress, topInsights, figureSvg, bodyMetrics } from './progress.js';
import { viewEvaluation, evaluationCard } from './evaluation.js';
import { viewReview } from './upload.js';
import { welcomeCard } from './welcome.js';

const app = document.getElementById('app');

// ---------------------------------------------------------------------------
// arranque
// ---------------------------------------------------------------------------
async function boot() {
  S.lang = store.get('aita-member-lang') === 'en' ? 'en' : 'es';  // español por defecto
  S.units = store.get('aita-member-units') === 'metric' ? 'metric' : 'imperial';
  document.documentElement.lang = S.lang;
  const qs = new URLSearchParams(location.search);
  if (qs.get('next') === 'evaluation') S.next = 'evaluation';
  const qrMatch = location.pathname.match(/^\/m\/q\/([^/?#]+)/);

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
    if (event === 'SIGNED_OUT') { S.data = null; wipeLocal(); renderLogin(); }
  });
  startIdleGuard();

  if (qrMatch) {
    // Enlace / QR personal: el GET no inicia sesión (así las vistas previas de
    // SMS/email no lo consumen). El socio pulsa "Entrar" y ahí se valida.
    const existing = (await S.sb.auth.getSession()).data.session;
    return renderQrEntry(decodeURIComponent(qrMatch[1]), Boolean(existing));
  }

  if (loginToken) {
    // Token de un solo uso (compatibilidad con enlaces anteriores).
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

async function redeem(tokenHash) {
  let { data, error } = await S.sb.auth.verifyOtp({ token_hash: tokenHash, type: 'magiclink' });
  if (error) ({ data, error } = await S.sb.auth.verifyOtp({ token_hash: tokenHash, type: 'email' }));
  return !error && data?.session;
}

function renderQrEntry(token, hasSession) {
  const err = el('p', { class: 'form-error', role: 'alert' });
  const btn = el('button', { class: 'btn btn-primary btn-block btn-lg', type: 'button', onclick: async () => {
    err.textContent = ''; btn.disabled = true; btn.textContent = t('qr.entering');
    try {
      const r = await fetch('/api/member/qr-login', { method: 'POST', cache: 'no-store', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ token }) });
      const d = await r.json().catch(() => ({}));
      if (!r.ok || !d.token_hash) throw new Error(d.error || 'login');
      if (!(await redeem(d.token_hash))) throw new Error('login_unavailable');
      history.replaceState(null, '', '/m/' + (S.next === 'evaluation' ? '#/evaluation' : ''));
      await loadAndRender();
    } catch (e) {
      btn.disabled = false; btn.textContent = t('qr.enter');
      const code = String(e.message || '');
      err.textContent = has(`link.${code}`) ? t(`link.${code}`) : t('err.linkInvalid');
    }
  } }, t('qr.enter'));
  clear(app).appendChild(el('div', { class: 'login-wrap' }, el('div', { class: 'login' },
    el('div', { class: 'brand' }, t('portal').toUpperCase()),
    el('h1', {}, S.next === 'evaluation' ? t('qr.titleEval') : t('qr.title')),
    el('p', { class: 'lead' }, t('qr.lead')), btn, err,
    hasSession ? el('a', { class: 'link-btn center-block', href: '/m/' }, t('qr.already')) : null,
    el('p', { class: 'muted small center' }, t('qr.private')),
    el('div', { class: 'lang-row' }, el('a', { class: 'link-btn', href: '/' }, t('site.back')), langToggle('link-btn')))));
}

// ---------- protección en dispositivos compartidos ----------
// Borra cualquier copia local de respuestas y cierra la sesión tras inactividad.
function wipeLocal() {
  try { sessionStorage.clear(); } catch { /* */ }
  try { Object.keys(localStorage).filter(k => k.startsWith('aita-eval-draft:') || k.startsWith('aita-doc-review:')).forEach(k => localStorage.removeItem(k)); } catch { /* */ }
}
const IDLE_MS = 15 * 60 * 1000;
function startIdleGuard() {
  let last = Date.now();
  const bump = () => { last = Date.now(); };
  ['pointerdown', 'keydown', 'scroll', 'touchstart'].forEach(e => window.addEventListener(e, bump, { passive: true }));
  setInterval(async () => {
    if (!S.session || Date.now() - last < IDLE_MS) return;
    last = Date.now();
    S.notice = { kind: 'ok', text: t('idle.out') };
    await S.sb.auth.signOut();
  }, 30000);
}

let seenMarked = false;
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
    window.addEventListener('hashchange', () => { route(); window.scrollTo(0, 0); });
  if (S.next === 'evaluation' && !location.hash) { S.next = null; location.hash = '#/evaluation'; return; }
  route();
  if (!seenMarked) { seenMarked = true; S.sb.rpc('member_mark_seen').then(() => {}, () => {}); }
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
  if (S.data) { applyBranding(); route(); } else if (location.pathname.startsWith('/m/q/')) boot(); else renderLogin();
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
    const { error } = await S.sb.auth.signInWithOtp({ email: v, options: { shouldCreateUser: false, emailRedirectTo: `${location.origin}/m/${S.next === 'evaluation' ? '?next=evaluation' : ''}` } });
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
    el('div', { class: 'lang-row' }, el('a', { class: 'link-btn', href: '/' }, t('site.back')), langToggle('link-btn')))));
  S.notice = null;
}

// ---------------------------------------------------------------------------
// layout con sesión
// ---------------------------------------------------------------------------
const ROUTES = [
  { key: 'home', icon: 'home', view: viewHome },
  { key: 'progress', icon: 'progress', view: (main) => viewProgress(main, rerender) },
  { key: 'appts', icon: 'appts', view: viewAppointments },
  { key: 'pay', icon: 'pay', view: viewPayments },
  { key: 'visits', icon: 'visits', view: viewVisits },
  { key: 'me', icon: 'me', view: viewMe },
];

function route() {
  const key = (location.hash.match(/^#\/(\w+)/) || [])[1] || 'home';
  const docId = (location.hash.match(/^#\/review\/([0-9a-f-]{36})$/i) || [])[1];
  const r = key === 'evaluation' ? { key: 'evaluation', view: (main) => viewEvaluation(main, rerender) }
    : (key === 'review' && docId) ? { key: 'review', view: (main) => viewReview(main, rerender, docId) }
    : (ROUTES.find(x => x.key === key) || ROUTES[0]);
  S.route = r.key;
  const m = S.data.member;
  const main = el('main', { class: 'stack' });
  clear(app).append(
    el('header', { class: 'top' },
      el('div', { class: 'top-row' },
        el('div', { class: 'brand' }, brandName()),
        el('div', { class: 'top-actions' }, el('a', { class: 'chip-btn', href: '/', title: t('site.back') }, t('site.chip')), langToggle('chip-btn'),
          el('button', { class: 'chip-btn', type: 'button', onclick: () => S.sb.auth.signOut() }, t('signout')))),
      el('div', { class: 'hello' }, el('h1', {}, t('hello', { name: m.first_name || '' })),
        el('p', {}, [m.membership_type, m.member_code ? `ID ${m.member_code}` : null].filter(Boolean).join(' · ') || t('portal')))),
    main,
    el('nav', { class: 'nav', 'aria-label': t('portal') }, ROUTES.map(x =>
      el('a', { href: `#/${x.key}`, class: x.key === r.key ? 'active' : '', 'aria-current': x.key === r.key ? 'page' : null }, icon(x.icon), t(`nav.${x.key}`)))));
  r.view(main);
}

// Recarga los datos (tras guardar algo) y vuelve a pintar la vista actual.
async function rerender(reload = false, after = null) {
  const y = window.scrollY;
  if (reload) {
    const { data, error } = await S.sb.rpc('member_portal');
    if (error) { toast(errText(error), 'error'); return; }
    S.data = data;
  }
  route();
  if (after) after(); else window.scrollTo(0, reload ? 0 : y);
}

async function refresh() {
  const { data, error } = await S.sb.rpc('member_portal');
  if (error) throw error;
  S.data = data;
  route();
}

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

  main.appendChild(welcomeCard());
  main.appendChild(evaluationCard());
  main.appendChild(progressPreview());

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

function progressPreview() {
  const ins = topInsights(2);
  const m = bodyMetrics();
  const any = (S.data.measurements || []).length || (S.data.progress || []).length;
  return el('section', { class: 'card progress-preview' },
    el('div', { class: 'card-head' }, el('h2', {}, t('prog.title')), el('a', { class: 'link-btn', href: '#/progress' }, t('prog.open'))),
    el('div', { class: 'pp' },
      el('div', { class: 'pp-fig' }, figureSvg(S.data.member.sex, {}, { compact: true })),
      el('div', { class: 'pp-text' },
        ins.length ? el('ul', { class: 'insights' }, ins.map(x => el('li', {}, x)))
          : el('p', { class: 'm0' }, any ? t('prog.previewKeep') : t('prog.previewStart')),
        m.weight ? el('p', { class: 'small muted m0' }, t('prog.lastRecord', { d: fmtDate(m.weight.cur.date, { month: 'short', day: 'numeric' }) })) : null,
        el('a', { class: 'btn btn-sm mt8', href: '#/progress' }, any ? t('prog.open') : t('prog.startQuick')))));
}

async function loadQr(box) {
  try {
    const r = await authFetch('/api/member/qr');
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
