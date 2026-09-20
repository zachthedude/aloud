"""The mode catalogue.

The modes screen shows all sixteen, including the ones still being built. That
is only honest if an unbuilt mode says so plainly and cannot be switched on,
and if a mode blocked by a missing key says which key.
"""

from __future__ import annotations

from app import features
from app.services import modes


def describe(key, **kwargs):
    defaults = {"flags": dict(features.DEFAULTS), "has_model": False,
                "has_vision": False, "muted": set()}
    defaults.update(kwargs)
    return {row["key"]: row for row in modes.catalogue(**defaults)}[key]


def test_every_flag_has_a_described_mode():
    assert set(modes.BY_KEY) == set(features.DEFAULTS)


def test_every_mode_says_what_it_does():
    for mode in modes.CATALOGUE:
        assert mode.blurb.endswith("."), mode.key
        assert len(mode.blurb.split()) <= 26, mode.key
        assert mode.icon


def test_an_unbuilt_mode_reads_as_coming():
    assert describe("triage")["state"] == "coming"


def test_a_built_mode_is_on_by_default():
    flags = dict(features.DEFAULTS, triage=True)
    assert describe("triage", flags=flags)["state"] == "on"


def test_a_muted_mode_reads_as_off():
    flags = dict(features.DEFAULTS, triage=True)
    assert describe("triage", flags=flags, muted={"triage"})["state"] == "off"


def test_a_mode_needing_a_key_is_blocked_without_one():
    flags = dict(features.DEFAULTS, teachback=True)
    row = describe("teachback", flags=flags)
    assert row["state"] == "blocked"
    assert "ANTHROPIC_API_KEY" in row["reasons"][0]


def test_the_same_mode_is_on_once_the_key_is_there():
    flags = dict(features.DEFAULTS, teachback=True)
    assert describe("teachback", flags=flags, has_model=True)["state"] == "on"


def test_muting_cannot_switch_on_something_unbuilt():
    # Not muted and not built is still not available.
    assert describe("duels", muted=set())["state"] == "coming"


def test_a_standing_requirement_is_stated_even_when_ready():
    flags = dict(features.DEFAULTS, examiner=True)
    row = describe("examiner", flags=flags)
    assert row["state"] == "on"
    assert "mark scheme" in row["reasons"][0]


def test_the_catalogue_keeps_its_order():
    keys = [row["key"] for row in modes.catalogue(
        flags=dict(features.DEFAULTS), has_model=False, has_vision=False, muted=set())]
    assert keys[0] == "triage" and keys[-1] == "duels"
    assert len(keys) == 16
