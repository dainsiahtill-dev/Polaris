"""Tests for polaris.kernelone.fs.registry."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from polaris.kernelone.fs.registry import (
    _ensure_default_adapter,
    get_default_adapter,
    reset_default_adapter,
    set_adapter_factory,
    set_default_adapter,
)


class TestSetAdapterFactory:
    def test_sets_factory(self) -> None:
        mock_factory = MagicMock()
        set_adapter_factory(mock_factory)
        # Verify by checking internal state via get_default_adapter fallback
        # Reset first to clear any previously set adapter
        reset_default_adapter()
        # After reset, _adapter_factory should still be set
        from polaris.kernelone.fs import registry

        assert registry._adapter_factory is mock_factory


class TestSetDefaultAdapter:
    def test_sets_adapter_directly(self) -> None:
        mock_adapter = MagicMock()
        set_default_adapter(mock_adapter)
        assert get_default_adapter() is mock_adapter


class TestResetDefaultAdapter:
    def test_clears_adapter(self) -> None:
        mock_adapter = MagicMock()
        set_default_adapter(mock_adapter)
        reset_default_adapter()
        from polaris.kernelone.fs import registry

        assert registry._default_adapter is None


class TestGetDefaultAdapter:
    def test_returns_set_adapter(self) -> None:
        mock_adapter = MagicMock()
        set_default_adapter(mock_adapter)
        result = get_default_adapter()
        assert result is mock_adapter

    def test_lazy_initialization_with_factory(self) -> None:
        reset_default_adapter()
        from polaris.kernelone.fs import registry

        registry._initialization_attempted = False
        mock_adapter = MagicMock()
        mock_factory = MagicMock(return_value=mock_adapter)
        set_adapter_factory(mock_factory)
        result = get_default_adapter()
        assert result is mock_adapter
        mock_factory.assert_called_once()

    def test_lazy_initialization_factory_raises_fallback(self) -> None:
        reset_default_adapter()
        from polaris.kernelone.fs import registry

        registry._initialization_attempted = False
        mock_factory = MagicMock(side_effect=RuntimeError("factory failed"))
        set_adapter_factory(mock_factory)
        # If local_fs_adapter is available, it will fall back
        try:
            result = get_default_adapter()
            # Fallback succeeded
            assert result is not None
        except RuntimeError as exc:
            # Fallback also failed (expected in some test environments)
            assert "lazy initialization failed" in str(exc)

    def test_raises_when_no_adapter_and_no_factory(self) -> None:
        reset_default_adapter()
        from polaris.kernelone.fs import registry

        registry._adapter_factory = None
        registry._initialization_attempted = False
        with pytest.raises(RuntimeError, match="lazy initialization failed"):
            get_default_adapter()

    def test_thread_safety_basic(self) -> None:
        mock_adapter = MagicMock()
        set_default_adapter(mock_adapter)
        # Multiple calls should return same instance
        assert get_default_adapter() is get_default_adapter()


class TestEnsureDefaultAdapter:
    def test_noop_when_already_initialized(self) -> None:
        mock_adapter = MagicMock()
        set_default_adapter(mock_adapter)
        from polaris.kernelone.fs import registry

        registry._initialization_attempted = True
        _ensure_default_adapter()
        assert get_default_adapter() is mock_adapter

    def test_noop_when_already_attempted(self) -> None:
        reset_default_adapter()
        from polaris.kernelone.fs import registry

        registry._initialization_attempted = True
        _ensure_default_adapter()
        assert registry._default_adapter is None


class TestModuleStateIsolation:
    def test_factory_and_adapter_are_independent(self) -> None:
        reset_default_adapter()
        from polaris.kernelone.fs import registry

        registry._initialization_attempted = False
        factory_adapter = MagicMock()
        direct_adapter = MagicMock()

        set_adapter_factory(lambda: factory_adapter)
        set_default_adapter(direct_adapter)

        # Direct adapter takes precedence
        assert get_default_adapter() is direct_adapter

        reset_default_adapter()
        registry._initialization_attempted = False
        # Now factory should be used
        assert get_default_adapter() is factory_adapter
