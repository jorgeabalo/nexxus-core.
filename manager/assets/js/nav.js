// Navegación de Nexxus Manager: catálogo de módulos, permisos por rol y perfil
// de la empresa activa. Lógica pura (sin DOM) para poder probarla aparte.
//
// Seguridad: esto solo decide qué se MUESTRA. Lo que cada persona puede leer o
// escribir lo siguen imponiendo RLS en Supabase y las comprobaciones del backend.

// Orden del menú. `key` = ruta (#/key) y clave en tenants.modules; no se cambian.
// `live` = el módulo ya tiene vista; si no, se muestra como "Próximamente".
export const CATALOG = [
  { key: 'dashboard', icon: 'dashboard', live: true },
  { key: 'members', icon: 'members', live: true },
  { key: 'attendance', icon: 'check', live: false },
  { key: 'schedule', icon: 'schedule', live: true },
  { key: 'payments', icon: 'payments', live: true },
  { key: 'accounting', icon: 'accounting', live: true },
  { key: 'team', icon: 'user', live: true },
  { key: 'inventory', icon: 'inventory', live: false },
  { key: 'marketing', icon: 'marketing', live: false },
  { key: 'claudia', icon: 'claudia', live: true },
  { key: 'settings', icon: 'settings', live: false },
  { key: 'agents', icon: 'agents', live: false, menu: false },   // ruta conservada; fuera del menú
];

// Máximo de módulos que puede ver cada rol. tenants.settings.role_modules solo
// puede RESTRINGIR esta lista (intersección), nunca ampliarla: staff jamás ve
// Personal (team) ni Configuración aunque role_modules los incluya.
// El owner ve siempre todos los módulos habilitados de su empresa.
export const ROLE_MAX = {
  manager: ['dashboard', 'members', 'attendance', 'schedule', 'payments', 'accounting', 'team',
    'inventory', 'marketing', 'claudia', 'agents'],
  staff: ['dashboard', 'members', 'attendance', 'schedule', 'payments', 'accounting', 'claudia'],
};

// Nombre genérico si el tenant no trae nombre (nunca el de un cliente concreto).
export const FALLBACK_NAME = 'Nexxus';

// Habilitado para la empresa: tenants.modules[key] distinto de false
// (mismo criterio que antes: un módulo nuevo sin clave cuenta como habilitado).
export function tenantEnabled(tenant, key) {
  return ((tenant && tenant.modules) || {})[key] !== false;
}

export function roleAllows(role, key, tenant) {
  if (role === 'owner') return true;
  const max = ROLE_MAX[role];
  if (!Array.isArray(max) || !max.includes(key)) return false;
  const custom = tenant && tenant.role_modules && tenant.role_modules[role];
  return Array.isArray(custom) ? custom.includes(key) : true;
}

export function canOpen(tenant, role, key) {
  const mod = CATALOG.find(m => m.key === key);
  return Boolean(mod) && tenantEnabled(tenant, key) && roleAllows(role, key, tenant);
}

// Módulos que aparecen en el menú para esta empresa y este rol.
export function visibleModules(tenant, role) {
  return CATALOG.filter(m => m.menu !== false && canOpen(tenant, role, m.key));
}

// Primer módulo al que puede entrar (para rutas vacías o no permitidas).
export function homeModule(tenant, role) {
  const v = visibleModules(tenant, role);
  return v.length ? v[0].key : null;
}

// Logo permitido (nunca SVG, nunca otro dominio):
//  * ruta local de una sola barra, solo con letras, números y . _ - ~ /, terminada en
//    .png/.jpg/.jpeg/.webp/.gif (opcionalmente con ?version). Sin backslash, sin "..",
//    sin "//", sin espacios ni controles y sin ningún "%": así no hay forma de colar un
//    SVG, otro dominio o una ruta distinta mediante caracteres codificados;
//  * o imagen rasterizada en base64 (PNG, JPEG, WebP, GIF) cuyo contenido empieza
//    con la firma real de ese formato (un SVG disfrazado de PNG no pasa).
const LOCAL_PATH = /^\/(?!\/)[A-Za-z0-9._~\-\/]+\.(?:png|jpe?g|webp|gif)(?:\?[A-Za-z0-9._~\-=&]*)?$/i;
const DATA_IMAGE = /^data:image\/(png|jpeg|webp|gif);base64,([A-Za-z0-9+/]+={0,2})$/;
const MAGIC = {
  png: (b) => b.startsWith('\x89PNG\r\n\x1a\n'),
  jpeg: (b) => b.startsWith('\xff\xd8\xff'),
  gif: (b) => b.startsWith('GIF87a') || b.startsWith('GIF89a'),
  webp: (b) => b.startsWith('RIFF') && b.slice(8, 12) === 'WEBP',
};
function safeLocalPath(url) {
  if (!LOCAL_PATH.test(url)) return false;
  const path = url.split('?')[0];
  return !path.includes('//') && !path.split('/').includes('..');
}
function safeDataImage(url) {
  const m = DATA_IMAGE.exec(url);
  if (!m) return false;
  let head;
  try { head = atob(m[2].slice(0, 24)); } catch (_) { return false; }
  return MAGIC[m[1]](head);
}
export function safeLogoUrl(url) {
  if (typeof url !== 'string' || url.length > 2_000_000) return null;
  return url.startsWith('data:') ? (safeDataImage(url) ? url : null) : (safeLocalPath(url) ? url : null);
}

// Color de marca: solo #RRGGBB; cualquier otra cosa se descarta.
const HEX = /^#[0-9A-Fa-f]{6}$/;
export function brandColor(v) { return typeof v === 'string' && HEX.test(v) ? v : null; }

// Colores de marca del tenant activo. Si la empresa no trae colores propios válidos
// (#RRGGBB) se quitan los del tenant anterior y vuelven los valores por defecto de la hoja de estilos.
export function applyBrandColors(style, profile) {
  const c = (profile && profile.colors) || {};
  for (const [prop, value] of [['--brand-primary', c.primary], ['--brand-accent', c.accent]]) {
    const ok = brandColor(value);       // se valida aquí también: nunca se aplica un valor no #RRGGBB
    if (ok) style.setProperty(prop, ok); else style.removeProperty(prop);
  }
}

// Datos variables de la empresa activa, desde la fila de tenants que ya existe.
export function tenantProfile(tenant) {
  const t = tenant || {};
  const b = t.branding || {};
  const locale = String(b.locale || '');
  const lang = b.language || (locale ? locale.slice(0, 2).toLowerCase() : null);
  const logo = safeLogoUrl(b.logo_url);
  const addr = t.address;
  const address = addr && typeof addr === 'object'
    ? [addr.line1 || addr.street, addr.city, addr.state, addr.zip || addr.postal_code].filter(Boolean).join(', ')
    : (addr || null);
  const name = b.business_name || t.name || FALLBACK_NAME;
  return {
    name,
    initials: name.split(/\s+/).filter(w => /^[A-Za-zÀ-ÿ]/.test(w)).slice(0, 2).map(w => w[0].toUpperCase()).join('') || 'N',
    logo,
    colors: { primary: brandColor(b.color_primary), accent: brandColor(b.color_accent) },
    phone: t.public_phone || b.phone || null,
    address,
    timezone: t.timezone || 'America/Chicago',
    language: lang === 'es' || lang === 'en' ? lang : null,
    locale: b.locale || null,
    currency: b.currency || 'USD',
    modules: CATALOG.filter(m => m.menu !== false && tenantEnabled(t, m.key)).map(m => m.key),
  };
}
