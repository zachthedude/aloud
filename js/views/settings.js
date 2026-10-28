/**
 * Settings.
 *
 * Also where the product is honest about itself: which teaching engine is
 * running, whether audio files can be made, and whether this browser can
 * listen. A student who cannot download a lesson should be able to find out
 * why in one place rather than guessing at a disabled button.
 */

import { h, icon } from '../core/dom.js';
import { api } from '../core/api.js';
import { store } from '../core/store.js';
import { narrator } from '../core/narrator.js';
import { listener, canListenInBrowser } from '../core/listener.js';
import { lessonPlayer, SPEEDS } from '../core/lessonplayer.js';
import { signOut } from '../components/shell.js';
import { toast, notifyError, confirmDialog } from '../core/feedback.js';
import { sectionHead } from '../components/pieces.js';

const STYLES = [
  ['balanced', 'Balanced', 'Precise explanation with concrete examples.'],
  ['concrete', 'Lead with examples', 'Start from something real, then name the idea.'],
  ['formal', 'Keep it precise', 'Rigorous and technical. Do not simplify away the detail.'],
];

function statusRow(label, value, ok, note) {
  return h(
    'div',
    { class: 'status-row' },
    h(
      'div',
      { style: { minWidth: 0 } },
      h('p', { class: 'strong' }, label),
      h('p', { class: 'meta' }, note)
    ),
    h(
      'span',
      { class: ok ? 'chip chip-mint' : 'chip' },
      value
    )
  );
}

/**
 * Delete everything inferred about the student, in one request.
 *
 * Asked once, because it cannot be undone and the student is usually looking
 * for this button precisely when they are not sure they want to press it.
 */
async function forgetEverything() {
  const ok = await confirmDialog({
    title: 'Forget what Aloud knows about you?',
    body:
      'Every answer you have given, what you said, and what Aloud worked out about ' +
      'which topics you know. Your uploads and lessons are kept. This cannot be undone.',
    confirmLabel: 'Forget it all',
    danger: true,
  });
  if (!ok) return;
  try {
    await api.learner.forget();
    toast('Done. Aloud is starting from nothing about you.');
  } catch (error) {
    notifyError(error);
  }
}

export async function settingsView(mountPoint) {
  const user = store.get().user;
  if (!user) return;

  let providers = store.get().providers;
  if (!providers) {
    try {
      const me = await api.auth.me();
      providers = me.providers;
      store.set({ user: me.user, providers });
    } catch {
      providers = null;
    }
  }

  let displayName = null;

  async function savePreferences(patch, message) {
    try {
      const { user: updated } = await api.auth.updateMe(patch);
      store.set({ user: updated });
      if (message) toast(message);
    } catch (error) {
      notifyError(error);
    }
  }

  function voiceSection() {
    const voices = narrator.voices();
    if (!narrator.supported) {
      return h(
        'div',
        { class: 'notice' },
        icon('info', { size: 16 }),
        'This browser has no speech engine, so lessons are shown as text instead of spoken. ' +
          'Chrome, Edge and Safari all speak.'
      );
    }
    return h(
      'div',
      { class: 'stack' },
      h(
        'label',
        { class: 'field' },
        h('span', { class: 'field-label' }, 'Teacher voice'),
        h(
          'select',
          {
            class: 'select',
            onChange: (event) => {
              narrator.setVoice(event.target.value);
              savePreferences({ preferredVoice: event.target.value });
              narrator.speak('This is how I will sound.');
            },
          },
          voices.map((voice) =>
            h(
              'option',
              { value: voice.name, selected: voice.name === (user.preferredVoice || '') },
              `${voice.name}${voice.lang ? ` (${voice.lang})` : ''}`
            )
          )
        ),
        h('span', { class: 'field-hint' }, 'Voices come from your device, so the list differs by browser.')
      ),
      h(
        'button',
        {
          class: 'btn btn-outline',
          onClick: () => narrator.speak('Before we move on, think about why that happens.'),
        },
        icon('ear', { size: 15 }),
        'Hear this voice'
      )
    );
  }

  mountPoint.replaceChildren(
    h(
      'div',
      { class: 'narrow-page stack stack-lg' },
      h(
        'div',
        { class: 'page-head' },
        h('h1', null, 'Settings'),
        h('p', { class: 'lede' }, 'How lessons sound, and what is running behind them.')
      ),

      h(
        'section',
        null,
        sectionHead('You'),
        h(
          'div',
          { class: 'card stack' },
          h(
            'label',
            { class: 'field' },
            h('span', { class: 'field-label' }, 'What the teacher calls you'),
            h('input', {
              class: 'input', type: 'text', value: user.displayName || '', maxlength: '80',
              ref: (el) => { displayName = el; },
              onChange: () => savePreferences({ displayName: displayName.value.trim() }, 'Saved.'),
            })
          ),
          h('p', { class: 'meta' }, user.email)
        )
      ),

      h(
        'section',
        null,
        sectionHead('Listening'),
        h(
          'div',
          { class: 'card stack stack-lg' },
          h(
            'div',
            { class: 'field' },
            h('span', { class: 'field-label' }, 'Default speed'),
            h(
              'div',
              { class: 'segmented', role: 'group', 'aria-label': 'Default playback speed' },
              SPEEDS.map((speed) =>
                h(
                  'button',
                  {
                    'aria-pressed': String(Math.abs(speed - (user.preferredSpeed || 1)) < 0.01),
                    onClick: (event) => {
                      lessonPlayer.setSpeed(speed);
                      const group = event.currentTarget.parentElement;
                      Array.from(group.children).forEach((button) =>
                        button.setAttribute('aria-pressed', String(button === event.currentTarget))
                      );
                    },
                  },
                  `${speed}×`
                )
              )
            ),
            h('span', { class: 'field-hint' }, 'Changing speed mid-lesson updates this too.')
          ),
          voiceSection()
        )
      ),

      h(
        'section',
        null,
        sectionHead('How you are taught'),
        h(
          'div',
          { class: 'card stack' },
          STYLES.map(([value, label, description]) =>
            h(
              'label',
              { class: 'choice' },
              h('input', {
                type: 'radio', name: 'style', value,
                checked: (user.explanationStyle || 'balanced') === value,
                onChange: () => savePreferences({ explanationStyle: value }, 'Saved. This applies to new lessons.'),
              }),
              h(
                'span',
                null,
                h('span', { class: 'strong', style: { display: 'block' } }, label),
                h('span', { class: 'meta' }, description)
              )
            )
          )
        )
      ),

      h(
        'section',
        null,
        sectionHead('What is running'),
        h(
          'div',
          { class: 'card stack' },
          providers
            ? h(
                'div',
                { class: 'stack stack-sm' },
                statusRow('Your teacher', providers.teacher, true, providers.teacherNote),
                statusRow(
                  'Audio files',
                  providers.downloadsAvailable ? providers.downloads : 'Off',
                  providers.downloadsAvailable,
                  providers.downloadsAvailable
                    ? 'Lessons can be saved as files to keep and play offline.'
                    : 'No speech provider is configured, so lessons cannot be saved as files. Listening still works.'
                ),
                statusRow(
                  'Hearing your answers',
                  canListenInBrowser ? 'This browser' : providers.listeningAvailable ? providers.listening : 'Typing only',
                  canListenInBrowser || providers.listeningAvailable,
                  canListenInBrowser
                    ? 'Your voice is recognised on this device and the audio never leaves it.'
                    : providers.listeningAvailable
                      ? 'This browser cannot listen, so recordings are transcribed on the server.'
                      : 'Answer by typing. Everything else works as normal.'
                )
              )
            : h('p', { class: 'meta' }, 'Could not read the server configuration.'),
          h(
            'p',
            { class: 'meta', style: { marginTop: 'var(--space-3)' } },
            'Live narration always uses your own device\'s voice, which is why lessons start instantly ' +
              'and keep playing without a connection.'
          )
        )
      ),

      h(
        'section',
        null,
        sectionHead('What Aloud keeps'),
        h(
          'div',
          { class: 'card stack' },
          h(
            'p',
            { class: 'muted', style: { margin: 0 } },
            'Your voice is never kept. In most browsers it never leaves your device; where it has ' +
              'to be transcribed on the server, it is deleted as soon as it has been read. What is ' +
              'kept is what you said as text, and what Aloud worked out from it: which topics you ' +
              'know, how quickly you start answering, and where you hesitate. That is what decides ' +
              'what gets taught next.'
          ),
          h(
            'button',
            {
              class: 'btn btn-danger btn-block',
              type: 'button',
              style: { justifyContent: 'flex-start' },
              onClick: forgetEverything,
            },
            icon('trash', { size: 16 }),
            'Forget what Aloud knows about me'
          ),
          h(
            'p',
            { class: 'meta', style: { margin: 0 } },
            'Your material and the lessons built from it stay. Everything learned about you goes, at once.'
          )
        )
      ),

      h(
        'section',
        null,
        sectionHead('Account'),
        h(
          'div',
          { class: 'card' },
          h(
            'button',
            { class: 'btn btn-ghost btn-block', style: { justifyContent: 'flex-start' }, onClick: signOut },
            icon('logout', { size: 16 }),
            'Sign out'
          )
        )
      )
    )
  );
}
