"""Backward compatibility alias for protocol module.

This file has been split into a directory structure.
KernelFileSystem is the canonical file I/O abstraction used by the protocol layer.
Please update imports to use the new module path:

    from polaris.kernelone.llm.toolkit.protocol import FileOperation

Or import directly from specific submodules:

    from polaris.kernelone.llm.toolkit.protocol.models import FileOperation
    from polaris.kernelone.llm.toolkit.protocol.parser import ProtocolParser
"""

from __future__ import annotations

# Re-export everything from the new module structure
from polaris.kernelone.llm.toolkit.protocol import (
    ApplyReport,
    EditType,
    # Constants
    ErrorCode,
    # Models
    FileOperation,
    OperationResult,
    OperationValidator,
    # Core classes
    ProtocolParser,
    StrictOperationApplier,
    ValidationResult,
    _detect_path_traversal,
    _is_path_safe,
    # Path utilities (for compatibility)
    _normalize_path,
    apply_operations,
    apply_protocol_output,
    # Convenience functions
    parse_protocol_output,
    validate_operations,
)

__all__ = [
    "ApplyReport",
    "EditType",
    # Constants
    "ErrorCode",
    # Models
    "FileOperation",
    "OperationResult",
    "OperationValidator",
    # Core classes
    "ProtocolParser",
    "StrictOperationApplier",
    "ValidationResult",
    "_detect_path_traversal",
    "_is_path_safe",
    # Path utilities
    "_normalize_path",
    "apply_operations",
    "apply_protocol_output",
    # Convenience functions
    "parse_protocol_output",
    "validate_operations",
]
