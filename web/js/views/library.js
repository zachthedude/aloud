/**
 * Library: saved places, downloaded audio, and every resource in one list.
 *
 * Download state is reported honestly. When the server has no speech provider
 * configured, this says so rather than showing a button that quietly does
 * nothing.
 */

import { h, icon } from '../core/dom.js';
import { api } from '../core/api.js';
import { store } from '../core/store.js';
import { lessonPlayer } from '../core/lessonplayer.js';
import { notifyError, toast, confirmDialog } from '../core/feedback.js';
import { resourceCard } from '../components/uploader.js';
import {
  bytesLabel, emptyState, errorState, formatTime, loadingPage,
  relativeTime, sectionHead,
} from '../components/pieces.js';

export async function libraryView(mountPoint, rerender) {
  mountPoint.replaceChildren(loadingPage('Loading your library'));

  let spaces;
  let bookmarks;
  try {
    [{ spaces }, { bookmarks }] = await Promise.all([api.spaces.list(), api.bookmarks.list()]);
  } catch (error) {
    mountPoint.replaceChildren(errorState(error.message, rerender));
    return;
  }

  const details = await Promise.all(spaces.map((s) => api.spaces.get(s.id).catch(() => null)));
  const providers = store.get().providers;
  const downloadsOn = providers?.downloadsAvailable !== false;

  const resources = [];
  for (const data of details) {
    if (!data) continue;
    for (const resource of data.resources) {
      resources.push({ ...resource, spaceTitle: data.space.title, spaceId: data.space.id });
    }
  }

  const lessons = [];
  for (const data of details) {
    if (!data) continue;
    for (const lesson of data.lessons) {
      lessons.push({ ...lesson, spaceTitle: data.space.title });
    }
  }

  function render() {
    mountPoint.replaceChildren(
      h(
        'div',
        { class: 'stack stack-lg' },
        h(
          'div',
          { class: 'page-head' },
          h('h1', null, 'Library'),
          h('p', { class: 'lede' }, 'Everything you have added, saved or kept for offline.')
        ),

        // Saved places
        h(
          'section',
          null,
          sectionHead('Saved places'),
          bookmarks.length
            ? h(
                'div',
                { class: 'rows' },
                bookmarks.map((bookmark) =>
                  h(
                    'div',
                    { class: 'row-item' },
                    h('span', { style: { color: 'var(--brass)' } }, icon('bookmark', { size: 17 })),
                    h(
                      'button',
                      {
                        class: 'row-item-body',
                        style: { textAlign: 'left', background: 'none' },
                        onClick: () => lessonPlayer.open(bookmark.lessonId),
                      },
                      h('span', { class: 'row-item-title truncate', style: { display: 'block' } },
                        bookmark.label || bookmark.lessonTitle),
                      h('span', { class: 'row-item-sub', style: { display: 'block' } },
                        `${bookmark.lessonTitle} · ${formatTime(bookmark.positionSeconds)} in · ${relativeTime(bookmark.createdAt)}`)
                    ),
                    h(
                      'button',
                      {
                        class: 'btn-icon',
                        'aria-label': 'Remove saved place',
                        onClick: async () => {
                          try {
                            await api.bookmarks.remove(bookmark.id);
                            bookmarks = bookmarks.filter((b) => b.id !== bookmark.id);
                            render();
                          } catch (error) {
                            notifyError(error);
                          }
                        },
                      },
                      icon('close', { size: 15 })
                    )
                  )
                )
              )
            : emptyState({
                mark: 'bookmark',
                title: 'Nothing saved yet',
                body: 'While listening, tap the bookmark to keep a place you want to come back to.',
              })
        ),

        // Offline
        h(
          'section',
          null,
          sectionHead('Take lessons with you'),
          downloadsOn
            ? h(
                'div',
                { class: 'stack' },
                h(
                  'p',
                  { class: 'muted', style: { fontSize: 'var(--text-sm)', maxWidth: '56ch' } },
                  'Lessons play instantly using your device\'s own voice, with no download needed. ' +
                    'Saving a lesson makes an audio file you can keep and play anywhere.'
                ),
                lessons.length
                  ? h('div', { class: 'rows' }, lessons.slice(0, 12).map(downloadRow))
                  : h('p', { class: 'meta' }, 'No lessons yet.')
              )
            : h(
                'div',
                { class: 'notice' },
                icon('info', { size: 16 }),
                h(
                  'div',
                  null,
                  h('p', { class: 'strong', style: { marginBottom: 'var(--space-1)' } }, 'Audio files are switched off'),
                  h('p', { style: { margin: 0 } },
                    'This server has no speech provider configured, so lessons cannot be saved as files. ' +
                    'Listening in the app works normally.')
                )
              )
        ),

        // Material
        h(
          'section',
          null,
          sectionHead('All material', h('span', { class: 'meta' },
            `${resources.length} ${resources.length === 1 ? 'item' : 'items'}`)),
          resources.length
            ? h(
                'ul',
                { class: 'grid grid-2' },
                resources.map((resource) =>
                  h(
                    'div',
                    null,
                    resourceCard(resource, {
                      onRemove: async () => {
                        const ok = await confirmDialog({
                          title: `Remove ${resource.title}?`,
                          body: `From ${resource.spaceTitle}. Lessons built from it stay until you rebuild that plan.`,
                          confirmLabel: 'Remove',
                          danger: true,
                        });
                        if (!ok) return;
                        try {
                          await api.resources.remove(resource.spaceId, resource.id);
                          rerender();
                        } catch (error) {
                          notifyError(error);
                        }
                      },
                    }),
                    h('p', { class: 'meta', style: { padding: '0 var(--space-1)' } }, resource.spaceTitle)
                  )
                )
              )
            : emptyState({
                mark: 'library',
                title: 'No material yet',
                body: 'Add PDFs, slides or notes to a subject and they appear here.',
                action: h('a', { href: '/spaces', class: 'btn btn-primary' }, 'Go to subjects'),
              })
        )
      )
    );
  }

  function downloadRow(lesson) {
    const statusSlot = h('span', { class: 'meta' }, '');
    const actionSlot = h('span');

    // Ask what state this lesson's audio is actually in, rather than guessing.
    api.audio
      .status(lesson.id)
      .then((status) => {
        statusSlot.textContent = status.message || '';
        actionSlot.replaceChildren(
          status.state === 'ready'
            ? h(
                'a',
                {
                  class: 'btn btn-sm btn-outline',
                  href: api.audio.downloadUrl(lesson.id, status.voice || ''),
                  download: '',
                  'data-native': '',
                },
                icon('download', { size: 14 }),
                bytesLabel(status.byteSize) || 'Download'
              )
            : status.state === 'unavailable'
              ? h('span', { class: 'chip' }, 'Unavailable')
              : h(
                  'button',
                  {
                    class: 'btn btn-sm',
                    disabled: status.state === 'preparing',
                    onClick: async (event) => {
                      const button = event.currentTarget;
                      button.disabled = true;
                      button.textContent = 'Preparing...';
                      try {
                        await api.audio.prepare(lesson.id);
                        toast('Preparing audio. It will appear here when ready.');
                        watch(lesson.id, statusSlot, actionSlot);
                      } catch (error) {
                        button.disabled = false;
                        button.textContent = 'Save';
                        notifyError(error);
                      }
                    },
                  },
                  status.state === 'preparing' ? 'Preparing...' : 'Save'
                )
        );
        if (status.state === 'preparing') watch(lesson.id, statusSlot, actionSlot);
      })
      .catch(() => {
        statusSlot.textContent = '';
      });

    return h(
      'div',
      { class: 'row-item' },
      h(
        'button',
        {
          class: 'row-item-body',
          style: { textAlign: 'left', background: 'none' },
          onClick: () => lessonPlayer.open(lesson.id),
        },
        h('span', { class: 'row-item-title truncate', style: { display: 'block' } }, lesson.title),
        h('span', { class: 'row-item-sub truncate', style: { display: 'block' } }, lesson.spaceTitle)
      ),
      statusSlot,
      actionSlot
    );
  }

  function watch(lessonId, statusSlot, actionSlot) {
    let tries = 0;
    const timer = setInterval(async () => {
      tries += 1;
      try {
        const status = await api.audio.status(lessonId);
        statusSlot.textContent = status.message || '';
        if (status.state === 'ready') {
          clearInterval(timer);
          actionSlot.replaceChildren(
            h(
              'a',
              {
                class: 'btn btn-sm btn-outline',
                href: api.audio.downloadUrl(lessonId, status.voice || ''),
                download: '',
                'data-native': '',
              },
              icon('download', { size: 14 }),
              bytesLabel(status.byteSize) || 'Download'
            )
          );
        } else if (status.state === 'failed' || tries > 80) {
          clearInterval(timer);
        }
      } catch {
        clearInterval(timer);
      }
    }, 2500);
  }

  render();
}
