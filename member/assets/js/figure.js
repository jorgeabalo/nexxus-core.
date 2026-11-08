// Figura corporal ilustrativa (vista frontal, anatómica) para "Mi progreso".
// Tres siluetas según members.gender: hombre, mujer o neutra (nunca se infiere
// del nombre). Solo es una referencia visual: las medidas reales van en las
// etiquetas, que salen de los registros del socio.
//
// Dibujo: contorno anatómico (cabeza, cuello, tronco, brazos con manos, piernas
// con pies) + volumen con luz desde arriba a la izquierda + definición muscular
// suave. Todo es SVG generado aquí (sin imágenes externas ni estilos inline).
import { t, svgEl } from './util.js';

// Medidas de la mitad derecha (distancia horizontal al eje del cuerpo).
const SHAPES = {
  male:    { neck: 11, trap: 47, sh: 64, chest: 51, wa: 40, hi: 47, thigh: 46, knee: 34, calf: 39, ankle: 28, bust: 0, tone: 1 },
  female:  { neck: 9, trap: 40, sh: 54, chest: 46, wa: 34, hi: 51, thigh: 46, knee: 33, calf: 37, ankle: 26, bust: 1, tone: 0 },
  neutral: { neck: 10, trap: 44, sh: 59, chest: 48, wa: 37, hi: 49, thigh: 46, knee: 34, calf: 38, ankle: 27, bust: 0, tone: 0 },
};
const CX = 180;
const ARM_TILT = 0.07;   // separación del brazo respecto al tronco, de hombro a mano
let uid = 0;             // ids únicos por figura (puede haber varias en la misma página)
const f = (n) => Math.round(n * 10) / 10;
const PAIR = /(-?\d+(?:\.\d+)?) (-?\d+(?:\.\d+)?)/g;
const mirrorPath = (d) => d.replace(PAIR, (_, x, y) => `${f(2 * CX - Number(x))} ${y}`);
const tilt = (d) => d.replace(PAIR, (_, x, y) => `${f(Number(x) + (Number(y) - 92) * ARM_TILT)} ${y}`);
const both = (d) => `${d} ${mirrorPath(d)}`;

// Contorno simétrico a partir de la mitad derecha (segmentos cúbicos).
function symmetric(start, segs) {
  const pt = ([x, y]) => `${f(x)} ${f(y)}`;
  const mpt = ([x, y]) => `${f(2 * CX - x)} ${f(y)}`;
  const out = [`M ${pt(start)}`];
  for (const [c1, c2, e] of segs) out.push(`C ${pt(c1)}, ${pt(c2)}, ${pt(e)}`);
  const pts = [start, ...segs.map(s => s[2])];
  for (let i = segs.length - 1; i >= 0; i--) out.push(`C ${mpt(segs[i][1])}, ${mpt(segs[i][0])}, ${mpt(pts[i])}`);
  out.push('Z');
  return out.join(' ');
}

// Cabeza: cráneo, oreja, mandíbula y barbilla.
function headPath(p) {
  const c = CX, w = p.bust ? 19 : 21;
  return symmetric([c, 12], [
    [[c + w * 0.7, 12], [c + w, 22], [c + w, 38]],               // cráneo
    [[c + w + 3, 37], [c + w + 4, 48], [c + w, 52]],             // oreja
    [[c + w - 1, 61], [c + w * 0.62, 69], [c + w * 0.36, 72]],   // mandíbula
    [[c + 4, 74], [c + 1, 74], [c, 74]],                          // barbilla
  ]);
}

// Tronco y piernas (mitad derecha, del cuello a la entrepierna).
function bodyPath(p) {
  const c = CX;
  return symmetric([c + p.neck - 1, 62], [
    [[c + p.neck, 70], [c + p.neck + 1, 78], [c + p.neck + 4, 84]],                 // cuello
    [[c + p.neck + 14, 88], [c + p.trap - 10, 89], [c + p.trap, 93]],               // trapecio
    [[c + p.sh - 4, 95], [c + p.sh + 2, 105], [c + p.sh, 118]],                     // hombro
    [[c + p.sh - 2, 128], [c + p.chest + 4, 134], [c + p.chest, 142]],              // axila
    [[c + p.chest + p.bust * 6, 160], [c + p.wa + 4, 192], [c + p.wa, 214]],        // costado → cintura
    [[c + p.wa - 1, 232], [c + p.hi, 244], [c + p.hi, 264]],                        // cadera
    [[c + p.thigh, 292], [c + p.knee + 8, 328], [c + p.knee, 352]],                 // muslo → rodilla
    [[c + p.knee + 2, 366], [c + p.calf + 2, 384], [c + p.ankle + 4, 410]],         // pantorrilla
    [[c + p.ankle + 1, 420], [c + p.ankle, 426], [c + p.ankle + 2, 431]],           // tobillo
    [[c + p.ankle + 9, 435], [c + p.ankle + 14, 441], [c + p.ankle + 11, 446]],     // empeine
    [[c + p.ankle + 6, 450], [c + 16, 450], [c + 12, 446]],                          // dedos
    [[c + 10, 440], [c + 13, 432], [c + 13, 422]],                                  // tobillo interior
    [[c + 12, 404], [c + 9, 380], [c + 11, 356]],                                   // pantorrilla interior
    [[c + 12, 330], [c + 8, 300], [c + 2, 282]],                                    // muslo interior
    [[c + 1, 281], [c + 0.5, 280], [c, 280]],                                       // entrepierna
  ]);
}

// Brazo derecho con mano (pulgar hacia el cuerpo).
function armPath(p) {
  const d = p.sh - 64, w = p.bust ? -2 : 0, x = (v) => CX + v + d;
  return tilt([
    `M ${x(56)} 92`,
    `C ${x(70)} 96, ${x(74 + w)} 112, ${x(72 + w)} 128`,                 // deltoides
    `C ${x(74 + w)} 150, ${x(76 + w)} 170, ${x(74 + w)} 190`,            // bíceps → codo
    `C ${x(76 + w)} 210, ${x(74 + w)} 232, ${x(70 + w)} 250`,            // antebrazo → muñeca
    `C ${x(73)} 258, ${x(75)} 272, ${x(72)} 284`,                         // canto de la mano
    `C ${x(70)} 292, ${x(64)} 295, ${x(61)} 291`,                         // yemas
    `C ${x(58)} 287, ${x(58)} 280, ${x(58)} 276`,
    `C ${x(55)} 274, ${x(53)} 268, ${x(55)} 262`,                         // pulgar
    `C ${x(56)} 258, ${x(57)} 254, ${x(57)} 250`,
    `C ${x(58 - w)} 236, ${x(56 - w)} 212, ${x(58 - w)} 192`,            // antebrazo interior
    `C ${x(58 - w)} 170, ${x(56 - w)} 150, ${x(52)} 140`,                // brazo interior → axila
    `C ${x(50)} 120, ${x(50)} 98, ${x(56)} 92`, 'Z',
  ].join(' '));
}

// Definición muscular (sombras suaves) y líneas anatómicas.
function shading(p) {
  const c = CX, d = p.sh - 64, ax = (v) => c + v + d;
  const shade = [
    tilt(`M ${ax(58)} 96 C ${ax(70)} 100, ${ax(72)} 116, ${ax(68)} 130 C ${ax(64)} 122, ${ax(60)} 110, ${ax(58)} 96 Z`),   // deltoides
    tilt(`M ${ax(70)} 196 C ${ax(74)} 214, ${ax(72)} 232, ${ax(68)} 246 C ${ax(66)} 230, ${ax(66)} 212, ${ax(70)} 196 Z`),  // antebrazo
    `M ${c + p.chest - 4} 150 C ${c + p.wa + 4} 180, ${c + p.wa + 2} 200, ${c + p.wa} 212 C ${c + p.wa - 8} 196, ${c + p.wa - 6} 172, ${c + p.chest - 4} 150 Z`,   // costado
    `M ${c + 30} 290 C ${c + 40} 306, ${c + 38} 330, ${c + 30} 346 C ${c + 24} 330, ${c + 24} 306, ${c + 30} 290 Z`,          // cuádriceps
    `M ${c + p.calf - 4} 372 C ${c + p.calf + 1} 384, ${c + p.calf - 2} 398, ${c + p.ankle + 4} 408 C ${c + p.calf - 10} 396, ${c + p.calf - 10} 382, ${c + p.calf - 4} 372 Z`,  // pantorrilla
  ];
  if (p.bust) {
    shade.push(`M ${c + 8} 148 C ${c + 16} 166, ${c + 36} 168, ${c + p.chest - 4} 146 C ${c + 34} 158, ${c + 18} 160, ${c + 8} 148 Z`);  // pecho
  } else {
    shade.push(`M ${c + 4} 146 C ${c + 18} 156, ${c + 36} 154, ${c + p.chest - 4} 136 C ${c + 36} 146, ${c + 20} 150, ${c + 4} 146 Z`);  // pectoral
  }
  if (p.tone) {   // abdomen: sombra suave a los lados del recto abdominal (sin "cuadros")
    shade.push(`M ${c + 3} 156 C ${c + 15} 162, ${c + 17} 194, ${c + 6} 208 C ${c + 11} 192, ${c + 11} 168, ${c + 3} 156 Z`);
  }
  const lines = [
    `M ${c + 6} 99 Q ${c + 22} 94 ${c + p.trap} 98`,                               // clavícula
    p.bust ? `M ${c + 6} 142 Q ${c + 24} 168 ${c + p.chest - 6} 143`
      : `M ${c + 4} 147 Q ${c + 24} 157 ${c + p.chest - 6} 138`,                    // pecho
    `M ${c + 22} 344 Q ${c + 25} 352 ${c + 21} 360`,                               // rótula
    tilt(`M ${CX + 64 + d} 280 L ${CX + 64 + d} 289 M ${CX + 67 + d} 279 L ${CX + 67 + d} 288`),   // dedos
  ];
  return {
    shade: both(shade.join(' ')),
    lines: both(lines.join(' ')) + ` M ${c} 152 L ${c} ${p.tone ? 208 : 204}` + ` M ${c - 1.5} 216 a 1.5 1.5 0 1 0 3 0 a 1.5 1.5 0 1 0 -3 0`,
  };
}

export function figureSvg(sex, labels = {}, { compact = false } = {}) {
  const key = SHAPES[sex] ? sex : 'neutral';
  const p = SHAPES[key];
  const W = 360, H = compact ? 456 : 462;
  const id = `fig${++uid}`;
  const arm = armPath(p);
  const silhouette = [bodyPath(p), arm, mirrorPath(arm), headPath(p)];
  const s = shading(p);

  const svg = svgEl('svg', { viewBox: `0 0 ${W} ${H}`, class: 'figure-svg', role: 'img', 'aria-label': t(`fig.aria.${key}`) });
  const defs = svgEl('defs', {},
    svgEl('linearGradient', { id: `${id}-skin`, x1: '0', y1: '0', x2: '1', y2: '0.35' },
      svgEl('stop', { offset: '0', class: 'fig-skin-a' }), svgEl('stop', { offset: '0.55', class: 'fig-skin-b' }),
      svgEl('stop', { offset: '1', class: 'fig-skin-c' })),
    svgEl('radialGradient', { id: `${id}-light`, cx: '0.38', cy: '0.22', r: '0.75' },
      svgEl('stop', { offset: '0', class: 'fig-light-a' }), svgEl('stop', { offset: '1', class: 'fig-light-b' })),
    svgEl('clipPath', { id: `${id}-clip` }, ...silhouette.map(d => svgEl('path', { d }))));

  const body = svgEl('g', { class: 'fig-body' },
    svgEl('ellipse', { cx: CX, cy: 452, rx: 70, ry: 6, class: 'fig-ground' }),          // sombra en el suelo
    // Contorno de la silueta completa: trazo grueso debajo y relleno encima, así
    // no se ven costuras donde se unen cabeza, cuello y brazos.
    ...silhouette.map(d => svgEl('path', { d, class: 'fig-outline' })),
    ...silhouette.map(d => svgEl('path', { d, class: 'fig-skin', fill: `url(#${id}-skin)` })),
    svgEl('g', { 'clip-path': `url(#${id}-clip)` },
      svgEl('rect', { x: 0, y: 0, width: W, height: H, fill: `url(#${id}-light)`, class: 'fig-light' }),
      svgEl('path', { d: s.shade, class: 'fig-shade' }),
      svgEl('path', { d: s.lines, class: 'fig-detail' })));
  svg.append(defs, body);

  // Indicadores: brazo (izq.), cintura (der.), muslo (izq. abajo). Sin solapes.
  const d = p.sh - 64;
  const anchors = {
    arm: { x: CX - 74 - d - (156 - 92) * ARM_TILT, y: 156, side: 'left', ly: 132 },
    waist: { x: CX + p.wa, y: 214, side: 'right', ly: 196 },
    leg: { x: CX - p.thigh + 2, y: 304, side: 'left', ly: 290 },
  };
  for (const [k, a] of Object.entries(anchors)) {
    const lab = labels[k];
    if (!lab) continue;
    const tx = a.side === 'left' ? 8 : W - 8;
    const lx = a.side === 'left' ? 96 : W - 96;
    const anchor = a.side === 'left' ? 'start' : 'end';
    svg.append(
      svgEl('path', { d: `M ${f(a.x)} ${a.y} L ${lx} ${a.ly + 14} L ${a.side === 'left' ? lx - 4 : lx + 4} ${a.ly + 14}`, class: 'fig-leader' }),
      svgEl('circle', { cx: f(a.x), cy: a.y, r: 5, class: 'fig-dot' }),
      svgEl('text', { x: tx, y: a.ly, class: 'fig-lab-title', 'text-anchor': anchor }, lab.title),
      svgEl('text', { x: tx, y: a.ly + 17, class: `fig-lab-val ${lab.value ? '' : 'is-pending'}`, 'text-anchor': anchor }, lab.value || t('prog.pendingShort')),
      lab.delta ? svgEl('text', { x: tx, y: a.ly + 33, class: 'fig-lab-delta', 'text-anchor': anchor }, lab.delta) : null,
    );
  }
  return svg;
}
