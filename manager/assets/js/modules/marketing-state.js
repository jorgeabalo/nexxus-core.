// AITA Marketing — lógica pura del módulo (sin DOM) para poder probarla con Node.
//
// Estado de la vista ligado a UNA empresa: al cambiar de tenant se descarta todo
// (pestaña, mes del calendario y cualquier dato en memoria), así nunca se ven
// colores, logo ni contenido de la empresa anterior.

export const TABS = ['overview', 'content', 'calendar', 'campaigns', 'brand'];
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
