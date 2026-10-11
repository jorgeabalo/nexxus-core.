// Marketing: aviso de error con SOLO el mensaje ya traducido al idioma activo (sin prefijos genéricos
// en otro idioma, como el "Could not load data:" del errorBox compartido).
import { el } from '../ui.js';

export function errorNotice(message) {
  return el('div', { class: 'error-box', role: 'alert' }, String(message || ''));
}
