"""The contract every mode implements.

A mode is a strategy: given what is known about the student and what they
just said, decide what Aloud says next. It gets everything it needs through
`ModeContext`, including the functions that evaluate an answer and record it,
so a mode holds no repository, no engine and no audio. That keeps each mode
one small file, and every mode testable with plain fakes.

Adding a mode is one file implementing `Mode`, and one line in `registry.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Protocol

from ...domain.assessment import AnswerAnalysis
from ...domain.models import Concept, MasteryRecord, Question
from ...domain.turn import Reply, Turn


@dataclass
class ModeContext:
    user_id: str
    space_id: str
    #: The mode's own memory between turns. Persisted by the service after
    #: every turn, so a sitting survives a closed tab. JSON-serialisable only.
    cursor: dict
    #: The learner model, one entry per concept in the subject. Read-only: the
    #: only way to change it is `observe`.
    concepts: list[tuple[Concept, MasteryRecord]]
    questions_for: Callable[[str], list[Question]]
    evaluate: Callable[[str, Question, Concept | None], AnswerAnalysis]
    #: Record an evaluated answer in the learner model. Returns the new record.
    observe: Callable[[str, AnswerAnalysis, Reply], MasteryRecord]
    now: datetime
    speed: float = 1.0
    _by_id: dict = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self._by_id = {concept.id: (concept, record) for concept, record in self.concepts}

    def concept(self, concept_id: str) -> Concept | None:
        found = self._by_id.get(concept_id)
        return found[0] if found else None

    def record(self, concept_id: str) -> MasteryRecord | None:
        found = self._by_id.get(concept_id)
        return found[1] if found else None


class Mode(Protocol):
    key: str

    def open(self, ctx: ModeContext) -> Turn:
        """The first thing Aloud says when the mode starts."""
        ...

    def respond(self, ctx: ModeContext, reply: Reply) -> Turn:
        """What Aloud says after the student's reply."""
        ...
