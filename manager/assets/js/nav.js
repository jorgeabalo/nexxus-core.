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

// Módulos por rol si el tenant no define otros en tenants.settings.role_modules.
// El owner ve siempre todos los módulos habilitados de su empresa.
export const ROLE_DEFAULTS = {
  manager: ['dashboard', 'members', 'attendance', 'schedule', 'payments', 'accounting', 'team',
    'inventory', 'marketing', 'claudia', 'agents'],
  staff: ['dashboard', 'members', 'attendance', 'schedule', 'payments', 'accounting', 'claudia'],
};

export const PILOT_NAME = 'Golden Age Fitness';   // solo si el tenant no trae nombre

// Habilitado para la empresa: tenants.modules[key] distinto de false
// (mismo criterio que antes: un módulo nuevo sin clave cuenta como habilitado).
export function tenantEnabled(tenant, key) {
  return ((tenant && tenant.modules) || {})[key] !== false;
}

export function roleAllows(role, key, tenant) {
  if (role === 'owner') return true;
  const custom = tenant && tenant.role_modules && tenant.role_modules[role];
  const list = Array.isArray(custom) ? custom : ROLE_DEFAULTS[role];
  return Array.isArray(list) && list.includes(key);
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

// Logo permitido: ruta local con una sola barra (/media/logo.png; nunca //host ni /\\host,
// que el navegador trata como otro dominio) o imagen rasterizada en base64 (PNG, JPEG,
// WebP, GIF). Nada de SVG ni URLs externas.
const LOCAL_PATH = /^\/(?![\/\\])[^\s\\]*$/;
const DATA_IMAGE = /^data:image\/(png|jpeg|webp|gif);base64,[A-Za-z0-9+/]+={0,2}$/;
export function safeLogoUrl(url) {
  if (typeof url !== 'string' || url.length > 2_000_000) return null;
  return LOCAL_PATH.test(url) || DATA_IMAGE.test(url) ? url : null;
}

// Colores de marca del tenant activo. Si la empresa no trae colores propios se
// quitan los del tenant anterior y vuelven los valores por defecto de la hoja de estilos.
export function applyBrandColors(style, profile) {
  const c = (profile && profile.colors) || {};
  for (const [prop, value] of [['--brand-primary', c.primary], ['--brand-accent', c.accent]]) {
    if (value) style.setProperty(prop, value); else style.removeProperty(prop);
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
  const name = b.business_name || t.name || PILOT_NAME;
  return {
    name,
    initials: name.split(/\s+/).filter(w => /^[A-Za-zÀ-ÿ]/.test(w)).slice(0, 2).map(w => w[0].toUpperCase()).join('') || 'N',
    logo,
    colors: { primary: b.color_primary || null, accent: b.color_accent || null },
    phone: t.public_phone || b.phone || null,
    address,
    timezone: t.timezone || 'America/Chicago',
    language: lang === 'es' || lang === 'en' ? lang : null,
    locale: b.locale || null,
    currency: b.currency || 'USD',
    modules: CATALOG.filter(m => m.menu !== false && tenantEnabled(t, m.key)).map(m => m.key),
  };
}
