/**
 * Adding material: files, a link, or notes typed in.
 *
 * Upload progress is real, measured from the request, because a student adding
 * a 20MB textbook over school wifi needs to see movement rather than a frozen
 * button. Rejections come from the server with wording meant for them, and are
 * shown against the specific file that failed rather than as a general error.
 */

import { h, icon } from '../core/dom.js';
import { api } from '../core/api.js';
import { toast } from '../core/feedback.js';
import { bytesLabel } from './pieces.js';

const DOC_ACCEPT = '.pdf,.docx,.pptx,.txt,.md,.markdown,.rst,.html,.htm';
const MEDIA_ACCEPT = '.mp4,.mov,.webm,.mkv,.avi,.m4v,.mp3,.m4a,.wav,.ogg,.oga,.opus,.flac,.aac';
const ACCEPT = `${DOC_ACCEPT},${MEDIA_ACCEPT}`;

const MEDIA_EXTENSIONS = new Set(MEDIA_ACCEPT.split(','));
const MAX_DOC_MB = 25;
const MAX_MEDIA_MB = 300;

function extensionOf(name) {
  const index = String(name).lastIndexOf('.');
  return index === -1 ? '' : name.slice(index).toLowerCase();
}

const isMedia = (name) => MEDIA_EXTENSIONS.has(extensionOf(name));

/**
 * @param {string} spaceId
 * @param {(resource: object) => void} onAdded
 */
export function uploader(spaceId, onAdded) {
  let dropzone = null;
  let input = null;
  let queueList = null;
  const queue = new Map();

  function renderQueue() {
    if (!queueList) return;
    queueList.replaceChildren();
    if (!queue.size) return;

    for (const [id, item] of queue) {
      queueList.appendChild(
        h(
          'li',
          { class: 'upload-item' },
          h('span', { class: 'upload-icon' },
            icon(item.error ? 'warn' : item.media ? 'video' : 'file', { size: 16 })),
          h(
            'span',
            { class: 'upload-body' },
            h('span', { class: 'upload-name truncate' }, item.name),
            item.error
              ? h('span', { class: 'upload-error' }, item.error)
              : h(
                  'span',
                  { class: 'track', style: { marginTop: '6px' } },
                  h('i', { style: { width: `${Math.round(item.progress * 100)}%` } })
                )
          ),
          item.error
            ? h(
                'button',
                {
                  class: 'btn-icon',
                  'aria-label': `Dismiss ${item.name}`,
                  onClick: () => { queue.delete(id); renderQueue(); },
                },
                icon('close', { size: 15 })
              )
            : h('span', { class: 'meta tabular' }, `${Math.round(item.progress * 100)}%`)
        )
      );
    }
  }

  async function send(file) {
    const id = `${file.name}-${file.size}-${Date.now()}`;

    const media = isMedia(file.name);
    const capMb = media ? MAX_MEDIA_MB : MAX_DOC_MB;
    if (file.size > capMb * 1024 * 1024) {
      queue.set(id, {
        name: file.name,
        progress: 0,
        media,
        error: `Larger than ${capMb} MB. ${media ? 'Try a shorter recording.' : 'Try splitting it up.'}`,
      });
      renderQueue();
      return;
    }

    queue.set(id, { name: file.name, progress: 0, media, error: '' });
    renderQueue();

    try {
      const result = await api.resources.upload(spaceId, file, '', (progress) => {
        const item = queue.get(id);
        if (item) {
          item.progress = progress;
          renderQueue();
        }
      });
      queue.delete(id);
      renderQueue();
      onAdded?.(result.resource);
    } catch (error) {
      const item = queue.get(id);
      if (item) {
        item.error = error.message;
        item.progress = 0;
        renderQueue();
      }
    }
  }

  function handleFiles(files) {
    Array.from(files || []).forEach(send);
  }

  function openLinkDialog() {
    import('../core/feedback.js').then(({ openModal }) => {
      openModal((close) => {
        let field = null;
        let busy = null;

        async function submit(event) {
          event?.preventDefault();
          const url = (field?.value || '').trim();
          if (!url) return;
          busy.disabled = true;
          busy.textContent = 'Reading the page...';
          try {
            const result = await api.resources.addUrl(spaceId, url);
            close();
            onAdded?.(result.resource);
          } catch (error) {
            busy.disabled = false;
            busy.textContent = 'Add page';
            toast(error.message, { tone: 'trouble', duration: 7000 });
          }
        }

        return h(
          'form',
          { class: 'stack', onSubmit: submit },
          h('h2', null, 'Add a web page'),
          h('p', { class: 'muted', style: { fontSize: 'var(--text-sm)' } },
            'The readable text on the page is imported. Pages that build themselves with scripts may not work.'),
          h('input', {
            class: 'input', type: 'url', placeholder: 'https://...',
            'aria-label': 'Page address', required: true,
            ref: (el) => { field = el; },
          }),
          h(
            'div',
            { class: 'modal-actions' },
            h('button', { class: 'btn btn-ghost', type: 'button', onClick: close }, 'Cancel'),
            h('button', { class: 'btn btn-primary', type: 'submit', ref: (el) => { busy = el; } }, 'Add page')
          )
        );
      });
    });
  }

  function openNoteDialog() {
    import('../core/feedback.js').then(({ openModal }) => {
      openModal((close) => {
        let title = null;
        let body = null;
        let busy = null;

        async function submit(event) {
          event?.preventDefault();
          const text = (body?.value || '').trim();
          if (text.length < 40) {
            toast('Add a little more text. There needs to be enough to teach from.', { tone: 'trouble' });
            return;
          }
          busy.disabled = true;
          busy.textContent = 'Adding...';
          try {
            const result = await api.resources.addNote(spaceId, {
              title: (title?.value || '').trim(),
              text,
            });
            close();
            onAdded?.(result.resource);
          } catch (error) {
            busy.disabled = false;
            busy.textContent = 'Add notes';
            toast(error.message, { tone: 'trouble' });
          }
        }

        return h(
          'form',
          { class: 'stack', onSubmit: submit },
          h('h2', null, 'Type or paste your notes'),
          h('input', {
            class: 'input', type: 'text', placeholder: 'What are these notes about?',
            'aria-label': 'Title', maxlength: '120',
            ref: (el) => { title = el; },
          }),
          h('textarea', {
            class: 'textarea', rows: '10',
            placeholder: 'Paste your notes here. Headings help: put each topic on its own line.',
            'aria-label': 'Your notes', required: true,
            ref: (el) => { body = el; },
          }),
          h(
            'div',
            { class: 'modal-actions' },
            h('button', { class: 'btn btn-ghost', type: 'button', onClick: close }, 'Cancel'),
            h('button', { class: 'btn btn-primary', type: 'submit', ref: (el) => { busy = el; } }, 'Add notes')
          )
        );
      });
    });
  }

  const zone = h(
    'div',
    {
      class: 'dropzone',
      ref: (el) => { dropzone = el; },
      onDragover: (event) => {
        event.preventDefault();
        dropzone.dataset.over = 'true';
      },
      onDragleave: () => { delete dropzone.dataset.over; },
      onDrop: (event) => {
        event.preventDefault();
        delete dropzone.dataset.over;
        handleFiles(event.dataTransfer?.files);
      },
    },
    h('input', {
      type: 'file',
      multiple: true,
      accept: ACCEPT,
      class: 'visually-hidden',
      id: `upload-${spaceId}`,
      ref: (el) => { input = el; },
      onChange: (event) => {
        handleFiles(event.target.files);
        event.target.value = '';
      },
    }),
    h('div', { class: 'dropzone-mark' }, icon('upload', { size: 26, stroke: 1.4 })),
    h('p', { class: 'dropzone-title' }, 'Drop your material here'),
    h(
      'p',
      { class: 'dropzone-sub' },
      `Documents and slides up to ${MAX_DOC_MB} MB. Lecture recordings up to ${MAX_MEDIA_MB} MB, `,
      'transcribed before they are taught.'
    ),
    h(
      'div',
      { class: 'row row-wrap', style: { justifyContent: 'center', gap: 'var(--space-2)' } },
      h(
        'button',
        { class: 'btn btn-primary', type: 'button', onClick: () => input.click() },
        icon('file', { size: 15 }),
        'Choose files'
      ),
      h(
        'button',
        { class: 'btn btn-outline', type: 'button', onClick: openLinkDialog },
        icon('link', { size: 15 }),
        'Add a link'
      ),
      h(
        'button',
        { class: 'btn btn-outline', type: 'button', onClick: openNoteDialog },
        icon('note', { size: 15 }),
        'Type notes'
      )
    ),
    h('ul', { class: 'upload-queue', ref: (el) => { queueList = el; } })
  );

  return zone;
}

const SOURCE_ICON = {
  url: 'link',
  note: 'note',
  video: 'video',
  audio: 'audio',
};

/** One resource as a card, with its processing state and a way to remove it. */
export function resourceCard(resource, { onRemove } = {}) {
  const details = [
    resource.pageCount ? `${resource.pageCount} pages` : '',
    resource.sectionCount ? `${resource.sectionCount} passages` : '',
    resource.wordCount ? `${resource.wordCount.toLocaleString()} words` : '',
    bytesLabel(resource.byteSize),
  ].filter(Boolean);

  const statusClass = {
    ready: 'chip chip-mint',
    failed: 'chip chip-trouble',
    queued: 'chip',
  }[resource.status] || 'chip chip-quiet';

  const statusLabel = {
    queued: 'Waiting',
    extracting: 'Reading',
    structuring: 'Structuring',
    modelling: 'Finding concepts',
    ready: 'Analysed',
    failed: 'Could not be read',
  }[resource.status] || resource.status;

  return h(
    'li',
    { class: 'source-card' },
    h(
      'div',
      { class: 'row-between', style: { alignItems: 'flex-start' } },
      h(
        'div',
        { class: 'row', style: { minWidth: 0, alignItems: 'flex-start' } },
        h('span', { class: 'source-icon' }, icon(SOURCE_ICON[resource.kind] || 'file', { size: 16 })),
        h(
          'div',
          { style: { minWidth: 0 } },
          h('p', { class: 'source-title truncate' }, resource.title || resource.filename),
          h('p', { class: 'meta truncate' }, details.join(' · ') || resource.kind.toUpperCase())
        )
      ),
      onRemove &&
        h(
          'button',
          { class: 'btn-icon', 'aria-label': `Remove ${resource.title}`, onClick: onRemove },
          icon('trash', { size: 15 })
        )
    ),
    h(
      'div',
      { class: 'row', style: { marginTop: 'var(--space-3)', gap: 'var(--space-2)' } },
      h('span', { class: statusClass }, statusLabel),
      resource.sourceUrl
        ? h(
            'a',
            {
              href: resource.sourceUrl, target: '_blank', rel: 'noopener noreferrer',
              class: 'meta truncate', 'data-native': '',
            },
            new URL(resource.sourceUrl).hostname
          )
        : null
    ),
    resource.error
      ? h('p', { class: 'source-error' }, resource.error)
      : null
  );
}
