/**
 * The app shell: side rail on desktop, top bar and tab bar on mobile.
 *
 * Five destinations, no more. Anything contextual (a subject's resources, its
 * knowledge map, the teacher) lives inside the place it belongs to rather than
 * competing for a slot in global navigation.
 */

import { h, icon } from '../core/dom.js';
import { store } from '../core/store.js';
import { navigate } from '../core/router.js';
import { api } from '../core/api.js';
import { notifyError } from '../core/feedback.js';
import { guide } from './guide.js';

const DESTINATIONS = [
  { path: '/app', label: 'Home', icon: 'home' },
  { path: '/spaces', label: 'Subjects', icon: 'layers' },
  { path: '/plan', label: 'Study plan', icon: 'route' },
  { path: '/modes', label: 'Ways of working', icon: 'grid' },
  { path: '/library', label: 'Library', icon: 'library' },
  { path: '/progress', label: 'Progress', icon: 'chart' },
];

function isCurrent(path, currentPath) {
  if (path === '/app') return currentPath === '/app' || currentPath === '/';
  return currentPath === path || currentPath.startsWith(`${path}/`);
}

export function brand({ compact = false } = {}) {
  return h(
    'a',
    { href: '/app', class: 'rail-brand', style: { textDecoration: 'none' } },
    h(
      'svg',
      { viewBox: '0 0 24 24', width: compact ? 20 : 22, height: compact ? 20 : 22, 'aria-hidden': 'true' },
      h('circle', {
        cx: 12, cy: 12, r: 10,
        fill: 'none', stroke: 'currentColor', 'stroke-width': 1.2, opacity: 0.3,
      }),
      h('path', {
        d: 'M12 5.5v13M8 8.2v7.6M16 8.2v7.6M4.6 10.6v2.8M19.4 10.6v2.8',
        stroke: 'currentColor', 'stroke-width': 1.6, 'stroke-linecap': 'round', fill: 'none',
      })
    ),
    h('span', null, 'Aloud')
  );
}

/* --- Theme ---------------------------------------------------------------
   One button that cycles light, dark and system, rather than a switch that
   cannot express "follow my device". The icon shows the mode you are in, and
   the label says what pressing it will do, so the control is legible without
   hovering and to a screen reader. -------------------------------------- */

const THEME_ICON = { light: 'sun', dark: 'moon', system: 'system' };
const THEME_LABEL = { light: 'Light', dark: 'Dark', system: 'Match device' };

function nextThemeMode(mode) {
  const order = ['light', 'dark', 'system'];
  return order[(order.indexOf(mode) + 1) % order.length];
}

export function themeToggle({ compact = false } = {}) {
  // Not named `api`: that is the imported HTTP client at module scope, and
  // shadowing it here would be a trap for the next person editing this file.
  const theme = window.__aloudTheme;
  if (!theme) return null;

  const button = h('button', {
    class: compact ? 'btn-icon' : 'nav-link theme-toggle',
    type: 'button',
  });

  function paint() {
    const mode = theme.mode;
    const next = nextThemeMode(mode);
    // Filtered, not passed conditionally: replaceChildren stringifies a null
    // into the literal text "null" rather than skipping it.
    const parts = [icon(THEME_ICON[mode] || 'system', { size: compact ? 18 : 17 })];
    if (!compact) parts.push(h('span', null, THEME_LABEL[mode]));
    button.replaceChildren(...parts);
    button.setAttribute('aria-label', `Appearance: ${THEME_LABEL[mode]}. Switch to ${THEME_LABEL[next]}.`);
    button.title = `Appearance: ${THEME_LABEL[mode]}`;
  }

  button.addEventListener('click', () => theme.set(nextThemeMode(theme.mode)));
  window.addEventListener('aloud:theme', paint);
  paint();
  return button;
}

async function signOut() {
  try {
    await api.auth.signOut();
  } catch (error) {
    notifyError(error);
    return;
  }
  store.set({ user: null, spaces: [], dashboard: null, spaceDetail: null });
  navigate('/');
}

function accountButton(user) {
  const initial = (user?.displayName || user?.email || '?').trim().charAt(0).toUpperCase();
  return h(
    'a',
    {
      href: '/settings',
      class: 'nav-link',
      style: { textDecoration: 'none' },
      'aria-label': 'Settings and account',
    },
    h('span', { class: 'avatar', 'aria-hidden': 'true' }, initial),
    h(
      'span',
      { class: 'truncate', style: { minWidth: 0 } },
      user?.displayName || 'Your account'
    )
  );
}

export function rail(currentPath) {
  const user = store.get().user;
  return h(
    'nav',
    { class: 'rail', 'aria-label': 'Main' },
    brand(),
    h(
      'ul',
      { class: 'rail-nav' },
      DESTINATIONS.map((item) =>
        h(
          'li',
          null,
          h(
            'a',
            {
              href: item.path,
              class: 'nav-link',
              'aria-current': isCurrent(item.path, currentPath) ? 'page' : null,
            },
            icon(item.icon),
            item.label
          )
        )
      )
    ),
    h(
      'div',
      { class: 'rail-foot stack stack-sm' },
      h(
        'a',
        { href: '/spaces/new', class: 'btn btn-outline btn-block', style: { justifyContent: 'flex-start' } },
        icon('plus', { size: 16 }),
        'New subject'
      ),
      themeToggle(),
      accountButton(user)
    )
  );
}

export function topbar() {
  return h(
    'header',
    { class: 'topbar' },
    brand({ compact: true }),
    h(
      'div',
      { class: 'row', style: { gap: 'var(--space-1)' } },
      themeToggle({ compact: true }),
      h(
        'a',
        { href: '/spaces/new', class: 'btn btn-ghost btn-sm', 'aria-label': 'New subject' },
        icon('plus', { size: 16 }),
        'New'
      )
    )
  );
}

export function tabbar(currentPath) {
  return h(
    'nav',
    { class: 'tabbar', 'aria-label': 'Main' },
    h(
      'div',
      { class: 'tabbar-inner' },
      DESTINATIONS.map((item) =>
        h(
          'a',
          {
            href: item.path,
            class: 'tab',
            'aria-current': isCurrent(item.path, currentPath) ? 'page' : null,
          },
          icon(item.icon, { size: 19 }),
          h('span', null, ({ 'Study plan': 'Plan', 'Ways of working': 'Modes' }[item.label] || item.label))
        )
      )
    )
  );
}

/** Wrap a page in the shell. */
export function shell(currentPath, content) {
  return h(
    'div',
    { class: 'shell' },
    rail(currentPath),
    h(
      'div',
      { style: { minWidth: 0 } },
      topbar(),
      h('main', { class: 'shell-main', id: 'main' }, h('div', { class: 'container' }, content))
    ),
    tabbar(currentPath),
    // Hum lives outside the scrolling main, so it stays put while a page
    // moves under it and is reached last in the tab order.
    guideFor(currentPath)
  );
}

/** One guide, kept alive across navigations so it does not blink on route change. */
let livingGuide = null;

function guideFor(currentPath) {
  if (livingGuide === null) livingGuide = guide({ onClose: () => { livingGuide = null; } });
  if (livingGuide) livingGuide.__say(currentPath);
  return livingGuide;
}

export { signOut };
