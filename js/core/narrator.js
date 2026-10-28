/**
 * The teacher's voice.
 *
 * Live narration uses the browser's own speech synthesis rather than audio
 * files from the server. That is a deliberate product decision:
 *
 *   - it starts instantly, with no generation wait before a lesson can begin
 *   - it works with no network, so a lesson keeps going on a train
 *   - changing speed re-synthesises rather than resampling, so the teacher
 *     still sounds like a person at 1.75x instead of a chipmunk
 *   - it costs nothing per lesson, so nobody is metered for listening
 *
 * Server-rendered audio files still exist, for downloading and offline
 * keeping. That is `audio_service.py`, not this.
 *
 * Two browser realities are handled here. Long utterances are cut off in
 * Chrome after roughly fifteen seconds, so text is spoken sentence by
 * sentence. And voices load asynchronously, so the list is resolved lazily.
 */

const WORDS_PER_MINUTE = 150;

/** Voices worth preferring, in order. Natural-sounding and widely installed. */
const PREFERRED = [
  'Google UK English Female', 'Google UK English Male', 'Google US English',
  'Microsoft Libby Online', 'Microsoft Sonia Online', 'Microsoft Aria Online',
  'Microsoft Ryan Online', 'Microsoft Hazel', 'Microsoft Zira',
  'Samantha', 'Daniel', 'Karen',
];

function splitSentences(text) {
  const cleaned = String(text || '').replace(/\s+/g, ' ').trim();
  if (!cleaned) return [];
  const parts = cleaned.match(/[^.!?]+[.!?]+["')\]]*|[^.!?]+$/g) || [cleaned];

  // Recombine very short fragments so the voice does not clip between them.
  const chunks = [];
  for (const part of parts) {
    const piece = part.trim();
    if (!piece) continue;
    const last = chunks[chunks.length - 1];
    if (last && (last.length < 40 || piece.length < 25) && last.length + piece.length < 220) {
      chunks[chunks.length - 1] = `${last} ${piece}`;
    } else {
      chunks.push(piece);
    }
  }
  return chunks;
}

export function estimateSeconds(text, rate = 1) {
  const words = String(text || '').trim().split(/\s+/).filter(Boolean).length;
  return Math.max(1, (words / (WORDS_PER_MINUTE * rate)) * 60);
}

export class Narrator {
  constructor() {
    this.supported =
      typeof window !== 'undefined' &&
      'speechSynthesis' in window &&
      'SpeechSynthesisUtterance' in window;

    this.rate = 1;
    this.volume = 1;
    this.voiceName = '';

    this._chunks = [];
    this._chunkIndex = 0;
    this._utterance = null;
    this._speaking = false;
    this._paused = false;
    this._stopped = true;

    this._startedAt = 0;
    this._accumulated = 0;   // seconds of audio completed before the current run
    this._total = 0;
    this._ticker = null;

    this.onProgress = null;  // (elapsedSeconds, totalSeconds) => void
    this.onEnd = null;       // () => void
    this.onError = null;     // (message) => void

    this._voices = [];
    if (this.supported) {
      this._loadVoices();
      window.speechSynthesis.addEventListener?.('voiceschanged', () => this._loadVoices());
    }
  }

  _loadVoices() {
    try {
      this._voices = window.speechSynthesis.getVoices() || [];
    } catch {
      this._voices = [];
    }
  }

  /** English voices, best first. */
  voices() {
    if (!this._voices.length) this._loadVoices();
    const english = this._voices.filter((v) => /^en(-|_|$)/i.test(v.lang || ''));
    const pool = english.length ? english : this._voices;
    return [...pool].sort((a, b) => {
      const ai = PREFERRED.findIndex((name) => a.name.includes(name));
      const bi = PREFERRED.findIndex((name) => b.name.includes(name));
      return (ai === -1 ? 99 : ai) - (bi === -1 ? 99 : bi);
    });
  }

  _voice() {
    const available = this.voices();
    if (!available.length) return null;
    if (this.voiceName) {
      const chosen = available.find((v) => v.name === this.voiceName);
      if (chosen) return chosen;
    }
    return available[0];
  }

  get speaking() {
    return this._speaking && !this._paused;
  }
  get paused() {
    return this._paused;
  }

  /** Seconds of narration delivered so far in the current text. */
  get elapsed() {
    if (!this._speaking || this._paused) return this._accumulated;
    return this._accumulated + (performance.now() - this._startedAt) / 1000;
  }

  get total() {
    return this._total;
  }

  /**
   * Speak a passage from the beginning, or from `offsetSeconds` in.
   * Resuming mid-passage starts at the nearest sentence boundary, which is
   * what a person would do when picking a lesson back up.
   */
  speak(text, { offsetSeconds = 0 } = {}) {
    this.stop();
    const chunks = splitSentences(text);
    if (!chunks.length) {
      this.onEnd?.();
      return;
    }

    this._chunks = chunks;
    this._total = estimateSeconds(text, this.rate);
    this._stopped = false;

    let index = 0;
    let consumed = 0;
    if (offsetSeconds > 0.5) {
      for (let i = 0; i < chunks.length; i += 1) {
        const length = estimateSeconds(chunks[i], this.rate);
        if (consumed + length > offsetSeconds) break;
        consumed += length;
        index = i + 1;
      }
      if (index >= chunks.length) {
        index = Math.max(0, chunks.length - 1);
        consumed = this._durationBefore(index);
      }
    }

    this._chunkIndex = index;
    this._accumulated = consumed;

    if (!this.supported) {
      // No speech engine: run the clock so the lesson still advances and the
      // transcript still follows. The student reads instead of listening.
      this._runSilently();
      return;
    }
    this._speakCurrent();
  }

  _durationBefore(index) {
    let total = 0;
    for (let i = 0; i < index; i += 1) total += estimateSeconds(this._chunks[i], this.rate);
    return total;
  }

  _speakCurrent() {
    if (this._stopped || this._chunkIndex >= this._chunks.length) return;

    const utterance = new SpeechSynthesisUtterance(this._chunks[this._chunkIndex]);
    utterance.rate = Math.max(0.5, Math.min(3, this.rate));
    utterance.volume = this.volume;
    utterance.pitch = 1;
    const voice = this._voice();
    if (voice) {
      utterance.voice = voice;
      utterance.lang = voice.lang;
    }

    utterance.onstart = () => {
      this._speaking = true;
      this._paused = false;
      this._startedAt = performance.now();
      this._startTicker();
    };

    utterance.onend = () => {
      if (this._stopped) return;
      this._accumulated += estimateSeconds(this._chunks[this._chunkIndex], this.rate);
      this._chunkIndex += 1;
      if (this._chunkIndex >= this._chunks.length) {
        this._finish();
      } else {
        this._startedAt = performance.now();
        this._speakCurrent();
      }
    };

    utterance.onerror = (event) => {
      // "interrupted" and "canceled" are what a deliberate stop looks like.
      if (this._stopped || event.error === 'interrupted' || event.error === 'canceled') return;
      this._stopTicker();
      this._speaking = false;
      if (event.error === 'not-allowed') {
        this.onError?.('This browser blocked the voice until you interact with the page.');
      } else {
        // Any other engine failure: keep the lesson moving rather than stall.
        this._runSilently();
      }
    };

    this._utterance = utterance;
    try {
      window.speechSynthesis.speak(utterance);
      this._speaking = true;
      this._startedAt = performance.now();
      this._startTicker();
    } catch {
      this._runSilently();
    }
  }

  /** Advance the clock with no audio, so transcript and progress still work. */
  _runSilently() {
    this._speaking = true;
    this._paused = false;
    this._startedAt = performance.now();
    this._silent = true;
    this._startTicker();
  }

  _startTicker() {
    this._stopTicker();
    const tick = () => {
      if (!this._speaking || this._paused || this._stopped) return;
      const elapsed = this.elapsed;
      this.onProgress?.(Math.min(elapsed, this._total), this._total);
      if (this._silent && elapsed >= this._total) {
        this._finish();
        return;
      }
      this._ticker = requestAnimationFrame(tick);
    };
    this._ticker = requestAnimationFrame(tick);
  }

  _stopTicker() {
    if (this._ticker) cancelAnimationFrame(this._ticker);
    this._ticker = null;
  }

  _finish() {
    this._stopTicker();
    this._speaking = false;
    this._paused = false;
    this._silent = false;
    this._accumulated = this._total;
    this.onProgress?.(this._total, this._total);
    this.onEnd?.();
  }

  pause() {
    if (!this._speaking || this._paused) return;
    this._accumulated += (performance.now() - this._startedAt) / 1000;
    this._paused = true;
    this._stopTicker();
    if (this.supported && !this._silent) {
      try {
        window.speechSynthesis.pause();
      } catch {
        /* some engines do not support pause; the clock is already stopped */
      }
    }
  }

  resume() {
    if (!this._paused) return;
    this._paused = false;
    this._startedAt = performance.now();
    this._startTicker();
    if (this.supported && !this._silent) {
      try {
        window.speechSynthesis.resume();
      } catch {
        /* fall through: the ticker keeps the lesson moving */
      }
    }
  }

  stop() {
    this._stopped = true;
    this._stopTicker();
    this._speaking = false;
    this._paused = false;
    this._silent = false;
    this._accumulated = 0;
    this._chunks = [];
    this._chunkIndex = 0;
    this._utterance = null;
    if (this.supported) {
      try {
        window.speechSynthesis.cancel();
      } catch {
        /* nothing to cancel */
      }
    }
  }

  /**
   * Change speed mid-passage.
   *
   * Web Speech cannot retune a running utterance, so the current sentence is
   * restarted at the new rate. Restarting one sentence rather than the whole
   * section is why this feels like a speed control instead of a rewind.
   */
  setRate(rate) {
    const next = Math.max(0.5, Math.min(3, rate));
    if (Math.abs(next - this.rate) < 0.01) return;

    const wasSpeaking = this._speaking && !this._paused;
    const before = this._durationBefore(this._chunkIndex);
    this.rate = next;

    if (!wasSpeaking || !this._chunks.length) {
      this._total = this._chunks.length
        ? this._durationBefore(this._chunks.length)
        : this._total;
      return;
    }

    // Recompute the timeline at the new rate, then restart this sentence.
    this._total = this._durationBefore(this._chunks.length);
    this._accumulated = this._durationBefore(this._chunkIndex);
    void before;

    if (this.supported && !this._silent) {
      try {
        window.speechSynthesis.cancel();
      } catch {
        /* ignore */
      }
    }
    this._startedAt = performance.now();
    if (this._silent) this._startTicker();
    else this._speakCurrent();
  }

  setVolume(volume) {
    this.volume = Math.max(0, Math.min(1, volume));
    if (this._utterance) this._utterance.volume = this.volume;
    // Applies to the next sentence; restarting mid-sentence to change volume
    // would be more disruptive than waiting a few seconds.
  }

  setVoice(name) {
    this.voiceName = name || '';
  }
}

export const narrator = new Narrator();
