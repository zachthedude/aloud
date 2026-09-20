"""Video and audio as study material.

A recorded lecture is the most common thing a student has and cannot study
from on the move: an hour of video they would have to sit and watch. This
turns it into the same passages every other format becomes, so it joins the
same plan and gets taught back in the same voice.

Transcription is Whisper running locally, which decodes the container through
PyAV. That means no separate demux step and no ffmpeg on PATH: an .mp4, .mov,
.webm, .mkv, .mp3 or .m4a all go in as they are.

A transcript has no headings, so the structure has to come from somewhere
else. Whisper returns timestamped segments, and a long gap between them is
where a speaker changed topic, took a question, or moved to the next slide.
Those gaps become the passage boundaries.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ...domain.models import ResourceKind
from .base import Block, ExtractedDocument, UnreadableDocument

logger = logging.getLogger("aloud.media")

#: Containers PyAV can decode. Anything ffmpeg reads, really, but these are the
#: ones a student actually has.
VIDEO_EXTENSIONS = (".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v")
AUDIO_EXTENSIONS = (".mp3", ".m4a", ".wav", ".ogg", ".oga", ".opus", ".flac", ".aac")
MEDIA_EXTENSIONS = VIDEO_EXTENSIONS + AUDIO_EXTENSIONS

#: A pause longer than this reads as a change of topic rather than a breath.
TOPIC_GAP_SECONDS = 2.6

#: Passages are capped so one unbroken monologue still chunks sensibly.
MAX_SEGMENT_WORDS = 220


class MediaProcessor:
    """Turns a recording into passages, using a speech-to-text provider."""

    kind = ResourceKind.TEXT
    extensions = MEDIA_EXTENSIONS

    def __init__(self, stt=None) -> None:
        self._stt = stt

    def can_handle(self, path: Path, mime_type: str = "") -> bool:
        if path.suffix.lower() in self.extensions:
            return True
        return mime_type.startswith(("video/", "audio/"))

    @property
    def available(self) -> bool:
        return bool(self._stt and getattr(self._stt, "available", False))

    def extract(self, path: Path) -> ExtractedDocument:
        if not self.available:
            raise UnreadableDocument(
                "Listening to recordings is not set up on this server, so video and "
                "audio cannot be turned into lessons yet. Text documents still work."
            )

        segments = self._transcribe(path)
        if not segments:
            raise UnreadableDocument(
                "No speech could be made out in that recording. If it is music or "
                "mostly silence, there is nothing to teach from."
            )

        blocks = [Block(text) for text in group_segments(segments)]
        document = ExtractedDocument(
            title=path.stem.replace("_", " ").replace("-", " ").strip(),
            blocks=blocks,
            page_count=0,
            kind=ResourceKind.TEXT,
        )
        if document.is_empty:
            raise UnreadableDocument(
                "Only a few words could be made out in that recording."
            )
        return document

    def _transcribe(self, path: Path) -> list[tuple[float, float, str]]:
        """(start, end, text) per segment, straight from the provider."""
        transcribe_file = getattr(self._stt, "transcribe_file", None)
        if transcribe_file is None:
            raise UnreadableDocument(
                "This speech provider cannot read recordings from a file."
            )
        try:
            return transcribe_file(path)
        except Exception as error:  # noqa: BLE001 - decoding raises broadly
            logger.warning("Transcription failed for %s: %s", path.name, error)
            # A setup failure and a bad file are different problems, and telling
            # a student their recording is damaged when the server cannot load
            # its speech model sends them off to re-export a perfectly good file.
            if _looks_like_setup_failure(error):
                raise UnreadableDocument(
                    "The speech model could not be loaded on this server, so recordings "
                    "cannot be transcribed right now. Your file is fine. Text documents "
                    "still work."
                ) from None
            raise UnreadableDocument(
                "That recording could not be read. It may be an unsupported format "
                "or the file may be damaged."
            ) from None


#: Words that mark a failure as the server's problem rather than the file's.
_SETUP_MARKERS = (
    "no module named", "huggingface", "model.bin", "snapshot", "download",
    "connection", "could not resolve", "repository not found", "importerror",
)


def _looks_like_setup_failure(error: Exception) -> bool:
    text = f"{type(error).__name__} {error}".lower()
    return any(marker in text for marker in _SETUP_MARKERS)


def group_segments(segments: list[tuple[float, float, str]]) -> list[str]:
    """Join segments into passages, breaking where the speaker paused.

    The alternative, one giant block of text, gives the analyser nothing to
    work with: every concept would be extracted from the same passage and the
    lesson could not point at where in the recording an idea came from.
    """
    passages: list[str] = []
    current: list[str] = []
    words = 0
    previous_end = None

    for start, end, text in segments:
        text = " ".join(text.split())
        if not text:
            continue

        gap = start - previous_end if previous_end is not None else 0.0
        too_long = words + len(text.split()) > MAX_SEGMENT_WORDS

        if current and (gap >= TOPIC_GAP_SECONDS or too_long):
            passages.append(_tidy(" ".join(current)))
            current, words = [], 0

        current.append(text)
        words += len(text.split())
        previous_end = end

    if current:
        passages.append(_tidy(" ".join(current)))
    return [passage for passage in passages if len(passage.split()) >= 8]


def _tidy(text: str) -> str:
    """Light cleanup of spoken text.

    Deliberately light. Whisper already punctuates, and rewriting a lecturer's
    words would break the promise that everything taught is traceable to what
    the student actually supplied.
    """
    cleaned = " ".join(text.split())
    for filler in (" um, ", " uh, ", " erm, "):
        cleaned = cleaned.replace(filler, " ")
    if cleaned and cleaned[0].islower():
        cleaned = cleaned[0].upper() + cleaned[1:]
    return cleaned
