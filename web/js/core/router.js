/**
 * Client routing over the History API.
 *
 * The server returns the app shell for every client route, so a refresh on
 * /space/spc_123 works and links are real URLs that can be shared and
 * bookmarked.
 */

const routes = [];
let onNavigate = null;
let current = { path: '/', params: {}, query: new URLSearchParams() };

/**
 * @param {string} pattern e.g. '/space/:id'
 * @param {(context: {params: object, query: URLSearchParams}) => any} handler
 */
export function route(pattern, handler) {
  const names = [];
  const regex = new RegExp(
    '^' +
      pattern
        .replace(/\/:([A-Za-z0-9_]+)/g, (_match, name) => {
          names.push(name);
          return '/([^/]+)';
        })
        .replace(/\/$/, '') +
      '/?$'
  );
  routes.push({ regex, names, handler, pattern });
}

export function resolve(path) {
  const clean = path.split('?')[0].split('#')[0] || '/';
  for (const entry of routes) {
    const match = clean.match(entry.regex);
    if (!match) continue;
    const params = {};
    entry.names.forEach((name, index) => {
      params[name] = decodeURIComponent(match[index + 1]);
    });
    return { entry, params };
  }
  return null;
}

export function navigate(path, { replace = false, state = {} } = {}) {
  if (path === location.pathname + location.search) {
    return render();
  }
  if (replace) history.replaceState(state, '', path);
  else history.pushState(state, '', path);
  return render();
}

export function currentRoute() {
  return current;
}

export async function render() {
  const path = location.pathname || '/';
  const query = new URLSearchParams(location.search);
  const match = resolve(path);

  current = { path, params: match ? match.params : {}, query };

  if (!match) {
    onNavigate?.(null, current);
    return;
  }
  await onNavigate?.(match.entry.handler, current);
}

export function start(handler) {
  onNavigate = handler;

  window.addEventListener('popstate', () => render());

  // Intercept in-app links so navigation stays client-side, while leaving
  // downloads, new tabs, modified clicks and external links to the browser.
  document.addEventListener('click', (event) => {
    if (event.defaultPrevented || event.button !== 0) return;
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;

    const anchor = event.target.closest?.('a[href]');
    if (!anchor) return;
    if (anchor.target && anchor.target !== '_self') return;
    if (anchor.hasAttribute('download')) return;
    if (anchor.dataset.native !== undefined) return;

    const href = anchor.getAttribute('href');
    if (!href || href.startsWith('http') || href.startsWith('//') || href.startsWith('mailto:')) {
      return;
    }
    if (href.startsWith('#')) return;

    event.preventDefault();
    navigate(href);
  });

  return render();
}
