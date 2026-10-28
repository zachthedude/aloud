/**
 * A very small DOM layer.
 *
 * There is no build step in this project, so there is no JSX and no framework
 * runtime. `h` is enough: it builds real elements, sets properties rather than
 * attributes where the DOM prefers them, and understands event handlers.
 *
 * Text goes in as strings and is set via textContent, so nothing in this file
 * can introduce an injection: user and AI text never becomes markup.
 */

import { withBase } from './router.js';

const SVG_NS = 'http://www.w3.org/2000/svg';
// Anything missing here is silently created as an HTML element inside the SVG,
// where it renders nothing. Camel-cased names matter: createElementNS keeps
// the case, createElement does not.
const SVG_TAGS = new Set([
  'svg', 'path', 'circle', 'ellipse', 'line', 'rect', 'g', 'polyline',
  'polygon', 'text', 'tspan', 'defs', 'clipPath', 'mask', 'pattern',
  'linearGradient', 'radialGradient', 'stop', 'use', 'symbol', 'marker',
  'foreignObject', 'filter', 'feGaussianBlur', 'feOffset', 'feMerge',
  'feMergeNode', 'feBlend', 'feColorMatrix',
  // Deliberately not 'title' or 'desc': both also exist in HTML, and the HTML
  // meaning is the one a caller outside an <svg> would expect.
]);

/**
 * @param {string} tag
 * @param {object|null} props
 * @param {...(Node|string|number|false|null|undefined|Array)} children
 * @returns {Element}
 */
export function h(tag, props, ...children) {
  const element = SVG_TAGS.has(tag)
    ? document.createElementNS(SVG_NS, tag)
    : document.createElement(tag);

  if (props) {
    for (const [key, value] of Object.entries(props)) {
      if (value === null || value === undefined || value === false) continue;

      if (key === 'class') {
        element.setAttribute('class', Array.isArray(value) ? value.filter(Boolean).join(' ') : value);
      } else if (key === 'style' && typeof value === 'object') {
        for (const [prop, val] of Object.entries(value)) {
          if (val === null || val === undefined) continue;
          if (prop.startsWith('--')) element.style.setProperty(prop, String(val));
          else element.style[prop] = val;
        }
      } else if (key === 'dataset') {
        for (const [prop, val] of Object.entries(value)) {
          if (val !== null && val !== undefined) element.dataset[prop] = String(val);
        }
      } else if (key.startsWith('on') && typeof value === 'function') {
        element.addEventListener(key.slice(2).toLowerCase(), value);
      } else if (key === 'ref' && typeof value === 'function') {
        value(element);
      } else if (key === 'html') {
        // Only ever called with markup this codebase authored, never with
        // text from a user, a document or the teacher.
        element.innerHTML = value;
      } else if (key === 'href' && typeof value === 'string') {
        element.setAttribute('href', withBase(value));
      } else if (value === true) {
        element.setAttribute(key, '');
      } else {
        element.setAttribute(key, String(value));
      }
    }
  }

  append(element, children);
  return element;
}

function append(parent, children) {
  for (const child of children) {
    if (child === null || child === undefined || child === false || child === '') continue;
    if (Array.isArray(child)) append(parent, child);
    else if (child instanceof Node) parent.appendChild(child);
    else parent.appendChild(document.createTextNode(String(child)));
  }
}

/** Replace everything inside `target` with `content`. */
export function mount(target, content) {
  target.replaceChildren();
  append(target, [content]);
  return target;
}

export const $ = (selector, scope = document) => scope.querySelector(selector);
export const $$ = (selector, scope = document) => Array.from(scope.querySelectorAll(selector));

/** Focus without scrolling the page out from under the user. */
export function focus(element) {
  if (element && typeof element.focus === 'function') {
    element.focus({ preventScroll: true });
  }
}

/**
 * Trap focus inside a container while it is open. Returns a release function.
 * Used by modals so keyboard users cannot tab into the page behind.
 */
export function trapFocus(container, onEscape) {
  const selector =
    'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), ' +
    'textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

  function onKeyDown(event) {
    if (event.key === 'Escape' && onEscape) {
      event.stopPropagation();
      onEscape();
      return;
    }
    if (event.key !== 'Tab') return;
    const focusable = $$(selector, container).filter((el) => el.offsetParent !== null);
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      focus(last);
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      focus(first);
    }
  }

  container.addEventListener('keydown', onKeyDown);
  return () => container.removeEventListener('keydown', onKeyDown);
}

/* -------------------------------------------------------------------------
 * Icons
 *
 * Drawn as a set: 1.6 stroke, round caps, 24-unit grid, so they sit together
 * without looking borrowed from different libraries.
 * ---------------------------------------------------------------------- */

const PATHS = {
  home: 'M3 10.5 12 3l9 7.5M5.5 9.5V20h13V9.5',
  layers: 'M12 3 3 7.5l9 4.5 9-4.5L12 3ZM3 12.5 12 17l9-4.5M3 17 12 21.5 21 17',
  route: 'M19.5 6a2.5 2.5 0 1 1-5 0 2.5 2.5 0 0 1 5 0ZM9.5 15.5a2.5 2.5 0 1 1-5 0 2.5 2.5 0 0 1 5 0ZM17 8.5v2.5a4.5 4.5 0 0 1-4.5 4.5H9.5',
  library: 'M3.5 5.5h4v14h-4zM9.5 5.5h4v14h-4zM16.1 6.7l3.3-.8 2.6 12.7-3.3.8z',
  chart: 'M6 19.5V11M12 19.5V5.5M18 19.5v-5.5M3.5 19.5h17',
  play: 'M8 4.5v15l12-7.5z',
  pause: 'M9 5v14M15 5v14',
  back15: 'M4.8 12a7.2 7.2 0 1 0 7.2-7.2H8.4M11.4 2.2 8.4 4.8l3 2.6',
  fwd15: 'M19.2 12a7.2 7.2 0 1 1-7.2-7.2H15.6M12.6 2.2 15.6 4.8l-3 2.6',
  prev: 'M18 5v14L8 12zM6 5v14',
  next: 'M6 5v14l10-7zM18 5v14',
  mic: 'M12 3a3 3 0 0 1 3 3v6a3 3 0 0 1-6 0V6a3 3 0 0 1 3-3ZM5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21M8.5 21h7',
  chat: 'M4 5.5h16v10H9l-5 4v-4H4z',
  close: 'M6 6l12 12M18 6 6 18',
  chevronDown: 'M6 9.5 12 15l6-5.5',
  chevronLeft: 'M14.5 5 8 12l6.5 7',
  chevronRight: 'M9.5 5 16 12l-6.5 7',
  plus: 'M12 5v14M5 12h14',
  download: 'M12 3v11M7.5 10 12 14.5 16.5 10M4.5 19h15',
  check: 'M4.5 12.5 9.5 17.5 19.5 6.5',
  bookmark: 'M6.5 3.5h11v17l-5.5-4-5.5 4z',
  upload: 'M12 15V4M7.5 8.5 12 4l4.5 4.5M4.5 19h15',
  file: 'M7 3h7l4 4v14H7zM14 3v4h4',
  link: 'M10 13.5a3.5 3.5 0 0 0 5 0l3-3a3.54 3.54 0 0 0-5-5l-1.2 1.2M14 10.5a3.5 3.5 0 0 0-5 0l-3 3a3.54 3.54 0 0 0 5 5l1.2-1.2',
  note: 'M5 4h14v16H5zM8.5 9h7M8.5 13h7M8.5 17h4',
  video: 'M3.5 6.5h12v11h-12zM15.5 10.5 20.5 8v8l-5-2.5z',
  audio: 'M9 18V6l10-2v12M9 18a2.5 2.5 0 1 1-5 0 2.5 2.5 0 0 1 5 0ZM19 16a2.5 2.5 0 1 1-5 0 2.5 2.5 0 0 1 5 0Z',
  trash: 'M4.5 6.5h15M9.5 6.5V4h5v2.5M7 6.5 8 20h8l1-13.5',
  settings: 'M12 15.2a3.2 3.2 0 1 0 0-6.4 3.2 3.2 0 0 0 0 6.4Z M19.4 13.5l1.8 1.1-1.9 3.3-2-.8a7.6 7.6 0 0 1-1.9 1.1l-.3 2.1h-3.8l-.3-2.1a7.6 7.6 0 0 1-1.9-1.1l-2 .8-1.9-3.3 1.8-1.1a7.7 7.7 0 0 1 0-2.2L2.8 9.5 4.7 6.2l2 .8a7.6 7.6 0 0 1 1.9-1.1l.3-2.1h3.8l.3 2.1a7.6 7.6 0 0 1 1.9 1.1l2-.8 1.9 3.3-1.8 1.1a7.7 7.7 0 0 1 0 2.2Z',
  question: 'M9.2 9a2.9 2.9 0 1 1 3.6 2.8c-.6.2-.8.7-.8 1.3v.7M12 17.2v.1',
  info: 'M12 11v6M12 7.5v.1M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Z',
  warn: 'M12 4 2.8 20h18.4L12 4ZM12 10v4.5M12 17.4v.1',
  sparkle: 'M12 3.5 13.8 9 19.5 11 13.8 13 12 18.5 10.2 13 4.5 11 10.2 9zM18.5 16l.8 2.2 2.2.8-2.2.8-.8 2.2-.8-2.2-2.2-.8 2.2-.8z',
  speed: 'M12 20a8 8 0 1 1 8-8M12 12l4.5-4',
  volume: 'M4.5 9.5h3L12 6v12l-4.5-3.5h-3zM16 9.5a4 4 0 0 1 0 5M18.5 7a7.5 7.5 0 0 1 0 10',
  mute: 'M4.5 9.5h3L12 6v12l-4.5-3.5h-3zM16 10l4 4M20 10l-4 4',
  moon: 'M20 14.5A8.5 8.5 0 0 1 9.5 4 8.5 8.5 0 1 0 20 14.5Z',
  sun: 'M12 16.5a4.5 4.5 0 1 0 0-9 4.5 4.5 0 0 0 0 9ZM12 2.5v2M12 19.5v2M4.2 4.2l1.4 1.4M18.4 18.4l1.4 1.4M2.5 12h2M19.5 12h2M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4',
  system: 'M3.5 5h17v11h-17zM9 20h6M12 16v4',
  logout: 'M9 5H5v14h4M15 8l4 4-4 4M19 12H9',
  user: 'M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8ZM4.5 20.5c.7-3.7 3.8-5.5 7.5-5.5s6.8 1.8 7.5 5.5',
  book: 'M4.5 4.5h6a3 3 0 0 1 3 3v13a2.5 2.5 0 0 0-2.5-2.5h-6.5zM19.5 4.5h-6a3 3 0 0 0-3 3v13a2.5 2.5 0 0 1 2.5-2.5h6.5z',
  ear: 'M5 10h3l4-3.5v11L8 14H5zM15.5 9.8a3.2 3.2 0 0 1 0 4.4M18 7.2a6.8 6.8 0 0 1 0 9.6',
  waves: 'M3 12h2M8 7.5v9M12 4.5v15M16 7.5v9M19 12h2',
  refresh: 'M20 11a8 8 0 1 0-.7 4.5M20 5v6h-6',

  // --- The crunch modes -------------------------------------------------
  // Drawn by hand rather than pulled from an icon set: there is no package
  // manager on this machine and the CSP forbids a CDN, so every glyph in the
  // product lives in this file. Same 24 grid, same weight, as everything above.
  clock: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18ZM12 7.5V12l3.2 2',
  flame: 'M12 3.5c3 3.2 5.5 5.6 5.5 9a5.5 5.5 0 0 1-11 0c0-1.6.6-2.9 1.6-4.2.5 1 1.2 1.6 2 1.9-.3-2.6.4-4.9 1.9-6.7Z',
  grid: 'M4 4.5h6v6H4zM14 4.5h6v6h-6zM4 13.5h6v6H4zM14 13.5h6v6h-6z',
  bell: 'M18 16H6l1.4-2.2V10a4.6 4.6 0 0 1 9.2 0v3.8ZM10 19a2 2 0 0 0 4 0',
  stamp: 'M6 20.5h12M7 17.5h10v-2H7zM9.5 15.5v-2.7a3.5 3.5 0 0 1-1-2.5V7a2.5 2.5 0 0 1 5 0v3.3a3.5 3.5 0 0 1-1 2.5v2.7',
  search: 'M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14ZM16 16l4.5 4.5',
  bridge: 'M3 16.5h18M5.5 16.5V12M18.5 16.5V12M3 12a9 9 0 0 1 18 0M9.5 16.5v-3M14.5 16.5v-3',
  mask: 'M4.5 7.5c0-1.1 1-2 2.2-1.8 3.4.6 6.8.6 10.2 0 1.2-.2 2.2.7 2.2 1.8 0 4.8-2.6 9-5.4 10.5a3.9 3.9 0 0 1-3.8 0C7.1 16.5 4.5 12.3 4.5 7.5ZM9 11h.1M15 11h.1',
  camera: 'M4 8.5h3l1.5-2.2h7L17 8.5h3v10H4zM12 16a3.2 3.2 0 1 0 0-6.4 3.2 3.2 0 0 0 0 6.4Z',
  lifebuoy: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18ZM12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7ZM5.7 5.7l3.8 3.8M14.5 14.5l3.8 3.8M18.3 5.7l-3.8 3.8M9.5 14.5l-3.8 3.8',
  walk: 'M13.5 5.2a1.6 1.6 0 1 0 0-3.2 1.6 1.6 0 0 0 0 3.2ZM11 21l1.6-5.4-2.2-2.2.9-4.6L9 10.5 7.5 13M13.4 9.3l2.3 1.8 2.3.4M12.6 15.6 16 21',
  map: 'M9 5 3.5 7v12L9 17l6 2 5.5-2V5L15 7zM9 5v12M15 7v12',
  shield: 'M12 3.5 19.5 6v5.5c0 4.2-3 7.6-7.5 9-4.5-1.4-7.5-4.8-7.5-9V6zM9 12l2.2 2.2L15.5 10',
  swords: 'M4 4h3.2l9 9-3.2 3.2-9-9zM20 4h-3.2l-3.6 3.6M7 15 4.5 17.5l2 2L9 17M17 15l2.5 2.5-2 2L15 17',
  arrowRight: 'M5 12h14M13 6l6 6-6 6',
};

/**
 * @param {keyof typeof PATHS} name
 */
export function icon(name, { size = 20, stroke = 1.6, filled = false, label = '' } = {}) {
  const d = PATHS[name];
  if (!d) return h('span', { 'aria-hidden': 'true' });
  return h(
    'svg',
    {
      viewBox: '0 0 24 24',
      width: size,
      height: size,
      fill: filled ? 'currentColor' : 'none',
      stroke: filled ? 'none' : 'currentColor',
      'stroke-width': stroke,
      'stroke-linecap': 'round',
      'stroke-linejoin': 'round',
      role: label ? 'img' : undefined,
      'aria-label': label || undefined,
      'aria-hidden': label ? undefined : 'true',
    },
    h('path', { d })
  );
}

export const hasIcon = (name) => Boolean(PATHS[name]);
