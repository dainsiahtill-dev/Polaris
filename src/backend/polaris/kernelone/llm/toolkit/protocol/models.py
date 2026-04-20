"""Data models for the protocol module.

Contains dataclasses for file operations, validation results, and reports.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any

from polaris.kernelone.llm.toolkit.protocol.constants import EditType, ErrorCode


def _normalize_path(text: str) -> str:
    """Normalize path: unify separators, remove redundant parts, security check."""
    if not text:
        return ""
    path = text.strip().strip("'`\"").strip()
    # Remove trailing comments (only # at line start, not inside quotes)
    import re

    path = re.sub(r"(?:^|[^\"'])#.*$", "", path).strip()
    path = re.sub(r"(?:^|[^\"'])//.*$", "", path).strip()
    # Unify separators
    path = path.replace("\\", "/")
    # Remove ./ prefix
    while path.startswith("./"):
        path = path[2:]
    # Remove duplicate slashes
    path = re.sub(r"/+", "/", path)
    # Remove trailing slash (keep leading slash for absolute paths)
    path = path.rstrip("/")
    return path


@dataclass(frozen=True)
class FileOperation:
    """Unified file operation IR (Intermediate Representation).

    All protocol dialects are normalized to FileOperation.
    frozen=True ensures hashability for deduplication and auditing.
    """

    path: str  # Relative path, normalized
    edit_type: EditType
    search: str | None = None  # Used for SEARCH_REPLACE
    replace: str | None = None  # Used for SEARCH_REPLACE/CREATE/FULL_FILE
    move_to: str | None = None  # Target path after update (apply_patch *** Move to:)

    # Audit metadata
    original_format: str = ""  # Original protocol format identifier
    source_line: int = 0  # Line number in original text

    def __post_init__(self):
        """Normalize paths after initialization."""
        # Freeze by modifying through object.__setattr__
        object.__setattr__(self, "path", _normalize_path(str(self.path) if self.path else ""))
        if self.move_to:
            object.__setattr__(self, "move_to", _normalize_path(str(self.move_to)))

    @property
    def is_valid(self) -> bool:
        """Check if the operation is valid."""
        if not self.path:
            return False
        if self.edit_type == EditType.SEARCH_REPLACE:
            return self.replace is not None
        if self.edit_type in (EditType.FULL_FILE, EditType.CREATE):
            return self.replace is not None
        return True

    def compute_hash(self) -> str:
        """Compute operation fingerprint for auditing and deduplication."""
        content = f"{self.path}:{self.edit_type.name}:{self.search}:{self.replace}:{self.move_to}"
        return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


@dataclass
class FileOpValidationResult:
    """Validation result for file operations.

    Note: This is distinct from other ValidationResult types:
    - ToolArgValidationResult: Tool argument validation
    - ProviderConfigValidationResult: Provider configuration validation
    - LaunchValidationResult: Bootstrap launch validation
    - SchemaValidationResult: Schema validation
    """

    valid: bool
    error_code: ErrorCode
    error_message: str = ""
    normalized_path: str = ""
    normalized_move_to: str = ""


# Backward compatibility alias (deprecated)
ValidationResult = FileOpValidationResult


@dataclass
class OperationResult:
    """Single operation execution result."""

    operation: FileOperation
    success: bool
    error_code: ErrorCode
    error_message: str = ""

    # Change information
    changed: bool = False
    old_hash: str = ""  # Hash of file before change
    new_hash: str = ""  # Hash of file after change
    old_line_count: int = 0
    new_line_count: int = 0

    # Audit timestamp
    timestamp: float = field(default_factory=time.time)


@dataclass
class ApplyReport:
    """Batch execution report."""

    success: bool
    protocol_version: str = "2.0-strict"

    # Statistics
    ops_total: int = 0
    ops_applied: int = 0
    ops_failed: int = 0
    ops_skipped: int = 0

    # Detailed results
    results: list[OperationResult] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)

    # Error aggregation
    error_codes: list[ErrorCode] = field(default_factory=list)

    # Audit
    original_text_hash: str = ""  # Hash of original input text
    audit_log: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict for serialization."""
        return {
            "success": self.success,
            "protocol_version": self.protocol_version,
            "stats": {
                "total": self.ops_total,
                "applied": self.ops_applied,
                "failed": self.ops_failed,
                "skipped": self.ops_skipped,
            },
            "changed_files": self.changed_files,
            "error_codes": [e.value for e in self.error_codes],
            "results": [
                {
                    "path": r.operation.path,
                    "type": r.operation.edit_type.name,
                    "success": r.success,
                    "error_code": r.error_code.value,
                    "changed": r.changed,
                }
                for r in self.results
            ],
        }
