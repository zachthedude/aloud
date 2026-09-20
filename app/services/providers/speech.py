"""Speech providers.

A deliberate split runs through the product:

  Live narration  the browser's own speech synthesis. Instant, offline, free,
                  and it changes speed without turning the teacher into a
                  chipmunk, because the engine re-synthesises rather than
                  resampling. Handled entirely in the client.

  Audio files     these providers. Used for downloads and offline listening,
                  where a real .mp3 has to exist.

  Answers         the browser's speech recognition where it exists (Chrome,
                  Edge), and `WhisperSTT` here for everyone else.

When no provider can serve a request the UI says so plainly rather than showing
a download button that does nothing.
"""

from __future__ import annotations

import io
import math
import tempfile
from pathlib import Path

from .base import ProviderUnavailable, SpeechResult, TranscriptionResult

#: Average speaking rate, used to estimate duration when probing is unavailable.
WORDS_PER_MINUTE = 150


def estimate_duration(text: str) -> float:
    words = len(text.split())
    return round(max(1.0, words / WORDS_PER_MINUTE * 60.0), 2)


def _probe_duration(data: bytes, suffix: str) -> float:
    """Read the real duration from an encoded file, if PyAV is installed."""
    try:
        import av  # type: ignore
    except ImportError:
        return 0.0
    try:
        with av.open(io.BytesIO(data)) as container:
            if container.duration:
                return round(container.duration / 1_000_000, 2)
    except Exception:  # noqa: BLE001 - probing is best-effort by design
        return 0.0
    return 0.0


class NullTTS:
    """No audio-file generation configured.

    Live narration still works, because that is the browser's job. This
    provider exists so the rest of the app has something to ask, and so the UI
    can tell the truth: downloads are unavailable, not broken.
    """

    name = "none"
    display_name = "Downloads off"
    available = False
    mime_type = "audio/mpeg"

    def synthesise(self, text: str, *, voice: str = "") -> SpeechResult:
        raise ProviderUnavailable(
            "No speech provider is configured, so lessons cannot be saved as audio files."
        )

    def voices(self) -> list[dict[str, str]]:
        return []


class GoogleTTS:
    """gTTS. Produces MP3, needs network, no API key."""

    name = "gtts"
    display_name = "Google Text-to-Speech"
    mime_type = "audio/mpeg"

    #: Accents worth offering. Kept short on purpose.
    _VOICES = [
        {"id": "com", "label": "English (US)"},
        {"id": "co.uk", "label": "English (UK)"},
        {"id": "com.au", "label": "English (Australia)"},
        {"id": "co.in", "label": "English (India)"},
        {"id": "ie", "label": "English (Ireland)"},
        {"id": "co.za", "label": "English (South Africa)"},
    ]

    def __init__(self) -> None:
        try:
            from gtts import gTTS  # noqa: F401
            self._ok = True
        except ImportError:
            self._ok = False

    @property
    def available(self) -> bool:
        return self._ok

    def synthesise(self, text: str, *, voice: str = "") -> SpeechResult:
        if not self._ok:
            raise ProviderUnavailable("gTTS is not installed.")
        from gtts import gTTS
        from gtts.tts import gTTSError

        tld = voice if voice in {v["id"] for v in self._VOICES} else "co.uk"
        buffer = io.BytesIO()
        try:
            gTTS(text=text, lang="en", tld=tld, slow=False).write_to_fp(buffer)
        except gTTSError as error:
            raise ProviderUnavailable(f"Speech service refused the request: {error}") from None
        except Exception as error:  # noqa: BLE001 - network stack raises broadly
            raise ProviderUnavailable(f"Could not reach the speech service: {error}") from None

        data = buffer.getvalue()
        if not data:
            raise ProviderUnavailable("Speech service returned no audio.")
        return SpeechResult(
            audio=data,
            mime_type=self.mime_type,
            duration_seconds=_probe_duration(data, ".mp3") or estimate_duration(text),
            provider=self.name,
        )

    def voices(self) -> list[dict[str, str]]:
        return list(self._VOICES)


class SystemTTS:
    """pyttsx3 over the operating system's own voices. Offline, produces WAV."""

    name = "pyttsx3"
    display_name = "System voice (offline)"
    mime_type = "audio/wav"

    def __init__(self) -> None:
        try:
            import pyttsx3  # noqa: F401
            self._ok = True
        except ImportError:
            self._ok = False

    @property
    def available(self) -> bool:
        return self._ok

    def _engine(self):
        import pyttsx3

        # A fresh engine per call: the driver holds OS resources and gets
        # unreliable when a single instance is reused across requests.
        return pyttsx3.init()

    def synthesise(self, text: str, *, voice: str = "") -> SpeechResult:
        if not self._ok:
            raise ProviderUnavailable("pyttsx3 is not installed.")
        target = Path(tempfile.gettempdir()) / f"aloud_{abs(hash(text)) & 0xFFFFFFFF:08x}.wav"
        try:
            engine = self._engine()
            if voice:
                engine.setProperty("voice", voice)
            engine.setProperty("rate", 165)
            engine.save_to_file(text, str(target))
            engine.runAndWait()
            engine.stop()
        except Exception as error:  # noqa: BLE001 - SAPI raises broadly
            raise ProviderUnavailable(f"The system voice failed: {error}") from None

        if not target.exists() or target.stat().st_size == 0:
            raise ProviderUnavailable("The system voice produced no audio.")
        data = target.read_bytes()
        target.unlink(missing_ok=True)
        return SpeechResult(
            audio=data,
            mime_type=self.mime_type,
            duration_seconds=_wav_duration(data) or estimate_duration(text),
            provider=self.name,
        )

    def voices(self) -> list[dict[str, str]]:
        if not self._ok:
            return []
        try:
            engine = self._engine()
            found = [
                {"id": v.id, "label": getattr(v, "name", "System voice")}
                for v in engine.getProperty("voices")
            ]
            engine.stop()
            return found
        except Exception:  # noqa: BLE001 - enumerating voices is best-effort
            return []


def _wav_duration(data: bytes) -> float:
    """Duration straight from the WAV header."""
    try:
        import wave

        with wave.open(io.BytesIO(data)) as handle:
            frames = handle.getnframes()
            rate = handle.getframerate()
            return round(frames / rate, 2) if rate else 0.0
    except Exception:  # noqa: BLE001 - header parsing is best-effort
        return 0.0


class NullSTT:
    """No server-side recognition. The browser handles answers, or typing does."""

    name = "none"
    display_name = "Browser only"
    available = False

    def transcribe(self, audio: bytes, *, mime_type: str = "") -> TranscriptionResult:
        raise ProviderUnavailable(
            "This browser cannot listen, and no speech recognition is configured on the server. "
            "You can type your answer instead."
        )


class WhisperSTT:
    """faster-whisper, running locally.

    The model is loaded on first use, not at import, so a server that never
    receives audio never pays the memory cost.
    """

    name = "whisper"
    display_name = "Whisper (on this machine)"

    def __init__(self, model_size: str = "base") -> None:
        self._model_size = model_size
        self._model = None
        try:
            import faster_whisper  # noqa: F401
            self._ok = True
        except ImportError:
            self._ok = False

    @property
    def available(self) -> bool:
        return self._ok

    @staticmethod
    def _cached_model_dir(size: str) -> Path | None:
        """Find an already-downloaded model without going through the hub.

        `faster-whisper` resolves a model name through `huggingface_hub`, which
        imports `httpx`. An old pinned `httpx` in the environment can make that
        import fail on newer Pythons, taking transcription down even when the
        model is sitting on disk. Pointing at the snapshot directory skips the
        resolver entirely, so a cached model keeps working regardless.
        """
        root = Path.home() / ".cache" / "huggingface" / "hub"
        for repo in (f"models--Systran--faster-whisper-{size}", f"models--openai--whisper-{size}"):
            snapshots = root / repo / "snapshots"
            if not snapshots.is_dir():
                continue
            for snapshot in sorted(snapshots.iterdir(), reverse=True):
                if (snapshot / "model.bin").is_file():
                    return snapshot
        return None

    def _load(self):
        if self._model is None:
            from faster_whisper import WhisperModel

            cached = self._cached_model_dir(self._model_size)
            source = str(cached) if cached else self._model_size
            self._model = WhisperModel(source, device="cpu", compute_type="int8")
        return self._model

    def transcribe_file(self, path) -> list[tuple[float, float, str]]:
        """Transcribe a recording on disk, keeping segment timings.

        Separate from `transcribe` because the timings matter here: they are
        what lets a lecture be split into passages at the points where the
        speaker actually paused. PyAV decodes the container, so video files
        work without a separate demux step or ffmpeg on PATH.
        """
        if not self._ok:
            raise ProviderUnavailable("Speech recognition is not installed on the server.")
        segments, _info = self._load().transcribe(
            str(path), language="en", vad_filter=True, beam_size=1
        )
        return [
            (float(segment.start), float(segment.end), segment.text)
            for segment in segments
            if segment.text and segment.text.strip()
        ]

    def transcribe(self, audio: bytes, *, mime_type: str = "") -> TranscriptionResult:
        if not self._ok:
            raise ProviderUnavailable("Speech recognition is not installed on the server.")
        if not audio:
            raise ProviderUnavailable("No audio was received.")

        suffix = ".webm" if "webm" in mime_type else ".ogg" if "ogg" in mime_type else ".wav"
        path = Path(tempfile.gettempdir()) / f"aloud_in_{abs(hash(audio[:64])) & 0xFFFFFFFF:08x}{suffix}"
        path.write_bytes(audio)
        try:
            segments, info = self._load().transcribe(str(path), language="en", vad_filter=True)
            parts = list(segments)
            text = " ".join(segment.text.strip() for segment in parts).strip()
            # average_logprob is a log probability; map it to a rough 0..1.
            if parts:
                mean_logprob = sum(getattr(s, "avg_logprob", -1.0) for s in parts) / len(parts)
                confidence = round(max(0.0, min(1.0, math.exp(mean_logprob))), 2)
            else:
                confidence = 0.0
        except Exception as error:  # noqa: BLE001 - decoding raises broadly
            raise ProviderUnavailable(f"Could not make out the audio: {error}") from None
        finally:
            path.unlink(missing_ok=True)

        if not text:
            raise ProviderUnavailable("Nothing audible was found in that recording.")
        return TranscriptionResult(text=text, confidence=confidence, provider=self.name)
