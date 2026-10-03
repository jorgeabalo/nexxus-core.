// AITA/Nexxus Manager Panel — arranque, autenticación, layout y rutas.
//
// Seguridad:
//   * El servidor solo entrega SUPABASE_URL y la clave pública (anon).
//   * Sin sesión válida solo se muestra el login; ningún módulo carga datos.
//   * Todas las consultas van con el JWT del usuario: RLS filtra por tenant.
import { el, clear, icon, setFormatContext, fmtLongToday, toast, errorBox } from './ui.js';
import { initApi, api } from './api.js';
import * as dashboard from './modules/dashboard.js';
import * as members from './modules/members.js';
import * as schedule from './modules/schedule.js';
import * as claudia from './modules/claudia.js';
import * as payments from './modules/payments.js';
import * as team from './modules/team.js';
import * as comingSoon from './modules/coming-soon.js';

// Catálogo de módulos del Manager Panel (igual para todos los tenants).
// Qué módulos están activos lo decide tenants.modules en la base de datos.
export const MODULES = [
  { key: 'dashboard', label: 'Dashboard', icon: 'dashboard', view: dashboard },
  { key: 'members', label: 'Members', icon: 'members', view: members },
  { key: 'schedule', label: 'Schedule', icon: 'schedule', view: schedule },
  { key: 'claudia', label: 'Claudia', icon: 'claudia', view: claudia },
  { key: 'payments', label: 'Payments', icon: 'payments', view: payments },
  { key: 'team', label: 'Team', icon: 'user', view: team },
  { key: 'accounting', label: 'Accounting', icon: 'accounting', view: comingSoon },
  { key: 'marketing', label: 'Marketing', icon: 'marketing', view: comingSoon },
  { key: 'inventory', label: 'Inventory', icon: 'inventory', view: comingSoon },
  { key: 'agents', label: 'Agents', icon: 'agents', view: comingSoon },
  { key: 'settings', label: 'Settings', icon: 'settings', view: comingSoon },
];
const PHASE_1 = new Set(['dashboard', 'members', 'schedule', 'claudia', 'payments', 'team']);

const app = document.getElementById('app');
const state = { sb: null, session: null, memberships: [], ctx: null };

// ---------------------------------------------------------------------------
async function boot() {
  // Detectar enlaces de invitación / recuperación ANTES de que el cliente
  // consuma el hash de la URL.
  const hash = new URLSearchParams(location.hash.replace(/^#/, ''));
  const needsPassword = ['invite', 'recovery'].includes(hash.get('type'));

  let cfg;
  try {
    const r = await fetch('/api/manager/config', { credentials: 'same-origin', cache: 'no-store' });
    if (!r.ok) throw new Error(r.status === 503 ? 'The panel is not configured yet (missing Supabase settings).' : `Config error ${r.status}`);
    cfg = await r.json();
  } catch (e) {
    return renderFatal(e.message);
  }

  state.sb = window.supabase.createClient(cfg.supabaseUrl, cfg.supabaseAnonKey, {
    auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: true, flowType: 'implicit' },
  });
  initApi(state.sb);

  state.sb.auth.onAuthStateChange((event, session) => {
    state.session = session;
    if (event === 'PASSWORD_RECOVERY') return renderSetPassword();
    if (event === 'SIGNED_OUT') { state.ctx = null; renderLogin(); }
  });

  const { data } = await state.sb.auth.getSession();
  state.session = data.session;
  if (!state.session) return renderLogin();
  if (needsPassword) return renderSetPassword();
  return enter();
}

// ---------------------------------------------------------------------------
// Autenticación
// ---------------------------------------------------------------------------
function authCard(subtitle, ...content) {
  return el('div', { class: 'auth-wrap' },
    el('div', { class: 'auth-card' },
      el('div', { class: 'auth-brand' },
        el('div', { class: 'wordmark' }, 'MANAGER'),
        el('div', { class: 'rule' }),
        el('p', {}, subtitle)),
      ...content,
      el('div', { class: 'auth-foot' }, el('a', { href: '/' }, '← Back to website'))));
}

function renderLogin(message = '') {
  history.replaceState(null, '', '/manager');
  const email = el('input', { class: 'input', type: 'email', autocomplete: 'username', required: true, id: 'login-email' });
  const password = el('input', { class: 'input', type: 'password', autocomplete: 'current-password', required: true, id: 'login-password' });
  const err = el('p', { class: 'form-error', role: 'alert' }, message);
  const submit = el('button', { class: 'btn btn-primary btn-block', type: 'submit' }, 'Sign in');
  const form = el('form', {
    onsubmit: async (e) => {
      e.preventDefault(); err.textContent = ''; submit.disabled = true;
      const { error } = await state.sb.auth.signInWithPassword({ email: email.value.trim(), password: password.value });
      submit.disabled = false;
      if (error) { err.textContent = 'Incorrect email or password.'; return; }
      const { data } = await state.sb.auth.getSession();
      state.session = data.session;
      enter();
    },
  },
    el('div', { class: 'field' }, el('label', { for: 'login-email' }, 'Email'), email),
    el('div', { class: 'field' }, el('label', { for: 'login-password' }, 'Password'), password),
    err, submit);
  const forgot = el('div', { class: 'auth-foot' }, el('button', { type: 'button', onclick: renderForgot }, 'Forgot password?'));
  clear(app).appendChild(authCard('Sign in to your business dashboard', form, forgot));
  email.focus();
}

function renderForgot() {
  const email = el('input', { class: 'input', type: 'email', required: true, id: 'reset-email', autocomplete: 'username' });
  const msg = el('p', { class: 'hint', role: 'status' });
  const form = el('form', {
    onsubmit: async (e) => {
      e.preventDefault();
      await state.sb.auth.resetPasswordForEmail(email.value.trim(), { redirectTo: `${location.origin}/manager` });
      // Mismo mensaje exista o no la cuenta (no revelar qué emails existen)
      msg.textContent = 'If that email has access, a reset link is on its way.';
    },
  }, el('div', { class: 'field' }, el('label', { for: 'reset-email' }, 'Email'), email), msg,
    el('button', { class: 'btn btn-primary btn-block', type: 'submit' }, 'Send reset link'));
  const back = el('div', { class: 'auth-foot' }, el('button', { type: 'button', onclick: () => renderLogin() }, 'Back to sign in'));
  clear(app).appendChild(authCard('Reset your password', form, back));
  email.focus();
}

function renderSetPassword() {
  history.replaceState(null, '', '/manager');
  const p1 = el('input', { class: 'input', type: 'password', minlength: '10', required: true, id: 'np1', autocomplete: 'new-password' });
  const p2 = el('input', { class: 'input', type: 'password', minlength: '10', required: true, id: 'np2', autocomplete: 'new-password' });
  const err = el('p', { class: 'form-error', role: 'alert' });
  const form = el('form', {
    onsubmit: async (e) => {
      e.preventDefault(); err.textContent = '';
      if (p1.value.length < 10) { err.textContent = 'Use at least 10 characters.'; return; }
      if (p1.value !== p2.value) { err.textContent = 'Passwords do not match.'; return; }
      const { error } = await state.sb.auth.updateUser({ password: p1.value });
      if (error) { err.textContent = error.message; return; }
      toast('Password saved');
      enter();
    },
  },
    el('div', { class: 'field' }, el('label', { for: 'np1' }, 'New password'), p1),
    el('div', { class: 'field' }, el('label', { for: 'np2' }, 'Repeat password'), p2),
    err, el('button', { class: 'btn btn-primary btn-block', type: 'submit' }, 'Save password'));
  clear(app).appendChild(authCard('Create your password', form));
  p1.focus();
}

function renderFatal(message) {
  clear(app).appendChild(authCard('Manager Dashboard', el('p', { class: 'form-error' }, message)));
}

function renderNoAccess() {
  clear(app).appendChild(authCard('No access',
    el('p', { class: 'hint center' }, 'Your account is not linked to any business yet. Ask the owner to invite you.'),
    el('div', { class: 'auth-foot' }, el('button', { type: 'button', onclick: () => state.sb.auth.signOut() }, 'Sign out'))));
}

// ---------------------------------------------------------------------------
// Contexto de tenant + layout
// ---------------------------------------------------------------------------
async function enter() {
  clear(app).appendChild(el('div', { class: 'boot' }, el('div', { class: 'spinner' }), el('span', {}, 'Loading…')));
  let memberships;
  try {
    memberships = await api.myMemberships(state.session.user.id);
  } catch (e) {
    clear(app).appendChild(authCard('Error', errorBox(e)));
    return;
  }
  if (!memberships.length) return renderNoAccess();
  state.memberships = memberships;

  let saved = null;
  try { saved = localStorage.getItem('aita.tenant'); } catch (_) { /* sin storage */ }
  const m = memberships.find(x => x.tenant.id === saved) || memberships[0];
  setTenant(m);
  renderShell();
  if (!state.routing) { window.addEventListener('hashchange', route); state.routing = true; }
  route();
}

function setTenant(m) {
  const t = m.tenant;
  const b = t.branding || {};
  state.ctx = {
    tenant: t,
    tenantId: t.id,
    role: m.role,
    user: state.session.user,
    modules: t.modules || {},
    branding: b,
  };
  try { localStorage.setItem('aita.tenant', t.id); } catch (_) { /* opcional */ }
  setFormatContext({ timezone: t.timezone, locale: b.locale, currency: b.currency });
  const root = document.documentElement.style;
  if (b.color_primary) root.setProperty('--brand-primary', b.color_primary);
  if (b.color_accent) root.setProperty('--brand-accent', b.color_accent);
  document.title = `${b.display_name || t.name} · ${b.subtitle || 'Manager Dashboard'}`;
}

let shellRefs = null;
function renderShell() {
  const { tenant, branding, role, user } = state.ctx;
  const displayName = branding.display_name || tenant.name;
  const subtitle = branding.subtitle || 'Manager Dashboard';

  const nav = el('nav', { class: 'sb-nav', 'aria-label': 'Main' });
  MODULES.forEach((mod, i) => {
    if (mod.key === 'team' && !['owner', 'manager'].includes(role)) return;  // staff no gestiona el equipo
    if (i === PHASE_1.size) nav.appendChild(el('div', { class: 'sb-sep', role: 'separator' }));
    const enabled = isEnabled(mod.key);
    nav.appendChild(el('a', { class: 'sb-link', href: `#/${mod.key}`, dataset: { key: mod.key }, onclick: closeNav },
      icon(mod.icon), el('span', {}, mod.label), enabled ? null : el('span', { class: 'soon' }, 'Soon')));
  });

  const sidebar = el('aside', { class: 'sidebar', id: 'sidebar' },
    el('div', { class: 'sb-brand' }, el('div', { class: 'wordmark' }, displayName), el('div', { class: 'sub' }, subtitle)),
    nav,
    el('div', { class: 'sb-foot' },
      el('div', { class: 'sb-user', title: user.email }, user.email),
      el('div', { class: 'sb-role' }, role),
      el('button', { class: 'btn btn-sm', type: 'button', onclick: () => state.sb.auth.signOut() }, 'Sign out')));

  const tenantSelect = state.memberships.length > 1
    ? el('select', {
      class: 'input tenant-select', 'aria-label': 'Business',
      onchange: (e) => { const m = state.memberships.find(x => x.tenant.id === e.target.value); setTenant(m); renderShell(); route(); },
    }, state.memberships.map(m => { const o = el('option', { value: m.tenant.id }, m.tenant.name); if (m.tenant.id === tenant.id) o.selected = true; return o; }))
    : null;

  const content = el('main', { class: 'content', id: 'content', tabindex: '-1' });
  const topbar = el('header', { class: 'topbar' },
    el('button', { class: 'menu-btn', type: 'button', 'aria-label': 'Open menu', 'aria-controls': 'sidebar', onclick: toggleNav }, icon('menu')),
    el('div', { class: 'titles' }, el('div', { class: 't-brand' }, displayName), el('div', { class: 't-sub' }, subtitle)),
    el('div', { class: 'date' }, fmtLongToday()),
    tenantSelect,
    el('a', { class: 'btn btn-sm site-link', href: '/', title: 'Back to website' }, '← Website'));

  const shell = el('div', { class: 'shell' }, sidebar, el('div', { class: 'scrim', onclick: closeNav }),
    el('div', { class: 'main' }, topbar, content));
  clear(app).appendChild(shell);
  shellRefs = { shell, nav, content };
}

function isEnabled(key) {
  // Solo los módulos de la Fase 1 tienen vista real; el resto es "Coming soon".
  return PHASE_1.has(key) && state.ctx.modules[key] !== false;
}
function toggleNav() { shellRefs.shell.classList.toggle('nav-open'); }
function closeNav() { shellRefs && shellRefs.shell.classList.remove('nav-open'); }

// ---------------------------------------------------------------------------
// Rutas (#/modulo/param)
// ---------------------------------------------------------------------------
let renderToken = 0;
async function route() {
  if (!state.ctx || !shellRefs) return;
  closeNav();
  const [key = 'dashboard', ...params] = location.hash.replace(/^#\/?/, '').split('/').filter(Boolean);
  const mod = MODULES.find(m => m.key === key) || MODULES[0];
  for (const a of shellRefs.nav.querySelectorAll('.sb-link')) {
    if (a.dataset.key === mod.key) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
  }
  const token = ++renderToken;
  const content = clear(shellRefs.content);
  const view = isEnabled(mod.key) ? mod.view : comingSoon;
  try {
    await view.render(content, { ...state.ctx, params, module: mod, isCurrent: () => token === renderToken });
  } catch (e) {
    if (token === renderToken) { clear(content).appendChild(errorBox(e)); }
  }
  content.focus({ preventScroll: true });
  window.scrollTo(0, 0);
}

boot();
