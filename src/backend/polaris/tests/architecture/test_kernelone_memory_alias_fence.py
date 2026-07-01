"""Architecture fence for retired KernelOne memory helper aliases."""

from __future__ import annotations

from pathlib import Path

import polaris.kernelone.memory as memory_package
from polaris.kernelone.memory.refs import has_memory_refs

BACKEND_ROOT = Path(__file__).resolve().parents[3]
MEMORY_STORE = BACKEND_ROOT / "polaris" / "kernelone" / "memory" / "memory_store.py"
MEMORY_PACKAGE = BACKEND_ROOT / "polaris" / "kernelone" / "memory" / "__init__.py"
ANTHROPOMORPHIC_FACADE = (
    BACKEND_ROOT / "polaris" / "cells" / "runtime" / "projection" / "internal" / "anthropomorphic" / "memory_store.py"
)


def test_private_memory_refs_alias_is_retired() -> None:
    """The memory package must expose only the canonical evidence-ref helper."""
    assert memory_package.has_memory_refs is has_memory_refs
    assert not hasattr(memory_package, "_has_refs")
    assert "_has_refs" not in memory_package.__all__


def test_memory_sources_do_not_reintroduce_private_alias() -> None:
    """Source-level fence blocks the retired ``_has_refs`` compatibility name."""
    for path in (MEMORY_STORE, MEMORY_PACKAGE):
        source = path.read_text(encoding="utf-8")
        assert "def _has_refs" not in source
        assert "_has_refs = has_memory_refs" not in source
        assert '"_has_refs"' not in source


def test_anthropomorphic_facade_imports_refs_helper_directly() -> None:
    """Facade should not rely on memory_store's incidental imported name."""
    source = ANTHROPOMORPHIC_FACADE.read_text(encoding="utf-8")
    assert "from polaris.kernelone.memory.refs import has_memory_refs" in source
    assert "has_memory_refs," not in source
