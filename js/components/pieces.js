/**
 * Shared pieces: mastery indicators, empty and loading states, small cards.
 *
 * Everything here is written to the product's no-pressure rule. There are no
 * percentages on concepts, no counts of what is left, and no language that
 * implies a student is behind.
 */

import { h, icon } from '../core/dom.js';
import { resonanceMark } from '../core/resonance.js';

export const MASTERY_LEVEL = {
  not_started: 0,
  needs_review: 1,
  developing: 2,
  understood: 3,
  mastered: 4,
};

export const STAGE_LABEL = {
  foundations: 'Foundations',
  core: 'Core ideas',
  application: 'Putting it to work',
  higher_order: 'Going deeper',
  recall: 'Bringing it back',
  assessment: 'Checking understanding',
  revision: 'Revisiting',
};

export function masteryTag(state, label) {
  return h(
    'span',
    { class: 'mastery', dataset: { state: state || 'not_started' } },
    h('i', { class: 'mastery-dot' }),
    label || 'Not started'
  );
}

export function masteryMeter(state) {
  const level = MASTERY_LEVEL[state] ?? 0;
  return h(
    'span',
    {
      class: 'mastery-meter',
      dataset: { level: String(level) },
      role: 'img',
      'aria-label': `Understanding: ${state.replace('_', ' ')}`,
    },
    h('i'), h('i'), h('i'), h('i')
  );
}

/**
 * A speech voice's name, minus the vendor prefix nobody reads.
 * "Microsoft Libby Online (Natural) - English (United Kingdom)" -> "Libby".
 */
export function shortVoiceName(name) {
  if (!name) return 'Default';
  let label = String(name)
    .replace(/^(Google|Microsoft|Apple|Chrome OS|Android)\s+/i, '')
    .replace(/\s*\(.*?\)\s*/g, ' ')
    .replace(/\s*-\s*English.*$/i, '')
    .replace(/Online/i, '')
    .replace(/\s{2,}/g, ' ')
    .trim();
  if (label.length > 22) label = `${label.slice(0, 21)}…`;
  return label || 'Default';
}

export function accentVar(index = 0) {
  return `var(--accent-${((index % 6) + 6) % 6})`;
}

/** Minutes, written the way a person says them. */
export function minutesLabel(minutes) {
  if (!minutes) return '';
  if (minutes < 60) return `about ${minutes} min`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? `about ${hours}h ${rest}m` : `about ${hours}h`;
}

export function formatTime(seconds) {
  const total = Math.max(0, Math.round(seconds || 0));
  const minutes = Math.floor(total / 60);
  const remainder = total % 60;
  return `${minutes}:${String(remainder).padStart(2, '0')}`;
}

export function relativeTime(iso) {
  if (!iso) return '';
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return '';
  const seconds = Math.round((Date.now() - then) / 1000);
  if (seconds < 90) return 'just now';
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return hours === 1 ? 'an hour ago' : `${hours} hours ago`;
  const days = Math.round(hours / 24);
  if (days === 1) return 'yesterday';
  if (days < 7) return `${days} days ago`;
  const weeks = Math.round(days / 7);
  if (weeks < 5) return weeks === 1 ? 'last week' : `${weeks} weeks ago`;
  return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

export function bytesLabel(bytes) {
  if (!bytes) return '';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/* ---------------------------------------------------------------------- */

export function emptyState({ mark = 'sparkle', title, body, action }) {
  return h(
    'div',
    { class: 'empty' },
    h(
      'div',
      { class: 'empty-mark', style: { color: 'var(--ink-ghost)' } },
      icon(mark, { size: 30, stroke: 1.3 })
    ),
    h('h3', null, title),
    body && h('p', null, body),
    action
  );
}

export function loadingRows(count = 3) {
  return h(
    'div',
    { class: 'stack', 'aria-busy': 'true', 'aria-label': 'Loading' },
    Array.from({ length: count }, () =>
      h(
        'div',
        { class: 'card', style: { padding: 'var(--space-4)' } },
        h('div', { class: 'skeleton skeleton-line', style: { width: '45%' } }),
        h('div', { class: 'skeleton skeleton-line' })
      )
    )
  );
}

export function loadingPage(label = 'Loading') {
  return h(
    'div',
    { class: 'stack stack-lg', 'aria-busy': 'true', 'aria-label': label },
    h('div', { class: 'skeleton', style: { height: '2rem', width: '40%' } }),
    h('div', { class: 'skeleton', style: { height: '9rem', borderRadius: 'var(--radius-xl)' } }),
    loadingRows(3)
  );
}

export function errorState(message, onRetry) {
  return h(
    'div',
    { class: 'empty' },
    h('div', { class: 'empty-mark', style: { color: 'var(--trouble)' } }, icon('warn', { size: 28 })),
    h('h3', null, 'That did not load'),
    h('p', null, message),
    onRetry && h('button', { class: 'btn btn-outline', onClick: onRetry }, icon('refresh', { size: 15 }), 'Try again')
  );
}

export function sectionHead(title, action) {
  return h('div', { class: 'section-head' }, h('h2', null, title), action && h('div', { class: 'section-action' }, action));
}

/**
 * The chip beside a plan row.
 *
 * Only one thing at a time is highlighted, and only when it is something to
 * act on. "Solid" gets no chip because the mastery meter already says so, and
 * giving it the same treatment as "Ready to revisit" would make praise and a
 * nudge look identical. Revisiting is offered quietly: it is an option, not a
 * task that has fallen due.
 */
function recommendationChip(recommendation) {
  if (!recommendation) return null;
  if (recommendation === 'Suggested next') {
    return h('span', { class: 'chip chip-brass' }, recommendation);
  }
  if (recommendation === 'Ready to revisit') {
    return h('span', { class: 'chip' }, recommendation);
  }
  return null;
}

/** A lesson as a flat row: mark, title, stage, understanding. */
export function lessonRow(lesson, { onOpen, note = '', disabled = false } = {}) {
  return h(
    'button',
    {
      class: 'row-item',
      onClick: disabled ? null : onOpen,
      'aria-disabled': disabled ? 'true' : null,
      type: 'button',
    },
    h('span', { style: { color: 'var(--ink-ghost)' } }, resonanceMark(lesson.conceptId || lesson.id, { size: 34 })),
    h(
      'span',
      { class: 'row-item-body' },
      h('span', { class: 'row-item-title truncate' }, lesson.title),
      h(
        'span',
        { class: 'row-item-sub truncate' },
        note || lesson.stageLabel || STAGE_LABEL[lesson.stage] || ''
      )
    ),
    recommendationChip(lesson.recommendation),
    masteryMeter(lesson.mastery || 'not_started')
  );
}

/** The status of a resource moving through ingestion. */
export function resourceStatus(resource) {
  const map = {
    queued: ['chip', 'Waiting to be read'],
    extracting: ['chip chip-quiet', 'Reading'],
    structuring: ['chip chip-quiet', 'Finding the structure'],
    modelling: ['chip chip-quiet', 'Working out the concepts'],
    ready: ['chip chip-mint', 'Analysed'],
    failed: ['chip chip-trouble', 'Could not be read'],
  };
  const [cls, label] = map[resource.status] || map.queued;
  return h('span', { class: cls }, label);
}

export const KIND_ICON = {
  pdf: 'file',
  docx: 'file',
  pptx: 'file',
  text: 'note',
  markdown: 'note',
  note: 'note',
  url: 'link',
};
