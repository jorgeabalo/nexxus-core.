import { el, clear, kpi, card, empty, errorBox, loading, money, num, fmtTime, fmtDuration, icon, badge, toast } from '../ui.js';
import { api } from '../api.js';

const KIND = {
  check_in: { icon: 'check', label: 'Check-in' },
  appointment: { icon: 'schedule', label: 'Booking' },
  call: { icon: 'claudia', label: 'Call' },
  payment: { icon: 'cash', label: 'Payment' },
  new_member: { icon: 'user', label: 'New member' },
  lead: { icon: 'lead', label: 'New lead' },
  alert: { icon: 'alert', label: 'Alert' },
};

// Adónde lleva cada alerta / evento
function linkFor(table, id) {
  switch (table) {
    case 'members': return `#/members/${id}`;
    case 'calls': return `#/claudia/${id}`;
    case 'appointments': return '#/schedule';
    case 'payments': return '#/payments';
    case 'leads': return '#/claudia';
    default: return null;
  }
}

export async function render(root, ctx) {
  root.appendChild(el('div', { class: 'page-head' },
    el('div', {}, el('h1', {}, 'Dashboard'), el('p', {}, 'Today at a glance. Every number comes from your live data.')),
    el('button', { class: 'btn btn-sm', type: 'button', onclick: () => render(clear(root), ctx) }, 'Refresh')));

  const kpiRow = el('div', { class: 'kpis' }, loading());
  const middle = el('div', { class: 'grid-2' });
  const activityBody = el('div', {}, loading());
  const claudiaBody = el('div', {}, loading());
  const paymentsBody = el('div', {}, loading());
  const alertsBody = el('div', {}, loading());

  middle.append(
    card("Today's activity", activityBody),
    el('div', { class: 'stack' },
      card('Claudia today', claudiaBody, el('a', { class: 'btn btn-sm', href: '#/claudia' }, 'Open calls')),
      card('Payments', paymentsBody, el('a', { class: 'btn btn-sm', href: '#/payments' }, 'View all'))));
  root.append(kpiRow, middle, card('Alerts · action required', alertsBody));

  const [dash, activity, alerts] = await Promise.allSettled([
    api.dashboard(ctx.tenantId), api.activity(ctx.tenantId, 60), api.alerts(ctx.tenantId),
  ]);
  if (!ctx.isCurrent()) return;

  // ----- KPIs -----
  clear(kpiRow);
  if (dash.status === 'rejected') {
    kpiRow.appendChild(errorBox(dash.reason));
  } else {
    const d = dash.value, k = d.kpis;
    kpiRow.append(
      kpi('Members today', num(k.members_today), 'checked in', true),
      kpi('Appointments today', num(k.appointments_today), 'scheduled / confirmed'),
      kpi('Calls today', num(k.calls_today), 'answered by Claudia'),
      kpi('New leads', num(k.new_leads), 'today'),
      kpi('Payments today', money(k.payments_today.amount), `${num(k.payments_today.count)} received`),
      kpi('Pending payments', money(k.pending_payments.amount), `${num(k.pending_payments.count)} open`));

    // ----- Claudia -----
    const c = d.claudia;
    clear(claudiaBody).appendChild(el('div', { class: 'stats cols-3' },
      stat('Calls answered', num(c.calls_answered)),
      stat('Average duration', c.avg_duration_seconds === null ? '—' : fmtDuration(c.avg_duration_seconds)),
      stat('New leads', num(c.new_leads)),
      stat('Appointments generated', num(c.appointments_generated)),
      stat('Transfers', num(c.transfers)),
      stat('Follow-ups required', num(c.follow_ups_required), 'open, all time')));

    // ----- Payments -----
    const p = d.payments;
    clear(paymentsBody).appendChild(el('div', { class: 'stats cols-3' },
      stat('Paid', money(p.paid_month.amount), `${num(p.paid_month.count)} this month`),
      stat('Pending', money(p.pending.amount), `${num(p.pending.count)} not yet due`),
      stat('Overdue', money(p.overdue.amount), `${num(p.overdue.count)} past due`)));
  }
  if (dash.status === 'rejected') {
    clear(claudiaBody).appendChild(errorBox(dash.reason));
    clear(paymentsBody).appendChild(errorBox(dash.reason));
  }

  // ----- Activity timeline -----
  clear(activityBody);
  if (activity.status === 'rejected') activityBody.appendChild(errorBox(activity.reason));
  else if (!activity.value.length) activityBody.appendChild(empty('No activity recorded today yet'));
  else {
    activityBody.appendChild(el('ul', { class: 'timeline' }, activity.value.map(a => {
      const k = KIND[a.kind] || KIND.alert;
      return el('li', {},
        el('span', { class: 'time' }, fmtTime(a.occurred_at)),
        el('span', { class: 'ico', title: k.label }, icon(k.icon, 16)),
        el('div', {}, el('div', { class: 't-title' }, a.title || k.label),
          el('div', { class: 't-detail' }, [k.label, a.detail].filter(Boolean).join(' · '))));
    })));
  }

  // ----- Alerts -----
  renderAlerts(alertsBody, alerts, ctx, () => render(clear(root), ctx));
}

function stat(label, value, meta = '') {
  return el('div', { class: 'stat' }, el('div', { class: 'label' }, label), el('div', { class: 'value' }, value), el('div', { class: 'meta' }, meta));
}

function renderAlerts(body, result, ctx, refresh) {
  clear(body);
  if (result.status === 'rejected') return body.appendChild(errorBox(result.reason));
  const list = result.value;
  if (!list.length) return body.appendChild(empty('Nothing needs your attention right now'));
  body.appendChild(el('ul', { class: 'alert-list' }, list.map(a => {
    const href = linkFor(a.ref_table, a.ref_id);
    return el('li', { class: `alert-item ${a.severity}` },
      el('span', { class: 'bar', 'aria-hidden': 'true' }),
      el('div', { class: 'a-body' },
        el('div', { class: 'a-title' }, a.title),
        a.detail ? el('div', { class: 'a-detail' }, a.detail) : null,
        el('div', { class: 'mt6' }, badge(a.kind))),
      el('div', { class: 'a-actions' },
        href ? el('a', { class: 'btn btn-sm', href }, 'Open') : null,
        a.stored ? el('button', {
          class: 'btn btn-sm', type: 'button', onclick: async (e) => {
            e.target.disabled = true;
            try { await api.resolveAlert(a.alert_id); toast('Alert resolved'); refresh(); }
            catch (err) { e.target.disabled = false; toast(err.message, 'error'); }
          },
        }, 'Resolve') : null));
  })));
}
