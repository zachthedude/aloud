/**
 * Interrupting the teacher.
 *
 * A student has to be able to cut in mid-sentence, the way you would with a
 * person, and have Aloud stop immediately. That is the whole feel of the
 * product: a tutor you can talk over, not a podcast with a pause button.
 *
 * The hard part is not detecting speech. It is not detecting *Aloud's own
 * speech*, which is playing out of the same device the microphone is pointed
 * at. Get that wrong and the teacher interrupts itself the moment it opens
 * its mouth, forever. Four defences, in order of how much work they do:
 *
 *   1. `echoCancellation` on the microphone stream, which the browser
 *      implements against the audio it is itself playing. Does most of it.
 *   2. Calibration happens *while Aloud is talking*, so whatever leaks
 *      through is baked into the floor rather than mistaken for a voice.
 *   3. A grace period before arming, so the first syllable is never the
 *      trigger.
 *   4. A sustain window: a voice has to stay above the floor for a fifth of
 *      a second. A door closing is louder than a person and much shorter.
 *
 * The detection logic is deliberately separate from the microphone plumbing,
 * because a headless browser has no microphone and this is the one piece of
 * this product that absolutely must be tested.
 */

/** Below this, nothing is a voice, however quiet the room. */
export const FLOOR = 0.05;

export class InterruptDetector {
  /**
   * @param margin      how far above the calibrated floor counts as a voice
   * @param sustainMs   how long it has to stay there
   * @param graceMs     silence from the detector for this long after speech starts
   * @param calibrateMs how long the floor is measured for
   */
  constructor({ margin = 0.085, sustainMs = 180, graceMs = 350, calibrateMs = 700 } = {}) {
    this.margin = margin;
    this.sustainMs = sustainMs;
    this.graceMs = graceMs;
    this.calibrateMs = calibrateMs;
    this.reset();
  }

  reset() {
    this._startedAt = null;
    this._samples = 0;
    this._sum = 0;
    this._ambient = 0;
    this._aboveSince = null;
    this._fired = false;
  }

  /** Called the moment Aloud starts speaking. */
  start(now) {
    this.reset();
    this._startedAt = now;
  }

  get calibrated() {
    return this._startedAt !== null && this._samples > 0;
  }

  /** The level a voice has to beat. Exposed so a meter can show it. */
  get threshold() {
    return Math.max(FLOOR, this._ambient + this.margin);
  }

  /**
   * Feed one microphone level. Returns true exactly once, on the frame the
   * student is judged to have started talking.
   */
  push(level, now) {
    if (this._startedAt === null || this._fired) return false;
    const elapsed = now - this._startedAt;

    if (elapsed < this.calibrateMs) {
      // Measuring the room, with Aloud's own voice leaking into it.
      this._samples += 1;
      this._sum += level;
      this._ambient = this._sum / this._samples;
      return false;
    }
    if (elapsed < this.graceMs) return false;

    if (level < this.threshold) {
      this._aboveSince = null;
      return false;
    }
    if (this._aboveSince === null) {
      this._aboveSince = now;
      return false;
    }
    if (now - this._aboveSince >= this.sustainMs) {
      this._fired = true;
      return true;
    }
    return false;
  }
}

/**
 * The detector wired to a real microphone.
 *
 * Holds its own stream so it can listen while the recogniser is not running.
 * Opening a microphone needs permission, so this fails quietly: a student who
 * has said no to the microphone still gets a lesson, just with a pause button
 * instead of the ability to talk over it.
 */
export function bargeIn({ onInterrupt, onLevel } = {}) {
  const detector = new InterruptDetector();
  let stream = null;
  let context = null;
  let analyser = null;
  let frame = null;
  let armed = false;
  let data = null;

  async function open() {
    if (stream || !navigator.mediaDevices?.getUserMedia) return Boolean(stream);
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        // Echo cancellation is what stops Aloud hearing itself. Without it
        // this whole mechanism is unusable on a speaker.
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: false },
      });
    } catch (error) {
      return false;
    }
    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    if (!AudioContextClass) return false;
    context = new AudioContextClass();
    analyser = context.createAnalyser();
    analyser.fftSize = 256;
    analyser.smoothingTimeConstant = 0.6;
    context.createMediaStreamSource(stream).connect(analyser);
    data = new Uint8Array(analyser.frequencyBinCount);
    return true;
  }

  function level() {
    analyser.getByteTimeDomainData(data);
    let sum = 0;
    for (let i = 0; i < data.length; i += 1) {
      const value = (data[i] - 128) / 128;
      sum += value * value;
    }
    return Math.sqrt(sum / data.length);
  }

  function tick() {
    if (!armed || !analyser) return;
    const now = performance.now();
    const value = level();
    onLevel?.(value, detector.threshold);
    if (detector.push(value, now)) {
      armed = false;
      onInterrupt?.();
      return;
    }
    frame = requestAnimationFrame(tick);
  }

  return {
    detector,

    /** Start watching. Called when Aloud begins a sentence. */
    async arm() {
      if (armed) return true;
      if (!(await open())) return false;
      if (context?.state === 'suspended') await context.resume().catch(() => {});
      detector.start(performance.now());
      armed = true;
      frame = requestAnimationFrame(tick);
      return true;
    },

    /** Stop watching, without giving up the microphone. */
    disarm() {
      armed = false;
      if (frame != null) cancelAnimationFrame(frame);
      frame = null;
      detector.reset();
    },

    /** Give the microphone back. The indicator light matters to people. */
    close() {
      this.disarm();
      stream?.getTracks().forEach((track) => track.stop());
      context?.close().catch(() => {});
      stream = null;
      context = null;
      analyser = null;
    },

    get armed() {
      return armed;
    },
  };
}
