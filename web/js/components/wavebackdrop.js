/**
 * The page's background is one long sound wave.
 *
 * Each section of the page is a phrase of the wave, with a beat of silence
 * between phrases. The wave is far wider than the screen and the screen's
 * centre line is the playhead: scrolling down slides the wave left, so the
 * reader travels along the same track and arrives at a different phrase for
 * each section. What is left of the playhead has been "played" and turns brass.
 *
 * It sits behind the text, faint enough to read through. Scroll speed makes
 * the wave swell a little, and the wave glides after the scroll rather than
 * snapping to it. Reduced motion swaps phrases in place without sliding.
 *
 * Drawn on one canvas, and only the bars on screen, because the whole track as
 * a composited layer would be several screens wide.
 */

import { h } from '../core/dom.js';

/** Deterministic noise, so the wave keeps its shape between visits. */
function noise(index, salt) {
  const value = Math.sin(index * 12.9898 + salt * 78.233) * 43758.5453;
  return value - Math.floor(value);
}

/** Loudness of a phrase at position t (0..1). Each suits its section. */
const SHAPES = {
  burst: (t, n) => 0.5 + 0.4 * n + (t > 0.3 && t < 0.62 ? 0.2 : 0),
  pulse: (t, n) => (0.28 + 0.72 * Math.pow(0.5 + 0.5 * Math.sin(t * Math.PI * 7), 2)) * (0.72 + 0.28 * n),
  steady: (_t, n) => 0.46 + 0.26 * n,
  calm: (_t, n) => 0.16 + 0.18 * n,
  wander: (t, n) => (0.28 + 0.52 * (0.5 + 0.5 * Math.sin(t * Math.PI * 3 + 1))) * (0.72 + 0.28 * n),
  rise: (t, n) => (0.14 + 0.86 * t) * (0.64 + 0.36 * n),
};

export function waveBackdrop(sections) {
  const reduced =
    typeof matchMedia === 'function' &&
    matchMedia('(prefers-reduced-motion: reduce)').matches;

  const canvas = h('canvas', { class: 'wave-canvas' });
  const node = h('div', { class: 'wave-backdrop', 'aria-hidden': 'true' }, canvas);
  const ctx = canvas.getContext('2d');

  let viewW = 0;
  let viewH = 0;
  let pitch = 0;
  let barWidth = 0;
  let bars = [];      // { x, loud } along the whole track, in px
  let centres = [];   // x of the middle of each phrase
  let ends = [];      // [start, end] x of each phrase

  let shownX = null;  // where the playhead is drawn
  let targetX = 0;    // where the scroll says it should be
  let phrase = 0;
  let energy = 0;
  let lastScrollY = window.scrollY;
  let lastTime = performance.now();
  let frame = null;
  let colours = null;

  /* -- the track ------------------------------------------------------ */

  function build() {
    viewW = window.innerWidth;
    viewH = window.innerHeight;
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    canvas.width = Math.round(viewW * dpr);
    canvas.height = Math.round(viewH * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    // Big bars: roughly 70 across a desktop screen, 36 across a phone.
    pitch = Math.max(10, viewW / (viewW < 700 ? 36 : 70));
    barWidth = pitch * 0.46;
    const perPhrase = Math.round((viewW * 0.94) / pitch);
    const silence = Math.max(3, Math.round((viewW * 0.16) / pitch));

    bars = [];
    centres = [];
    ends = [];
    let index = 0;
    sections.forEach((section, p) => {
      const shape = SHAPES[section.shape] || SHAPES.steady;
      const start = index * pitch;
      for (let i = 0; i < perPhrase; i += 1) {
        const t = i / (perPhrase - 1);
        // Taper into the silence at both edges, so phrases read as separate.
        const taper = Math.pow(Math.sin(Math.PI * (t * 0.94 + 0.03)), 0.6);
        bars.push({ x: index * pitch, loud: Math.min(1, shape(t, noise(index, p + 1)) * taper) });
        index += 1;
      }
      const end = (index - 1) * pitch;
      ends.push([start, end]);
      centres.push((start + end) / 2);
      index += silence;
    });
    readColours();
  }

  function readColours() {
    const style = getComputedStyle(node);
    const alpha = (name, fallback) => {
      const value = parseFloat(style.getPropertyValue(name));
      return Number.isFinite(value) ? value : fallback;
    };
    colours = {
      ahead: style.color,
      played: style.getPropertyValue('--wave-played').trim() || style.color,
      aheadAlpha: alpha('--wave-ahead-alpha', 0.08),
      playedAlpha: alpha('--wave-played-alpha', 0.2),
    };
  }

  /* -- reading the page ----------------------------------------------- */

  /**
   * A section sits in the middle of its phrase when its top reaches the top of
   * the screen, and the silence between two phrases passes the playhead when
   * the boundary between their sections passes the middle of the screen.
   * Sections the page runs out of scroll before reaching are spread over the
   * scroll that is left, so the last phrase is always reachable.
   */
  function anchors() {
    const scrollY = window.scrollY;
    const max = Math.max(0, document.documentElement.scrollHeight - viewH);
    const raw = sections.map((section, i) => {
      if (i === 0) return 0;
      const element = document.getElementById(section.id);
      return element ? Math.max(0, element.getBoundingClientRect().top + scrollY) : 0;
    });
    let reachable = 0;
    raw.forEach((point, i) => {
      if (point < max) reachable = i;
    });
    const spare = raw.length - 1 - reachable;
    return {
      max,
      points: raw.map((point, i) =>
        i <= reachable ? point : raw[reachable] + ((max - raw[reachable]) * (i - reachable)) / spare
      ),
    };
  }

  function measure() {
    const scrollY = window.scrollY;
    const { points, max } = anchors();
    const last = points.length - 1;

    let x;
    if (scrollY >= points[last]) {
      // Past the last section's top: play out the rest of its phrase.
      const tail = max - points[last];
      const fraction = tail > 0 ? Math.min(1, (scrollY - points[last]) / tail) : 0;
      x = centres[last] + fraction * (ends[last][1] - centres[last]);
    } else {
      let i = 0;
      while (i < last - 1 && scrollY >= points[i + 1]) i += 1;
      const span = points[i + 1] - points[i];
      const fraction = span > 0 ? Math.max(0, Math.min(1, (scrollY - points[i]) / span)) : 0;
      x = centres[i] + fraction * (centres[i + 1] - centres[i]);
    }

    // The current section is whichever phrase centre the playhead is nearest.
    let nearest = 0;
    centres.forEach((centre, i) => {
      if (Math.abs(centre - x) < Math.abs(centres[nearest] - x)) nearest = i;
    });
    return { x, nearest };
  }

  /* -- drawing -------------------------------------------------------- */

  function draw() {
    if (!colours || !bars.length) return;
    ctx.clearRect(0, 0, viewW, viewH);

    const mid = viewW / 2;
    const midY = viewH / 2;
    const amplitude = viewH * 0.36 * (1 + energy * 0.22);
    const offset = mid - shownX;

    const path = (from, to) => {
      ctx.beginPath();
      // A few hundred bars in all, so walking the list is cheaper than indexing it.
      for (let i = 0; i < bars.length; i += 1) {
        const x = bars[i].x + offset;
        if (x < from - barWidth) continue;
        if (x > to + barWidth) break;
        const half = Math.max(barWidth / 2, bars[i].loud * amplitude);
        ctx.moveTo(x, midY - half);
        ctx.lineTo(x, midY + half);
      }
    };

    ctx.lineCap = 'round';
    ctx.lineWidth = barWidth;

    // The track itself, faint, running the full width like a timeline.
    ctx.globalAlpha = colours.aheadAlpha;
    ctx.fillStyle = colours.ahead;
    ctx.fillRect(0, midY - 0.5, viewW, 1);

    ctx.save();
    ctx.beginPath();
    ctx.rect(mid, 0, viewW - mid, viewH);
    ctx.clip();
    ctx.strokeStyle = colours.ahead;
    path(mid, viewW);
    ctx.stroke();
    ctx.restore();

    ctx.save();
    ctx.beginPath();
    ctx.rect(0, 0, mid, viewH);
    ctx.clip();
    ctx.globalAlpha = colours.playedAlpha;
    ctx.strokeStyle = colours.played;
    path(0, mid);
    ctx.stroke();
    ctx.restore();

    ctx.globalAlpha = 1;
  }

  function update(force = false) {
    frame = null;
    if (!node.isConnected) {
      teardown();
      return;
    }
    if (!bars.length) return;

    const now = performance.now();
    const scrollY = window.scrollY;
    const measured = measure();
    phrase = measured.nearest;
    node.dataset.phrase = String(phrase);

    if (reduced) {
      // No sliding: the wave sits on the current section's phrase.
      targetX = centres[phrase];
      shownX = targetX;
    } else {
      targetX = measured.x;
      const speed = Math.abs(scrollY - lastScrollY) / Math.max(1, now - lastTime);
      energy += (Math.min(1, speed / 2.5) - energy) * (speed > energy ? 0.35 : 0.08);
      if (shownX == null || force) shownX = targetX;
      else shownX += (targetX - shownX) * 0.16;
    }
    lastScrollY = scrollY;
    lastTime = now;

    draw();

    // Keep gliding until the wave has caught up and settled.
    if (!reduced && (Math.abs(targetX - shownX) > 0.5 || energy > 0.01)) schedule();
  }

  /* -- lifecycle ------------------------------------------------------ */

  function schedule() {
    if (frame == null) frame = requestAnimationFrame(() => update());
  }

  const onScroll = () => schedule();
  const onResize = () => {
    build();
    update(true);
  };
  const onTheme = () => {
    readColours();
    draw();
  };

  function teardown() {
    window.removeEventListener('scroll', onScroll);
    window.removeEventListener('resize', onResize);
    window.removeEventListener('aloud:theme', onTheme);
    if (frame != null) cancelAnimationFrame(frame);
  }

  window.addEventListener('scroll', onScroll, { passive: true });
  window.addEventListener('resize', onResize);
  window.addEventListener('aloud:theme', onTheme);

  // Colours and section positions only exist once the node is in the page.
  let attempts = 0;
  requestAnimationFrame(function mount() {
    if (!node.isConnected) {
      attempts += 1;
      if (attempts < 60) requestAnimationFrame(mount);
      else teardown();
      return;
    }
    build();
    update(true);
    // Fonts settle after first paint and move the sections.
    document.fonts?.ready?.then(() => update(true));
  });

  // For tests: headless browsers throttle animation frames heavily.
  node.__update = () => {
    update(true);
    return { phrase, label: sections[phrase].label, x: targetX, end: ends[ends.length - 1][1] };
  };
  return node;
}
