"""Feature flags.

Every crunch mode ships behind a flag. These tests pin the two behaviours that
matter: a half-built mode cannot be reached by accident, and a typo in the
environment fails at boot rather than silently disabling something.
"""

from __future__ import annotations

import pytest

from app import features


def test_every_mode_in_the_brief_has_a_flag():
    # Sixteen features, and none of them reachable until they are built.
    assert len(features.DEFAULTS) == 16
    assert set(features.DEFAULTS) >= {"triage", "heatmap", "examiner", "panic", "duels"}


def test_unbuilt_modes_are_off_by_default():
    # A flag defaults on exactly when its mode is implemented.
    from app.services.modes import registry

    resolved = features.resolve("")
    assert resolved["duels"] is False
    for key, on in resolved.items():
        assert on is (key in registry.MODES), key


def test_a_named_feature_turns_on():
    resolved = features.resolve("triage")
    assert resolved["triage"] is True
    assert resolved["examiner"] is False


def test_a_built_mode_can_be_switched_off():
    assert features.resolve("-quarantine")["quarantine"] is False


def test_a_minus_prefix_turns_one_off():
    resolved = features.resolve("all,-duels")
    assert resolved["triage"] is True
    assert resolved["duels"] is False


def test_all_turns_on_everything():
    resolved = features.resolve("all")
    assert all(resolved.values())


def test_whitespace_and_case_are_forgiven():
    assert features.resolve(" Triage , HEATMAP ")["heatmap"] is True


def test_an_unknown_flag_fails_loudly():
    # Silently ignoring a typo means shipping with a mode you thought was on.
    with pytest.raises(ValueError, match="trige"):
        features.resolve("trige")


def test_settings_carry_resolved_flags(monkeypatch, tmp_path):
    from app.config import load_settings

    monkeypatch.setenv("ALOUD_FEATURES", "triage,panic")
    settings = load_settings(env_file=tmp_path / "absent.env")
    assert settings.features["triage"] is True
    assert settings.features["panic"] is True
    assert settings.features["liar"] is False
