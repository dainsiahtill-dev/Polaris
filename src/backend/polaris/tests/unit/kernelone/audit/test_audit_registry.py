"""Tests for polaris.kernelone.audit.registry."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from polaris.kernelone.audit import registry as audit_registry
from polaris.kernelone.audit.registry import (
    create_audit_store,
    get_audit_store,
    has_audit_store_factory,
    set_audit_store_factory,
)


@pytest.fixture(autouse=True)
def _isolated_registry():
    """Reset the registry module's globals around every test.

    This file used to set factories without cleanup (polluting other test
    modules' "false initially" assertions) and read ``_store_factory`` via a
    from-import, which binds the value at import time and never observes
    later ``set_audit_store_factory`` calls.
    """
    audit_registry._store_factory = None
    audit_registry._store_cache.clear()
    yield
    audit_registry._store_factory = None
    audit_registry._store_cache.clear()


class TestSetAuditStoreFactory:
    def test_sets_global(self) -> None:
        mock_factory = MagicMock()
        set_audit_store_factory(mock_factory)
        assert audit_registry._store_factory is mock_factory


class TestHasAuditStoreFactory:
    def test_true_after_set(self) -> None:
        set_audit_store_factory(MagicMock())
        assert has_audit_store_factory() is True

    def test_false_initially(self) -> None:
        assert has_audit_store_factory() is False


class TestCreateAuditStore:
    def test_raises_when_not_set(self) -> None:
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
        result1 = get_audit_store(Path("/tmp"))
        result2 = get_audit_store(Path("/tmp"))
        assert result1 is mock_store
        assert result2 is mock_store
        mock_factory.assert_called_once()
