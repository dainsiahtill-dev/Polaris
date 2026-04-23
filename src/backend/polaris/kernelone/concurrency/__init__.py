"""Unified Concurrency Management for Polaris.

This module provides centralized thread pool and process pool management
to avoid resource fragmentation and ensure consistent并发控制 across the codebase.

Usage:
    from polaris.kernelone.concurrency import get_concurrency_manager

    manager = get_concurrency_manager()
    io_pool = manager.get_io_pool()
    cpu_pool = manager.get_cpu_pool()
"""

from __future__ import annotations

from polaris.kernelone.concurrency.manager import (
    ConcurrencyPoolConfig,
    ConcurrencyPoolType,
    UnifiedConcurrencyManager,
    get_concurrency_manager,
)

__all__ = [
    "ConcurrencyPoolConfig",
    "ConcurrencyPoolType",
    "UnifiedConcurrencyManager",
    "get_concurrency_manager",
]
