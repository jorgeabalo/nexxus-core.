// AITA Marketing: plan de publicaciones, "¿Necesitas más publicaciones?" (en Campañas), flujo de AITA y resultados.
// El manager solicita; el owner (o un manager con su permiso) autoriza. La confirmación muestra la frase exacta
// y se desmarca si cambia la cantidad, la frecuencia o el precio. El navegador nunca envía el precio.
import { el, clear, card, toast } from '../ui.js';
import { api } from '../api.js';
import { getLang } from '../i18n.js';
import { p, pErr } from './marketing-plan-i18n.js';
import { s as sText } from './marketing-studio-i18n.js';
import { OBJECTIVES, newIdempotencyKey } from './marketing-studio-state.js';
import { ADDON_PRESETS, WEEKLY_PRESETS, WEEKS_PER_MONTH, addonSpec, addonBody, fmtPrice, confirmSignature,
  blocksText } from './marketing-addons-state.js';

const M = api.marketing;
const fmtDay = (iso) => new Intl.DateTimeFormat(getLang() === 'es' ? 'es-US' : 'en-US',
  { day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC' }).format(new Date(`${String(iso).slice(0, 10)}T12:00:00Z`));
const localToday = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`; };
const dl = (rows) => el('dl', { class: 'mk-summary' }, ...rows.flatMap(([k, v]) => [el('dt', {}, k), el('dd', {}, v)]));

export function planCard(plan) {
  if (!plan) return null;
  if (plan.total === null) return card(p('plan_title'), el('div', { class: 'card-body' }, el('p', { class: 'hint' }, p('unlimited'))));
  return card(p('plan_title'), el('div', { class: 'card-body' },
    dl([[p('included'), String(plan.included)], [p('added'), String(plan.added || 0)],
      [p('used'), String(plan.used)], [p('available'), String(plan.available)]]),
    plan.next_total !== null && plan.next_total !== undefined && plan.next_total !== plan.total
      ? el('p', { class: 'mk-note' }, p('next_total', fmtDay(plan.next_cycle), plan.next_total)) : null,
    el('p', { class: 'hint' }, p('plan_kinds'))));
}

// Casilla de confirmación con la frase exacta. `onAct` recibe el botón; se habilita solo con la casilla marcada.
function confirmRow(phrase, label, onAct, cls = 'btn btn-accent') {
  const box = el('input', { type: 'checkbox' });
  const btn = el('button', { class: cls, type: 'button', disabled: true }, label);
  box.addEventListener('change', () => { btn.disabled = !box.checked; });
  btn.addEventListener('click', async () => { if (!box.checked) return; btn.disabled = true; await onAct(btn); });
  return el('div', { class: 'mk-addon-confirm' }, el('label', { class: 'mk-check' }, box, el('span', {}, phrase)), btn);
}

export function addonSection(ctx, plan, role, reload) {
  if (!plan || plan.total === null) return null;
  const approver = plan.can_authorize === true;
  const st = { cadence: 'monthly', choice: 4, custom: '', key: newIdempotencyKey(), sig: '' };
  const body = el('div', { class: 'card-body mk-addon' });

  const spec = () => addonSpec(st.cadence, st.choice === 'custom' ? st.custom : st.choice);
  const optionsFor = () => (st.cadence === 'weekly' ? WEEKLY_PRESETS : ADDON_PRESETS);

  function draw() {
    const sp = spec();
    const sig = confirmSignature(sp);
    if (sig !== st.sig) { st.sig = sig; st.key = newIdempotencyKey(); }   // otra cantidad/frecuencia/precio = otra solicitud
    const seg = el('div', { class: 'btn-row mk-seg', role: 'group', 'aria-label': p('ad_title') },
      ...['monthly', 'weekly'].map(c => el('button', { type: 'button', class: `btn btn-sm${st.cadence === c ? ' btn-accent' : ''}`,
        'aria-pressed': String(st.cadence === c), onclick: () => { st.cadence = c; st.choice = c === 'weekly' ? 1 : 4; st.custom = ''; draw(); } },
      p(c))));
    const radios = el('div', { class: 'mk-addon-options', role: 'radiogroup' },
      ...[...optionsFor(), 'custom'].map(v => {
        const q = v === 'custom' ? null : addonSpec(st.cadence, v);
        const text = v === 'custom' ? p('custom')
          : st.cadence === 'weekly' ? p('weekly_preset', v, v * WEEKS_PER_MONTH, fmtPrice(q.amount)) : p('preset', v, fmtPrice(q.amount));
        return el('label', { class: 'mk-check mk-addon-option' },
          el('input', { type: 'radio', name: 'mk-addon-choice', checked: st.choice === v,
            onchange: () => { st.choice = v; draw(); } }), el('span', {}, text));
      }));
    const custom = st.choice !== 'custom' ? null : el('label', { class: 'field' },
      el('span', {}, p(st.cadence === 'weekly' ? 'custom_weekly' : 'custom_monthly')),
      el('input', { class: 'input mk-addon-qty', type: 'number', min: '1', max: st.cadence === 'weekly' ? '125' : '500', step: '1',
        inputmode: 'numeric', value: st.custom,
        oninput: (e) => { st.custom = e.target.value; refresh(); } }));
    const detail = el('div', { class: 'mk-addon-quote', 'aria-live': 'polite' });
    function refresh() {
      const q = spec();
      const s2 = confirmSignature(q);
      if (s2 !== st.sig) { st.sig = s2; st.key = newIdempotencyKey(); }
      clear(detail).append(...quoteBlock(q));
    }
    function quoteBlock(q) {
      if (!q) return [el('p', { class: 'form-error' }, p('invalid'))];
      // Una sola ampliación: si ya hay una en este ciclo, la nueva la reemplaza desde el ciclo siguiente.
      const change = Boolean(plan.current);
      return [
        st.cadence === 'weekly' ? el('p', {}, p('equiv', q.per_week, q.additional)) : null,
        st.cadence === 'weekly' ? el('p', { class: 'hint', dataset: { note: 'weekly' } }, p('weekly_note')) : null,
        el('p', { class: 'hint' }, p('combo', blocksText(q.blocks))),
        dl([[p('s_included'), String(plan.included)], [p('s_added'), String(q.additional)],
          [p('s_total'), String(plan.included + q.additional)], [p('s_price'), fmtPrice(q.amount)],
          [p('s_start'), change ? fmtDay(plan.next_cycle) : fmtDay(localToday())]]),
        change ? el('p', { class: 'hint', dataset: { note: 'change' } }, p('change_note', fmtDay(plan.next_cycle))) : null,
        el('p', { class: 'mk-note' }, p('recurring')),
        approver ? null : el('p', { class: 'hint' }, p('owner_note')),
        confirmRow(p(approver ? 'confirm_authorize' : 'confirm_request', q.additional, fmtPrice(q.amount)),
          p(approver ? 'authorize' : 'request'), async (btn) => {
            if (confirmSignature(spec()) !== confirmSignature(q)) { refresh(); return; }   // cambió: confirmar de nuevo
            try {
              await M.submitPublicationAddon(ctx.tenantId, approver ? 'authorize' : 'request', addonBody(q), st.key);
              toast(p(approver ? 'authorized' : 'requested'));
              await reload();
            } catch (e) { toast(pErr(e), 'error'); btn.disabled = false; }
          }),
      ].filter(Boolean);
    }
    clear(body).append(el('p', {}, p('ad_intro')), seg, radios, custom || el('span'), detail);
    refresh();
  }
  draw();

  const act = (id, action, okText) => async (btn) => {
    btn.disabled = true;
    try { await M.publicationAddonAction(ctx.tenantId, id, action); toast(p(okText)); await reload(); }
    catch (e) { toast(pErr(e), 'error'); btn.disabled = false; }
  };
  const rowText = (a) => `${p('row', a.additional_publications, fmtPrice(a.amount))}${a.cadence === 'weekly' ? ` ${p('row_weekly', a.per_week)}` : ''}`;
  const pending = (plan.pending || []).length ? el('div', { class: 'mk-addon-list' }, el('h3', {}, p('pending_title')),
    ...plan.pending.map(a => el('div', { class: 'mk-addon-item' },
      el('p', {}, rowText(a), a.requested_by_me ? [' · ', el('strong', { class: 'mk-addon-state' }, p('by_me'))] : null),
      approver ? confirmRow(p('confirm_authorize', a.additional_publications, fmtPrice(a.amount)), p('approve'),
        act(a.id, 'approve', 'authorized')) : null,
      approver ? el('button', { class: 'btn btn-sm', type: 'button', onclick: (e) => act(a.id, 'decline', 'declined')(e.target) },
        p('decline')) : null))) : null;
  const mine = [plan.current, plan.upcoming].filter(Boolean);
  const active = mine.length ? el('div', { class: 'mk-addon-list' }, el('h3', {}, p('active_title')),
    ...mine.map(a => el('div', { class: 'mk-addon-item', dataset: { state: a.state } },
      el('p', {}, rowText(a), ' · ', el('strong', { class: 'mk-addon-state' }, p(`state_${a.state}`))),
      el('p', { class: 'hint' }, [a.state === 'upcoming' ? p('since', fmtDay(a.starts_cycle)) : null,
        a.ends_cycle ? p('until', fmtDay(a.ends_cycle)) : null].filter(Boolean).join(' · ')),
      approver && ['active', 'upcoming'].includes(a.state) ? confirmRow(p('cancel_confirm', a.additional_publications,
        fmtPrice(a.amount), fmtDay(a.state === 'upcoming' ? a.starts_cycle : plan.next_cycle)), p('cancel'),
      act(a.id, 'cancel', 'cancelled'), 'btn btn-sm') : null))) : null;
  const delegate = role !== 'owner' ? null : el('label', { class: 'mk-check' },
    el('input', { type: 'checkbox', checked: plan.managers_can_authorize === true, onchange: async (e) => {
      try { await M.setPurchasePermission(ctx.tenantId, e.target.checked); toast(p('delegate_saved')); }
      catch (err) { e.target.checked = !e.target.checked; toast(pErr(err), 'error'); }
    } }), el('span', {}, p('delegate')));
  return card(p('ad_title'), el('div', {}, body, pending, active, delegate ? el('div', { class: 'card-body' }, delegate) : null));
}

export function flowCard() {
  return card(p('flow_title'), el('div', { class: 'card-body' },
    el('ol', { class: 'mk-flow' }, ...p('flow').map(x => el('li', {}, x))),
    el('p', { class: 'hint' }, `${p('goals_title')}: ${OBJECTIVES.map(o => sText(`ob_${o}`)).join(' · ')}`),
    el('p', { class: 'mk-note' }, p('flow_note'))));
}

export function resultsCard() {
  return card(p('results_title'), el('div', { class: 'card-body' },
    el('p', { class: 'hint' }, p('results_none')),
    el('ul', { class: 'mk-flow' }, ...p('metrics').map(x => el('li', {}, x)))));
}
