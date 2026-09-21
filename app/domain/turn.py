"""What passes between a mode and the voice loop.

A mode never touches a microphone, a speech engine or a socket. It receives a
`Reply` (what the student said or did) and returns a `Turn` (what Aloud says
next, and what it expects back). The voice loop does everything audible. That
split is what lets sixteen modes share one audio path and be tested without
one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: The brief's tone rule: never more than three sentences at a time unless the
#: student asks for more. Checked against every mode in the test suite.
MAX_SENTENCES = 3

EXPECTS = ("answer", "nothing")

_SENTENCE_END = re.compile(r"[.!?]+[\"')\]]*(?:\s+|$)")


def sentence_count(text: str) -> int:
    """Sentences as a listener hears them. Decimals like 9.8 are not breaks."""
    cleaned = re.sub(r"(\d)\.(\d)", r"\1\2", (text or "").strip())
    if not cleaned:
        return 0
    return max(1, len([part for part in _SENTENCE_END.split(cleaned) if part.strip()]))


def first_sentence(text: str) -> str:
    """The opening sentence of a passage, for when only one will fit."""
    parts = re.split(r"(?<=[.!?])\s+", (text or "").strip(), maxsplit=1)
    return parts[0].strip() if parts else ""


@dataclass(frozen=True)
class Turn:
    """Aloud's side of one exchange."""

    say: str
    expects: str = "nothing"                  # answer | nothing
    concept_id: str = ""
    question_id: str = ""
    grounded_in: tuple[str, ...] = ()         # section ids behind every claim made
    done: bool = False                        # the mode has nothing further

    def __post_init__(self) -> None:
        if self.expects not in EXPECTS:
            raise ValueError(f"A turn expects one of {EXPECTS}, not {self.expects!r}")


@dataclass(frozen=True)
class Reply:
    """The student's side of one exchange.

    `action` is empty for a spoken or typed answer. "next" means the student
    heard a turn that expected nothing and is ready for the one after; "skip"
    means they want to move past what is on the floor.
    """

    text: str = ""
    action: str = ""
    input_mode: str = "voice"
    latency_ms: int | None = None
