"""Feature flags for the crunch modes.

Sixteen modes ship one at a time. Each stays behind a flag until it is built,
tested and reviewed, so a half-finished mode cannot be reached by typing a URL
or guessing an API path. Flags resolve once at startup and travel to the client
with `/api/auth/me`, so the interface and the server never disagree about what
exists.

Set them with one environment variable:

    ALOUD_FEATURES=triage,heatmap      turn these on
    ALOUD_FEATURES=all,-duels          everything except duels
"""

from __future__ import annotations

# Every mode in the brief, in the order it is planned. False until it lands,
# True once it is implemented and registered in services/modes/registry.py.
DEFAULTS: dict[str, bool] = {
    "triage": False,      # 1  exam date -> ranked, time-boxed plan
    "last90": True,       # 2  ninety-second recap of what is already known
    "sleep": False,       # 3  quiet replay at the sleep boundary
    "heatmap": False,     # 4  hesitation heatmap
    "coldcall": False,    # 5  sudden questions mid-explanation
    "teachback": False,   # 6  the learner teaches, Aloud plays confused
    "examiner": False,    # 7  marking against an uploaded mark scheme
    "patterns": False,    # 8  recurring shapes across past papers
    "bridge": False,      # 9  questions spanning two topics
    "liar": False,        # 10 plausible falsehoods to catch
    "paper": False,       # 11 photograph your working, critiqued aloud
    "panic": False,       # 12 "I blanked" -> retrieval chain
    "walk": False,        # 13 prompts timed to walking pace
    "palace": False,      # 14 spoken memory palace
    "quarantine": True,   # 15 three misses pulls a concept into a drill queue
    "duels": False,       # 16 two learners quizzing each other
}


def resolve(spec: str) -> dict[str, bool]:
    """Apply an `ALOUD_FEATURES` string to the defaults.

    An unknown name raises rather than being ignored: a typo that silently
    leaves a mode off is the kind of bug you only find in front of someone.
    """
    flags = dict(DEFAULTS)
    for raw in spec.split(","):
        name = raw.strip().lower()
        if not name:
            continue
        wanted = not name.startswith("-")
        name = name.lstrip("-")
        if name == "all":
            flags = {key: wanted for key in flags}
            continue
        if name not in flags:
            known = ", ".join(sorted(DEFAULTS))
            raise ValueError(f"Unknown feature {name!r} in ALOUD_FEATURES. Known: {known}")
        flags[name] = wanted
    return flags
