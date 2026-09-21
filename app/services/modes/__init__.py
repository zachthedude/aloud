"""Crunch modes.

`catalogue` is what each mode is called and what it needs. `registry` is which
of them are actually implemented. Keeping those apart is what lets the
interface show all sixteen honestly while only some exist.
"""

from .catalogue import BY_KEY, CATALOGUE, Mode, catalogue, describe

__all__ = ["BY_KEY", "CATALOGUE", "Mode", "catalogue", "describe"]
