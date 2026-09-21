"""What Aloud has learned about a student, and the way to make it forget.

Two endpoints. The read is what every mode and the heatmap will draw on: per
concept, the raw observations and the three numbers derived from them. The
delete is the privacy promise made concrete: one call, one transaction, and
everything inferred about the student is gone.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ..domain import learner
from ..domain.models import Concept, MasteryRecord, User, utcnow
from .deps import Container, current_user, get_container, owned_space

router = APIRouter(prefix="/api/learner", tags=["learner"])


def serialise_signals(concept: Concept, record: MasteryRecord, *, now) -> dict:
    ordered = sorted(record.latencies)
    return {
        "conceptId": concept.id,
        "name": concept.name,
        "examWeight": round(concept.exam_weight, 4),
        # Derived. What modes should actually read.
        "confidence": round(learner.confidence(record, now=now), 3),
        "hesitation": round(learner.hesitation(record), 3),
        "shaky": learner.is_shaky(record),
        "quarantined": record.quarantined,
        "catchRate": learner.catch_rate(record),
        # Observed. Kept so a student can see what the numbers came from.
        "answered": learner.answered(record),
        "correct": record.correct,
        "partial": record.partial,
        "wrong": record.wrong,
        "missed": record.missed,
        "medianLatencyMs": ordered[len(ordered) // 2] if ordered else None,
        "fillers": record.fillers,
        "corrections": record.corrections,
        "lastSeenAt": record.last_seen_at.isoformat() if record.last_seen_at else None,
    }


@router.get("/{space_id}")
def learner_model(
    space_id: str,
    user: User = Depends(current_user),
    container: Container = Depends(get_container),
) -> dict:
    """Every concept in a subject, with what is known about it."""
    owned_space(space_id, user, container)
    now = utcnow()
    return {
        "concepts": [
            serialise_signals(concept, record, now=now)
            for concept, record in container.learner.concepts(user.id, space_id)
        ]
    }


@router.delete("")
def forget_me(
    user: User = Depends(current_user),
    container: Container = Depends(get_container),
) -> dict:
    """Forget everything learned about this student.

    The learner model, every transcript, every reply and every assessment.
    The material they uploaded and the lessons built from it stay: those are
    theirs, not something we inferred about them.
    """
    removed = container.learner.forget(user.id)
    return {"forgotten": removed}
