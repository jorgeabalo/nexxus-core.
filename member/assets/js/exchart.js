// Mini gráfica por máquina: lo que el socio hizo en sus últimas sesiones.
// La usan el portal del socio y el Manager Panel. Sin dependencias: SVG
// construido con el DOM (nada de innerHTML) y colores vía clases CSS de cada panel.
const NS = 'http://www.w3.org/2000/svg';
const svg = (tag, attrs = {}, text) => {
  const n = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  if (text !== undefined) n.textContent = text;
  return n;
};

// Misma máquina = mismo exercise_key y mismo nombre (sin mayúsculas/espacios).
export const machineKey = (x) => `${x.exercise_key}|${String(x.name || '').trim().toLowerCase()}`;

// Volumen de una sesión: minutos en cardio; si no, repeticiones totales (series x reps).
export const volume = (l) => (l.duration_min ? Number(l.duration_min) : Number(l.sets || 0) * Number(l.reps || 0));

// Registros de una máquina, del más antiguo al más reciente.
export function historyFor(logs, item) {
  const k = machineKey(item);
  return (logs || []).filter(l => machineKey(l) === k).sort((a, b) => String(a.date).localeCompare(String(b.date)));
}

// points: [{ date: 'YYYY-MM-DD', value, title }]; muestra las últimas `max` barras.
export function miniChart(points, { max = 8, label = '', locale = 'es-US' } = {}) {
  const pts = points.slice(-max);
  const W = 200, H = 84, top = 14, base = 66, slot = W / max, bw = Math.min(16, slot * 0.6);
  const peak = Math.max(1, ...pts.map(p => p.value));
  const day = (iso) => {
    const [y, m, d] = String(iso).slice(0, 10).split('-').map(Number);
    return new Intl.DateTimeFormat(locale, { day: 'numeric', month: 'numeric', timeZone: 'UTC' }).format(new Date(Date.UTC(y, m - 1, d)));
  };
  const root = svg('svg', { viewBox: `0 0 ${W} ${H}`, class: 'exc', role: 'img', 'aria-label': label });
  root.appendChild(svg('line', { x1: 0, x2: W, y1: base + 0.5, y2: base + 0.5, class: 'exc-axis' }));
  const x0 = W - pts.length * slot;   // alineadas a la derecha: lo más reciente siempre al final
  pts.forEach((p, i) => {
    const h = Math.max(2, Math.round((p.value / peak) * (base - top)));
    const cx = x0 + i * slot + slot / 2;
    const g = svg('g', { class: i === pts.length - 1 ? 'exc-last' : '' });
    g.appendChild(svg('title', {}, p.title || `${day(p.date)}: ${p.value}`));
    g.appendChild(svg('rect', { x: cx - bw / 2, y: base - h, width: bw, height: h, rx: 2, class: 'exc-bar' }));
    g.appendChild(svg('text', { x: cx, y: base - h - 3, 'text-anchor': 'middle', class: 'exc-val' }, String(p.value)));
    g.appendChild(svg('text', { x: cx, y: H - 4, 'text-anchor': 'middle', class: 'exc-lbl' }, day(p.date)));
    root.appendChild(g);
  });
  return root;
}
