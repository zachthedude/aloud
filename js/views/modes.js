/**
 * Every way of working, on one screen.
 *
 * Sixteen modes over one voice loop and one learner model. This screen shows
 * all of them, including the ones still being built, because a student
 * deciding whether to trust an hour of revision to this should be able to see
 * the whole shape of it. Nothing here pretends: a mode that does not exist
 * yet says so and cannot be switched on.
 *
 * The toggle is a real checkbox behind the card, so the whole card is one hit
 * target, keyboard reaches it in one tab, and a screen reader announces the
 * mode name and its state together.
 */

import { h, icon, mount } from '../core/dom.js';
import { api } from '../core/api.js';
import { store } from '../core/store.js';
import { notifyError } from '../core/feedback.js';

const STATE_LABEL = {
  on: 'On',
  off: 'Off',
  blocked: 'Needs setup',
  coming: 'Being built',
};

function stateNote(mode) {
  if (mode.reasons.length) return mode.reasons[0];
  if (mode.state === 'coming') return 'Not built yet. It will switch on here when it lands.';
  return '';
}

function modeCard(mode, onToggle, spaceId) {
  const interactive = mode.state === 'on' || mode.state === 'off';
  const note = stateNote(mode);

  const control = interactive
    ? h('input', {
        type: 'checkbox',
        class: 'mode-switch',
        checked: mode.state === 'on',
        id: `mode-${mode.key}`,
        onChange: (event) => onToggle(mode.key, event.target.checked),
      })
    : null;

  return h(
    'li',
    { class: 'mode-card', dataset: { state: mode.state } },
    h(
      'div',
      { class: 'mode-head' },
      h('span', { class: 'mode-icon' }, icon(mode.icon, { size: 19 })),
      control,
      interactive
        ? h('label', { class: 'mode-name', for: `mode-${mode.key}` }, mode.name)
        : h('span', { class: 'mode-name' }, mode.name),
      h('span', { class: 'mode-state' }, STATE_LABEL[mode.state])
    ),
    h('p', { class: 'mode-blurb' }, mode.blurb),
    note ? h('p', { class: 'mode-note' }, note) : null,
    mode.state === 'on' && spaceId
      ? h(
          'a',
          {
            class: 'btn btn-primary btn-sm mode-start',
            href: `/room/${spaceId}/${mode.key}`,
            'aria-label': `Start ${mode.name}`,
          },
          icon('play', { size: 13, filled: true }),
          'Start'
        )
      : null
  );
}

const SUBJECT_KEY = 'aloud-crunch-subject';

function rememberedSubject() {
  try {
    return localStorage.getItem(SUBJECT_KEY) || '';
  } catch (error) {
    return '';
  }
}

export async function modesView(container) {
  const list = h('ul', { class: 'mode-grid' });
  const summary = h('p', { class: 'lede' });
  const subjectSlot = h('div');
  let spaceId = '';
  let latest = [];

  async function toggle(key, on) {
    const user = store.get().user || {};
    const muted = new Set(user.mutedModes || []);
    if (on) muted.delete(key);
    else muted.add(key);
    try {
      const result = await api.auth.updateMe({ mutedModes: [...muted] });
      store.set({ user: result.user });
      await load();
    } catch (error) {
      notifyError(error);
      await load();
    }
  }

  async function load() {
    try {
      const { modes } = await api.auth.modes();
      const ready = modes.filter((mode) => mode.state === 'on').length;
      const coming = modes.filter((mode) => mode.state === 'coming').length;
      summary.textContent =
        `${ready} ready to use now. ${coming} still being built. ` +
        'Every one of them listens, speaks and learns from the same place, so what you do in one counts in all of them.';
      latest = modes;
      list.replaceChildren(...modes.map((mode) => modeCard(mode, toggle, spaceId)));
    } catch (error) {
      notifyError(error);
    }
  }

  async function loadSubjects() {
    let spaces = [];
    try {
      ({ spaces } = await api.spaces.list());
    } catch (error) {
      notifyError(error);
    }
    const ready = spaces.filter((space) => space.status === 'ready');
    const wanted = rememberedSubject();
    spaceId = (ready.find((space) => space.id === wanted) || ready[0] || {}).id || '';

    if (!ready.length) {
      subjectSlot.replaceChildren(
        h('p', { class: 'muted' },
          'Every mode works on a subject. ',
          h('a', { href: '/spaces/new', class: 'strong' }, 'Add your material'),
          ' and the ones that are on can start.')
      );
      return;
    }
    if (ready.length === 1) {
      subjectSlot.replaceChildren(h('p', { class: 'muted' }, `Working on ${ready[0].title}.`));
      return;
    }
    subjectSlot.replaceChildren(
      h(
        'label',
        { class: 'mode-subject' },
        h('span', { class: 'muted' }, 'Working on'),
        h(
          'select',
          {
            onChange: (event) => {
              spaceId = event.target.value;
              try { localStorage.setItem(SUBJECT_KEY, spaceId); } catch (error) { /* survivable */ }
              list.replaceChildren(...latest.map((mode) => modeCard(mode, toggle, spaceId)));
            },
          },
          ready.map((space) => h('option', { value: space.id, selected: space.id === spaceId }, space.title))
        )
      )
    );
  }

  mount(
    container,
    h(
      'div',
      { class: 'stack stack-lg' },
      h('div', { class: 'page-head' }, h('h1', null, 'Ways of working'), summary, subjectSlot),
      list
    )
  );
  await loadSubjects();
  await load();
}
