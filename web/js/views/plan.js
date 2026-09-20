/**
 * The study plan across every subject.
 *
 * Ordered the way the curriculum orders it, annotated with where the student
 * stands. The only thing ever marked "suggested next" is one item, because a
 * list of six urgent things is a list of six sources of guilt.
 */

import { h, icon } from '../core/dom.js';
import { api } from '../core/api.js';
import { store } from '../core/store.js';
import { lessonPlayer } from '../core/lessonplayer.js';
import { resonanceMark } from '../core/resonance.js';
import {
  accentVar, emptyState, errorState, lessonRow, loadingPage,
  minutesLabel, sectionHead, STAGE_LABEL,
} from '../components/pieces.js';

export async function planView(mountPoint, rerender) {
  mountPoint.replaceChildren(loadingPage('Loading your plan'));

  let spaces;
  try {
    ({ spaces } = await api.spaces.list());
  } catch (error) {
    mountPoint.replaceChildren(errorState(error.message, rerender));
    return;
  }

  const ready = spaces.filter((space) => space.lessonCount > 0);
  if (!ready.length) {
    mountPoint.replaceChildren(
      h(
        'div',
        { class: 'stack stack-lg' },
        h('div', { class: 'page-head' }, h('h1', null, 'Study plan')),
        emptyState({
          mark: 'route',
          title: 'Nothing planned yet',
          body: 'Add material to a subject and build a plan from it. The order comes from what depends on what.',
          action: h('a', { href: '/spaces/new', class: 'btn btn-primary' }, 'Create a subject'),
        })
      )
    );
    return;
  }

  const details = await Promise.all(
    ready.map((space) => api.spaces.get(space.id).catch(() => null))
  );

  mountPoint.replaceChildren(
    h(
      'div',
      { class: 'stack stack-lg' },
      h(
        'div',
        { class: 'page-head' },
        h('h1', null, 'Study plan'),
        h(
          'p',
          { class: 'lede' },
          'Suggested order, not a schedule. Nothing here expires and nothing is overdue.'
        )
      ),
      details.filter(Boolean).map((data) => subjectPlan(data))
    )
  );
}

function subjectPlan(data) {
  const space = data.space;
  const groups = [];
  for (const item of data.plan) {
    const last = groups[groups.length - 1];
    if (last && last.stage === item.stage) last.items.push(item);
    else groups.push({ stage: item.stage, label: item.stageLabel || STAGE_LABEL[item.stage], items: [item] });
  }

  return h(
    'section',
    { class: 'plan-subject', style: { '--accent': accentVar(space.accent) } },
    h(
      'div',
      { class: 'row-between row-wrap', style: { marginBottom: 'var(--space-5)', gap: 'var(--space-3)' } },
      h(
        'div',
        { class: 'row', style: { minWidth: 0 } },
        resonanceMark(space.id, { size: 40, color: 'var(--accent)' }),
        h(
          'div',
          { style: { minWidth: 0 } },
          h('h2', null, space.title),
          h('p', { class: 'meta' },
            `${data.plan.length} ${data.plan.length === 1 ? 'lesson' : 'lessons'}`)
        )
      ),
      h('a', { href: `/space/${space.id}`, class: 'btn btn-ghost btn-sm' }, 'Open subject')
    ),
    data.recommendation
      ? h(
          'div',
          { class: 'plan-next' },
          h(
            'div',
            { style: { minWidth: 0 } },
            h('p', { class: 'meta' }, 'Suggested next'),
            h('p', { class: 'strong truncate' }, data.recommendation.title),
            h('p', { class: 'meta clamp-2' }, data.recommendation.reason)
          ),
          h(
            'button',
            {
              class: 'btn btn-primary btn-sm',
              onClick: () => lessonPlayer.open(data.recommendation.lessonId, { autoplay: true }),
            },
            icon('play', { size: 13, filled: true, stroke: 0 }),
            'Listen'
          )
        )
      : null,
    groups.map((group) =>
      h(
        'div',
        { style: { marginTop: 'var(--space-5)' } },
        sectionHead(group.label),
        h(
          'div',
          { class: 'rows' },
          group.items.map((item) =>
            lessonRow(
              { ...item, id: item.lessonId },
              {
                onOpen: () => lessonPlayer.open(item.lessonId, { autoplay: true }),
                note: [
                  minutesLabel(item.estimatedMinutes),
                  !item.prerequisitesMet ? 'builds on an earlier idea' : '',
                ].filter(Boolean).join(' · '),
              }
            )
          )
        )
      )
    )
  );
}
