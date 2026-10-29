// Versiones por plataforma: cómo se verá una publicación en cada red (texto dentro del límite y medida del
// visual) y el botón «Escribir con IA». Las reglas están en marketing-platforms-state.js.
import { el, clear, field, toast } from '../ui.js';
import { api } from '../api.js';
import { errText } from './marketing-i18n.js';
import { g } from './marketing-guide-i18n.js';
import { channelLabel } from './marketing-forms.js';
import { variantsFor } from './marketing-platforms-state.js';

function aspectLabel(a) {
  if (!a) return '';
  const [w, h] = a.split(':').map(Number);
  const kind = w === h ? 'a_square' : w > h ? 'a_landscape' : w / h <= 0.6 ? 'a_vertical' : 'a_portrait';
  return `${g('v_visual')}: ${g(kind)} ${a}`;
}

// Pestañas con una vista previa por red: texto, contador de caracteres, medida del visual y botón Copiar.
export function variantsPanel(c) {
  const list = variantsFor(c);
  if (!list.length) return el('p', { class: 'hint' }, g('v_none'));
  const holder = el('div', { class: 'mk-variant' });
  let current = list[0].channel;
  const bar = el('div', { class: 'mk-variant-tabs', role: 'tablist' });
  const show = () => {
    clear(bar).append(...list.map(v => el('button', { type: 'button', role: 'tab', 'aria-selected': String(v.channel === current),
      class: `btn btn-sm${v.channel === current ? ' btn-primary' : ''}`, onclick: () => { current = v.channel; show(); } }, channelLabel(v.channel))));
    const v = list.find(x => x.channel === current);
    const copy = el('button', { class: 'btn btn-sm', type: 'button', onclick: async () => {
      try { await navigator.clipboard.writeText(v.text); toast(g('v_copied')); } catch (e) { /* sin permiso: no pasa nada */ }
    } }, g('v_copy'));
    clear(holder).append(
      el('div', { class: 'mk-variant-meta' }, el('span', { class: v.text.length > v.limit ? 'form-error' : 'hint' }, g('v_chars', v.text.length, v.limit)),
        v.aspect ? el('span', { class: 'badge' }, aspectLabel(v.aspect)) : null, copy),
      el('div', { class: 'mk-variant-text' }, v.text || '—'));
  };
  show();
  return el('div', { class: 'mk-variants' }, el('h4', {}, g('v_title')), bar, holder);
}

// «Escribir con IA»: pide al backend el texto y las versiones por red. onDone recibe el contenido actualizado.
export function aiWriter(ctx, c, onDone) {
  const req = el('input', { class: 'input', maxlength: '1000', placeholder: g('ai_request_ph') });
  const msg = el('p', { class: 'hint', role: 'status' });
  const btn = el('button', { class: 'btn btn-sm btn-accent', type: 'button', onclick: async () => {
    btn.disabled = true; msg.className = 'hint'; msg.textContent = g('ai_writing');
    try {
      const out = await api.marketing.writeCopy(ctx.tenantId, c.id, req.value.trim());
      msg.textContent = g('ai_done');
      onDone(out.content);
    } catch (e) {
      msg.className = e?.code === 'text_ai_unavailable' ? 'mk-note' : 'form-error';
      msg.textContent = e?.code === 'text_ai_unavailable' ? g('ai_off') : errText(e);
    } finally { btn.disabled = false; }
  } }, c.caption ? g('ai_rewrite') : g('ai_write'));
  return el('div', { class: 'mk-ai-writer' }, field(g('ai_request'), req, { full: true }), el('div', { class: 'btn-row' }, btn), msg);
}
