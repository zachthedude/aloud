/**
 * The resonance figure.
 *
 * Every concept gets a visual identity, and rather than invent an abstract
 * shape, this draws the one sound actually makes: a Chladni figure, the
 * pattern sand forms on a plate driven at a resonant frequency. The lines are
 * the nodes, where the plate is still.
 *
 * It is the right image for this product. It is literally the shape of sound,
 * it is mathematical rather than decorative, and it belongs in a physics
 * syllabus. Two different concepts get two different figures because their
 * ids pick different vibration modes, so a student learns to recognise a
 * lesson by its pattern.
 *
 * The field for a square plate is:
 *
 *     f(x, y) = cos(n pi x) cos(m pi y) - cos(m pi x) cos(n pi y)
 *
 * and the nodal lines are where f = 0. Those are traced with marching squares.
 */

import { h } from './dom.js';

/** Stable 32-bit hash, so a concept always draws the same figure. */
function hash(seed) {
  let value = 2166136261;
  const text = String(seed || 'aloud');
  for (let i = 0; i < text.length; i += 1) {
    value ^= text.charCodeAt(i);
    value = Math.imul(value, 16777619);
  }
  return value >>> 0;
}

/**
 * Choose a vibration mode from a seed.
 *
 * Two constraints, both learned from looking at the results. n must never
 * equal m, because the field cancels to zero and the plate comes out blank.
 * And the modes have to differ by at least two, because adjacent modes trace
 * only a handful of nearly straight curves, which reads as a random polygon
 * rather than as a resonance figure.
 */
function modeFor(seed) {
  const value = hash(seed);
  const n = 3 + (value % 5);                    // 3..7
  const offset = 2 + (Math.floor(value / 11) % 3); // 2..4
  let m = n + offset;
  if (m > 9) m = n - offset;                    // reflect rather than clamp
  if (m < 2) m = n + 2;
  const rotation = (value % 90) - 45;
  return { n, m, rotation };
}

function field(x, y, n, m) {
  const PI = Math.PI;
  return (
    Math.cos(n * PI * x) * Math.cos(m * PI * y) -
    Math.cos(m * PI * x) * Math.cos(n * PI * y)
  );
}

/** Where the zero crossing sits between two sampled corners. */
function interpolate(a, b, va, vb) {
  const t = va / (va - vb);
  return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t];
}

/**
 * Trace f = 0 with marching squares.
 * @returns {string} an SVG path built from independent segments
 */
function nodalPath(n, m, { resolution = 54, size = 100 } = {}) {
  const step = 1 / resolution;
  const segments = [];

  for (let i = 0; i < resolution; i += 1) {
    for (let j = 0; j < resolution; j += 1) {
      const x0 = i * step;
      const y0 = j * step;
      const x1 = x0 + step;
      const y1 = y0 + step;

      const corners = [
        [x0, y0, field(x0, y0, n, m)],
        [x1, y0, field(x1, y0, n, m)],
        [x1, y1, field(x1, y1, n, m)],
        [x0, y1, field(x0, y1, n, m)],
      ];

      // Which corners are positive, as a 4-bit index.
      let index = 0;
      for (let k = 0; k < 4; k += 1) if (corners[k][2] > 0) index |= 1 << k;
      if (index === 0 || index === 15) continue;

      // Zero crossings on each edge that has a sign change.
      const crossings = [];
      for (let k = 0; k < 4; k += 1) {
        const a = corners[k];
        const b = corners[(k + 1) % 4];
        if (a[2] > 0 !== b[2] > 0) {
          crossings.push(interpolate([a[0], a[1]], [b[0], b[1]], a[2], b[2]));
        }
      }
      // Join crossings pairwise. Ambiguous saddles (4 crossings) join in
      // order, which is visually indistinguishable at this scale.
      for (let k = 0; k + 1 < crossings.length; k += 2) {
        const [ax, ay] = crossings[k];
        const [bx, by] = crossings[k + 1];
        segments.push(
          `M${(ax * size).toFixed(2)} ${(ay * size).toFixed(2)}` +
          `L${(bx * size).toFixed(2)} ${(by * size).toFixed(2)}`
        );
      }
    }
  }
  return segments.join('');
}

const cache = new Map();

function cachedPath(n, m, resolution) {
  const key = `${n}:${m}:${resolution}`;
  if (!cache.has(key)) cache.set(key, nodalPath(n, m, { resolution }));
  return cache.get(key);
}

/**
 * Build the figure.
 *
 * @param {string} seed          concept or lesson id
 * @param {object} options
 * @param {boolean} options.active    resonating (audio is playing)
 * @param {string}  options.mode      'idle' | 'listening' | 'busy'
 * @param {boolean} options.detailed  a second, fainter mode behind the first
 */
export function resonanceFigure(seed, { active = false, mode = 'idle', detailed = true } = {}) {
  const { n, m, rotation } = modeFor(seed);
  const path = cachedPath(n, m, 58);
  // A neighbouring mode drawn faintly gives the plate depth. It must not
  // collapse to n === m, which would cancel the field to nothing.
  const softMode = m > n ? m + 1 : Math.max(2, m - 1);
  const soft = detailed && softMode !== n ? cachedPath(n, softMode, 36) : '';

  // The field is computed on a square, but the plate is round, so the pattern
  // is clipped to it. Without this the square's corners poke past the rim and
  // the figure reads as a mistake.
  const clipId = `plate-${Math.abs(hash(seed)).toString(36)}`;

  return h(
    'div',
    { class: 'resonance', dataset: { active: String(active), mode } },
    h(
      'svg',
      { viewBox: '0 0 100 100', 'aria-hidden': 'true' },
      h(
        'defs',
        null,
        h('clipPath', { id: clipId }, h('circle', { cx: 50, cy: 50, r: 46 }))
      ),
      h('circle', { class: 'plate', cx: 50, cy: 50, r: 47 }),
      // Listening halo: the plate receiving rather than emitting.
      h('circle', {
        class: 'halo',
        cx: 50,
        cy: 50,
        r: '47%',
        fill: 'none',
        stroke: 'currentColor',
        'stroke-width': 1,
        opacity: mode === 'listening' ? 0.18 : 0,
        style: { color: 'var(--floor)' },
      }),
      h(
        'g',
        { 'clip-path': `url(#${clipId})` },
        h(
          'g',
          { style: { transform: `rotate(${rotation}deg)`, transformOrigin: 'center' } },
          h(
            'g',
            { class: 'ring' },
            soft && h('path', { class: 'node node-soft', d: soft }),
            h('path', { class: 'node', d: path })
          )
        )
      ),
      // Thinking: one slow sweep around the plate.
      mode === 'busy' &&
        h('circle', {
          class: 'sweep',
          cx: 50,
          cy: 50,
          r: 47,
          fill: 'none',
          stroke: 'var(--floor)',
          'stroke-width': 1.6,
          'stroke-linecap': 'round',
          'stroke-dasharray': '18 277',
          opacity: 0.85,
        })
    )
  );
}

/**
 * A small flat version for cards and list rows.
 * No animation, fewer lines, sized to sit beside a title.
 */
export function resonanceMark(seed, { size = 44, color = 'currentColor' } = {}) {
  const { n, m, rotation } = modeFor(seed);
  // Coarser than the full figure: at 44px, fine detail turns into grey fuzz.
  const path = cachedPath(n, m, 34);
  const clipId = `mark-${Math.abs(hash(seed)).toString(36)}`;
  return h(
    'svg',
    {
      viewBox: '0 0 100 100',
      width: size,
      height: size,
      'aria-hidden': 'true',
      style: { flex: 'none' },
    },
    h('defs', null, h('clipPath', { id: clipId }, h('circle', { cx: 50, cy: 50, r: 45 }))),
    h('circle', {
      cx: 50, cy: 50, r: 46,
      fill: 'none', stroke: color, 'stroke-width': 1.5, opacity: 0.2,
    }),
    h(
      'g',
      { 'clip-path': `url(#${clipId})` },
      h('path', {
        d: path,
        fill: 'none',
        stroke: color,
        'stroke-width': 2.2,
        'stroke-linecap': 'round',
        opacity: 0.8,
        style: { transform: `rotate(${rotation}deg)`, transformOrigin: 'center' },
      })
    )
  );
}
