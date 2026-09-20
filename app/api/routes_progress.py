"""The dashboard, progress, and server-side speech recognition."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from ..domain.curriculum import recommend_next
from ..domain.mastery import overall_progress, summarise
from ..domain.models import Mastery, User
from ..services.providers.base import ProviderUnavailable
from .deps import Container, current_user, get_container
from .schemas import (
    serialise_assessment,
    serialise_recommendation,
    serialise_space,
)

router = APIRouter(prefix="/api", tags=["progress"])

#: Uploaded answer audio is short by nature. Anything larger is not an answer.
MAX_AUDIO_BYTES = 10 * 1024 * 1024


@router.get("/dashboard")
def dashboard(
    user: User = Depends(current_user),
    container: Container = Depends(get_container),
) -> dict:
    """A calm overview: what to continue, what to revisit, where things stand.

    Deliberately absent: streaks, daily targets, time spent, comparisons.
    """
    repos = container.repos
    spaces = repos.spaces.list_for_user(user.id)

    space_payloads = []
    review_items: list[dict] = []
    all_records = []

    for space in spaces:
        concepts = repos.concepts.list_for_space(space.id)
        mastery = repos.mastery.for_space(user.id, space.id)
        records = [mastery[c.id] for c in concepts if c.id in mastery]
        all_records.extend(records)
        lessons = {l.concept_id: l for l in repos.lessons.list_for_space(space.id)}

        space_payloads.append(
            serialise_space(
                space,
                resource_count=len(repos.resources.list_for_space(space.id)),
                lesson_count=len(lessons),
                concept_count=len(concepts),
                progress=overall_progress(records) if concepts else 0.0,
                needs_review=sum(1 for r in records if r.needs_review),
            )
        )

        for concept in concepts:
            record = mastery.get(concept.id)
            if record and record.state is Mastery.NEEDS_REVIEW and concept.id in lessons:
                review_items.append({
                    "conceptId": concept.id,
                    "conceptName": concept.name,
                    "spaceId": space.id,
                    "spaceTitle": space.title,
                    "lessonId": lessons[concept.id].id,
                    "note": record.note,
                })

    # Continue where you left off.
    continue_card = None
    active = repos.sessions.most_recent(user.id, unfinished_only=True)
    if active:
        lesson = repos.lessons.get(active.lesson_id)
        space = repos.spaces.get(active.space_id, user.id)
        if lesson and space:
            sections = repos.lessons.sections(lesson.id)
            total = max(1, len(sections))
            continue_card = {
                "lessonId": lesson.id,
                "title": lesson.title,
                "subtitle": lesson.subtitle,
                "spaceId": space.id,
                "spaceTitle": space.title,
                "accent": space.accent,
                "sessionId": active.id,
                "sectionOrdinal": active.section_ordinal,
                "positionSeconds": active.position_seconds,
                "progress": round(min(1.0, active.section_ordinal / total), 3),
                "estimatedMinutes": lesson.estimated_minutes,
            }

    # What to do next, from the most recently touched subject.
    recommendation = None
    if spaces:
        target = next((s for s in spaces if continue_card and s.id == continue_card["spaceId"]), spaces[0])
        plan = container.curriculum.plan_for(target.id, user.id)
        found = recommend_next(
            plan, in_progress_lesson_id=continue_card["lessonId"] if continue_card else ""
        )
        recommendation = serialise_recommendation(found)
        if recommendation:
            recommendation["spaceId"] = target.id
            recommendation["spaceTitle"] = target.title

    recent = []
    for session in repos.sessions.recent(user.id, limit=6):
        lesson = repos.lessons.get(session.lesson_id)
        if not lesson:
            continue
        record = repos.mastery.get(user.id, lesson.concept_id)
        recent.append({
            "lessonId": lesson.id,
            "title": lesson.title,
            "spaceId": session.space_id,
            "completed": session.completed,
            "mastery": (record.state if record else Mastery.NOT_STARTED).value,
            "masteryLabel": (record.state if record else Mastery.NOT_STARTED).label,
            "updatedAt": session.updated_at.isoformat(),
        })

    return {
        "continue": continue_card,
        "recommendation": recommendation,
        "spaces": space_payloads,
        "needsReview": review_items[:6],
        "recent": recent,
        "summary": {
            "counts": summarise(all_records),
            "progress": round(overall_progress(all_records), 3),
            "conceptsTouched": len(all_records),
        },
    }


@router.get("/progress")
def progress(
    user: User = Depends(current_user),
    container: Container = Depends(get_container),
) -> dict:
    """Progress per subject and per concept, described rather than scored."""
    repos = container.repos
    spaces = repos.spaces.list_for_user(user.id)
    payload = []

    for space in spaces:
        concepts = repos.concepts.list_for_space(space.id)
        mastery = repos.mastery.for_space(user.id, space.id)
        lessons = {l.concept_id: l for l in repos.lessons.list_for_space(space.id)}
        records = [mastery[c.id] for c in concepts if c.id in mastery]

        payload.append({
            "spaceId": space.id,
            "title": space.title,
            "accent": space.accent,
            "progress": round(overall_progress(records), 3) if concepts else 0.0,
            "counts": summarise(records),
            "concepts": [
                {
                    "id": concept.id,
                    "name": concept.name,
                    "stage": concept.stage.value,
                    "stageLabel": concept.stage.label,
                    "lessonId": lessons[concept.id].id if concept.id in lessons else "",
                    "mastery": (mastery[concept.id].state if concept.id in mastery else Mastery.NOT_STARTED).value,
                    "masteryLabel": (mastery[concept.id].state if concept.id in mastery else Mastery.NOT_STARTED).label,
                    "evidence": mastery[concept.id].evidence_count if concept.id in mastery else 0,
                }
                for concept in concepts
            ],
        })

    assessments = repos.assessments.recent(user.id, limit=10)
    return {
        "spaces": payload,
        "assessments": [serialise_assessment(a) for a in assessments],
    }


@router.post("/transcribe")
async def transcribe(
    audio: UploadFile = File(...),
    user: User = Depends(current_user),
    container: Container = Depends(get_container),
) -> dict:
    """Server-side speech recognition, for browsers that cannot listen.

    Chrome and Edge recognise speech in the browser, which is faster and keeps
    the audio on the device. This is the fallback for everyone else.
    """
    if not container.stt.available:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "This browser cannot listen, and speech recognition is not set up on "
                "the server. You can type your answer instead."
            ),
        )

    data = await audio.read(MAX_AUDIO_BYTES + 1)
    await audio.close()
    if len(data) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="That recording is too long.")
    if not data:
        raise HTTPException(status_code=400, detail="No audio was received.")

    try:
        result = container.stt.transcribe(data, mime_type=audio.content_type or "")
    except ProviderUnavailable as error:
        raise HTTPException(status_code=422, detail=str(error)) from None

    return {
        "text": result.text,
        "confidence": result.confidence,
        "provider": result.provider,
    }
