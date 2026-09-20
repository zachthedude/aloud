/**
 * Bootstrap.
 *
 * Resolves the session, registers routes, and renders. The player and mini
 * player are mounted outside the page container so navigating between screens
 * never interrupts narration.
 */

import { h, mount, $ } from './core/dom.js';
import { api } from './core/api.js';
import { store, player } from './core/store.js';
import { route, start, navigate, currentRoute } from './core/router.js';
import { shell } from './components/shell.js';
import { playerView, miniPlayer, updatePlayerLive } from './components/playerview.js';
import { lessonPlayer } from './core/lessonplayer.js';
import { landingView } from './views/landing.js';
import { signInView, signUpView } from './views/auth.js';
import { welcomeView } from './views/welcome.js';
import { dashboardView } from './views/dashboard.js';
import { spacesView, newSpaceView, spaceView } from './views/space.js';
import { planView } from './views/plan.js';
import { libraryView } from './views/library.js';
import { progressView } from './views/progress.js';
import { settingsView } from './views/settings.js';
import { errorState } from './components/pieces.js';
import { announce } from './core/feedback.js';

const root = document.getElementById('app');
const playerLayer = document.getElementById('player-layer');

/** Pages that do not require an account. */
const PUBLIC = new Set(['/', '/signin', '/signup']);

/* --- Page rendering ----------------------------------------------------- */

/**
 * Render a view inside the app shell.
 *
 * Views receive a container to fill and a re-render callback, so an error
 * state can offer a working "try again" without the router knowing about it.
 */
function inShell(render, { title } = {}) {
  return async (context) => {
    const container = h('div');
    await withTransition(() => mount(root, shell(currentRoute().path, container)));
    document.title = title ? `${title} · Aloud` : 'Aloud';

    const rerender = () => runView(render, container, context, rerender);
    await runView(render, container, context, rerender);
    announcePage(title);
  };
}

async function runView(render, container, context, rerender) {
  try {
    await render(container, context, rerender);
  } catch (error) {
    console.error(error);
    mount(container, errorState(error.message || 'Something went wrong.', rerender));
  }
}

/**
 * Cross-fade the page on navigation, where the browser can do it natively.
 *
 * The motion is motivated: it says the content changed, which is the one thing
 * a client-side route change otherwise fails to communicate. `startViewTransition`
 * is a browser API, so there is no animation library here and no work at all on
 * browsers that do not have it. Reduced motion skips it entirely.
 */
function withTransition(update) {
  const reduced =
    typeof matchMedia === 'function' &&
    matchMedia('(prefers-reduced-motion: reduce)').matches;

  if (reduced || typeof document.startViewTransition !== 'function') {
    return update();
  }
  return document.startViewTransition(update).updateCallbackDone;
}

function bare(render, { title } = {}) {
  return async (context) => {
    const container = h('div');
    await withTransition(() => mount(root, container));
    document.title = title ? `${title} · Aloud` : 'Aloud';
    await runView(render, container, context, () => runView(render, container, context, () => {}));
    announcePage(title);
  };
}

let lastAnnounced = '';
function announcePage(title) {
  if (!title || title === lastAnnounced) return;
  lastAnnounced = title;
  announce(`${title} page`);
}

/* --- Routes -------------------------------------------------------------- */

route('/', bare((container) => mount(container, landingView()), { title: '' }));
route('/signin', bare((container) => mount(container, signInView()), { title: 'Sign in' }));
route('/signup', bare((container) => mount(container, signUpView()), { title: 'Create account' }));

route('/welcome', bare((container) => welcomeView(container), { title: 'Welcome' }));

route('/app', inShell((container, _context, rerender) => dashboardView(container, rerender), { title: 'Home' }));
route('/spaces', inShell((container, _context, rerender) => spacesView(container, rerender), { title: 'Subjects' }));
route('/spaces/new', inShell((container) => newSpaceView(container), { title: 'New subject' }));
route(
  '/space/:id',
  inShell(
    (container, context, rerender) =>
      spaceView(container, context.params.id, context.query, rerender),
    { title: 'Subject' }
  )
);
route('/plan', inShell((container, _context, rerender) => planView(container, rerender), { title: 'Study plan' }));
route('/library', inShell((container, _context, rerender) => libraryView(container, rerender), { title: 'Library' }));
route('/progress', inShell((container, _context, rerender) => progressView(container, rerender), { title: 'Progress' }));
route('/settings', inShell((container) => settingsView(container), { title: 'Settings' }));

/* --- Player layer -------------------------------------------------------- */

function renderPlayerLayer() {
  const state = player();
  playerLayer.replaceChildren();
  if (state.open) {
    playerLayer.appendChild(playerView());
    document.body.style.overflow = 'hidden';
  } else {
    document.body.style.removeProperty('overflow');
    const mini = miniPlayer();
    if (mini) playerLayer.appendChild(mini);
  }
}

/**
 * Two subscriptions, not one.
 *
 * Narration reports progress on every animation frame. Rebuilding the player
 * that often restarts its entrance animation and throws away focus while a
 * student is typing an answer, so only a change of structure rebuilds; the
 * moving parts are patched in place.
 */
store.subscribe(renderPlayerLayer, (state) => {
  const p = state.player;
  return {
    open: p.open,
    lessonId: p.lessonId,
    lesson: p.lesson,
    sections: p.sections,
    state: p.state,
    speaking: p.speaking,
    sectionIndex: p.sectionIndex,
    question: p.question,
    reply: p.reply,
    finished: p.finished,
    showTranscript: p.showTranscript,
    listening: p.listening,
    speed: p.speed,
    muted: p.muted,
    error: p.error,
    audioStatus: p.audioStatus,
  };
});

store.subscribe(updatePlayerLive, (state) => ({
  elapsed: state.player.elapsed,
  level: state.player.level,
  partial: state.player.partial,
}));

/* --- Session ------------------------------------------------------------- */

async function resolveSession() {
  try {
    const me = await api.auth.me();
    store.set({ user: me.user, providers: me.providers, ready: true });
    return me.user;
  } catch {
    store.set({ user: null, ready: true });
    return null;
  }
}

async function onNavigate(handler, context) {
  const state = store.get();

  if (!state.ready) await resolveSession();
  const user = store.get().user;
  const path = context.path;

  if (!handler) {
    // Unknown path: send people somewhere useful rather than to a dead end.
    navigate(user ? '/app' : '/', { replace: true });
    return;
  }

  if (!user && !PUBLIC.has(path)) {
    navigate('/signin', { replace: true });
    return;
  }
  if (user && PUBLIC.has(path) && path !== '/') {
    navigate('/app', { replace: true });
    return;
  }

  await handler(context);
  window.scrollTo({ top: 0, behavior: 'auto' });
}

/* --- Go ------------------------------------------------------------------ */

start(onNavigate);
renderPlayerLayer();
