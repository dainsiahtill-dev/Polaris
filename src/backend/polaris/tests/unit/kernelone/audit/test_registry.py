"""Unit tests for polaris.kernelone.audit.registry."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from polaris.kernelone.audit import registry as audit_registry
from polaris.kernelone.audit.registry import (
    create_audit_store,
    get_audit_store,
    has_audit_store_factory,
    set_audit_store_factory,
)


class TestRegistry:
    def setup_method(self) -> None:
        self._reset_registry()

    def teardown_method(self) -> None:
        self._reset_registry()

    @staticmethod
    def _reset_registry() -> None:
        # Reset the REGISTRY module's globals. (The old version declared
        # `global _store_factory` inside this test module, which rebinds a
        # test-module name and leaves the real registry untouched — the
        # factory then leaked across tests/modules.)
        audit_registry._store_factory = None
        audit_registry._store_cache.clear()

    def test_has_factory_false_initially(self) -> None:
        assert has_audit_store_factory() is False

    def test_set_and_has_factory(self) -> None:
        set_audit_store_factory(lambda path: MagicMock())
        assert has_audit_store_factory() is True

    def test_create_audit_store_without_factory_raises(self) -> None:
        with pytest.raises(RuntimeError, match="factory not registered"):
            create_audit_store(Path("/tmp"))

    def test_create_audit_store_with_factory(self) -> None:
        mock_store = MagicMock()
        set_audit_store_factory(lambda path: mock_store)
        result = create_audit_store(Path("/tmp"))
        assert result is mock_store

    def test_get_audit_store_caches(self) -> None:
        mock_store = MagicMock()
        set_audit_store_factory(lambda path: mock_store)
        s1 = get_audit_store(Path("/tmp"))
        s2 = get_audit_store(Path("/tmp"))
        assert s1 is s2

    def test_get_audit_store_different_paths(self) -> None:
        calls: list[Path] = []

        def factory(path: Path) -> Any:
            calls.append(path)
            return MagicMock()

        set_audit_store_factory(factory)
        get_audit_store(Path("/tmp/a"))
        get_audit_store(Path("/tmp/b"))
        assert len(calls) == 2
