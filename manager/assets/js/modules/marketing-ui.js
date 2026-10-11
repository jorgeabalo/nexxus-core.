// Marketing: aviso de error con SOLO el mensaje ya traducido al idioma activo (sin prefijos genéricos
// en otro idioma, como el "Could not load data:" del errorBox compartido).
import { el, tz } from '../ui.js';
import { getLang } from '../i18n.js';
import { fmtWhen, fmtDay } from './marketing-state.js';

export function errorNotice(message) {
  return el('div', { class: 'error-box', role: 'alert' }, String(message || ''));
}

// Fechas de Marketing en el idioma activo y la zona horaria del tenant (ver fmtWhen/fmtDay).
export const when = (iso) => fmtWhen(iso, getLang(), tz());
export const day = (d) => fmtDay(d, getLang());
