/**
 * Keeping a lesson alive with the screen off.
 *
 * The product's promise is that you put the phone in your pocket and keep
 * learning. Three different things on a phone will break that, and each needs
 * its own answer:
 *
 *   The screen sleeps      Wake Lock holds it awake while a lesson is
 *                          actually playing. Released the moment it pauses,
 *                          because holding a screen on costs battery and
 *                          nobody asked for a torch.
 *   The lock screen has    Media Session puts the lesson on the lock screen
 *   no controls            and on headphone buttons, so play, pause and skip
 *                          work without looking.
 *   The tab gets frozen    A silent looping track marks the page as playing
 *                          audio, which is what stops a backgrounded tab
 *                          being throttled into silence. speechSynthesis on
 *                          its own does not always count.
 *
 * Every one of these is best-effort. None of them exists on every browser,
 * and Wake Lock in particular is refused outright in some contexts. Nothing
 * here throws: a lesson that keeps playing with the screen on is a worse
 * experience, not a broken one.
 */

/** A fraction of a second of silence, looped. Small enough to inline. */
const SILENCE =
  'data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAgD4AAAB9AAACABAAZGF0YQAAAAA=';

export class KeepAlive {
  constructor() {
    this.supported = {
      wakeLock: typeof navigator !== 'undefined' && 'wakeLock' in navigator,
      mediaSession: typeof navigator !== 'undefined' && 'mediaSession' in navigator,
    };
    this._lock = null;
    this._silence = null;
    this._wanted = false;

    // A wake lock is dropped whenever the tab is hidden, and is not given
    // back automatically. Re-take it when the student comes back.
    if (typeof document !== 'undefined') {
      document.addEventListener('visibilitychange', () => {
        if (this._wanted && document.visibilityState === 'visible') this._takeLock();
      });
    }
  }

  /** A lesson is playing. Hold everything it needs. */
  async start() {
    this._wanted = true;
    this._playSilence();
    await this._takeLock();
  }

  /** A lesson has paused or finished. Give it all back. */
  async stop() {
    this._wanted = false;
    this._stopSilence();
    await this._releaseLock();
  }

  async _takeLock() {
    if (!this.supported.wakeLock || this._lock) return;
    try {
      this._lock = await navigator.wakeLock.request('screen');
      this._lock.addEventListener?.('release', () => {
        this._lock = null;
      });
    } catch (error) {
      // Refused: not a secure context, battery saver, or the browser simply
      // says no. The lesson carries on regardless.
      this._lock = null;
    }
  }

  async _releaseLock() {
    try {
      await this._lock?.release();
    } catch (error) {
      /* already gone */
    }
    this._lock = null;
  }

  _playSilence() {
    if (this._silence) return;
    try {
      const audio = new Audio(SILENCE);
      audio.loop = true;
      audio.volume = 0;
      // Autoplay is allowed here because a lesson only ever starts from a
      // press, which is the user gesture the policy is asking for.
      const played = audio.play();
      played?.catch?.(() => {});
      this._silence = audio;
    } catch (error) {
      this._silence = null;
    }
  }

  _stopSilence() {
    try {
      this._silence?.pause();
    } catch (error) {
      /* nothing to stop */
    }
    this._silence = null;
  }

  /**
   * Put the lesson on the lock screen.
   *
   * `handlers` maps a Media Session action to a function: play, pause,
   * previoustrack, nexttrack, seekbackward, seekforward. Unsupported actions
   * are skipped rather than throwing, which is how this API reports what a
   * given browser and headset can actually do.
   */
  describe({ title, artist, album }, handlers = {}) {
    if (!this.supported.mediaSession) return;
    try {
      const MetadataClass = window.MediaMetadata;
      if (MetadataClass) {
        navigator.mediaSession.metadata = new MetadataClass({ title, artist, album });
      }
      for (const [action, handler] of Object.entries(handlers)) {
        try {
          navigator.mediaSession.setActionHandler(action, handler);
        } catch (error) {
          /* this browser does not offer that button */
        }
      }
    } catch (error) {
      /* metadata is a nicety */
    }
  }

  /** Tell the lock screen whether it is playing, so the button is right. */
  setPlaying(playing) {
    if (!this.supported.mediaSession) return;
    try {
      navigator.mediaSession.playbackState = playing ? 'playing' : 'paused';
    } catch (error) {
      /* older browsers have no playbackState */
    }
  }

  get holding() {
    return Boolean(this._lock);
  }
}

export const keepAlive = new KeepAlive();
