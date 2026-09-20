/**
 * Progress.
 *
 * Informative, not judgmental. There is no score, no comparison, no time
 * spent, and no count of what is left undone. What it shows is where each
 * concept stands and what the teacher made of recent answers, both of which
 * help a student decide what to do next.
 */

import { h, icon } from '../core/dom.js';
import { api } from '../core/api.js';
import { lessonPlayer } from '../core/lessonplayer.js';
import { resonanceMark } from '../core/resonance.js';
import {
  accentVar, emptyState, errorState, loadingPage, masteryMeter,
  relativeTime, sectionHead, STAGE_LABEL,
} from '../components/pieces.js';

const ORDER = ['mastered', 'understood', 'developing', 'needs_review', 'not_started'];
const LABEL = {
  mastered: 'Solid',
  understood: 'Understood',
  developing: 'Building',
  needs_review: 'Worth revisiting',
  not_started: 'Not started',
};
const COLOR = {
  mastered: 'var(--mastery-4)',
  understood: 'var(--mastery-3)',
  developing: 'var(--mastery-2)',
  needs_review: 'var(--mastery-1)',
  not_started: 'var(--mastery-0)',
};

/**
 * A single stacked bar of where a subject's concepts stand.
 * One bar, not five numbers: the shape is the information.
 */
function distribution(counts, total) {
  if (!total) return null;
  return h(
    'div',
    { class: 'distribution', role: 'img', 'aria-label': describe(counts) },
    ORDER.filter((state) => counts[state] > 0).map((state) =>
      h('i', {
        style: { width: `${(counts[state] / total) * 100}%`, background: COLOR[state] },
        title: `${counts[state]} ${LABEL[state].toLowerCase()}`,
      })
    )
  );
}

function describe(counts) {
  return ORDER.filter((state) => counts[state] > 0)
    .map((state) => `${counts[state]} ${LABEL[state].toLowerCase()}`)
    .join(', ');
}

function legend(counts) {
  return h(
    'div',
    { class: 'row row-wrap', style: { gap: 'var(--space-4)' } },
    ORDER.filter((state) => counts[state] > 0).map((state) =>
      h(
        'span',
        { class: 'row', style: { gap: 'var(--space-2)', fontSize: 'var(--text-xs)' } },
        h('i', {
          style: {
            width: '8px', height: '8px', borderRadius: '999px',
            background: COLOR[state], display: 'block', flex: 'none',
          },
        }),
        h('span', { class: 'faint' }, `${counts[state]} ${LABEL[state].toLowerCase()}`)
      )
    )
  );
}

export async function progressView(mountPoint, rerender) {
  mountPoint.replaceChildren(loadingPage('Loading your progress'));

  let data;
  try {
    data = await api.progress();
  } catch (error) {
    mountPoint.replaceChildren(errorState(error.message, rerender));
    return;
  }

  const withConcepts = data.spaces.filter((space) => space.concepts.length);

  if (!withConcepts.length) {
    mountPoint.replaceChildren(
      h(
        'div',
        { class: 'stack stack-lg' },
        h('div', { class: 'page-head' }, h('h1', null, 'Progress')),
        emptyState({
          mark: 'chart',
          title: 'Nothing to show yet',
          body: 'Once you have listened to a lesson and answered a question or two, this fills in.',
          action: h('a', { href: '/spaces', class: 'btn btn-primary' }, 'Go to subjects'),
        })
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
        h('h1', null, 'Progress'),
        h(
          'p',
          { class: 'lede' },
          'Where each idea stands, in the teacher\'s reading of your answers. ' +
            'Nothing here is a grade, and nothing is late.'
        )
      ),

      withConcepts.map((space) => {
        const total = space.concepts.length;
        return h(
          'section',
          { class: 'progress-subject', style: { '--accent': accentVar(space.accent) } },
          h(
            'div',
            { class: 'row-between row-wrap', style: { gap: 'var(--space-3)', marginBottom: 'var(--space-4)' } },
            h(
              'div',
              { class: 'row', style: { minWidth: 0 } },
              resonanceMark(space.spaceId, { size: 40, color: 'var(--accent)' }),
              h(
                'div',
                { style: { minWidth: 0 } },
                h('h2', null, space.title),
                h('p', { class: 'meta' }, `${total} ${total === 1 ? 'concept' : 'concepts'}`)
              )
            ),
            h('a', { href: `/space/${space.spaceId}?view=map`, class: 'btn btn-ghost btn-sm' },
              icon('layers', { size: 14 }), 'See the map')
          ),
          distribution(space.counts, total),
          h('div', { style: { marginTop: 'var(--space-3)' } }, legend(space.counts)),
          h(
            'div',
            { class: 'rows', style: { marginTop: 'var(--space-5)' } },
            space.concepts.map((concept) =>
              h(
                'button',
                {
                  class: 'row-item',
                  disabled: !concept.lessonId,
                  onClick: () => concept.lessonId && lessonPlayer.open(concept.lessonId, { autoplay: true }),
                },
                h(
                  'span',
                  { class: 'row-item-body' },
                  h('span', { class: 'row-item-title truncate' }, concept.name),
                  h(
                    'span',
                    { class: 'row-item-sub' },
                    [
                      concept.stageLabel || STAGE_LABEL[concept.stage],
                      concept.evidence ? `${concept.evidence} ${concept.evidence === 1 ? 'answer' : 'answers'}` : 'not answered yet',
                    ].filter(Boolean).join(' · ')
                  )
                ),
                h('span', { class: 'meta nowrap' }, concept.masteryLabel),
                masteryMeter(concept.mastery)
              )
            )
          )
        );
      }),

      data.assessments?.length
        ? h(
            'section',
            null,
            sectionHead('What the teacher noted'),
            h(
              'div',
              { class: 'stack' },
              data.assessments.slice(0, 6).map((assessment) =>
                h(
                  'div',
                  { class: 'card assessment-card' },
                  h(
                    'div',
                    { class: 'row-between', style: { alignItems: 'flex-start', gap: 'var(--space-3)' } },
                    h(
                      'div',
                      { style: { minWidth: 0 } },
                      h('p', { class: 'strong' }, assessment.outcomeLabel),
                      h('p', { class: 'muted', style: { fontSize: 'var(--text-sm)', marginTop: 'var(--space-1)' } },
                        assessment.recommendation)
                    ),
                    h('span', { class: 'meta nowrap' }, relativeTime(assessment.createdAt))
                  ),
                  assessment.gaps?.length
                    ? h(
                        'p',
                        { class: 'meta', style: { marginTop: 'var(--space-3)' } },
                        `Still loose: ${assessment.gaps[0]}`
                      )
                    : null,
                  assessment.confidence < 0.45
                    ? h(
                        'p',
                        { class: 'meta', style: { marginTop: 'var(--space-2)', fontStyle: 'italic' } },
                        'Based on limited evidence. Answering a couple more questions would tell us both more.'
                      )
                    : null
                )
              )
            )
          )
        : null
    )
  );
}
