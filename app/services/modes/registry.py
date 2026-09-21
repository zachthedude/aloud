"""Which modes are actually implemented.

Adding a mode is one file in this package and one entry below. A feature flag
can switch a registered mode off, but it can never switch on a mode that is
not here: `effective_flags` is what makes that impossible.
"""

from __future__ import annotations

from .base import Mode
from .last90 import Last90
from .quarantine import Quarantine

MODES: dict[str, Mode] = {mode.key: mode for mode in (Last90(), Quarantine())}


def effective_flags(flags: dict[str, bool]) -> dict[str, bool]:
    """A flag counts only if the mode it names exists."""
    return {key: bool(on) and key in MODES for key, on in flags.items()}
