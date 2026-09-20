"""Turning what a student says into an intent.

"Go back" should move the lesson, not start a conversation about going back.
This module separates control of the session from questions about the subject,
so the teacher only reasons about the things that are genuinely questions.

Matching is deliberately conservative. A phrase only becomes a command when it
is short and unambiguous, because misreading "can you explain why we move on
from here" as a skip command is far worse than missing a command.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class Intent(str, Enum):
    # Transport
    PAUSE = "pause"
    RESUME = "resume"
    REPEAT = "repeat"
    BACK = "back"
    FORWARD = "forward"
    SLOWER = "slower"
    FASTER = "faster"
    STOP = "stop"
    # Teaching
    EXPLAIN_SIMPLER = "explain_simpler"
    EXPLAIN_DIFFERENTLY = "explain_differently"
    GIVE_EXAMPLE = "give_example"
    DEFINE = "define"
    ASK_ME = "ask_me"
    QUIZ_ME = "quiz_me"
    CONFUSED = "confused"
    MOVE_ON = "move_on"
    REVIEW_LATER = "review_later"
    # Fallback
    QUESTION = "question"      # a genuine question about the material
    ANSWER = "answer"          # a reply to a question the teacher asked


@dataclass(frozen=True)
class Command:
    intent: Intent
    text: str                  # the original utterance
    argument: str = ""         # e.g. the term to define
    confidence: float = 1.0


# Ordered: the first pattern to match wins, so put specific before general.
# Anchored with ^...$ (after normalisation) to keep commands short and certain.
_PATTERNS: list[tuple[Intent, re.Pattern[str]]] = [
    (Intent.PAUSE, re.compile(r"^(pause|hold on|wait|stop for a (sec|second|moment)|one (sec|second))$")),
    (Intent.RESUME, re.compile(r"^(resume|continue|carry on|keep going|go on|play|unpause|go ahead)$")),
    (Intent.REPEAT, re.compile(
        r"^((can you |could you |please )?(say|repeat) that again"
        r"|repeat|again|say again|one more time|(can you |could you )?repeat that"
        r"|play that again)$")),
    (Intent.BACK, re.compile(r"^(go back|back|previous|last (bit|part|section)|rewind)$")),
    (Intent.FORWARD, re.compile(r"^(skip|next|forward|skip (ahead|forward)|next (bit|part|section))$")),
    (Intent.SLOWER, re.compile(r"^(slow(er| down| it down)?|too fast|not so fast)$")),
    (Intent.FASTER, re.compile(r"^(faster|speed (up|it up)|too slow|hurry up)$")),
    (Intent.STOP, re.compile(r"^(stop|end (the )?lesson|i(\s|')?m done|that(\s|')?s enough|quit)$")),
    (Intent.EXPLAIN_SIMPLER, re.compile(
        r"^((can you |could you |please )?(explain|say|put) (that|this|it) (more )?simpl(y|er)"
        r"|simpler|explain like i(\s|')?m (new|five|a beginner)"
        r"|(in )?(plain|simple) (english|terms)"
        r"|i don(\s|')?t (get|understand) (that|this|it))$")),
    (Intent.EXPLAIN_DIFFERENTLY, re.compile(
        r"^((can you |could you |please )?explain (that|this|it) (a )?(different(ly)?|another way)"
        r"|another way|different explanation|try again differently)$")),
    (Intent.GIVE_EXAMPLE, re.compile(
        r"^((can you |could you |please )?(give|show) me (an|another) example"
        r"|(an?|another) example|example please|for example|like what)$")),
    (Intent.ASK_ME, re.compile(
        r"^((can you |could you |please )?ask me (a|another) question"
        r"|ask me something|question me|another question|test me)$")),
    (Intent.QUIZ_ME, re.compile(
        r"^(quiz me( on (this|that|everything|it|all of (it|this)))?"
        r"|quiz me on everything we (just )?(learned|covered)"
        r"|test me on everything)$")),
    (Intent.CONFUSED, re.compile(
        r"^(i(\s|')?m (confused|lost|stuck)|this is confusing|i don(\s|')?t follow"
        r"|no idea|i have no idea|huh|what)$")),
    (Intent.MOVE_ON, re.compile(r"^(move on|got it|i got it|makes sense|understood|i understand|ok(ay)? next)$")),
    (Intent.REVIEW_LATER, re.compile(
        r"^(review (this )?later|come back to (this|it)( later)?|save (this|it) for later"
        r"|mark (this|it) for review|flag (this|it))$")),
]

# "What does X mean" / "define X" carries an argument, so it is handled apart.
_DEFINE = re.compile(
    r"^(what (does|do) (?P<a>.+?) mean"
    r"|what(\s|')?s (a |an |the )?(?P<b>.+?)"
    r"|define (?P<c>.+?)"
    r"|meaning of (?P<d>.+?))$"
)

_QUESTION_OPENERS = (
    "why", "how", "what", "when", "where", "which", "who",
    "can you", "could you", "would", "does", "do", "is", "are", "should",
)

_FILLER = re.compile(r"^(um+|uh+|er+|hmm+|like|so|well|okay|ok|yeah|right)\b[\s,]*")


def normalise(text: str) -> str:
    """Lowercase, strip filler and punctuation, collapse whitespace."""
    cleaned = text.strip().lower()
    cleaned = re.sub(r"[.!?,;:]+$", "", cleaned)
    previous = None
    while previous != cleaned:
        previous = cleaned
        cleaned = _FILLER.sub("", cleaned).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def parse(text: str, *, awaiting_answer: bool = False) -> Command:
    """Classify an utterance.

    `awaiting_answer` matters: while the teacher is waiting for a reply to its
    own question, almost everything the student says is an answer. Only a small
    set of unmistakable control words is still treated as a command, so a
    student answering "stop the motion continues" is not cut off mid-sentence.
    """
    cleaned = normalise(text)
    if not cleaned:
        return Command(Intent.ANSWER if awaiting_answer else Intent.QUESTION, text, confidence=0.0)

    for intent, pattern in _PATTERNS:
        if pattern.match(cleaned):
            if awaiting_answer and intent not in _SAFE_WHILE_ANSWERING:
                break
            return Command(intent, text)

    match = _DEFINE.match(cleaned)
    if match and not awaiting_answer:
        term = next((g for g in match.groupdict().values() if g), "").strip()
        # "what is the answer" is a question, not a definition request.
        if term and len(term.split()) <= 4:
            return Command(Intent.DEFINE, text, argument=term, confidence=0.8)

    if awaiting_answer:
        return Command(Intent.ANSWER, text)

    if cleaned.endswith("?") or cleaned.split()[0] in _QUESTION_OPENERS:
        return Command(Intent.QUESTION, text)

    return Command(Intent.QUESTION, text, confidence=0.6)


#: Commands that remain commands even mid-answer. Short, unmistakable, and the
#: kind of thing a student says when something has gone wrong.
_SAFE_WHILE_ANSWERING: frozenset[Intent] = frozenset(
    {Intent.PAUSE, Intent.STOP, Intent.REPEAT, Intent.CONFUSED, Intent.SLOWER}
)


#: Intents the client can satisfy on its own, without a round trip.
LOCAL_INTENTS: frozenset[Intent] = frozenset(
    {Intent.PAUSE, Intent.RESUME, Intent.REPEAT, Intent.BACK, Intent.FORWARD,
     Intent.SLOWER, Intent.FASTER, Intent.STOP}
)
