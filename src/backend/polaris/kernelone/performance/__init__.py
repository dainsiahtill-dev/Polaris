"""Performance optimization module for KernelOne.

This module provides performance optimization utilities including:
- Caching with TTL support
- Lock-free data structures
- Performance metrics collection
"""

from polaris.kernelone.performance.optimizer import (
    CacheStats,
    PerformanceMetrics,
    PerformanceOptimizer,
)

__all__ = [
    "CacheStats",
    "PerformanceMetrics",
    "PerformanceOptimizer",
]
