// Idioma del Manager Panel (español / inglés), compartido por el encabezado,
// el menú y los módulos bilingües.
//
// Regla (sin ambigüedad):
//  * aita.lang es una PREFERENCIA GLOBAL DEL USUARIO en este navegador: si la
//    persona eligió un idioma, se mantiene al cambiar de empresa.
//  * Sin preferencia guardada, manda el idioma de la empresa activa
//    (branding.language / branding.locale); si esa empresa no define idioma, el
//    del navegador. Nunca se arrastra el idioma por defecto de la empresa anterior.
// Cambiar el idioma emite el evento "aita:lang" para que el panel se redibuje.
const KEY = 'aita.lang';
const OK = new Set(['es', 'en']);

function stored() {
  try { const s = localStorage.getItem(KEY); return OK.has(s) ? s : null; } catch (_) { return null; }
}
function browserLang() {
  const n = (typeof navigator !== 'undefined' && navigator.language) || 'en';
  return n.toLowerCase().startsWith('es') ? 'es' : 'en';
}
let lang = stored() || browserLang();

export const getLang = () => lang;

// Se llama en cada cambio de empresa (setTenant).
export function useTenantDefault(tenantLang) {
  if (stored()) return;                                   // preferencia global del usuario
  lang = OK.has(tenantLang) ? tenantLang : browserLang(); // empresa activa, o navegador
}

export function setLang(l) {
  if (!OK.has(l) || l === lang) return;
  lang = l;
  try { localStorage.setItem(KEY, l); } catch (_) { /* opcional */ }
  window.dispatchEvent(new CustomEvent('aita:lang', { detail: l }));
}

const T = {
  es: {
    platform: 'Nexxus Manager', signOut: 'Cerrar sesión', website: 'Web', websiteTitle: 'Volver a la web',
    openMenu: 'Abrir menú', language: 'Idioma', business: 'Empresa', mainNav: 'Menú principal', soon: 'Pronto',
    status_ok: 'Sistema en línea', status_degraded: 'Conexión limitada', status_offline: 'Sin conexión', status_checking: 'Comprobando…',
    role_owner: 'Propietario', role_manager: 'Gerente', role_staff: 'Personal',
    noAccessTitle: 'Sin acceso a este módulo', noAccessText: 'Este módulo no está habilitado para tu empresa o para tu rol. Pide acceso al propietario.',
    backHome: 'Ir al Resumen', comingSoon: 'Próximamente', profile: 'Perfil de la empresa',
    phone: 'Teléfono', address: 'Dirección', timezone: 'Zona horaria', languageLabel: 'Idioma', modulesOn: 'Módulos habilitados',
    soon_attendance: 'Registro de entradas, asistencia por socio y alertas de inactividad.',
    soon_inventory: 'Productos, suplementos y equipos con existencias y alertas.',
    soon_marketing: 'Campañas, recordatorios y reactivación de socios.',
    soon_agents: 'Agentes de IA adicionales que trabajan junto a Claudia.',
    soon_settings: 'Perfil de la empresa, horarios, equipo y marca.',
    m_dashboard: 'Resumen', m_members: 'Miembros', m_attendance: 'Asistencia', m_schedule: 'Citas y servicios',
    m_payments: 'Pagos', m_accounting: 'Contabilidad', m_team: 'Personal', m_inventory: 'Inventario',
    m_marketing: 'Marketing', m_claudia: 'Claudia IA', m_settings: 'Configuración', m_agents: 'Agentes',
  },
  en: {
    platform: 'Nexxus Manager', signOut: 'Sign out', website: 'Website', websiteTitle: 'Back to website',
    openMenu: 'Open menu', language: 'Language', business: 'Business', mainNav: 'Main menu', soon: 'Soon',
    status_ok: 'System online', status_degraded: 'Limited connection', status_offline: 'Offline', status_checking: 'Checking…',
    role_owner: 'Owner', role_manager: 'Manager', role_staff: 'Staff',
    noAccessTitle: 'No access to this module', noAccessText: 'This module is not enabled for your business or your role. Ask the owner for access.',
    backHome: 'Go to Overview', comingSoon: 'Coming soon', profile: 'Business profile',
    phone: 'Phone', address: 'Address', timezone: 'Time zone', languageLabel: 'Language', modulesOn: 'Enabled modules',
    soon_attendance: 'Check-ins, attendance per member and inactivity alerts.',
    soon_inventory: 'Products, supplements and equipment with stock alerts.',
    soon_marketing: 'Campaigns, reminders and member re-engagement.',
    soon_agents: 'Additional AI agents that work alongside Claudia.',
    soon_settings: 'Business profile, hours, team and branding.',
    m_dashboard: 'Overview', m_members: 'Members', m_attendance: 'Attendance', m_schedule: 'Appointments & services',
    m_payments: 'Payments', m_accounting: 'Accounting', m_team: 'Staff', m_inventory: 'Inventory',
    m_marketing: 'Marketing', m_claudia: 'Claudia AI', m_settings: 'Settings', m_agents: 'Agents',
  },
};

export function tr(key) { return T[lang][key] ?? T.en[key] ?? key; }
