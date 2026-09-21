/**
 * The offline shell.
 *
 * A student on a train loses signal mid-lesson. Narration is the browser's own
 * speech engine, so it keeps talking regardless; what breaks is the next
 * navigation, which would otherwise be a dinosaur.
 *
 * Deliberately narrow. Only the shell is cached: the HTML, the stylesheets,
 * the modules and the fonts. Lessons, progress and anything under /api are
 * never cached, because a stale answer about what you know is worse than an
 * honest "you are offline". The app already has an offline message for that.
 *
 * Cache-first for the shell because those files are versioned by this cache
 * name and change only on deploy. Network-only for everything else.
 */

const CACHE = 'aloud-shell-v1';

const SHELL = [
  '/',
  '/index.html',
  '/js/theme.js',
  '/styles/tokens.css',
  '/styles/base.css',
  '/styles/components.css',
  '/styles/app.css',
  '/styles/landing.css',
  '/styles/player.css',
  '/assets/icon.svg',
  '/assets/fonts/figtree.css',
  '/assets/fonts/newsreader.css',
];

self.addEventListener('install', (event) => {
  // One missing file must not fail the whole install, or a renamed asset
  // silently leaves every student without an offline shell.
  event.waitUntil(
    caches.open(CACHE).then((cache) =>
      Promise.all(SHELL.map((url) => cache.add(url).catch(() => {})))
    )
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((names) =>
      Promise.all(names.filter((name) => name !== CACHE).map((name) => caches.delete(name)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  const { request } = event;
  if (request.method !== 'GET') return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith('/api/')) return;

  // A navigation to any route renders the same shell, so serve it from cache
  // when the network is gone rather than failing the page.
  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request).catch(() => caches.match('/index.html').then((hit) => hit || Response.error()))
    );
    return;
  }

  const isShell =
    url.pathname.startsWith('/js/') ||
    url.pathname.startsWith('/styles/') ||
    url.pathname.startsWith('/assets/');
  if (!isShell) return;

  event.respondWith(
    caches.match(request).then(
      (hit) =>
        hit ||
        fetch(request).then((response) => {
          if (response.ok) {
            const copy = response.clone();
            caches.open(CACHE).then((cache) => cache.put(request, copy));
          }
          return response;
        })
    )
  );
});
