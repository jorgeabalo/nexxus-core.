// Acceso a datos del Manager Panel. Todas las consultas usan la sesión del
// usuario (JWT) y filtran por tenant_id explícitamente; además RLS en
// Supabase impide leer o escribir filas de otro tenant aunque se omitiera
// el filtro. Nada aquí inventa datos: si no hay filas, se devuelve vacío/0.

let sb = null;
export function initApi(client) { sb = client; }

function must({ data, error }) {
  if (error) throw error;
  return data;
}
function uid() { return sb.auth.getSession().then(r => r.data.session?.user?.id); }

export const api = {
  // ----- contexto -----
  async myMemberships(userId) {
    const rows = must(await sb.from('tenant_users')
      .select('role, staff_id, tenant:tenants(id, slug, name, vertical, timezone, branding, modules)')
      .eq('user_id', userId).eq('active', true));
    return (rows || []).filter(r => r.tenant);
  },

  // ----- dashboard (RPC security invoker) -----
  async dashboard(tenantId) { return must(await sb.rpc('manager_dashboard', { p_tenant: tenantId })); },
  async activity(tenantId, limit = 50) { return must(await sb.rpc('manager_activity', { p_tenant: tenantId, p_limit: limit })) || []; },
  async alerts(tenantId) { return must(await sb.rpc('manager_alerts', { p_tenant: tenantId })) || []; },
  async resolveAlert(id) {
    return must(await sb.from('alerts').update({ status: 'resolved', resolved_at: new Date().toISOString(), resolved_by: await uid() }).eq('id', id));
  },

  // ----- members -----
  async members(tenantId, { search = '', filter = 'all' } = {}) {
    let q = sb.from('member_overview').select('*').eq('tenant_id', tenantId);
    if (filter === 'active') q = q.eq('membership_status', 'active');
    if (filter === 'inactive') q = q.neq('membership_status', 'active');
    if (filter === 'past_due') q = q.eq('is_past_due', true);
    if (filter === 'new') q = q.eq('is_new', true);
    const s = search.trim().replace(/[,()*%]/g, ' ').trim();
    if (s) {
      const digits = s.replace(/\D/g, '');
      const ors = [`full_name.ilike.*${s}*`, `member_code.ilike.*${s}*`, `email.ilike.*${s}*`];
      if (digits.length >= 3) ors.push(`phone.ilike.*${digits}*`);
      q = q.or(ors.join(','));
    }
    return must(await q.order('first_name', { ascending: true }).limit(500)) || [];
  },
  async member(tenantId, id) {
    return must(await sb.from('member_overview').select('*').eq('tenant_id', tenantId).eq('id', id).maybeSingle());
  },
  async memberActivity(tenantId, id) {
    const [checkins, appts, pays] = await Promise.all([
      sb.from('check_ins').select('id, check_in_time, method').eq('tenant_id', tenantId).eq('member_id', id).order('check_in_time', { ascending: false }).limit(10),
      sb.from('appointment_overview').select('*').eq('tenant_id', tenantId).eq('member_id', id).order('appointment_date', { ascending: false }).limit(10),
      sb.from('payment_overview').select('*').eq('tenant_id', tenantId).eq('member_id', id).order('created_at', { ascending: false }).limit(10),
    ]);
    return { checkins: must(checkins) || [], appointments: must(appts) || [], payments: must(pays) || [] };
  },
  async createMember(tenantId, values) {
    return must(await sb.from('members').insert({ ...values, tenant_id: tenantId }).select('id').single());
  },
  async memberOptions(tenantId) {
    return must(await sb.from('members').select('id, first_name, last_name, member_id, phone')
      .eq('tenant_id', tenantId).order('first_name').limit(1000)) || [];
  },

  // ----- schedule -----
  async appointments(tenantId, from, to) {
    return must(await sb.from('appointment_overview').select('*').eq('tenant_id', tenantId)
      .gte('appointment_date', from).lte('appointment_date', to)
      .order('appointment_date').order('start_time')) || [];
  },
  async services(tenantId) {
    return must(await sb.from('services').select('id, name, duration_minutes, active').eq('tenant_id', tenantId).eq('active', true).order('name')) || [];
  },
  async staff(tenantId) {
    return must(await sb.from('staff').select('id, first_name, last_name, role, active').eq('tenant_id', tenantId).eq('active', true).order('first_name')) || [];
  },
  async createAppointment(tenantId, values) {
    return must(await sb.from('appointments').insert({ ...values, tenant_id: tenantId, created_by: await uid() }).select('id').single());
  },
  async updateAppointment(tenantId, id, values) {
    return must(await sb.from('appointments').update(values).eq('tenant_id', tenantId).eq('id', id));
  },
  async rescheduleAppointment(tenantId, original, { appointment_date, start_time, end_time }) {
    // Se crea una cita nueva enlazada (rescheduled_from) y la original queda
    // como "rescheduled": el historial se conserva.
    const fresh = await this.createAppointment(tenantId, {
      member_id: original.member_id, staff_id: original.staff_id, service: original.service,
      client_name: original.client_name, client_phone: original.client_phone,
      call_id: original.call_id, lead_id: original.lead_id, notes: original.notes,
      source: original.source, status: 'scheduled',
      appointment_date, start_time, end_time: end_time || null, rescheduled_from: original.id,
    });
    await this.updateAppointment(tenantId, original.id, { status: 'rescheduled' });
    return fresh;
  },

  // ----- claudia -----
  async calls(tenantId, sinceISO) {
    let q = sb.from('call_overview').select('*').eq('tenant_id', tenantId);
    if (sinceISO) q = q.gte('started_at', sinceISO);
    return must(await q.order('started_at', { ascending: false }).limit(500)) || [];
  },
  async call(tenantId, id) {
    return must(await sb.from('call_overview').select('*').eq('tenant_id', tenantId).eq('id', id).maybeSingle());
  },
  async callLinks(tenantId, call) {
    const [appt, alerts] = await Promise.all([
      sb.from('appointment_overview').select('*').eq('tenant_id', tenantId)
        .or([`call_id.eq.${call.id}`, call.appointment_id ? `id.eq.${call.appointment_id}` : null, call.lead_id ? `lead_id.eq.${call.lead_id}` : null].filter(Boolean).join(',')),
      sb.from('alerts').select('*').eq('tenant_id', tenantId).eq('call_id', call.id).order('created_at', { ascending: false }),
    ]);
    return { appointments: must(appt) || [], alerts: must(alerts) || [] };
  },
  async markLeadContacted(tenantId, call) {
    if (call.lead_id) {
      must(await sb.from('leads').update({ status: 'contacted', contacted_at: new Date().toISOString() }).eq('tenant_id', tenantId).eq('id', call.lead_id));
    }
    must(await sb.from('calls').update({ follow_up_required: false }).eq('tenant_id', tenantId).eq('id', call.id));
    must(await sb.from('alerts').update({ status: 'resolved', resolved_at: new Date().toISOString(), resolved_by: await uid() })
      .eq('tenant_id', tenantId).eq('call_id', call.id).eq('status', 'open'));
  },

  // ----- payments -----
  async payments(tenantId, status = 'all') {
    let q = sb.from('payment_overview').select('*').eq('tenant_id', tenantId);
    if (status !== 'all') q = q.eq('effective_status', status);
    return must(await q.order('created_at', { ascending: false }).limit(500)) || [];
  },
  async createPayment(tenantId, values) {
    return must(await sb.from('payments').insert({ ...values, tenant_id: tenantId }).select('id').single());
  },
  async markPaid(tenantId, id, method) {
    return must(await sb.from('payments').update({ payment_status: 'paid', payment_date: new Date().toISOString(), payment_method: method || null })
      .eq('tenant_id', tenantId).eq('id', id));
  },
};
