/**
 * Home.
 *
 * A calm overview, in the order a returning student cares about: what you were
 * in the middle of, what to do next, what is worth revisiting, and your
 * subjects. Deliberately absent: streaks, daily goals, time studied, and
 * anything that counts what has not been done yet.
 */

import { h, icon } from '../core/dom.js';
import { api } from '../core/api.js';
import { store } from '../core/store.js';
import { navigate } from '../core/router.js';
import { lessonPlayer } from '../core/lessonplayer.js';
import { resonanceMark, resonanceFigure } from '../core/resonance.js';
import {
  emptyState, errorState, loadingPage, masteryTag, minutesLabel,
  relativeTime, sectionHead, accentVar,
} from '../components/pieces.js';

function greeting(name) {
  const hour = new Date().getHours();
  const part = hour < 5 ? 'Still up' : hour < 12 ? 'Good morning' : hour < 18 ? 'Good afternoon' : 'Good evening';
  return name ? `${part}, ${name}` : part;
}

function continueCard(card) {
  const percent = Math.round((card.progress || 0) * 100);
  return h(
    'article',
    { class: 'card-feature continue-card', style: { '--accent': accentVar(card.accent) } },
    h(
      'div',
      { class: 'continue-figure' },
      resonanceMark(card.lessonId, { size: 76, color: 'var(--accent)' })
    ),
    h(
      'div',
      { class: 'continue-body' },
      h('p', { class: 'continue-eyebrow' }, card.spaceTitle),
      h('h2', { class: 'continue-title' }, card.title),
      card.subtitle ? h('p', { class: 'muted clamp-2', style: { fontSize: 'var(--text-sm)' } }, card.subtitle) : null,
      h(
        'div',
        { class: 'continue-meter' },
        h('div', { class: 'track', style: { flex: '1' } }, h('i', { style: { width: `${percent}%`, background: 'var(--accent)' } })),
        h('span', { class: 'meta nowrap' }, percent > 3 ? `${percent}% through` : minutesLabel(card.estimatedMinutes))
      ),
      h(
        'div',
        { class: 'row row-wrap', style: { gap: 'var(--space-2)', marginTop: 'var(--space-4)' } },
        h(
          'button',
          {
            class: 'btn btn-primary btn-lg',
            onClick: () => lessonPlayer.open(card.lessonId, { autoplay: true }),
          },
          icon('play', { size: 16, filled: true, stroke: 0 }),
          'Carry on listening'
        ),
        h(
          'a',
          { href: `/space/${card.spaceId}`, class: 'btn btn-outline btn-lg' },
          'Open subject'
        )
      )
    )
  );
}

function recommendationCard(recommendation) {
  const verb = {
    start: 'Start',
    continue: 'Carry on with',
    review: 'Another pass at',
    assess: 'Check your understanding of',
    done: 'Revisit',
  }[recommendation.kind] || 'Next';

  return h(
    'article',
    { class: 'card next-card' },
    h(
      'div',
      { class: 'row-between', style: { alignItems: 'flex-start' } },
      h(
        'div',
        { style: { minWidth: 0 } },
        h('p', { class: 'meta' }, recommendation.spaceTitle || 'Suggested next'),
        h('h3', { style: { margin: 'var(--space-1) 0 var(--space-2)' } }, `${verb} ${recommendation.title}`),
        h('p', { class: 'muted', style: { fontSize: 'var(--text-sm)' } }, recommendation.reason)
      ),
      resonanceMark(recommendation.conceptId || recommendation.lessonId, { size: 40, color: 'var(--ink-ghost)' })
    ),
    h(
      'button',
      {
        class: 'btn btn-outline',
        style: { marginTop: 'var(--space-4)' },
        onClick: () => lessonPlayer.open(recommendation.lessonId, { autoplay: true }),
      },
      icon('play', { size: 14, filled: true, stroke: 0 }),
      'Begin'
    )
  );
}

function reviewList(items) {
  return h(
    'section',
    null,
    sectionHead('Worth revisiting'),
    h(
      'div',
      { class: 'rows' },
      items.map((item) =>
        h(
          'button',
          {
            class: 'row-item',
            onClick: () => lessonPlayer.open(item.lessonId, { autoplay: true }),
          },
          h('span', { style: { color: 'var(--mastery-1)' } }, resonanceMark(item.conceptId, { size: 30 })),
          h(
            'span',
            { class: 'row-item-body' },
            h('span', { class: 'row-item-title truncate' }, item.conceptName),
            h(
              'span',
              { class: 'row-item-sub truncate' },
              item.note || `${item.spaceTitle} · another explanation should settle it`
            )
          ),
          h('span', { class: 'chip' }, 'Listen again')
        )
      )
    )
  );
}

function spaceCard(space) {
  const percent = Math.round((space.progress || 0) * 100);
  const status = {
    empty: 'No material yet',
    analysing: 'Being read',
    ready: `${space.lessonCount} ${space.lessonCount === 1 ? 'lesson' : 'lessons'}`,
    failed: 'Needs attention',
  }[space.status] || '';

  return h(
    'a',
    { href: `/space/${space.id}`, class: 'card space-card', style: { '--accent': accentVar(space.accent) } },
    h(
      'div',
      { class: 'row-between', style: { alignItems: 'flex-start', marginBottom: 'var(--space-4)' } },
      resonanceMark(space.id, { size: 44, color: 'var(--accent)' }),
      space.needsReview
        ? h('span', { class: 'chip chip-brass' }, `${space.needsReview} to revisit`)
        : null
    ),
    h('h3', { class: 'clamp-2' }, space.title),
    h('p', { class: 'meta', style: { marginTop: 'var(--space-1)' } }, [space.level, status].filter(Boolean).join(' · ')),
    space.conceptCount
      ? h(
          'div',
          { class: 'track', style: { marginTop: 'var(--space-4)' } },
          h('i', { style: { width: `${percent}%`, background: 'var(--accent)' } })
        )
      : null
  );
}

function recentList(recent) {
  return h(
    'section',
    null,
    sectionHead('Recently'),
    h(
      'div',
      { class: 'rows' },
      recent.map((item) =>
        h(
          'button',
          { class: 'row-item', onClick: () => lessonPlayer.open(item.lessonId) },
          h(
            'span',
            { class: 'row-item-body' },
            h('span', { class: 'row-item-title truncate' }, item.title),
            h('span', { class: 'row-item-sub' }, relativeTime(item.updatedAt))
          ),
          masteryTag(item.mastery, item.masteryLabel)
        )
      )
    )
  );
}

export async function dashboardView(mountPoint, rerender) {
  const cached = store.get().dashboard;
  if (!cached) mountPoint.replaceChildren(loadingPage('Loading your learning'));

  let data = cached;
  try {
    data = await api.dashboard();
    store.set({ dashboard: data });
  } catch (error) {
    if (!cached) {
      mountPoint.replaceChildren(errorState(error.message, rerender));
      return;
    }
  }

  const user = store.get().user;

  if (!data.spaces.length) {
    mountPoint.replaceChildren(
      h(
        'div',
        { class: 'stack stack-lg' },
        h(
          'div',
          { class: 'page-head' },
          h('h1', { class: 'display' }, greeting(user?.displayName)),
          h('p', { class: 'lede' }, 'Nothing here yet. Give it something to teach you.')
        ),
        h(
          'div',
          { class: 'card-feature', style: { textAlign: 'center' } },
          h('div', { style: { display: 'flex', justifyContent: 'center', marginBottom: 'var(--space-5)' } },
            resonanceMark('aloud-empty', { size: 60, color: 'var(--ink-ghost)' })),
          h('h2', { style: { marginBottom: 'var(--space-2)' } }, 'Create your first subject'),
          h('p', { class: 'lede', style: { marginInline: 'auto', marginBottom: 'var(--space-5)' } },
            'Name what you are studying, add the material you already have, and a course gets built from it.'),
          h('a', { href: '/spaces/new', class: 'btn btn-primary btn-lg' }, icon('plus', { size: 16 }), 'New subject')
        )
      )
    );
    return;
  }

  mountPoint.replaceChildren(
    h(
      'div',
      { class: 'stack stack-lg' },
      h(
        'div',
        { class: 'page-head' },
        h('h1', { class: 'display' }, greeting(user?.displayName)),
        h(
          'p',
          { class: 'lede' },
          data.continue
            ? 'You left off part way through. Pick it up whenever suits.'
            : 'Everything is where you left it.'
        )
      ),

      data.continue ? continueCard(data.continue) : null,

      data.recommendation && (!data.continue || data.recommendation.lessonId !== data.continue.lessonId)
        ? h(
            'section',
            null,
            sectionHead(data.continue ? 'After that' : 'Suggested next'),
            recommendationCard(data.recommendation)
          )
        : null,

      data.needsReview?.length ? reviewList(data.needsReview) : null,

      h(
        'section',
        null,
        sectionHead(
          'Your subjects',
          h('a', { href: '/spaces/new', class: 'btn btn-ghost btn-sm' }, icon('plus', { size: 14 }), 'New')
        ),
        h('div', { class: 'grid grid-3' }, data.spaces.map(spaceCard))
      ),

      data.recent?.length ? recentList(data.recent) : null
    )
  );
}
