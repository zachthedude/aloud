/**
 * Theme resolution, loaded synchronously in <head>.
 *
 * Runs before first paint so a dark-mode student never gets a white flash on
 * the way in. It resolves the three-way choice (light / dark / system) down to
 * an explicit `data-theme` attribute, which is why `tokens.css` needs only one
 * light block and one dark block instead of a media query duplicating both.
 *
 * Not an ES module: a `type="module"` script is deferred by definition, which
 * would put it after first paint and reintroduce the flash. It is a separate
 * file rather than an inline script because the CSP here allows `script-src
 * 'self'` and no inline.
 */
(function () {
  var KEY = 'aloud-theme';
  var MODES = ['light', 'dark', 'system'];

  function stored() {
    try {
      var value = localStorage.getItem(KEY);
      return MODES.indexOf(value) === -1 ? 'system' : value;
    } catch (error) {
      // Private mode, or site data blocked. System preference is a fine default.
      return 'system';
    }
  }

  function systemPrefersDark() {
    return (
      typeof matchMedia === 'function' &&
      matchMedia('(prefers-color-scheme: dark)').matches
    );
  }

  function apply(mode) {
    var resolved = mode === 'system' ? (systemPrefersDark() ? 'dark' : 'light') : mode;
    document.documentElement.setAttribute('data-theme', resolved);
    document.documentElement.setAttribute('data-theme-mode', mode);
    return resolved;
  }

  var current = stored();
  apply(current);

  // Exposed for the app's toggle. Kept tiny and framework-free so it can run
  // this early without importing anything.
  window.__aloudTheme = {
    get mode() {
      return document.documentElement.getAttribute('data-theme-mode') || 'system';
    },
    get resolved() {
      return document.documentElement.getAttribute('data-theme') || 'light';
    },
    set: function (mode) {
      if (MODES.indexOf(mode) === -1) mode = 'system';
      try {
        localStorage.setItem(KEY, mode);
      } catch (error) {
        // Not persisting is survivable; the choice still applies this session.
      }
      var resolved = apply(mode);
      window.dispatchEvent(
        new CustomEvent('aloud:theme', { detail: { mode: mode, resolved: resolved } })
      );
      return resolved;
    },
    modes: MODES,
  };

  // Follow the operating system while the student has not chosen for themselves.
  if (typeof matchMedia === 'function') {
    var query = matchMedia('(prefers-color-scheme: dark)');
    var onChange = function () {
      if (window.__aloudTheme.mode !== 'system') return;
      var resolved = apply('system');
      window.dispatchEvent(
        new CustomEvent('aloud:theme', { detail: { mode: 'system', resolved: resolved } })
      );
    };
    if (query.addEventListener) query.addEventListener('change', onChange);
    else if (query.addListener) query.addListener(onChange);
  }
})();
