"""Provider interfaces.

Everything the application needs from the outside world is declared here as a
Protocol. The rest of the codebase depends on these names and never on a vendor
SDK, so swapping speech or model providers is a change in one wiring function
(`app/services/providers/registry.py`) rather than a change across the app.

Two layers sit between the app and a model:

    LLMProvider     raw text generation. One method. Easy to implement.
    TeachingEngine  the pedagogical operations the product actually needs.

`OnDeviceEngine` implements `TeachingEngine` with no model at all, so the
product works with no API key. `ModelBackedEngine` implements the same
interface against an `LLMProvider` and falls back to the on-device engine when
a call fails, which means a network blip degrades the lesson rather than
breaking it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from ...domain.assessment import AnswerAnalysis
from ...domain.models import Concept, Question, ResourceSection, SectionKind


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class KnowledgeModel:
    """What the analyser understood about a set of resources.

    Not a summary. This is the structure teaching is built from.
    """

    concepts: list[Concept] = field(default_factory=list)
    subject_summary: str = ""
    suggested_title: str = ""


@dataclass
class DraftSection:
    """One narrated beat, before it becomes a persisted LessonSection."""

    kind: SectionKind
    heading: str
    script: str
    source_section_ids: list[str] = field(default_factory=list)
    question: Question | None = None


@dataclass
class LessonDraft:
    title: str
    subtitle: str
    sections: list[DraftSection] = field(default_factory=list)
    estimated_minutes: int = 8


@dataclass
class GroundedAnswer:
    """A reply to a student question, with what it was based on."""

    text: str
    source_section_ids: list[str] = field(default_factory=list)
    source_note: str = ""        # e.g. "From your Forces and Motion notes"
    used_outside_knowledge: bool = False


@dataclass
class TranscriptionResult:
    text: str
    confidence: float = 0.0
    provider: str = ""


@dataclass
class SpeechResult:
    audio: bytes
    mime_type: str
    duration_seconds: float = 0.0
    provider: str = ""


class ProviderUnavailable(RuntimeError):
    """Raised when a provider cannot serve a request.

    Callers are expected to degrade gracefully and tell the student something
    true, never to surface this text directly.
    """


# ---------------------------------------------------------------------------
# Interfaces
# ---------------------------------------------------------------------------


@runtime_checkable
class LLMProvider(Protocol):
    """Raw text generation. The only thing a new model vendor must implement."""

    name: str
    available: bool

    def complete(
        self,
        system: str,
        prompt: str,
        *,
        max_tokens: int = 2000,
        temperature: float = 0.4,
    ) -> str:
        """Return the model's text response, or raise ProviderUnavailable."""
        ...


@runtime_checkable
class TeachingEngine(Protocol):
    """The pedagogical operations the product is built from."""

    name: str
    #: Shown in settings so a student can see what is teaching them.
    display_name: str
    grounded_only: bool

    def analyse(
        self, sections: list[ResourceSection], *, subject_hint: str = ""
    ) -> KnowledgeModel:
        """Build a knowledge model from cleaned resource sections."""
        ...

    def write_lesson(
        self,
        concept: Concept,
        sections: list[ResourceSection],
        *,
        style: str = "balanced",
        prior_concepts: list[Concept] | None = None,
    ) -> LessonDraft:
        """Write a narrated lesson for one concept."""
        ...

    def write_questions(
        self, concept: Concept, sections: list[ResourceSection], *, count: int = 3
    ) -> list[Question]:
        """Write questions that can be answered out loud."""
        ...

    def evaluate_answer(
        self, transcript: str, question: Question, concept: Concept | None = None
    ) -> AnswerAnalysis:
        """Read a spoken answer."""
        ...

    def answer_question(
        self,
        question: str,
        *,
        concept: Concept | None,
        sections: list[ResourceSection],
        history: list[tuple[str, str]] | None = None,
    ) -> GroundedAnswer:
        """Answer a student question, grounded in their own material."""
        ...

    def reexplain(
        self,
        concept: Concept,
        sections: list[ResourceSection],
        *,
        mode: str = "simpler",
    ) -> GroundedAnswer:
        """Explain a concept again: simpler, differently, or with an example."""
        ...


@runtime_checkable
class TextToSpeechProvider(Protocol):
    """Renders narration to an audio file for download and offline listening."""

    name: str
    display_name: str
    available: bool
    mime_type: str

    def synthesise(self, text: str, *, voice: str = "") -> SpeechResult:
        ...

    def voices(self) -> list[dict[str, str]]:
        ...


@runtime_checkable
class SpeechToTextProvider(Protocol):
    """Server-side fallback for browsers without speech recognition."""

    name: str
    display_name: str
    available: bool

    def transcribe(self, audio: bytes, *, mime_type: str = "") -> TranscriptionResult:
        ...


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Vectorises text for retrieval over the student's own material."""

    name: str
    dimensions: int

    def embed(self, texts: list[str]) -> list[list[float]]:
        ...
