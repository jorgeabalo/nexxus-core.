// Tarjeta de bienvenida: catálogo de mensajes (ES/EN) que rota a diario y se
// personaliza SOLO con datos reales (nombre, visitas registradas, evaluación
// pendiente). No inventa logros, cambios físicos ni resultados médicos.
import { S, el, t } from './util.js';
import { evalState } from './evaluation.js';

const COUNTS = { first: 3, onboardingNew: 3, onboardingExisting: 3, consistency: 3, return: 3, general: 4 };

function daysSince(iso) {
  if (!iso) return null;
  return Math.floor((Date.now() - new Date(iso).getTime()) / 86400000);
}
function pick(cat, seed) {
  const n = COUNTS[cat];
  const day = Math.floor(Date.now() / 86400000);
  return (day + seed) % n;
}

export function welcomeKind() {
  const m = S.data.member;
  const v = S.data.visits || {};
  const st = evalState();
  if (!m.portal_last_seen_at) return 'first';
  const away = daysSince(v.last_visit);
  if (v.total > 0 && away !== null && away >= 21) return 'return';
  if (!st.initial) return m.joined_as === 'new' ? 'onboardingNew' : 'onboardingExisting';
  if ((v.last_30_days || 0) > 0) return 'consistency';
  return 'general';
}

export function welcomeCard() {
  const m = S.data.member;
  const kind = welcomeKind();
  const seed = [...String(m.id)].reduce((a, c) => a + c.charCodeAt(0), 0);
  const i = pick(kind, seed);
  const vars = { name: m.first_name || '', n: S.data.visits?.last_30_days || 0 };
  const extra = kind === 'first' ? el('p', { class: 'small m0 welcome-sub' }, t('wel.first.guide'))
    : kind.startsWith('onboarding') ? el('a', { class: 'welcome-link', href: '#/evaluation' }, t('wel.onb.cta')) : null;
  return el('section', { class: `card welcome welcome-${kind}`, 'aria-live': 'polite' },
    el('p', { class: 'welcome-msg m0' }, t(`wel.${kind}.${i}`, vars)), extra);
}
