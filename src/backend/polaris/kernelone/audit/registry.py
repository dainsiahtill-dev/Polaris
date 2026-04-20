from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from .contracts import KernelAuditStorePort

_store_factory: Callable[[Path], KernelAuditStorePort] | None = None
_store_cache: dict[str, KernelAuditStorePort] = {}


def set_audit_store_factory(factory: Callable[[Path], KernelAuditStorePort]) -> None:
    """Set the factory for creating audit stores."""
    global _store_factory
    _store_factory = factory


def has_audit_store_factory() -> bool:
    """Return whether an audit store factory has been registered."""
    return _store_factory is not None


def create_audit_store(runtime_root: Path) -> KernelAuditStorePort:
    """Create an audit store using the registered factory."""
    global _store_factory
    if _store_factory is None:
        raise RuntimeError("Audit store factory not registered. It must be injected by the bootstrap layer.")
    return _store_factory(runtime_root)


def get_audit_store(runtime_root: Path) -> KernelAuditStorePort:
    """Get or create a cached audit store for a runtime root."""
    cache_key = str(runtime_root)
    if cache_key not in _store_cache:
        _store_cache[cache_key] = create_audit_store(Path(runtime_root))
    return _store_cache[cache_key]
