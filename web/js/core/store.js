/**
 * A minimal reactive store.
 *
 * Views subscribe to the slices they care about and re-render when those
 * change. Keeping player state here rather than inside the player view is what
 * lets the mini player keep working after you navigate away from a lesson:
 * the narration is owned by the app, not by a screen.
 */

function shallowEqual(a, b) {
  if (Object.is(a, b)) return true;
  if (typeof a !== 'object' || typeof b !== 'object' || a === null || b === null) return false;
  const keysA = Object.keys(a);
  const keysB = Object.keys(b);
  if (keysA.length !== keysB.length) return false;
  return keysA.every((key) => Object.is(a[key], b[key]));
}

export function createStore(initial = {}) {
  let state = { ...initial };
  const subscribers = new Set();

  function get() {
    return state;
  }

  function set(patch) {
    const next = typeof patch === 'function' ? patch(state) : patch;
    if (!next) return state;
    const merged = { ...state, ...next };
    if (shallowEqual(state, merged)) return state;
    const previous = state;
    state = merged;
    for (const subscriber of Array.from(subscribers)) {
      subscriber(state, previous);
    }
    return state;
  }

  /**
   * @param {(state: object, previous: object) => void} listener
   * @param {(state: object) => any} [selector] only fire when this slice changes
   */
  function subscribe(listener, selector) {
    if (!selector) {
      subscribers.add(listener);
      return () => subscribers.delete(listener);
    }
    let current = selector(state);
    const wrapped = (next, previous) => {
      const value = selector(next);
      if (shallowEqual(current, value)) return;
      current = value;
      listener(next, previous);
    };
    subscribers.add(wrapped);
    return () => subscribers.delete(wrapped);
  }

  return { get, set, subscribe };
}

/** The one store this app uses. */
export const store = createStore({
  // Session
  user: null,
  providers: null,
  ready: false,

  // Data caches, keyed by id
  spaces: [],
  spaceDetail: null,
  dashboard: null,

  // Playback, owned by the app so the mini player survives navigation
  player: {
    open: false,
    lessonId: '',
    spaceId: '',
    sessionId: '',
    lesson: null,
    sections: [],
    concept: null,
    neighbours: null,
    audioStatus: null,

    state: 'idle',        // mirrors the server's LessonState
    speaking: false,      // narration is actually producing sound right now
    sectionIndex: 0,
    elapsed: 0,           // seconds into the current section
    speed: 1,
    voice: '',          // the speech-engine voice currently reading
    muted: false,
    showTranscript: false,

    question: null,       // the question currently on the floor
    reply: null,          // the teacher's last response
    listening: false,
    partial: '',
    level: 0,
    finished: null,       // the end-of-lesson assessment
    error: '',
  },

  toasts: [],
});

/** Patch the player slice without clobbering the rest of it. */
export function setPlayer(patch) {
  store.set((state) => ({
    player: { ...state.player, ...(typeof patch === 'function' ? patch(state.player) : patch) },
  }));
}

export const player = () => store.get().player;
