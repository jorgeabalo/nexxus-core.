import { el } from '../ui.js';

// Módulos preparados en la navegación y en tenants.modules, pero todavía sin
// vista. Cuando se construya uno, basta con crear su archivo en modules/ y
// registrarlo en MODULES (app.js) y activarlo en tenants.modules.
const COPY = {
  accounting: 'Income, expenses and reports connected to your payments.',
  marketing: 'Campaigns, reminders and member re-engagement.',
  inventory: 'Stock of products, supplements and equipment.',
  agents: 'Additional AI agents that work alongside Claudia.',
  settings: 'Business profile, team access, hours and branding.',
};

export async function render(root, ctx) {
  const key = ctx.module.key;
  root.appendChild(el('section', { class: 'card soon-card' },
    el('span', { class: 'pill' }, 'Coming soon'),
    el('h1', {}, ctx.module.label),
    el('p', {}, COPY[key] || 'This module is being prepared.')));
}
