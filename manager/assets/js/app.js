// Nexxus Manager — arranque, autenticación, layout y rutas.
// Nexxus es la plataforma; la empresa activa (tenant) aporta nombre, logo,
// colores, contacto, zona horaria, idioma y módulos habilitados.
//
// Seguridad:
//   * El servidor solo entrega SUPABASE_URL y la clave pública (anon).
//   * Sin sesión válida solo se muestra el login; ningún módulo carga datos.
//   * Todas las consultas van con el JWT del usuario: RLS filtra por tenant.
import { el, clear, icon, nexxusMark, setFormatContext, toast, errorBox } from './ui.js';
import { tr, getLang, setLang, useTenantDefault } from './i18n.js';
import { CATALOG, canOpen, visibleModules, homeModule, tenantProfile } from './nav.js';
import { initApi, api } from './api.js';
import * as dashboard from './modules/dashboard.js';
import * as members from './modules/members.js';
import * as schedule from './modules/schedule.js';
import * as claudia from './modules/claudia.js';
import * as payments from './modules/payments.js';
import * as team from './modules/team.js';
import * as accounting from './modules/accounting.js';
import * as comingSoon from './modules/coming-soon.js';

// Vista de cada módulo. El orden del menú, qué está habilitado y qué ve cada
// rol se deciden en nav.js (tenants.modules + rol del usuario).
const VIEWS = { dashboard, members, schedule, claudia, payments, team, accounting };
export const MODULES = CATALOG.map(m => ({ ...m, view: m.live ? VIEWS[m.key] : comingSoon }));

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
        el('div', { class: 'auth-mark' }, nexxusMark(44)),
        el('div', { class: 'wordmark' }, 'NEXXUS MANAGER'),
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
  clear(app).appendChild(authCard('Nexxus Manager', el('p', { class: 'form-error' }, message)));
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
  const profile = tenantProfile(t);
  state.ctx = {
    tenant: t,
    tenantId: t.id,
    role: m.role,
    user: state.session.user,
    modules: t.modules || {},
    branding: b,
    profile,
  };
  try { localStorage.setItem('aita.tenant', t.id); } catch (_) { /* opcional */ }
  useTenantDefault(profile.language);
  setFormatContext({ timezone: profile.timezone, locale: b.locale, currency: profile.currency });
  const root = document.documentElement.style;
  if (profile.colors.primary) root.setProperty('--brand-primary', profile.colors.primary);
  if (profile.colors.accent) root.setProperty('--brand-accent', profile.colors.accent);
  document.documentElement.lang = getLang();
  document.title = `${tr('platform')} · ${profile.name}`;
}

let shellRefs = null;
function renderShell() {
  const { role, user, profile, tenant } = state.ctx;
  const userName = (user.user_metadata && (user.user_metadata.full_name || user.user_metadata.name)) || user.email;
  const roleLabel = tr(`role_${role}`);

  const nav = el('nav', { class: 'sb-nav', 'aria-label': tr('mainNav') });
  for (const mod of visibleModules(tenant, role)) {
    nav.appendChild(el('a', { class: 'sb-link', href: `#/${mod.key}`, dataset: { key: mod.key }, onclick: closeNav },
      icon(mod.icon), el('span', {}, tr(`m_${mod.key}`)), mod.live ? null : el('span', { class: 'soon' }, tr('soon'))));
  }

  const companyLogo = profile.logo
    ? el('img', { class: 'co-logo', src: profile.logo, alt: '' })
    : el('span', { class: 'co-logo co-initials', 'aria-hidden': 'true' }, profile.initials);

  const sidebar = el('aside', { class: 'sidebar', id: 'sidebar' },
    el('div', { class: 'sb-brand' },
      el('div', { class: 'nx-brand' }, nexxusMark(30), el('div', { class: 'wordmark' }, 'NEXXUS', el('span', {}, 'MANAGER'))),
      el('div', { class: 'sb-company' }, companyLogo,
        el('div', { class: 'co-text' }, el('div', { class: 'co-name' }, profile.name),
          profile.phone ? el('div', { class: 'co-meta' }, profile.phone) : null))),
    nav,
    el('div', { class: 'sb-foot' },
      el('div', { class: 'sb-user', title: user.email }, userName),
      el('div', { class: 'sb-role' }, roleLabel),
      el('button', { class: 'btn btn-sm', type: 'button', onclick: () => state.sb.auth.signOut() }, tr('signOut')),
      el('a', { class: 'sb-site', href: '/' }, `← ${tr('websiteTitle')}`)));

  const tenantSelect = state.memberships.length > 1
    ? el('select', {
      class: 'input tenant-select', 'aria-label': tr('business'),
      onchange: (e) => { const m = state.memberships.find(x => x.tenant.id === e.target.value); setTenant(m); renderShell(); route(); },
    }, state.memberships.map(m => { const o = el('option', { value: m.tenant.id }, tenantProfile(m.tenant).name); if (m.tenant.id === tenant.id) o.selected = true; return o; }))
    : null;

  const langSelect = el('select', { class: 'input lang-select', 'aria-label': tr('language'), onchange: (e) => setLang(e.target.value) },
    [['es', 'ES'], ['en', 'EN']].map(([v, l]) => { const o = el('option', { value: v }, l); if (v === getLang()) o.selected = true; return o; }));

  const status = el('span', { class: 'sys-status checking', role: 'status', title: tr('status_checking') },
    el('span', { class: 'dot', 'aria-hidden': 'true' }), el('span', { class: 'label' }, tr('status_checking')));

  const content = el('main', { class: 'content', id: 'content', tabindex: '-1' });
  const topbar = el('header', { class: 'topbar' },
    el('button', { class: 'menu-btn', type: 'button', 'aria-label': tr('openMenu'), 'aria-controls': 'sidebar', onclick: toggleNav }, icon('menu')),
    el('div', { class: 'titles' },
      el('div', { class: 't-brand' }, nexxusMark(22), el('span', {}, tr('platform'))),
      el('div', { class: 't-sub' }, profile.name)),
    el('div', { class: 'date' }, new Intl.DateTimeFormat(getLang() === 'es' ? 'es-US' : 'en-US',
      { timeZone: profile.timezone, weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' }).format(new Date())),
    status,
    tenantSelect,
    langSelect,
    el('div', { class: 'tb-user' }, el('span', { class: 'tb-name', title: user.email }, userName), el('span', { class: 'tb-role' }, roleLabel)),
    el('button', { class: 'btn btn-sm tb-signout', type: 'button', onclick: () => state.sb.auth.signOut() }, tr('signOut')),
    el('a', { class: 'btn btn-sm site-link', href: '/', title: tr('websiteTitle') }, `← ${tr('website')}`));

  const shell = el('div', { class: 'shell' }, sidebar, el('div', { class: 'scrim', onclick: closeNav }),
    el('div', { class: 'main' }, topbar, content));
  clear(app).appendChild(shell);
  shellRefs = { shell, nav, content, status };
  checkStatus();
}

// Indicador discreto: el servidor responde y hay conexión. Sin datos sensibles.
let statusTimer = null;
async function checkStatus() {
  if (!shellRefs) return;
  let st = 'ok';
  if (!navigator.onLine) st = 'offline';
  else {
    try {
      const r = await fetch('/api/manager/config', { cache: 'no-store', credentials: 'same-origin' });
      if (!r.ok) st = 'degraded';
    } catch (_) { st = 'offline'; }
  }
  const s = shellRefs && shellRefs.status;
  if (!s) return;
  s.className = `sys-status ${st}`;
  s.title = tr(`status_${st}`);
  s.querySelector('.label').textContent = tr(`status_${st}`);
  clearTimeout(statusTimer);
  statusTimer = setTimeout(checkStatus, 60000);
}
window.addEventListener('online', checkStatus);
window.addEventListener('offline', checkStatus);

// Cambio de idioma: se redibuja el encabezado, el menú y el módulo actual.
window.addEventListener('aita:lang', () => {
  if (!state.ctx) return;
  document.documentElement.lang = getLang();
  document.title = `${tr('platform')} · ${state.ctx.profile.name}`;
  renderShell();
  route();
});

function toggleNav() { shellRefs.shell.classList.toggle('nav-open'); }
function closeNav() { shellRefs && shellRefs.shell.classList.remove('nav-open'); }

// ---------------------------------------------------------------------------
// Rutas (#/modulo/param)
// ---------------------------------------------------------------------------
let renderToken = 0;
async function route() {
  if (!state.ctx || !shellRefs) return;
  closeNav();
  const { tenant, role } = state.ctx;
  const home = homeModule(tenant, role);
  const [key = home, ...params] = location.hash.replace(/^#\/?/, '').split('/').filter(Boolean);
  const mod = MODULES.find(m => m.key === key);
  for (const a of shellRefs.nav.querySelectorAll('.sb-link')) {
    if (mod && a.dataset.key === mod.key) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
  }
  const token = ++renderToken;
  const content = clear(shellRefs.content);
  if (!mod || !canOpen(tenant, role, mod.key)) {
    // Módulo inexistente, deshabilitado para la empresa o no permitido para el rol.
    content.appendChild(el('section', { class: 'card soon-card' },
      el('span', { class: 'pill' }, tr('noAccessTitle')),
      el('p', {}, tr('noAccessText')),
      home ? el('p', {}, el('a', { class: 'btn', href: `#/${home}` }, tr('backHome'))) : null));
  } else {
    try {
      await mod.view.render(content, { ...state.ctx, params, module: { ...mod, label: tr(`m_${mod.key}`) }, isCurrent: () => token === renderToken });
    } catch (e) {
      if (token === renderToken) { clear(content).appendChild(errorBox(e)); }
    }
  }
  content.focus({ preventScroll: true });
  window.scrollTo(0, 0);
}

boot();
