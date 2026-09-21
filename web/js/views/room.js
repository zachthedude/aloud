/**
 * The room.
 *
 * Every crunch mode runs on this one screen. It is built for a phone in a
 * hand, in a pocket, or on a pillow: the words being spoken are the interface,
 * set large enough to read at arm's length, and there is one control that
 * matters, a big button to talk.
 *
 * Nothing here knows which mode is running. The server says what to say and
 * whether to listen afterwards; the room says it, shows it, and listens.
 */

import { h, icon, mount } from '../core/dom.js';
import { VoiceLoop } from '../core/voiceloop.js';
import { lessonPlayer } from '../core/lessonplayer.js';
import { player } from '../core/store.js';
import { navigate, onLeave } from '../core/router.js';
import { canListenInBrowser, canRecord } from '../core/listener.js';

/** Who holds the floor, in words, so it never depends on colour alone. */
const FLOOR_WORDS = {
  aloud: 'Aloud is talking. Talk over it any time.',
  student: 'Your turn.',
  listening: 'Listening.',
  thinking: 'Thinking.',
  done: 'That sitting is finished.',
  quiet: '',
};

/** Changes that only move a meter or the live words, never the layout. */
const LIVE_ONLY = new Set(['level', 'partial']);

export async function roomView(container, spaceId, mode, query) {
  // One voice in the room. A lesson playing underneath would talk over it.
  if (player().lessonId && player().speaking) lessonPlayer.pause();

  const canTalk = canListenInBrowser || canRecord;
  let typing = !canTalk;
  let typed = null;
  let levelNode = null;
  let partialNode = null;

  const loop = new VoiceLoop({ onChange: (state, patch) => changed(state, patch) });

  // Leaving by the back button must release the microphone, the audio and
  // the borrowed callbacks exactly as the Leave button does.
  onLeave(async () => {
    document.removeEventListener('keydown', onKey);
    document.removeEventListener('keyup', onKeyUp);
    await loop.leave();
  });

  const leave = () => navigate('/modes');
  const again = () => navigate(location.pathname, { replace: true });

  function sendTyped() {
    if (!typed || !typed.value.trim()) return;
    loop.answer(typed.value, 'text');
    typed.value = '';
  }

  // Hold H to talk, the same key as in a lesson. R repeats, S skips, Escape
  // leaves. None of them fire while typing an answer.
  function onKey(event) {
    const inField = event.target && event.target.tagName === 'INPUT';
    if (event.key === 'Escape') { event.preventDefault(); leave(); return; }
    if (inField) return;
    if ((event.key === 'h' || event.key === 'H') && !event.repeat) { event.preventDefault(); loop.talk(); }
    else if (event.key === 'Enter' && loop.state.floor === 'quiet') loop.next();
    else if (event.key === 'r' || event.key === 'R') loop.repeat();
    else if (event.key === 's' || event.key === 'S') loop.skip();
  }
  function onKeyUp(event) {
    if ((event.key === 'h' || event.key === 'H') && loop.state.floor === 'listening') loop.talk();
  }
  document.addEventListener('keydown', onKey);
  document.addEventListener('keyup', onKeyUp);

  function changed(state, patch) {
    if (Object.keys(patch).every((key) => LIVE_ONLY.has(key))) {
      if (levelNode) levelNode.style.transform = `scale(${1 + Math.min(state.level * 1.6, 0.6)})`;
      if (partialNode) partialNode.textContent = state.partial;
      return;
    }
    render(state);
  }

  // The entrance plays only when a line is genuinely new, not every time the
  // room redraws for a change of floor.
  let shownLines = 0;

  function lineNodes(lines) {
    const arrived = lines.length > shownLines;
    shownLines = lines.length;
    return lines.map((line, index) => {
      const age = lines.length - 1 - index;
      return h(
        'p',
        {
          class: `room-line room-line-${line.who}${arrived && age === 0 ? ' is-new' : ''}`,
          dataset: { age: String(Math.min(age, 3)) },
          // Only the newest line is announced, so a screen reader hears each
          // thing once rather than the whole history on every change.
          'aria-hidden': age === 0 ? 'false' : 'true',
        },
        line.who === 'you' ? h('span', { class: 'visually-hidden' }, 'You said: ') : null,
        line.text
      );
    });
  }

  function talkButton(state) {
    const listening = state.floor === 'listening';
    return h(
      'button',
      {
        class: 'room-talk',
        type: 'button',
        disabled: state.floor === 'thinking',
        dataset: { listening: String(listening) },
        'aria-pressed': String(listening),
        'aria-label': listening ? 'Stop listening' : 'Talk',
        onClick: () => loop.talk(),
      },
      h('span', { class: 'room-level', 'aria-hidden': 'true', ref: (el) => { levelNode = el; } }),
      icon(listening ? 'pause' : 'mic', { size: 30, stroke: 1.8 })
    );
  }

  function controls(state) {
    if (state.floor === 'done') {
      return h(
        'div',
        { class: 'room-finished' },
        h('button', { class: 'btn btn-lg room-again', type: 'button', onClick: again }, 'Go again'),
        h('button', { class: 'btn btn-lg room-quiet', type: 'button', onClick: leave }, 'Back to ways of working')
      );
    }
    // A turn that expects no answer normally moves on by itself once it has
    // been said. If the voice could not play, or the student wants to go at
    // their own pace, this is the way forward, named for what it does.
    const waiting = state.floor === 'quiet' && state.turn && !state.turn.done
      && state.turn.expects === 'nothing';
    return [
      waiting
        ? h('button', { class: 'btn btn-lg room-again', type: 'button', onClick: () => loop.next() }, 'Continue')
        : canTalk ? talkButton(state) : null,
      typing
        ? h(
            'form',
            { class: 'room-type', onSubmit: (event) => { event.preventDefault(); sendTyped(); } },
            h('label', { class: 'visually-hidden', for: 'room-typed' }, 'Type your answer'),
            h('input', {
              id: 'room-typed',
              class: 'room-input',
              type: 'text',
              autocomplete: 'off',
              placeholder: 'Type your answer',
              ref: (el) => { typed = el; },
            }),
            h('button', { class: 'room-send', type: 'submit', 'aria-label': 'Send' },
              icon('chevronRight', { size: 18 }))
          )
        : null,
      h(
        'div',
        { class: 'room-small' },
        canTalk
          ? h('button', {
              type: 'button',
              onClick: () => {
                typing = !typing;
                render(loop.state);
                if (typing) typed?.focus();
              },
            }, typing ? 'Hide typing' : 'Type instead')
          : null,
        h('button', { type: 'button', onClick: () => loop.repeat() }, 'Say it again'),
        h('button', { type: 'button', onClick: () => loop.skip() }, 'Skip')
      ),
    ];
  }

  function render(state) {
    // Whatever was half-typed survives the rebuild, as does focus in the field.
    const draft = typed?.value || '';
    const hadFocus = typed && document.activeElement === typed;
    const session = state.session;

    const view = h(
      'div',
      { class: 'room night', dataset: { floor: state.floor } },
      h(
        'header',
        { class: 'room-top' },
        h('button', { class: 'room-leave', type: 'button', onClick: leave },
          icon('chevronLeft', { size: 18 }), 'Leave'),
        session
          ? h('p', { class: 'room-mode' }, icon(session.modeIcon || 'waves', { size: 16 }), session.modeName)
          : null
      ),
      h(
        'main',
        { class: 'room-stage', id: 'main' },
        h(
          'div',
          { class: 'room-lines', 'aria-live': 'polite' },
          state.lines.length
            ? lineNodes(state.lines)
            : h('p', { class: 'room-line room-line-aloud', dataset: { age: '0' } }, 'One moment.')
        ),
        h('p', { class: 'room-partial', 'aria-hidden': 'true', ref: (el) => { partialNode = el; } }, state.partial),
        h('p', { class: 'room-floor', role: 'status' },
          h('span', { class: 'room-pip', 'aria-hidden': 'true' }),
          FLOOR_WORDS[state.floor] || ''),
        state.error ? h('p', { class: 'room-error', role: 'alert' }, state.error) : null
      ),
      h('footer', { class: 'room-controls' }, controls(state))
    );

    mount(container, view);
    if (typed) {
      typed.value = draft;
      if (hadFocus) typed.focus();
    }
  }

  render(loop.state);
  // Exposed for the browser journey, which drives the room without a voice.
  container.__room = loop;

  const resumeId = query?.get?.('session');
  if (resumeId) await loop.resume(resumeId);
  else await loop.start(spaceId, mode);
}
