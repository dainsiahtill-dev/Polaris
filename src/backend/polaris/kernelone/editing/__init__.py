"""KernelOne canonical code-editing capabilities.

This package is the normalized editing surface for patch/search-replace/editblock/
whole-file style operations. It is intentionally separate from LLM tool-calling
protocols:

- tool calling decides *when* and *which* editing tool to invoke
- ``polaris.kernelone.editing`` decides *how* rich edit payloads are normalized

Do not mix text-wrapper tool protocols into this package.
"""

from __future__ import annotations

from polaris.kernelone.editing.editblock_engine import extract_edit_blocks
from polaris.kernelone.editing.operation_router import route_edit_operations
from polaris.kernelone.editing.patch_engine import RoutedOperation, extract_apply_patch_operations
from polaris.kernelone.editing.search_replace_engine import (
    apply_edit_with_metadata,
    apply_fuzzy_search_replace,
)
from polaris.kernelone.editing.unified_diff_engine import extract_unified_diff_edits
from polaris.kernelone.editing.wholefile_engine import extract_wholefile_blocks

__all__ = [
    "RoutedOperation",
    "apply_edit_with_metadata",
    "apply_fuzzy_search_replace",
    "extract_apply_patch_operations",
    "extract_edit_blocks",
    "extract_unified_diff_edits",
    "extract_wholefile_blocks",
    "route_edit_operations",
]
