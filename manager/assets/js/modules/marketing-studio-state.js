// AITA Marketing (Fase 2) — lógica pura de Biblioteca y Estudio (sin DOM), probada con Node.
// Las reglas reales viven en el backend; aquí solo se presenta y se ayuda a elegir.

export const MIX_PRESETS = [[100, 0], [75, 25], [50, 50], [25, 75], [0, 100]];
export const MIX_STEP = 5;
export const OBJECTIVES = ['new_members', 'class_promo', 'event', 'offer', 'brand', 'education'];
export const AUDIENCES = ['general', 'seniors_60_plus', 'beginners', 'athletes', 'parents', 'custom'];
export const STYLES = ['energetic', 'calm', 'professional', 'cinematic', 'educational'];
export const DURATIONS = [15, 30, 45, 60];
export const TARGETS = ['instagram', 'facebook', 'tiktok', 'youtube_shorts'];
export const QUALITY = ['draft', 'standard', 'premium'];
export const PEOPLE_POLICIES = ['exclude', 'anonymize', 'consented', 'no_people'];
export const ANON_METHODS = ['pixelate_faces', 'blur_faces', 'crop_people', 'silhouette', 'replace_background_and_people'];
export const WIZARD_STEPS = ['objective', 'audience', 'sources', 'style', 'script', 'cost', 'review'];
export const ACCEPT = 'image/png,image/jpeg,image/webp,image/gif,video/mp4,video/quicktime,video/webm';

// Porcentaje real redondeado al paso del deslizador; IA = 100 - real. Siempre suma 100.
export function normalizeMix(real, step = MIX_STEP) {
  const n = Number(real);
  const r = Number.isFinite(n) ? Math.min(100, Math.max(0, Math.round(n / step) * step)) : 50;
  return { real: r, ai: 100 - r };
}

// Misma aproximación que el backend (resto mayor): escenas y segundos por origen, ANTES de generar.
function hamilton(total, weights) {
  const s = weights.reduce((a, b) => a + b, 0);
  if (!s || !total) return weights.map(() => 0);
  const exact = weights.map(w => total * w / s);
  const out = exact.map(Math.floor);
  const order = exact.map((x, i) => [x - out[i], i]).sort((a, b) => b[0] - a[0] || a[1] - b[1]);
  for (let k = 0; k < total - out.reduce((a, b) => a + b, 0); k++) out[order[k][1]] += 1;
  return out;
}
export function approxScenes(real, scenes, seconds, adaptReal = false) {
  const { real: r, ai } = normalizeMix(real);
  const [nReal, nAi] = hamilton(scenes, [r, ai]);
  const per = hamilton(seconds, Array(scenes).fill(1));
  const realSecs = per.slice(0, nReal).reduce((a, b) => a + b, 0);   // aproximado; el backend reparte exacto
  return { realPercent: r, aiPercent: ai, realScenes: nReal, aiScenes: nAi, realSeconds: realSecs,
    aiSeconds: seconds - realSecs, realOrigin: adaptReal ? 'client_ai_adapted' : 'client_original' };
}

// Clase de privacidad del original (espejo informativo de services/marketing_privacy.py).
// Personas sin confirmar que NO hay menores → siempre restringido.
const minorsRisk = (m) => m.contains_people !== false && m.contains_minors !== false;
export function privacyClass(m) {
  if (!m || minorsRisk(m) || m.consent_status === 'revoked') return 'restricted';
  if (m.contains_people === false && m.people_policy === 'no_people') return 'business_media_no_people';
  if (m.people_policy === 'consented' && m.consent_status === 'granted') return 'consented_people';
  return 'restricted';
}
// ¿Se puede elegir este archivo como fuente de un Reel? Un derivado simulado nunca cuenta.
export function usableAsSource(m) {
  if (!m || m.processing_status !== 'ready' || m.validation_status !== 'passed') return false;
  if (minorsRisk(m) || m.consent_status === 'revoked') return false;
  if (m.people_policy === 'anonymize') {
    return (m.derivatives || []).some(d => d.kind === 'anonymized' && d.status === 'ready' && !d.is_mock);
  }
  return privacyClass(m) !== 'restricted';
}

// Estados del Reel claramente distintos: nunca se insinúa que algo se publicó.
export function reelStage(job, content, publications = []) {
  if (job && job.status !== 'succeeded') return job.status === 'failed' || job.status === 'cancelled' ? 'failed' : 'generating';
  if (!content) return 'generated';
  const s = content.status;
  if (s === 'published') return 'published';
  if (s === 'failed') return 'failed';
  if (s === 'publishing' || (s === 'scheduled' && publications.some(p => p.status === 'pending'))) return 'pending_publication';
  if (s === 'scheduled') return 'scheduled';
  if (s === 'approved') return 'approved';
  return 'in_review';
}

export function fmtBytes(n) {
  const v = Number(n || 0);
  if (v >= 1024 ** 3) return `${(v / 1024 ** 3).toFixed(1)} GB`;
  if (v >= 1024 ** 2) return `${(v / 1024 ** 2).toFixed(1)} MB`;
  if (v >= 1024) return `${Math.round(v / 1024)} KB`;
  return `${v} B`;
}

export function fmtCost(v, currency = 'USD') {
  const n = Number(v || 0);
  return `${currency} ${n.toFixed(n && n < 0.1 ? 4 : 2)}`;
}

// Clave de idempotencia por envío del asistente (evita trabajos duplicados por doble clic).
export function newIdempotencyKey(rand = () => Math.random()) {
  return `job-${Date.now().toString(36)}-${Array.from({ length: 16 }, () => Math.floor(rand() * 36).toString(36)).join('')}`;
}
