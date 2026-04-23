"""Akashic Nexus: Integration Module.

Provides integration helpers for bootstrapping the Akashic memory engine
with existing KernelOne subsystems.

Usage::

    from polaris.kernelone.akashic.integration import (
        create_memory_manager,
        get_default_manager,
    )

    # Create and initialize
    manager = await create_memory_manager(workspace=".")
    await manager.initialize()

    # Use working memory
    manager.working_memory.push("user", "Fix the bug")

    # Check status
    status = manager.get_status()

    # Shutdown when done
    await manager.shutdown()
"""

from __future__ import annotations

import logging
import os
from typing import Any

from polaris.kernelone.memory.memory_store import MemoryStore

from .compression_daemon import CompressionDaemon, DaemonConfig
from .memory_manager import MemoryManager, MemoryManagerConfig
from .protocols import MemoryManagerPort
from .semantic_cache import SemanticCacheInterceptor
from .working_memory import WorkingMemoryWindow

logger = logging.getLogger(__name__)

# Global singleton (for backwards compatibility)
_default_manager: MemoryManager | None = None
_daemon: CompressionDaemon | None = None


async def create_memory_manager(
    workspace: str = ".",
    *,
    config: MemoryManagerConfig | None = None,
    enable_daemon: bool = True,
) -> MemoryManager:
    """Create and configure a MemoryManager with all tiers.

    This is the canonical way to create a fully configured Akashic memory manager.

    Args:
        workspace: The workspace path for persistence
        config: Optional MemoryManager configuration
        enable_daemon: Whether to start the compression daemon

    Returns:
        A fully initialized MemoryManager instance
    """
    # Load legacy memory store if available
    legacy_store = None
    memory_file = os.path.join(workspace, "brain", "MEMORY.jsonl")
    # Only load if both directory and file exist
    if os.path.exists(os.path.dirname(memory_file)) and os.path.exists(memory_file):
        try:
            legacy_store = MemoryStore(memory_file=memory_file)
        except (RuntimeError, ValueError) as exc:
            logger.warning("Failed to load legacy MemoryStore: %s", exc)

    # Create manager with DI
    manager = MemoryManager(
        config=config,
        workspace=workspace,
        working_memory=WorkingMemoryWindow(),
        semantic_cache=SemanticCacheInterceptor(),
        legacy_memory_store=legacy_store,  # type: ignore[arg-type]  # MemoryStore is compatible via adapter
    )

    # Start compression daemon if enabled
    if enable_daemon:
        daemon = CompressionDaemon(
            memory_manager=manager,
            config=DaemonConfig(),
            workspace=workspace,
        )
        await daemon.start()
        globals()["_daemon"] = daemon

    return manager


async def get_default_manager() -> MemoryManager:
    """Get or create the default singleton MemoryManager.

    This is for backwards compatibility. Prefer create_memory_manager()
    for explicit configuration.
    """
    global _default_manager

    if _default_manager is None:
        workspace = os.environ.get("KERNELONE_WORKSPACE") or "."
        _default_manager = await create_memory_manager(workspace)

    return _default_manager


async def shutdown_default_manager() -> None:
    """Shutdown the default singleton manager and daemon.

    Call this during application shutdown.
    """
    global _default_manager, _daemon

    if _daemon is not None:
        await _daemon.stop()
        _daemon = None

    if _default_manager is not None:
        await _default_manager.shutdown()
        _default_manager = None


def get_memory_manager_status() -> dict[str, Any]:
    """Get status of the default memory manager.

    Returns empty dict if manager not initialized.
    """
    global _default_manager, _daemon

    status: dict[str, Any] = {
        "manager_initialized": _default_manager is not None,
        "daemon_running": _daemon is not None,
    }

    if _daemon is not None:
        status["daemon"] = _daemon.get_status()

    if _default_manager is not None:
        status["manager"] = _default_manager.get_status()

    return status


async def inject_into_context(context: Any) -> None:
    """Inject the default memory manager into a context object.

    This provides backwards compatibility with existing code that expects
    memory to be available via a context attribute.
    """
    manager = await get_default_manager()

    if hasattr(context, "__dict__"):
        context.memory_manager = manager


__all__ = [
    "CompressionDaemon",
    "DaemonConfig",
    # Core
    "MemoryManager",
    "MemoryManagerConfig",
    "MemoryManagerPort",
    "SemanticCacheInterceptor",
    # Sub-systems
    "WorkingMemoryWindow",
    # Integration
    "create_memory_manager",
    "get_default_manager",
    "get_memory_manager_status",
    "inject_into_context",
    "shutdown_default_manager",
]
