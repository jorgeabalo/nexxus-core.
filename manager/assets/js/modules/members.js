import { el, clear, card, table, tabs, badge, empty, errorBox, loading, fmtDate, fmtDateTime, fmtClock, money, num, icon, debounce, openModal, field, input, select, toast } from '../ui.js';
import { api } from '../api.js';

const FILTERS = [
  { value: 'all', label: 'All' },
  { value: 'active', label: 'Active' },
  { value: 'inactive', label: 'Inactive' },
  { value: 'past_due', label: 'Past due' },
  { value: 'new', label: 'New' },
];
const state = { search: '', filter: 'all' };

export async function render(root, ctx) {
  if (ctx.params[0]) return renderDetail(root, ctx, ctx.params[0]);

  root.appendChild(el('div', { class: 'page-head' },
    el('div', {}, el('h1', {}, 'Members'), el('p', {}, 'Search by name, member ID or phone.')),
    el('button', { class: 'btn btn-primary', type: 'button', onclick: () => newMemberModal(ctx) }, '+ New member')));

  const search = el('input', {
    class: 'input search', type: 'search', placeholder: 'Search name, member ID or phone', value: state.search,
    'aria-label': 'Search members',
    oninput: debounce((e) => { state.search = e.target.value; load(); }, 300),
  });
  const tabsHolder = el('div');
  const body = el('div', {}, loading());
  const count = el('span', { class: 'muted small' });
  root.appendChild(el('section', { class: 'card' }, el('div', { class: 'toolbar' }, search, tabsHolder, count), body));

  const drawTabs = () => clear(tabsHolder).appendChild(tabs(FILTERS, state.filter, (v) => { state.filter = v; drawTabs(); load(); }));
  drawTabs();

  let seq = 0;
  async function load() {
    const mine = ++seq;
    clear(body).appendChild(loading());
    try {
      const rows = await api.members(ctx.tenantId, state);
      if (mine !== seq || !ctx.isCurrent()) return;
      count.textContent = `${num(rows.length)} ${rows.length === 1 ? 'member' : 'members'}`;
      clear(body).appendChild(table([
        { label: 'Name', render: r => el('span', { class: 'strong' }, r.full_name || '—') },
        { label: 'Member ID', key: 'member_code' },
        { label: 'Status', render: r => badge(r.membership_status) },
        { label: 'Last visit', render: r => r.last_visit ? fmtDateTime(r.last_visit) : null },
        { label: 'Visits this month', num: true, render: r => num(r.visits_this_month) },
        { label: 'Next appointment', render: r => r.next_appointment_date ? `${fmtDate(r.next_appointment_date, { month: 'short', day: 'numeric' })} · ${fmtClock(r.next_appointment_time)}` : null },
        { label: 'Payment', render: r => badge(r.payment_status, r.payment_status === 'none' ? 'no payments' : null) },
      ], rows, {
        onRowClick: (r) => { location.hash = `#/members/${r.id}`; },
        emptyText: state.search || state.filter !== 'all' ? 'No members match this search' : 'No members yet',
      }));
    } catch (e) {
      if (mine === seq) clear(body).appendChild(errorBox(e));
    }
  }
  await load();
}

async function renderDetail(root, ctx, id) {
  root.appendChild(el('a', { class: 'back-link', href: '#/members' }, icon('back', 18), 'Members'));
  const holder = el('div', { class: 'stack' }, loading());
  root.appendChild(holder);
  let m, act;
  try {
    [m, act] = await Promise.all([api.member(ctx.tenantId, id), api.memberActivity(ctx.tenantId, id)]);
  } catch (e) { return clear(holder).appendChild(errorBox(e)); }
  if (!ctx.isCurrent()) return;
  clear(holder);
  if (!m) return holder.appendChild(card('Member', empty('Member not found')));

  holder.appendChild(el('div', { class: 'page-head' },
    el('div', {}, el('h1', {}, m.full_name || 'Member'),
      el('p', {}, [m.member_code ? `ID ${m.member_code}` : null, m.membership_type].filter(Boolean).join(' · ') || ' ')),
    el('div', { class: 'btn-row' }, badge(m.membership_status), badge(m.payment_status, m.payment_status === 'none' ? 'no payments' : null))));

  const dt = (k, v) => el('div', { class: 'dt' }, el('div', { class: 'k' }, k), el('div', { class: 'v' }, v ?? '—'));
  holder.appendChild(card('Profile', el('div', { class: 'detail-grid' },
    dt('Phone', m.phone ? el('a', { href: `tel:${m.phone}` }, m.phone) : null),
    dt('Email', m.email ? el('a', { href: `mailto:${m.email}` }, m.email) : null),
    dt('Member since', m.start_date ? fmtDate(m.start_date) : null),
    dt('Last visit', m.last_visit ? fmtDateTime(m.last_visit) : null),
    dt('Visits this month', num(m.visits_this_month)),
    dt('Next payment date', m.next_payment_date ? fmtDate(m.next_payment_date) : null),
    dt('Next appointment', m.next_appointment_date ? `${fmtDate(m.next_appointment_date)} · ${fmtClock(m.next_appointment_time)}` : null),
    dt('Service', m.next_appointment_service),
    dt('Notes', m.notes))));

  holder.appendChild(portalCard(m));
  holder.appendChild(progressCard(ctx, m));

  holder.appendChild(el('div', { class: 'grid-2' },
    card('Appointments', table([
      { label: 'Date', render: a => `${fmtDate(a.appointment_date, { month: 'short', day: 'numeric' })} · ${fmtClock(a.start_time)}` },
      { label: 'Service', key: 'service' },
      { label: 'Staff', key: 'staff_name' },
      { label: 'Status', render: a => badge(a.status) },
    ], act.appointments, { emptyText: 'No appointments' })),
    card('Payments', table([
      { label: 'Amount', num: true, render: p => money(p.amount) },
      { label: 'Due', render: p => p.due_date ? fmtDate(p.due_date) : null },
      { label: 'Status', render: p => badge(p.effective_status) },
      { label: 'Paid on', render: p => p.payment_date ? fmtDateTime(p.payment_date) : null },
    ], act.payments, { emptyText: 'No payments recorded' }))));

  holder.appendChild(card('Recent check-ins', table([
    { label: 'Date', render: c => fmtDateTime(c.check_in_time) },
    { label: 'Method', render: c => badge(c.method) },
  ], act.checkins, { emptyText: 'No check-ins recorded' })));
}

function newMemberModal(ctx) {
  const first = input({ required: true, autocomplete: 'off' });
  const last = input({ autocomplete: 'off' });
  const phone = input({ type: 'tel', autocomplete: 'off' });
  const email = input({ type: 'email', autocomplete: 'off' });
  const code = input({ autocomplete: 'off' });
  const type = input({ autocomplete: 'off', placeholder: 'e.g. Monthly' });
  const status = select([{ value: 'active', label: 'Active', selected: true }, { value: 'inactive', label: 'Inactive' }, { value: 'paused', label: 'Paused' }]);
  const joined = select([{ value: 'new', label: 'New member (show onboarding)', selected: true }, { value: 'existing', label: 'Existing member (no onboarding now)' }]);
  const gender = select([{ value: '', label: 'Not recorded', selected: true }, { value: 'F', label: 'Female' }, { value: 'M', label: 'Male' }]);
  const err = el('p', { class: 'form-error', role: 'alert' });
  const form = el('form', { class: 'form-grid', onsubmit: (e) => e.preventDefault() },
    field('First name *', first), field('Last name', last),
    field('Phone', phone), field('Email', email),
    field('Member ID', code, { hint: 'Leave empty if you do not use member codes' }), field('Membership type', type),
    field('Status', status), field('Joined as', joined), field('Sex (for body figure)', gender, { hint: 'Optional. Never inferred from the name.' }), err);
  openModal({
    title: 'New member', body: form, actions: [(close) => el('button', {
      class: 'btn btn-primary', type: 'button', onclick: async (e) => {
        err.textContent = '';
        if (!first.value.trim()) { err.textContent = 'First name is required.'; return; }
        e.target.disabled = true;
        try {
          const row = await api.createMember(ctx.tenantId, {
            first_name: first.value.trim(), last_name: last.value.trim() || null,
            phone: phone.value.trim() || null, email: email.value.trim().toLowerCase() || null,
            member_id: code.value.trim() || null, membership_type: type.value.trim() || null,
            membership_status: status.value, joined_as: joined.value, gender: gender.value || null,
          });
          close(); toast('Member created'); location.hash = `#/members/${row.id}`;
        } catch (ex) {
          e.target.disabled = false;
          err.textContent = /duplicate|unique/i.test(ex.message) ? 'That member ID is already in use.' : ex.message;
        }
      },
    }, 'Save member')],
  });
}

// ---------- Member portal: acceso personal (QR / enlace) ----------
const PORTAL_ERRORS = {
  portal_not_configured: 'The member portal is not configured on the server yet (PORTAL_TOKEN_SECRET).',
  forbidden: 'Only owners and managers can manage portal access.',
  unauthorized: 'Your session expired. Sign in again.',
  account_conflict: 'That email already belongs to another member account.',
};
const portalMsg = (e) => PORTAL_ERRORS[e.code] || e.message || 'Something went wrong';
const SEND_DETAIL = { no_phone: 'no phone on file', invalid_phone: 'phone is not a valid mobile number', no_email: 'no email on file',
  twilio_not_configured: 'SMS not configured', twilio_error: 'SMS provider rejected it', rate_limited: 'email limit reached, try later' };
const SMS_STATE = { queued: ['Accepted / queued', 'pending'], sent: ['Sent to carrier', 'info'], delivered: ['Delivered', 'active'], failed: ['Failed', 'overdue'] };
// Códigos de Twilio más habituales (https://www.twilio.com/docs/api/errors)
const TWILIO_ERR = {
  30034: 'Blocked: the sender number is not registered for US A2P 10DLC messaging.',
  30032: 'Blocked: toll-free number not verified.', 30007: 'Filtered by the carrier as spam.',
  30003: 'Unreachable handset (off or out of coverage).', 30005: 'Unknown or inactive number.', 30006: 'Landline or unreachable carrier.',
  30008: 'Unknown delivery error.', 21608: 'Trial account: recipient is not a verified number.', 21610: 'Recipient replied STOP (unsubscribed).',
  21211: 'Invalid phone number.', 21614: 'Not a mobile number.', 21408: 'Region not enabled for SMS.', 21606: 'Sender number cannot send SMS.',
};
const sendState = (x) => !x ? null : x.status ? `${(SMS_STATE[x.status] || [x.status])[0]}${x.error_code ? ` · error ${x.error_code}` : ''}${x.detail && !x.ok ? ` (${SEND_DETAIL[x.detail] || x.detail})` : ''}` : (x.ok ? 'sent' : (SEND_DETAIL[x.detail] || x.detail));

function qrImage(link, cell = 6) {
  const qr = window.qrcode(0, 'M');
  qr.addData(link); qr.make();
  return el('img', { class: 'qr-img', src: qr.createDataURL(cell, 4), alt: 'Personal QR code' });
}

function portalCard(m) {
  const status = el('div', { class: 'detail-grid' }, loading());
  const out = el('p', { class: 'muted small m0', role: 'status' });
  const btn = (label, onclick, primary = false) => el('button', { class: `btn ${primary ? 'btn-primary' : ''}`, type: 'button', onclick }, label);
  const dt = (k, v) => el('div', { class: 'dt' }, el('div', { class: 'k' }, k), el('div', { class: 'v' }, v ?? '—'));

  async function refresh() {
    try {
      const r = await api.portalLink(m.id);
      clear(status).append(
        dt('Status', r.activated_at ? badge('active', 'activated') : r.invited_at ? badge('pending', 'invited') : badge('inactive', 'not invited')),
        dt('Invited', r.invited_at ? fmtDateTime(r.invited_at) : null),
        dt('First access', r.activated_at ? fmtDateTime(r.activated_at) : null),
        dt('Last access', r.last_used_at ? fmtDateTime(r.last_used_at) : null),
        dt('Last SMS', smsView(r.last_sms)));
      return r;
    } catch (e) { clear(status).appendChild(el('p', { class: 'form-error m0' }, portalMsg(e))); return null; }
  }

  async function send(e) {
    const sms = el('input', { type: 'checkbox', checked: !!m.phone, disabled: !m.phone });
    const mail = el('input', { type: 'checkbox', checked: !!m.email, disabled: !m.email });
    const err = el('p', { class: 'form-error', role: 'alert' });
    openModal({
      title: 'Send portal access',
      body: el('div', { class: 'stack' },
        el('p', { class: 'm0' }, 'The member receives a personal link. Opening it (or scanning the QR) signs them in to their portal.'),
        el('label', { class: 'check' }, sms, ` SMS to ${m.phone || '(no phone)'}`),
        el('label', { class: 'check' }, mail, ` Email sign-in link to ${m.email || '(no email)'}`),
        err),
      actions: [(close) => btn('Send', async (ev) => {
        if (!sms.checked && !mail.checked) { err.textContent = 'Choose SMS or email.'; return; }
        ev.target.disabled = true;
        try {
          const r = await api.portalAccess(m.id, { sms: sms.checked, email: mail.checked });
          const parts = [];
          if (r.sms) parts.push(`SMS: ${sendState(r.sms)}`);
          if (r.email) parts.push(`Email: ${r.email.ok ? 'sent' : (SEND_DETAIL[r.email.detail] || r.email.detail)}`);
          close(); toast(parts.join(' · '), (r.sms?.ok || r.email?.ok) ? '' : 'error'); refresh();
        } catch (ex) { ev.target.disabled = false; err.textContent = portalMsg(ex); }
      }, true)],
    });
  }

  async function showQr() {
    let r;
    try { r = await api.portalLink(m.id); } catch (e) { return toast(portalMsg(e), 'error'); }
    const name = m.full_name || 'Member';
    openModal({
      title: 'Personal QR',
      body: el('div', { class: 'stack center qr-print' },
        el('p', { class: 'strong m0' }, name),
        qrImage(r.link, 8),
        el('p', { class: 'muted small m0' }, 'Personal key — do not share.'),
        el('input', { class: 'input', readonly: true, value: r.link, 'aria-label': 'Personal link', onfocus: (e) => e.target.select() })),
      actions: [
        () => btn('Copy link', async () => { try { await navigator.clipboard.writeText(r.link); toast('Link copied'); } catch { toast('Copy failed', 'error'); } }),
        () => btn('Print', () => { document.body.classList.add('printing-qr'); window.print(); document.body.classList.remove('printing-qr'); }, true),
      ],
    });
  }

  function regenerate() {
    openModal({
      title: 'Regenerate QR',
      body: el('p', { class: 'm0' }, 'The current QR and link stop working immediately. The member will need the new one.'),
      actions: [(close) => btn('Regenerate', async (ev) => {
        ev.target.disabled = true;
        try { await api.portalAccess(m.id, { regenerate: true }); close(); toast('New QR generated'); refresh(); showQr(); }
        catch (ex) { ev.target.disabled = false; toast(portalMsg(ex), 'error'); }
      }, true)],
    });
  }

  async function checkSms(ev) {
    ev.target.disabled = true;
    try { await api.smsRefresh(m.id); await refresh(); toast('Delivery status updated from Twilio'); }
    catch (e) { toast(portalMsg(e), 'error'); }
    ev.target.disabled = false;
  }

  refresh();
  return card('Member portal', el('div', { class: 'stack' }, status,
    el('div', { class: 'btn-row' }, btn('Send access', send, true), btn('Show / print QR', showQr), btn('Regenerate QR', regenerate),
      btn('Check SMS delivery', checkSms), btn('SMS diagnostics', smsDiagnostics)), out));
}

function smsView(x) {
  if (!x) return null;
  const [label, tone] = SMS_STATE[x.status] || [x.status, ''];
  return el('div', { class: 'stack-tight' },
    el('div', {}, badge(tone || 'none', label), x.error_code ? el('span', { class: 'small strong' }, ` error ${x.error_code}`) : null),
    el('div', { class: 'small muted' }, `${x.purpose === 'evaluation_invite' ? 'Evaluation invite' : 'Portal access'} · to ${x.to_masked} · ${fmtDateTime(x.updated_at || x.created_at)}${x.sid_tail ? ` · SID …${x.sid_tail}` : ''}`),
    x.error_code ? el('div', { class: 'small' }, TWILIO_ERR[x.error_code] || x.error_message || '') : null,
    x.status === 'queued' ? el('div', { class: 'small muted' }, 'Twilio accepted it; this does NOT mean it was delivered.') : null);
}

async function smsDiagnostics() {
  const body = el('div', { class: 'stack' }, loading());
  openModal({ title: 'SMS diagnostics (live from Twilio)', body, actions: [] });
  try {
    const d = await api.smsDiagnostics();
    const line = (k, v) => el('div', { class: 'dt' }, el('div', { class: 'k' }, k), el('div', { class: 'v' }, v ?? '—'));
    clear(body).append(
      el('div', { class: 'detail-grid' },
        line('Sender', d.sender), line('Credentials', d.credentials ? 'configured' : 'missing'),
        line('Account', d.account ? `${d.account.type} · ${d.account.status}` : null),
        line('Sender can send SMS', d.number ? (d.number.sms ? 'yes' : 'NO') : null),
        line('Public URL', d.public_base_url)),
      d.errors.length ? el('p', { class: 'form-error m0' }, d.errors.join(' · ')) : null,
      d.account && /trial/i.test(d.account.type) ? el('p', { class: 'small m0' }, 'Trial account: SMS only reach verified numbers.') : null,
      table([
        { label: 'Date', render: r => r.date ? fmtDateTime(r.date) : null },
        { label: 'To', key: 'to' },
        { label: 'Status', render: r => badge((SMS_STATE[r.state] || [])[1] || 'none', r.status) },
        { label: 'Error', render: r => r.error_code ? `${r.error_code} — ${TWILIO_ERR[r.error_code] || ''}` : null },
      ], d.recent, { emptyText: 'No recent messages from this number' }));
  } catch (e) { clear(body).appendChild(el('p', { class: 'form-error m0' }, portalMsg(e))); }
}

// ---------- Progress & evaluations ----------
const METRIC_LABEL = {
  walk: 'Walking (min)', bike: 'Bike (min)', other: 'Other activity (min)', chair_rise: 'Getting up from a chair (1-5)',
  stairs: 'Climbing stairs (1-5)', walking: 'Walking ease (1-5)', chair_stand_30s: '30-s chair stand (reps)',
  energy: 'Energy (1-5)', sleep: 'Sleep quality (1-5)', overall: 'How they feel (1-5)',
};
const L = (o) => (o && typeof o === 'object') ? (o.en || o.es || '') : (o || '');

function progressCard(ctx, m) {
  const holder = el('div', { class: 'stack' }, loading());
  async function load() {
    let d;
    try { d = await api.memberProgress(ctx.tenantId, m.id); } catch (e) { return clear(holder).appendChild(errorBox(e)); }
    Object.assign(m, d.member);
    const sexLabel = { M: 'male', F: 'female' }[String(m.gender || '').toUpperCase().slice(0, 1)] || 'not recorded';
    const submitted = d.evaluations.filter(e => e.status === 'submitted');
    const draft = d.evaluations.find(e => e.status === 'draft');
    const qById = Object.fromEntries(d.questionnaires.map(q => [q.id, q]));
    const active = d.questionnaires.find(q => q.active);
    const src = (r) => r.source === 'staff' ? `taken by staff (${r.recorded_by_name || '—'})` : `reported by member${r.recorded_by_name ? ` (${r.recorded_by_name})` : ''}`;
    clear(holder).append(
      el('div', { class: 'detail-grid' },
        el('div', { class: 'dt' }, el('div', { class: 'k' }, 'Joined as'), el('div', { class: 'v' }, m.joined_as || 'existing (default)')),
        el('div', { class: 'dt' }, el('div', { class: 'k' }, 'Sex (figure)'), el('div', { class: 'v' }, sexLabel)),
        el('div', { class: 'dt' }, el('div', { class: 'k' }, 'Next evaluation'), el('div', { class: 'v' }, m.next_evaluation_due ? fmtDate(m.next_evaluation_due) : '—')),
        el('div', { class: 'dt' }, el('div', { class: 'k' }, 'Questionnaire'), el('div', { class: 'v' }, active ? `${L(active.title)} v${active.version}` : 'Original not loaded yet')),
        el('div', { class: 'dt' }, el('div', { class: 'k' }, 'Draft in progress'), el('div', { class: 'v' }, draft ? `step ${draft.current_step + 1} · ${fmtDateTime(draft.updated_at)}` : '—'))),
      el('div', { class: 'btn-row' },
        el('button', { class: 'btn btn-primary', type: 'button', onclick: () => inviteModal(m) }, 'Send evaluation invite'),
        el('button', { class: 'btn', type: 'button', onclick: () => recordModal(ctx, m, d, load) }, 'Record measurement')),
      el('h3', { class: 'm0' }, 'Evaluations'),
      table([
        { label: 'Type', render: e => e.kind === 'initial' ? 'Initial' : 'Quarterly' },
        { label: 'Submitted', render: e => fmtDateTime(e.submitted_at) },
        { label: 'Questionnaire', render: e => e.questionnaire_version ? `v${e.questionnaire_version}` : 'not included' },
        { label: 'Next due', render: e => e.next_due_date ? fmtDate(e.next_due_date) : null },
        { label: '', render: e => el('span', { class: 'btn-row' },
            el('button', { class: 'btn btn-sm', type: 'button', onclick: () => viewEvaluation(e, qById[e.questionnaire_id]) }, 'View'),
            el('button', { class: 'btn btn-sm', type: 'button', onclick: (ev) => downloadPdf(e, ev.target) }, 'PDF')) },
      ], submitted, { emptyText: 'No evaluations submitted yet' }),
      el('h3', { class: 'm0' }, 'Body measurements (history, lb / in)'),
      table([
        { label: 'Date', render: r => fmtDate(r.measurement_date) },
        { label: 'Weight', num: true, render: r => r.weight_lb ?? '—' }, { label: 'Waist', num: true, render: r => r.waist_in ?? '—' },
        { label: 'Arm', num: true, render: r => r.left_arm_in ?? r.right_arm_in ?? '—' }, { label: 'Thigh', num: true, render: r => r.left_leg_in ?? r.right_leg_in ?? '—' },
        { label: 'Height', num: true, render: r => r.height_in ?? '—' },
        { label: 'Source', render: r => el('span', {}, src(r), r.is_baseline ? el('span', { class: 'badge gold' }, ' baseline') : null) },
      ], d.measurements, { emptyText: 'No measurements recorded' }),
      el('h3', { class: 'm0' }, 'Progress indicators'),
      table([
        { label: 'Date', render: r => fmtDate(r.entry_date) },
        { label: 'Indicator', render: r => r.category === 'strength' ? `Strength · ${r.exercise}` : `${r.category} · ${METRIC_LABEL[r.metric] || r.metric}${r.exercise && r.metric === 'other' ? ` (${r.exercise})` : ''}` },
        { label: 'Value', render: r => r.category === 'strength' ? `${r.value} lb × ${r.reps}` : String(r.value) },
        { label: 'Conditions', key: 'conditions' },
        { label: 'Source', render: r => el('span', {}, src(r), r.is_baseline ? el('span', { class: 'badge gold' }, ' baseline') : null) },
      ], d.progress, { emptyText: 'No indicators recorded' }));
  }
  load();
  return card('Progress & evaluations', holder);
}

async function downloadPdf(e, btnEl) {
  btnEl.disabled = true;
  try {
    const blob = await api.evaluationPdf(e.id);
    const url = URL.createObjectURL(blob);
    const a = el('a', { href: url, download: `evaluation-${e.kind}-${String(e.submitted_at).slice(0, 10)}.pdf` });
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 5000);
  } catch (err) { toast(portalMsg(err), 'error'); }
  btnEl.disabled = false;
}

function viewEvaluation(e, q) {
  const a = e.answers || {};
  const b = a.body || {};
  const items = [];
  for (const [k, lab] of [['weight_lb', 'Weight (lb)'], ['height_in', 'Height (in)'], ['waist_in', 'Waist (in)'], ['left_arm_in', 'Arm (in)'], ['left_leg_in', 'Thigh (in)']])
    if (b[k] != null) items.push(el('li', {}, `${lab}: ${b[k]}`));
  for (const p of a.progress || []) items.push(el('li', {}, p.category === 'strength' ? `Strength · ${p.exercise}: ${p.value} lb × ${p.reps}${p.conditions ? ` (${p.conditions})` : ''}` : `${p.category} · ${METRIC_LABEL[p.metric] || p.metric}: ${p.value}`));
  const sections = q ? (q.definition.sections || []).map(sec => el('div', {}, el('h3', {}, L(sec.title)),
    el('ul', {}, (sec.questions || []).map(qu => {
      const v = (a.q || {})[qu.id];
      const opt = (x) => L((qu.options || []).find(o => o.value === x)?.label) || x;
      const txt = v === undefined || v === null || v === '' ? 'No answer' : Array.isArray(v) ? v.map(opt).join(', ') : typeof v === 'boolean' ? (v ? 'Yes' : 'No') : opt(v);
      return el('li', {}, `${L(qu.label)}: ${txt}`);
    })))) : [el('p', { class: 'muted' }, 'This evaluation does not include the questionnaire (not loaded yet).')];
  openModal({ title: `${e.kind === 'initial' ? 'Initial' : 'Quarterly'} evaluation · ${fmtDate(String(e.submitted_at).slice(0, 10))}`,
    body: el('div', { class: 'stack' }, el('ul', {}, items.length ? items : el('li', {}, 'No measurements or indicators')), ...sections), actions: [] });
}

function inviteModal(m) {
  const sms = el('input', { type: 'checkbox', checked: !!m.phone, disabled: !m.phone });
  const mail = el('input', { type: 'checkbox', checked: !!m.email, disabled: !m.email });
  const err = el('p', { class: 'form-error', role: 'alert' });
  openModal({
    title: 'Send evaluation invite',
    body: el('div', { class: 'stack' },
      el('p', { class: 'm0' }, 'The member receives a personal, secure link that opens their evaluation form after signing in. Answers are never sent in the message.'),
      el('label', { class: 'check' }, sms, ` SMS to ${m.phone || '(no phone)'}`),
      el('label', { class: 'check' }, mail, ` Email sign-in link to ${m.email || '(no email)'}`), err),
    actions: [(close) => el('button', { class: 'btn btn-primary', type: 'button', onclick: async (ev) => {
      if (!sms.checked && !mail.checked) { err.textContent = 'Choose SMS or email.'; return; }
      ev.target.disabled = true;
      try {
        const r = await api.evaluationInvite(m.id, { sms: sms.checked, email: mail.checked });
        const parts = [];
        if (r.sms) parts.push(`SMS: ${sendState(r.sms)}`);
        if (r.email) parts.push(`Email: ${r.email.ok ? 'sent' : (SEND_DETAIL[r.email.detail] || r.email.detail)}`);
        close(); toast(parts.join(' · '), (r.sms?.ok || r.email?.ok) ? '' : 'error');
      } catch (e) { ev.target.disabled = false; err.textContent = portalMsg(e); }
    } }, 'Send')],
  });
}

function recordModal(ctx, m, d, reload) {
  const hasBaseline = d.measurements.some(x => x.is_baseline);
  const n = (attrs = {}) => input({ type: 'number', step: 'any', min: 0, ...attrs });
  const F = { weight: n(), height: n(), waist: n(), arm: n(), leg: n(), ex: input({ maxlength: 80 }), load: n(), reps: n({ step: 1, min: 1 }), cond: input({ maxlength: 200 }),
    walk: n({ step: 1 }), chair30: n({ step: 1 }), chair: select([{ value: '', label: '—', selected: true }, ...[1, 2, 3, 4, 5].map(v => ({ value: String(v), label: String(v) }))]),
    energy: select([{ value: '', label: '—', selected: true }, ...[1, 2, 3, 4, 5].map(v => ({ value: String(v), label: String(v) }))]) };
  const source = select([{ value: 'staff', label: 'Taken by staff', selected: true }, { value: 'member', label: 'Reported by the member' }]);
  const baseline = el('input', { type: 'checkbox', disabled: hasBaseline });
  const date = input({ type: 'date', value: new Date().toISOString().slice(0, 10) });
  const err = el('p', { class: 'form-error', role: 'alert' });
  const form = el('form', { class: 'form-grid', onsubmit: (e) => e.preventDefault() },
    field('Date', date), field('Source', source),
    field('Weight (lb)', F.weight), field('Height (in)', F.height), field('Waist (in)', F.waist), field('Arm (in)', F.arm), field('Thigh (in)', F.leg),
    field('Strength exercise', F.ex, { hint: 'Same exercise/machine each time' }), field('Load (lb)', F.load), field('Reps', F.reps), field('Test conditions', F.cond, { full: true }),
    field('Walking (min)', F.walk), field('30-s chair stand (reps)', F.chair30), field('Getting up from a chair (1-5)', F.chair), field('Energy (1-5)', F.energy),
    el('label', { class: 'check full' }, baseline, hasBaseline ? ' Baseline already recorded (cannot be changed)' : ' This is the initial evaluation (baseline)'), err);
  openModal({ title: `Record measurement · ${m.full_name || ''}`, body: form, actions: [(close) => el('button', { class: 'btn btn-primary', type: 'button', onclick: async (ev) => {
    err.textContent = '';
    const v = (x) => (x.value === '' ? null : Number(x.value));
    const body = { weight_lb: v(F.weight), height_in: v(F.height), waist_in: v(F.waist), left_arm_in: v(F.arm), left_leg_in: v(F.leg) };
    const hasBody = Object.values(body).some(x => x !== null);
    const rows = [];
    const common = { entry_date: date.value, source: source.value, conditions: null, is_baseline: baseline.checked };
    if (F.ex.value.trim() || F.load.value || F.reps.value) {
      if (!F.ex.value.trim() || F.load.value === '' || !F.reps.value) { err.textContent = 'Strength needs exercise, load and reps.'; return; }
      rows.push({ ...common, category: 'strength', metric: 'exercise', exercise: F.ex.value.trim(), value: v(F.load), reps: Math.round(v(F.reps)), conditions: F.cond.value.trim() || null });
    }
    if (F.walk.value) rows.push({ ...common, category: 'endurance', metric: 'walk', value: v(F.walk) });
    if (F.chair30.value !== '') rows.push({ ...common, category: 'mobility', metric: 'chair_stand_30s', value: v(F.chair30), conditions: F.cond.value.trim() || null });
    if (F.chair.value) rows.push({ ...common, category: 'mobility', metric: 'chair_rise', value: v(F.chair) });
    if (F.energy.value) rows.push({ ...common, category: 'wellbeing', metric: 'energy', value: v(F.energy) });
    if (!hasBody && !rows.length) { err.textContent = 'Enter at least one value.'; return; }
    ev.target.disabled = true;
    try {
      if (hasBody) await api.recordMeasurement(ctx.tenantId, m.id, { ...body, measurement_date: date.value, source: source.value, is_baseline: baseline.checked && !hasBaseline });
      if (rows.length) await api.recordProgress(ctx.tenantId, m.id, rows);
      close(); toast('Saved'); reload();
    } catch (ex) {
      ev.target.disabled = false;
      err.textContent = /duplicate|unique/i.test(ex.message) ? 'A baseline already exists for one of these indicators.' : /check constraint/i.test(ex.message) ? 'A value is out of range.' : ex.message;
    }
  } }, 'Save')] });
}
