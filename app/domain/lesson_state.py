"""The lesson conversation as an explicit state machine.

Voice interaction involves many asynchronous parties: the narrator, the
microphone, the transcriber, the evaluator. Without a single authority on what
may happen next, that becomes a pile of callbacks racing each other.

Every transition in a lesson goes through `advance()`. Illegal transitions
raise, so a bug surfaces as a clear error rather than a stuck microphone.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import LessonState as S

#: Available from everywhere, by construction rather than by being listed on
#: each line. A student can always stop, put the lesson down, or hit a failure,
#: and no combination of events may leave a session with nowhere to go.
_ALWAYS: frozenset[S] = frozenset({S.IDLE, S.COMPLETED, S.ERROR})

# What each state may become, beyond the escapes above. Read this as the
# ordering rules of the conversation: it exists to stop nonsense, such as
# evaluating an answer before one has been given, not to corner anybody.
_RULES: dict[S, frozenset[S]] = {
    # Nothing has started yet.
    S.IDLE: frozenset({S.PLAYING, S.AI_SPEAKING, S.REVIEWING}),
    # A section is being narrated.
    S.PLAYING: frozenset({S.AI_SPEAKING, S.PAUSED, S.WAITING_FOR_STUDENT, S.THINKING}),
    S.AI_SPEAKING: frozenset(
        {S.PLAYING, S.PAUSED, S.WAITING_FOR_STUDENT, S.THINKING, S.LISTENING}
    ),
    # Pausing is the most ordinary thing a student does, and it can happen at
    # any moment, including while a question is being asked. So paused leads
    # back to everything: pausing mid-question and answering a minute later
    # must not hit a wall.
    S.PAUSED: frozenset(
        {S.PLAYING, S.AI_SPEAKING, S.WAITING_FOR_STUDENT, S.LISTENING,
         S.TRANSCRIBING, S.EVALUATING, S.THINKING, S.EXPLAINING, S.REVIEWING}
    ),
    # The teacher has asked something and the floor belongs to the student.
    S.WAITING_FOR_STUDENT: frozenset(
        {S.LISTENING, S.TRANSCRIBING, S.EVALUATING, S.PAUSED, S.THINKING, S.AI_SPEAKING}
    ),
    S.LISTENING: frozenset(
        {S.TRANSCRIBING, S.EVALUATING, S.WAITING_FOR_STUDENT, S.PAUSED}
    ),
    S.TRANSCRIBING: frozenset(
        {S.EVALUATING, S.EXPLAINING, S.WAITING_FOR_STUDENT, S.PAUSED}
    ),
    S.EVALUATING: frozenset(
        {S.EXPLAINING, S.AI_SPEAKING, S.REVIEWING, S.WAITING_FOR_STUDENT, S.PAUSED}
    ),
    # A free-form question from the student, mid-lesson.
    S.THINKING: frozenset(
        {S.EXPLAINING, S.AI_SPEAKING, S.WAITING_FOR_STUDENT, S.PLAYING, S.PAUSED}
    ),
    S.EXPLAINING: frozenset(
        {S.AI_SPEAKING, S.PLAYING, S.WAITING_FOR_STUDENT, S.PAUSED, S.THINKING}
    ),
    S.REVIEWING: frozenset({S.PLAYING, S.AI_SPEAKING}),
    S.COMPLETED: frozenset({S.REVIEWING, S.PLAYING}),
    # Errors are always recoverable: never strand the student.
    S.ERROR: frozenset({S.PAUSED, S.PLAYING, S.WAITING_FOR_STUDENT}),
}

#: The full table, with the universal escapes folded in.
_TRANSITIONS: dict[S, frozenset[S]] = {
    state: (allowed | _ALWAYS) - {state} for state, allowed in _RULES.items()
}

#: States in which the student holds the floor. Drives the cool UI palette.
STUDENT_TURN: frozenset[S] = frozenset(
    {S.WAITING_FOR_STUDENT, S.LISTENING, S.TRANSCRIBING}
)


class IllegalTransition(ValueError):
    """Raised when a state change would break the conversation model."""

    def __init__(self, current: S, requested: S) -> None:
        self.current = current
        self.requested = requested
        super().__init__(
            f"Cannot move from {current.value} to {requested.value}. "
            f"Allowed: {sorted(s.value for s in _TRANSITIONS[current])}"
        )


def can_advance(current: S, requested: S) -> bool:
    """True when `requested` is a legal next state."""
    if current is requested:
        return True  # idempotent re-entry is always fine
    return requested in _TRANSITIONS[current]


def advance(current: S, requested: S) -> S:
    """Return `requested` if the transition is legal, else raise."""
    if not can_advance(current, requested):
        raise IllegalTransition(current, requested)
    return requested


def allowed_from(current: S) -> frozenset[S]:
    return _TRANSITIONS[current]


@dataclass(frozen=True)
class TurnPresentation:
    """How a state should be presented. Keeps UI wording out of the frontend.

    `holder` drives the colour temperature of the player: warm while the
    teacher speaks, cool while the student answers, neutral while thinking.
    """

    holder: str          # teacher | student | system
    headline: str        # shown large in the player
    hint: str            # secondary line, may be empty
    mic_active: bool
    audio_active: bool
    busy: bool


_PRESENTATION: dict[S, TurnPresentation] = {
    S.IDLE: TurnPresentation("system", "Ready when you are", "", False, False, False),
    S.PLAYING: TurnPresentation("teacher", "Teaching", "", False, True, False),
    S.AI_SPEAKING: TurnPresentation("teacher", "Teaching", "", False, True, False),
    S.PAUSED: TurnPresentation("system", "Paused", "Take your time.", False, False, False),
    S.WAITING_FOR_STUDENT: TurnPresentation(
        "student", "Your turn", "Answer out loud, or type instead.", True, False, False
    ),
    S.LISTENING: TurnPresentation("student", "I am listening", "", True, False, False),
    S.TRANSCRIBING: TurnPresentation(
        "student", "Getting that down", "", False, False, True
    ),
    S.EVALUATING: TurnPresentation(
        "system", "Thinking about your answer", "", False, False, True
    ),
    S.THINKING: TurnPresentation("system", "One moment", "", False, False, True),
    S.EXPLAINING: TurnPresentation("teacher", "Explaining", "", False, True, False),
    S.REVIEWING: TurnPresentation("system", "Looking back over this", "", False, False, False),
    S.COMPLETED: TurnPresentation("system", "Lesson finished", "", False, False, False),
    S.ERROR: TurnPresentation(
        "system", "Something interrupted us", "You can pick up where you left off.",
        False, False, False
    ),
}


def presentation(state: S) -> TurnPresentation:
    return _PRESENTATION[state]
