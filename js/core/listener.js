/**
 * Hearing the student.
 *
 * Two paths, chosen by what the browser can do:
 *
 *   Chrome / Edge   Web Speech recognition. Interim results arrive as the
 *                   student talks, so they can see they are being heard, and
 *                   the audio never leaves the device.
 *
 *   Everyone else   Record with MediaRecorder, send the clip to /api/transcribe,
 *                   which uses Whisper running locally on the server.
 *
 * Either way there is always a text field. Voice-first must not mean
 * voice-only: a student in a quiet library, one with a speech difference, or
 * one whose microphone is broken has to be able to answer.
 *
 * The level meter is a real measurement from an AnalyserNode, not an
 * animation. If it does not move, the microphone genuinely is not hearing
 * anything, which is the information a student needs.
 */

const SpeechRecognition =
  typeof window !== 'undefined'
    ? window.SpeechRecognition || window.webkitSpeechRecognition
    : null;

export const canListenInBrowser = Boolean(SpeechRecognition);
export const canRecord =
  typeof window !== 'undefined' &&
  typeof navigator !== 'undefined' &&
  Boolean(navigator.mediaDevices?.getUserMedia) &&
  typeof window.MediaRecorder !== 'undefined';

/** How long to keep listening after the student stops talking. */
const SILENCE_MS = 2200;
/** Never listen longer than this in one go. */
const MAX_LISTEN_MS = 60_000;

export class MicrophoneDenied extends Error {}
export class MicrophoneUnavailable extends Error {}

export class Listener {
  constructor() {
    this.listening = false;

    this.onPartial = null;   // (text) => void
    this.onFinal = null;     // (text, {confidence}) => void
    this.onLevel = null;     // (0..1) => void
    this.onError = null;     // (message) => void
    this.onStart = null;
    this.onStop = null;

    this._recognition = null;
    this._recorder = null;
    this._chunks = [];
    this._stream = null;
    this._audioContext = null;
    this._analyser = null;
    this._levelTimer = null;
    this._silenceTimer = null;
    this._maxTimer = null;
    this._finalText = '';
    this._resolved = false;
  }

  get available() {
    return canListenInBrowser || canRecord;
  }

  get mode() {
    if (canListenInBrowser) return 'browser';
    if (canRecord) return 'server';
    return 'none';
  }

  async start() {
    if (this.listening) return;
    this._finalText = '';
    this._resolved = false;

    if (!this.available) {
      this.onError?.(
        'This browser cannot use the microphone. You can type your answer instead.'
      );
      return;
    }

    try {
      await this._openMicrophone();
    } catch (error) {
      this._teardown();
      if (error instanceof MicrophoneDenied) {
        this.onError?.(
          'Microphone access is blocked. Allow it in your browser settings, or type your answer.'
        );
      } else {
        this.onError?.(
          'No microphone was found. You can type your answer instead.'
        );
      }
      return;
    }

    this.listening = true;
    this.onStart?.();

    this._maxTimer = setTimeout(() => this.stop(), MAX_LISTEN_MS);

    if (canListenInBrowser) this._startRecognition();
    else this._startRecording();
  }

  async _openMicrophone() {
    if (!navigator.mediaDevices?.getUserMedia) {
      // Recognition can still work without our own stream; skip the meter.
      if (canListenInBrowser) return;
      throw new MicrophoneUnavailable();
    }
    try {
      this._stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
    } catch (error) {
      if (error.name === 'NotAllowedError' || error.name === 'SecurityError') {
        throw new MicrophoneDenied();
      }
      throw new MicrophoneUnavailable();
    }
    this._startLevelMeter();
  }

  _startLevelMeter() {
    if (!this._stream) return;
    try {
      const AudioContextClass = window.AudioContext || window.webkitAudioContext;
      if (!AudioContextClass) return;
      this._audioContext = new AudioContextClass();
      const source = this._audioContext.createMediaStreamSource(this._stream);
      this._analyser = this._audioContext.createAnalyser();
      this._analyser.fftSize = 256;
      this._analyser.smoothingTimeConstant = 0.7;
      source.connect(this._analyser);

      const data = new Uint8Array(this._analyser.frequencyBinCount);
      const sample = () => {
        if (!this._analyser) return;
        this._analyser.getByteTimeDomainData(data);
        let sum = 0;
        for (let i = 0; i < data.length; i += 1) {
          const value = (data[i] - 128) / 128;
          sum += value * value;
        }
        const rms = Math.sqrt(sum / data.length);
        // Perceptual curve, so quiet speech still shows movement.
        this.onLevel?.(Math.min(1, Math.pow(rms * 3.2, 0.7)));
        this._levelTimer = requestAnimationFrame(sample);
      };
      this._levelTimer = requestAnimationFrame(sample);
    } catch {
      /* the meter is a nicety; recognition continues without it */
    }
  }

  _startRecognition() {
    const recognition = new SpeechRecognition();
    recognition.lang = navigator.language?.startsWith('en') ? navigator.language : 'en-GB';
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.maxAlternatives = 1;

    let confidence = 0;

    recognition.onresult = (event) => {
      let interim = '';
      for (let i = event.resultIndex; i < event.results.length; i += 1) {
        const result = event.results[i];
        const text = result[0].transcript;
        if (result.isFinal) {
          this._finalText = `${this._finalText} ${text}`.trim();
          confidence = result[0].confidence || confidence;
        } else {
          interim += text;
        }
      }
      this.onPartial?.(`${this._finalText} ${interim}`.trim());
      this._restartSilenceTimer(confidence);
    };

    recognition.onerror = (event) => {
      if (event.error === 'aborted' || event.error === 'no-speech') return;
      if (event.error === 'not-allowed' || event.error === 'service-not-allowed') {
        this.onError?.(
          'Microphone access is blocked. Allow it in your browser settings, or type your answer.'
        );
      } else if (event.error === 'network') {
        this.onError?.(
          'Speech recognition needs a connection right now. You can type your answer instead.'
        );
      } else {
        this.onError?.('I could not make that out. Try again, or type your answer.');
      }
      this.stop();
    };

    recognition.onend = () => {
      if (this.listening && !this._resolved) this._resolve(confidence);
    };

    this._recognition = recognition;
    try {
      recognition.start();
    } catch {
      this.onError?.('The microphone could not start. You can type your answer instead.');
      this.stop();
    }
  }

  _startRecording() {
    if (!this._stream) {
      this.onError?.('No microphone was found. You can type your answer instead.');
      this.stop();
      return;
    }
    const mimeType = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus', 'audio/mp4']
      .find((type) => MediaRecorder.isTypeSupported?.(type));

    try {
      this._recorder = new MediaRecorder(this._stream, mimeType ? { mimeType } : undefined);
    } catch {
      this.onError?.('This browser could not record audio. You can type your answer instead.');
      this.stop();
      return;
    }

    this._chunks = [];
    this._recorder.ondataavailable = (event) => {
      if (event.data && event.data.size > 0) this._chunks.push(event.data);
    };
    this._recorder.onstop = async () => {
      const blob = new Blob(this._chunks, { type: this._recorder.mimeType || 'audio/webm' });
      this._chunks = [];
      if (blob.size < 1200) {
        this._resolveText('', 0);
        return;
      }
      this.onPartial?.('...');
      try {
        const { api } = await import('./api.js');
        const result = await api.transcribe(blob);
        this._resolveText(result.text || '', result.confidence || 0);
      } catch (error) {
        this.onError?.(error.message || 'That recording could not be understood.');
        this._resolveText('', 0);
      }
    };
    this._recorder.start();
    this._restartSilenceTimer(0);
  }

  /**
   * Stop listening once the student has been quiet for a moment.
   *
   * Deliberately generous. A student thinking mid-answer should not be cut
   * off, and "learning without time pressure" has to hold in the microphone
   * too, not just in the copy.
   */
  _restartSilenceTimer(confidence) {
    clearTimeout(this._silenceTimer);
    this._silenceTimer = setTimeout(() => {
      if (this.listening) this.stop(confidence);
    }, SILENCE_MS);
  }

  _resolve(confidence = 0) {
    this._resolveText(this._finalText.trim(), confidence);
  }

  _resolveText(text, confidence) {
    if (this._resolved) return;
    this._resolved = true;
    this._teardown();
    this.listening = false;
    this.onStop?.();
    this.onFinal?.(text, { confidence });
  }

  stop(confidence = 0) {
    if (!this.listening) return;
    clearTimeout(this._silenceTimer);
    clearTimeout(this._maxTimer);

    if (this._recognition) {
      try {
        this._recognition.stop();
      } catch {
        /* already stopped */
      }
      // onend resolves; if the engine never fires it, resolve anyway.
      setTimeout(() => {
        if (!this._resolved) this._resolve(confidence);
      }, 600);
      return;
    }

    if (this._recorder && this._recorder.state !== 'inactive') {
      this.listening = false;
      this.onStop?.();
      try {
        this._recorder.stop();   // onstop transcribes and resolves
      } catch {
        this._resolveText('', 0);
      }
      this._stopMeter();
      return;
    }

    this._resolveText(this._finalText.trim(), confidence);
  }

  cancel() {
    this._resolved = true;
    this.listening = false;
    clearTimeout(this._silenceTimer);
    clearTimeout(this._maxTimer);
    if (this._recognition) {
      try {
        this._recognition.abort();
      } catch {
        /* ignore */
      }
    }
    if (this._recorder && this._recorder.state !== 'inactive') {
      this._recorder.onstop = null;
      try {
        this._recorder.stop();
      } catch {
        /* ignore */
      }
    }
    this._teardown();
    this.onStop?.();
  }

  _stopMeter() {
    if (this._levelTimer) cancelAnimationFrame(this._levelTimer);
    this._levelTimer = null;
    this.onLevel?.(0);
  }

  _teardown() {
    this._stopMeter();
    this._analyser = null;
    if (this._audioContext) {
      this._audioContext.close().catch(() => {});
      this._audioContext = null;
    }
    if (this._stream) {
      // Release the microphone, so the browser's recording indicator goes out.
      this._stream.getTracks().forEach((track) => track.stop());
      this._stream = null;
    }
    this._recognition = null;
    this._recorder = null;
  }
}

export const listener = new Listener();
