/**
 * The listening room.
 *
 * Renders entirely from the store, so it can be torn down and rebuilt without
 * interrupting narration: the audio belongs to `lessonPlayer`, not to this
 * screen.
 *
 * The colour of the room answers "who is speaking?" before any text is read,
 * which is the point of a product meant to be used with the screen in a
 * pocket.
 */

import { h, icon, focus } from '../core/dom.js';
import { store, player } from '../core/store.js';
import { lessonPlayer, SPEEDS } from '../core/lessonplayer.js';
import { resonanceFigure } from '../core/resonance.js';
import { listener, canListenInBrowser } from '../core/listener.js';
import { narrator, estimateSeconds } from '../core/narrator.js';
import { formatTime, shortVoiceName } from './pieces.js';
import { openModal, toast } from '../core/feedback.js';
import { api } from '../core/api.js';
import { navigate } from '../core/router.js';

const TEACHER_STATES = new Set(['playing', 'ai_speaking', 'explaining']);
const STUDENT_STATES = new Set(['waiting_for_student', 'listening', 'transcribing']);
const BUSY_STATES = new Set(['thinking', 'evaluating', 'transcribing', 'reviewing']);

const HEADLINE = {
  idle: ['Ready when you are', ''],
  playing: ['Teaching', ''],
  ai_speaking: ['Teaching', ''],
  paused: ['Paused', 'Take your time.'],
  waiting_for_student: ['Your turn', 'Answer out loud, or type instead.'],
  listening: ['I am listening', ''],
  transcribing: ['Getting that down', ''],
  evaluating: ['Thinking about your answer', ''],
  thinking: ['One moment', ''],
  explaining: ['Explaining', ''],
  reviewing: ['Looking back over this', ''],
  completed: ['Lesson finished', ''],
  error: ['Something interrupted us', 'You can pick up where you left off.'],
};

const KIND_LABEL = {
  context: 'Where we are',
  explanation: 'Explanation',
  example: 'Example',
  analogy: 'Another way in',
  check: 'Your turn',
  deeper: 'Going deeper',
  application: 'Putting it to work',
  recap: 'Recap',
  assessment: 'Your turn',
  next_step: 'What next',
};

function holderFor(state) {
  if (TEACHER_STATES.has(state)) return 'teacher';
  if (STUDENT_STATES.has(state)) return 'student';
  return 'system';
}

function figureMode(state) {
  if (state === 'listening') return 'listening';
  if (BUSY_STATES.has(state)) return 'busy';
  return 'idle';
}

/* ---------------------------------------------------------------------- */

function topBar(state) {
  const lesson = state.lesson;
  return h(
    'div',
    { class: 'player-top' },
    h(
      'button',
      {
        class: 'btn-icon',
        'aria-label': 'Back to the app',
        onClick: () => lessonPlayer.close(),
      },
      icon('chevronDown', { size: 20 })
    ),
    h(
      'div',
      { class: 'player-crumb spacer truncate' },
      state.neighbours
        ? `Lesson ${state.neighbours.position} of ${state.neighbours.total}`
        : '',
      lesson ? h('strong', null, ` · ${lesson.stageLabel || ''}`) : null
    ),
    h(
      'button',
      {
        class: 'btn-icon',
        'aria-label': state.showTranscript ? 'Hide transcript' : 'Show transcript',
        'aria-pressed': String(state.showTranscript),
        onClick: () => lessonPlayer.toggleTranscript(),
      },
      icon('book', { size: 19 })
    ),
    h(
      'button',
      { class: 'btn-icon', 'aria-label': 'Lesson options', onClick: () => openOptions(state) },
      icon('settings', { size: 19 })
    )
  );
}

function stage(state) {
  const [headline, hint] = HEADLINE[state.state] || HEADLINE.idle;
  const section = state.sections[state.sectionIndex];
  const active = TEACHER_STATES.has(state.state) && state.speaking;
  // The answer panel already announces whose turn it is, so repeating it here
  // only competes for the space the panel needs.
  const showTurn = !state.question;

  return h(
    'div',
    { class: 'player-stage' },
    resonanceFigure(state.concept?.id || state.lessonId, {
      active,
      mode: figureMode(state.state),
    }),
    h(
      'div',
      { class: 'player-titles' },
      h('h1', null, state.lesson?.title || 'Lesson'),
      h('p', null, section ? KIND_LABEL[section.kind] || section.heading : state.lesson?.subtitle || '')
    ),
    showTurn
      ? h(
          'div',
          { class: 'row', style: { justifyContent: 'center', flexWrap: 'wrap', gap: 'var(--space-3)' } },
          h('span', { class: 'turn' }, h('i', { class: 'turn-pip' }), headline),
          hint && h('span', { class: 'turn-hint' }, hint)
        )
      : null
  );
}

function transcript(state) {
  return h(
    'div',
    { class: 'transcript' },
    state.sections.map((section, index) =>
      h(
        'button',
        {
          class: 'transcript-line',
          'aria-current': index === state.sectionIndex ? 'true' : null,
          dataset: { kind: section.kind },
          onClick: () => lessonPlayer.goToSection(index),
        },
        h('span', { class: 'transcript-kind' }, KIND_LABEL[section.kind] || section.heading),
        section.question ? section.question.prompt : section.script
      )
    )
  );
}

/**
 * Elements that change many times a second.
 *
 * Narration reports progress on every animation frame. Rebuilding the player
 * that often would restart its entrance animation, throw away focus while a
 * student is typing an answer, and do a great deal of work for a moving
 * number. So the structure is rendered once and these few nodes are patched
 * in place instead.
 */
const live = {
  fill: null,
  thumb: null,
  elapsed: null,
  remaining: null,
  total: 1,
  bars: [],
  partial: null,
};

function resetLive() {
  live.fill = null;
  live.thumb = null;
  live.elapsed = null;
  live.remaining = null;
  live.bars = [];
  live.partial = null;
  live.total = 1;
}

/** Apply high-frequency values without touching the DOM structure. */
export function updatePlayerLive() {
  const state = player();
  if (live.fill) {
    const ratio = Math.max(0, Math.min(1, state.elapsed / live.total));
    live.fill.style.width = `${ratio * 100}%`;
    if (live.thumb) live.thumb.style.left = `${ratio * 100}%`;
    if (live.elapsed) live.elapsed.textContent = formatTime(state.elapsed);
    if (live.remaining) live.remaining.textContent = `-${formatTime(Math.max(0, live.total - state.elapsed))}`;
  }
  if (live.bars.length) {
    live.bars.forEach((bar, index) => {
      const weight = 1 - Math.abs(index - 8.5) / 11;
      const height = 12 + state.level * 88 * weight;
      bar.style.setProperty('--h', `${Math.min(100, height)}%`);
    });
  }
  if (live.partial) {
    live.partial.textContent = state.partial || (state.listening ? 'Listening...' : '');
  }
}

function scrubber(state) {
  const section = state.sections[state.sectionIndex];
  if (!section) return null;
  const total = Math.max(1, estimateSeconds(section.script, state.speed || 1));
  const ratio = Math.max(0, Math.min(1, state.elapsed / total));
  live.total = total;

  function seekFromEvent(event, element) {
    const rect = element.getBoundingClientRect();
    const x = (event.touches ? event.touches[0].clientX : event.clientX) - rect.left;
    lessonPlayer.seekTo((x / rect.width) * total);
  }

  const track = h(
    'div',
    {
      class: 'scrub-track',
      role: 'slider',
      tabindex: '0',
      'aria-label': 'Position in this part',
      'aria-valuemin': '0',
      'aria-valuemax': String(Math.round(total)),
      'aria-valuenow': String(Math.round(state.elapsed)),
      'aria-valuetext': `${formatTime(state.elapsed)} of ${formatTime(total)}`,
      onPointerdown: (event) => {
        event.currentTarget.setPointerCapture?.(event.pointerId);
        seekFromEvent(event, event.currentTarget);
      },
      onPointermove: (event) => {
        if (event.buttons === 1) seekFromEvent(event, event.currentTarget);
      },
      onKeydown: (event) => {
        const step = event.shiftKey ? 30 : 5;
        if (event.key === 'ArrowRight') { event.preventDefault(); lessonPlayer.seekBy(step); }
        if (event.key === 'ArrowLeft') { event.preventDefault(); lessonPlayer.seekBy(-step); }
        if (event.key === 'Home') { event.preventDefault(); lessonPlayer.seekTo(0); }
        if (event.key === 'End') { event.preventDefault(); lessonPlayer.next(); }
      },
    },
    h('div', { class: 'scrub-rail' }),
    h('div', {
      class: 'scrub-fill',
      style: { width: `${ratio * 100}%` },
      ref: (el) => { live.fill = el; },
    }),
    h('div', {
      class: 'scrub-thumb',
      style: { left: `${ratio * 100}%` },
      ref: (el) => { live.thumb = el; },
    })
  );

  return h(
    'div',
    { class: 'scrubber' },
    track,
    h(
      'div',
      { class: 'scrub-times' },
      h('span', { ref: (el) => { live.elapsed = el; } }, formatTime(state.elapsed)),
      h('span', null, `Part ${state.sectionIndex + 1} of ${state.sections.length}`),
      h('span', { ref: (el) => { live.remaining = el; } },
        `-${formatTime(Math.max(0, total - state.elapsed))}`)
    )
  );
}

function transport(state) {
  const speaking = state.speaking;
  const atStart = state.sectionIndex === 0 && state.elapsed < 1;
  const atEnd = state.sectionIndex >= state.sections.length - 1;

  return h(
    'div',
    { class: 'transport' },
    h(
      'button',
      {
        'aria-label': 'Previous part',
        disabled: atStart,
        onClick: () => lessonPlayer.previous(),
      },
      icon('prev', { size: 20 })
    ),
    h(
      'button',
      { class: 'skip', 'aria-label': 'Back 15 seconds', onClick: () => lessonPlayer.seekBy(-15) },
      icon('back15', { size: 22 }),
      h('span', { class: 'skip-label' }, '15')
    ),
    h(
      'button',
      {
        class: 'play',
        'aria-label': speaking ? 'Pause' : 'Play',
        onClick: () => lessonPlayer.toggle(),
      },
      icon(speaking ? 'pause' : 'play', { size: 26, filled: !speaking, stroke: 2 })
    ),
    h(
      'button',
      { class: 'skip', 'aria-label': 'Forward 15 seconds', onClick: () => lessonPlayer.seekBy(15) },
      icon('fwd15', { size: 22 }),
      h('span', { class: 'skip-label' }, '15')
    ),
    h(
      'button',
      {
        'aria-label': atEnd ? 'Finish lesson' : 'Next part',
        onClick: () => lessonPlayer.next(),
      },
      icon('next', { size: 20 })
    )
  );
}

function speedControl(state) {
  return h(
    'div',
    { class: 'segmented', role: 'group', 'aria-label': 'Playback speed' },
    SPEEDS.map((speed) =>
      h(
        'button',
        {
          'aria-pressed': String(Math.abs(speed - state.speed) < 0.01),
          onClick: () => lessonPlayer.setSpeed(speed),
        },
        `${speed}×`
      )
    )
  );
}

/**
 * Step to the next voice.
 *
 * A cycle rather than a menu: the only way to judge a voice is to hear it, and
 * the label shows which one is reading so the control says where you are as
 * well as what it does.
 */
function voiceButton(state) {
  const voices = narrator.voices();
  if (voices.length < 2) return null;
  const current = state.voice || narrator.voiceName || voices[0].name;
  const label = shortVoiceName(current);
  return h(
    'button',
    {
      class: 'btn btn-sm voice-button',
      'aria-label': `Teacher voice: ${label}. Press for the next voice.`,
      title: `Voice: ${label}`,
      onClick: () => {
        const next = lessonPlayer.cycleVoice();
        if (next) toast(`Voice: ${shortVoiceName(next.name)}`, { duration: 2200 });
      },
    },
    icon('ear', { size: 14 }),
    h('span', { class: 'voice-label truncate' }, label)
  );
}

function actions(state) {
  return h(
    'div',
    { class: 'player-actions' },
    speedControl(state),
    h(
      'div',
      { class: 'row', style: { gap: 'var(--space-1)' } },
      h(
        'button',
        { class: 'btn btn-sm', onClick: () => openAsk(state) },
        icon('chat', { size: 15 }),
        'Ask'
      ),
      h(
        'button',
        {
          class: 'btn-icon',
          'aria-label': 'Replay this part',
          onClick: () => lessonPlayer.replay(),
        },
        icon('refresh', { size: 17 })
      ),
      h(
        'button',
        { class: 'btn-icon', 'aria-label': 'Save this place', onClick: () => lessonPlayer.bookmark() },
        icon('bookmark', { size: 17 })
      ),
      voiceButton(state),
      h(
        'button',
        {
          class: 'btn-icon',
          'aria-label': state.muted ? 'Unmute' : 'Mute',
          'aria-pressed': String(state.muted),
          onClick: () => lessonPlayer.setMuted(!state.muted),
        },
        icon(state.muted ? 'mute' : 'volume', { size: 17 })
      )
    )
  );
}

/* --- Your turn --------------------------------------------------------- */

function answerPanel(state) {
  const question = state.question;
  if (!question) return null;

  live.bars = [];
  const bars = Array.from({ length: 18 }, (_unused, index) => {
    // Centre bars respond more, which reads as a voice rather than a graph.
    const weight = 1 - Math.abs(index - 8.5) / 11;
    const height = 12 + state.level * 88 * weight;
    return h('i', {
      style: { '--h': `${Math.min(100, height)}%` },
      ref: (el) => { live.bars.push(el); },
    });
  });

  let typed = null;

  function sendTyped() {
    const value = (typed?.value || '').trim();
    if (!value) return;
    typed.value = '';
    lessonPlayer.submitAnswer(value, 'text');
  }

  return h(
    'div',
    { class: 'answer-panel' },
    h('p', { class: 'answer-question' }, question.prompt),
    h(
      'div',
      { class: 'answer-tools' },
      h(
        'button',
        {
          class: 'mic',
          dataset: { listening: String(state.listening) },
          'aria-label': state.listening ? 'Stop and send my answer' : 'Answer out loud',
          'aria-pressed': String(state.listening),
          onClick: () => lessonPlayer.listen(),
        },
        icon('mic', { size: 22 })
      ),
      state.listening
        ? h('div', { class: 'level', 'aria-hidden': 'true' }, bars)
        : h(
            'div',
            { class: 'spacer', style: { color: 'var(--night-faint)', fontSize: 'var(--text-sm)' } },
            listener.available
              ? 'Tap to answer out loud. There is no rush.'
              : 'Type your answer below.'
          ),
      h(
        'button',
        { class: 'btn btn-sm', onClick: () => lessonPlayer.skipQuestion() },
        'Skip'
      )
    ),
    state.partial || state.listening
      ? h(
          'div',
          { class: 'answer-transcript', 'aria-live': 'polite',
            ref: (el) => { live.partial = el; } },
          state.partial || 'Listening...'
        )
      : null,
    h(
      'div',
      { class: 'answer-typed' },
      h('input', {
        class: 'input',
        type: 'text',
        placeholder: 'Or type your answer',
        'aria-label': 'Type your answer',
        ref: (el) => { typed = el; },
        onKeydown: (event) => {
          if (event.key === 'Enter') { event.preventDefault(); sendTyped(); }
        },
      }),
      h('button', { class: 'btn btn-primary', onClick: sendTyped }, 'Send')
    ),
    !canListenInBrowser && listener.available
      ? h(
          'p',
          { class: 'meta', style: { marginTop: 'var(--space-3)', color: 'var(--night-faint)' } },
          'This browser sends your recording to be transcribed on the server.'
        )
      : null
  );
}

/* --- Teacher's reply --------------------------------------------------- */

const VERDICT = {
  correct: 'That is right',
  mostly_correct: 'Nearly there',
  incomplete: 'Part of the way',
  misconception: 'Worth a second look',
  incorrect: 'Let us go again',
  unclear: 'Not sure I followed',
};

function replyPanel(state) {
  const reply = state.reply;
  if (!reply) return null;
  return h(
    'div',
    { class: 'reply' },
    reply.judgement
      ? h(
          'div',
          { class: 'reply-verdict' },
          icon(reply.judgement === 'correct' ? 'check' : 'sparkle', { size: 14 }),
          VERDICT[reply.judgement] || '',
          reply.mastery
            ? h('span', { style: { color: 'var(--night-faint)', fontWeight: '400' } }, ` · ${reply.mastery}`)
            : null
        )
      : null,
    h('p', { class: 'reply-text' }, reply.text),
    reply.sourceNote ? h('p', { class: 'reply-source' }, reply.sourceNote) : null,
    reply.revisit
      ? h(
          'div',
          { class: 'row', style: { marginTop: 'var(--space-4)', gap: 'var(--space-2)' } },
          h('button', { class: 'btn btn-primary btn-sm', onClick: () => lessonPlayer.next() }, 'Carry on here'),
          h('button', { class: 'btn btn-sm', onClick: () => lessonPlayer.close() }, 'Go to that lesson')
        )
      : null
  );
}

/* --- Finished ---------------------------------------------------------- */

function finishedView(state) {
  const result = state.finished;
  const assessment = result?.assessment;
  const recommendation = result?.recommendation;

  return h(
    'div',
    { class: 'finished' },
    resonanceFigure(state.concept?.id || state.lessonId, { active: false }),
    h('h2', null, state.lesson?.title || 'Lesson finished'),
    assessment
      ? h(
          'div',
          { class: 'finished-card stack' },
          h(
            'div',
            { class: 'row-between' },
            h('span', { class: 'chip chip-brass' }, assessment.outcomeLabel),
            h(
              'span',
              { class: 'meta', style: { color: 'var(--night-faint)' } },
              assessment.confidence >= 0.55 ? '' : 'Based on a little evidence'
            )
          ),
          h(
            'p',
            { style: { fontFamily: 'var(--font-serif)', color: 'var(--night-soft)', lineHeight: '1.7' } },
            assessment.recommendation
          ),
          assessment.takeaways?.length
            ? h(
                'div',
                null,
                h(
                  'h4',
                  { style: { color: 'var(--night-text)', marginBottom: 'var(--space-2)' } },
                  'Worth keeping'
                ),
                assessment.takeaways.map((takeaway) =>
                  h(
                    'div',
                    { class: 'takeaway' },
                    h('span', { class: 'takeaway-mark' }, icon('check', { size: 15 })),
                    h('span', null, takeaway)
                  )
                )
              )
            : null,
          assessment.gaps?.length
            ? h(
                'div',
                null,
                h(
                  'h4',
                  { style: { color: 'var(--night-text)', marginBottom: 'var(--space-2)' } },
                  'Loose ends'
                ),
                assessment.gaps.map((gap) =>
                  h(
                    'div',
                    { class: 'takeaway' },
                    h('span', { class: 'takeaway-mark', style: { color: 'var(--night-faint)' } }, '·'),
                    h('span', null, gap)
                  )
                )
              )
            : null
        )
      : h('p', { style: { color: 'var(--night-soft)' } }, 'Your place is saved.'),
    h(
      'div',
      { class: 'row', style: { flexWrap: 'wrap', justifyContent: 'center', gap: 'var(--space-2)' } },
      h(
        'button',
        { class: 'btn btn-sm', onClick: () => { lessonPlayer.goToSection(0); lessonPlayer.play(); } },
        icon('refresh', { size: 15 }),
        'Listen again'
      ),
      state.neighbours?.next
        ? h(
            'button',
            {
              class: 'btn btn-primary btn-sm',
              onClick: () => lessonPlayer.open(state.neighbours.next.id, { autoplay: true }),
            },
            'Next: ',
            state.neighbours.next.title
          )
        : null,
      recommendation && !state.neighbours?.next
        ? h(
            'button',
            {
              class: 'btn btn-primary btn-sm',
              onClick: () => lessonPlayer.open(recommendation.lessonId, { autoplay: true }),
            },
            recommendation.title
          )
        : null,
      h('button', { class: 'btn btn-sm', onClick: () => lessonPlayer.close() }, 'Back to my plan')
    )
  );
}

/* --- Modals ------------------------------------------------------------ */

function openAsk(state) {
  let input = null;
  const suggestions = [
    'Explain that more simply',
    'Give me an example',
    'Why does that happen?',
    'Quiz me on this',
  ];

  openModal((close) => {
    async function send(text) {
      const value = (text || input?.value || '').trim();
      if (!value) return;
      close();
      await lessonPlayer.ask(value);
    }

    return h(
      'div',
      { class: 'stack' },
      h('h2', null, 'Ask your teacher'),
      h(
        'p',
        { class: 'muted', style: { fontSize: 'var(--text-sm)' } },
        'About ',
        h('strong', null, state.concept?.name || state.lesson?.title || 'this lesson'),
        '. The answer comes from your own material.'
      ),
      h('input', {
        class: 'input',
        type: 'text',
        placeholder: 'What would you like to know?',
        'aria-label': 'Your question',
        ref: (el) => { input = el; },
        onKeydown: (event) => {
          if (event.key === 'Enter') { event.preventDefault(); send(); }
        },
      }),
      h(
        'div',
        { class: 'row row-wrap', style: { gap: 'var(--space-2)' } },
        suggestions.map((text) =>
          h('button', { class: 'chip chip-outline', onClick: () => send(text), type: 'button' }, text)
        )
      ),
      h(
        'div',
        { class: 'modal-actions' },
        h('button', { class: 'btn btn-ghost', onClick: close }, 'Cancel'),
        h('button', { class: 'btn btn-primary', onClick: () => send() }, 'Ask')
      )
    );
  });
}

function openOptions(state) {
  openModal((close) => {
    const status = state.audioStatus || {};
    const canDownload = status.state === 'ready';

    async function prepare() {
      close();
      try {
        await api.audio.prepare(state.lessonId);
        toast('Preparing audio. This takes a moment.');
        pollAudio(state.lessonId);
      } catch (error) {
        toast(error.message, { tone: 'trouble' });
      }
    }

    return h(
      'div',
      { class: 'stack stack-lg' },
      h('h2', null, 'Lesson options'),

      h(
        'div',
        { class: 'stack stack-sm' },
        h('h4', null, 'Sleep timer'),
        h(
          'div',
          { class: 'row row-wrap', style: { gap: 'var(--space-2)' } },
          [10, 20, 30, 45].map((minutes) =>
            h(
              'button',
              {
                class: 'btn btn-sm btn-outline',
                onClick: () => { lessonPlayer.setSleepTimer(minutes); close(); },
              },
              `${minutes} min`
            )
          ),
          h(
            'button',
            { class: 'btn btn-sm btn-ghost', onClick: () => { lessonPlayer.setSleepTimer(0); close(); } },
            'Off'
          )
        )
      ),

      h(
        'div',
        { class: 'stack stack-sm' },
        h('h4', null, 'Take this lesson with you'),
        h('p', { class: 'meta' }, status.message || ''),
        canDownload
          ? h(
              'a',
              {
                class: 'btn btn-outline',
                href: api.audio.downloadUrl(state.lessonId, status.voice || ''),
                download: '',
                'data-native': '',
                onClick: () => close(),
              },
              icon('download', { size: 15 }),
              'Download audio'
            )
          : status.state === 'unavailable'
            ? h(
                'p',
                { class: 'notice' },
                icon('info', { size: 16 }),
                'Saving lessons as files is switched off on this server. Listening still works.'
              )
            : h(
                'button',
                { class: 'btn btn-outline', onClick: prepare, disabled: status.state === 'preparing' },
                icon('download', { size: 15 }),
                status.state === 'preparing' ? 'Preparing...' : 'Save for offline'
              )
      ),

      h(
        'div',
        { class: 'stack stack-sm' },
        h('h4', null, 'This lesson'),
        h(
          'button',
          { class: 'btn btn-ghost btn-block', style: { justifyContent: 'flex-start' },
            onClick: () => { close(); lessonPlayer.anotherQuestion(); } },
          icon('question', { size: 16 }), 'Quiz me on this'
        ),
        state.spaceId
          ? h(
              'button',
              { class: 'btn btn-ghost btn-block', style: { justifyContent: 'flex-start' },
                onClick: () => { close(); lessonPlayer.close(); navigate(`/space/${state.spaceId}`); } },
              icon('layers', { size: 16 }), 'Go to the subject'
            )
          : null,
        h(
          'button',
          { class: 'btn btn-ghost btn-block', style: { justifyContent: 'flex-start', color: 'var(--trouble)' },
            onClick: () => { close(); lessonPlayer.exit(); } },
          icon('close', { size: 16 }), 'End lesson'
        )
      )
    );
  });
}

let audioPollTimer = null;
function pollAudio(lessonId) {
  clearInterval(audioPollTimer);
  let tries = 0;
  audioPollTimer = setInterval(async () => {
    tries += 1;
    try {
      const status = await api.audio.status(lessonId);
      const current = player();
      if (current.lessonId === lessonId) {
        store.set((state) => ({ player: { ...state.player, audioStatus: status } }));
      }
      if (status.state === 'ready') {
        clearInterval(audioPollTimer);
        toast('Saved. Available offline.', { tone: 'neutral' });
      } else if (status.state === 'failed' || tries > 60) {
        clearInterval(audioPollTimer);
        if (status.state === 'failed') toast(status.message, { tone: 'trouble' });
      }
    } catch {
      clearInterval(audioPollTimer);
    }
  }, 2500);
}

/* --- Keyboard ---------------------------------------------------------- */

function onKeyDown(event) {
  const state = player();
  if (!state.open) return;
  const target = event.target;
  if (target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA')) return;

  // Hold H to talk. The push-to-talk fallback for anyone whose microphone,
  // room or preference makes talking over the teacher unreliable, and the
  // only way in at all when barge-in has never been armed.
  if (event.key === 'h' || event.key === 'H') {
    event.preventDefault();
    if (!event.repeat && !state.listening) lessonPlayer.listen();
    return;
  }

  switch (event.key) {
    case ' ':
    case 'k':
      event.preventDefault();
      lessonPlayer.toggle();
      break;
    case 'ArrowLeft': event.preventDefault(); lessonPlayer.seekBy(-15); break;
    case 'ArrowRight': event.preventDefault(); lessonPlayer.seekBy(15); break;
    case 'j': lessonPlayer.previous(); break;
    case 'l': lessonPlayer.next(); break;
    case 'm': lessonPlayer.setMuted(!state.muted); break;
    case 't': lessonPlayer.toggleTranscript(); break;
    case 'r': lessonPlayer.replay(); break;
    case 'a':
      if (state.question) lessonPlayer.listen();
      else openAsk(state);
      break;
    case 'Escape': lessonPlayer.close(); break;
    default: break;
  }
}

function onKeyUp(event) {
  if (event.key !== 'h' && event.key !== 'H') return;
  const state = player();
  if (state.open && state.listening) lessonPlayer.listen();   // stops it
}

let keysBound = false;

/* --- Render ------------------------------------------------------------ */

export function playerView() {
  resetLive();
  const state = player();
  if (!keysBound) {
    document.addEventListener('keydown', onKeyDown);
    document.addEventListener('keyup', onKeyUp);
    keysBound = true;
  }

  if (state.error) {
    return h(
      'div',
      { class: 'player night', dataset: { holder: 'system' } },
      topBar(state),
      h(
        'div',
        { class: 'player-stage' },
        h('div', { style: { color: 'var(--trouble)' } }, icon('warn', { size: 30 })),
        h('h1', { style: { fontSize: 'var(--text-lg)' } }, 'That lesson could not be loaded'),
        h('p', { style: { color: 'var(--night-faint)' } }, state.error),
        h('button', { class: 'btn btn-primary', onClick: () => lessonPlayer.close() }, 'Back')
      )
    );
  }

  if (!state.lesson) {
    return h(
      'div',
      { class: 'player night', dataset: { holder: 'system' } },
      topBar(state),
      h(
        'div',
        { class: 'player-stage' },
        h('div', { class: 'spinner', style: { width: '1.6rem', height: '1.6rem' } }),
        h('p', { style: { color: 'var(--night-faint)' } }, 'Getting your lesson ready')
      )
    );
  }

  const finished = state.state === 'completed' && state.finished;

  return h(
    'div',
    {
      class: 'player night',
      dataset: { holder: holderFor(state.state) },
      role: 'region',
      'aria-label': `Lesson: ${state.lesson.title}`,
    },
    topBar(state),
    finished
      ? finishedView(state)
      : state.showTranscript
        ? transcript(state)
        : stage(state),
    !finished && state.question ? answerPanel(state) : null,
    !finished && !state.question && state.reply ? replyPanel(state) : null,
    !finished
      ? h('div', { class: 'player-controls' }, scrubber(state), transport(state), actions(state))
      : null
  );
}

/* --- Mini player -------------------------------------------------------- */

export function miniPlayer() {
  const state = player();
  if (!state.lessonId || !state.lesson || state.open) return null;

  const total = state.sections.length || 1;
  const progress = Math.min(1, (state.sectionIndex + 0.5) / total);
  const speaking = state.speaking;

  return h(
    'div',
    { class: 'miniplayer', role: 'region', 'aria-label': 'Now playing' },
    h('div', { class: 'miniplayer-track' }, h('i', { style: { width: `${progress * 100}%` } })),
    h(
      'div',
      { class: 'miniplayer-body' },
      h(
        'button',
        {
          class: 'miniplayer-open',
          onClick: () => { lessonPlayer.open(state.lessonId); },
          'aria-label': `Open ${state.lesson.title}`,
        },
        h(
          'span',
          { style: { minWidth: 0 } },
          h('span', { class: 'miniplayer-title truncate', style: { display: 'block' } }, state.lesson.title),
          h(
            'span',
            { class: 'miniplayer-sub truncate', style: { display: 'block' } },
            state.question ? 'Your turn' : (HEADLINE[state.state] || [''])[0]
          )
        )
      ),
      h(
        'button',
        {
          class: 'btn-icon mini-play',
          'aria-label': speaking ? 'Pause' : 'Play',
          onClick: () => lessonPlayer.toggle(),
        },
        icon(speaking ? 'pause' : 'play', { size: 16, filled: !speaking, stroke: 2 })
      ),
      h(
        'button',
        { class: 'btn-icon', 'aria-label': 'End lesson', onClick: () => lessonPlayer.exit() },
        icon('close', { size: 16 })
      )
    )
  );
}

export { focus };
