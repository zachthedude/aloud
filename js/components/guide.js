/**
 * Hum, the guide.
 *
 * A small companion in the corner that says one useful thing about wherever
 * you are, and will read it aloud. Voice-first products have a real problem:
 * a student who has put the phone down cannot see a tooltip. Hum answers that
 * by being the one part of the interface that is happy to be listened to
 * rather than looked at.
 *
 * Three rules keep it from being Clippy:
 *
 *   It says one thing, once. No queue of tips, no nagging, no reappearing
 *   after it has been closed.
 *   It only speaks when asked. The narrator belongs to the lesson; Hum
 *   borrows it on a button press and never interrupts teaching.
 *   It is always dismissible, and it stays dismissed.
 *
 * The face is drawn from the same vocabulary as the rest of the product: two
 * eyes and a waveform for a mouth. It blinks, slowly, and stops entirely
 * under reduced motion.
 */

import { h, icon } from '../core/dom.js';
import { narrator } from '../core/narrator.js';

const KEY = 'aloud-guide';

/**
 * One line per place. Written as a person would say it: what this screen is
 * for, or the next thing worth doing, never a description of the furniture.
 */
const LINES = [
  [/^\/app\/?$/, 'This is where you left off. Press play on anything here and I will start talking.'],
  [/^\/spaces\/new/, 'Name the subject and the exam board if you know it. You can add the material next.'],
  [/^\/spaces\/[^/]+/, 'Tag your files. A mark scheme unlocks the examiner, and past papers unlock the pattern hunter.'],
  [/^\/spaces\/?$/, 'One subject per exam works best. Everything you upload to it gets taught and tested together.'],
  [/^\/plan/, 'The order changes as you answer. Shaky topics move up on their own, so you can just follow it down.'],
  [/^\/modes/, 'Sixteen ways of working, all on the same material. Switch off any you find annoying.'],
  [/^\/library/, 'Everything you have uploaded, across all subjects. Removing a file here does not delete what it taught you.'],
  [/^\/progress/, 'Amber is worth your time. Green is done. Nothing here is a score, and nothing counts down.'],
  [/^\/settings/, 'Speed and voice are the two worth setting. Try 1.5 and see whether you can still follow.'],
];

const FALLBACK = 'I am Hum. I will tell you what is worth doing on each screen, and read it out if you want.';

function lineFor(path) {
  const match = LINES.find(([pattern]) => pattern.test(path));
  return match ? match[1] : FALLBACK;
}

function face() {
  // Two eyes and a waveform mouth. The same visual language as the brand
  // mark, so it reads as part of the product rather than a mascot bolted on.
  return h(
    'svg',
    { class: 'guide-face', viewBox: '0 0 40 40', 'aria-hidden': 'true', focusable: 'false' },
    h('circle', { cx: 20, cy: 20, r: 18.4, class: 'guide-face-ring' }),
    h('circle', { cx: 14, cy: 16.5, r: 1.9, class: 'guide-eye' }),
    h('circle', { cx: 26, cy: 16.5, r: 1.9, class: 'guide-eye' }),
    h('path', {
      class: 'guide-mouth',
      d: 'M11 25.5h2.6l2-4 2.4 7 2.4-7 2 4H26',
      fill: 'none',
      'stroke-linecap': 'round',
      'stroke-linejoin': 'round',
    })
  );
}

function remembered() {
  try {
    return localStorage.getItem(KEY);
  } catch (error) {
    // Private windows throw on access. A guide that cannot remember being
    // closed is annoying; one that crashes the page is worse.
    return null;
  }
}

function remember(value) {
  try {
    localStorage.setItem(KEY, value);
  } catch (error) {
    /* not remembering is survivable */
  }
}

export function guide({ onClose } = {}) {
  if (remembered() === 'closed') return null;

  let bubble = null;
  let speakButton = null;
  let speaking = false;

  const node = h('div', { class: 'guide', dataset: { open: 'true' } });

  function setSpeaking(value) {
    speaking = value;
    node.dataset.speaking = String(value);
    if (!speakButton) return;
    speakButton.replaceChildren(icon(value ? 'pause' : 'volume', { size: 15 }));
    speakButton.setAttribute('aria-label', value ? 'Stop reading' : 'Read this out');
  }

  function speak() {
    if (speaking) {
      narrator.stop();
      setSpeaking(false);
      return;
    }
    if (!narrator.supported) {
      bubble.textContent = 'This browser has no speech engine, so I can only write it down.';
      return;
    }
    narrator.onEnd = () => setSpeaking(false);
    narrator.speak(bubble.textContent);
    setSpeaking(true);
  }

  function close() {
    narrator.stop();
    remember('closed');
    node.remove();
    onClose?.();
  }

  function collapse() {
    const open = node.dataset.open === 'true';
    node.dataset.open = String(!open);
    badge.setAttribute('aria-expanded', String(!open));
    remember(open ? 'small' : 'open');
    if (open) {
      narrator.stop();
      setSpeaking(false);
    }
  }

  let badge = null;

  node.append(
    h(
      'div',
      { class: 'guide-bubble', role: 'status', 'aria-live': 'polite' },
      h('p', { class: 'guide-line', ref: (el) => { bubble = el; } }, FALLBACK),
      h(
        'div',
        { class: 'guide-actions' },
        h('button', {
          class: 'btn-icon guide-speak',
          type: 'button',
          onClick: speak,
          ref: (el) => { speakButton = el; setSpeaking(false); },
        }),
        h(
          'button',
          { class: 'btn btn-ghost btn-sm', type: 'button', onClick: close },
          'Not now'
        )
      )
    ),
    h(
      'button',
      {
        class: 'guide-badge',
        type: 'button',
        onClick: collapse,
        'aria-expanded': 'true',
        'aria-label': 'Hum, your guide',
        ref: (el) => { badge = el; },
      },
      face()
    )
  );

  // On a phone the bubble would cover a card, so Hum starts as just a face
  // until asked. On a wide screen there is room for it to say hello.
  const startsSmall = remembered() === 'small'
    || (remembered() === null && window.innerWidth < 700);
  if (startsSmall) {
    node.dataset.open = 'false';
    badge.setAttribute('aria-expanded', 'false');
  }

  // Kept in sync by the router rather than the other way round, so the guide
  // never has to know which views exist.
  node.__say = (path) => {
    const line = lineFor(path);
    if (bubble.textContent === line) return;
    bubble.textContent = line;
    if (speaking) {
      narrator.stop();
      setSpeaking(false);
    }
  };

  return node;
}
