/**
 * First run.
 *
 * Not a questionnaire. It says what is about to happen in three lines and gets
 * out of the way, because the first useful thing should happen in under a
 * minute. The only choice offered is the one that matters: your own material,
 * or the sample subject to see how it works first.
 */

import { h, icon } from '../core/dom.js';
import { navigate } from '../core/router.js';
import { store } from '../core/store.js';
import { api } from '../core/api.js';
import { resonanceFigure } from '../core/resonance.js';
import { narrator } from '../core/narrator.js';
import { notifyError } from '../core/feedback.js';

const STEPS = [
  ['Add what you already study from', 'PDFs, slides, notes, a web page, or a recording of the lecture itself.'],
  ['It gets read and organised', 'The concepts inside are pulled out and put in the order they build on each other.'],
  ['Then you listen', 'A teacher explains each one, asks you about it, and listens to your answer.'],
];

export function welcomeView(mountPoint) {
  const user = store.get().user;

  async function markOnboarded() {
    try {
      await api.auth.updateMe({ onboarded: true });
      store.set((state) => (state.user ? { user: { ...state.user, onboarded: true } } : {}));
    } catch {
      /* not worth blocking the student over */
    }
  }

  function begin() {
    markOnboarded();
    navigate('/spaces/new');
  }

  async function trySample() {
    markOnboarded();
    try {
      const { spaces } = await api.spaces.list();
      if (spaces.length) {
        navigate(`/space/${spaces[0].id}`);
        return;
      }
    } catch (error) {
      notifyError(error);
    }
    navigate('/spaces/new');
  }

  const figure = resonanceFigure('aloud-welcome', { active: false });

  mountPoint.replaceChildren(
    h(
      'div',
      { class: 'welcome' },
      h(
        'div',
        { class: 'welcome-figure night' },
        figure,
        h(
          'button',
          {
            class: 'btn btn-sm welcome-hear',
            onClick: () => {
              figure.dataset.active = 'true';
              narrator.onEnd = () => { figure.dataset.active = 'false'; };
              narrator.speak(
                user?.displayName
                  ? `Hello ${user.displayName}. When you are ready, we will start with whatever you are studying.`
                  : 'Hello. When you are ready, we will start with whatever you are studying.'
              );
            },
          },
          icon('ear', { size: 14 }),
          'Hear your teacher'
        )
      ),

      h(
        'div',
        { class: 'welcome-copy stack stack-lg' },
        h(
          'div',
          null,
          h('h1', { class: 'display', style: { fontSize: 'var(--text-3xl)', marginBottom: 'var(--space-3)' } },
            user?.displayName ? `Welcome, ${user.displayName}` : 'Welcome'),
          h(
            'p',
            { class: 'lede' },
            'You provide the material. It becomes a teacher who knows your course, ' +
              'notices when you are lost, and never rushes you.'
          )
        ),

        h(
          'ol',
          { class: 'welcome-steps' },
          STEPS.map(([title, body], index) =>
            h(
              'li',
              null,
              h('span', { class: 'welcome-step-number' }, String(index + 1)),
              h('div', null, h('h3', null, title), h('p', { class: 'muted' }, body))
            )
          )
        ),

        h(
          'div',
          { class: 'row row-wrap', style: { gap: 'var(--space-3)' } },
          h('button', { class: 'btn btn-primary btn-lg', onClick: begin },
            icon('plus', { size: 16 }), 'Add my material'),
          h('button', { class: 'btn btn-outline btn-lg', onClick: trySample },
            'Look around first')
        ),

        h(
          'p',
          { class: 'meta' },
          'Nothing here counts down, and nothing is ever overdue.'
        )
      )
    )
  );
}
