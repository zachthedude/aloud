"""What each topic is worth in the exam.

Triage ranks topics by marks times weakness and refuses to teach the cheap
ones. That refusal is only defensible if the marks number comes from
something the student actually uploaded, so each weight carries the basis it
was derived from, in words Aloud can say out loud.

Two kinds of evidence, in order of how much they are trusted:

    past papers   what has actually been asked, and how often. The strongest
                  signal available, and still only a frequency, never a
                  promise about the paper the student is about to sit.
    syllabus      what is examinable at all. Weaker, because a syllabus says
                  nothing about how many marks a topic carries, but it does
                  separate the examinable from the merely interesting.

With neither, every topic weighs the same and the basis says so. A guess
dressed as a weighting would make triage skip the wrong thing confidently,
which is worse than not ranking at all.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..domain.models import Concept, ResourceRole

#: Never let a topic fall to zero. Absent from every past paper is weak
#: evidence that it will not come up, not proof, and a zero would make triage
#: refuse to teach something that is about to be asked.
FLOOR = 0.25

#: How much a syllabus mention is worth against one past-paper appearance.
SYLLABUS_WEIGHT = 0.6

_WORD = re.compile(r"[a-z0-9']+")


@dataclass(frozen=True)
class Evidence:
    """One uploaded document's text, and what kind of document it is."""

    role: ResourceRole
    text: str


@dataclass(frozen=True)
class Weight:
    """A topic's share of the exam, and why Aloud believes it."""

    share: float          # 0..1, sums to 1 across the subject
    basis: str            # said out loud, so it must be a defensible sentence
    papers_seen: int = 0  # how many past papers mentioned it
    in_syllabus: bool = False


def _terms(concept: Concept) -> list[str]:
    """The phrases that mean this concept has come up."""
    found = [concept.name.lower()] + [term.lower() for term in concept.terminology]
    return [term for term in dict.fromkeys(found) if term.strip()]


def _mentions(haystack: str, terms: list[str]) -> bool:
    """Whether any of a concept's phrases appear in a document.

    Matched on whole words so "force" does not fire on "enforced", and on the
    phrase as written so "resultant force" needs both words in order.
    """
    words = " ".join(_WORD.findall(haystack.lower()))
    return any(f" {term} " in f" {words} " for term in terms)


def weigh(concepts: list[Concept], evidence: list[Evidence]) -> dict[str, Weight]:
    """A share of the exam per concept, with the basis for each."""
    if not concepts:
        return {}

    papers = [item for item in evidence if item.role is ResourceRole.PAST_PAPER]
    syllabus = [item for item in evidence if item.role is ResourceRole.SYLLABUS]

    if not papers and not syllabus:
        share = 1.0 / len(concepts)
        basis = (
            "spread evenly: no past papers or syllabus uploaded, so there is "
            "nothing to weigh this against"
        )
        return {concept.id: Weight(share=share, basis=basis) for concept in concepts}

    raw: dict[str, float] = {}
    counts: dict[str, int] = {}
    listed: dict[str, bool] = {}

    for concept in concepts:
        terms = _terms(concept)
        seen = sum(1 for paper in papers if _mentions(paper.text, terms))
        in_syllabus = any(_mentions(item.text, terms) for item in syllabus)
        counts[concept.id] = seen
        listed[concept.id] = in_syllabus
        # The floor keeps an unasked topic in the plan; the evidence above it
        # is what pushes a frequently-asked one to the front.
        score = FLOOR
        if papers:
            score += seen / len(papers)
        if in_syllabus:
            score += SYLLABUS_WEIGHT
        raw[concept.id] = score

    total = sum(raw.values()) or 1.0
    return {
        concept.id: Weight(
            share=raw[concept.id] / total,
            basis=_basis(counts[concept.id], len(papers), listed[concept.id]),
            papers_seen=counts[concept.id],
            in_syllabus=listed[concept.id],
        )
        for concept in concepts
    }


def _basis(seen: int, papers: int, in_syllabus: bool) -> str:
    """One sentence a teacher could say without overclaiming."""
    parts: list[str] = []
    if papers:
        parts.append(
            f"came up in {seen} of {papers} past paper{'s' if papers != 1 else ''}"
            if seen
            else f"did not come up in the {papers} past paper{'s' if papers != 1 else ''} you uploaded"
        )
    if in_syllabus:
        parts.append("named in the syllabus")
    elif not papers:
        parts.append("not named in the syllabus you uploaded")
    return "; ".join(parts) if parts else "no evidence either way"
