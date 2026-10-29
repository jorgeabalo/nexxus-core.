// Reglas de cada red para las versiones por plataforma (sin DOM, se prueba en Node). Misma tabla que
// services/marketing_platforms.py: si se cambia allí, cambiar aquí. El backend guarda las versiones
// (platform_variants); aquí solo se calculan para la vista previa cuando aún no hay ninguna guardada.
export const RULES = {
  instagram: { text_max: 2200, hashtags: 5 }, facebook: { text_max: 2000, hashtags: 3 },
  tiktok: { text_max: 2200, hashtags: 5 }, linkedin: { text_max: 3000, hashtags: 3 },
  x: { text_max: 280, hashtags: 2 }, youtube: { text_max: 5000, hashtags: 3 },
  google_business: { text_max: 1500, hashtags: 0 }, threads: { text_max: 500, hashtags: 1 },
};
const ALL = Object.keys(RULES);
const every = (v) => Object.fromEntries(ALL.map(c => [c, v]));
export const ASPECT = {
  image: { instagram: '4:5', facebook: '4:5', tiktok: '9:16', linkedin: '1:1', x: '16:9', youtube: '16:9', google_business: '4:3', threads: '4:5' },
  carousel: { instagram: '4:5', facebook: '1:1', tiktok: '9:16', linkedin: '1:1', x: '1:1', youtube: '16:9', google_business: '4:3', threads: '4:5' },
  reel: every('9:16'), story: every('9:16'),
  video: { instagram: '9:16', facebook: '9:16', tiktok: '9:16', linkedin: '1:1', x: '16:9', youtube: '16:9', google_business: '16:9', threads: '9:16' },
  text: every(''),
};

function cut(text, limit) {
  if (text.length <= limit) return text;
  const head = text.slice(0, Math.max(limit - 1, 0));
  const stop = Math.max(head.lastIndexOf('. '), head.lastIndexOf('! '), head.lastIndexOf('? '), head.lastIndexOf('\n'));
  if (stop >= Math.floor(limit / 2)) return head.slice(0, stop + 1).trimEnd();
  const space = head.lastIndexOf(' ');
  return (space > 0 ? head.slice(0, space) : head).replace(/[ ,;:]+$/, '') + '…';
}

export function compose(caption, cta, hashtags, channel) {
  const rule = RULES[channel];
  const tags = (hashtags || []).slice(0, rule.hashtags).join(' ');
  let tail = [String(cta || '').trim(), tags].filter(Boolean).join('\n\n');
  const body = String(caption || '').trim();
  if (!tail) return cut(body, rule.text_max);
  let room = rule.text_max - tail.length - 2;
  if (room < 40) { tail = String(cta || '').trim(); room = rule.text_max - tail.length - 2; }
  return [cut(body, Math.max(room, 0)), tail].filter(Boolean).join('\n\n');
}

export function variantsFor(c) {
  const saved = c.platform_variants || {};
  return (c.channels || []).filter(ch => RULES[ch]).map(ch => ({
    channel: ch,
    text: saved[ch]?.text ?? compose(c.caption, c.cta, c.hashtags, ch),
    aspect: saved[ch]?.aspect ?? (ASPECT[c.format] || {})[ch] ?? '',
    limit: RULES[ch].text_max,
  }));
}
