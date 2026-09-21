"""Quarantine: the topics that keep getting missed, drilled until they clear.

A concept lands here after three misses and leaves after two clean answers in
a row. Those rules live in the learner model (`app/domain/learner.py`), not in
this file, so every mode agrees on what is quarantined. This mode only decides
the order things are asked in and what Aloud says in between.

Two choices worth knowing:

  Interleaved, not hammered. A concept answered correctly goes to the back of
  the queue rather than being asked again straight away, because recalling
  something you said ten seconds ago proves nothing about knowing it.

  A sitting ends. Each concept is asked at most a few times per sitting, so a
  student who genuinely cannot get one out is not stuck in a loop at midnight.
  It is still in quarantine tomorrow.
"""

from __future__ import annotations

from ...domain import learner
from ...domain.turn import Reply, Turn, first_sentence
from .base import ModeContext

#: Per concept, per sitting. Enough for two clears with a miss in between.
MAX_ASKS = 4

EMPTY = (
    "Nothing is in quarantine. A topic lands here after three misses, and "
    "leaves after two clean answers in a row."
)
CLEARED_ALL = "Quarantine is empty. Everything in it has cleared."
ENOUGH = "That is enough for one sitting. What is still in quarantine will be here next time."


class Quarantine:
    key = "quarantine"

    def open(self, ctx: ModeContext) -> Turn:
        held = [(c, r) for c, r in ctx.concepts if r.quarantined and ctx.questions_for(c.id)]
        if not held:
            return Turn(say=EMPTY, done=True)

        # Most strikes first: the topic most likely to cost marks tomorrow.
        held.sort(key=lambda pair: -pair[1].strikes)
        queue = [concept.id for concept, _record in held]
        ctx.cursor.update(queue=queue, asks={}, current=None)

        first = ctx.concept(queue[0])
        count = len(queue)
        lead = (
            f"One topic in quarantine: {first.name}."
            if count == 1
            else f"{count} topics in quarantine, starting with {first.name}."
        )
        return self._ask(ctx, lead=lead)

    def respond(self, ctx: ModeContext, reply: Reply) -> Turn:
        current = ctx.cursor.get("current")

        if reply.action == "next" or not current:
            return self._ask(ctx)

        concept_id = current["concept_id"]
        if reply.action == "skip":
            self._to_back(ctx, concept_id)
            ctx.cursor["current"] = None
            return self._ask(ctx, lead="Leaving that one for now.")

        question = next(
            (q for q in ctx.questions_for(concept_id) if q.id == current["question_id"]), None
        )
        if question is None:
            ctx.cursor["current"] = None
            return self._ask(ctx)

        concept = ctx.concept(concept_id)
        analysis = ctx.evaluate(reply.text, question, concept)
        after = ctx.observe(concept_id, analysis, reply)
        outcome = learner.outcome_for(analysis.judgement)
        ctx.cursor["current"] = None

        missing = first_sentence(analysis.missed_points[0]) if analysis.missed_points else ""
        missing = missing.rstrip(".")
        if not after.quarantined:
            self._remove(ctx, concept_id)
            say = f"That clears it. {concept.name} is out of quarantine."
        elif outcome == "correct":
            self._to_back(ctx, concept_id)
            say = "Right. One more clean answer in a row and it is out."
        elif outcome == "partial":
            self._to_back(ctx, concept_id)
            say = f"Nearly. The piece missing was: {missing}." if missing else "Nearly, but not all of it."
        else:
            self._to_back(ctx, concept_id)
            say = (
                f"Not quite. What it is after: {missing}. It stays in quarantine for now."
                if missing
                else "Not quite. It stays in quarantine for now."
            )

        return Turn(say=say, concept_id=concept_id, grounded_in=tuple(concept.section_ids))

    # -- the queue -----------------------------------------------------------

    def _ask(self, ctx: ModeContext, lead: str = "") -> Turn:
        queue: list[str] = ctx.cursor.get("queue", [])
        asks: dict[str, int] = ctx.cursor.setdefault("asks", {})
        if not queue:
            return Turn(say=CLEARED_ALL, done=True)

        concept_id = next((cid for cid in queue if asks.get(cid, 0) < MAX_ASKS), None)
        if concept_id is None:
            return Turn(say=ENOUGH, done=True)

        questions = ctx.questions_for(concept_id)
        if not questions:
            self._remove(ctx, concept_id)
            return self._ask(ctx, lead=lead)

        # Rotate through the concept's questions, so a concept asked twice
        # is never asked the same question twice in a row.
        count = asks.get(concept_id, 0)
        question = questions[count % len(questions)]
        asks[concept_id] = count + 1
        ctx.cursor["current"] = {"concept_id": concept_id, "question_id": question.id}

        concept = ctx.concept(concept_id)
        say = f"{lead} {question.prompt}".strip() if lead else question.prompt
        return Turn(
            say=say,
            expects="answer",
            concept_id=concept_id,
            question_id=question.id,
            grounded_in=tuple(concept.section_ids) if concept else (),
        )

    @staticmethod
    def _to_back(ctx: ModeContext, concept_id: str) -> None:
        queue = ctx.cursor.get("queue", [])
        if concept_id in queue:
            queue.remove(concept_id)
            queue.append(concept_id)

    @staticmethod
    def _remove(ctx: ModeContext, concept_id: str) -> None:
        queue = ctx.cursor.get("queue", [])
        if concept_id in queue:
            queue.remove(concept_id)
