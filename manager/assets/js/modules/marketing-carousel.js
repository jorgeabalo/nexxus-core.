// Editor de carruseles: tema y público, láminas (portada, desarrollo, cierre) con imagen opcional de la
// Biblioteca, referencias, caption y hashtags. Guarda en el backend (que valida textos, imágenes y privacidad) y
// exporta las láminas como PNG 1080 × 1350 numerados en un ZIP, con caption.txt y referencias.txt.
// La publicación es manual: un enlace al perfil no es una cuenta conectada.
import { el, clear, openModal, field, input, select, toast } from '../ui.js';
import { api } from '../api.js';
import { t, errText } from './marketing-i18n.js';
import { k, kErr } from './marketing-carousel-i18n.js';
import { CHANNELS, LANGUAGES, EDITABLE } from './marketing-state.js';
import { drawSlide, toPng, zip, slug } from './marketing-carousel-render.js';

const M = api.marketing;
const ROLES = ['cover', 'content', 'close'];
const THEMES = ['light', 'dark', 'brand'];
const err = (e) => kErr(e) || errText(e);

// El carrusel vive en content.script como JSON (ver services/marketing_carousel.py).
export function parseCarousel(c) {
  if (!c || c.format !== 'carousel' || typeof c.script !== 'string' || !c.script.trim().startsWith('{')) return null;
  try { const d = JSON.parse(c.script); return d && d.kind === 'carousel' ? d : null; } catch { return null; }
}

const blank = (i, n) => ({ role: i === 0 ? 'cover' : i === n - 1 ? 'close' : 'content', title: '', body: '', media_id: null, refs: [], review: false });
function resize(slides, n) {
  const out = slides.slice(0, n).map(s => ({ ...s }));
  while (out.length < n) out.push(blank(out.length, n));
  out.forEach((s, i) => { if (i === n - 1 && s.role === 'content') s.role = 'close'; else if (i < n - 1 && s.role === 'close') s.role = 'content'; });
  return out;
}

// Imágenes que el carrusel puede usar: imagen lista y con privacidad apta (en un canal médico, solo sin personas).
// El backend vuelve a comprobarlo al guardar.
export function usableImages(items, accountType) {
  const ok = accountType === 'medical_education' ? ['business_media_no_people'] : ['business_media_no_people', 'consented_people'];
  return (items || []).filter(m => m.media_type === 'image' && m.usable && ok.includes(m.privacy_class));
}

async function loadImage(tenantId, id, cache) {
  if (!id) return null;
  if (!cache.has(id)) {
    cache.set(id, (async () => {
      const s = await M.streamSession(tenantId, id);
      try {
        const res = await fetch(s.url, { credentials: 'same-origin', cache: 'no-store' });
        if (!res.ok) throw new Error('image_unavailable');
        return await createImageBitmap(await res.blob());
      } finally { M.streamRevoke(tenantId, id).catch(() => {}); }
    })().catch(() => null));
  }
  return cache.get(id);
}

export async function carouselEditor(ctx, { existing = null, reload }) {
  const holder = el('div', { class: 'mk-carousel' }, el('div', { class: 'spinner spinner-inline' }));
  const close = openModal({ title: existing ? k('title_edit') : k('title_new'), closeLabel: t('close'), body: holder });
  document.querySelector('#modal-root .modal')?.classList.add('modal-carousel');
  let brandData, library = [], item = existing;
  try {
    [brandData, library] = await Promise.all([M.brand(ctx.tenantId),
      M.library(ctx.tenantId).then(r => r.media || r.items || []).catch(() => [])]);
    if (existing) item = (await M.content(ctx.tenantId, existing.id)).content;
  } catch (e) { clear(holder).appendChild(el('p', { class: 'form-error' }, err(e))); return; }
  await Promise.all(['700 64px Inter', '400 40px Inter', '600 30px Inter', '800 120px Inter']
    .map(f => document.fonts?.load(f).catch(() => null)));

  const brand = brandData.profile || {};
  const medical = brand.account_type === 'medical_education';
  const images = usableImages(library, brand.account_type);
  const cache = new Map();
  const saved = parseCarousel(item);
  const st = {
    id: item?.id || null, status: item?.status || 'draft',
    title: item?.title || '', topic: item?.topic || '', objective: item?.objective || '',
    language: item?.language || ctx.profile.language || 'es',
    channels: item?.channels?.length ? item.channels : ['instagram'],
    caption: item?.caption || '', hashtags: (item?.hashtags || []).join(' '), cta: item?.cta || '',
    audience: saved?.audience || '', tone: saved?.tone || brand.tone || '',
    theme: saved?.theme || 'light', medical_review: !!saved?.medical_review,
    slides: saved?.slides?.length ? saved.slides.map(s => ({ ...s, refs: [...(s.refs || [])] })) : resize([], 8),
    references: (saved?.references || []).map(r => ({ ...r })),
    sel: 0, dirty: false, overflow: {},
  };
  const editable = EDITABLE.includes(st.status);
  const touch = () => { st.dirty = true; status.textContent = k('unsaved'); };

  const doc = () => ({ audience: st.audience, tone: st.tone, theme: st.theme, medical_review: st.medical_review,
    slides: st.slides.map(s => ({ role: s.role, title: s.title.trim(), body: s.body.trim(), media_id: s.media_id || null,
      refs: s.refs.filter(n => n < st.references.length), review: !!s.review })),
    references: st.references.filter(r => r.label.trim()).map(r => ({ label: r.label.trim(), url: r.url.trim() })) });
  const opts = async (i) => ({ slide: st.slides[i], index: i, total: st.slides.length, theme: st.theme, brand, medical,
    image: await loadImage(ctx.tenantId, st.slides[i].media_id, cache),
    cites: st.slides[i].refs.filter(n => n < st.references.length).map(n => n + 1),
    labels: { edu: k('edu'), swipe: k('swipe'), sources: k('sources') } });

  // ---------- vista previa ----------
  const big = el('canvas', { class: 'mk-cs-canvas', role: 'img', 'aria-label': k('slide', 1) });
  const strip = el('div', { class: 'mk-cs-strip' });
  const status = el('p', { class: 'hint', role: 'status' }, editable ? '' : k('locked'));
  const checks = el('ul', { class: 'mk-cs-checks' });
  let drawing = 0;
  async function preview() {
    const run = ++drawing;
    for (let i = 0; i < st.slides.length; i++) {
      const o = await opts(i);
      if (run !== drawing) return;
      const thumb = strip.children[i]?.querySelector('canvas');
      const r = drawSlide(thumb || el('canvas'), o, 0.16);
      st.overflow[i] = r.overflow;
      if (i === st.sel) { drawSlide(big, o, 0.5); big.setAttribute('aria-label', `${k('slide', i + 1)}: ${o.slide.title}`); }
    }
    renderChecks();
  }
  function renderChecks() {
    const w = [];
    st.slides.forEach((s, i) => {
      if (!s.title.trim() && !s.body.trim()) w.push(k('w_empty', i + 1));
      if (st.overflow[i]) w.push(k('w_overflow', i + 1));
      if (s.review) w.push(k('w_review', i + 1));
      if (medical && s.role === 'content' && !s.refs.length) w.push(k('w_norefs', i + 1));
    });
    if (!st.caption.trim()) w.push(k('w_caption'));
    if (medical && !st.medical_review) w.push(k('w_medical'));
    clear(checks).append(...(w.length ? w : [k('w_ok')]).map(x => el('li', { class: w.length ? 'warn' : 'ok' }, x)));
  }
  const schedule = (() => { let h; return () => { clearTimeout(h); h = setTimeout(preview, 120); }; })();

  // ---------- láminas ----------
  const slideBox = el('div', { class: 'mk-cs-slide' });
  function drawStrip() {
    clear(strip).append(...st.slides.map((s, i) => el('button', {
      type: 'button', class: `mk-cs-thumb${i === st.sel ? ' sel' : ''}`, 'aria-label': k('slide', i + 1),
      'aria-pressed': String(i === st.sel), onclick: () => { st.sel = i; drawStrip(); drawSlideForm(); schedule(); },
    }, el('canvas'), el('span', {}, String(i + 1)))));
  }
  function drawSlideForm() {
    const s = st.slides[st.sel], i = st.sel, n = st.slides.length;
    const dis = !editable;
    const role = select(ROLES.map(r => ({ value: r, label: k(`r_${r}`), selected: r === s.role })), { disabled: dis });
    role.onchange = () => { s.role = role.value; touch(); schedule(); };
    const title = input({ maxlength: '90', value: s.title, disabled: dis });
    const body = el('textarea', { class: 'input', maxlength: '320', rows: '4', disabled: dis }, s.body);
    const count = (node, max) => { const c = el('span', { class: 'hint' }, `${node.value.length}/${max}`);
      node.addEventListener('input', () => { c.textContent = `${node.value.length}/${max}`; }); return c; };
    title.oninput = () => { s.title = title.value; touch(); schedule(); };
    body.oninput = () => { s.body = body.value; touch(); schedule(); };
    const img = select([{ value: '', label: k('no_image') },
      ...images.map(m => ({ value: m.id, label: m.original_filename || m.id.slice(0, 8), selected: m.id === s.media_id }))], { disabled: dis });
    img.onchange = () => { s.media_id = img.value || null; touch(); schedule(); };
    const cites = el('div', { class: 'mk-checks' }, st.references.map((r, n2) => el('label', { class: 'mk-check' },
      el('input', { type: 'checkbox', checked: s.refs.includes(n2), disabled: dis,
        onchange: (e) => { s.refs = e.target.checked ? [...s.refs, n2] : s.refs.filter(x => x !== n2); touch(); schedule(); } }),
      el('span', {}, `${n2 + 1}. ${r.label || '—'}`))));
    const review = el('label', { class: 'mk-check' }, el('input', { type: 'checkbox', checked: !!s.review, disabled: dis,
      onchange: (e) => { s.review = e.target.checked; touch(); schedule(); } }), el('span', {}, k('review')));
    const move = (d) => () => { const j = i + d; [st.slides[i], st.slides[j]] = [st.slides[j], st.slides[i]]; st.sel = j;
      touch(); drawStrip(); drawSlideForm(); schedule(); };
    const btn = (label, on, off) => el('button', { class: 'btn btn-sm', type: 'button', disabled: dis || off, onclick: on }, label);
    clear(slideBox).append(el('h4', {}, k('slide', i + 1)),
      el('div', { class: 'form-grid' },
        field(k('role'), role), field(k('image'), img, { hint: images.length ? (medical ? k('image_medical') : null) : k('no_images') }),
        el('div', { class: 'field full' }, el('label', { for: 'cs-title' }, k('s_title')), Object.assign(title, { id: 'cs-title' }), count(title, 90)),
        el('div', { class: 'field full' }, el('label', { for: 'cs-body' }, k('s_body')), Object.assign(body, { id: 'cs-body' }), count(body, 320)),
        st.references.length ? field(k('cites'), cites, { full: true }) : null,
        el('div', { class: 'field full' }, review)),
      el('div', { class: 'btn-row' },
        btn(`↑ ${k('up')}`, move(-1), i === 0), btn(`↓ ${k('down')}`, move(1), i === n - 1),
        btn(k('remove'), () => { st.slides.splice(i, 1); st.slides = resize(st.slides, st.slides.length); st.sel = Math.min(i, st.slides.length - 1);
          count2.value = String(st.slides.length); touch(); drawStrip(); drawSlideForm(); schedule(); }, n <= 2),
        btn(k('add'), () => { st.slides.splice(i + 1, 0, blank(i + 1, n + 1)); st.slides = resize(st.slides, st.slides.length);
          st.sel = i + 1; count2.value = String(st.slides.length); touch(); drawStrip(); drawSlideForm(); schedule(); }, n >= 10)));
  }

  // ---------- tema, público y texto ----------
  const bind = (node, key, after) => { node.addEventListener('input', () => { st[key] = node.value; touch(); after?.(); }); return node; };
  const dis = !editable;
  const name = bind(input({ maxlength: '160', value: st.title, disabled: dis }), 'title');
  const topic = bind(input({ maxlength: '300', value: st.topic, disabled: dis }), 'topic');
  const objective = bind(input({ maxlength: '300', value: st.objective, disabled: dis }), 'objective');
  const audience = bind(input({ maxlength: '200', value: st.audience, disabled: dis }), 'audience');
  const tone = bind(input({ maxlength: '120', value: st.tone, disabled: dis }), 'tone');
  const count2 = select(Array.from({ length: 9 }, (_, i) => i + 2).map(n => ({ value: String(n), label: String(n), selected: n === st.slides.length })), { disabled: dis });
  count2.onchange = () => { st.slides = resize(st.slides, Number(count2.value)); st.sel = Math.min(st.sel, st.slides.length - 1); touch(); drawStrip(); drawSlideForm(); schedule(); };
  const lang = select(LANGUAGES.map(l => ({ value: l, label: t(`l_${l}`), selected: l === st.language })), { disabled: dis });
  lang.onchange = () => { st.language = lang.value; touch(); };
  const theme = select(THEMES.map(x => ({ value: x, label: k(`th_${x}`), selected: x === st.theme })), { disabled: dis });
  theme.onchange = () => { st.theme = theme.value; touch(); schedule(); };
  const channels = el('div', { class: 'mk-checks' }, CHANNELS.map(c => el('label', { class: 'mk-check' },
    el('input', { type: 'checkbox', value: c, checked: st.channels.includes(c), disabled: dis,
      onchange: () => { st.channels = [...channels.querySelectorAll('input:checked')].map(x => x.value); touch(); } }), el('span', {}, c))));
  const caption = bind(el('textarea', { class: 'input', maxlength: '5000', rows: '6', disabled: dis }, st.caption), 'caption', renderChecks);
  const hashtags = bind(input({ maxlength: '1000', value: st.hashtags, disabled: dis }), 'hashtags');
  const cta = bind(input({ maxlength: '200', value: st.cta, disabled: dis }), 'cta');
  const medReview = el('label', { class: 'mk-check' }, el('input', { type: 'checkbox', checked: st.medical_review, disabled: dis,
    onchange: (e) => { st.medical_review = e.target.checked; touch(); renderChecks(); } }), el('span', {}, k('medical_review')));

  const refsBox = el('div', { class: 'mk-cs-refs' });
  function drawRefs() {
    clear(refsBox).append(...st.references.map((r, i) => {
      const label = input({ maxlength: '240', value: r.label, placeholder: k('ref_label'), 'aria-label': `${i + 1}. ${k('ref_label')}`, disabled: dis });
      const url = input({ maxlength: '500', value: r.url, placeholder: k('ref_url'), type: 'url', 'aria-label': `${i + 1}. ${k('ref_url')}`, disabled: dis });
      label.oninput = () => { r.label = label.value; touch(); };
      url.oninput = () => { r.url = url.value; touch(); };
      return el('div', { class: 'mk-cs-ref' }, el('strong', {}, `${i + 1}.`), label, url,
        el('button', { class: 'btn btn-sm', type: 'button', disabled: dis, onclick: () => {
          st.references.splice(i, 1);
          st.slides.forEach(s => { s.refs = s.refs.filter(n => n !== i).map(n => (n > i ? n - 1 : n)); });
          touch(); drawRefs(); drawSlideForm(); schedule(); } }, k('remove')));
    }), el('button', { class: 'btn btn-sm', type: 'button', disabled: dis || st.references.length >= 15,
      onclick: () => { st.references.push({ label: '', url: '' }); touch(); drawRefs(); drawSlideForm(); } }, k('ref_add')));
  }

  // ---------- acciones ----------
  const msg = el('p', { class: 'form-error', role: 'alert' });
  async function save() {
    msg.textContent = '';
    if (!st.title.trim()) { msg.textContent = t('required'); name.focus(); return false; }
    const values = { title: st.title.trim(), topic: st.topic.trim() || null, objective: st.objective.trim() || null,
      format: 'carousel', language: st.language, channels: st.channels, caption: st.caption.trim() || null,
      hashtags: st.hashtags, cta: st.cta.trim() || null, carousel: doc() };
    saveBtn.disabled = true; status.textContent = k('saving');
    try {
      const row = st.id ? await M.updateContent(ctx.tenantId, st.id, values) : await M.createContent(ctx.tenantId, values);
      st.id = row.id; st.dirty = false; status.textContent = k('saved'); toast(k('saved')); reload?.();
      aiBtn.disabled = !st.channels.length;
      return true;
    } catch (e) { msg.textContent = err(e); status.textContent = ''; return false; } finally { saveBtn.disabled = false; }
  }
  const saveBtn = el('button', { class: 'btn btn-primary', type: 'button', disabled: !editable, onclick: save }, k('save'));
  const dlBtn = el('button', { class: 'btn btn-accent', type: 'button', onclick: async () => {
    dlBtn.disabled = true; status.textContent = k('preparing');
    try {
      const files = [], base = slug(st.title || brand.business_name);
      const c = el('canvas');
      for (let i = 0; i < st.slides.length; i++) {
        drawSlide(c, await opts(i), 1);
        files.push({ name: `${base}-${String(i + 1).padStart(2, '0')}.png`, data: await toPng(c) });
      }
      files.push({ name: 'caption.txt', data: captionText() });
      const refs = doc().references;
      if (refs.length) files.push({ name: 'referencias.txt', data: refs.map((r, i) => `${i + 1}. ${r.label}${r.url ? `\n   ${r.url}` : ''}`).join('\n') + '\n' });
      const a = el('a', { href: URL.createObjectURL(await zip(files)), download: `${base}.zip` });
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(a.href), 30000);
      status.textContent = k('downloaded');
    } catch (e) { msg.textContent = err(e); status.textContent = ''; } finally { dlBtn.disabled = false; }
  } }, k('download'));
  const captionText = () => [st.caption.trim(), st.cta.trim(), st.hashtags.trim().split(/[\s,]+/).filter(Boolean)
    .map(h => (h.startsWith('#') ? h : `#${h}`)).join(' ')].filter(Boolean).join('\n\n');
  const copyBtn = el('button', { class: 'btn', type: 'button', onclick: async () => {
    try { await navigator.clipboard.writeText(captionText()); toast(k('copied')); } catch { msg.textContent = k('copy_fail'); }
  } }, k('copy'));
  const aiMsg = el('p', { class: 'hint', role: 'status' }, k('ai_hint'));
  const aiBtn = el('button', { class: 'btn btn-sm btn-accent', type: 'button', disabled: !editable || !st.id || !st.channels.length,
    title: k('ai_save_first'), onclick: async () => {
      if (st.dirty && !(await save())) return;
      aiBtn.disabled = true; aiMsg.textContent = k('ai_wait');
      try {
        const out = await M.writeCopy(ctx.tenantId, st.id, '');
        close(); carouselEditor(ctx, { existing: out.content, reload });
      } catch (e) {
        aiMsg.textContent = e?.code === 'text_ai_unavailable' ? errText(e) : err(e); aiBtn.disabled = false;
      }
    } }, k('ai'));

  clear(holder).append(
    el('p', { class: 'hint' }, k('manual')), status,
    el('div', { class: 'mk-cs-layout' },
      el('div', { class: 'mk-cs-view' }, big, strip, el('h4', {}, k('check')), checks),
      el('div', { class: 'mk-cs-edit' },
        el('h3', {}, k('setup')),
        el('div', { class: 'form-grid' },
          field(`${k('name')} *`, name, { full: true }), field(k('topic'), topic, { full: true }),
          field(k('audience'), audience), field(k('objective'), objective), field(k('tone'), tone),
          field(k('count'), count2), field(k('language'), lang), field(k('theme'), theme)),
        el('div', { class: 'mk-ai-writer' }, el('div', { class: 'btn-row' }, aiBtn), aiMsg),
        el('h3', {}, k('slides')), slideBox,
        el('h3', {}, k('refs')), el('p', { class: 'hint' }, k('refs_hint')), refsBox,
        el('h3', {}, k('text')),
        el('div', { class: 'form-grid' }, field(k('caption'), caption, { full: true }),
          field(k('hashtags'), hashtags), field(k('cta'), cta), field(k('channels'), channels, { full: true }),
          medical ? el('div', { class: 'field full' }, medReview) : null))),
    msg, el('div', { class: 'btn-row mk-cs-actions' }, saveBtn, dlBtn, copyBtn));
  drawStrip(); drawSlideForm(); drawRefs(); preview();
}
