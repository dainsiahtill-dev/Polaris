"""Constants for the protocol module.

Contains enums and constants used by the protocol kernel.
"""

from __future__ import annotations

from enum import Enum, auto


class ErrorCode(Enum):
    """Structured error codes - supports LLM retry decisions."""

    # Protocol layer errors
    SEARCH_NOT_FOUND = "SEARCH_NOT_FOUND"
    SEARCH_AMBIGUOUS = "SEARCH_AMBIGUOUS"
    INVALID_PROTOCOL_BLOCK = "INVALID_PROTOCOL_BLOCK"
    UNCLOSED_BLOCK = "UNCLOSED_BLOCK"
    EMPTY_OPERATION = "EMPTY_OPERATION"
    INVALID_OPERATION = "INVALID_OPERATION"

    # Path/security errors
    PATH_OUTSIDE_WORKSPACE = "PATH_OUTSIDE_WORKSPACE"
    PATH_TRAVERSAL = "PATH_TRAVERSAL"
    INVALID_PATH = "INVALID_PATH"

    # Encoding/IO errors
    ENCODING_ERROR = "ENCODING_ERROR"
    FILE_NOT_READABLE = "FILE_NOT_READABLE"
    FILE_NOT_WRITABLE = "FILE_NOT_WRITABLE"
    BINARY_FILE = "BINARY_FILE"
    FILE_TOO_LARGE = "FILE_TOO_LARGE"

    # Execution errors
    CONCURRENT_MODIFICATION = "CONCURRENT_MODIFICATION"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    DISK_FULL = "DISK_FULL"

    # Success states
    OK = "OK"
    NOOP = "NOOP"


class EditType(Enum):
    """Unified edit type."""

    SEARCH_REPLACE = auto()
    FULL_FILE = auto()
    CREATE = auto()
    DELETE = auto()
