/**
 * The landing page.
 *
 * Six quiet screens over one long sound wave. Each screen says one thing, and
 * scrolling travels along the wave in the background, so every section arrives
 * with a phrase of its own. The first screen can also speak: press play and a
 * teacher starts talking, using the same speech engine the real lessons use.
 */

import { h, icon } from '../core/dom.js';
import { narrator } from '../core/narrator.js';
import { brand, themeToggle } from '../components/shell.js';
import { waveBackdrop } from '../components/wavebackdrop.js';

const SAMPLE = [
  "Let us look at Newton's second law.",
  'You already have force in hand, and this builds directly on that.',
  'Your notes define it like this. Acceleration is directly proportional to the resultant force, and inversely proportional to the mass.',
  'Let me unpack that, because a definition rarely does the work on its own.',
  'Picture pushing a shopping trolley. Empty, it leaps forward. Full, the same push barely moves it.',
  'The push has not changed. The mass has.',
  'Now, before we move on, think about this for a moment.',
  'Why would doubling the mass halve the acceleration?',
];

/**
 * The page as a track: one phrase of the background wave per section, in page
 * order. Each shape is chosen to feel like its section.
 */
const TRACK = [
  { id: 'listen', label: 'Hear it', shape: 'burst' },
  { id: 'how', label: 'How it works', shape: 'pulse' },
  { id: 'syllabus', label: 'Your syllabus', shape: 'steady' },
  { id: 'no-pressure', label: 'No pressure', shape: 'calm' },
  { id: 'where', label: 'Where it fits', shape: 'wander' },
  { id: 'start', label: 'Start', shape: 'rise' },
];

const STEPS = [
  ['Upload', 'Your notes, slides, PDFs or lecture recordings.'],
  ['Listen', 'A teacher explains it, one idea at a time.'],
  ['Answer', 'Say what you think. The lesson adapts to you.'],
];

/**
 * A word in the headline that cycles.
 *
 * The words are the answer to "when would I use this", which is the one thing
 * a listening product has to establish in the first second. Rotating them says
 * "any of these" faster than a list underneath the headline would.
 *
 * Every word is rendered stacked in the same grid cell, so the line never
 * reflows as they swap and the headline cannot change height mid-animation.
 * Reduced motion holds the first word still.
 */
function rotatingWord(words, { interval = 2200 } = {}) {
  const reduced =
    typeof matchMedia === 'function' &&
    matchMedia('(prefers-reduced-motion: reduce)').matches;

  const spans = words.map((word, index) =>
    h('span', { class: 'rotator-word', dataset: { state: index === 0 ? 'in' : 'below' } }, word)
  );

  const node = h(
    'span',
    {
      class: 'rotator',
      role: 'text',
      // Screen readers get the whole list once, rather than a word that keeps
      // changing under them.
      'aria-label': `${words.slice(0, -1).join(', ')} or ${words[words.length - 1]}`,
    },
    h('span', { class: 'visually-hidden' }, words.join(', ')),
    h('span', { class: 'rotator-stack', 'aria-hidden': 'true' }, spans)
  );

  if (reduced || words.length < 2) return node;

  let current = 0;
  let timer = null;

  function advance() {
    spans[current].dataset.state = 'above';
    current = (current + 1) % spans.length;
    spans[current].dataset.state = 'below';
    // Next frame, so the browser paints the start position before the move.
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        spans[current].dataset.state = 'in';
        spans.forEach((span, index) => {
          if (index !== current && span.dataset.state === 'above') {
            span.dataset.state = 'below';
          }
        });
      });
    });
  }

  function start() {
    stop();
    timer = setInterval(advance, interval);
  }
  function stop() {
    clearInterval(timer);
    timer = null;
  }

  start();
  // A headline animating in a tab nobody is looking at is wasted work.
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') stop();
    else if (node.isConnected) start();
  });
  window.addEventListener('pagehide', stop);

  return node;
}

/** Press play and the sample lesson is read aloud, one line at a time. */
function hearButton() {
  let index = 0;
  let playing = false;
  let button = null;
  let caption = null;

  function setButton(isPlaying) {
    button.replaceChildren(
      icon(isPlaying ? 'pause' : 'play', { size: 18, filled: !isPlaying, stroke: 2 }),
      document.createTextNode(isPlaying ? 'Pause' : 'Hear a lesson')
    );
    button.setAttribute('aria-label', isPlaying ? 'Pause the sample' : 'Play a sample lesson');
  }

  function speakNext() {
    if (index >= SAMPLE.length) {
      stop();
      caption.textContent = 'That is a lesson. Yours are built from your own material.';
      return;
    }
    caption.textContent = SAMPLE[index];
    narrator.onEnd = () => {
      index += 1;
      if (playing) speakNext();
    };
    narrator.speak(SAMPLE[index]);
  }

  function stop() {
    playing = false;
    narrator.onEnd = null;
    narrator.stop();
    setButton(false);
  }

  function toggle() {
    if (playing) {
      stop();
      caption.textContent = 'Paused.';
      return;
    }
    if (!narrator.supported) {
      caption.textContent = 'This browser cannot speak, but your lessons can still be read.';
      return;
    }
    playing = true;
    if (index >= SAMPLE.length) index = 0;
    setButton(true);
    speakNext();
  }

  window.addEventListener('pagehide', stop);

  return {
    button: h('button', {
      class: 'btn btn-outline btn-lg',
      type: 'button',
      onClick: toggle,
      ref: (el) => { button = el; setButton(false); },
    }),
    caption: h('p', {
      class: 'screen-caption',
      'aria-live': 'polite',
      ref: (el) => { caption = el; },
    }),
  };
}

/** One full screen, saying one thing. */
function screen(id, index, ...children) {
  return h(
    'section',
    { class: 'screen', id },
    h(
      'div',
      { class: 'screen-inner' },
      h('p', { class: 'screen-eyebrow' }, `${String(index + 1).padStart(2, '0')} · ${TRACK[index].label}`),
      ...children
    )
  );
}

export function landingView() {
  const hear = hearButton();

  return h(
    'div',
    { class: 'landing' },
    waveBackdrop(TRACK),

    h(
      'header',
      { class: 'landing-nav' },
      h(
        'div',
        { class: 'container row-between' },
        brand(),
        h(
          'div',
          { class: 'row', style: { gap: 'var(--space-2)' } },
          themeToggle({ compact: true }),
          h('a', { href: '/signin', class: 'btn btn-ghost btn-sm' }, 'Sign in')
        )
      )
    ),

    h(
      'main',
      { id: 'main' },
      screen(
        'listen', 0,
        h(
          'h1',
          { class: 'screen-title screen-title-hero' },
          h('span', { class: 'screen-title-line' }, 'Your notes, taught aloud'),
          h(
            'span',
            { class: 'screen-title-line' },
            'while you ',
            rotatingWord(['walk', 'commute', 'wash up', 'train', 'cook', 'wind down'])
          )
        ),
        h('p', { class: 'screen-lede' }, 'Upload what you study. Listen to it taught. Answer out loud.'),
        h(
          'div',
          { class: 'screen-actions' },
          h('a', { href: '/signup', class: 'btn btn-primary btn-lg' }, 'Start learning'),
          hear.button
        ),
        hear.caption
      ),

      screen(
        'how', 1,
        h('h2', { class: 'screen-title' }, 'Three steps.'),
        h(
          'ol',
          { class: 'steps' },
          STEPS.map(([title, body]) => h('li', null, h('h3', null, title), h('p', null, body)))
        )
      ),

      screen(
        'syllabus', 2,
        h('h2', { class: 'screen-title' }, 'Your syllabus, not the internet.'),
        h('p', { class: 'screen-lede' },
          'Every explanation comes from what you uploaded. If your notes do not cover something, it says so.')
      ),

      screen(
        'no-pressure', 3,
        h('h2', { class: 'screen-title' }, 'Understanding, not speed.'),
        h('p', { class: 'screen-lede' },
          'No streaks, no timers, no rankings. Pause any time and come back on Thursday.')
      ),

      screen(
        'where', 4,
        h('h2', { class: 'screen-title' }, 'For the hours you cannot read.'),
        h('p', { class: 'screen-lede' }, 'Walking. On the bus. Washing up. At the gym. Before sleep.')
      ),

      screen(
        'start', 5,
        h('h2', { class: 'screen-title' }, 'Put the screen away and listen.'),
        h(
          'div',
          { class: 'screen-actions' },
          h('a', { href: '/signup', class: 'btn btn-primary btn-lg' }, 'Create your first subject')
        )
      )
    ),

    h(
      'footer',
      { class: 'landing-foot' },
      h(
        'div',
        { class: 'container row-between row-wrap' },
        h('span', { class: 'meta' }, 'Aloud'),
        h('span', { class: 'meta' }, 'Learning without time pressure.')
      )
    )
  );
}
