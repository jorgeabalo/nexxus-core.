// Textos de la guía de primeros pasos, «Escribir con IA» y las versiones por plataforma (español / inglés).
import { getLang } from '../i18n.js';

const T = {
  es: {
    g_title: 'Primeros pasos', g_intro: 'Sigue estos pasos y AITA te ayuda con el resto.',
    g_progress: (n, total) => `${n} de ${total} listos`, g_hide: 'Ocultar guía', g_done: '¡Todo listo! Ya puedes crear y planear tus publicaciones.',
    g_brand: 'Completa tu marca', g_brand_d: 'Nombre, tono, servicios y lo que nunca se debe decir.',
    g_social: 'Pon tus redes', g_social_d: 'Las direcciones de tus perfiles (Instagram, Facebook, TikTok…).',
    g_library: 'Sube tus fotos y vídeos', g_library_d: 'Material real de tu negocio para tus publicaciones.',
    g_first: 'Crea tu primera publicación', g_first_d: 'Elige el objetivo y AITA escribe el texto para cada red.',
    g_go: 'Empezar', g_ok: 'Hecho',
    ai_write: '✨ Escribir con IA', ai_rewrite: '✨ Reescribir con IA', ai_writing: 'Escribiendo…',
    ai_request: '¿Quieres algún cambio? (opcional)', ai_request_ph: 'p. ej. más corto, más cercano, menciona la clase de los martes',
    ai_done: 'Texto listo. Revísalo y cámbialo si quieres.',
    ai_off: 'La escritura con IA aún no está activada. Puedes escribir el texto tú y AITA lo adapta a cada red.',
    v_title: 'Así se verá en cada red', v_chars: (n, max) => `${n} / ${max} caracteres`, v_copy: 'Copiar', v_copied: 'Copiado',
    v_visual: 'Visual', v_none: 'Elige al menos una red para ver las versiones.',
    a_vertical: 'Vertical', a_square: 'Cuadrado', a_portrait: 'Retrato', a_landscape: 'Horizontal',
    send_review: 'Enviar a revisión',
  },
  en: {
    g_title: 'Getting started', g_intro: 'Follow these steps and AITA helps with the rest.',
    g_progress: (n, total) => `${n} of ${total} done`, g_hide: 'Hide guide', g_done: 'All set! You can now create and plan your posts.',
    g_brand: 'Complete your brand', g_brand_d: 'Name, tone, services and what should never be said.',
    g_social: 'Add your social media', g_social_d: 'Your profile addresses (Instagram, Facebook, TikTok…).',
    g_library: 'Upload your photos and videos', g_library_d: 'Real material from your business for your posts.',
    g_first: 'Create your first post', g_first_d: 'Pick the goal and AITA writes the text for each network.',
    g_go: 'Start', g_ok: 'Done',
    ai_write: '✨ Write with AI', ai_rewrite: '✨ Rewrite with AI', ai_writing: 'Writing…',
    ai_request: 'Any changes? (optional)', ai_request_ph: 'e.g. shorter, friendlier, mention the Tuesday class',
    ai_done: 'Text ready. Review it and change it if you like.',
    ai_off: 'AI writing is not turned on yet. You can write the text yourself and AITA adapts it to each network.',
    v_title: 'How it will look on each network', v_chars: (n, max) => `${n} / ${max} characters`, v_copy: 'Copy', v_copied: 'Copied',
    v_visual: 'Visual', v_none: 'Pick at least one network to see the versions.',
    a_vertical: 'Vertical', a_square: 'Square', a_portrait: 'Portrait', a_landscape: 'Landscape',
    send_review: 'Send for review',
  },
};

export function g(key, ...args) { const v = T[getLang()][key] ?? T.en[key] ?? key; return typeof v === 'function' ? v(...args) : v; }
