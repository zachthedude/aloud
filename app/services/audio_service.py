"""Rendering lessons to audio files.

Audio is generated per lesson section, never as one file for a whole subject.
That is what makes retries cheap, caching possible, partial downloads useful
and adaptive teaching workable: when the teacher decides to explain something
again, only that beat is re-rendered.

Live narration does not come through here at all. The browser speaks the
lesson, which is instant, free, works offline and changes speed without
distorting the voice. These files exist so a student can take a lesson with
them: download it, listen on a plane, keep it after the tab is closed.
"""

from __future__ import annotations

import io
import logging
import re
import wave
from pathlib import Path

from ..domain.models import AudioAsset, AudioStatus, Lesson, LessonSection, new_id
from .jobs import JobHandle, Step
from .providers.base import ProviderUnavailable, TextToSpeechProvider

logger = logging.getLogger("aloud.audio")

#: Providers cap request length; long sections are spoken in pieces and joined.
MAX_CHARS_PER_REQUEST = 1800


class AudioService:
    def __init__(self, repos, tts: TextToSpeechProvider, audio_dir: Path) -> None:
        self.repos = repos
        self.tts = tts
        self.audio_dir = audio_dir
        self.audio_dir.mkdir(parents=True, exist_ok=True)

    @property
    def available(self) -> bool:
        return bool(self.tts.available)

    def default_voice(self) -> str:
        voices = self.tts.voices()
        return voices[0]["id"] if voices else ""

    # -- generation --------------------------------------------------------

    def ensure_section(self, section: LessonSection, *, voice: str) -> AudioAsset:
        """Return cached audio for a section, rendering it if needed."""
        existing = self.repos.audio.for_section(section.id, voice)
        if existing and existing.status is AudioStatus.READY:
            path = Path(existing.file_path)
            if path.is_file() and path.stat().st_size > 0:
                return existing
            # The row says ready but the file is gone. Re-render rather than
            # hand the client a download that 404s.
            logger.info("Audio file missing for %s, regenerating", section.id)

        if not self.tts.available:
            return self.repos.audio.upsert(
                AudioAsset(
                    id=new_id("aud"),
                    lesson_section_id=section.id,
                    lesson_id=section.lesson_id,
                    voice=voice,
                    status=AudioStatus.UNAVAILABLE,
                    error="No speech provider is configured.",
                )
            )

        asset = AudioAsset(
            id=existing.id if existing else new_id("aud"),
            lesson_section_id=section.id,
            lesson_id=section.lesson_id,
            voice=voice,
            status=AudioStatus.PREPARING,
        )
        self.repos.audio.upsert(asset)

        try:
            audio, mime, duration = self._synthesise(section.script, voice)
        except ProviderUnavailable as error:
            asset.status = AudioStatus.FAILED
            asset.error = str(error)
            return self.repos.audio.upsert(asset)

        extension = ".mp3" if "mpeg" in mime else ".wav"
        path = self.audio_dir / f"{section.id}_{_slug(voice)}{extension}"
        path.write_bytes(audio)

        asset.status = AudioStatus.READY
        asset.file_path = str(path)
        asset.mime_type = mime
        asset.byte_size = len(audio)
        asset.duration_seconds = duration
        asset.error = ""
        return self.repos.audio.upsert(asset)

    def _synthesise(self, script: str, voice: str) -> tuple[bytes, str, float]:
        """Speak a script, splitting long text and joining the results."""
        chunks = _split_for_tts(script)
        pieces: list[bytes] = []
        total = 0.0
        mime = self.tts.mime_type
        for chunk in chunks:
            result = self.tts.synthesise(chunk, voice=voice)
            pieces.append(result.audio)
            total += result.duration_seconds
            mime = result.mime_type
        if len(pieces) == 1:
            return pieces[0], mime, total
        return _join_audio(pieces, mime), mime, total

    def prepare_lesson(
        self, lesson: Lesson, *, voice: str, handle: JobHandle | None = None
    ) -> dict:
        """Render every section of a lesson. Runs as a background job."""
        sections = self.repos.lessons.sections(lesson.id)
        ready = 0
        failed = 0
        for index, section in enumerate(sections):
            asset = self.ensure_section(section, voice=voice)
            if asset.status is AudioStatus.READY:
                ready += 1
            else:
                failed += 1
            if handle:
                handle.step(Step.AUDIO, (index + 1) / max(1, len(sections)))

        result = {
            "lessonId": lesson.id,
            "ready": ready,
            "failed": failed,
            "total": len(sections),
            "voice": voice,
        }
        if handle:
            if ready:
                handle.done(Step.DONE, **result)
            else:
                handle.fail("No audio could be generated for this lesson.")
        return result

    # -- reading -----------------------------------------------------------

    def lesson_status(self, lesson_id: str, voice: str) -> dict:
        """Download state for a lesson, as the UI should describe it."""
        sections = self.repos.lessons.sections(lesson_id)
        assets = {a.lesson_section_id: a for a in self.repos.audio.for_lesson(lesson_id, voice)}

        ready = [s for s in sections if _is_ready(assets.get(s.id))]
        preparing = [s for s in sections if assets.get(s.id) and assets[s.id].status is AudioStatus.PREPARING]
        failed = [s for s in sections if assets.get(s.id) and assets[s.id].status is AudioStatus.FAILED]

        if not self.tts.available:
            state = "unavailable"
        elif len(ready) == len(sections) and sections:
            state = "ready"
        elif preparing:
            state = "preparing"
        elif failed and not ready:
            state = "failed"
        elif ready:
            state = "partial"
        else:
            state = "absent"

        duration = sum(assets[s.id].duration_seconds for s in ready if s.id in assets)
        size = sum(assets[s.id].byte_size for s in ready if s.id in assets)
        return {
            "state": state,
            "ready": len(ready),
            "total": len(sections),
            "durationSeconds": round(duration, 1),
            "byteSize": size,
            "voice": voice,
            "message": _download_message(state, len(ready), len(sections)),
        }

    def section_asset(self, section_id: str, voice: str) -> AudioAsset | None:
        asset = self.repos.audio.for_section(section_id, voice)
        if asset and asset.status is AudioStatus.READY and Path(asset.file_path).is_file():
            return asset
        return None

    def lesson_file(self, lesson_id: str, voice: str) -> tuple[bytes, str, str] | None:
        """One file for the whole lesson: bytes, mime type and filename."""
        lesson = self.repos.lessons.get(lesson_id)
        if lesson is None:
            return None
        sections = self.repos.lessons.sections(lesson_id)
        assets = {a.lesson_section_id: a for a in self.repos.audio.for_lesson(lesson_id, voice)}

        pieces: list[bytes] = []
        mime = self.tts.mime_type
        for section in sections:
            asset = assets.get(section.id)
            if not _is_ready(asset):
                continue
            path = Path(asset.file_path)
            if not path.is_file():
                continue
            pieces.append(path.read_bytes())
            mime = asset.mime_type

        if not pieces:
            return None
        extension = "mp3" if "mpeg" in mime else "wav"
        filename = f"{_slug(lesson.title) or 'lesson'}.{extension}"
        return _join_audio(pieces, mime), mime, filename


def _is_ready(asset: AudioAsset | None) -> bool:
    return bool(asset and asset.status is AudioStatus.READY)


def _download_message(state: str, ready: int, total: int) -> str:
    return {
        "unavailable": "Saving lessons as files is switched off on this server.",
        "ready": "Saved. Available offline.",
        "preparing": "Preparing audio...",
        "partial": f"{ready} of {total} parts saved.",
        "failed": "The audio could not be prepared. You can try again.",
        "absent": "Save this lesson to listen offline.",
    }.get(state, "")


def _slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", value or "").strip("-").lower()
    return slug[:60]


def _split_for_tts(script: str) -> list[str]:
    """Break a script at sentence boundaries under the provider's limit."""
    script = " ".join(script.split())
    if len(script) <= MAX_CHARS_PER_REQUEST:
        return [script]
    chunks: list[str] = []
    current = ""
    for sentence in re.split(r"(?<=[.!?])\s+", script):
        if len(current) + len(sentence) + 1 > MAX_CHARS_PER_REQUEST and current:
            chunks.append(current.strip())
            current = ""
        current += sentence + " "
    if current.strip():
        chunks.append(current.strip())
    return chunks or [script]


def _join_audio(pieces: list[bytes], mime: str) -> bytes:
    """Join rendered pieces into one file.

    WAV is joined through the `wave` module so the header describes the real
    length. MP3 frames are self-describing, so concatenating them produces a
    file every player handles.
    """
    if len(pieces) == 1:
        return pieces[0]
    if "wav" in mime:
        return _join_wav(pieces)
    return b"".join(pieces)


def _join_wav(pieces: list[bytes]) -> bytes:
    output = io.BytesIO()
    writer: wave.Wave_write | None = None
    try:
        for piece in pieces:
            with wave.open(io.BytesIO(piece)) as reader:
                if writer is None:
                    writer = wave.open(output, "wb")
                    writer.setnchannels(reader.getnchannels())
                    writer.setsampwidth(reader.getsampwidth())
                    writer.setframerate(reader.getframerate())
                writer.writeframes(reader.readframes(reader.getnframes()))
    except (wave.Error, EOFError):
        # Mismatched formats: fall back to the first piece rather than produce
        # a file that will not play.
        logger.warning("Could not join WAV pieces; returning the first only")
        return pieces[0]
    finally:
        if writer is not None:
            writer.close()
    return output.getvalue()
