"""The one writer of the learner model.

Every answer, in every mode, ends up here. Nothing else in the codebase saves
a mastery row, which is what guarantees that two modes can never hold two
different opinions about the same student: there is one row, and one door
into it.

Each observation is folded into the row and saved in a single upsert, so an
answer never lands half-recorded, with its correctness counted and its
hesitation lost.
"""

from __future__ import annotations

from datetime import datetime

from ..domain import learner
from ..domain import mastery as mastery_rules
from ..domain.learner import Observation
from ..domain.models import Concept, Judgement, Mastery, MasteryRecord, utcnow


class LearnerService:
    def __init__(self, repos) -> None:
        self.repos = repos

    def current(self, user_id: str, space_id: str, concept_id: str) -> MasteryRecord:
        """What is known about this student and concept, before anything new."""
        return self.repos.mastery.get_or_create(user_id, space_id, concept_id)

    def observe_answer(
        self,
        before: MasteryRecord,
        *,
        judgement: Judgement,
        confidence: float,
        transcript: str,
        latency_ms: int | None,
        note: str = "",
        now: datetime | None = None,
    ) -> MasteryRecord:
        """Fold one evaluated answer into the model and save it, in one write.

        `confidence` is how sure the evaluator was of its own judgement, which
        decides whether the mastery state may move at all. It is not the
        learner's confidence; that is derived from the whole row.
        """
        now = now or utcnow()
        updated = mastery_rules.apply_evidence(before, judgement, confidence, now=now)
        updated = learner.record(
            updated,
            Observation(learner.outcome_for(judgement), latency_ms=latency_ms, transcript=transcript),
            now=now,
        )
        if note:
            updated.note = note[:200]
        return self.repos.mastery.save(updated)

    def observe_exposure(
        self, user_id: str, space_id: str, concept_id: str, *, now: datetime | None = None
    ) -> MasteryRecord:
        """They listened all the way through without being asked anything.

        Real exposure, so it moves a concept out of "not started", but it is
        not evidence of knowing it and does not touch any learner signal.
        """
        record = self.current(user_id, space_id, concept_id)
        if record.state.rank < Mastery.DEVELOPING.rank:
            record.state = Mastery.DEVELOPING
            record.last_seen_at = now or utcnow()
            record = self.repos.mastery.save(record)
        return record

    def concepts(self, user_id: str, space_id: str) -> list[tuple[Concept, MasteryRecord]]:
        """Every concept in a subject with what is known about it.

        Concepts never touched come back with an empty record rather than
        being left out, so a mode ranking topics sees the untouched ones too.
        """
        records = self.repos.mastery.for_space(user_id, space_id)
        return [
            (concept, records.get(concept.id)
             or MasteryRecord(user_id=user_id, space_id=space_id, concept_id=concept.id))
            for concept in self.repos.concepts.list_for_space(space_id)
        ]

    def forget(self, user_id: str) -> dict[str, int]:
        """Delete everything learned about this student. One call, one transaction."""
        return self.repos.mastery.forget_user(user_id)
