"""Reading a spoken answer.

Students answer out loud, so answers arrive as loose, unpunctuated, sometimes
half-finished speech. This module scores that against what a question was
looking for, and is explicit about how sure it is.

The scorer is lexical, and it knows it. It reports low confidence on short,
hedged or off-topic answers, and the teacher turns low confidence into "I am
not sure I followed that, let me ask differently" rather than a verdict. When a
hosted model is configured it produces the judgement instead, but its output is
still run through `sanity_check` here, so a model that returns "correct" for an
empty answer cannot award mastery.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .models import Judgement, Question

_STOPWORDS = frozenset(
    """a an the and or but if then than that this these those there here it its
    is are was were be been being am do does did doing have has had having
    i you he she we they me him her us them my your his their our
    of in on at to for with by from as into about over under between
    so because just very really quite some any all no not nor only own same
    can could will would shall should may might must ought
    what which who whom whose when where why how
    up down out off again more most other such too also well
    like get got go going make makes made say said see seen know knows knew
    thing things stuff kind sort lot bit way
    um uh er hmm yeah ok okay right mean means basically actually""".split()
)

# Speech-friendly equivalences. A student saying "push" for "force" is not wrong.
_SYNONYMS: dict[str, str] = {
    "push": "force", "pull": "force", "pushes": "force", "pulls": "force",
    "pushing": "force", "pulling": "force", "shove": "force",
    "speed": "velocity", "speeds": "velocity", "fast": "velocity",
    "faster": "velocity", "quickness": "velocity",
    "slow": "decelerate", "slowing": "decelerate", "braking": "decelerate",
    "speeding": "accelerate", "accelerates": "accelerate",
    "acceleration": "accelerate", "accelerating": "accelerate",
    "heavier": "mass", "heavy": "mass", "weight": "mass", "heaviness": "mass",
    "moving": "motion", "move": "motion", "moves": "motion", "movement": "motion",
    "stationary": "rest", "still": "rest", "stopped": "rest", "motionless": "rest",
    "change": "change", "changes": "change", "changing": "change",
    "opposite": "opposite", "reverse": "opposite", "reversed": "opposite",
    "equal": "equal", "same": "equal", "identical": "equal",
    "friction": "friction", "rubbing": "friction", "drag": "friction",
    "gravity": "gravity", "gravitational": "gravity",
    "energy": "energy", "power": "energy",
    "temperature": "temperature", "hot": "temperature", "heat": "temperature",
    "pressure": "pressure", "squeeze": "pressure", "squeezed": "pressure",
    "particles": "particle", "molecules": "particle", "atoms": "atom",
    "bigger": "increase", "larger": "increase", "greater": "increase",
    "rises": "increase", "rise": "increase", "grows": "increase", "up": "increase",
    "smaller": "decrease", "less": "decrease", "lower": "decrease",
    "falls": "decrease", "drops": "decrease", "down": "decrease",
}

_SUFFIXES = ("ational", "ization", "iveness", "fulness", "ousness", "ities",
             "ement", "ation", "ingly", "edly", "ness", "ment", "tion", "sion",
             "ance", "ence", "able", "ible", "ing", "ies", "ied", "est", "ity",
             "ive", "ers", "ed", "es", "ly", "er", "s")

_WORD = re.compile(r"[a-z0-9]+")

#: An answer shorter than this cannot support a confident verdict.
MIN_SUBSTANTIVE_TOKENS = 3

_HEDGES = re.compile(
    r"\b(i (don\'?t|do not) know|no idea|not sure|maybe|i guess|i think maybe|"
    r"something like|dunno|pass|skip( this)?|can\'?t remember|forgot)\b"
)


def stem(word: str) -> str:
    """Crude suffix stripping. Good enough to match speech to source text."""
    word = _SYNONYMS.get(word, word)
    if len(word) <= 4:
        return word
    for suffix in _SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]
    return word


def tokenise(text: str, *, keep_stopwords: bool = False) -> list[str]:
    words = _WORD.findall(text.lower())
    if not keep_stopwords:
        words = [w for w in words if w not in _STOPWORDS]
    return [stem(w) for w in words if w]


def _coverage(point: str, answer_tokens: set[str]) -> float:
    """Fraction of a point's meaningful words the answer touches."""
    point_tokens = set(tokenise(point))
    if not point_tokens:
        return 0.0
    return len(point_tokens & answer_tokens) / len(point_tokens)


#: Base share of a point's words an answer must touch to have covered it.
POINT_THRESHOLD = 0.5


def _threshold_for(point: str) -> float:
    """How much of a point an answer must touch, given how long the point is.

    A short point like "inversely proportional to mass" should be matched
    closely. A point lifted whole from a textbook sentence carries a lot of
    incidental wording, and a student explaining the same idea in their own
    words will never reproduce most of it. Holding long points to the same bar
    marks correct answers wrong, so the bar relaxes as points get longer.
    """
    length = len(set(tokenise(point)))
    if length <= 4:
        # Few words, so each one is load-bearing. "doubling the force doubles
        # the acceleration" should not be matched by naming force alone.
        return 0.6
    if length <= 8:
        return 0.42
    return 0.34


@dataclass
class AnswerAnalysis:
    """What the scorer made of an answer, and how sure it is."""

    judgement: Judgement
    confidence: float
    matched_points: list[str] = field(default_factory=list)
    missed_points: list[str] = field(default_factory=list)
    detected_misconceptions: list[str] = field(default_factory=list)
    coverage: float = 0.0
    reason: str = ""


def evaluate(transcript: str, question: Question) -> AnswerAnalysis:
    """Score an answer against what the question was looking for."""
    raw = (transcript or "").strip()
    if not raw:
        return AnswerAnalysis(
            Judgement.UNCLEAR, 0.0, missed_points=list(question.expected_points),
            reason="Nothing was said.",
        )

    lowered = raw.lower()
    substantive = tokenise(raw)
    answer_tokens = set(substantive)

    # An explicit "I don't know" is honest, not wrong. Treat it as unclear so
    # it costs no ground, and let the teacher respond by explaining again.
    if _HEDGES.search(lowered) and len(substantive) <= 8:
        return AnswerAnalysis(
            Judgement.UNCLEAR, 0.3, missed_points=list(question.expected_points),
            reason="The student said they are not sure.",
        )

    misconceptions = [
        marker for marker in question.misconception_markers
        if _coverage(marker, answer_tokens) >= 0.6
    ]

    matched: list[str] = []
    missed: list[str] = []
    for point in question.expected_points:
        if _coverage(point, answer_tokens) >= _threshold_for(point):
            matched.append(point)
        else:
            missed.append(point)

    total = len(question.expected_points)
    point_ratio = len(matched) / total if total else 0.0

    # Matching sentence by sentence is brittle: a student can express the whole
    # idea in their own words and mirror no single expected sentence closely.
    # So also score against the pooled vocabulary of every expected point, and
    # take whichever reading is kinder. Being understood in different words is
    # not a wrong answer.
    pooled = {t for point in question.expected_points for t in tokenise(point)}
    pooled_coverage = len(pooled & answer_tokens) / len(pooled) if pooled else 0.0
    coverage = max(point_ratio, pooled_coverage)

    # Confidence rises with how much there is to read and how clear the result
    # is. A two-word answer never produces a confident verdict.
    length_factor = min(1.0, len(substantive) / 12.0)
    decisiveness = abs(coverage - 0.5) * 2  # 0 at the boundary, 1 at either end
    confidence = round(min(0.95, 0.25 + 0.45 * length_factor + 0.3 * decisiveness), 2)

    if len(substantive) < MIN_SUBSTANTIVE_TOKENS:
        return AnswerAnalysis(
            Judgement.UNCLEAR, min(confidence, 0.35), matched, missed, misconceptions,
            coverage, "Too short to tell what the student means.",
        )

    if misconceptions:
        judgement = Judgement.MISCONCEPTION
        reason = "The answer contains a known misconception."
    elif coverage >= 0.85:
        judgement = Judgement.CORRECT
        reason = "The answer covers what the question was looking for."
    elif coverage >= 0.6:
        judgement = Judgement.MOSTLY_CORRECT
        reason = "Most of the idea is there."
    elif coverage >= 0.3:
        judgement = Judgement.INCOMPLETE
        reason = "Part of the idea is there, with something missing."
    elif coverage >= 0.25:
        judgement = Judgement.INCOMPLETE
        reason = "Only a fragment of the idea came through."
    else:
        # Brushing one word of the answer is not partial understanding.
        judgement = Judgement.INCORRECT
        reason = "The answer does not touch what the question was about."

    return AnswerAnalysis(judgement, confidence, matched, missed, misconceptions, coverage, reason)


def sanity_check(analysis: AnswerAnalysis, transcript: str) -> AnswerAnalysis:
    """Guard rails applied to any evaluator, including a hosted model.

    A model that confidently marks an empty or two-word answer correct would
    hand out mastery nobody earned. This caps that.
    """
    substantive = tokenise(transcript or "")
    if len(substantive) < MIN_SUBSTANTIVE_TOKENS:
        analysis.judgement = Judgement.UNCLEAR
        analysis.confidence = min(analysis.confidence, 0.35)
        analysis.reason = analysis.reason or "Too short to tell what the student means."
    analysis.confidence = max(0.0, min(1.0, analysis.confidence))
    return analysis


def combine(analyses: list[AnswerAnalysis]) -> tuple[float, float]:
    """Aggregate several answers into (coverage, confidence) for a lesson."""
    if not analyses:
        return 0.0, 0.0
    coverage = sum(a.coverage for a in analyses) / len(analyses)
    confidence = sum(a.confidence for a in analyses) / len(analyses)
    return round(coverage, 3), round(confidence, 3)
