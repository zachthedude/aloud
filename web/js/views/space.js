/**
 * Subjects: the list, creating one, and a subject's own page.
 *
 * A subject page has three views, because a student needs three different
 * things from it: the plan (what to learn next), the material (what it was
 * built from), and the map (how the ideas hang together).
 */

import { h, icon, focus } from '../core/dom.js';
import { api } from '../core/api.js';
import { store } from '../core/store.js';
import { navigate } from '../core/router.js';
import { lessonPlayer } from '../core/lessonplayer.js';
import { resonanceMark } from '../core/resonance.js';
import { uploader, resourceCard } from '../components/uploader.js';
import { confirmDialog, notifyError, toast } from '../core/feedback.js';
import { knowledgeMap } from '../components/knowledgemap.js';
import {
  accentVar, emptyState, errorState, loadingPage, lessonRow, masteryTag,
  minutesLabel, sectionHead, STAGE_LABEL,
} from '../components/pieces.js';

/** "1 lesson", "3 lessons", and nothing at all for zero. */
function plural(count, word) {
  if (!count) return '';
  return `${count} ${word}${count === 1 ? '' : 's'}`;
}

/* --- The list ----------------------------------------------------------- */

export async function spacesView(mountPoint, rerender) {
  mountPoint.replaceChildren(loadingPage('Loading your subjects'));
  let spaces;
  try {
    ({ spaces } = await api.spaces.list());
    store.set({ spaces });
  } catch (error) {
    mountPoint.replaceChildren(errorState(error.message, rerender));
    return;
  }

  mountPoint.replaceChildren(
    h(
      'div',
      { class: 'stack stack-lg' },
      h(
        'div',
        { class: 'page-head row-between row-wrap' },
        h(
          'div',
          null,
          h('h1', null, 'Subjects'),
          h('p', { class: 'lede' }, 'Each one holds its own material, plan and progress.')
        ),
        h('a', { href: '/spaces/new', class: 'btn btn-primary' }, icon('plus', { size: 15 }), 'New subject')
      ),
      spaces.length
        ? h(
            'div',
            { class: 'grid grid-3' },
            spaces.map((space) => {
              const percent = Math.round((space.progress || 0) * 100);
              return h(
                'a',
                { href: `/space/${space.id}`, class: 'card space-card', style: { '--accent': accentVar(space.accent) } },
                h(
                  'div',
                  { class: 'row-between', style: { alignItems: 'flex-start', marginBottom: 'var(--space-4)' } },
                  resonanceMark(space.id, { size: 44, color: 'var(--accent)' }),
                  space.status === 'analysing'
                    ? h('span', { class: 'chip chip-quiet' }, 'Being read')
                    : space.needsReview
                      ? h('span', { class: 'chip chip-brass' }, `${space.needsReview} to revisit`)
                      : null
                ),
                h('h3', { class: 'clamp-2' }, space.title),
                h(
                  'p',
                  { class: 'meta', style: { marginTop: 'var(--space-1)' } },
                  [
                    space.level,
                    space.lessonCount
                      ? plural(space.lessonCount, 'lesson')
                      : plural(space.resourceCount, 'file') || 'no material yet',
                  ].filter(Boolean).join(' · ')
                ),
                space.conceptCount
                  ? h('div', { class: 'track', style: { marginTop: 'var(--space-4)' } },
                      h('i', { style: { width: `${percent}%`, background: 'var(--accent)' } }))
                  : null
              );
            })
          )
        : emptyState({
            mark: 'layers',
            title: 'No subjects yet',
            body: 'A subject holds the material for one course, and everything built from it.',
            action: h('a', { href: '/spaces/new', class: 'btn btn-primary' }, 'Create one'),
          })
    )
  );
}

/* --- Creating one ------------------------------------------------------- */

const EXAMPLES = ['Grade 10 Chemistry', 'IB Geography', 'Physics: Forces', 'SAT Biology', 'World History'];

export function newSpaceView(mountPoint) {
  let title = null;
  let level = null;
  let submit = null;
  let errorSlot = null;

  async function onSubmit(event) {
    event.preventDefault();
    errorSlot.replaceChildren();
    const value = title.value.trim();
    if (value.length < 2) {
      errorSlot.replaceChildren(
        h('div', { class: 'notice notice-trouble', role: 'alert' }, 'Give your subject a name first.')
      );
      focus(title);
      return;
    }
    submit.disabled = true;
    submit.textContent = 'Creating...';
    try {
      const { space } = await api.spaces.create({ title: value, level: level.value.trim() });
      store.set({ dashboard: null });
      navigate(`/space/${space.id}`, { replace: true });
    } catch (error) {
      errorSlot.replaceChildren(
        h('div', { class: 'notice notice-trouble', role: 'alert' }, error.message)
      );
      submit.disabled = false;
      submit.textContent = 'Create subject';
    }
  }

  mountPoint.replaceChildren(
    h(
      'div',
      { class: 'narrow-page stack stack-lg' },
      h(
        'div',
        { class: 'page-head' },
        h('h1', null, 'What are you studying?'),
        h('p', { class: 'lede' }, 'Name it the way you would say it out loud. You can add the material next.')
      ),
      h(
        'form',
        { class: 'card-feature stack', onSubmit, novalidate: true },
        h('div', { ref: (el) => { errorSlot = el; } }),
        h(
          'label',
          { class: 'field' },
          h('span', { class: 'field-label' }, 'Subject'),
          h('input', {
            class: 'input', type: 'text', required: true, maxlength: '80',
            placeholder: 'Grade 10 Chemistry', autocomplete: 'off',
            ref: (el) => { title = el; },
          })
        ),
        h(
          'div',
          { class: 'row row-wrap', style: { gap: 'var(--space-2)' } },
          EXAMPLES.map((example) =>
            h(
              'button',
              {
                class: 'chip chip-outline', type: 'button',
                onClick: () => { title.value = example; focus(title); },
              },
              example
            )
          )
        ),
        h(
          'label',
          { class: 'field' },
          h('span', { class: 'field-label' }, 'Level, if it helps'),
          h('input', {
            class: 'input', type: 'text', maxlength: '60',
            placeholder: 'Grade 10, IB HL, first year...', autocomplete: 'off',
            ref: (el) => { level = el; },
          }),
          h('span', { class: 'field-hint' }, 'Used to pitch explanations. Optional.')
        ),
        h(
          'div',
          { class: 'row', style: { justifyContent: 'flex-end', gap: 'var(--space-2)' } },
          h('a', { href: '/spaces', class: 'btn btn-ghost' }, 'Cancel'),
          h('button', { class: 'btn btn-primary', type: 'submit', ref: (el) => { submit = el; } }, 'Create subject')
        )
      )
    )
  );
  focus(title);
}

/* --- A subject ---------------------------------------------------------- */

const TABS = [
  { id: 'plan', label: 'Study plan', icon: 'route' },
  { id: 'material', label: 'Material', icon: 'library' },
  { id: 'map', label: 'Knowledge map', icon: 'layers' },
];

export async function spaceView(mountPoint, spaceId, query, rerender) {
  mountPoint.replaceChildren(loadingPage('Loading subject'));

  let data;
  try {
    data = await api.spaces.get(spaceId);
  } catch (error) {
    mountPoint.replaceChildren(errorState(error.message, rerender));
    return;
  }

  const tab = TABS.some((t) => t.id === query.get('view')) ? query.get('view') : defaultTab(data);
  render(data, tab);

  function defaultTab(payload) {
    if (!payload.resources.length || payload.space.status !== 'ready') return 'material';
    return 'plan';
  }

  async function refresh({ keepTab = tab } = {}) {
    try {
      data = await api.spaces.get(spaceId);
      render(data, keepTab);
    } catch (error) {
      notifyError(error);
    }
  }

  /* -- analysis ---------------------------------------------------------- */

  let pollTimer = null;

  function stopPolling() {
    clearInterval(pollTimer);
    pollTimer = null;
  }

  async function startAnalysis() {
    try {
      const { job } = await api.spaces.analyse(spaceId);
      data.job = job;
      data.space.status = 'analysing';
      render(data, 'material');
      pollJob();
    } catch (error) {
      notifyError(error);
    }
  }

  function pollJob() {
    stopPolling();
    pollTimer = setInterval(async () => {
      try {
        const { job } = await api.spaces.job(spaceId);
        if (!job) { stopPolling(); return; }
        data.job = job;
        updateProgress(job);
        if (job.status === 'done') {
          stopPolling();
          store.set({ dashboard: null });
          toast('Your plan is ready.');
          await refresh({ keepTab: 'plan' });
        } else if (job.status === 'failed') {
          stopPolling();
          await refresh({ keepTab: 'material' });
        }
      } catch {
        stopPolling();
      }
    }, 1200);
  }

  let progressBar = null;
  let progressLabel = null;

  function updateProgress(job) {
    if (progressBar) progressBar.style.width = `${Math.round((job.progress || 0) * 100)}%`;
    if (progressLabel) progressLabel.textContent = job.step || '';
  }

  /* -- rendering --------------------------------------------------------- */

  function analysisPanel(job) {
    const running = job && (job.status === 'queued' || job.status === 'running');
    if (running) {
      if (!pollTimer) pollJob();
      return h(
        'div',
        { class: 'card analysis-panel', role: 'status' },
        h(
          'div',
          { class: 'row' },
          h('span', { class: 'spinner' }),
          h(
            'div',
            { class: 'spacer' },
            h('p', { class: 'strong', ref: (el) => { progressLabel = el; } }, job.step || 'Working'),
            h('p', { class: 'meta' }, 'This usually takes under a minute. You can leave this page.')
          )
        ),
        h(
          'div',
          { class: 'track', style: { marginTop: 'var(--space-4)' } },
          h('i', {
            ref: (el) => { progressBar = el; },
            style: { width: `${Math.round((job.progress || 0) * 100)}%` },
          })
        )
      );
    }

    if (job && job.status === 'failed') {
      return h(
        'div',
        { class: 'notice notice-trouble', role: 'alert' },
        icon('warn', { size: 16 }),
        h(
          'div',
          null,
          h('p', { class: 'strong', style: { marginBottom: 'var(--space-1)' } }, 'The plan could not be built'),
          h('p', { style: { margin: 0 } }, job.error || 'Something went wrong reading your material.'),
          h(
            'button',
            { class: 'btn btn-sm btn-outline', style: { marginTop: 'var(--space-3)' }, onClick: startAnalysis },
            icon('refresh', { size: 14 }),
            'Try again'
          )
        )
      );
    }
    return null;
  }

  function planTab() {
    if (!data.plan.length) {
      return emptyState({
        mark: 'route',
        title: 'No plan yet',
        body: 'Add your material, then build the plan from it.',
        action: h(
          'button',
          { class: 'btn btn-primary', onClick: () => render(data, 'material') },
          'Add material'
        ),
      });
    }

    // Group by stage, so the shape of the course is visible.
    const groups = [];
    for (const item of data.plan) {
      const last = groups[groups.length - 1];
      if (last && last.stage === item.stage) last.items.push(item);
      else groups.push({ stage: item.stage, label: item.stageLabel || STAGE_LABEL[item.stage], items: [item] });
    }

    return h(
      'div',
      { class: 'stack stack-lg' },
      data.recommendation
        ? h(
            'div',
            { class: 'card next-card' },
            h(
              'div',
              { class: 'row-between', style: { alignItems: 'flex-start' } },
              h(
                'div',
                { style: { minWidth: 0 } },
                h('p', { class: 'meta' }, 'Suggested next'),
                h('h3', { style: { margin: 'var(--space-1) 0 var(--space-2)' } }, data.recommendation.title),
                h('p', { class: 'muted', style: { fontSize: 'var(--text-sm)' } }, data.recommendation.reason)
              ),
              resonanceMark(data.recommendation.conceptId, { size: 40, color: 'var(--ink-ghost)' })
            ),
            h(
              'button',
              {
                class: 'btn btn-primary', style: { marginTop: 'var(--space-4)' },
                onClick: () => lessonPlayer.open(data.recommendation.lessonId, { autoplay: true }),
              },
              icon('play', { size: 14, filled: true, stroke: 0 }),
              'Start listening'
            )
          )
        : null,

      ...groups.map((group) =>
        h(
          'section',
          null,
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

  function materialTab() {
    const ready = data.resources.filter((r) => r.status === 'ready').length;
    const canBuild = data.resources.length > 0;
    const built = data.space.status === 'ready' && data.lessons.length > 0;

    return h(
      'div',
      { class: 'stack stack-lg' },
      analysisPanel(data.job),

      canBuild
        ? h(
            'div',
            { class: 'card build-card' },
            h(
              'div',
              { class: 'row-between row-wrap', style: { gap: 'var(--space-4)' } },
              h(
                'div',
                { style: { minWidth: 0 } },
                h('h3', null, built ? 'Rebuild the plan' : 'Build your plan'),
                h(
                  'p',
                  { class: 'muted', style: { fontSize: 'var(--text-sm)', marginTop: 'var(--space-1)' } },
                  built
                    ? 'Adding or removing material changes what there is to teach. Rebuilding keeps everything you have already learned.'
                    : `${plural(data.resources.length, 'resource')} ready to read.`
                )
              ),
              h(
                'button',
                {
                  class: built ? 'btn btn-outline' : 'btn btn-primary btn-lg',
                  onClick: startAnalysis,
                  disabled: data.job && ['queued', 'running'].includes(data.job.status),
                },
                icon(built ? 'refresh' : 'sparkle', { size: 15 }),
                built ? 'Rebuild' : 'Build my plan'
              )
            )
          )
        : null,

      h(
        'section',
        null,
        sectionHead('Add material'),
        uploader(spaceId, () => refresh({ keepTab: 'material' }))
      ),

      data.resources.length
        ? h(
            'section',
            null,
            sectionHead(
              'Your material',
              h('span', { class: 'meta' }, `${ready} of ${data.resources.length} analysed`)
            ),
            h(
              'ul',
              { class: 'grid grid-2' },
              data.resources.map((resource) =>
                resourceCard(resource, {
                  onRole: async (role) => {
                    try {
                      await api.resources.setRole(spaceId, resource.id, role);
                      await refresh({ keepTab: 'material' });
                    } catch (error) {
                      notifyError(error);
                    }
                  },
                  onRemove: async () => {
                    const ok = await confirmDialog({
                      title: `Remove ${resource.title}?`,
                      body: 'Lessons already built from it stay until you rebuild the plan.',
                      confirmLabel: 'Remove',
                      danger: true,
                    });
                    if (!ok) return;
                    try {
                      await api.resources.remove(spaceId, resource.id);
                      await refresh({ keepTab: 'material' });
                    } catch (error) {
                      notifyError(error);
                    }
                  },
                })
              )
            )
          )
        : null
    );
  }

  function mapTab() {
    return knowledgeMap(spaceId, {
      onOpen: (lessonId) => lessonPlayer.open(lessonId, { autoplay: true }),
    });
  }

  function render(payload, activeTab) {
    const space = payload.space;
    const body = {
      plan: planTab,
      material: materialTab,
      map: mapTab,
    }[activeTab] || planTab;

    mountPoint.replaceChildren(
      h(
        'div',
        { class: 'stack stack-lg', style: { '--accent': accentVar(space.accent) } },
        h(
          'div',
          { class: 'page-head' },
          h(
            'div',
            { class: 'row', style: { marginBottom: 'var(--space-3)' } },
            h('a', { href: '/spaces', class: 'meta' }, 'Subjects'),
            h('span', { class: 'faint' }, '/'),
            h('span', { class: 'meta' }, space.level || 'Subject')
          ),
          h(
            'div',
            { class: 'row-between row-wrap', style: { gap: 'var(--space-4)' } },
            h(
              'div',
              { class: 'row', style: { minWidth: 0 } },
              resonanceMark(space.id, { size: 52, color: 'var(--accent)' }),
              h(
                'div',
                { style: { minWidth: 0 } },
                h('h1', null, space.title),
                h(
                  'p',
                  { class: 'meta' },
                  [
                    plural(space.conceptCount, 'concept'),
                    plural(space.lessonCount, 'lesson'),
                    space.resourceCount ? plural(space.resourceCount, 'resource') : 'no material yet',
                  ].filter(Boolean).join(' · ')
                )
              )
            ),
            h(
              'div',
              { class: 'row', style: { gap: 'var(--space-2)' } },
              h(
                'button',
                { class: 'btn btn-outline btn-sm', onClick: () => openAsk(space) },
                icon('chat', { size: 14 }),
                'Ask your teacher'
              ),
              h(
                'button',
                {
                  class: 'btn-icon',
                  'aria-label': 'Subject settings',
                  onClick: () => openSpaceSettings(space, refresh),
                },
                icon('settings', { size: 17 })
              )
            )
          ),
          space.description
            ? h('p', { class: 'lede', style: { marginTop: 'var(--space-4)' } }, space.description)
            : null
        ),

        h(
          'div',
          { class: 'tabs', role: 'tablist' },
          TABS.map((item) =>
            h(
              'button',
              {
                class: 'tab-button',
                role: 'tab',
                'aria-selected': String(item.id === activeTab),
                onClick: () => {
                  history.replaceState({}, '', `/space/${spaceId}?view=${item.id}`);
                  render(payload, item.id);
                },
              },
              icon(item.icon, { size: 15 }),
              item.label
            )
          )
        ),

        h('div', { role: 'tabpanel' }, body())
      )
    );
  }

  window.addEventListener('popstate', stopPolling, { once: true });
}

/* --- Dialogs ------------------------------------------------------------ */

function openAsk(space) {
  import('../core/feedback.js').then(({ openModal }) => {
    openModal((close) => {
      let field = null;
      let answerSlot = null;
      let button = null;

      async function ask(text) {
        const value = (text || field?.value || '').trim();
        if (!value) return;
        button.disabled = true;
        answerSlot.replaceChildren(
          h('div', { class: 'row' }, h('span', { class: 'spinner' }), h('span', { class: 'meta' }, 'Looking through your material'))
        );
        try {
          const result = await api.spaces.ask(space.id, { text: value });
          if (result.answer) {
            answerSlot.replaceChildren(
              h(
                'div',
                { class: 'ask-answer' },
                h('p', { class: 'prose' }, result.answer.text),
                result.answer.sourceNote
                  ? h('p', { class: 'grounding-source', style: { marginTop: 'var(--space-3)' } }, result.answer.sourceNote)
                  : null
              )
            );
          } else {
            answerSlot.replaceChildren(
              h('p', { class: 'meta' }, 'That one is a player command. Open a lesson and try it there.')
            );
          }
        } catch (error) {
          answerSlot.replaceChildren(
            h('div', { class: 'notice notice-trouble' }, error.message)
          );
        }
        button.disabled = false;
      }

      return h(
        'div',
        { class: 'stack' },
        h('h2', null, 'Ask your teacher'),
        h('p', { class: 'muted', style: { fontSize: 'var(--text-sm)' } },
          `Anything about ${space.title}. Answers come from your own material.`),
        h(
          'div',
          { class: 'row', style: { gap: 'var(--space-2)' } },
          h('input', {
            class: 'input', type: 'text', placeholder: 'What does terminal velocity actually mean?',
            'aria-label': 'Your question',
            ref: (el) => { field = el; },
            onKeydown: (event) => { if (event.key === 'Enter') { event.preventDefault(); ask(); } },
          }),
          h('button', { class: 'btn btn-primary', ref: (el) => { button = el; }, onClick: () => ask() }, 'Ask')
        ),
        h('div', { ref: (el) => { answerSlot = el; } }),
        h('div', { class: 'modal-actions' }, h('button', { class: 'btn btn-ghost', onClick: close }, 'Close'))
      );
    });
  });
}

function openSpaceSettings(space, refresh) {
  import('../core/feedback.js').then(({ openModal }) => {
    openModal((close) => {
      let title = null;
      let level = null;

      async function save(event) {
        event.preventDefault();
        try {
          await api.spaces.update(space.id, {
            title: title.value.trim(),
            level: level.value.trim(),
            description: space.description || '',
          });
          close();
          store.set({ dashboard: null });
          await refresh();
        } catch (error) {
          notifyError(error);
        }
      }

      async function remove() {
        close();
        const ok = await confirmDialog({
          title: `Delete ${space.title}?`,
          body: 'Its material, lessons and everything you have learned in it are removed. This cannot be undone.',
          confirmLabel: 'Delete subject',
          danger: true,
        });
        if (!ok) return;
        try {
          await api.spaces.remove(space.id);
          store.set({ dashboard: null });
          toast('Subject deleted.');
          navigate('/spaces');
        } catch (error) {
          notifyError(error);
        }
      }

      return h(
        'form',
        { class: 'stack', onSubmit: save },
        h('h2', null, 'Subject settings'),
        h(
          'label',
          { class: 'field' },
          h('span', { class: 'field-label' }, 'Name'),
          h('input', { class: 'input', type: 'text', value: space.title, maxlength: '80', ref: (el) => { title = el; } })
        ),
        h(
          'label',
          { class: 'field' },
          h('span', { class: 'field-label' }, 'Level'),
          h('input', { class: 'input', type: 'text', value: space.level, maxlength: '60', ref: (el) => { level = el; } })
        ),
        h('hr', { class: 'rule', style: { margin: 'var(--space-4) 0' } }),
        h(
          'button',
          { class: 'btn btn-danger btn-block', type: 'button', style: { justifyContent: 'flex-start' }, onClick: remove },
          icon('trash', { size: 15 }),
          'Delete this subject'
        ),
        h(
          'div',
          { class: 'modal-actions' },
          h('button', { class: 'btn btn-ghost', type: 'button', onClick: close }, 'Cancel'),
          h('button', { class: 'btn btn-primary', type: 'submit' }, 'Save')
        )
      );
    });
  });
}
