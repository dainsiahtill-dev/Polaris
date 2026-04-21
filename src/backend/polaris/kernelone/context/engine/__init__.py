"""Context engine module.

This module provides context management for polaris.
"""

from polaris.kernelone.utils.time_utils import _utc_now

from .cache import ContextCache
from .engine import ContextEngine
from .models import ContextBudget, ContextItem, ContextPack, ContextRequest
from .providers import (
    BaseProvider,
    ContractProvider,
    DocsProvider,
    EventsProvider,
    MemoryProvider,
    RepoEvidenceProvider,
    RepoMapProvider,
)
from .utils import (
    _estimate_tokens,
    _hash_text,
    _read_slice_spec,
    _read_tail_lines,
    _safe_json,
)

__all__ = [
    "BaseProvider",
    "ContextBudget",
    "ContextCache",
    "ContextEngine",
    "ContextItem",
    "ContextPack",
    "ContextRequest",
    "ContractProvider",
    "DocsProvider",
    "EventsProvider",
    "MemoryProvider",
    "RepoEvidenceProvider",
    "RepoMapProvider",
    "_estimate_tokens",
    "_hash_text",
    "_read_slice_spec",
    "_read_tail_lines",
    "_safe_json",
    "_utc_now",
]
