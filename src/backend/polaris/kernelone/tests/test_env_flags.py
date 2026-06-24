"""Tests for strict environment feature-flag resolution."""

from __future__ import annotations

import pytest

from polaris.kernelone.env_flags import resolve_env_flag


def test_unset_flag_fails_closed_by_default() -> None:
    decision = resolve_env_flag(("FEATURE_A",), environ={})

    assert decision.enabled is False
    assert decision.reason == "default_false"
    assert decision.configured_names == ()


@pytest.mark.parametrize("raw", ["1", "true", "TRUE", " yes ", "on"])
def test_explicit_true_values_enable_flag(raw: str) -> None:
    decision = resolve_env_flag(("FEATURE_A",), environ={"FEATURE_A": raw})

    assert decision.enabled is True
    assert decision.reason == "explicit_true"


@pytest.mark.parametrize("raw", ["0", "false", "FALSE", " no ", "off"])
def test_explicit_false_values_disable_flag(raw: str) -> None:
    decision = resolve_env_flag(("FEATURE_A",), environ={"FEATURE_A": raw})

    assert decision.enabled is False
    assert decision.reason == "explicit_false"


def test_invalid_value_fails_closed() -> None:
    decision = resolve_env_flag(("FEATURE_A",), environ={"FEATURE_A": "development"})

    assert decision.enabled is False
    assert decision.reason == "invalid_value"


def test_consistent_aliases_are_accepted() -> None:
    decision = resolve_env_flag(
        ("FEATURE_A", "FEATURE_A_LEGACY"),
        environ={"FEATURE_A": "1", "FEATURE_A_LEGACY": "true"},
    )

    assert decision.enabled is True
    assert decision.configured_names == ("FEATURE_A", "FEATURE_A_LEGACY")


def test_conflicting_aliases_fail_closed() -> None:
    decision = resolve_env_flag(
        ("FEATURE_A", "FEATURE_A_LEGACY"),
        environ={"FEATURE_A": "1", "FEATURE_A_LEGACY": "0"},
    )

    assert decision.enabled is False
    assert decision.reason == "conflicting_aliases"


def test_empty_name_list_is_rejected() -> None:
    with pytest.raises(ValueError, match="at least one"):
        resolve_env_flag((), environ={})
