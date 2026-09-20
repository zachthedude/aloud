"""Domain entities.

These are plain dataclasses with no framework dependency, so every rule in
`app/domain/` can be unit-tested without a database, an HTTP client or an AI
provider. Persistence lives in `app/storage/`, serialisation at the API edge.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


def new_id(prefix: str) -> str:
    """Short, readable, collision-resistant identifier."""
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class ResourceKind(str, Enum):
    PDF = "pdf"
    DOCX = "docx"
    PPTX = "pptx"
    TEXT = "text"
    MARKDOWN = "markdown"
    URL = "url"
    NOTE = "note"
    VIDEO = "video"    # a recorded lecture, transcribed before teaching
    AUDIO = "audio"


class ResourceRole(str, Enum):
    """What a document is for, which decides which modes may read it.

    Notes is the default and the safe one. The other three are only claimed on
    positive evidence: the examiner must never mark against a "mark scheme"
    that is really a set of revision notes.
    """

    NOTES = "notes"
    PAST_PAPER = "past_paper"
    MARK_SCHEME = "mark_scheme"
    SYLLABUS = "syllabus"


class ProcessingStatus(str, Enum):
    """Lifecycle of a resource moving through the ingestion pipeline."""

    QUEUED = "queued"
    EXTRACTING = "extracting"
    STRUCTURING = "structuring"
    MODELLING = "modelling"
    READY = "ready"
    FAILED = "failed"


class SpaceStatus(str, Enum):
    EMPTY = "empty"            # created, no resources yet
    ANALYSING = "analysing"    # pipeline running
    READY = "ready"            # plan built, lessons available
    FAILED = "failed"


class Mastery(str, Enum):
    """Where a student stands on a concept.

    Deliberately not a grade. The ordering drives progression decisions and is
    never displayed to the student as a score out of four.
    """

    NOT_STARTED = "not_started"
    NEEDS_REVIEW = "needs_review"
    DEVELOPING = "developing"
    UNDERSTOOD = "understood"
    MASTERED = "mastered"

    @property
    def rank(self) -> int:
        return _MASTERY_RANK[self]

    @property
    def label(self) -> str:
        return _MASTERY_LABEL[self]


_MASTERY_RANK: dict[Mastery, int] = {
    Mastery.NOT_STARTED: 0,
    Mastery.NEEDS_REVIEW: 1,
    Mastery.DEVELOPING: 2,
    Mastery.UNDERSTOOD: 3,
    Mastery.MASTERED: 4,
}

# Student-facing wording. Descriptive, never judgmental.
_MASTERY_LABEL: dict[Mastery, str] = {
    Mastery.NOT_STARTED: "Not started",
    Mastery.NEEDS_REVIEW: "Worth revisiting",
    Mastery.DEVELOPING: "Building this",
    Mastery.UNDERSTOOD: "Understood",
    Mastery.MASTERED: "Solid",
}


class Judgement(str, Enum):
    """How the teacher read a spoken answer."""

    CORRECT = "correct"
    MOSTLY_CORRECT = "mostly_correct"
    INCOMPLETE = "incomplete"
    MISCONCEPTION = "misconception"
    INCORRECT = "incorrect"
    UNCLEAR = "unclear"


class QuestionKind(str, Enum):
    RECALL = "recall"
    EXPLANATION = "explanation"
    APPLICATION = "application"
    COMPARISON = "comparison"
    REASONING = "reasoning"
    SCENARIO = "scenario"
    MISCONCEPTION_PROBE = "misconception_probe"
    SYNTHESIS = "synthesis"
    TEACH_BACK = "teach_back"


class SectionKind(str, Enum):
    """The pedagogical role a lesson section plays."""

    CONTEXT = "context"
    EXPLANATION = "explanation"
    EXAMPLE = "example"
    ANALOGY = "analogy"
    CHECK = "check"              # a spoken question to the student
    DEEPER = "deeper"
    APPLICATION = "application"
    RECAP = "recap"
    ASSESSMENT = "assessment"
    NEXT_STEP = "next_step"


class PlanStage(str, Enum):
    FOUNDATIONS = "foundations"
    CORE = "core"
    APPLICATION = "application"
    HIGHER_ORDER = "higher_order"
    RECALL = "recall"
    ASSESSMENT = "assessment"
    REVISION = "revision"

    @property
    def label(self) -> str:
        return _STAGE_LABEL[self]


_STAGE_LABEL: dict[PlanStage, str] = {
    PlanStage.FOUNDATIONS: "Foundations",
    PlanStage.CORE: "Core ideas",
    PlanStage.APPLICATION: "Putting it to work",
    PlanStage.HIGHER_ORDER: "Going deeper",
    PlanStage.RECALL: "Bringing it back",
    PlanStage.ASSESSMENT: "Checking understanding",
    PlanStage.REVISION: "Revisiting",
}


class LessonState(str, Enum):
    """Explicit conversational state machine for a live lesson.

    Transitions are validated in `app/domain/lesson_state.py` so voice
    interaction never degrades into a pile of disconnected callbacks.
    """

    IDLE = "idle"
    PLAYING = "playing"
    PAUSED = "paused"
    AI_SPEAKING = "ai_speaking"
    WAITING_FOR_STUDENT = "waiting_for_student"
    LISTENING = "listening"
    TRANSCRIBING = "transcribing"
    EVALUATING = "evaluating"
    THINKING = "thinking"
    EXPLAINING = "explaining"
    REVIEWING = "reviewing"
    COMPLETED = "completed"
    ERROR = "error"


class AudioStatus(str, Enum):
    ABSENT = "absent"            # never requested
    PREPARING = "preparing"
    READY = "ready"
    UNAVAILABLE = "unavailable"  # no TTS provider configured
    FAILED = "failed"


# ---------------------------------------------------------------------------
# Entities
# ---------------------------------------------------------------------------


@dataclass
class User:
    id: str = field(default_factory=lambda: new_id("usr"))
    email: str = ""
    display_name: str = ""
    password_hash: str = ""
    created_at: datetime = field(default_factory=utcnow)
    # Learned preferences, see app/domain/personalisation.py
    preferred_speed: float = 1.0
    preferred_voice: str = ""
    explanation_style: str = "balanced"  # balanced | concrete | formal
    onboarded: bool = False


@dataclass
class LearningSpace:
    """One subject: its resources, concepts, plan, lessons and progress."""

    id: str = field(default_factory=lambda: new_id("spc"))
    user_id: str = ""
    title: str = ""
    description: str = ""
    level: str = ""              # e.g. "Grade 10", "IB HL", free text
    status: SpaceStatus = SpaceStatus.EMPTY
    accent: int = 0              # index into the palette, for visual identity
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)


@dataclass
class Resource:
    id: str = field(default_factory=lambda: new_id("res"))
    space_id: str = ""
    title: str = ""
    kind: ResourceKind = ResourceKind.TEXT
    role: ResourceRole = ResourceRole.NOTES
    original_filename: str = ""
    stored_path: str = ""
    source_url: str = ""
    byte_size: int = 0
    page_count: int = 0
    section_count: int = 0
    word_count: int = 0
    status: ProcessingStatus = ProcessingStatus.QUEUED
    error: str = ""
    created_at: datetime = field(default_factory=utcnow)

    @property
    def analysed(self) -> bool:
        return self.status is ProcessingStatus.READY


@dataclass
class ResourceSection:
    """A cleaned, chunked passage of a resource. The retrieval unit."""

    id: str = field(default_factory=lambda: new_id("sec"))
    resource_id: str = ""
    space_id: str = ""
    ordinal: int = 0
    heading: str = ""
    text: str = ""
    page: int = 0
    word_count: int = 0


@dataclass
class Concept:
    """A single idea the student needs to learn."""

    id: str = field(default_factory=lambda: new_id("cpt"))
    space_id: str = ""
    name: str = ""
    summary: str = ""
    definition: str = ""
    stage: PlanStage = PlanStage.CORE
    difficulty: int = 2                       # 1 (gentle) .. 4 (demanding)
    ordinal: int = 0
    terminology: list[str] = field(default_factory=list)
    formulas: list[str] = field(default_factory=list)
    facts: list[str] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)
    misconceptions: list[str] = field(default_factory=list)
    prerequisites: list[str] = field(default_factory=list)  # concept ids
    section_ids: list[str] = field(default_factory=list)    # grounding sources


@dataclass
class Lesson:
    id: str = field(default_factory=lambda: new_id("lsn"))
    space_id: str = ""
    concept_id: str = ""
    title: str = ""
    subtitle: str = ""
    stage: PlanStage = PlanStage.CORE
    ordinal: int = 0
    estimated_minutes: int = 8
    created_at: datetime = field(default_factory=utcnow)


@dataclass
class LessonSection:
    """One narrated beat of a lesson. Audio is generated per section."""

    id: str = field(default_factory=lambda: new_id("lsc"))
    lesson_id: str = ""
    ordinal: int = 0
    kind: SectionKind = SectionKind.EXPLANATION
    heading: str = ""
    script: str = ""                      # what the teacher says aloud
    question_id: str = ""                 # set when kind is CHECK
    source_section_ids: list[str] = field(default_factory=list)
    estimated_seconds: int = 0


@dataclass
class AudioAsset:
    """A rendered audio file for one lesson section."""

    id: str = field(default_factory=lambda: new_id("aud"))
    lesson_section_id: str = ""
    lesson_id: str = ""
    voice: str = ""
    status: AudioStatus = AudioStatus.ABSENT
    file_path: str = ""
    mime_type: str = "audio/mpeg"
    byte_size: int = 0
    duration_seconds: float = 0.0
    error: str = ""
    created_at: datetime = field(default_factory=utcnow)


@dataclass
class Question:
    id: str = field(default_factory=lambda: new_id("qst"))
    space_id: str = ""
    concept_id: str = ""
    lesson_id: str = ""
    kind: QuestionKind = QuestionKind.EXPLANATION
    prompt: str = ""                          # spoken aloud
    expected_points: list[str] = field(default_factory=list)
    misconception_markers: list[str] = field(default_factory=list)
    follow_up: str = ""
    difficulty: int = 2


@dataclass
class StudentResponse:
    id: str = field(default_factory=lambda: new_id("rsp"))
    question_id: str = ""
    session_id: str = ""
    user_id: str = ""
    concept_id: str = ""
    transcript: str = ""
    input_mode: str = "voice"                 # voice | text
    judgement: Judgement = Judgement.UNCLEAR
    confidence: float = 0.0                   # how sure the evaluator is
    matched_points: list[str] = field(default_factory=list)
    missed_points: list[str] = field(default_factory=list)
    detected_misconceptions: list[str] = field(default_factory=list)
    teacher_reply: str = ""
    created_at: datetime = field(default_factory=utcnow)


@dataclass
class MasteryRecord:
    """Rolling view of how well a student holds one concept."""

    id: str = field(default_factory=lambda: new_id("mst"))
    user_id: str = ""
    space_id: str = ""
    concept_id: str = ""
    state: Mastery = Mastery.NOT_STARTED
    evidence_count: int = 0
    correct_streak: int = 0
    struggle_count: int = 0
    last_seen_at: datetime | None = None
    note: str = ""                            # e.g. "mixed up mass and weight"

    @property
    def needs_review(self) -> bool:
        return self.state in (Mastery.NEEDS_REVIEW, Mastery.DEVELOPING)


@dataclass
class LearningSession:
    """One sitting with one lesson. Holds resume position and live state."""

    id: str = field(default_factory=lambda: new_id("ses"))
    user_id: str = ""
    space_id: str = ""
    lesson_id: str = ""
    state: LessonState = LessonState.IDLE
    section_ordinal: int = 0
    position_seconds: float = 0.0
    playback_speed: float = 1.0
    completed: bool = False
    started_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)
    pending_question_id: str = ""
    # Adaptive bookkeeping for the current sitting
    reexplain_count: int = 0
    extension_offered: bool = False


@dataclass
class TeacherMessage:
    """A turn in the conversation, whether narrated, asked or answered."""

    id: str = field(default_factory=lambda: new_id("msg"))
    session_id: str = ""
    space_id: str = ""
    user_id: str = ""
    role: str = "teacher"                     # teacher | student
    text: str = ""
    concept_id: str = ""
    grounded_in: list[str] = field(default_factory=list)  # resource section ids
    created_at: datetime = field(default_factory=utcnow)


@dataclass
class Bookmark:
    id: str = field(default_factory=lambda: new_id("bkm"))
    user_id: str = ""
    space_id: str = ""
    lesson_id: str = ""
    section_ordinal: int = 0
    position_seconds: float = 0.0
    label: str = ""
    created_at: datetime = field(default_factory=utcnow)


@dataclass
class StudyPlanItem:
    """One step of the plan. Ordering is derived, never hand-maintained."""

    lesson_id: str
    concept_id: str
    title: str
    stage: PlanStage
    ordinal: int
    estimated_minutes: int
    mastery: Mastery
    prerequisites_met: bool
    recommendation: str = ""                  # "Suggested next", "Ready to revisit"


@dataclass
class Assessment:
    """Result of a comprehension check at the end of a teaching section."""

    id: str = field(default_factory=lambda: new_id("asm"))
    user_id: str = ""
    space_id: str = ""
    lesson_id: str = ""
    concept_id: str = ""
    session_id: str = ""
    response_ids: list[str] = field(default_factory=list)
    outcome: Mastery = Mastery.DEVELOPING
    confidence: float = 0.0
    takeaways: list[str] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    recommendation: str = ""
    next_action: str = "continue"             # continue | review | revisit_prereq | extend
    created_at: datetime = field(default_factory=utcnow)


@dataclass
class Job:
    """A long-running background task the UI can watch."""

    id: str = field(default_factory=lambda: new_id("job"))
    space_id: str = ""
    user_id: str = ""
    kind: str = ""                            # analyse | audio
    status: str = "queued"                    # queued | running | done | failed
    step: str = ""                            # student-facing wording
    progress: float = 0.0                     # 0..1
    detail: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)
