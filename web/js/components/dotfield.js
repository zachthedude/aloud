/**
 * An animated dot field.
 *
 * A grid of dots that flicker between a few fixed opacities, rippling outwards
 * from the centre as they first appear. Used behind the sign-in panel, where
 * it gives the page a pulse without competing with anything that has to be
 * read.
 *
 * Canvas 2D rather than WebGL, for three reasons. The effect is a grid of
 * squares with varying alpha, which needs no shader. There is no bundler here,
 * so a 600KB 3D library would have to come from a CDN, and the content
 * security policy on this server allows scripts from its own origin only. And
 * a canvas fallback has to exist regardless, for the machines where WebGL is
 * unavailable or blocked.
 *
 * It costs little: the flicker is redrawn about twelve times a second rather
 * than every frame, it stops when the tab is hidden, and reduced motion gets a
 * single still frame.
 */

const SPACING = 22;
const DOT = 6;

/** The opacity ladder from the original: mostly dim, occasionally bright. */
const OPACITIES = [0.28, 0.28, 0.28, 0.45, 0.45, 0.45, 0.7, 0.7, 0.7, 1.0];

/** How often a cell picks a new opacity, in seconds. */
const FREQUENCY = 2.2;

/** Redraws per second. The flicker is a mood, not an animation. */
const FPS = 12;

/** Deterministic per-cell noise, so a cell keeps its own rhythm. */
function noise(x, y) {
  const value = Math.sin(x * 127.1 + y * 311.7) * 43758.5453;
  return value - Math.floor(value);
}

/**
 * @param {object} options
 * @param {string} options.color   CSS colour for the dots
 * @param {number} options.opacity overall ceiling, 0..1
 */
export function dotField({ color = 'rgba(255,255,255,1)', opacity = 0.5 } = {}) {
  const canvas = document.createElement('canvas');
  canvas.className = 'dotfield';
  canvas.setAttribute('aria-hidden', 'true');

  const context = canvas.getContext('2d', { alpha: true });
  if (!context) return canvas;

  const reduced =
    typeof matchMedia === 'function' &&
    matchMedia('(prefers-reduced-motion: reduce)').matches;

  let width = 0;
  let height = 0;
  let columns = 0;
  let rows = 0;
  let frame = null;
  let startedAt = performance.now();
  let lastDraw = 0;

  function resize() {
    const rect = canvas.getBoundingClientRect();
    if (!rect.width || !rect.height) return false;
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    width = rect.width;
    height = rect.height;
    canvas.width = Math.round(width * ratio);
    canvas.height = Math.round(height * ratio);
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    columns = Math.ceil(width / SPACING);
    rows = Math.ceil(height / SPACING);
    return true;
  }

  function draw(now) {
    const elapsed = (now - startedAt) / 1000;
    context.clearRect(0, 0, width, height);
    context.fillStyle = color;

    // The intro ripples outward, so the field assembles rather than blinking on.
    const centreX = columns / 2;
    const centreY = rows / 2;
    const introSpeed = 3.0;

    for (let column = 0; column < columns; column += 1) {
      for (let row = 0; row < rows; row += 1) {
        const offset = noise(column, row);
        const distance = Math.hypot(column - centreX, row - centreY);
        const appearsAt = distance * 0.045 + offset * 0.35;
        if (elapsed * introSpeed < appearsAt) continue;

        const bucket = Math.floor(elapsed / FREQUENCY + offset * FREQUENCY);
        const pick = noise(column + bucket * 7.3, row - bucket * 3.1);
        const alpha = OPACITIES[Math.floor(pick * OPACITIES.length)] * opacity;

        context.globalAlpha = alpha;
        context.fillRect(column * SPACING, row * SPACING, DOT, DOT);
      }
    }
    context.globalAlpha = 1;
  }

  function tick(now) {
    frame = requestAnimationFrame(tick);
    if (now - lastDraw < 1000 / FPS) return;
    lastDraw = now;
    draw(now);
  }

  function start() {
    if (frame || reduced) return;
    frame = requestAnimationFrame(tick);
  }

  function stop() {
    if (frame) cancelAnimationFrame(frame);
    frame = null;
  }

  function mounted() {
    if (!resize()) return;
    startedAt = performance.now();
    if (reduced) {
      // One settled frame: the texture without the movement.
      draw(startedAt + 4000);
      return;
    }
    start();
  }

  // The canvas has no size until it is in the document, so wait for layout.
  const observer =
    typeof ResizeObserver === 'function'
      ? new ResizeObserver(() => {
          if (resize() && reduced) draw(performance.now() + 4000);
        })
      : null;
  observer?.observe(canvas);

  requestAnimationFrame(mounted);

  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') stop();
    else if (canvas.isConnected) start();
  });
  window.addEventListener('pagehide', stop);

  canvas.addEventListener('aloud:teardown', () => {
    stop();
    observer?.disconnect();
  });

  return canvas;
}
