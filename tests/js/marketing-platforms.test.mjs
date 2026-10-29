// Versiones por plataforma: la vista previa del panel debe dar el mismo texto que el backend
// (services/marketing_platforms.py) y respetar el límite de cada red.
// Ejecutar: node --test tests/js/marketing-platforms.test.mjs
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { RULES, ASPECT, compose, variantsFor } from '../../manager/assets/js/modules/marketing-platforms-state.js';
import { CHANNELS } from '../../manager/assets/js/modules/marketing-state.js';

const long = 'Entrenamos fuerza y equilibrio con calma. '.repeat(40).trim();
const CASES = [
  { format: 'reel', channels: CHANNELS, caption: long, cta: 'Llámanos hoy', hashtags: ['#a', '#b', '#c', '#d', '#e', '#f'] },
  { format: 'image', channels: ['x', 'threads'], caption: 'Corto', cta: '', hashtags: [] },
  { format: 'video', channels: ['youtube', 'google_business'], caption: 'Una frase sin punto final '.repeat(30), cta: 'Reserva', hashtags: ['#x'] },
];

test('cada red tiene reglas y medidas', () => {
  assert.deepEqual(Object.keys(RULES).sort(), [...CHANNELS].sort());
  for (const f of Object.keys(ASPECT)) assert.deepEqual(Object.keys(ASPECT[f]).sort(), [...CHANNELS].sort(), f);
});

test('el texto nunca pasa el límite de la red y conserva la llamada a la acción', () => {
  for (const ch of CHANNELS) {
    const out = compose(long, 'Llámanos hoy', ['#fuerza', '#salud'], ch);
    assert.ok(out.length <= RULES[ch].text_max, ch);
    assert.ok(out.includes('Llámanos hoy'), ch);
  }
});

test('las versiones guardadas por la IA tienen prioridad', () => {
  const v = variantsFor({ format: 'reel', channels: ['instagram'], caption: 'x', platform_variants: { instagram: { text: 'Propio', aspect: '9:16' } } });
  assert.equal(v[0].text, 'Propio');
});

test('mismo resultado que el backend (Python)', (t) => {
  let py;
  try {
    py = JSON.parse(execFileSync(process.env.PYTHON || 'python3', ['-c', `
import json, sys
sys.path.insert(0, '.')
from services import marketing_platforms as p
cases = json.loads(sys.stdin.read())
print(json.dumps([p.variants(c) for c in cases]))`], { input: JSON.stringify(CASES), encoding: 'utf8' }));
  } catch (e) { t.skip('python no disponible'); return; }
  CASES.forEach((c, i) => {
    for (const v of variantsFor(c)) {
      assert.equal(v.text, py[i][v.channel].text, `${i}:${v.channel}`);
      assert.equal(v.aspect, py[i][v.channel].aspect, `${i}:${v.channel}`);
    }
  });
});
