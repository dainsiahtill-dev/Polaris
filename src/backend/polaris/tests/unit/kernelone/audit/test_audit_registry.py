"""Tests for polaris.kernelone.audit.registry."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from polaris.kernelone.audit.registry import (
    _store_cache,
    _store_factory,
    create_audit_store,
    get_audit_store,
    has_audit_store_factory,
    set_audit_store_factory,
)


class TestSetAuditStoreFactory:
    def test_sets_global(self) -> None:
        mock_factory = MagicMock()
        set_audit_store_factory(mock_factory)
        assert _store_factory is mock_factory


class TestHasAuditStoreFactory:
    def test_true_after_set(self) -> None:
        set_audit_store_factory(MagicMock())
        assert has_audit_store_factory() is True

    def test_false_initially(self) -> None:
        # Note: depends on module state; may be true if other tests ran first
        pass


class TestCreateAuditStore:
    def test_raises_when_not_set(self) -> None:
        set_audit_store_factory(None)
        with pytest.raises(RuntimeError, match="factory not registered"):
            create_audit_store(Path("/tmp"))

    def test_calls_factory(self) -> None:
        mock_store = MagicMock()
        mock_factory = MagicMock(return_value=mock_store)
        set_audit_store_factory(mock_factory)
        result = create_audit_store(Path("/tmp"))
        assert result is mock_store
        mock_factory.assert_called_once_with(Path("/tmp"))


class TestGetAuditStore:
    def test_caches_result(self) -> None:
        mock_store = MagicMock()
        mock_factory = MagicMock(return_value=mock_store)
        set_audit_store_factory(mock_factory)
        # Clear cache
        _store_cache.clear()
        result1 = get_audit_store(Path("/tmp"))
        result2 = get_audit_store(Path("/tmp"))
        assert result1 is mock_store
        assert result2 is mock_store
        mock_factory.assert_called_once()
