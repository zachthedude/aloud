/**
 * The lesson controller.
 *
 * Owns narration, the microphone and the conversation state for a lesson, and
 * lives at the app level rather than inside a screen. That is what lets a
 * student leave the player, browse their plan, and keep listening: the mini
 * player is a view onto this controller, not a second implementation of it.
 *
 * The server holds the authoritative state machine. This mirrors it, sends
 * transitions as they happen, and saves position continuously so closing the
 * tab on a bus loses at most a couple of seconds.
 */

import { api, ApiError } from './api.js';
import { store, setPlayer, player } from './store.js';
import { narrator, estimateSeconds } from './narrator.js';
import { listener } from './listener.js';
import { announce, notifyError, toast } from './feedback.js';

/** How often to persist playback position. */
const SAVE_INTERVAL_MS = 5000;

/** Sections that hand the floor to the student. */
const QUESTION_KINDS = new Set(['check', 'assessment']);

export const SPEEDS = [0.75, 1, 1.25, 1.5, 1.75, 2];

class LessonPlayer {
  constructor() {
    this._saveTimer = null;
    this._lastSaved = 0;
    this._sleepTimer = null;
    this._sleepAt = 0;
    this._wireNarrator();
    this._wireListener();
  }

  // -- lifecycle ---------------------------------------------------------

  /** Load a lesson and put the player on screen. */
  async open(lessonId, { autoplay = false } = {}) {
    const existing = player();
    if (existing.lessonId === lessonId && existing.lesson) {
      setPlayer({ open: true });
      if (autoplay && !narrator.speaking) this.play();
      return;
    }

    this.stop({ keepOpen: true });
    setPlayer({
      open: true,
      lessonId,
      lesson: null,
      sections: [],
      error: '',
      finished: null,
      reply: null,
      question: null,
      state: 'idle',
    });

    let payload;
    try {
      payload = await api.lessons.get(lessonId);
    } catch (error) {
      setPlayer({ error: error.message || 'That lesson could not be loaded.' });
      return;
    }

    const user = store.get().user;
    const speed = payload.session.playbackSpeed || user?.preferredSpeed || 1;
    narrator.rate = speed;
    if (user?.preferredVoice) narrator.setVoice(user.preferredVoice);

    setPlayer({
      lesson: payload.lesson,
      sections: payload.sections,
      concept: payload.concept,
      neighbours: payload.neighbours,
      audioStatus: payload.audio,
      spaceId: payload.lesson.spaceId,
      sessionId: payload.session.id,
      sectionIndex: Math.min(payload.session.sectionOrdinal, payload.sections.length - 1),
      elapsed: payload.session.positionSeconds || 0,
      speed,
      voice: narrator.voiceName || (narrator.voices()[0]?.name ?? ''),
      state: payload.session.completed ? 'completed' : 'idle',
      question: null,
      reply: null,
      finished: null,
      error: '',
    });

    // Tell the server where the conversation now stands. Without this the
    // next transition is checked against whatever state the last sitting left
    // behind, and a legal move is rejected as an illegal one.
    this._save({ state: payload.session.completed ? 'completed' : 'idle', force: true });

    if (autoplay) this.play();
  }

  close() {
    setPlayer({ open: false });
  }

  /** Stop everything and release the microphone. */
  stop({ keepOpen = false } = {}) {
    narrator.stop();
    listener.cancel();
    clearInterval(this._saveTimer);
    this._saveTimer = null;
    this._clearSleepTimer();
    setPlayer({
      open: keepOpen ? player().open : false,
      listening: false,
      speaking: false,
      partial: '',
      level: 0,
    });
  }

  /** Leave the lesson entirely: stop audio, clear the mini player. */
  exit() {
    const state = player();
    if (state.sessionId) this._save({ state: 'paused', force: true });
    this.stop();
    setPlayer({
      open: false,
      lessonId: '',
      lesson: null,
      sections: [],
      sessionId: '',
      state: 'idle',
      question: null,
      reply: null,
      finished: null,
    });
  }

  // -- transport ---------------------------------------------------------

  get currentSection() {
    const state = player();
    return state.sections[state.sectionIndex] || null;
  }

  play() {
    const state = player();
    const section = this.currentSection;
    if (!section) return;

    if (state.question) {
      // A question is on the floor; playing again re-asks it.
      this.speakQuestion();
      return;
    }

    if (narrator.paused) {
      narrator.resume();
      setPlayer({ speaking: true });
      this._setState(this._speakingState());
      this._startSaving();
      return;
    }

    setPlayer({ reply: null });
    this._speakSection(section, state.elapsed);
  }

  pause() {
    narrator.pause();
    setPlayer({ speaking: false });
    this._setState('paused');
    this._save({ state: 'paused', force: true });
    clearInterval(this._saveTimer);
    this._saveTimer = null;
  }

  toggle() {
    if (narrator.speaking) this.pause();
    else this.play();
  }

  /** Jump within the current section, or across sections at the edges. */
  seekBy(seconds) {
    const state = player();
    const target = state.elapsed + seconds;
    const section = this.currentSection;
    if (!section) return;

    if (target < 0) {
      if (state.sectionIndex === 0) {
        this.seekTo(0);
        return;
      }
      this.goToSection(state.sectionIndex - 1, { fromEnd: true });
      return;
    }
    const total = this._sectionSeconds(section);
    if (target >= total) {
      this.next();
      return;
    }
    this.seekTo(target);
  }

  seekTo(seconds) {
    const section = this.currentSection;
    if (!section) return;
    const clamped = Math.max(0, Math.min(seconds, this._sectionSeconds(section)));
    setPlayer({ elapsed: clamped });
    if (narrator.speaking || narrator.paused) {
      this._speakSection(section, clamped);
    } else {
      this._save({ force: true });
    }
  }

  /** Replay the current beat from its start. */
  replay() {
    setPlayer({ elapsed: 0, reply: null });
    const section = this.currentSection;
    if (section) this._speakSection(section, 0);
    announce('Replaying this part.');
  }

  next() {
    const state = player();
    if (state.sectionIndex >= state.sections.length - 1) {
      this.finish();
      return;
    }
    this.goToSection(state.sectionIndex + 1);
  }

  previous() {
    const state = player();
    if (state.elapsed > 3) {
      this.seekTo(0);
      return;
    }
    if (state.sectionIndex === 0) {
      this.seekTo(0);
      return;
    }
    this.goToSection(state.sectionIndex - 1);
  }

  goToSection(index, { fromEnd = false, autoplay = true } = {}) {
    const state = player();
    const clamped = Math.max(0, Math.min(index, state.sections.length - 1));
    const section = state.sections[clamped];
    if (!section) return;

    listener.cancel();
    const elapsed = fromEnd ? Math.max(0, this._sectionSeconds(section) - 8) : 0;
    setPlayer({
      sectionIndex: clamped,
      elapsed,
      question: null,
      reply: null,
      listening: false,
      partial: '',
    });
    this._save({ force: true });

    if (autoplay) this._speakSection(section, elapsed);
    else this._setState('paused');
  }

  setSpeed(speed) {
    narrator.setRate(speed);
    setPlayer({ speed });
    this._save({ speed, force: true });
    api.auth.updateMe({ preferredSpeed: speed }).catch(() => {});
    store.set((state) =>
      state.user ? { user: { ...state.user, preferredSpeed: speed } } : {}
    );
  }

  setMuted(muted) {
    narrator.setVolume(muted ? 0 : 1);
    setPlayer({ muted });
  }

  /**
   * Step to the next installed voice.
   *
   * A cycle rather than a dropdown: the only way to judge a voice is to hear
   * one, and hearing the next one should cost a single tap while the lesson is
   * in front of you. The full list still lives in settings for anyone who
   * would rather pick by name.
   */
  cycleVoice({ direction = 1, preview = true } = {}) {
    const voices = narrator.voices();
    if (!voices.length) {
      toast('This browser has no speech voices installed.', { tone: 'trouble' });
      return null;
    }

    const current = narrator.voiceName || voices[0].name;
    const index = voices.findIndex((voice) => voice.name === current);
    const next = voices[((index === -1 ? 0 : index) + direction + voices.length) % voices.length];

    const wasSpeaking = narrator.speaking;
    narrator.setVoice(next.name);
    setPlayer({ voice: next.name });

    api.auth.updateMe({ preferredVoice: next.name }).catch(() => {});
    store.set((state) =>
      state.user ? { user: { ...state.user, preferredVoice: next.name } } : {}
    );

    // Re-speak the current beat in the new voice, so the change is audible
    // immediately rather than at the next section boundary.
    if (wasSpeaking) {
      const section = this.currentSection;
      if (section) this._speakSection(section, player().elapsed);
    } else if (preview) {
      narrator.speak('This is how I will sound.');
    }
    return next;
  }

  setVoice(name) {
    narrator.setVoice(name);
    setPlayer({ voice: name });
  }

  toggleTranscript() {
    setPlayer({ showTranscript: !player().showTranscript });
  }

  // -- sleep timer -------------------------------------------------------

  setSleepTimer(minutes) {
    this._clearSleepTimer();
    if (!minutes) {
      toast('Sleep timer off.');
      return;
    }
    this._sleepAt = Date.now() + minutes * 60_000;
    this._sleepTimer = setTimeout(() => {
      this.pause();
      this._sleepAt = 0;
      toast('Sleep timer finished. Your place is saved.');
    }, minutes * 60_000);
    toast(`Pausing in ${minutes} minutes.`);
  }

  get sleepRemaining() {
    return this._sleepAt ? Math.max(0, this._sleepAt - Date.now()) : 0;
  }

  _clearSleepTimer() {
    clearTimeout(this._sleepTimer);
    this._sleepTimer = null;
    this._sleepAt = 0;
  }

  // -- narration ---------------------------------------------------------

  _sectionSeconds(section) {
    const state = player();
    return Math.max(1, estimateSeconds(section.script, state.speed || 1));
  }

  _speakingState() {
    const section = this.currentSection;
    if (!section) return 'playing';
    return QUESTION_KINDS.has(section.kind) ? 'ai_speaking' : 'playing';
  }

  _speakSection(section, offsetSeconds = 0) {
    narrator.rate = player().speed || 1;
    setPlayer({ speaking: true });
    this._setState(this._speakingState());
    this._startSaving();
    narrator.speak(section.script, { offsetSeconds });
  }

  _wireNarrator() {
    narrator.onProgress = (elapsed) => {
      const current = player();
      // `speaking` only enters the store when it flips, so a progress tick
      // stays a cheap update of one number.
      if (current.speaking !== narrator.speaking) {
        setPlayer({ elapsed, speaking: narrator.speaking });
      } else {
        setPlayer({ elapsed });
      }
    };

    narrator.onEnd = () => {
      const state = player();
      const section = state.sections[state.sectionIndex];
      if (!section) return;

      // A check section hands the floor over once the question is asked.
      if (QUESTION_KINDS.has(section.kind) && section.question && !state.question) {
        this._askQuestion(section.question);
        return;
      }
      if (state.sectionIndex >= state.sections.length - 1) {
        this.finish();
        return;
      }
      this.goToSection(state.sectionIndex + 1);
    };

    narrator.onError = (message) => {
      toast(message, { tone: 'trouble' });
      this._setState('paused');
    };
  }

  // -- questions and answers ---------------------------------------------

  _askQuestion(question) {
    setPlayer({ question, reply: null, partial: '', elapsed: 0 });
    this._setState('waiting_for_student');
    announce(`Your turn. ${question.prompt}`, { assertive: true });
  }

  /** Re-speak the question currently on the floor. */
  speakQuestion() {
    const question = player().question;
    if (!question) return;
    narrator.onEnd = () => {
      this._setState('waiting_for_student');
      this._wireNarrator();
    };
    this._setState('ai_speaking');
    narrator.speak(question.prompt);
  }

  async listen() {
    const state = player();
    if (state.listening) {
      listener.stop();
      return;
    }
    narrator.stop();
    setPlayer({ partial: '', level: 0 });
    this._setState('listening');
    await listener.start();
  }

  _wireListener() {
    listener.onPartial = (text) => setPlayer({ partial: text });
    listener.onLevel = (level) => setPlayer({ level });
    listener.onStart = () => setPlayer({ listening: true });
    listener.onStop = () => setPlayer({ listening: false, level: 0 });

    listener.onError = (message) => {
      setPlayer({ listening: false, level: 0 });
      toast(message, { tone: 'trouble', duration: 7000 });
      if (player().question) this._setState('waiting_for_student');
    };

    listener.onFinal = (text) => {
      setPlayer({ listening: false, level: 0 });
      if (!text || !text.trim()) {
        this._setState('waiting_for_student');
        announce('I did not catch that.');
        return;
      }
      this.submitAnswer(text, 'voice');
    };
  }

  /**
   * Send an answer, or act on a command if that is what it turned out to be.
   *
   * A student who says "can you explain that again" while a question is on the
   * floor means it, so anything that is clearly a command is routed to the
   * teacher rather than marked as a wrong answer.
   */
  async submitAnswer(text, inputMode = 'text') {
    const state = player();
    if (!state.question || !state.sessionId) return;

    setPlayer({ partial: text });
    this._setState('transcribing');

    // Ask the server to classify first: it owns intent parsing, so the client
    // does not carry a second, drifting copy of the rules.
    try {
      const parsed = await api.spaces.ask(state.spaceId, {
        text,
        sessionId: state.sessionId,
        conceptId: state.question.conceptId || '',
      });
      if (parsed.handledBy === 'player') {
        this._runLocalIntent(parsed.intent);
        return;
      }
      if (parsed.handledBy === 'teacher' && parsed.answer) {
        this._showReply({ text: parsed.answer.text, sourceNote: parsed.answer.sourceNote });
        this._setState('explaining');
        this._speakReply(parsed.answer.text, () => this._setState('waiting_for_student'));
        return;
      }
    } catch (error) {
      if (error instanceof ApiError && error.isOffline) {
        notifyError(error);
        this._setState('waiting_for_student');
        return;
      }
      // Classification failed: treat it as an answer, which is the likelier case.
    }

    this._setState('evaluating');
    try {
      const result = await api.sessions.answer(state.sessionId, {
        questionId: state.question.id,
        transcript: text,
        inputMode,
      });
      const reply = result.reply;
      setPlayer({ question: null, partial: '' });
      this._showReply({
        text: reply.text,
        judgement: reply.judgement,
        mastery: reply.masteryLabel,
        action: reply.action,
      });
      this._setState('explaining');
      announce(reply.text);
      this._speakReply(reply.text, () => this._afterReply(reply));
    } catch (error) {
      notifyError(error);
      this._setState('waiting_for_student');
    }
  }

  _showReply(reply) {
    setPlayer({ reply });
  }

  _speakReply(text, done) {
    setPlayer({ speaking: true });
    narrator.onEnd = () => {
      this._wireNarrator();
      done?.();
    };
    narrator.rate = player().speed || 1;
    narrator.speak(text);
  }

  /**
   * Act on what the teacher decided. This is the adaptive branch: a strong
   * answer moves on, a shaky one gets another explanation, a repeated
   * misunderstanding drops back to the section before.
   */
  _afterReply(reply) {
    const state = player();
    setPlayer({ reply: { ...state.reply, done: true } });

    switch (reply.action) {
      case 'ask_again':
        if (reply.followUpQuestion) {
          this._askQuestion(reply.followUpQuestion);
        } else if (state.sections[state.sectionIndex]?.question) {
          this._askQuestion(state.sections[state.sectionIndex].question);
        } else {
          this._setState('paused');
        }
        break;

      case 'reexplain':
      case 'another_example': {
        // Go back to the nearest explanation or example before this check.
        const target = this._findPrevious(['example', 'analogy', 'explanation', 'deeper']);
        if (target >= 0) {
          this.goToSection(target);
        } else {
          this._advance();
        }
        break;
      }

      case 'revisit_prereq':
        this._setState('reviewing');
        setPlayer({ reply: { ...state.reply, done: true, revisit: true } });
        break;

      case 'extend':
      case 'continue':
      default:
        this._advance();
        break;
    }
  }

  _findPrevious(kinds) {
    const state = player();
    for (let i = state.sectionIndex - 1; i >= 0; i -= 1) {
      if (kinds.includes(state.sections[i].kind)) return i;
    }
    return -1;
  }

  _advance() {
    const state = player();
    if (state.sectionIndex >= state.sections.length - 1) {
      this.finish();
      return;
    }
    this.goToSection(state.sectionIndex + 1);
  }

  /** Skip the question on the floor without answering it. */
  skipQuestion() {
    setPlayer({ question: null, partial: '', reply: null });
    this._advance();
  }

  async anotherQuestion() {
    const state = player();
    if (!state.sessionId) return;
    this._setState('thinking');
    try {
      const { question } = await api.sessions.anotherQuestion(state.sessionId);
      narrator.onEnd = () => {
        this._wireNarrator();
        this._askQuestion(question);
      };
      this._setState('ai_speaking');
      narrator.speak(question.prompt);
    } catch (error) {
      notifyError(error);
      this._setState('paused');
    }
  }

  // -- asking the teacher ------------------------------------------------

  async ask(text) {
    const state = player();
    if (!state.spaceId || !text.trim()) return null;

    const wasSpeaking = narrator.speaking;
    narrator.pause();
    this._setState('thinking');

    try {
      const result = await api.spaces.ask(state.spaceId, {
        text,
        sessionId: state.sessionId,
        conceptId: state.concept?.id || '',
      });

      if (result.handledBy === 'player') {
        this._runLocalIntent(result.intent, { wasSpeaking });
        return result;
      }
      if (result.handledBy === 'quiz') {
        await this.anotherQuestion();
        return result;
      }
      if (result.handledBy === 'bookmark') {
        await this.bookmark();
        if (wasSpeaking) this.play();
        return result;
      }
      if (result.answer) {
        this._showReply({ text: result.answer.text, sourceNote: result.answer.sourceNote });
        this._setState('explaining');
        announce(result.answer.text);
        this._speakReply(result.answer.text, () => {
          if (player().question) this._setState('waiting_for_student');
          else this._setState('paused');
        });
      }
      return result;
    } catch (error) {
      notifyError(error);
      this._setState(wasSpeaking ? 'paused' : 'idle');
      return null;
    }
  }

  /** Commands the player carries out itself, with no round trip. */
  _runLocalIntent(intent, { wasSpeaking = true } = {}) {
    switch (intent) {
      case 'pause': this.pause(); break;
      case 'resume': this.play(); break;
      case 'repeat': this.replay(); break;
      case 'back': this.previous(); break;
      case 'forward': this.next(); break;
      case 'slower': this.setSpeed(this._adjacentSpeed(-1)); this._resumeIf(wasSpeaking); break;
      case 'faster': this.setSpeed(this._adjacentSpeed(1)); this._resumeIf(wasSpeaking); break;
      case 'stop': this.finish(); break;
      default: this._resumeIf(wasSpeaking); break;
    }
  }

  _resumeIf(wasSpeaking) {
    if (wasSpeaking && !narrator.speaking) this.play();
  }

  _adjacentSpeed(direction) {
    const current = player().speed || 1;
    const index = SPEEDS.indexOf(current);
    const base = index === -1 ? SPEEDS.indexOf(1) : index;
    return SPEEDS[Math.max(0, Math.min(SPEEDS.length - 1, base + direction))];
  }

  // -- finishing ---------------------------------------------------------

  async finish() {
    const state = player();
    narrator.stop();
    setPlayer({ speaking: false });
    listener.cancel();
    clearInterval(this._saveTimer);
    this._saveTimer = null;

    if (!state.sessionId) {
      this._setState('completed');
      return;
    }
    this._setState('reviewing');
    try {
      const result = await api.sessions.complete(state.sessionId);
      setPlayer({
        finished: result,
        question: null,
        reply: null,
        state: 'completed',
      });
      announce(`Lesson finished. ${result.assessment.recommendation}`);
    } catch (error) {
      notifyError(error);
      this._setState('completed');
    }
  }

  async bookmark() {
    const state = player();
    if (!state.spaceId || !state.lessonId) return;
    try {
      await api.bookmarks.add(state.spaceId, {
        lessonId: state.lessonId,
        sectionOrdinal: state.sectionIndex,
        positionSeconds: state.elapsed,
        label: state.sections[state.sectionIndex]?.heading || state.lesson?.title || '',
      });
      toast('Saved to your library.');
    } catch (error) {
      notifyError(error);
    }
  }

  // -- persistence -------------------------------------------------------

  _setState(next) {
    setPlayer({ state: next });
    this._save({ state: next, force: true });
  }

  _startSaving() {
    if (this._saveTimer) return;
    this._saveTimer = setInterval(() => this._save({}), SAVE_INTERVAL_MS);
  }

  /**
   * Persist position and state.
   *
   * An illegal transition is a client bug, not something to show a student, so
   * it is logged and swallowed: losing a position update is better than an
   * error toast interrupting a lesson.
   */
  async _save({ state, speed, force = false } = {}) {
    const current = player();
    if (!current.sessionId) return;

    const now = Date.now();
    if (!force && now - this._lastSaved < SAVE_INTERVAL_MS - 200) return;
    this._lastSaved = now;

    const payload = {
      sectionOrdinal: current.sectionIndex,
      positionSeconds: Math.round(current.elapsed * 10) / 10,
    };
    if (state) payload.state = state;
    if (speed) payload.playbackSpeed = speed;

    try {
      await api.sessions.update(current.sessionId, payload);
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        console.warn('Player state rejected by server:', error.message);
      }
      // Offline or transient: the next save will carry the position.
    }
  }
}

export const lessonPlayer = new LessonPlayer();

/**
 * Save on the way out.
 *
 * `pagehide` fires reliably on mobile where `unload` does not, and this is a
 * product people close mid-lesson. `keepalive` lets the request outlive the
 * page; `sendBeacon` cannot be used here because it only issues POST and this
 * endpoint is a PATCH.
 */
function saveOnExit() {
  const state = player();
  if (!state.sessionId) return;
  try {
    fetch(`/api/sessions/${state.sessionId}`, {
      method: 'PATCH',
      keepalive: true,
      credentials: 'same-origin',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        sectionOrdinal: state.sectionIndex,
        positionSeconds: Math.round(state.elapsed * 10) / 10,
      }),
    }).catch(() => {});
  } catch {
    /* the periodic save has already stored a position within a few seconds */
  }
}

window.addEventListener('pagehide', saveOnExit);
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'hidden') saveOnExit();
});
