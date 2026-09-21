"""The learner model: what one student knows about one concept, and how surely.

Every mode reads this and every answer writes it. It lives on the same row as
the mastery state rather than in a table of its own, because two tables about
the same student and the same concept will eventually disagree, and the one
thing fourteen features need is a single answer to "how well do they know
this".

What is stored is only what was observed: answers by outcome, how long each
took to start, filler words, self-corrections, lies caught. Everything a mode
actually asks for (confidence, hesitation, shakiness) is derived here on
read, because it depends on the clock: confidence in something last seen four
days ago is not the confidence it had this morning.

All of this is pure. No I/O, no clock of its own, so it is tested without a
database and every mode can call it freely.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import datetime

from .models import Judgement, MasteryRecord

#: How many recent response times are kept. Old ones stop saying anything about
#: how the student is doing tonight.
LATENCY_WINDOW = 8

#: Starting to answer within this is fluent; by this it is plainly hesitant.
FLUENT_MS = 2500
HALTING_MS = 8000

#: A concept is quarantined after this many misses, and released after this
#: many clean answers in a row.
STRIKES_TO_QUARANTINE = 3
CLEARS_TO_RELEASE = 2

#: Confidence in an unrevisited concept halves over this many hours, down to a
#: floor of half. Knowing something yesterday is still worth something.
HALF_LIFE_HOURS = 48

#: Right-but-hesitant is shaky above this.
SHAKY_AT = 0.45

#: "Already known", for the modes that must only touch what is known: the
#: last-90-seconds recap, and anything else that promises no new material.
#: Reached by two fluent right answers, or three halting ones, seen recently.
KNOWN_AT = 0.7

# Disfluencies and hedges. "like", "sort of" and "kind of" are left out on
# purpose: "a kind of force that acts like a spring" is physics, not doubt.
_FILLERS = re.compile(
    r"\b(?:u+m+|u+h+|e+r+m*|a+h+|h+m+|m{2,}|you know|i think|i guess|i suppose)\b"
)
_CORRECTIONS = re.compile(
    r"\b(?:no wait|wait no|sorry|i mean|i meant|or rather|scratch that|"
    r"start again|actually no|no actually|let me rephrase)\b"
)
_NOT_LETTERS = re.compile(r"[^a-z']+")


def _normalise(text: str) -> str:
    return " ".join(_NOT_LETTERS.sub(" ", text.lower()).split())


def count_fillers(transcript: str) -> int:
    """Um, uh, er, and the hedges people use while they work out an answer.

    Speech recognisers often strip these out before we ever see the text, so
    this undercounts. It never invents them.
    """
    return len(_FILLERS.findall(_normalise(transcript)))


def count_corrections(transcript: str) -> int:
    """Places where the student stopped and changed what they were saying."""
    return len(_CORRECTIONS.findall(_normalise(transcript)))


def outcome_for(judgement: Judgement) -> str:
    """How an evaluated answer counts towards the learner model.

    Unclear includes an honest "I don't know", which is a miss: it is exactly
    the case quarantine exists for.
    """
    return {
        Judgement.CORRECT: "correct",
        Judgement.MOSTLY_CORRECT: "correct",
        Judgement.INCOMPLETE: "partial",
        Judgement.MISCONCEPTION: "wrong",
        Judgement.INCORRECT: "wrong",
    }.get(judgement, "missed")


@dataclass(frozen=True)
class Observation:
    """One answer, as the learner model sees it."""

    outcome: str                    # correct | partial | wrong | missed
    latency_ms: int | None = None   # time to start answering; None when typed
    transcript: str = ""


def record(state: MasteryRecord, seen: Observation, *, now: datetime) -> MasteryRecord:
    """The learner model after one more answer. Returns a new record."""
    updated = replace(state, latencies=list(state.latencies))
    updated.last_seen_at = now

    if seen.outcome == "correct":
        updated.correct += 1
    elif seen.outcome == "partial":
        updated.partial += 1
    elif seen.outcome == "wrong":
        updated.wrong += 1
    else:
        updated.missed += 1

    if seen.latency_ms is not None and seen.latency_ms >= 0:
        updated.latencies = (updated.latencies + [int(seen.latency_ms)])[-LATENCY_WINDOW:]
    if seen.transcript:
        updated.fillers += count_fillers(seen.transcript)
        updated.corrections += count_corrections(seen.transcript)

    # Quarantine. Only a clean answer counts towards release, so a partial one
    # between two clears breaks the run just as a miss does.
    if seen.outcome in ("wrong", "missed"):
        updated.strikes += 1
        updated.clear_streak = 0
        if updated.strikes >= STRIKES_TO_QUARANTINE:
            updated.quarantined = True
    elif updated.quarantined:
        if seen.outcome == "correct":
            updated.clear_streak += 1
            if updated.clear_streak >= CLEARS_TO_RELEASE:
                updated.quarantined = False
                updated.strikes = 0
                updated.clear_streak = 0
        else:
            updated.clear_streak = 0
    return updated


def record_lie(state: MasteryRecord, *, caught: bool, now: datetime) -> MasteryRecord:
    """Liar mode's score for this concept."""
    updated = replace(state, latencies=list(state.latencies))
    updated.lies_heard += 1
    updated.catches += 1 if caught else 0
    updated.last_seen_at = now
    return updated


# ---------------------------------------------------------------------------
# What the modes actually ask for
# ---------------------------------------------------------------------------


def answered(state: MasteryRecord) -> int:
    return state.correct + state.partial + state.wrong + state.missed


def accuracy(state: MasteryRecord) -> float:
    """Share of answers that were right, with a partial counting as half."""
    total = answered(state)
    return (state.correct + 0.5 * state.partial) / total if total else 0.0


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def hesitation(state: MasteryRecord) -> float:
    """0 is fluent, 1 is halting. Independent of whether the answers were right.

    Time to start answering carries most of the weight, because it is the
    signal least distorted by the speech recogniser. Typed answers have no
    latency, so the weights are spread over what there is.
    """
    total = answered(state)
    if not total:
        return 0.0

    signals: list[tuple[float, float]] = []
    if state.latencies:
        ordered = sorted(state.latencies)
        median = ordered[len(ordered) // 2]
        signals.append((0.5, _clamp((median - FLUENT_MS) / (HALTING_MS - FLUENT_MS))))
    signals.append((0.3, _clamp(state.fillers / total / 3)))
    signals.append((0.2, _clamp(state.corrections / total / 1.5)))

    weight = sum(w for w, _ in signals)
    return _clamp(sum(w * value for w, value in signals) / weight)


def confidence(state: MasteryRecord, *, now: datetime) -> float:
    """How sure Aloud is that the student knows this, right now. 0 to 1.

    Accuracy is smoothed so one right answer is promising rather than proof,
    discounted by hesitation so a right answer dragged out of someone counts
    for less, and faded by time since the concept was last seen.
    """
    total = answered(state)
    if not total:
        return 0.0
    smoothed = (state.correct + 0.5 * state.partial + 1) / (total + 2)
    fluency = 1 - 0.4 * hesitation(state)

    seen = state.last_seen_at or now
    hours = max(0.0, (now - seen).total_seconds() / 3600)
    recency = 0.5 + 0.5 * 0.5 ** (hours / HALF_LIFE_HOURS)

    return _clamp(smoothed * fluency * recency)


def is_shaky(state: MasteryRecord) -> bool:
    """Right, but not sure. What a mark scheme cannot see and a tutor can.

    Needs two answers before it will say so: one slow answer is a bad moment,
    not a pattern.
    """
    return answered(state) >= 2 and accuracy(state) >= 0.6 and hesitation(state) >= SHAKY_AT


def is_known(state: MasteryRecord, *, now: datetime) -> bool:
    """Solid enough to recap without teaching it again."""
    return confidence(state, now=now) >= KNOWN_AT


def catch_rate(state: MasteryRecord) -> float | None:
    return state.catches / state.lies_heard if state.lies_heard else None
