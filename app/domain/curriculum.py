"""Turning a set of concepts into an order worth learning them in.

The plan is derived, never stored as a hand-maintained list. Every time the
student's understanding changes, the same functions run again and the plan
reflects the new picture. That is what keeps it a curriculum rather than a
checklist.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import Concept, Lesson, Mastery, MasteryRecord, PlanStage, StudyPlanItem

# Stages in teaching order. Concepts sort by stage first, then by prerequisite
# depth, so foundations always precede the things built on them.
_STAGE_ORDER: dict[PlanStage, int] = {
    PlanStage.FOUNDATIONS: 0,
    PlanStage.CORE: 1,
    PlanStage.APPLICATION: 2,
    PlanStage.HIGHER_ORDER: 3,
    PlanStage.RECALL: 4,
    PlanStage.ASSESSMENT: 5,
    PlanStage.REVISION: 6,
}


class CircularPrerequisite(ValueError):
    """Raised when concepts depend on each other in a loop."""


def prerequisite_depth(concepts: list[Concept]) -> dict[str, int]:
    """How many prerequisite layers sit beneath each concept.

    Depth 0 means nothing must be learned first. Unknown prerequisite ids are
    ignored rather than fatal, because an analyser may reference a concept that
    did not survive deduplication.
    """
    by_id = {c.id: c for c in concepts}
    depth: dict[str, int] = {}
    visiting: set[str] = set()

    def resolve(concept_id: str) -> int:
        if concept_id in depth:
            return depth[concept_id]
        if concept_id in visiting:
            raise CircularPrerequisite(
                f"Concept {concept_id} is part of a prerequisite loop"
            )
        concept = by_id.get(concept_id)
        if concept is None:
            return 0
        visiting.add(concept_id)
        known = [p for p in concept.prerequisites if p in by_id]
        value = 1 + max((resolve(p) for p in known), default=-1)
        visiting.discard(concept_id)
        depth[concept_id] = value
        return value

    for concept in concepts:
        resolve(concept.id)
    return depth


def order_concepts(concepts: list[Concept]) -> list[Concept]:
    """Teaching order: stage, then prerequisite depth, then the author's order.

    Falls back to the declared order if prerequisites contain a loop, so a bad
    analysis degrades into a plain sequence instead of an error page.
    """
    try:
        depth = prerequisite_depth(concepts)
    except CircularPrerequisite:
        depth = {c.id: 0 for c in concepts}

    return sorted(
        concepts,
        key=lambda c: (_STAGE_ORDER[c.stage], depth.get(c.id, 0), c.ordinal, c.name),
    )


def prerequisites_met(
    concept: Concept, mastery: dict[str, MasteryRecord], *, threshold: Mastery = Mastery.DEVELOPING
) -> bool:
    """True when everything this concept rests on has been at least started."""
    for prerequisite_id in concept.prerequisites:
        record = mastery.get(prerequisite_id)
        if record is None or record.state.rank < threshold.rank:
            return False
    return True


def build_plan(
    concepts: list[Concept],
    lessons: list[Lesson],
    mastery: dict[str, MasteryRecord],
) -> list[StudyPlanItem]:
    """The full plan, in order, annotated with where the student stands."""
    lesson_by_concept = {lesson.concept_id: lesson for lesson in lessons}
    ordered = order_concepts(concepts)

    items: list[StudyPlanItem] = []
    suggested_taken = False

    for index, concept in enumerate(ordered):
        lesson = lesson_by_concept.get(concept.id)
        if lesson is None:
            continue
        record = mastery.get(concept.id)
        state = record.state if record else Mastery.NOT_STARTED
        ready = prerequisites_met(concept, mastery)

        recommendation = ""
        if state in (Mastery.NEEDS_REVIEW, Mastery.DEVELOPING):
            recommendation = "Ready to revisit"
        elif state is Mastery.NOT_STARTED and ready and not suggested_taken:
            recommendation = "Suggested next"
            suggested_taken = True
        elif state is Mastery.NOT_STARTED and not ready:
            recommendation = "Comes after the earlier ideas"
        elif state is Mastery.MASTERED:
            recommendation = "Solid"

        items.append(
            StudyPlanItem(
                lesson_id=lesson.id,
                concept_id=concept.id,
                title=lesson.title,
                stage=concept.stage,
                ordinal=index,
                estimated_minutes=lesson.estimated_minutes,
                mastery=state,
                prerequisites_met=ready,
                recommendation=recommendation,
            )
        )

    # Nothing new to start, but something to revisit: promote the first of those.
    if not suggested_taken:
        for item in items:
            if item.mastery in (Mastery.NEEDS_REVIEW, Mastery.DEVELOPING):
                item.recommendation = "Suggested next"
                break

    return items


@dataclass(frozen=True)
class Recommendation:
    """The single thing the dashboard should offer next."""

    kind: str            # start | continue | review | assess | done
    lesson_id: str
    concept_id: str
    title: str
    reason: str


def recommend_next(
    items: list[StudyPlanItem], *, in_progress_lesson_id: str = ""
) -> Recommendation | None:
    """Pick one next action, phrased without any sense of falling behind."""
    if not items:
        return None

    if in_progress_lesson_id:
        for item in items:
            if item.lesson_id == in_progress_lesson_id:
                return Recommendation(
                    "continue", item.lesson_id, item.concept_id, item.title,
                    "Pick up where you left off.",
                )

    for item in items:
        if item.mastery is Mastery.NEEDS_REVIEW:
            return Recommendation(
                "review", item.lesson_id, item.concept_id, item.title,
                "This one may benefit from another explanation.",
            )

    for item in items:
        if item.mastery is Mastery.NOT_STARTED and item.prerequisites_met:
            return Recommendation(
                "start", item.lesson_id, item.concept_id, item.title,
                "The next idea in your plan.",
            )

    for item in items:
        if item.mastery is Mastery.DEVELOPING:
            return Recommendation(
                "review", item.lesson_id, item.concept_id, item.title,
                "You are building this one. Another pass will settle it.",
            )

    last = items[-1]
    return Recommendation(
        "done", last.lesson_id, last.concept_id, last.title,
        "You have been through everything here. Revisit anything you want to keep sharp.",
    )


def knowledge_graph(
    concepts: list[Concept], mastery: dict[str, MasteryRecord]
) -> dict[str, object]:
    """Nodes and edges for the knowledge map, with layout depth precomputed."""
    try:
        depth = prerequisite_depth(concepts)
    except CircularPrerequisite:
        depth = {c.id: 0 for c in concepts}

    valid = {c.id for c in concepts}
    nodes = [
        {
            "id": c.id,
            "name": c.name,
            "stage": c.stage.value,
            "depth": depth.get(c.id, 0),
            "difficulty": c.difficulty,
            "mastery": (mastery[c.id].state if c.id in mastery else Mastery.NOT_STARTED).value,
        }
        for c in order_concepts(concepts)
    ]
    edges = [
        {"from": prerequisite_id, "to": c.id}
        for c in concepts
        for prerequisite_id in c.prerequisites
        if prerequisite_id in valid
    ]
    return {"nodes": nodes, "edges": edges}
