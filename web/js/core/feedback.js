/**
 * Toasts, confirmations and screen-reader announcements.
 *
 * One live region carries anything a sighted user learns from a visual
 * change: the teacher starting to speak, a state moving to "your turn", a
 * lesson finishing. A voice-first product that is silent to a screen reader
 * would be a poor joke.
 */

import { h, mount, trapFocus, focus } from './dom.js';

let toastLayer = null;
let liveRegion = null;
let assertiveRegion = null;

function ensureLayers() {
  if (!toastLayer) {
    toastLayer = h('div', { class: 'toasts', id: 'toasts' });
    document.body.appendChild(toastLayer);
  }
  if (!liveRegion) {
    liveRegion = h('div', {
      class: 'visually-hidden',
      role: 'status',
      'aria-live': 'polite',
      'aria-atomic': 'true',
    });
    assertiveRegion = h('div', {
      class: 'visually-hidden',
      role: 'alert',
      'aria-live': 'assertive',
      'aria-atomic': 'true',
    });
    document.body.append(liveRegion, assertiveRegion);
  }
}

/** Announce to assistive technology without showing anything. */
export function announce(message, { assertive = false } = {}) {
  ensureLayers();
  const region = assertive ? assertiveRegion : liveRegion;
  // Clearing first forces a re-announcement of identical text.
  region.textContent = '';
  window.setTimeout(() => {
    region.textContent = message;
  }, 60);
}

export function toast(message, { tone = 'neutral', duration = 5000, action } = {}) {
  ensureLayers();

  const node = h(
    'div',
    { class: ['toast', tone === 'trouble' && 'toast-trouble'], role: 'status' },
    h('span', { class: 'spacer' }, message),
    action &&
      h(
        'button',
        {
          class: 'strong',
          onClick: () => {
            action.onClick?.();
            dismiss();
          },
        },
        action.label
      ),
    h(
      'button',
      { 'aria-label': 'Dismiss', onClick: () => dismiss() },
      h('span', { 'aria-hidden': 'true' }, '×')
    )
  );

  let timer = null;
  function dismiss() {
    clearTimeout(timer);
    node.style.transition = 'opacity 160ms, transform 160ms';
    node.style.opacity = '0';
    node.style.transform = 'translateY(6px)';
    setTimeout(() => node.remove(), 170);
  }

  toastLayer.appendChild(node);
  announce(message, { assertive: tone === 'trouble' });
  if (duration) timer = setTimeout(dismiss, duration);
  return dismiss;
}

export const notifyError = (error) =>
  toast(typeof error === 'string' ? error : error?.message || 'Something went wrong.', {
    tone: 'trouble',
    duration: 7000,
  });

/**
 * A modal. Returns a promise that resolves true on confirm, false otherwise.
 */
export function confirmDialog({
  title,
  body = '',
  confirmLabel = 'Confirm',
  cancelLabel = 'Cancel',
  danger = false,
}) {
  return new Promise((resolve) => {
    const previous = document.activeElement;
    let release = null;

    function close(result) {
      release?.();
      backdrop.remove();
      document.body.style.removeProperty('overflow');
      focus(previous);
      resolve(result);
    }

    const dialog = h(
      'div',
      { class: 'modal', role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': 'confirm-title' },
      h('h2', { id: 'confirm-title' }, title),
      body && h('p', { class: 'muted', style: { marginTop: 'var(--space-2)' } }, body),
      h(
        'div',
        { class: 'modal-actions' },
        h('button', { class: 'btn btn-ghost', onClick: () => close(false) }, cancelLabel),
        h(
          'button',
          {
            class: danger ? 'btn btn-primary' : 'btn btn-primary',
            style: danger ? { background: 'var(--trouble)' } : null,
            onClick: () => close(true),
          },
          confirmLabel
        )
      )
    );

    const backdrop = h(
      'div',
      {
        class: 'modal-backdrop',
        onClick: (event) => {
          if (event.target === backdrop) close(false);
        },
      },
      dialog
    );

    document.body.appendChild(backdrop);
    document.body.style.overflow = 'hidden';
    release = trapFocus(dialog, () => close(false));
    focus(dialog.querySelector('.btn-primary'));
  });
}

/** A modal with arbitrary content. `render` receives a `close` function. */
export function openModal(render, { labelledBy = '' } = {}) {
  const previous = document.activeElement;
  let release = null;

  function close() {
    release?.();
    backdrop.remove();
    document.body.style.removeProperty('overflow');
    focus(previous);
  }

  const dialog = h('div', {
    class: 'modal',
    role: 'dialog',
    'aria-modal': 'true',
    'aria-labelledby': labelledBy || undefined,
  });
  mount(dialog, render(close));

  const backdrop = h(
    'div',
    {
      class: 'modal-backdrop',
      onClick: (event) => {
        if (event.target === backdrop) close();
      },
    },
    dialog
  );

  document.body.appendChild(backdrop);
  document.body.style.overflow = 'hidden';
  release = trapFocus(dialog, close);

  const first = dialog.querySelector('input, textarea, select, button');
  focus(first || dialog);
  return close;
}
