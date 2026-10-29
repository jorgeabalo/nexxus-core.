// Primeros pasos de Marketing: una lista corta que lleva de la mano a quien entra por primera vez
// (marca → redes → fotos y vídeos → primera publicación). Cada paso se marca solo con datos reales del
// backend; la guía desaparece cuando todo está hecho o si la persona la oculta (solo en este navegador).
import { el } from '../ui.js';
import { api } from '../api.js';
import { g } from './marketing-guide-i18n.js';

const key = (tenantId) => `mk-guide-hidden:${tenantId}`;
function hidden(tenantId) { try { return localStorage.getItem(key(tenantId)) === '1'; } catch (e) { return false; } }
function hide(tenantId) { try { localStorage.setItem(key(tenantId), '1'); } catch (e) { /* sin almacenamiento */ } }

export async function guideCard(ctx, dashboard, go) {
  if (hidden(ctx.tenantId)) return null;
  const [brand, library] = await Promise.all([
    api.marketing.brand(ctx.tenantId).catch(() => null),
    api.marketing.library(ctx.tenantId).catch(() => null)]);
  const counts = dashboard.counts || {};
  const posts = Object.entries(counts).filter(([k]) => k !== 'waiting_approval').reduce((n, [, v]) => n + (v || 0), 0);
  const steps = [
    { id: 'brand', done: !!brand && !brand.inherited, action: go.brand },
    { id: 'social', done: !!brand && Object.keys(brand.profile?.social_links || {}).length > 0, action: go.brand },
    { id: 'library', done: !!library && (library.items || []).length > 0, action: go.library },
    { id: 'first', done: posts > 0, action: go.create },
  ];
  const ready = steps.filter(s => s.done).length;
  if (ready === steps.length) return null;
  const next = steps.find(s => !s.done);
  const node = el('section', { class: 'card mk-guide' }, el('div', { class: 'card-body' },
    el('div', { class: 'mk-guide-head' },
      el('div', {}, el('h2', {}, g('g_title')), el('p', { class: 'hint' }, g('g_intro'))),
      el('div', { class: 'btn-row' }, el('span', { class: 'badge' }, g('g_progress', ready, steps.length)),
        el('button', { class: 'btn btn-sm', type: 'button', onclick: () => { hide(ctx.tenantId); node.remove(); } }, g('g_hide')))),
    el('ol', { class: 'mk-guide-steps' }, ...steps.map((s, i) => el('li', { class: s.done ? 'done' : s === next ? 'next' : '' },
      el('span', { class: 'mk-guide-num', 'aria-hidden': 'true' }, s.done ? '✓' : String(i + 1)),
      el('div', {}, el('strong', {}, g(`g_${s.id}`)), el('p', { class: 'hint' }, g(`g_${s.id}_d`))),
      s.done ? el('span', { class: 'hint' }, g('g_ok'))
        : el('button', { class: `btn btn-sm${s === next ? ' btn-accent' : ''}`, type: 'button', disabled: s.id === 'first' && go.createDisabled,
          onclick: s.action }, g('g_go')))))));
  return node;
}
