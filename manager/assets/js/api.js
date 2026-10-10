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
async function backend(method, path, body, retried = false) {
  const token = (await sb.auth.getSession()).data.session?.access_token;
  const res = await fetch(path, {
    method, headers: { Authorization: `Bearer ${token || ''}`, ...(body ? { 'Content-Type': 'application/json' } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (res.status === 401 && !retried) {
    // El token puede pertenecer a una sesión ya cerrada: se renueva una vez;
    // si no se puede, se cierra la sesión para volver a entrar.
    const { error } = await sb.auth.refreshSession();
    if (!error) return backend(method, path, body, true);
    await sb.auth.signOut();
  }
  if (!res.ok) { const e = new Error(data.error || `HTTP ${res.status}`); e.code = data.error; throw e; }
  return data;
}
function uid() { return sb.auth.getSession().then(r => r.data.session?.user?.id); }
function qs(params) {
  return new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== '')).toString();
}
async function blob(path) {
  const token = (await sb.auth.getSession()).data.session?.access_token;
  const res = await fetch(path, { headers: { Authorization: `Bearer ${token || ''}` }, cache: 'no-store' });
  if (!res.ok) { const data = await res.json().catch(() => ({})); const e = new Error(data.error || `HTTP ${res.status}`); e.code = data.error; throw e; }
  return res.blob();
}

export const api = {
  // ----- contexto -----
  async myMemberships(userId) {
    const rows = must(await sb.from('tenant_users')
      .select('role, staff_id, tenant:tenants(id, slug, name, vertical, timezone, branding, modules, public_phone:settings->>public_phone, address:settings->address, role_modules:settings->role_modules)')
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

  // ----- member portal (backend: valida owner/manager con el JWT) -----
  async portalLink(memberId) { return backend('GET', `/api/manager/members/${encodeURIComponent(memberId)}/portal-link`); },
  async portalAccess(memberId, { sms = false, email = false, regenerate = false } = {}) {
    return backend('POST', `/api/manager/members/${encodeURIComponent(memberId)}/portal-access`, { sms, email, regenerate });
  },

  // ----- equipo (backend: valida owner/manager con el JWT) -----
  async team(tenantId) { return backend('GET', `/api/manager/team?tenant_id=${encodeURIComponent(tenantId)}`); },
  async teamInvite(tenantId, email, role) { return backend('POST', '/api/manager/team/invite', { tenant_id: tenantId, email, role }); },
  async teamRevoke(tenantId, userId) {
    return backend('POST', `/api/manager/team/${encodeURIComponent(userId)}/revoke`, { tenant_id: tenantId });
  },
  async teamCancelInvite(tenantId, inviteId) {
    return backend('POST', `/api/manager/team/invites/${encodeURIComponent(inviteId)}/cancel`, { tenant_id: tenantId });
  },

  async evaluationInvite(memberId, { sms = false, email = false } = {}) {
    return backend('POST', `/api/manager/members/${encodeURIComponent(memberId)}/evaluation-invite`, { sms, email });
  },
  async smsRefresh(memberId) { return backend('POST', `/api/manager/members/${encodeURIComponent(memberId)}/sms-refresh`, {}); },
  async smsDiagnostics() { return backend('GET', '/api/manager/sms-diagnostics'); },
  async evaluationPdf(evaluationId) {
    const token = (await sb.auth.getSession()).data.session?.access_token;
    const res = await fetch(`/api/manager/evaluations/${encodeURIComponent(evaluationId)}/pdf?lang=en`, { headers: { Authorization: `Bearer ${token || ''}` }, cache: 'no-store' });
    if (!res.ok) { const e = new Error(`HTTP ${res.status}`); throw e; }
    return res.blob();
  },

  async documentFile(docId, n) {
    const token = (await sb.auth.getSession()).data.session?.access_token;
    const res = await fetch(`/api/manager/evaluation-documents/${encodeURIComponent(docId)}/files/${Number(n)}`, { headers: { Authorization: `Bearer ${token || ''}` }, cache: 'no-store' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.blob();
  },

  // ----- progress & evaluations (RLS: staff del mismo gym) -----
  async memberProgress(tenantId, id) {
    const [meas, prog, evals, qs, mem, docs] = await Promise.all([
      sb.from('measurements').select('*').eq('tenant_id', tenantId).eq('member_id', id).order('measurement_date').order('created_at'),
      sb.from('progress_entries').select('*').eq('tenant_id', tenantId).eq('member_id', id).order('entry_date').order('created_at'),
      sb.from('member_evaluations').select('id, kind, status, submitted_at, updated_at, questionnaire_id, questionnaire_version, answers, next_due_date, current_step')
        .eq('tenant_id', tenantId).eq('member_id', id).order('started_at'),
      sb.from('questionnaires').select('id, version, title, definition, active').eq('tenant_id', tenantId),
      sb.from('members').select('gender, joined_as, next_evaluation_due, onboarding_completed').eq('tenant_id', tenantId).eq('id', id).maybeSingle(),
      // RLS: solo owner/manager ven los documentos (datos de salud)
      sb.from('evaluation_documents').select('id, kind, status, files, extraction, extraction_error, evaluation_id, created_at, confirmed_at, questionnaire_version, uploaded_via')
        .eq('tenant_id', tenantId).eq('member_id', id).order('created_at', { ascending: false }),
    ]);
    return { measurements: must(meas) || [], progress: must(prog) || [], evaluations: must(evals) || [], questionnaires: must(qs) || [], member: must(mem) || {},
             documents: docs.error ? [] : (docs.data || []) };
  },
  async recordMeasurement(tenantId, memberId, values) {
    return must(await sb.from('measurements').insert({ ...values, tenant_id: tenantId, member_id: memberId }).select('id').single());
  },
  async recordProgress(tenantId, memberId, rows) {
    return must(await sb.from('progress_entries').insert(rows.map(r => ({ ...r, tenant_id: tenantId, member_id: memberId, metric_key: '-' }))).select('id'));
  },
  async updateMember(tenantId, id, values) {
    return must(await sb.from('members').update(values).eq('tenant_id', tenantId).eq('id', id));
  },

  // ----- contabilidad (backend: valida rol y tenant con el JWT) -----
  accounting: {
    summary(tenantId, start, end) { return backend('GET', `/api/manager/accounting/summary?${qs({ tenant_id: tenantId, start, end })}`); },
    transactions(tenantId, start, end) { return backend('GET', `/api/manager/accounting/transactions?${qs({ tenant_id: tenantId, start, end })}`); },
    createTransaction(tenantId, values) { return backend('POST', '/api/manager/accounting/transactions', { ...values, tenant_id: tenantId }); },
    updateTransaction(tenantId, id, values) { return backend('PATCH', `/api/manager/accounting/transactions/${encodeURIComponent(id)}`, { ...values, tenant_id: tenantId }); },
    cancelTransaction(tenantId, id) { return backend('POST', `/api/manager/accounting/transactions/${encodeURIComponent(id)}/cancel`, { tenant_id: tenantId }); },
    categories(tenantId) { return backend('GET', `/api/manager/accounting/categories?${qs({ tenant_id: tenantId })}`); },
    createCategory(tenantId, values) { return backend('POST', '/api/manager/accounting/categories', { ...values, tenant_id: tenantId }); },
    updateCategory(tenantId, id, values) { return backend('PATCH', `/api/manager/accounting/categories/${encodeURIComponent(id)}`, { ...values, tenant_id: tenantId }); },
    obligations(tenantId) { return backend('GET', `/api/manager/accounting/obligations?${qs({ tenant_id: tenantId })}`); },
    createObligation(tenantId, values) { return backend('POST', '/api/manager/accounting/obligations', { ...values, tenant_id: tenantId }); },
    payObligation(tenantId, id, values) { return backend('POST', `/api/manager/accounting/obligations/${encodeURIComponent(id)}/pay`, { ...values, tenant_id: tenantId }); },
    async uploadReceipt(tenantId, id, file) {
      const token = (await sb.auth.getSession()).data.session?.access_token;
      const form = new FormData();
      form.append('tenant_id', tenantId);
      form.append('file', file);
      const res = await fetch(`/api/manager/accounting/transactions/${encodeURIComponent(id)}/receipt`, { method: 'POST', headers: { Authorization: `Bearer ${token || ''}` }, body: form });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) { const e = new Error(data.error || `HTTP ${res.status}`); e.code = data.error; throw e; }
      return data;
    },
    receipt(tenantId, id) { return blob(`/api/manager/accounting/transactions/${encodeURIComponent(id)}/receipt?${qs({ tenant_id: tenantId })}`); },
    exportReport(tenantId, start, end, format, lang) { return blob(`/api/manager/accounting/export?${qs({ tenant_id: tenantId, start, end, format, lang })}`); },
  },

  // ----- AITA Marketing (backend: solo owner/manager; valida tenant y reglas de estado) -----
  marketing: {
    dashboard(tenantId) { return backend('GET', `/api/manager/marketing/dashboard?${qs({ tenant_id: tenantId })}`); },
    brand(tenantId) { return backend('GET', `/api/manager/marketing/brand?${qs({ tenant_id: tenantId })}`); },
    saveBrand(tenantId, values) { return backend('PUT', '/api/manager/marketing/brand', { ...values, tenant_id: tenantId }); },
    campaigns(tenantId) { return backend('GET', `/api/manager/marketing/campaigns?${qs({ tenant_id: tenantId })}`); },
    createCampaign(tenantId, values) { return backend('POST', '/api/manager/marketing/campaigns', { ...values, tenant_id: tenantId }); },
    updateCampaign(tenantId, id, values) { return backend('PATCH', `/api/manager/marketing/campaigns/${encodeURIComponent(id)}`, { ...values, tenant_id: tenantId }); },
    contentList(tenantId, { status, campaignId } = {}) {
      return backend('GET', `/api/manager/marketing/content?${qs({ tenant_id: tenantId, status, campaign_id: campaignId })}`);
    },
    content(tenantId, id) { return backend('GET', `/api/manager/marketing/content/${encodeURIComponent(id)}?${qs({ tenant_id: tenantId })}`); },
    createContent(tenantId, values) { return backend('POST', '/api/manager/marketing/content', { ...values, tenant_id: tenantId }); },
    updateContent(tenantId, id, values) { return backend('PATCH', `/api/manager/marketing/content/${encodeURIComponent(id)}`, { ...values, tenant_id: tenantId }); },
    transition(tenantId, id, to, { comment, scheduledAt } = {}) {
      return backend('POST', `/api/manager/marketing/content/${encodeURIComponent(id)}/transition`,
        { tenant_id: tenantId, to, comment: comment || null, scheduled_at: scheduledAt || null });
    },
    calendar(tenantId, start, end) { return backend('GET', `/api/manager/marketing/calendar?${qs({ tenant_id: tenantId, start, end })}`); },
    // Fase 2: Biblioteca privada, Estudio de Reels y trabajos de generación (todo por el backend).
    library(tenantId) { return backend('GET', `/api/manager/marketing/library?${qs({ tenant_id: tenantId })}`); },
    // El archivo va como cuerpo (no multipart): el backend aplica el límite antes de leerlo.
    async upload(tenantId, file) {
      const token = (await sb.auth.getSession()).data.session?.access_token;
      const res = await fetch(`/api/manager/marketing/library?${qs({ tenant_id: tenantId })}`, {
        method: 'POST', body: file,
        headers: { Authorization: `Bearer ${token || ''}`, 'Content-Type': file.type || 'application/octet-stream',
          'X-File-Name': encodeURIComponent(file.name || '') },
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) { const e = new Error(data.error || `HTTP ${res.status}`); e.code = data.error; throw e; }
      return data;
    },
    // Autorización temporal same-origin para reproducir con HTTP Range (el <video> no envía Authorization).
    streamToken(tenantId, id, derivativeId) {
      return backend('POST', `/api/manager/marketing/library/${encodeURIComponent(id)}/stream-token`, { tenant_id: tenantId, derivative_id: derivativeId || null });
    },
    streamRevoke(tenantId, id) { return backend('POST', `/api/manager/marketing/library/${encodeURIComponent(id)}/stream-revoke`, { tenant_id: tenantId }); },
    setRetention(tenantId, id, days) { return backend('POST', `/api/manager/marketing/library/${encodeURIComponent(id)}/retention`, { tenant_id: tenantId, days }); },
    revokeConsent(tenantId, id) { return backend('POST', `/api/manager/marketing/library/${encodeURIComponent(id)}/revoke-consent`, { tenant_id: tenantId }); },
    deleteMedia(tenantId, id, reason) {
      return backend('POST', `/api/manager/marketing/library/${encodeURIComponent(id)}/delete`, { tenant_id: tenantId, reason, confirm: true });
    },
    classify(tenantId, id, values) { return backend('PATCH', `/api/manager/marketing/library/${encodeURIComponent(id)}/privacy`, { ...values, tenant_id: tenantId }); },
    archive(tenantId, id, archived) { return backend('POST', `/api/manager/marketing/library/${encodeURIComponent(id)}/archive`, { tenant_id: tenantId, archived }); },
    anonymize(tenantId, id, method) { return backend('POST', `/api/manager/marketing/library/${encodeURIComponent(id)}/anonymize`, { tenant_id: tenantId, method }); },
    reviewDerivative(tenantId, id, approve) {
      return backend('POST', `/api/manager/marketing/library/derivatives/${encodeURIComponent(id)}/review`, { tenant_id: tenantId, approve, confirm_reviewed: approve });
    },
    studio(tenantId) { return backend('GET', `/api/manager/marketing/studio?${qs({ tenant_id: tenantId })}`); },
    mixPreview(tenantId, values) { return backend('POST', '/api/manager/marketing/studio/mix-preview', { ...values, tenant_id: tenantId }); },
    jobs(tenantId) { return backend('GET', `/api/manager/marketing/jobs?${qs({ tenant_id: tenantId })}`); },
    job(tenantId, id) { return backend('GET', `/api/manager/marketing/jobs/${encodeURIComponent(id)}?${qs({ tenant_id: tenantId })}`); },
    createJob(tenantId, values) { return backend('POST', '/api/manager/marketing/jobs', { ...values, tenant_id: tenantId }); },
    jobAction(tenantId, id, action, values = {}) {
      return backend('POST', `/api/manager/marketing/jobs/${encodeURIComponent(id)}/${encodeURIComponent(action)}`, { ...values, tenant_id: tenantId });
    },
  },

  // ----- plan de entrenamiento (RLS: staff del mismo gym) -----
  async trainingPlan(tenantId, memberId) {
    const plan = must(await sb.from('training_plans').select('id, title, notes, updated_at')
      .eq('tenant_id', tenantId).eq('member_id', memberId).eq('active', true).maybeSingle());
    if (!plan) return null;
    const since = new Date(Date.now() - 120 * 86400000).toISOString().slice(0, 10);
    const [items, logs] = await Promise.all([
      sb.from('training_plan_items').select('day_of_week, position, exercise_key, name, sets, reps, duration_min, weight_lb, notes')
        .eq('tenant_id', tenantId).eq('plan_id', plan.id).order('day_of_week').order('position'),
      sb.from('training_logs').select('log_date, exercise_key, name, sets, reps, duration_min, weight_lb')
        .eq('tenant_id', tenantId).eq('member_id', memberId).gte('log_date', since).order('log_date'),
    ]);
    return { ...plan, items: must(items) || [], logs: (must(logs) || []).map(l => ({ ...l, date: l.log_date })) };
  },
  async saveTrainingPlan(tenantId, memberId, { title, notes, items }) {
    return must(await sb.rpc('manager_save_training_plan', { p_tenant: tenantId, p_member: memberId, p_title: title || '', p_notes: notes || null, p_items: items }));
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
