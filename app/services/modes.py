"""The catalogue of crunch modes.

Sixteen ways of working on the same material, over the same voice loop and the
same learner model. This module is the list itself: what each one is called,
what it does in one sentence, what it needs before it can run, and whether it
exists yet.

Deliberately only the catalogue. The strategies that implement each mode land
in `app/services/modes/` in phase D. Keeping the description separate from the
implementation is what lets the interface show a student the whole product,
including the parts still being built, without pretending any of it works.
"""

from __future__ import annotations

from dataclasses import dataclass

from .. import features

#: Requirements a mode can have beyond being built.
NEEDS_MODEL = "model"            # real reasoning, so an API key
NEEDS_VISION = "vision"          # reading a photograph
NEEDS_MARK_SCHEME = "mark_scheme"
NEEDS_PAST_PAPERS = "past_papers"
NEEDS_MOTION = "motion"          # a device that can sense walking
NEEDS_PARTNER = "partner"        # another student sitting the same exam

_REASON = {
    NEEDS_MODEL: "needs an ANTHROPIC_API_KEY to judge an answer properly",
    NEEDS_VISION: "needs an ANTHROPIC_API_KEY to look at a photo",
    NEEDS_MARK_SCHEME: "needs a mark scheme in your material",
    NEEDS_PAST_PAPERS: "needs at least two past papers in your material",
    NEEDS_MOTION: "needs a phone that can feel you walking",
    NEEDS_PARTNER: "needs someone else sitting the same exam",
}


@dataclass(frozen=True)
class Mode:
    key: str
    name: str
    blurb: str          # one sentence, said the way the mode behaves
    icon: str
    needs: tuple[str, ...] = ()


CATALOGUE: tuple[Mode, ...] = (
    Mode("triage", "Triage", "Tell it when the exam is and it plans the hours, out loud, including what it refuses to teach.", "clock"),
    Mode("last90", "Last 90 seconds", "A spoken lap of what you already know, right before you walk in. No new material.", "flame"),
    Mode("sleep", "Sleep boundary", "Quiet, slow, no questions. The day's weakest points replayed for the last fifteen minutes.", "moon"),
    Mode("heatmap", "Hesitation map", "Finds the topics you got right but hesitated on, and steers everything else towards them.", "grid"),
    Mode("coldcall", "Cold call", "Cuts into its own explanation with a sudden question. You set how often.", "bell"),
    Mode("teachback", "Teach back", "It plays a younger student who is not quite following. You explain. It names what you left out.", "mic", (NEEDS_MODEL,)),
    Mode("examiner", "Examiner", "Marks what you said against your mark scheme, marks lost and gained, and drills command words.", "stamp", (NEEDS_MARK_SCHEME,)),
    Mode("patterns", "Pattern hunter", "What your past papers keep asking, as a frequency. Never as a prediction.", "search", (NEEDS_PAST_PAPERS,)),
    Mode("bridge", "Bridge", "Questions that only work if you hold two chapters at once.", "bridge"),
    Mode("liar", "Liar", "It says something plausible and wrong. You catch it. It tracks how often you do.", "mask"),
    Mode("paper", "Paper handshake", "Work it on paper, photograph it, and it critiques your working out loud.", "camera", (NEEDS_VISION,)),
    Mode("panic", "I blanked", "Say the words and it walks you back from something you do know, in fifteen seconds.", "lifebuoy"),
    Mode("walk", "Walk", "Times the prompts to your pace so recall lands on your steps.", "walk", (NEEDS_MOTION,)),
    Mode("palace", "Memory palace", "Describe a route you know. It puts the facts along it and walks you through.", "map"),
    Mode("quarantine", "Quarantine", "Three misses and a topic goes in the drill queue until it clears twice.", "shield"),
    Mode("duels", "Duels", "Quiz someone else sitting the same exam, by voice, in your own time.", "swords", (NEEDS_PARTNER,)),
)

BY_KEY = {mode.key: mode for mode in CATALOGUE}

assert set(BY_KEY) == set(features.DEFAULTS), "the catalogue and the feature flags disagree"


def describe(
    mode: Mode,
    *,
    built: bool,
    has_model: bool,
    has_vision: bool,
    muted: bool,
) -> dict:
    """One mode as the interface should show it.

    Requirements that depend on a particular subject's uploads, like a mark
    scheme, are reported as standing conditions rather than checked here: the
    modes screen is about the product, and the subject screen is where a
    missing mark scheme is actually actionable.
    """
    missing = []
    if NEEDS_MODEL in mode.needs and not has_model:
        missing.append(NEEDS_MODEL)
    if NEEDS_VISION in mode.needs and not has_vision:
        missing.append(NEEDS_VISION)

    if not built:
        state = "coming"
    elif missing:
        state = "blocked"
    elif muted:
        state = "off"
    else:
        state = "on"

    # Conditions the student can satisfy by uploading something are always
    # worth stating, even when the mode is otherwise ready.
    standing = [need for need in mode.needs if need not in (NEEDS_MODEL, NEEDS_VISION)]
    return {
        "key": mode.key,
        "name": mode.name,
        "blurb": mode.blurb,
        "icon": mode.icon,
        "state": state,
        "reasons": [_REASON[need] for need in (missing or standing)],
    }


def catalogue(*, flags: dict[str, bool], has_model: bool, has_vision: bool, muted: set[str]) -> list[dict]:
    return [
        describe(
            mode,
            built=flags.get(mode.key, False),
            has_model=has_model,
            has_vision=has_vision,
            muted=mode.key in muted,
        )
        for mode in CATALOGUE
    ]
