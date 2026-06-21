"""Tests for Director multi-binding readiness checks in _ensure_llm_ready.

Verifies that all Director bindings are properly validated for readiness,
including edge cases like missing bindings, partial readiness, and fail-closed behavior.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException


class TestDirectorMultiBindingReadiness:
    """Tests for _ensure_llm_ready with multiple Director bindings."""

    def _make_state(self) -> Any:
        """Create a mock AppState for testing."""
        mock_settings = MagicMock()
        mock_settings.workspace = "/tmp/test_workspace"
        mock_settings.ramdisk_root = None
        from polaris.cells.runtime.state_owner.internal.state import AppState

        return AppState(settings=mock_settings)

    def test_director_all_bindings_ready_passes(self) -> None:
        """All bindings ready should pass without raising."""
        from polaris.delivery.http.routers._shared import _ensure_llm_ready

        state = self._make_state()
        config_payload = {
            "providers": {
                "openai": {"type": "openai_compat"},
                "anthropic": {"type": "anthropic_compat"},
                "gemini": {"type": "google_ai"},
            },
            "roles": {
                "director": {
                    "provider_id": "openai",
                    "model": "gpt-4",
                    "bindings": [
                        {"provider_id": "openai", "model": "gpt-4", "binding_id": "b0"},
                        {"provider_id": "anthropic", "model": "claude-3", "binding_id": "b1"},
                        {"provider_id": "gemini", "model": "gemini-pro", "binding_id": "b2"},
                    ],
                },
            },
        }

        # Create separate index entries for each binding
        ready_index = {
            "roles": {
                "director": {"ready": True, "provider_id": "openai", "model": "gpt-4"},
            },
            "providers": {
                "openai": {"ready": True, "model": "gpt-4"},
                "anthropic": {"ready": True, "model": "claude-3"},
                "gemini": {"ready": True, "model": "gemini-pro"},
            },
        }

        with (
            patch("polaris.delivery.http.routers._shared.build_cache_root", return_value="/tmp/test_cache"),
            patch("polaris.delivery.http.routers._shared.llm_config.load_llm_config", return_value=config_payload),
            patch("polaris.delivery.http.routers._shared.load_llm_test_index", return_value=ready_index),
            patch("polaris.delivery.http.routers._shared.load_llm_test_index_candidates", return_value=[]),
        ):
            # Should not raise
            _ensure_llm_ready(state, "director")

    def test_director_one_binding_not_ready_raises(self) -> None:
        """One binding not ready should raise HTTPException."""
        from polaris.delivery.http.routers._shared import _ensure_llm_ready

        state = self._make_state()
        config_payload = {
            "providers": {
                "openai": {"type": "openai_compat"},
                "anthropic": {"type": "anthropic_compat"},
            },
            "roles": {
                "director": {
                    "provider_id": "openai",
                    "model": "gpt-4",
                    "bindings": [
                        {"provider_id": "openai", "model": "gpt-4", "binding_id": "b0"},
                        {"provider_id": "anthropic", "model": "claude-3", "binding_id": "b1"},
                    ],
                },
            },
        }

        # Only openai is ready, anthropic is not
        ready_index = {
            "roles": {
                "director": {"ready": True, "provider_id": "openai", "model": "gpt-4"},
            },
            "providers": {},
        }

        with (
            patch("polaris.delivery.http.routers._shared.build_cache_root", return_value="/tmp/test_cache"),
            patch("polaris.delivery.http.routers._shared.llm_config.load_llm_config", return_value=config_payload),
            patch("polaris.delivery.http.routers._shared.load_llm_test_index", return_value=ready_index),
            patch("polaris.delivery.http.routers._shared.load_llm_test_index_candidates", return_value=[]),
            pytest.raises(HTTPException) as exc_info,
        ):
            _ensure_llm_ready(state, "director")

        assert exc_info.value.status_code == 409
        assert "binding b1" in str(exc_info.value.detail)
        assert "anthropic" in str(exc_info.value.detail)

    def test_director_binding_with_empty_provider_skipped(self) -> None:
        """Bindings with empty provider_id should be skipped."""
        from polaris.delivery.http.routers._shared import _ensure_llm_ready

        state = self._make_state()
        config_payload = {
            "providers": {
                "openai": {"type": "openai_compat"},
            },
            "roles": {
                "director": {
                    "provider_id": "openai",
                    "model": "gpt-4",
                    "bindings": [
                        {"provider_id": "openai", "model": "gpt-4", "binding_id": "b0"},
                        {"provider_id": "", "model": "", "binding_id": "b1"},  # Empty binding
                    ],
                },
            },
        }

        ready_index = {
            "roles": {
                "director": {"ready": True, "provider_id": "openai", "model": "gpt-4"},
            },
            "providers": {},
        }

        with (
            patch("polaris.delivery.http.routers._shared.build_cache_root", return_value="/tmp/test_cache"),
            patch("polaris.delivery.http.routers._shared.llm_config.load_llm_config", return_value=config_payload),
            patch("polaris.delivery.http.routers._shared.load_llm_test_index", return_value=ready_index),
            patch("polaris.delivery.http.routers._shared.load_llm_test_index_candidates", return_value=[]),
        ):
            # Should not raise - empty binding is skipped
            _ensure_llm_ready(state, "director")

    def test_director_binding_missing_ready_status_raises(self) -> None:
        """Binding with missing ready status should raise HTTPException."""
        from polaris.delivery.http.routers._shared import _ensure_llm_ready

        state = self._make_state()
        config_payload = {
            "providers": {
                "openai": {"type": "openai_compat"},
            },
            "roles": {
                "director": {
                    "provider_id": "openai",
                    "model": "gpt-4",
                    "bindings": [
                        {"provider_id": "openai", "model": "gpt-4", "binding_id": "b0"},
                    ],
                },
            },
        }

        # No director role in index
        ready_index = {
            "roles": {},
            "providers": {},
        }

        with (
            patch("polaris.delivery.http.routers._shared.build_cache_root", return_value="/tmp/test_cache"),
            patch("polaris.delivery.http.routers._shared.llm_config.load_llm_config", return_value=config_payload),
            patch("polaris.delivery.http.routers._shared.load_llm_test_index", return_value=ready_index),
            patch("polaris.delivery.http.routers._shared.load_llm_test_index_candidates", return_value=[]),
            pytest.raises(HTTPException) as exc_info,
        ):
            _ensure_llm_ready(state, "director")

        assert exc_info.value.status_code == 409
        assert "not ready" in str(exc_info.value.detail).lower()

    def test_director_binding_with_none_bindings_uses_single_check(self) -> None:
        """When bindings is None, should fall back to single provider/model check."""
        from polaris.delivery.http.routers._shared import _ensure_llm_ready

        state = self._make_state()
        config_payload = {
            "providers": {
                "openai": {"type": "openai_compat"},
            },
            "roles": {
                "director": {
                    "provider_id": "openai",
                    "model": "gpt-4",
                    # No bindings key
                },
            },
        }

        ready_index = {
            "roles": {
                "director": {"ready": True, "provider_id": "openai", "model": "gpt-4"},
            },
            "providers": {},
        }

        with (
            patch("polaris.delivery.http.routers._shared.build_cache_root", return_value="/tmp/test_cache"),
            patch("polaris.delivery.http.routers._shared.llm_config.load_llm_config", return_value=config_payload),
            patch("polaris.delivery.http.routers._shared.load_llm_test_index", return_value=ready_index),
            patch("polaris.delivery.http.routers._shared.load_llm_test_index_candidates", return_value=[]),
        ):
            # Should not raise
            _ensure_llm_ready(state, "director")

    def test_director_binding_with_empty_list_uses_single_check(self) -> None:
        """When bindings is empty list, should fall back to single provider/model check."""
        from polaris.delivery.http.routers._shared import _ensure_llm_ready

        state = self._make_state()
        config_payload = {
            "providers": {
                "openai": {"type": "openai_compat"},
            },
            "roles": {
                "director": {
                    "provider_id": "openai",
                    "model": "gpt-4",
                    "bindings": [],  # Empty list
                },
            },
        }

        ready_index = {
            "roles": {
                "director": {"ready": True, "provider_id": "openai", "model": "gpt-4"},
            },
            "providers": {},
        }

        with (
            patch("polaris.delivery.http.routers._shared.build_cache_root", return_value="/tmp/test_cache"),
            patch("polaris.delivery.http.routers._shared.llm_config.load_llm_config", return_value=config_payload),
            patch("polaris.delivery.http.routers._shared.load_llm_test_index", return_value=ready_index),
            patch("polaris.delivery.http.routers._shared.load_llm_test_index_candidates", return_value=[]),
        ):
            # Should not raise
            _ensure_llm_ready(state, "director")

    def test_director_binding_provider_status_ready_passes(self) -> None:
        """Binding with provider_status ready should pass."""
        from polaris.delivery.http.routers._shared import _ensure_llm_ready

        state = self._make_state()
        config_payload = {
            "providers": {
                "openai": {"type": "openai_compat"},
            },
            "roles": {
                "director": {
                    "provider_id": "openai",
                    "model": "gpt-4",
                    "bindings": [
                        {"provider_id": "openai", "model": "gpt-4", "binding_id": "b0"},
                    ],
                },
            },
        }

        # Role status not ready, but provider status ready
        ready_index = {
            "roles": {},
            "providers": {
                "openai": {"ready": True, "model": "gpt-4"},
            },
        }

        with (
            patch("polaris.delivery.http.routers._shared.build_cache_root", return_value="/tmp/test_cache"),
            patch("polaris.delivery.http.routers._shared.llm_config.load_llm_config", return_value=config_payload),
            patch("polaris.delivery.http.routers._shared.load_llm_test_index", return_value=ready_index),
            patch("polaris.delivery.http.routers._shared.load_llm_test_index_candidates", return_value=[]),
        ):
            # Should not raise
            _ensure_llm_ready(state, "director")

    def test_director_binding_provider_status_not_ready_raises(self) -> None:
        """Binding with provider_status not ready should raise HTTPException."""
        from polaris.delivery.http.routers._shared import _ensure_llm_ready

        state = self._make_state()
        config_payload = {
            "providers": {
                "openai": {"type": "openai_compat"},
            },
            "roles": {
                "director": {
                    "provider_id": "openai",
                    "model": "gpt-4",
                    "bindings": [
                        {"provider_id": "openai", "model": "gpt-4", "binding_id": "b0"},
                    ],
                },
            },
        }

        # Neither role nor provider status ready
        ready_index = {
            "roles": {},
            "providers": {},
        }

        with (
            patch("polaris.delivery.http.routers._shared.build_cache_root", return_value="/tmp/test_cache"),
            patch("polaris.delivery.http.routers._shared.llm_config.load_llm_config", return_value=config_payload),
            patch("polaris.delivery.http.routers._shared.load_llm_test_index", return_value=ready_index),
            patch("polaris.delivery.http.routers._shared.load_llm_test_index_candidates", return_value=[]),
            pytest.raises(HTTPException) as exc_info,
        ):
            _ensure_llm_ready(state, "director")

        assert exc_info.value.status_code == 409

    def test_director_binding_multiple_failures_reports_first(self) -> None:
        """Multiple binding failures should report the first one encountered."""
        from polaris.delivery.http.routers._shared import _ensure_llm_ready

        state = self._make_state()
        config_payload = {
            "providers": {
                "openai": {"type": "openai_compat"},
                "anthropic": {"type": "anthropic_compat"},
                "gemini": {"type": "google_ai"},
            },
            "roles": {
                "director": {
                    "provider_id": "openai",
                    "model": "gpt-4",
                    "bindings": [
                        {"provider_id": "openai", "model": "gpt-4", "binding_id": "b0"},
                        {"provider_id": "anthropic", "model": "claude-3", "binding_id": "b1"},
                        {"provider_id": "gemini", "model": "gemini-pro", "binding_id": "b2"},
                    ],
                },
            },
        }

        # Only openai is ready
        ready_index = {
            "roles": {
                "director": {"ready": True, "provider_id": "openai", "model": "gpt-4"},
            },
            "providers": {},
        }

        with (
            patch("polaris.delivery.http.routers._shared.build_cache_root", return_value="/tmp/test_cache"),
            patch("polaris.delivery.http.routers._shared.llm_config.load_llm_config", return_value=config_payload),
            patch("polaris.delivery.http.routers._shared.load_llm_test_index", return_value=ready_index),
            patch("polaris.delivery.http.routers._shared.load_llm_test_index_candidates", return_value=[]),
            pytest.raises(HTTPException) as exc_info,
        ):
            _ensure_llm_ready(state, "director")

        assert exc_info.value.status_code == 409
        # Should report the first failing binding
        assert "binding b1" in str(exc_info.value.detail)

    def test_director_binding_with_tested_provider_model_passes(self) -> None:
        """Binding with tested provider/model should pass."""
        from polaris.delivery.http.routers._shared import _ensure_llm_ready

        state = self._make_state()
        config_payload = {
            "providers": {
                "openai": {"type": "openai_compat"},
            },
            "roles": {
                "director": {
                    "provider_id": "openai",
                    "model": "gpt-4",
                    "bindings": [
                        {"provider_id": "openai", "model": "gpt-4", "binding_id": "b0"},
                    ],
                },
            },
        }

        # Role status with different tested provider/model
        ready_index = {
            "roles": {
                "director": {
                    "ready": True,
                    "provider_id": "openai",
                    "model": "gpt-4",
                    "tested_provider_id": "openai",
                    "tested_model": "gpt-4",
                    "timestamp": "2026-06-21T00:00:00Z",
                },
            },
            "providers": {},
        }

        with (
            patch("polaris.delivery.http.routers._shared.build_cache_root", return_value="/tmp/test_cache"),
            patch("polaris.delivery.http.routers._shared.llm_config.load_llm_config", return_value=config_payload),
            patch("polaris.delivery.http.routers._shared.load_llm_test_index", return_value=ready_index),
            patch("polaris.delivery.http.routers._shared.load_llm_test_index_candidates", return_value=[]),
        ):
            # Should not raise
            _ensure_llm_ready(state, "director")

    def test_director_binding_with_mixed_ready_not_ready_raises(self) -> None:
        """Mix of ready and not ready bindings should raise HTTPException."""
        from polaris.delivery.http.routers._shared import _ensure_llm_ready

        state = self._make_state()
        config_payload = {
            "providers": {
                "openai": {"type": "openai_compat"},
                "anthropic": {"type": "anthropic_compat"},
            },
            "roles": {
                "director": {
                    "provider_id": "openai",
                    "model": "gpt-4",
                    "bindings": [
                        {"provider_id": "openai", "model": "gpt-4", "binding_id": "b0"},
                        {"provider_id": "anthropic", "model": "claude-3", "binding_id": "b1"},
                    ],
                },
            },
        }

        # openai ready, anthropic not ready
        ready_index = {
            "roles": {
                "director": {"ready": True, "provider_id": "openai", "model": "gpt-4"},
            },
            "providers": {},
        }

        with (
            patch("polaris.delivery.http.routers._shared.build_cache_root", return_value="/tmp/test_cache"),
            patch("polaris.delivery.http.routers._shared.llm_config.load_llm_config", return_value=config_payload),
            patch("polaris.delivery.http.routers._shared.load_llm_test_index", return_value=ready_index),
            patch("polaris.delivery.http.routers._shared.load_llm_test_index_candidates", return_value=[]),
            pytest.raises(HTTPException) as exc_info,
        ):
            _ensure_llm_ready(state, "director")

        assert exc_info.value.status_code == 409
        assert "binding b1" in str(exc_info.value.detail)
        assert "anthropic" in str(exc_info.value.detail)

    def test_director_binding_with_invalid_binding_format_skipped(self) -> None:
        """Bindings with invalid format (not dict) should be skipped."""
        from polaris.delivery.http.routers._shared import _ensure_llm_ready

        state = self._make_state()
        config_payload = {
            "providers": {
                "openai": {"type": "openai_compat"},
            },
            "roles": {
                "director": {
                    "provider_id": "openai",
                    "model": "gpt-4",
                    "bindings": [
                        {"provider_id": "openai", "model": "gpt-4", "binding_id": "b0"},
                        "invalid_binding",  # Not a dict
                        123,  # Not a dict
                    ],
                },
            },
        }

        ready_index = {
            "roles": {
                "director": {"ready": True, "provider_id": "openai", "model": "gpt-4"},
            },
            "providers": {},
        }

        with (
            patch("polaris.delivery.http.routers._shared.build_cache_root", return_value="/tmp/test_cache"),
            patch("polaris.delivery.http.routers._shared.llm_config.load_llm_config", return_value=config_payload),
            patch("polaris.delivery.http.routers._shared.load_llm_test_index", return_value=ready_index),
            patch("polaris.delivery.http.routers._shared.load_llm_test_index_candidates", return_value=[]),
        ):
            # Should not raise - invalid bindings are skipped
            _ensure_llm_ready(state, "director")
