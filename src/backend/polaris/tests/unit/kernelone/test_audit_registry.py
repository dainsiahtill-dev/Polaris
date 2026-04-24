"""Tests for polaris.kernelone.audit.registry."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from polaris.kernelone.audit.registry import (
    create_audit_store,
    get_audit_store,
    has_audit_store_factory,
    set_audit_store_factory,
)


class TestAuditRegistry:
    def setup_method(self) -> None:
        # Ensure clean global state before each test
        import polaris.kernelone.audit.registry as reg
        reg._store_factory = None
        reg._store_cache.clear()

    def teardown_method(self) -> None:
        # Clean up global state after each test
        import polaris.kernelone.audit.registry as reg
        reg._store_factory = None
        reg._store_cache.clear()

    def test_has_audit_store_factory_false(self) -> None:
        assert has_audit_store_factory() is False

    def test_has_audit_store_factory_true(self) -> None:
        set_audit_store_factory(lambda p: MagicMock())
        assert has_audit_store_factory() is True

    def test_create_audit_store_without_factory(self) -> None:
        with pytest.raises(RuntimeError, match="factory not registered"):
            create_audit_store(Path("/tmp"))

    def test_create_audit_store_with_factory(self) -> None:
        mock_store = MagicMock()
        set_audit_store_factory(lambda p: mock_store)
        result = create_audit_store(Path("/tmp"))
        assert result is mock_store

    def test_get_audit_store_caches(self) -> None:
        mock_store = MagicMock()
        set_audit_store_factory(lambda p: mock_store)
        result1 = get_audit_store(Path("/tmp"))
        result2 = get_audit_store(Path("/tmp"))
        assert result1 is mock_store
        assert result1 is result2
