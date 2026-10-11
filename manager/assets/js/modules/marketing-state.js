// AITA Marketing — lógica pura del módulo (sin DOM) para poder probarla con Node.
//
// Estado de la vista ligado a UNA empresa: al cambiar de tenant se descarta todo
// (pestaña, mes del calendario y cualquier dato en memoria), así nunca se ven
// colores, logo ni contenido de la empresa anterior.

// Navegación visible: exactamente estas cinco pestañas. La identidad de marca es una acción secundaria.
export const TABS = ['overview', 'content', 'calendar', 'campaigns', 'library'];
// Enlaces anteriores del Estudio y de Trabajos llevan a Contenido; el del Estudio abre el planificador.
export const REDIRECTS = { studio: { tab: 'content', planner: true }, jobs: { tab: 'content', planner: false } };
export function route(param) {
  if (TABS.includes(param)) return { tab: param, planner: false, brand: false };
  if (param === 'brand') return { tab: 'overview', planner: false, brand: true };
  if (REDIRECTS[param]) return { ...REDIRECTS[param], brand: false, redirected: true };
  return null;
}

// Estados que ve el cliente (los mismos que devuelve el backend en display_status).
export const DISPLAY_STATUSES = ['draft', 'preparing', 'ready_for_review', 'changes_requested', 'approved', 'scheduled',
  'published', 'could_not_prepare', 'rejected', 'archived'];
const SHOWN = { idea: 'draft', review: 'ready_for_review', failed: 'could_not_prepare', publishing: 'scheduled' };
export const shownStatus = (s) => (DISPLAY_STATUSES.includes(s) ? s : SHOWN[s] || 'draft');

// Planear contenido: 6 pasos. Dos modos de creación y cuatro formatos (formatos, no módulos).
export const PLAN_STEPS = ['objective', 'mode', 'format', 'instructions', 'proposal', 'review'];
export const PLAN_OBJECTIVES = ['calls', 'messages', 'trial_visit', 'memberships', 'reactivation', 'reviews'];
export const PLAN_MODES = ['library_ai', 'fully_ai'];
export const PLAN_FORMATS = ['image', 'carousel', 'reel', 'video'];
export const TONES = ['energetic', 'calm', 'professional', 'cinematic', 'educational'];

// Qué falta para avanzar desde un paso (null = se puede seguir). La IA puede proponer; decide el usuario.
export function planStepError(step, w) {
  if (step === 'objective' && !PLAN_OBJECTIVES.includes(w.objective)) return 'pick_objective';
  if (step === 'mode') {
    if (!PLAN_MODES.includes(w.mode)) return 'pick_mode';
    if (w.mode === 'library_ai' && !(w.media && w.media.length)) return 'real_media_required';
    if (w.mode === 'fully_ai' && w.media && w.media.length) return 'fully_ai_uses_no_media';
  }
  if (step === 'format' && !PLAN_FORMATS.includes(w.format)) return 'pick_format';
  if (step === 'instructions' && !(w.platforms && w.platforms.length)) return 'channels_required';
  return null;
}

// Cuerpo del envío (solo lo que el usuario decidió). Completamente con IA nunca lleva archivos.
export function planBody(w, labels = {}) {
  return {
    objective: w.objective, mode: w.mode, format: w.format,
    media_ids: w.mode === 'library_ai' ? [...(w.media || [])] : [],
    instructions: { topic: w.topic || '', audience: w.audience || '', offer: w.offer || '', tone: w.tone || 'professional',
      cta: w.cta || '', platforms: [...(w.platforms || [])], preferred_date: w.date || null },
    language: w.language === 'en' ? 'en' : 'es', idempotency_key: w.key, labels,
  };
}
export const FORMATS = ['image', 'carousel', 'reel', 'story', 'video', 'text'];
export const CHANNELS = ['instagram', 'facebook', 'tiktok', 'linkedin', 'x', 'youtube', 'google_business', 'threads'];
export const LANGUAGES = ['en', 'es'];
// Las acciones posibles de cada pieza las decide el backend (campo `allowed`).
export const EDITABLE = ['idea', 'draft', 'rejected'];

export function createScope() {
  let tenantId = null;
  let data = {};
  return {
    // Devuelve el estado de este tenant; si cambió la empresa, empieza de cero.
    for(id) {
      if (id !== tenantId) { tenantId = id; data = { tab: 'overview', month: null }; }
      return data;
    },
    get tenantId() { return tenantId; },
  };
}

// Colores que muestra el Brand Kit: los guardados o, si no hay perfil, los del
// tenant ACTIVO que manda el backend. Nunca se reutilizan valores previos.
const HEX = /^#[0-9A-Fa-f]{6}$/;
export function brandSwatches(profile) {
  const p = profile || {};
  return ['color_primary', 'color_accent', 'color_secondary']
    .map(k => ({ key: k, value: HEX.test(p[k] || '') ? p[k] : null }));
}

export function monthStart(iso) { return `${iso.slice(0, 7)}-01`; }
export function shiftMonth(iso, n) {
  const [y, m] = iso.split('-').map(Number);
  return new Date(Date.UTC(y, m - 1 + n, 1)).toISOString().slice(0, 10);
}
export function monthEnd(iso) {
  return new Date(Date.parse(shiftMonth(monthStart(iso), 1)) - 86400000).toISOString().slice(0, 10);
}

// Fecha local (YYYY-MM-DD) de un instante en la zona del tenant.
export function localDay(isoInstant, timeZone) {
  if (!isoInstant) return null;
  const parts = new Intl.DateTimeFormat('en-CA', { timeZone, year: 'numeric', month: '2-digit', day: '2-digit' })
    .formatToParts(new Date(isoInstant));
  const get = (t) => parts.find(p => p.type === t).value;
  return `${get('year')}-${get('month')}-${get('day')}`;
}

// 'YYYY-MM-DDTHH:MM' en la zona del tenant para <input type="datetime-local">.
export function localInput(isoInstant, timeZone) {
  if (!isoInstant) return '';
  const parts = new Intl.DateTimeFormat('en-CA', { timeZone, year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).formatToParts(new Date(isoInstant));
  const get = (t) => parts.find(p => p.type === t).value;
  return `${get('year')}-${get('month')}-${get('day')}T${get('hour')}:${get('minute')}`;
}

// Semanas (lunes a domingo) que cubren el mes, con los elementos de cada día.
export function monthGrid(monthIso, items, timeZone) {
  const first = monthStart(monthIso);
  const last = monthEnd(monthIso);
  const start = new Date(`${first}T00:00:00Z`);
  start.setUTCDate(start.getUTCDate() - ((start.getUTCDay() + 6) % 7));
  const byDay = {};
  for (const it of items || []) {
    const day = localDay(it.scheduled_at || it.planned_at, timeZone);
    if (day) (byDay[day] = byDay[day] || []).push(it);
  }
  const weeks = [];
  const cur = new Date(start);
  while (cur.toISOString().slice(0, 10) <= last) {
    const week = [];
    for (let i = 0; i < 7; i++) {
      const iso = cur.toISOString().slice(0, 10);
      week.push({ date: iso, inMonth: iso >= first && iso <= last, items: byDay[iso] || [] });
      cur.setUTCDate(cur.getUTCDate() + 1);
    }
    weeks.push(week);
  }
  return weeks;
}

// Porcentaje de uso del plan (null = sin límite).
export function usageBar(used, limit) {
  if (limit === null || limit === undefined) return { used, limit: null, pct: null, full: false };
  const pct = limit === 0 ? 100 : Math.min(100, Math.round((used / limit) * 100));
  return { used, limit, pct, full: used >= limit };
}
