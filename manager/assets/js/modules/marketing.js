// AITA Marketing: resumen, contenido, calendario, campañas, Brand Kit (Fase 1) y
// Biblioteca, Estudio de Reels y Trabajos de generación (Fase 2).
// Los datos vienen del backend (/api/manager/marketing/*), que valida rol
// (owner/manager), tenant y reglas de estado. Nada se publica en redes todavía.
// El estado de la vista es por empresa: al cambiar de tenant se empieza de cero.
import { el, clear, card, kpi, table, tabs, errorBox, loading, fmtDateTime, todayISO, select, tz } from '../ui.js';
import { api } from '../api.js';
import { t, errText, getLang } from './marketing-i18n.js';
import { TABS, createScope, monthStart, shiftMonth, monthEnd, monthGrid, usageBar } from './marketing-state.js';
import { contentModal, contentDetail, campaignModal, brandForm, channelLabel, statusBadge } from './marketing-forms.js';
import { libraryView } from './marketing-library.js';
import { studioView, jobsView } from './marketing-studio.js';
import { s as sText } from './marketing-studio-i18n.js';

const M = api.marketing;
const scope = createScope();

export async function render(root, ctx) {
  const st = scope.for(ctx.tenantId);
  if (ctx.params[0] && TABS.includes(ctx.params[0])) st.tab = ctx.params[0];
  let campaigns = [];
  let enabled = false;

  const createBtn = el('button', { class: 'btn btn-accent', type: 'button', disabled: true,
    onclick: () => contentModal(ctx, { campaigns }, reload) }, `+ ${t('create')}`);
  root.appendChild(el('div', { class: 'page-head' },
    el('div', {}, el('h1', {}, t('title')), el('p', {}, t('subtitle'))),
    el('div', { class: 'btn-row' }, createBtn)));
  const notice = el('div');
  const tabHolder = el('div', { class: 'toolbar' });
  const body = el('div', { class: 'mk-body' });
  root.append(notice, tabHolder, body);

  const tabLabel = (v) => (['library', 'studio', 'jobs'].includes(v) ? sText(`tab_${v}`) : t(`tab_${v}`));
  const drawTabs = () => clear(tabHolder).appendChild(tabs(TABS.map(v => ({ value: v, label: tabLabel(v) })), st.tab,
    (v) => { st.tab = v; history.replaceState(null, '', `#/marketing/${v}`); drawTabs(); draw(); }));
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
      const view = { overview, content, calendar, campaigns: campaignList, brand, library, studio, jobs }[st.tab];
      const node = await view();
      if (ctx.isCurrent()) clear(body).appendChild(node);
    } catch (e) {
      if (ctx.isCurrent()) clear(body).appendChild(errorBox({ message: errText(e) }));
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
    const k = d.counts;
    const bar = (label, used, limit) => {
      const u = usageBar(used, limit);
      return el('div', { class: 'mk-usage' },
        el('div', { class: 'mk-usage-head' }, el('span', {}, label),
          el('span', {}, u.limit === null ? `${used} · ${t('unlimited')}` : `${used} ${t('of')} ${u.limit}`)),
        u.pct === null ? null : meter(u));
    };
    return el('div', {},
      el('div', { class: 'kpis mk-kpis' },
        ...['drafts', 'review', 'approved', 'scheduled', 'published'].map(x => kpi(t(`k_${x}`), String(k[x] || 0), '', x === 'review' && k.review > 0))),
      el('div', { class: 'grid-2' },
        card(t('usage'), el('div', { class: 'card-body' },
          el('p', { class: 'hint' }, `${t('plan')}: ${d.settings.plan_code}`),
          bar(t('posts'), d.usage.posts, d.limits.monthly_post_limit),
          bar(t('images'), d.usage.images, d.limits.monthly_image_limit),
          bar(t('reels'), d.usage.reels, d.limits.monthly_reel_limit))),
        card(t('upcoming'), table([
          { label: t('date'), render: r => fmtDateTime(r.scheduled_at || r.planned_at) },
          { label: t('titleF'), key: 'title' },
          { label: t('format'), render: r => t(`f_${r.format}`) },
          { label: t('status'), render: r => statusBadge(r.status) },
        ], d.upcoming, { onRowClick: r => open(r.id), emptyText: t('noUpcoming') }))));
  }

  // ---------- contenido ----------
  async function content() {
    const filter = select([{ value: '', label: t('all') }, ...['idea', 'draft', 'review', 'approved', 'rejected', 'scheduled',
      'published', 'failed', 'archived'].map(s => ({ value: s, label: t(`s_${s}`), selected: s === st.status }))],
    { 'aria-label': t('status'), onchange: (e) => { st.status = e.target.value; draw(); } });
    const rows = (await M.contentList(ctx.tenantId, { status: st.status })).content;
    const camp = Object.fromEntries(campaigns.map(c => [c.id, c.name]));
    return el('section', { class: 'card' },
      el('div', { class: 'card-head' }, el('h2', {}, t('tab_content')), filter),
      table([
        { label: t('titleF'), key: 'title' },
        { label: t('status'), render: r => statusBadge(r.status) },
        { label: t('format'), render: r => t(`f_${r.format}`) },
        { label: t('channels'), render: r => (r.channels || []).map(channelLabel).join(', ') },
        { label: t('campaign'), render: r => camp[r.campaign_id] || null },
        { label: t('date'), render: r => (r.scheduled_at || r.planned_at) ? fmtDateTime(r.scheduled_at || r.planned_at) : null },
      ], rows, { onRowClick: r => open(r.id), emptyText: t('noContent') }));
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
    const chip = (it) => el('button', { class: `mk-chip mk-s-${it.status}`, type: 'button', onclick: () => open(it.id),
      title: [it.title, t(`s_${it.status}`), t(`f_${it.format}`), (it.channels || []).map(channelLabel).join(', '),
        it.campaign_name, it.assignee_name].filter(Boolean).join(' · ') },
    el('span', { class: 'mk-chip-title' }, it.title),
    el('span', { class: 'mk-chip-meta' }, [t(`s_${it.status}`), t(`f_${it.format}`), (it.channels || []).map(channelLabel).join(', ')]
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
    campaigns = (await M.campaigns(ctx.tenantId)).campaigns;
    return card(t('tab_campaigns'), table([
      { label: t('name'), key: 'name' },
      { label: t('status'), render: c => el('span', { class: 'badge' }, t(`c_${c.status}`)) },
      { label: t('objective'), key: 'objective' },
      { label: t('start'), key: 'start_date' }, { label: t('end'), key: 'end_date' },
      { label: t('content'), num: true, render: c => String(c.content_count || 0) },
    ], campaigns, { onRowClick: c => campaignModal(ctx, c, reload), emptyText: t('noCampaigns') }),
    el('button', { class: 'btn btn-sm', type: 'button', onclick: () => campaignModal(ctx, null, reload) }, `+ ${t('newCampaign')}`));
  }

  // ---------- Fase 2: Biblioteca, Estudio de Reels y Trabajos ----------
  const library = () => libraryView(ctx, draw);
  const goJobs = (id) => { st.tab = 'jobs'; st.openJob = id; history.replaceState(null, '', '#/marketing/jobs'); drawTabs(); draw(); };
  const studio = () => studioView(ctx, goJobs);
  const jobs = () => { const id = st.openJob; st.openJob = null; return jobsView(ctx, draw, id); };

  // ---------- Brand Kit ----------
  async function brand() {
    const data = await M.brand(ctx.tenantId);
    const holder = el('div');
    brandForm(holder, ctx, data, ctx.isCurrent);
    return holder;
  }

  // El resumen también fija si el módulo está activado (botón "Crear contenido").
  if (st.tab !== 'overview') {
    M.dashboard(ctx.tenantId).then(d => {
      if (!ctx.isCurrent()) return;
      enabled = d.settings.marketing_enabled; createBtn.disabled = !enabled;
      if (!enabled) clear(notice).appendChild(el('div', { class: 'error-box mk-notice' }, t('disabled')));
    }).catch(() => {});
  }
  await reload();
}
