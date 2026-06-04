"""Tests for Context OS / Cognitive Runtime feature switches."""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

import pytest
from polaris.kernelone.context.runtime_feature_flags import (
    COGNITIVE_RUNTIME_MODE_ENV,
    CONTEXT_OS_ENABLED_ENV,
    CognitiveRuntimeMode,
    cognitive_runtime_is_enabled,
    resolve_cognitive_runtime_mode,
    resolve_context_os_enabled,
)

if TYPE_CHECKING:
    pass


class TestContextOSFeatureFlag:
    def test_default_is_enabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(CONTEXT_OS_ENABLED_ENV, raising=False)
        assert resolve_context_os_enabled(default=True) is True

    def test_env_can_disable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(CONTEXT_OS_ENABLED_ENV, "off")
        assert resolve_context_os_enabled(default=True) is False

    def test_context_override_has_higher_priority_than_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(CONTEXT_OS_ENABLED_ENV, "off")
        assert (
            resolve_context_os_enabled(
                incoming_context={"state_first_context_os_enabled": True},
                default=True,
            )
            is True
        )

    def test_strategy_override_is_supported(self) -> None:
        assert (
            resolve_context_os_enabled(
                incoming_context={
                    "strategy_override": {
                        "session_continuity": {
                            "state_first_context_os_enabled": False,
                        }
                    }
                },
                default=True,
            )
            is False
        )

    def test_canonical_env_disables(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(CONTEXT_OS_ENABLED_ENV, raising=False)
        monkeypatch.setenv(CONTEXT_OS_ENABLED_ENV, "off")
        assert resolve_context_os_enabled(default=True) is False

    def test_canonical_env_enables(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(CONTEXT_OS_ENABLED_ENV, raising=False)
        monkeypatch.setenv(CONTEXT_OS_ENABLED_ENV, "on")
        assert resolve_context_os_enabled(default=False) is True

    def test_canonical_env_takes_priority_over_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(CONTEXT_OS_ENABLED_ENV, "off")
        assert resolve_context_os_enabled(default=True) is False

    def test_invalid_env_value_falls_back_to_default(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Invalid boolean value triggers warning and falls back to default."""
        monkeypatch.setenv(CONTEXT_OS_ENABLED_ENV, "invalid_yes_no")
        with caplog.at_level(logging.WARNING):
            result = resolve_context_os_enabled(default=True)
        assert result is True
        assert "Invalid boolean value" in caplog.text
        assert CONTEXT_OS_ENABLED_ENV in caplog.text


class TestCognitiveRuntimeFeatureFlag:
    def test_default_is_mainline(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(COGNITIVE_RUNTIME_MODE_ENV, raising=False)
        assert resolve_cognitive_runtime_mode() is CognitiveRuntimeMode.MAINLINE

    def test_env_mode_can_be_mainline(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(COGNITIVE_RUNTIME_MODE_ENV, "mainline")
        assert resolve_cognitive_runtime_mode() is CognitiveRuntimeMode.MAINLINE

    def test_metadata_enabled_false_maps_to_off(self) -> None:
        assert (
            resolve_cognitive_runtime_mode(
                metadata={"cognitive_runtime_enabled": False},
            )
            is CognitiveRuntimeMode.OFF
        )

    def test_metadata_enabled_true_maps_to_mainline(self) -> None:
        assert (
            resolve_cognitive_runtime_mode(
                metadata={"cognitive_runtime_enabled": True},
            )
            is CognitiveRuntimeMode.MAINLINE
        )

    def test_explicit_mode_wins_over_boolean(self) -> None:
        assert (
            resolve_cognitive_runtime_mode(
                metadata={
                    "cognitive_runtime_mode": "mainline",
                    "cognitive_runtime_enabled": False,
                }
            )
            is CognitiveRuntimeMode.MAINLINE
        )

    def test_enabled_predicate(self) -> None:
        assert cognitive_runtime_is_enabled(CognitiveRuntimeMode.SHADOW) is True
        assert cognitive_runtime_is_enabled(CognitiveRuntimeMode.MAINLINE) is True
        assert cognitive_runtime_is_enabled(CognitiveRuntimeMode.OFF) is False

    def test_canonical_env_mainline(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(COGNITIVE_RUNTIME_MODE_ENV, raising=False)
        monkeypatch.setenv(COGNITIVE_RUNTIME_MODE_ENV, "mainline")
        assert resolve_cognitive_runtime_mode() is CognitiveRuntimeMode.MAINLINE

    def test_canonical_env_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(COGNITIVE_RUNTIME_MODE_ENV, raising=False)
        monkeypatch.setenv(COGNITIVE_RUNTIME_MODE_ENV, "off")
        assert resolve_cognitive_runtime_mode() is CognitiveRuntimeMode.OFF

    def test_canonical_env_takes_priority_over_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(COGNITIVE_RUNTIME_MODE_ENV, "off")
        assert resolve_cognitive_runtime_mode() is CognitiveRuntimeMode.OFF

    def test_legacy_polaris_env_is_normalized_at_boundary(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from polaris._env_compat import normalize_env_prefix

        previous_canonical = os.environ.pop(COGNITIVE_RUNTIME_MODE_ENV, None)
        monkeypatch.setenv("POLARIS_COGNITIVE_RUNTIME_MODE", "off")

        try:
            with pytest.warns(DeprecationWarning):
                normalize_env_prefix()

            assert os.environ[COGNITIVE_RUNTIME_MODE_ENV] == "off"
            assert resolve_cognitive_runtime_mode() is CognitiveRuntimeMode.OFF
        finally:
            if previous_canonical is None:
                os.environ.pop(COGNITIVE_RUNTIME_MODE_ENV, None)
            else:
                os.environ[COGNITIVE_RUNTIME_MODE_ENV] = previous_canonical

    def test_invalid_env_value_falls_back_to_default(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Invalid mode value triggers warning and falls back to default."""
        monkeypatch.setenv(COGNITIVE_RUNTIME_MODE_ENV, "invalid_mode")
        with caplog.at_level(logging.WARNING):
            result = resolve_cognitive_runtime_mode()
        assert result is CognitiveRuntimeMode.MAINLINE
        assert "Invalid CognitiveRuntimeMode value" in caplog.text
        assert COGNITIVE_RUNTIME_MODE_ENV in caplog.text
