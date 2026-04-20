"""Compatibility facade for anthropomorphic memory store.

Canonical implementation is hosted in ``polaris.kernelone.memory.memory_store``.
This facade keeps historical imports stable during migration.
"""

from __future__ import annotations

from polaris.kernelone.memory.memory_store import (
    BM25,
    EMBEDDING_MODEL,
    QUERY_TYPE_WEIGHTS,
    SYNONYM_DICT,
    MemoryStore,
    QueryCache,
    has_memory_refs,
)

__all__ = [
    "BM25",
    "EMBEDDING_MODEL",
    "QUERY_TYPE_WEIGHTS",
    "SYNONYM_DICT",
    "MemoryStore",
    "QueryCache",
    "has_memory_refs",
]
