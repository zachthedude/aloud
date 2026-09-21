"""Last 90 seconds: a spoken lap of what the student already knows.

For the corridor outside the exam hall. It recaps definitions, formulas and
traps, and only for concepts the learner model says are already known. It
teaches nothing new and asks nothing, on purpose: ninety seconds before an
exam is the worst possible time to meet something for the first time, and a
question you cannot answer at that moment only costs you nerve.

Everything it says is read from the student's own material: the definition,
the formula and the trap are the concept's, word for word.
"""

from __future__ import annotations

from ...domain import learner
from ...domain.models import Concept
from ...domain.turn import Reply, Turn, first_sentence
from .base import ModeContext

BUDGET_SECONDS = 90
#: Speaking rate at 1x, the same one the narrator estimates durations with.
WORDS_PER_SECOND = 150 / 60

INTRO = "Ninety seconds, only what you already have."
OUTRO = "That is the lot. You know these, so go and use them."
NOTHING_KNOWN = (
    "Nothing is solid enough yet for a last lap. These ninety seconds are only "
    "for what you already know, so there is nothing to cram into them."
)


def _words(text: str) -> int:
    return len(text.split())


def recap(concept: Concept) -> str:
    """At most three sentences: what it is, its formula, and the trap."""
    parts: list[str] = []
    opening = first_sentence(concept.definition or concept.summary)
    if opening:
        if concept.name.lower() not in opening.lower():
            opening = f"{concept.name}: {opening}"
        parts.append(opening if opening.endswith((".", "!", "?")) else f"{opening}.")
    if concept.formulas:
        parts.append(f"The formula is {concept.formulas[0].rstrip('.')}.")
    if concept.misconceptions:
        parts.append(f"Watch out: {first_sentence(concept.misconceptions[0])}")
    if not parts:
        return ""
    if not opening:
        # Nothing to say what it is, but a formula or trap is still worth hearing.
        parts.insert(0, f"{concept.name}.")
    return " ".join(parts[:3])


class Last90:
    key = "last90"

    def open(self, ctx: ModeContext) -> Turn:
        known = [(c, r) for c, r in ctx.concepts if learner.is_known(r, now=ctx.now)]
        if not known:
            return Turn(say=NOTHING_KNOWN, done=True)

        # What is worth most in the exam first, so running out of time cuts
        # the cheapest material rather than the dearest.
        known.sort(key=lambda pair: (-pair[0].exam_weight, -learner.confidence(pair[1], now=ctx.now)))

        budget = BUDGET_SECONDS * WORDS_PER_SECOND * max(0.5, ctx.speed)
        spent = _words(INTRO) + _words(OUTRO)
        items: list[dict] = []
        for concept, _record in known:
            line = recap(concept)
            if not line:
                continue
            cost = _words(line)
            if items and spent + cost > budget:
                break
            items.append({"concept_id": concept.id, "say": line, "grounded": list(concept.section_ids)})
            spent += cost

        if not items:
            return Turn(say=NOTHING_KNOWN, done=True)
        ctx.cursor.update(items=items, index=-1)
        return Turn(say=INTRO)

    def respond(self, ctx: ModeContext, reply: Reply) -> Turn:
        items = ctx.cursor.get("items", [])
        index = ctx.cursor.get("index", -1) + 1
        ctx.cursor["index"] = index
        if index >= len(items):
            return Turn(say=OUTRO, done=True)
        item = items[index]
        return Turn(say=item["say"], concept_id=item["concept_id"], grounded_in=tuple(item["grounded"]))
