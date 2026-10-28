/**
 * The knowledge map.
 *
 * Laid out in layers by prerequisite depth, so reading down the page is
 * reading the order things have to be learned in. A force-directed blob would
 * look busier and say less; the one fact worth showing here is what rests on
 * what, and a layered graph shows exactly that.
 *
 * Colour is the same mastery scale used everywhere else, so a glance says
 * which parts of the subject are solid and which are still open.
 */

import { h, icon } from '../core/dom.js';
import { api } from '../core/api.js';
import { emptyState, errorState, MASTERY_LEVEL } from './pieces.js';

const MASTERY_COLOR = {
  not_started: 'var(--mastery-0)',
  needs_review: 'var(--mastery-1)',
  developing: 'var(--mastery-2)',
  understood: 'var(--mastery-3)',
  mastered: 'var(--mastery-4)',
};

const MASTERY_WORD = {
  not_started: 'Not started',
  needs_review: 'Worth revisiting',
  developing: 'Building this',
  understood: 'Understood',
  mastered: 'Solid',
};

const NODE_WIDTH = 168;
const NODE_HEIGHT = 54;
const GAP_X = 26;
const GAP_Y = 74;
/** Nodes per row before a wide layer wraps. */
const MAX_PER_ROW = 4;

export function knowledgeMap(spaceId, { onOpen } = {}) {
  const container = h('div', { class: 'map-wrap' }, h('div', { class: 'skeleton', style: { height: '18rem' } }));

  api.spaces
    .map(spaceId)
    .then((graph) => {
      container.replaceChildren(render(graph, onOpen));
    })
    .catch((error) => {
      container.replaceChildren(errorState(error.message));
    });

  return container;
}

function render(graph, onOpen) {
  const nodes = graph.nodes || [];
  if (!nodes.length) {
    return emptyState({
      mark: 'layers',
      title: 'No map yet',
      body: 'Once your material has been read, the concepts in it appear here with what depends on what.',
    });
  }

  // Group into layers by prerequisite depth.
  const layers = new Map();
  for (const node of nodes) {
    const depth = node.depth || 0;
    if (!layers.has(depth)) layers.set(depth, []);
    layers.get(depth).push(node);
  }
  const depths = [...layers.keys()].sort((a, b) => a - b);

  // A layer wider than this wraps onto further rows. Several concepts all
  // resting on one foundation is the common shape, and a single row of eight
  // runs off the side of the screen where nobody finds the last three.
  const busiest = Math.max(...depths.map((d) => layers.get(d).length));
  const perRow = busiest > MAX_PER_ROW ? Math.ceil(busiest / Math.ceil(busiest / MAX_PER_ROW)) : busiest;

  // Lay each depth out as one or more rows, keeping depth order down the page.
  const rows = [];
  for (const depth of depths) {
    const items = layers.get(depth);
    for (let start = 0; start < items.length; start += perRow) {
      rows.push(items.slice(start, start + perRow));
    }
  }

  const widest = Math.max(...rows.map((row) => row.length));
  const width = Math.max(widest * (NODE_WIDTH + GAP_X) - GAP_X, NODE_WIDTH);
  const height = rows.length * (NODE_HEIGHT + GAP_Y) - GAP_Y;

  // Position every node.
  const position = new Map();
  rows.forEach((items, row) => {
    const rowWidth = items.length * (NODE_WIDTH + GAP_X) - GAP_X;
    const offset = (width - rowWidth) / 2;
    items.forEach((node, column) => {
      position.set(node.id, {
        x: offset + column * (NODE_WIDTH + GAP_X),
        y: row * (NODE_HEIGHT + GAP_Y),
        node,
      });
    });
  });

  // Edges first, so they sit behind the nodes.
  const edges = (graph.edges || [])
    .map(({ from, to }) => {
      const a = position.get(from);
      const b = position.get(to);
      if (!a || !b) return null;
      const x1 = a.x + NODE_WIDTH / 2;
      const y1 = a.y + NODE_HEIGHT;
      const x2 = b.x + NODE_WIDTH / 2;
      const y2 = b.y;
      const mid = (y1 + y2) / 2;
      return h('path', {
        d: `M${x1} ${y1} C ${x1} ${mid}, ${x2} ${mid}, ${x2} ${y2}`,
        fill: 'none',
        stroke: 'var(--chalk-line-strong)',
        'stroke-width': 1.2,
      });
    })
    .filter(Boolean);

  const figure = h(
    'div',
    { class: 'map-scroll' },
    h(
      'svg',
      {
        class: 'map-svg',
        viewBox: `-8 -8 ${width + 16} ${height + 16}`,
        width: width + 16,
        height: height + 16,
        role: 'img',
        'aria-label': 'Concept map showing which ideas depend on which',
      },
      h('g', null, edges),
      h(
        'g',
        null,
        [...position.values()].map(({ x, y, node }) => {
          const colour = MASTERY_COLOR[node.mastery] || MASTERY_COLOR.not_started;
          const level = MASTERY_LEVEL[node.mastery] ?? 0;
          return h(
            'g',
            {
              class: 'map-node',
              transform: `translate(${x} ${y})`,
              role: node.lessonId ? 'button' : undefined,
              tabindex: node.lessonId ? '0' : undefined,
              'aria-label': `${node.name}. ${MASTERY_WORD[node.mastery]}.`,
              onClick: () => node.lessonId && onOpen?.(node.lessonId),
              onKeydown: (event) => {
                if ((event.key === 'Enter' || event.key === ' ') && node.lessonId) {
                  event.preventDefault();
                  onOpen(node.lessonId);
                }
              },
            },
            h('rect', {
              width: NODE_WIDTH, height: NODE_HEIGHT, rx: 10,
              fill: 'var(--chalk-raised)',
              stroke: level > 0 ? colour : 'var(--chalk-line)',
              'stroke-width': level >= 3 ? 1.6 : 1,
            }),
            h('rect', { x: 0, y: 0, width: 3, height: NODE_HEIGHT, rx: 1.5, fill: colour }),
            h(
              'text',
              {
                x: 14, y: 23,
                fill: 'var(--ink)',
                'font-size': '12.5',
                'font-weight': '600',
                'font-family': 'var(--font-sans)',
              },
              truncate(node.name, 21)
            ),
            h(
              'text',
              {
                x: 14, y: 40,
                fill: 'var(--ink-faint)',
                'font-size': '10.5',
                'font-family': 'var(--font-sans)',
              },
              MASTERY_WORD[node.mastery] || ''
            )
          );
        })
      )
    )
  );

  return h(
    'div',
    { class: 'stack' },
    h(
      'div',
      { class: 'row-between row-wrap', style: { gap: 'var(--space-3)' } },
      h(
        'p',
        { class: 'muted', style: { fontSize: 'var(--text-sm)', maxWidth: '46ch' } },
        'Ideas lower down rest on the ones above them. Pick any to listen to it.'
      ),
      h(
        'div',
        { class: 'map-legend' },
        ['mastered', 'understood', 'developing', 'needs_review', 'not_started'].map((state) =>
          h(
            'span',
            { class: 'map-legend-item' },
            h('i', { style: { background: MASTERY_COLOR[state] } }),
            MASTERY_WORD[state]
          )
        )
      )
    ),
    figure
  );
}

function truncate(text, max) {
  return text.length <= max ? text : `${text.slice(0, max - 1)}…`;
}
