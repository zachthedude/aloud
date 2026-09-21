/**
 * The voice loop for crunch modes.
 *
 * Speak a turn, listen for the reply, send it, speak the next turn. Every mode
 * runs through this one loop; the loop knows nothing about any of them. It
 * only reads what the server says to say and whether to listen afterwards.
 *
 * It does not own any audio of its own. The narrator, the recogniser, barge-in
 * and keep-alive are the same modules the lesson player uses. The loop borrows
 * their callbacks while the room is open and hands them back when it closes,
 * so a lesson picked up afterwards behaves exactly as it did before.
 *
 * ponytail: the lesson player predates this loop and still drives those
 * modules itself. It should move onto this loop once lessons become a mode;
 * until then the two take turns, which is safe because the room pauses any
 * lesson on the way in.
 */

import { api } from './api.js';
import { narrator } from './narrator.js';
import { listener } from './listener.js';
import { bargeIn } from './bargein.js';
import { keepAlive } from './keepalive.js';
import { latencyFrom } from './latency.js';
import { announce } from './feedback.js';

/** Replace some callbacks on an object; returns a function that puts them back. */
function borrow(target, handlers) {
  const saved = {};
  for (const [key, handler] of Object.entries(handlers)) {
    saved[key] = target[key];
    target[key] = handler;
  }
  return () => Object.assign(target, saved);
}

export class VoiceLoop {
  /**
   * @param onChange called with (state, patch) whenever anything changes.
   *                 The room renders from that and nothing else.
   */
  constructor({ onChange } = {}) {
    this.onChange = onChange;
    this.state = {
      session: null,
      turn: null,
      lines: [],        // { who: 'aloud' | 'you', text }
      floor: 'quiet',   // aloud | student | listening | thinking | done | quiet
      partial: '',
      level: 0,
      error: '',
    };
    this._restore = [];
    this._barge = null;
    this._micGranted = false;
    this._asked = null;
    this._heard = null;
    this._pressed = null;
  }

  // -- lifecycle -------------------------------------------------------------

  async start(spaceId, mode) {
    this._attach();
    const result = await this._call(() => api.crunch.start({ spaceId, mode }));
    if (result) this._receive(result);
  }

  async resume(sessionId) {
    this._attach();
    const result = await this._call(() => api.crunch.get(sessionId));
    if (result) this._receive(result);
  }

  /** Leave the room: stop every sound, give the microphone and the callbacks back. */
  async leave({ end = true } = {}) {
    narrator.stop();
    listener.cancel();
    this._barge?.close();
    keepAlive.setPlaying(false);
    keepAlive.stop();
    const session = this.state.session;
    if (end && session && !session.ended) {
      await api.crunch.end(session.id).catch(() => {});
    }
    this._restore.forEach((restore) => restore());
    this._restore = [];
  }

  // -- the student's controls ---------------------------------------------------

  /** Press to talk, press again to stop. Also what barge-in calls. */
  async talk() {
    if (listener.listening) {
      listener.stop();
      return;
    }
    narrator.stop();
    this._barge?.disarm();
    this._set({ floor: 'listening', partial: '' });
    await listener.start();
    // Getting here means the microphone was allowed, so from now on the
    // student can simply talk over the teacher.
    this._micGranted = true;
  }

  /** Send an answer, spoken or typed. */
  async answer(text, inputMode = 'text') {
    const clean = (text || '').trim();
    if (!clean || !this.state.session || this.state.floor === 'thinking') return;
    const latencyMs = latencyFrom({
      asked: this._asked, heard: this._heard, pressed: this._pressed, inputMode,
    });
    this._asked = null;
    this._push('you', clean);
    await this._send({ text: clean, inputMode, latencyMs });
  }

  /** Move past whatever is on the floor. Nothing is recorded against it. */
  async skip() {
    narrator.stop();
    listener.cancel();
    await this._send({ action: 'skip' });
  }

  /** Carry on after a turn that expected no answer, without waiting for the voice. */
  next() {
    narrator.stop();
    this._send({ action: 'next' });
  }

  /** Say the current turn again. */
  repeat() {
    if (this.state.turn) this._speak(this.state.turn);
  }

  // -- internals --------------------------------------------------------------

  _attach() {
    if (this._restore.length) return;
    this._restore.push(
      borrow(narrator, {
        onEnd: () => this._spoken(),
        onProgress: () => {},
        onError: (message) => this._set({ error: message, floor: this._afterSpeaking() }),
      }),
      borrow(listener, {
        onStart: () => {
          this._pressed = performance.now();
          this._set({ floor: 'listening' });
        },
        onPartial: (text) => {
          if (this._heard == null && text) this._heard = performance.now();
          this._set({ partial: text });
        },
        onLevel: (level) => this._set({ level }),
        onStop: () => this._set({ level: 0 }),
        onError: (message) => this._set({ error: message, floor: 'student', level: 0 }),
        onFinal: (text) => {
          this._set({ partial: '', level: 0 });
          if (text && text.trim()) this.answer(text, 'voice');
          else this._set({ floor: 'student', error: 'I did not catch that. Try again, or type it.' });
        },
      })
    );
  }

  async _send(body) {
    const session = this.state.session;
    if (!session) return;
    this._set({ floor: 'thinking', error: '' });
    const result = await this._call(() => api.crunch.turn(session.id, body));
    if (result) this._receive(result);
    else this._set({ floor: this._afterSpeaking() });
  }

  _receive({ session, turn }) {
    this._set({ session, turn });
    this._push('aloud', turn.say);
    this._speak(turn);
  }

  _speak(turn) {
    this._set({ floor: 'aloud', error: '' });
    keepAlive.start();
    keepAlive.setPlaying(true);
    if (!narrator.supported) {
      // No speech engine: the caption is the whole turn. Move on as if heard.
      this._spoken();
      return;
    }
    narrator.speak(turn.say);
    this._armInterrupt();
  }

  /** The current turn has been said in full. */
  _spoken() {
    this._barge?.disarm();
    const turn = this.state.turn;
    if (!turn) return;
    if (turn.done) {
      keepAlive.setPlaying(false);
      keepAlive.stop();
      this._set({ floor: 'done' });
      announce('That sitting is finished.');
      return;
    }
    if (turn.expects === 'answer') {
      // The clock for hesitation starts the moment the floor is handed over.
      this._asked = performance.now();
      this._heard = null;
      this._set({ floor: 'student' });
      announce('Your turn.');
      return;
    }
    this._send({ action: 'next' });
  }

  _afterSpeaking() {
    const turn = this.state.turn;
    if (!turn) return 'quiet';
    if (turn.done) return 'done';
    return turn.expects === 'answer' ? 'student' : 'quiet';
  }

  _armInterrupt() {
    if (!this._micGranted) return;
    if (!this._barge) {
      this._barge = bargeIn({ onInterrupt: () => this.talk() });
    }
    this._barge.arm();
  }

  _push(who, text) {
    // Keep the room short: the last few lines are context, the rest is history
    // that lives in the transcript, not on screen.
    const lines = [...this.state.lines, { who, text }].slice(-6);
    this._set({ lines });
  }

  async _call(request) {
    try {
      return await request();
    } catch (error) {
      this._set({ error: error.message || 'Something went wrong.' });
      return null;
    }
  }

  _set(patch) {
    this.state = { ...this.state, ...patch };
    // The patch goes along too, so the room can update a meter in place
    // rather than rebuilding itself sixty times a second while listening.
    this.onChange?.(this.state, patch);
  }
}
