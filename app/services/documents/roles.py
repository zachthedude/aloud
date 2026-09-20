"""What kind of document an upload is.

Four roles, because four different parts of the product need different things
from the same pile of files: triage ranks by marks, so it needs the syllabus;
the pattern hunter only looks at past papers; the examiner refuses to mark
without a mark scheme; everything else teaches from notes.

The guess is timid on purpose. Notes is the default, and the other three roles
are only claimed on positive evidence, because the cost of the two mistakes is
not symmetric: calling a mark scheme "notes" means the student tags it by
hand, while calling notes a "mark scheme" would let the examiner award marks
against criteria nobody wrote.
"""

from __future__ import annotations

import re

from ...domain.models import ResourceRole

# Only the opening of a document is read. Past papers, mark schemes and
# syllabuses all announce themselves on the first page; a passing mention on
# page 40 says nothing about what the document is.
OPENING_CHARS = 1200

# Phrases confident enough to trust anywhere, filename or text.
_PHRASES: tuple[tuple[ResourceRole, tuple[str, ...]], ...] = (
    (ResourceRole.MARK_SCHEME, (
        r"mark scheme", r"marking scheme", r"marking guide", r"markscheme",
        r"examiner report", r"examiners report", r"principal examiner",
    )),
    (ResourceRole.PAST_PAPER, (
        r"past paper", r"question paper", r"exam paper", r"examination paper",
        r"paper \d", r"\bpaper [iv]+\b",
    )),
    (ResourceRole.SYLLABUS, (
        r"syllabus", r"specification", r"curriculum", r"scheme of work",
        r"learning objectives", r"assessment objectives",
    )),
)

# Exam-board shorthand. Trusted in a filename, where "ms" between separators
# really does mean mark scheme, and nowhere else.
_CODES: tuple[tuple[ResourceRole, tuple[str, ...]], ...] = (
    (ResourceRole.MARK_SCHEME, (r"\bms\b", r"\bmsc\b")),
    (ResourceRole.PAST_PAPER, (r"\bqp\b", r"\bpaper\b")),
    (ResourceRole.SYLLABUS, (r"\bspec\b",)),
)

_NOT_WORD = re.compile(r"[^a-z0-9]+")


def _normalise(value: str) -> str:
    """Lowercase, and every separator a space, so `9702_s22_ms_12` yields `ms`."""
    return _NOT_WORD.sub(" ", value.lower()).strip()


def _matches(haystack: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, haystack) for pattern in patterns)


def guess_role(filename: str, text: str = "") -> ResourceRole:
    """Best guess at a resource's role. Always correctable by hand.

    Mark scheme wins over past paper, which wins over syllabus, because a mark
    scheme names its paper on its own front page and a paper quotes the
    syllabus. Checking in that order stops the weaker signal winning.
    """
    name = _normalise(filename)
    opening = _normalise(text[:OPENING_CHARS])

    for role, patterns in _PHRASES:
        if _matches(name, patterns) or _matches(opening, patterns):
            return role
    for role, patterns in _CODES:
        if _matches(name, patterns):
            return role
    return ResourceRole.NOTES
