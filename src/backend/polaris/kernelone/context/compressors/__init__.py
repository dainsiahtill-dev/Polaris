"""Context compressors for intelligent code and text compression.

This module provides layered compression strategies:
- CompressionRegistry: Factory pattern registry for compression strategies
- CompressionStrategy: Protocol for compression implementations
- CompressionResult / CompressionCost: Result and cost dataclasses

Design constraints:
    - All compressors are pure functions (no side effects)
    - Compression decisions are tracked for auditability
    - Token estimation is consistent with ChunkBudgetTracker
"""

from __future__ import annotations

from .registry import (
    CompressionCost,
    CompressionRegistry,
    CompressionResult,
    CompressionStrategy,
)

__all__ = [
    "CompressionCost",
    "CompressionRegistry",
    "CompressionResult",
    "CompressionStrategy",
]
