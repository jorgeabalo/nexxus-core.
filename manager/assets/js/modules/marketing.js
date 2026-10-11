// AITA Marketing: Resumen, Contenido (con «Planear contenido»), Calendario, Campañas y Biblioteca.
// La identidad de marca es una acción secundaria. La preparación con IA es interna: aquí solo hay contenido
// con estados comerciales.
// Los datos vienen del backend (/api/manager/marketing/*), que valida rol
// (owner/manager), tenant y reglas de estado. Nada se publica en redes todavía.
// El estado de la vista es por empresa: al cambiar de tenant se empieza de cero.
import { el, clear, card, kpi, table, tabs, loading, todayISO, select, tz } from '../ui.js';
import { errorNotice, when, day } from './marketing-ui.js';
import { api } from '../api.js';
import { t, errText, getLang } from './marketing-i18n.js';
import { TABS, DISPLAY_STATUSES, route, createScope, monthStart, shiftMonth, monthEnd, monthGrid, usageBar } from './marketing-state.js';
import { contentModal, contentDetail, campaignModal, brandForm, channelLabel, statusBadge } from './marketing-forms.js';
import { libraryView } from './marketing-library.js';
import { plannerModal } from './marketing-planner.js';
import { controlCard } from './marketing-autopilot.js';
import { planCard, addonSection, flowCard, resultsCard } from './marketing-addons.js';

const M = api.marketing;
const scope = createScope();

export async function render(root, ctx) {
  const st = scope.for(ctx.tenantId);
  // Enlaces anteriores: Estudio → Contenido con el planificador abierto; Trabajos → Contenido.
  const r = ctx.params[0] ? route(ctx.params[0]) : null;
  let openPlanner = false;
  if (r) {
    st.tab = r.tab; st.brand = r.brand; openPlanner = r.planner;
    if (r.redirected) history.replaceState(null, '', `#/marketing/${r.tab}`);
  }
  let campaigns = [];
  let enabled = false;
  let gate = Promise.resolve();

  const plan = () => plannerModal(ctx, reload);
  const createBtn = el('button', { class: 'btn btn-accent', type: 'button', disabled: true, onclick: plan }, `+ ${t('create')}`);
  const brandBtn = el('button', { class: 'btn', type: 'button', onclick: () => {
    st.brand = true; history.replaceState(null, '', '#/marketing/brand'); drawTabs(); draw(); } }, t('tab_brand'));
  root.appendChild(el('div', { class: 'page-head' },
    el('div', {}, el('h1', {}, t('title')), el('p', {}, t('subtitle'))),
    el('div', { class: 'btn-row' }, brandBtn, createBtn)));
  const notice = el('div');
  const tabHolder = el('div', { class: 'toolbar' });
  const body = el('div', { class: 'mk-body' });
  root.append(notice, tabHolder, body);

  const drawTabs = () => clear(tabHolder).appendChild(tabs(TABS.map(v => ({ value: v, label: t(`tab_${v}`) })), st.brand ? null : st.tab,
    (v) => { st.tab = v; st.brand = false; history.replaceState(null, '', `#/marketing/${v}`); drawTabs(); draw(); }));
  drawTabs();
  const open = (id) => contentDetail(ctx, id, { campaigns, reload });

  async function reload() {
    if (!ctx.isCurrent()) return;
    try {
      campaigns = (await M.campaigns(ctx.tenantId)).campaigns;
    } catch (e) { campaigns = []; }
    await draw();
  }

  async function draw() {
    if (!ctx.isCurrent()) return;
    clear(body).appendChild(loading());
    try {
      const view = st.brand ? brand : { overview, content, calendar, campaigns: campaignList, library }[st.tab];
      const node = await view();
      if (ctx.isCurrent()) clear(body).appendChild(node);
    } catch (e) {
      if (ctx.isCurrent()) clear(body).appendChild(errorNotice(errText(e)));
    }
  }

  // ---------- resumen ----------
  // Ancho por CSSOM: la CSP del panel no permite atributos style en línea.
  const meter = (u) => {
    const fill = el('span');
    fill.style.width = `${u.pct}%`;
    return el('div', { class: `mk-meter${u.full ? ' full' : ''}`, role: 'progressbar', 'aria-valuemin': '0',
      'aria-valuemax': String(u.limit), 'aria-valuenow': String(u.used) }, fill);
  };
  async function overview() {
    const d = await M.dashboard(ctx.tenantId);
    enabled = d.settings.marketing_enabled;
    createBtn.disabled = !enabled;
    clear(notice).appendChild(el('div', {},
      enabled ? null : el('div', { class: 'error-box mk-notice' }, t('disabled')),
      el('p', { class: 'hint' }, d.settings.approval_required ? `${t('approvalOn')} ` : '', t('noPublishing'))));
    const k = d.counts, plan = d.publication_plan;
    const recs = d.recommendations || [];
    const bar = (label, used, limit) => {
      const u = usageBar(used, limit);
      return el('div', { class: 'mk-usage' },
        el('div', { class: 'mk-usage-head' }, el('span', {}, label),
          el('span', {}, u.limit === null ? `${used} · ${t('unlimited')}` : `${used} ${t('of')} ${u.limit}`)),
        u.pct === null ? null : meter(u));
    };
    return el('div', {},
      el('div', { class: 'kpis mk-kpis' },
        kpi(t('k_used'), plan.total === null ? String(plan.used) : `${plan.used} ${t('of')} ${plan.total}`),
        kpi(t('k_upcoming'), String(d.upcoming.length)),
        kpi(t('k_waiting'), String(k.waiting_approval || 0), '', k.waiting_approval > 0),
        kpi(t('k_published'), String(k.published || 0))),
      card(t('recTitle'), el('div', { class: 'card-body' }, recs.length
        ? el('ul', { class: 'mk-flow mk-recs' }, ...recs.map(x => el('li', {}, t(`rec_${x.code}`, x.count))))
        : el('p', { class: 'hint' }, t('noRecs')))),
      el('div', { class: 'grid-2' },
        el('div', {}, planCard(plan), el('div', { class: 'card-body' }, bar(t('posts'), plan.used, plan.total))),
        card(t('upcoming'), table([
          { label: t('date'), render: r => when(r.scheduled_at || r.planned_at) },
          { label: t('titleF'), key: 'title' },
          { label: t('format'), render: r => t(`f_${r.format}`) },
          { label: t('status'), render: r => statusBadge(r.display_status) },
        ], d.upcoming, { onRowClick: r => open(r.id), emptyText: t('noUpcoming') }))),
      el('div', { class: 'grid-2' }, flowCard(), resultsCard(d.results)));
  }

  // ---------- contenido ----------
  async function content() {
    const filter = select([{ value: '', label: t('all') }, ...DISPLAY_STATUSES.filter(s => s !== 'archived')
      .map(s => ({ value: s, label: t(`s_${s}`), selected: s === st.status }))],
    { 'aria-label': t('status'), onchange: (e) => { st.status = e.target.value; draw(); } });
    await gate;
    const all = (await M.contentList(ctx.tenantId)).content;
    const rows = st.status ? all.filter(r => r.display_status === st.status) : all;
    const camp = Object.fromEntries(campaigns.map(c => [c.id, c.name]));
    // Modo de control: si aún no está disponible (migración sin aplicar), Contenido sigue funcionando sin él.
    const control = enabled ? await controlCard(ctx, draw).catch(() => null) : null;
    return el('div', { class: 'mk-content' }, control, el('section', { class: 'card' },
      el('div', { class: 'card-head' }, el('h2', {}, t('tab_content')),
        el('div', { class: 'btn-row' }, filter,
          el('button', { class: 'btn btn-sm', type: 'button', disabled: !enabled, onclick: () => contentModal(ctx, { campaigns }, reload) }, t('newBlank')),
          el('button', { class: 'btn btn-sm btn-accent', type: 'button', disabled: !enabled, onclick: plan }, t('create')))),
      table([
        { label: t('titleF'), key: 'title' },
        { label: t('status'), render: r => statusBadge(r.display_status) },
        { label: t('format'), render: r => t(`f_${r.format}`) },
        { label: t('channels'), render: r => (r.channels || []).map(channelLabel).join(', ') },
        { label: t('campaign'), render: r => camp[r.campaign_id] || null },
        { label: t('date'), render: r => (r.scheduled_at || r.planned_at) ? when(r.scheduled_at || r.planned_at) : null },
      ], rows, { onRowClick: r => open(r.id), emptyText: t('noContent') })));
  }

  // ---------- calendario mensual ----------
  async function calendar() {
    st.month = st.month || monthStart(todayISO());
    const start = st.month, end = monthEnd(st.month);
    const data = await M.calendar(ctx.tenantId, start, end);
    const zone = data.timezone || tz();
    const weeks = monthGrid(start, data.items, zone);
    const title = new Intl.DateTimeFormat(getLang() === 'es' ? 'es-US' : 'en-US', { month: 'long', year: 'numeric', timeZone: 'UTC' })
      .format(new Date(`${start}T12:00:00Z`));
    const nav = (n) => () => { st.month = n === 0 ? monthStart(todayISO()) : shiftMonth(st.month, n); draw(); };
    const today = todayISO();
    const chip = (it) => el('button', { class: `mk-chip mk-s-${it.display_status}`, type: 'button', onclick: () => open(it.id),
      title: [it.title, t(`s_${it.display_status}`), t(`f_${it.format}`), (it.channels || []).map(channelLabel).join(', '),
        it.campaign_name, it.assignee_name].filter(Boolean).join(' · ') },
    el('span', { class: 'mk-chip-title' }, it.title),
    el('span', { class: 'mk-chip-meta' }, [t(`s_${it.display_status}`), t(`f_${it.format}`), (it.channels || []).map(channelLabel).join(', ')]
      .filter(Boolean).join(' · ')),
    it.campaign_name || it.assignee_name
      ? el('span', { class: 'mk-chip-meta' }, [it.campaign_name, it.assignee_name].filter(Boolean).join(' · ')) : null);
    return el('section', { class: 'card mk-cal' },
      el('div', { class: 'card-head' }, el('h2', {}, title),
        el('div', { class: 'btn-row' },
          el('button', { class: 'btn btn-sm', type: 'button', 'aria-label': t('prevMonth'), onclick: nav(-1) }, '‹'),
          el('button', { class: 'btn btn-sm', type: 'button', onclick: nav(0) }, t('today')),
          el('button', { class: 'btn btn-sm', type: 'button', 'aria-label': t('nextMonth'), onclick: nav(1) }, '›'))),
      el('div', { class: 'card-body' }, el('div', { class: 'mk-grid', role: 'grid' },
        ...t('weekdays').map(w => el('div', { class: 'mk-wd', role: 'columnheader' }, w)),
        ...weeks.flat().map(day => el('div', {
          class: `mk-day${day.inMonth ? '' : ' out'}${day.date === today ? ' today' : ''}${day.items.length ? '' : ' empty-day'}`, role: 'gridcell' },
        el('span', { class: 'mk-num' }, String(Number(day.date.slice(8)))), ...day.items.map(chip))))));
  }

  // ---------- campañas ----------
  async function campaignList() {
    const [list, d] = await Promise.all([M.campaigns(ctx.tenantId), M.dashboard(ctx.tenantId)]);
    campaigns = list.campaigns;
    return el('div', {}, campaignCard(), addonSection(ctx, d.publication_plan, d.role, draw));
  }
  function campaignCard() {
    return card(t('tab_campaigns'), table([
      { label: t('name'), key: 'name' },
      { label: t('status'), render: c => el('span', { class: 'badge' }, t(`c_${c.status}`)) },
      { label: t('objective'), key: 'objective' },
      { label: t('start'), render: c => (c.start_date ? day(c.start_date) : null) },
      { label: t('end'), render: c => (c.end_date ? day(c.end_date) : null) },
      { label: t('content'), num: true, render: c => String(c.content_count || 0) },
    ], campaigns, { onRowClick: c => campaignModal(ctx, c, reload), emptyText: t('noCampaigns') }),
    el('button', { class: 'btn btn-sm', type: 'button', onclick: () => campaignModal(ctx, null, reload) }, `+ ${t('newCampaign')}`));
  }

  // ---------- Biblioteca ----------
  const library = () => libraryView(ctx, draw);

  // ---------- Brand Kit ----------
  async function brand() {
    const data = await M.brand(ctx.tenantId);
    const holder = el('div');
    brandForm(holder, ctx, data, ctx.isCurrent);
    return holder;
  }

  // El resumen fija si el módulo está activado (botones «Planear contenido» y «Contenido en blanco»).
  gate = M.dashboard(ctx.tenantId).then(d => {
    if (!ctx.isCurrent()) return;
    enabled = d.settings.marketing_enabled; createBtn.disabled = !enabled;
    if (!enabled && st.tab !== 'overview') clear(notice).appendChild(el('div', { class: 'error-box mk-notice' }, t('disabled')));
    if (enabled && openPlanner) { openPlanner = false; plan(); }
  }).catch(() => {});
  await reload();
}
