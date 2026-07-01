"""Public inspection contracts for Director Runtime repair-kernel facts.

This module intentionally exposes only stable constants and pure helper
functions. It does not expose repair planners, runners, policy gates, or any
mutation authority. Cross-cell adapters and tests can use this surface to
verify repair receipts without importing ``director.runtime.internal``.
"""

from __future__ import annotations

from polaris.cells.director.runtime.internal.repair_kernel.contracts import (
    FILE_ABSENT_HASH as _FILE_ABSENT_HASH,
    sha256_text as _sha256_text,
)
from polaris.cells.director.runtime.internal.repair_kernel.generic_hygiene_syntax import (
    remove_patch_residue_lines as _remove_patch_residue_lines,
)
from polaris.cells.director.runtime.internal.repair_kernel.javascript_syntax import (
    _is_overstrict_node_test_script_contract,
    build_substantive_node_test_script as _build_substantive_node_test_script,
)
from polaris.cells.director.runtime.internal.repair_kernel.rust_syntax import (
    RUST_DUPLICATE_MODULE_FILE_SOURCE_TOOL as _RUST_DUPLICATE_MODULE_FILE_SOURCE_TOOL,
    RUST_MISSING_MODULE_FILE_SOURCE_TOOL as _RUST_MISSING_MODULE_FILE_SOURCE_TOOL,
    RUST_MISSING_MODULE_FILE_STUB as _RUST_MISSING_MODULE_FILE_STUB,
)

FILE_ABSENT_HASH: str = _FILE_ABSENT_HASH
RUST_DUPLICATE_MODULE_FILE_SOURCE_TOOL: str = _RUST_DUPLICATE_MODULE_FILE_SOURCE_TOOL
RUST_MISSING_MODULE_FILE_SOURCE_TOOL: str = _RUST_MISSING_MODULE_FILE_SOURCE_TOOL
RUST_MISSING_MODULE_FILE_STUB: str = _RUST_MISSING_MODULE_FILE_STUB


def sha256_text(value: str) -> str:
    """Return the stable Director repair-kernel UTF-8 SHA-256 text hash."""

    return _sha256_text(value)


def remove_patch_residue_lines(text: str) -> str:
    """Return source text with leaked patch-protocol residue lines removed."""

    return _remove_patch_residue_lines(text)


def build_substantive_node_test_script() -> str:
    """Return the deterministic Node test script used by repair receipts."""

    return _build_substantive_node_test_script()


def is_overstrict_node_test_script_contract(script_text: str) -> bool:
    """Return whether generated Node test text matches the over-strict contract."""

    return _is_overstrict_node_test_script_contract(script_text)


__all__ = [
    "FILE_ABSENT_HASH",
    "RUST_DUPLICATE_MODULE_FILE_SOURCE_TOOL",
    "RUST_MISSING_MODULE_FILE_SOURCE_TOOL",
    "RUST_MISSING_MODULE_FILE_STUB",
    "build_substantive_node_test_script",
    "is_overstrict_node_test_script_contract",
    "remove_patch_residue_lines",
    "sha256_text",
]
