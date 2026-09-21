"""Request validation and response shaping.

Pydantic models validate and constrain what comes in. The `serialise_*`
functions shape what goes out, in camelCase for the browser, deliberately
hand-written rather than generated: the client should receive what a screen
needs, not a transcript of the database. Password hashes, stored file paths and
provider internals never appear in a response.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, EmailStr, Field, field_validator

from ..domain.curriculum import Recommendation
from ..domain.mastery import readiness_note
from ..domain.models import (
    Assessment,
    Bookmark,
    Concept,
    LearningSession,
    LearningSpace,
    Lesson,
    LessonSection,
    Mastery,
    MasteryRecord,
    Question,
    Resource,
    ResourceRole,
    StudyPlanItem,
    TeacherMessage,
    User,
)

# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------


class SignUpRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=200)
    display_name: str = Field(default="", max_length=80, alias="displayName")

    model_config = {"populate_by_name": True}

    @field_validator("password")
    @classmethod
    def not_trivial(cls, value: str) -> str:
        if value.strip() != value:
            raise ValueError("Your password cannot start or end with a space.")
        if value.lower() in {"password", "12345678", "qwertyui", "aloud123"}:
            raise ValueError("Please choose something harder to guess.")
        return value


class SignInRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


class PreferencesRequest(BaseModel):
    display_name: str | None = Field(default=None, max_length=80, alias="displayName")
    preferred_speed: float | None = Field(default=None, ge=0.5, le=3.0, alias="preferredSpeed")
    preferred_voice: str | None = Field(default=None, max_length=120, alias="preferredVoice")
    explanation_style: str | None = Field(default=None, alias="explanationStyle")
    onboarded: bool | None = None
    muted_modes: list[str] | None = Field(default=None, max_length=32, alias="mutedModes")

    model_config = {"populate_by_name": True}

    @field_validator("explanation_style")
    @classmethod
    def known_style(cls, value: str | None) -> str | None:
        if value is not None and value not in ("balanced", "concrete", "formal"):
            raise ValueError("Unknown explanation style.")
        return value


class SpaceRequest(BaseModel):
    title: str = Field(min_length=2, max_length=80)
    level: str = Field(default="", max_length=60)
    description: str = Field(default="", max_length=400)

    @field_validator("title", "level", "description")
    @classmethod
    def tidy(cls, value: str) -> str:
        return " ".join(value.split())


class UrlResourceRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2000)


class NoteResourceRequest(BaseModel):
    title: str = Field(default="", max_length=120)
    text: str = Field(min_length=40, max_length=200_000)


class ResourceRoleRequest(BaseModel):
    """Correcting what a document is. Pydantic rejects anything not a role."""

    role: ResourceRole


class PlaybackRequest(BaseModel):
    state: str | None = None
    section_ordinal: int | None = Field(default=None, ge=0, le=200, alias="sectionOrdinal")
    position_seconds: float | None = Field(default=None, ge=0, le=100_000, alias="positionSeconds")
    playback_speed: float | None = Field(default=None, ge=0.5, le=3.0, alias="playbackSpeed")

    model_config = {"populate_by_name": True}


class AnswerRequest(BaseModel):
    question_id: str = Field(min_length=1, max_length=64, alias="questionId")
    transcript: str = Field(default="", max_length=8000)
    input_mode: str = Field(default="voice", alias="inputMode")
    # How long the student took to start answering, measured by the client
    # from the moment the floor was handed over. Capped at ten minutes: a
    # longer pause is someone who walked away, not someone thinking.
    latency_ms: int | None = Field(default=None, ge=0, le=600_000, alias="latencyMs")

    model_config = {"populate_by_name": True}

    @field_validator("input_mode")
    @classmethod
    def known_mode(cls, value: str) -> str:
        return value if value in ("voice", "text") else "text"


class AskRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    session_id: str = Field(default="", max_length=64, alias="sessionId")
    concept_id: str = Field(default="", max_length=64, alias="conceptId")
    # The client knows a question is on the floor before the server does: its
    # save of "waiting for the student" is asynchronous, and a quick answer can
    # overtake it. Without this, that answer is chatted to instead of marked.
    awaiting_answer: bool = Field(default=False, alias="awaitingAnswer")

    model_config = {"populate_by_name": True}


class BookmarkRequest(BaseModel):
    lesson_id: str = Field(min_length=1, max_length=64, alias="lessonId")
    section_ordinal: int = Field(default=0, ge=0, le=200, alias="sectionOrdinal")
    position_seconds: float = Field(default=0, ge=0, le=100_000, alias="positionSeconds")
    label: str = Field(default="", max_length=120)

    model_config = {"populate_by_name": True}


class AudioRequest(BaseModel):
    voice: str = Field(default="", max_length=120)


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------


def serialise_user(user: User) -> dict[str, Any]:
    return {
        "id": user.id,
        "email": user.email,
        "displayName": user.display_name or user.email.split("@")[0],
        "preferredSpeed": user.preferred_speed,
        "preferredVoice": user.preferred_voice,
        "explanationStyle": user.explanation_style,
        "onboarded": user.onboarded,
        "mutedModes": user.muted_modes,
        "createdAt": user.created_at.isoformat(),
    }


def serialise_space(
    space: LearningSpace,
    *,
    resource_count: int = 0,
    lesson_count: int = 0,
    concept_count: int = 0,
    progress: float = 0.0,
    needs_review: int = 0,
) -> dict[str, Any]:
    return {
        "id": space.id,
        "title": space.title,
        "description": space.description,
        "level": space.level,
        "status": space.status.value,
        "accent": space.accent,
        "resourceCount": resource_count,
        "lessonCount": lesson_count,
        "conceptCount": concept_count,
        "progress": round(progress, 3),
        "needsReview": needs_review,
        "createdAt": space.created_at.isoformat(),
        "updatedAt": space.updated_at.isoformat(),
    }


def serialise_resource(resource: Resource) -> dict[str, Any]:
    return {
        "id": resource.id,
        "title": resource.title,
        "kind": resource.kind.value,
        "role": resource.role.value,
        "filename": resource.original_filename,
        "sourceUrl": resource.source_url,
        "byteSize": resource.byte_size,
        "pageCount": resource.page_count,
        "sectionCount": resource.section_count,
        "wordCount": resource.word_count,
        "status": resource.status.value,
        "analysed": resource.analysed,
        "error": resource.error,
        "createdAt": resource.created_at.isoformat(),
    }


def serialise_concept(concept: Concept, mastery: MasteryRecord | None = None) -> dict[str, Any]:
    state = mastery.state if mastery else Mastery.NOT_STARTED
    return {
        "id": concept.id,
        "name": concept.name,
        "summary": concept.summary,
        "definition": concept.definition,
        "stage": concept.stage.value,
        "stageLabel": concept.stage.label,
        "difficulty": concept.difficulty,
        "terminology": concept.terminology,
        "examWeight": round(concept.exam_weight, 4),
        "weightBasis": concept.weight_basis,
        "formulas": concept.formulas,
        "examples": concept.examples,
        "misconceptions": concept.misconceptions,
        "prerequisites": concept.prerequisites,
        "mastery": state.value,
        "masteryLabel": state.label,
        "note": readiness_note(state),
    }


def serialise_lesson(lesson: Lesson, *, mastery: Mastery | None = None) -> dict[str, Any]:
    return {
        "id": lesson.id,
        "spaceId": lesson.space_id,
        "conceptId": lesson.concept_id,
        "title": lesson.title,
        "subtitle": lesson.subtitle,
        "stage": lesson.stage.value,
        "stageLabel": lesson.stage.label,
        "ordinal": lesson.ordinal,
        "estimatedMinutes": lesson.estimated_minutes,
        "mastery": (mastery or Mastery.NOT_STARTED).value,
        "masteryLabel": (mastery or Mastery.NOT_STARTED).label,
    }


def serialise_question(question: Question) -> dict[str, Any]:
    """Note what is absent: expected answers never reach the client.

    Sending them would let a curious student read the answer out of the network
    tab, and would make evaluation a client-side concern.
    """
    return {
        "id": question.id,
        "kind": question.kind.value,
        "prompt": question.prompt,
        "followUp": question.follow_up,
        "conceptId": question.concept_id,
    }


def serialise_lesson_section(
    section: LessonSection, question: Question | None = None, audio: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {
        "id": section.id,
        "ordinal": section.ordinal,
        "kind": section.kind.value,
        "heading": section.heading,
        "script": section.script,
        "estimatedSeconds": section.estimated_seconds,
        "sourceSectionIds": section.source_section_ids,
        "question": serialise_question(question) if question else None,
        "audio": audio,
    }


def serialise_session(session: LearningSession) -> dict[str, Any]:
    return {
        "id": session.id,
        "lessonId": session.lesson_id,
        "spaceId": session.space_id,
        "state": session.state.value,
        "sectionOrdinal": session.section_ordinal,
        "positionSeconds": session.position_seconds,
        "playbackSpeed": session.playback_speed,
        "completed": session.completed,
        "pendingQuestionId": session.pending_question_id,
        "updatedAt": session.updated_at.isoformat(),
    }


def serialise_plan_item(item: StudyPlanItem) -> dict[str, Any]:
    return {
        "lessonId": item.lesson_id,
        "conceptId": item.concept_id,
        "title": item.title,
        "stage": item.stage.value,
        "stageLabel": item.stage.label,
        "ordinal": item.ordinal,
        "estimatedMinutes": item.estimated_minutes,
        "mastery": item.mastery.value,
        "masteryLabel": item.mastery.label,
        "prerequisitesMet": item.prerequisites_met,
        "recommendation": item.recommendation,
    }


def serialise_recommendation(recommendation: Recommendation | None) -> dict[str, Any] | None:
    if recommendation is None:
        return None
    return {
        "kind": recommendation.kind,
        "lessonId": recommendation.lesson_id,
        "conceptId": recommendation.concept_id,
        "title": recommendation.title,
        "reason": recommendation.reason,
    }


def serialise_assessment(assessment: Assessment) -> dict[str, Any]:
    return {
        "id": assessment.id,
        "lessonId": assessment.lesson_id,
        "conceptId": assessment.concept_id,
        "outcome": assessment.outcome.value,
        "outcomeLabel": assessment.outcome.label,
        "confidence": round(assessment.confidence, 2),
        "takeaways": assessment.takeaways,
        "gaps": assessment.gaps,
        "recommendation": assessment.recommendation,
        "nextAction": assessment.next_action,
        "createdAt": assessment.created_at.isoformat(),
    }


def serialise_message(message: TeacherMessage) -> dict[str, Any]:
    return {
        "id": message.id,
        "role": message.role,
        "text": message.text,
        "conceptId": message.concept_id,
        "groundedIn": message.grounded_in,
        "createdAt": message.created_at.isoformat(),
    }


def serialise_bookmark(bookmark: Bookmark, lesson_title: str = "") -> dict[str, Any]:
    return {
        "id": bookmark.id,
        "lessonId": bookmark.lesson_id,
        "lessonTitle": lesson_title,
        "spaceId": bookmark.space_id,
        "sectionOrdinal": bookmark.section_ordinal,
        "positionSeconds": bookmark.position_seconds,
        "label": bookmark.label,
        "createdAt": bookmark.created_at.isoformat(),
    }
