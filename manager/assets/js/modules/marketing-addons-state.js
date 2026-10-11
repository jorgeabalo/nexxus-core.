// Ampliaciones de publicaciones (puro, sin DOM): precios comerciales, combinación más económica y frecuencia.
// El backend y la base calculan lo mismo; el navegador solo muestra la cotización (nunca envía el precio).
export const ADDON_BLOCKS = [[8, 35], [4, 20], [1, 6]];   // +8 = $35, +4 = $20, +1 = $6
export const ADDON_PRESETS = [1, 4, 8];
export const WEEKLY_PRESETS = [1, 2];
export const WEEKS_PER_MONTH = 4;
export const MAX_ADDITIONAL = 500;

// Combinación exacta más económica de bloques para `additional` publicaciones al mes.
export function addonQuote(additional) {
  const n = Number(additional);
  if (!Number.isInteger(n) || n < 1 || n > MAX_ADDITIONAL) return null;
  let left = n, amount = 0;
  const blocks = [];
  for (const [size, price] of ADDON_BLOCKS) {
    const quantity = Math.floor(left / size);
    left %= size;
    if (quantity) { blocks.push({ size, quantity, subtotal: quantity * price }); amount += quantity * price; }
  }
  return { additional: n, amount, currency: 'USD', blocks };
}

// Lo que se enviará al backend y lo que se mostrará: mensual (N al mes) o semanal (N por semana = 4N al mes).
export function addonSpec(cadence, value) {
  const v = Number(value);
  if (cadence === 'weekly') {
    if (!Number.isInteger(v) || v < 1 || v * WEEKS_PER_MONTH > MAX_ADDITIONAL) return null;
    const q = addonQuote(v * WEEKS_PER_MONTH);
    return q && { cadence: 'weekly', per_week: v, ...q };
  }
  const q = addonQuote(v);
  return q && { cadence: 'monthly', per_week: null, ...q };
}

export function addonBody(spec) {
  return spec.cadence === 'weekly' ? { cadence: 'weekly', per_week: spec.per_week }
    : { cadence: 'monthly', additional_publications: spec.additional };
}

// Precio comercial: siempre en dólares enteros ($55); con decimales solo si los hubiera.
export const fmtPrice = (n) => (Number.isInteger(Number(n)) ? `$${Number(n)}` : `$${Number(n).toFixed(2)}`);

// Firma de lo confirmado: si cambia cantidad, frecuencia o precio, la confirmación se desmarca.
export const confirmSignature = (spec) => (spec ? `${spec.cadence}:${spec.per_week ?? ''}:${spec.additional}:${spec.amount}` : '');

// Bloques legibles: "8 + 4" en vez de "1× +8 + 1× +4".
export const blocksText = (blocks) => blocks.flatMap(b => Array(b.quantity).fill(String(b.size))).join(' + ');
