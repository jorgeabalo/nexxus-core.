import { el } from '../ui.js';
import { tr } from '../i18n.js';

// Módulos preparados en la navegación y en tenants.modules, pero todavía sin
// vista. Cuando se construya uno, basta con crear su archivo en modules/,
// registrarlo en VIEWS (app.js), marcarlo `live` en nav.js y activarlo en
// tenants.modules.
export async function render(root, ctx) {
  const key = ctx.module.key;
  root.appendChild(el('section', { class: 'card soon-card' },
    el('span', { class: 'pill' }, tr('comingSoon')),
    el('h1', {}, tr(`m_${key}`)),
    el('p', {}, tr(`soon_${key}`))));

  // Configuración: mientras llega la edición, se muestra el perfil actual (solo lectura).
  if (key === 'settings' && ctx.profile) {
    const p = ctx.profile;
    const row = (k, v) => el('div', { class: 'dt' }, el('div', { class: 'k' }, k), el('div', { class: 'v' }, v || '—'));
    root.appendChild(el('section', { class: 'card' },
      el('div', { class: 'card-head' }, el('h2', {}, tr('profile'))),
      el('div', { class: 'detail-grid' },
        row(tr('business'), p.name), row(tr('phone'), p.phone), row(tr('address'), p.address),
        row(tr('timezone'), p.timezone), row(tr('languageLabel'), p.language ? p.language.toUpperCase() : p.locale),
        row(tr('modulesOn'), p.modules.map(k => tr(`m_${k}`)).join(', ')))));
  }
}
