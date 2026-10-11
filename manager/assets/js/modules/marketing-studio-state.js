// AITA Marketing — lógica pura de la Biblioteca y del planificador (sin DOM), probada con Node.
// Las reglas reales viven en el backend; aquí solo se presenta y se ayuda a elegir.

export const PEOPLE_POLICIES = ['exclude', 'anonymize', 'consented', 'no_people'];
export const ANON_METHODS = ['pixelate_faces', 'blur_faces', 'crop_people', 'silhouette', 'replace_background_and_people'];
export const ACCEPT = 'image/png,image/jpeg,image/webp,image/gif,video/mp4,video/quicktime,video/webm';

// Clase de privacidad del original (espejo informativo de services/marketing_privacy.py).
// Personas sin confirmar que NO hay menores → siempre restringido.
const minorsRisk = (m) => m.contains_people !== false && m.contains_minors !== false;
export function privacyClass(m) {
  if (!m || minorsRisk(m) || m.consent_status === 'revoked') return 'restricted';
  if (m.contains_people === false && m.people_policy === 'no_people') return 'business_media_no_people';
  if (m.people_policy === 'consented' && m.consent_status === 'granted') return 'consented_people';
  return 'restricted';
}
// ¿Se puede elegir este archivo en «Usar mi Biblioteca y mejorar con IA»? Un derivado simulado nunca cuenta,
// y un archivo vencido no se ofrece (el backend tampoco lo devuelve).
export function usableAsSource(m, now = Date.now()) {
  if (!m || m.processing_status !== 'ready' || m.validation_status !== 'passed') return false;
  if (m.expires_at && Date.parse(m.expires_at) <= now) return false;
  if (minorsRisk(m) || m.consent_status === 'revoked') return false;
  if (m.people_policy === 'anonymize') {
    return (m.derivatives || []).some(d => d.kind === 'anonymized' && d.status === 'ready' && !d.is_mock);
  }
  return privacyClass(m) !== 'restricted';
}

export function fmtBytes(n) {
  const v = Number(n || 0);
  if (v >= 1024 ** 3) return `${(v / 1024 ** 3).toFixed(1)} GB`;
  if (v >= 1024 ** 2) return `${(v / 1024 ** 2).toFixed(1)} MB`;
  if (v >= 1024) return `${Math.round(v / 1024)} KB`;
  return `${v} B`;
}

// Clave de idempotencia por envío (evita duplicados por doble clic).
export function newIdempotencyKey(rand = () => Math.random()) {
  return `plan-${Date.now().toString(36)}-${Array.from({ length: 16 }, () => Math.floor(rand() * 36).toString(36)).join('')}`;
}
