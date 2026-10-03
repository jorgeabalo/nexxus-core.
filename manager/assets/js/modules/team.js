// Equipo: quién entra al Manager Panel, invitar gerentes y quitar accesos.
// Todo pasa por el backend, que valida el rol con el JWT del usuario.
import { el, clear, card, table, badge, errorBox, loading, fmtDate, openModal, field, input, select, toast } from '../ui.js';
import { api } from '../api.js';

const ROLE_HELP = {
  owner: 'Full access, can invite anyone and remove access.',
  manager: 'Full access to the dashboard, can invite managers and staff.',
  staff: 'Day-to-day access (members, schedule, payments).',
};
const ERRORS = {
  invalid_email: 'Enter a valid email.',
  role_not_allowed: 'Your role cannot invite that role.',
  rate_limited: 'Too many emails sent. Wait a few minutes and try again.',
  invite_failed: 'The invitation email could not be sent. Try again later.',
  cannot_remove_self: 'You cannot remove your own access.',
  forbidden: 'Only owners and managers can manage the team.',
  portal_not_configured: 'The server is not configured yet (Supabase).',
};
const msg = (e) => ERRORS[e.code] || e.message || 'Something went wrong';

export async function render(root, ctx) {
  const canInvite = ['owner', 'manager'].includes(ctx.role);
  root.appendChild(el('div', { class: 'page-head' },
    el('div', {}, el('h1', {}, 'Team'), el('p', {}, 'People who can sign in to this Manager Dashboard.')),
    canInvite ? el('button', { class: 'btn btn-primary', type: 'button', onclick: () => inviteModal(ctx, reload) }, '+ Invite manager') : null));

  const body = el('div', { class: 'stack' }, loading());
  root.appendChild(body);

  async function reload() {
    let d;
    try { d = await api.team(ctx.tenantId); } catch (e) { return clear(body).appendChild(errorBox(e)); }
    if (!ctx.isCurrent()) return;
    const isOwner = d.me.role === 'owner';
    clear(body).append(
      card('With access', table([
        { label: 'Email', render: p => el('span', {}, p.email || '—', p.is_me ? el('span', { class: 'muted small' }, ' (you)') : null) },
        { label: 'Role', render: p => badge(p.role) },
        { label: 'Since', render: p => p.since ? fmtDate(p.since) : '—' },
        { label: '', render: p => isOwner && !p.is_me
          ? el('button', { class: 'btn btn-sm', type: 'button', onclick: () => revokeModal(ctx, p, reload) }, 'Remove access') : null },
      ], d.members, { emptyText: 'Nobody yet' })),
      card('Pending invitations', table([
        { label: 'Email', key: 'email' },
        { label: 'Role', render: i => badge(i.role) },
        { label: 'Invited', render: i => i.created_at ? fmtDate(i.created_at) : '—' },
        { label: '', render: i => canInvite ? el('div', { class: 'btn-row' },
          el('button', { class: 'btn btn-sm', type: 'button', onclick: () => resend(ctx, i, reload) }, 'Resend'),
          el('button', { class: 'btn btn-sm', type: 'button', onclick: () => cancelInvite(ctx, i, reload) }, 'Cancel')) : null },
      ], d.pending, { emptyText: 'No pending invitations' })));
  }
  await reload();
}

function inviteModal(ctx, reload) {
  const email = input({ type: 'email', autocomplete: 'off', required: true, placeholder: 'name@email.com' });
  const roles = ctx.role === 'owner' ? ['manager', 'staff', 'owner'] : ['manager', 'staff'];
  const role = select(roles.map((r, i) => ({ value: r, label: r[0].toUpperCase() + r.slice(1), selected: i === 0 })));
  const help = el('p', { class: 'hint' }, ROLE_HELP[role.value]);
  role.addEventListener('change', () => { help.textContent = ROLE_HELP[role.value]; });
  const err = el('p', { class: 'form-error', role: 'alert' });
  const form = el('form', { class: 'form-grid', onsubmit: (e) => e.preventDefault() },
    field('Email *', email, { full: true }), field('Role', role, { full: true }), help, err,
    el('p', { class: 'hint' }, 'They will receive an email to create their password and sign in at /manager.'));
  openModal({
    title: 'Invite manager', body: form, actions: [(close) => el('button', {
      class: 'btn btn-primary', type: 'button', onclick: async (e) => {
        err.textContent = '';
        e.target.disabled = true;
        try {
          const r = await api.teamInvite(ctx.tenantId, email.value.trim(), role.value);
          close();
          toast(r.status === 'existing_account'
            ? `${r.email} already had an account: access granted and a sign-in email was sent.`
            : `Invitation sent to ${r.email}`);
          reload();
        } catch (ex) { e.target.disabled = false; err.textContent = msg(ex); }
      },
    }, 'Send invitation')],
  });
}

async function resend(ctx, inv, reload) {
  try { await api.teamInvite(ctx.tenantId, inv.email, inv.role); toast(`Invitation sent again to ${inv.email}`); reload(); }
  catch (e) { toast(msg(e), 'error'); }
}

async function cancelInvite(ctx, inv, reload) {
  try { await api.teamCancelInvite(ctx.tenantId, inv.id); toast('Invitation cancelled'); reload(); }
  catch (e) { toast(msg(e), 'error'); }
}

function revokeModal(ctx, person, reload) {
  openModal({
    title: 'Remove access',
    body: el('p', {}, `${person.email || 'This person'} will no longer be able to sign in to this dashboard. You can invite them again later.`),
    actions: [(close) => el('button', {
      class: 'btn btn-primary', type: 'button', onclick: async (e) => {
        e.target.disabled = true;
        try { await api.teamRevoke(ctx.tenantId, person.user_id); close(); toast('Access removed'); reload(); }
        catch (ex) { e.target.disabled = false; toast(msg(ex), 'error'); }
      },
    }, 'Remove access')],
  });
}
