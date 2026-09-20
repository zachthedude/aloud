"""How evidence from spoken answers moves a student's grip on a concept.

Two principles shape everything here.

1. Evaluation is not truth. A judgement arrives with a confidence, and a
   low-confidence read moves mastery less than a clear one. When the evaluator
   is genuinely unsure the teacher says so and asks again, rather than
   recording a verdict the student never earned.

2. Mistakes never cost ground the student has already made. A concept can drop
   to "worth revisiting" but never falls back to "not started", and nothing in
   this module produces a score, a streak to protect or a penalty.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from .models import Judgement, Mastery, MasteryRecord

#: Below this, the evaluator is not sure enough to move mastery at all.
UNSURE_BELOW = 0.45

#: At or above this, a judgement counts at full weight.
CONFIDENT_AT = 0.75

# How each judgement nudges mastery, in rank steps, at full confidence.
_STEP: dict[Judgement, int] = {
    Judgement.CORRECT: +1,
    Judgement.MOSTLY_CORRECT: +1,
    Judgement.INCOMPLETE: 0,
    Judgement.MISCONCEPTION: -1,
    Judgement.INCORRECT: -1,
    Judgement.UNCLEAR: 0,
}

_POSITIVE = (Judgement.CORRECT, Judgement.MOSTLY_CORRECT)
_NEGATIVE = (Judgement.MISCONCEPTION, Judgement.INCORRECT)

_BY_RANK: dict[int, Mastery] = {m.rank: m for m in Mastery}

#: Once a student has engaged with a concept, it never returns to "not started".
_FLOOR = Mastery.NEEDS_REVIEW


@dataclass(frozen=True)
class NextStep:
    """What the teacher should do after reading an answer."""

    action: str      # continue | another_example | reexplain | revisit_prereq
                     # | extend | ask_again
    reason: str      # student-facing sentence, already in the teacher voice


def _clamp(rank: int) -> Mastery:
    return _BY_RANK[max(_FLOOR.rank, min(Mastery.MASTERED.rank, rank))]


def apply_evidence(
    record: MasteryRecord,
    judgement: Judgement,
    confidence: float,
    *,
    now: datetime | None = None,
) -> MasteryRecord:
    """Fold one answer into a mastery record, returning an updated copy.

    Pure: the input record is never mutated.
    """
    confidence = max(0.0, min(1.0, confidence))
    evidence_count = record.evidence_count + 1

    # Too unsure to judge. Log that we saw an answer and leave mastery alone.
    if confidence < UNSURE_BELOW or judgement is Judgement.UNCLEAR:
        return replace(
            record,
            evidence_count=evidence_count,
            last_seen_at=now or record.last_seen_at,
        )

    step = _STEP[judgement]
    # A merely-plausible read moves half as far as a confident one.
    if step > 0 and confidence < CONFIDENT_AT:
        step = 0 if record.correct_streak == 0 else step

    streak = record.correct_streak + 1 if judgement in _POSITIVE else 0
    struggle = record.struggle_count + 1 if judgement in _NEGATIVE else max(
        0, record.struggle_count - (1 if judgement in _POSITIVE else 0)
    )

    current = record.state
    target_rank = max(current.rank, Mastery.DEVELOPING.rank) if step >= 0 else current.rank
    if current is Mastery.NOT_STARTED:
        # First contact: land on developing, or lower if it went badly.
        target_rank = Mastery.DEVELOPING.rank + (0 if step >= 0 else -1)
    else:
        target_rank = current.rank + step

    # "Solid" is only reached by being right twice running, never on one answer.
    if target_rank >= Mastery.MASTERED.rank and streak < 2:
        target_rank = Mastery.UNDERSTOOD.rank
    # Repeated trouble parks the concept in revisit territory.
    if struggle >= 2:
        target_rank = min(target_rank, Mastery.NEEDS_REVIEW.rank)

    return replace(
        record,
        state=_clamp(target_rank),
        evidence_count=evidence_count,
        correct_streak=streak,
        struggle_count=struggle,
        last_seen_at=now or record.last_seen_at,
    )


def decide_next_step(
    record: MasteryRecord,
    judgement: Judgement,
    confidence: float,
    *,
    reexplain_count: int = 0,
    has_prerequisite: bool = False,
    extension_offered: bool = False,
) -> NextStep:
    """Choose what happens next, the way a teacher watching a face would.

    `reexplain_count` is how many times this concept has already been explained
    again in the current sitting, which is what stops the teacher looping.
    """
    if confidence < UNSURE_BELOW or judgement is Judgement.UNCLEAR:
        return NextStep(
            "ask_again",
            "I am not sure I followed that. Let me put the question a different way.",
        )

    if judgement is Judgement.CORRECT:
        if record.state is Mastery.MASTERED and not extension_offered:
            return NextStep(
                "extend",
                "That is solid. Let me give you something harder to chew on.",
            )
        return NextStep("continue", "That is the key idea. Let us keep going.")

    if judgement is Judgement.MOSTLY_CORRECT:
        return NextStep(
            "another_example",
            "You have the shape of it. One more example will settle it.",
        )

    if judgement is Judgement.INCOMPLETE:
        return NextStep(
            "another_example",
            "You are on the right track. There is one more piece to add.",
        )

    # Misconception or outright wrong.
    if has_prerequisite and (record.struggle_count >= 2 or reexplain_count >= 2):
        return NextStep(
            "revisit_prereq",
            "Let us step back to the idea underneath this one. It will make this easier.",
        )
    if judgement is Judgement.MISCONCEPTION:
        return NextStep("reexplain", "Not quite, and it is a common place to trip. Let us look again.")
    return NextStep("reexplain", "Let me explain that a different way.")


def summarise(records: list[MasteryRecord]) -> dict[str, int]:
    """Counts per mastery state, for progress views."""
    counts = {m.value: 0 for m in Mastery}
    for record in records:
        counts[record.state.value] += 1
    return counts


def overall_progress(records: list[MasteryRecord]) -> float:
    """0..1 across a space. Informative, never presented as a grade."""
    if not records:
        return 0.0
    top = Mastery.MASTERED.rank
    return sum(r.state.rank for r in records) / (len(records) * top)


def readiness_note(state: Mastery) -> str:
    """One calm sentence describing where a concept stands."""
    return {
        Mastery.NOT_STARTED: "Not started yet.",
        Mastery.NEEDS_REVIEW: "This one is worth another pass.",
        Mastery.DEVELOPING: "You are building this.",
        Mastery.UNDERSTOOD: "You have this.",
        Mastery.MASTERED: "This is solid. It will hold up under pressure.",
    }[state]
