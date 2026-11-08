// Dibujo de las láminas del carrusel (canvas 1080 × 1350) y ZIP sin dependencias.
// Todo ocurre en el navegador: las imágenes llegan por el backend (sesión de lectura de la Biblioteca, mismo
// origen), así el canvas no queda «contaminado» y se puede exportar a PNG. Nada sale a otros servicios.
export const W = 1080, H = 1350, PAD = 96;
const FONT = 'Inter, system-ui, -apple-system, "Segoe UI", sans-serif';
const NAVY = '#102A43', WHITE = '#FFFFFF', TEAL = '#0E6E7A';

// ---------- color y contraste (WCAG) ----------
const rgb = (hex) => { const n = parseInt(String(hex).slice(1), 16); return [(n >> 16) & 255, (n >> 8) & 255, n & 255]; };
const lum = (hex) => {
  const [r, g, b] = rgb(hex).map(v => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
};
export const contrast = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); };
const isHex = (v) => typeof v === 'string' && /^#[0-9A-Fa-f]{6}$/.test(v);

export function palette(theme, brand = {}) {
  const primary = isHex(brand.color_primary) ? brand.color_primary : TEAL;
  const accent = isHex(brand.color_accent) ? brand.color_accent : primary;
  if (theme === 'dark') {
    const acc = [accent, primary, '#7FD1D9'].find(c => contrast(c, NAVY) >= 3);
    return { bg: NAVY, fg: WHITE, muted: '#B8C4D6', accent: acc };
  }
  if (theme === 'brand') {
    const fg = contrast(WHITE, primary) >= 4.5 ? WHITE : NAVY;
    return { bg: primary, fg, muted: fg, accent: fg };
  }
  const acc = [primary, accent, TEAL].find(c => contrast(c, WHITE) >= 3);
  return { bg: WHITE, fg: NAVY, muted: '#5B6B82', accent: acc };
}

// ---------- texto ----------
function wrap(g, text, width) {
  const out = [];
  for (const para of String(text || '').split('\n')) {
    let line = '';
    for (let word of para.split(/\s+/).filter(Boolean)) {
      while (g.measureText(word).width > width) {            // palabra más ancha que la caja: se parte
        let cut = word.length - 1;
        while (cut > 1 && g.measureText(`${word.slice(0, cut)}-`).width > width) cut--;
        if (line) { out.push(line); line = ''; }
        out.push(`${word.slice(0, cut)}-`); word = word.slice(cut);
      }
      const next = line ? `${line} ${word}` : word;
      if (g.measureText(next).width <= width) line = next; else { if (line) out.push(line); line = word; }
    }
    out.push(line);
  }
  while (out.length && !out[out.length - 1]) out.pop();
  return out;
}

// Ajusta título y texto a la caja: reduce el tamaño hasta el mínimo; si aun así no cabe, corta con «…» y avisa.
function layout(g, title, body, box, spec) {
  for (let s = 1; s >= 0; s -= 0.05) {
    const ts = Math.round(spec.title[1] + (spec.title[0] - spec.title[1]) * s);
    const bs = Math.round(spec.body[1] + (spec.body[0] - spec.body[1]) * s);
    g.font = `700 ${ts}px ${FONT}`; const tl = title ? wrap(g, title, box.w) : [];
    g.font = `400 ${bs}px ${FONT}`; const bl = body ? wrap(g, body, box.w) : [];
    const h = tl.length * ts * 1.12 + (tl.length && bl.length ? bs * 0.9 : 0) + bl.length * bs * 1.38;
    if (h <= box.h) return { ts, bs, tl, bl, h, overflow: false };
  }
  const [ts, bs] = [spec.title[1], spec.body[1]];
  g.font = `700 ${ts}px ${FONT}`; let tl = title ? wrap(g, title, box.w) : [];
  const maxT = Math.max(1, Math.floor(box.h * 0.45 / (ts * 1.12)));
  if (tl.length > maxT) tl = [...tl.slice(0, maxT - 1), `${tl[maxT - 1]}…`];
  g.font = `400 ${bs}px ${FONT}`; let bl = body ? wrap(g, body, box.w) : [];
  const room = box.h - tl.length * ts * 1.12 - (tl.length ? bs * 0.9 : 0);
  const maxB = Math.max(0, Math.floor(room / (bs * 1.38)));
  if (bl.length > maxB) bl = maxB ? [...bl.slice(0, maxB - 1), `${bl[maxB - 1]}…`] : [];
  return { ts, bs, tl, bl, h: box.h, overflow: true };
}

function drawText(g, L, x, y, color, bodyColor) {
  g.textBaseline = 'top'; g.fillStyle = color; g.font = `700 ${L.ts}px ${FONT}`;
  L.tl.forEach((ln, i) => g.fillText(ln, x, y + i * L.ts * 1.12));
  let by = y + L.tl.length * L.ts * 1.12 + (L.tl.length && L.bl.length ? L.bs * 0.9 : 0);
  g.fillStyle = bodyColor; g.font = `400 ${L.bs}px ${FONT}`;
  L.bl.forEach((ln, i) => g.fillText(ln, x, by + i * L.bs * 1.38));
}

function rounded(g, x, y, w, h, r) {
  g.beginPath(); g.moveTo(x + r, y); g.arcTo(x + w, y, x + w, y + h, r); g.arcTo(x + w, y + h, x, y + h, r);
  g.arcTo(x, y + h, x, y, r); g.arcTo(x, y, x + w, y, r); g.closePath();
}
// Recorte tipo «cover»: llena la caja sin deformar la imagen.
function cover(g, img, x, y, w, h) {
  const s = Math.max(w / img.width, h / img.height), sw = w / s, sh = h / s;
  g.drawImage(img, (img.width - sw) / 2, (img.height - sh) / 2, sw, sh, x, y, w, h);
}

/**
 * Dibuja una lámina. `scale` < 1 para miniaturas. Devuelve { overflow } (texto cortado).
 * opts: { slide, index, total, theme, brand, image, medical, cites: [números de referencia] }
 */
export function drawSlide(canvas, opts, scale = 1) {
  const { slide, index, total, brand = {}, image = null, medical = false, cites = [], labels = {} } = opts;
  canvas.width = Math.round(W * scale); canvas.height = Math.round(H * scale);
  const g = canvas.getContext('2d');
  g.setTransform(scale, 0, 0, scale, 0, 0);
  const p = palette(opts.theme, brand);
  const fullBleed = slide.role === 'cover' && image;
  g.fillStyle = p.bg; g.fillRect(0, 0, W, H);
  const fg = fullBleed ? WHITE : p.fg, muted = fullBleed ? '#E8EDF4' : p.muted;
  if (fullBleed) {
    cover(g, image, 0, 0, W, H);
    const grad = g.createLinearGradient(0, H * 0.25, 0, H);
    grad.addColorStop(0, 'rgba(11,31,58,0.05)'); grad.addColorStop(0.55, 'rgba(11,31,58,0.72)'); grad.addColorStop(1, 'rgba(11,31,58,0.92)');
    g.fillStyle = grad; g.fillRect(0, 0, W, H);
    const top = g.createLinearGradient(0, 0, 0, 240);
    top.addColorStop(0, 'rgba(11,31,58,0.55)'); top.addColorStop(1, 'rgba(11,31,58,0)');
    g.fillStyle = top; g.fillRect(0, 0, W, 240);
  }
  // cabecera: marca y número de lámina
  g.textBaseline = 'top'; g.font = `600 30px ${FONT}`; g.fillStyle = muted;
  g.fillText(String(brand.business_name || '').slice(0, 40), PAD, PAD - 20);
  const num = `${index + 1}/${total}`; g.fillText(num, W - PAD - g.measureText(num).width, PAD - 20);

  const footer = 120;
  const area = { x: PAD, y: PAD + 70, w: W - 2 * PAD, h: H - PAD - 70 - footer - PAD / 2 };
  let L;
  if (fullBleed) {
    const box = { w: area.w, h: 620 };
    L = layout(g, slide.title, slide.body, box, { title: [96, 60], body: [40, 32] });
    drawText(g, L, PAD, area.y + area.h - L.h - 24, fg, '#F1F4F8');
  } else if (image) {
    const ih = slide.role === 'cover' ? 600 : 540;
    g.save(); rounded(g, area.x, area.y, area.w, ih, 28); g.clip(); cover(g, image, area.x, area.y, area.w, ih); g.restore();
    const box = { w: area.w, h: area.h - ih - 56 };
    L = layout(g, slide.title, slide.body, box, { title: [68, 44], body: [40, 30] });
    drawText(g, L, PAD, area.y + ih + 56, fg, p.fg === WHITE ? '#E3E9F1' : '#24364F');
  } else {
    // Portada y cierre: barra de acento y texto centrado en vertical. Desarrollo: número grande de lámina.
    const centered = slide.role !== 'content', cover1 = slide.role === 'cover';
    g.fillStyle = p.accent;
    if (centered) g.fillRect(PAD, area.y + 40, 120, 14);
    else { g.font = `800 120px ${FONT}`; g.fillText(String(index + 1).padStart(2, '0'), PAD - 6, area.y); }
    const top = area.y + (centered ? 110 : 170);
    const box = { w: area.w, h: area.y + area.h - top };
    L = layout(g, slide.title, slide.body, box, cover1 ? { title: [104, 64], body: [44, 34] }
      : centered ? { title: [88, 56], body: [44, 34] } : { title: [76, 48], body: [44, 32] });
    const y = centered ? top + Math.max(0, (box.h - L.h) / 2) : top;
    drawText(g, L, PAD, y, fg, p.fg === WHITE ? '#E3E9F1' : '#24364F');
  }
  // pie: fuentes, aviso educativo (canal médico) y progreso
  const fy = H - PAD / 2 - footer + 24;
  g.font = `500 28px ${FONT}`; g.fillStyle = muted;
  const notes = [];
  if (cites.length) notes.push(`${labels.sources || 'Fuentes'}: ${cites.join(', ')}`);
  if (medical && (slide.role === 'close' || slide.role === 'cover')) notes.push(labels.edu || '');
  notes.filter(Boolean).forEach((n, i) => {
    const lines = wrap(g, n, W - 2 * PAD - (slide.role === 'cover' && i === notes.length - 1 ? 200 : 0));
    g.fillText(lines[0] + (lines.length > 1 ? '…' : ''), PAD, fy + i * 36);
  });
  if (slide.role === 'cover' && total > 1) {
    g.font = `700 30px ${FONT}`; g.fillStyle = fullBleed ? WHITE : p.accent;
    const sw = labels.swipe || 'Desliza →';
    g.fillText(sw, W - PAD - g.measureText(sw).width, fy + (notes.length ? (notes.length - 1) * 36 : 0));
  }
  const bar = W - 2 * PAD, by = H - PAD / 2;
  g.fillStyle = fullBleed ? 'rgba(255,255,255,0.3)' : (p.fg === WHITE ? 'rgba(255,255,255,0.25)' : 'rgba(11,31,58,0.12)');
  g.fillRect(PAD, by, bar, 6);
  g.fillStyle = fullBleed ? WHITE : p.accent; g.fillRect(PAD, by, bar * (index + 1) / total, 6);
  return { overflow: L.overflow, contrast: contrast(fg, fullBleed ? NAVY : p.bg) };
}

export const toPng = (canvas) => new Promise((resolve, reject) =>
  canvas.toBlob(b => (b ? resolve(b) : reject(new Error('png_failed'))), 'image/png'));

// ---------- ZIP (método «store», sin compresión: los PNG ya van comprimidos) ----------
const CRC = (() => { const t = new Uint32Array(256); for (let n = 0; n < 256; n++) { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xEDB88320 ^ (c >>> 1) : c >>> 1; t[n] = c >>> 0; } return t; })();
function crc32(bytes) { let c = 0xFFFFFFFF; for (let i = 0; i < bytes.length; i++) c = CRC[(c ^ bytes[i]) & 255] ^ (c >>> 8); return (c ^ 0xFFFFFFFF) >>> 0; }

export async function zip(files, now = new Date()) {
  const enc = new TextEncoder();
  const time = (now.getHours() << 11) | (now.getMinutes() << 5) | (now.getSeconds() >> 1);
  const date = ((now.getFullYear() - 1980) << 9) | ((now.getMonth() + 1) << 5) | now.getDate();
  const parts = [], central = [];
  let offset = 0;
  for (const f of files) {
    const name = enc.encode(f.name);
    const data = f.data instanceof Blob ? new Uint8Array(await f.data.arrayBuffer()) : enc.encode(String(f.data));
    const crc = crc32(data);
    const head = new DataView(new ArrayBuffer(30));
    [[0, 0x04034b50, 4], [4, 20, 2], [6, 0x0800, 2], [8, 0, 2], [10, time, 2], [12, date, 2], [14, crc, 4],
      [18, data.length, 4], [22, data.length, 4], [26, name.length, 2], [28, 0, 2]]
      .forEach(([o, v, n]) => (n === 4 ? head.setUint32(o, v, true) : head.setUint16(o, v, true)));
    const dir = new DataView(new ArrayBuffer(46));
    [[0, 0x02014b50, 4], [4, 20, 2], [6, 20, 2], [8, 0x0800, 2], [10, 0, 2], [12, time, 2], [14, date, 2], [16, crc, 4],
      [20, data.length, 4], [24, data.length, 4], [28, name.length, 2], [30, 0, 2], [32, 0, 2], [34, 0, 2], [36, 0, 2],
      [38, 0, 4], [42, offset, 4]]
      .forEach(([o, v, n]) => (n === 4 ? dir.setUint32(o, v, true) : dir.setUint16(o, v, true)));
    parts.push(head, name, data); central.push(dir, name);
    offset += 30 + name.length + data.length;
  }
  const size = central.reduce((s, x) => s + x.byteLength, 0);
  const end = new DataView(new ArrayBuffer(22));
  [[0, 0x06054b50, 4], [4, 0, 2], [6, 0, 2], [8, files.length, 2], [10, files.length, 2], [12, size, 4], [16, offset, 4], [20, 0, 2]]
    .forEach(([o, v, n]) => (n === 4 ? end.setUint32(o, v, true) : end.setUint16(o, v, true)));
  return new Blob([...parts, ...central, end], { type: 'application/zip' });
}

export function slug(s) {
  return String(s || 'carrusel').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase()
    .replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 40) || 'carrusel';
}
