"""KernelOne tool execution module.

This module provides the canonical tool chain execution capability for KernelOne.
It is the single source of truth for tool execution, planning, parsing, and security.

Submodules
----------
constants : Tool name, command whitelist, and parameter-constraint constants.
models    : ``ToolChainStep`` and ``ToolChainResult`` dataclasses.
security  : Command-level blocked-pattern matching and whitelist enforcement.
utils     : General utility functions (sanitize, split, safe_int, append_log).
validators: Type validators for parameter validation.
plan_parser : Tool plan parsing and extraction from text.
cli_builder : CLI argument builders for repo_* tools.
output    : Output processing, analysis, scoring, and persistence.
chain     : Chain step parsing and plan normalization.
executor  : ~~DEPRECATED~~ Removed 2026-04-17. Use AgentAccelToolExecutor.
executor_core : ~~DEPRECATED~~ Removed 2026-04-17. Use AgentAccelToolExecutor.

Consumers
---------
- ``polaris.cells.director.execution`` : imports tool chain execution
- ``polaris.kernelone.tool_execution.runtime_executor`` : uses build_tool_cli_args
- ``polaris.infrastructure.compat.io_utils`` : uses io_tools sub-module
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
from polaris.kernelone.constants import DEFAULT_MAX_RETRIES

# ---------------------------------------------------------------------------
# Chain
# ---------------------------------------------------------------------------
from polaris.kernelone.tool_execution.chain import (
    normalize_tool_plan,
    parse_tool_chain_step,
)

# ---------------------------------------------------------------------------
# CLI builder
# ---------------------------------------------------------------------------
from polaris.kernelone.tool_execution.cli_builder import build_tool_cli_args
from polaris.kernelone.tool_execution.constants import (
    ALLOWED_EXECUTION_COMMANDS,
    ALLOWED_TOOLS,
    BLOCKED_COMMAND_PATTERNS,
    DEFAULT_READ_RADIUS,
    KV_ALLOWED_KEYS,
    MAX_EVENT_CONTENT_LINES,
    MAX_TOOL_READ_LINES,
    READ_ONLY_TOOLS,
    WRITE_TOOLS,
    CommandValidationResult,
    CommandWhitelistValidator,
)

# ---------------------------------------------------------------------------
# Contracts
# ---------------------------------------------------------------------------
from polaris.kernelone.tool_execution.contracts import (
    ERROR_INVALID_TOOL_ARGS,
    ERROR_MAX_LENGTH,
    ERROR_MAXIMUM,
    ERROR_MIN_LENGTH,
    ERROR_MINIMUM,
    ERROR_PATTERN,
    # Error codes (re-exported from contracts)
    ERROR_UNKNOWN_TOOL,
    canonicalize_tool_name,
    list_tool_contracts,
    normalize_tool_args,
    render_tool_contract_for_prompt,
    supported_tool_names,
    validate_tool_step,
)

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
from polaris.kernelone.tool_execution.models import ToolChainResult, ToolChainStep

# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------
from polaris.kernelone.tool_execution.output import (
    analyze_slice_content,
    annotate_rg_output,
    build_refs,
    compact_tool_output,
    count_tool_output_lines,
    persist_tool_raw_output,
    score_hit,
    suggest_radius,
)

# ---------------------------------------------------------------------------
# Plan parser
# ---------------------------------------------------------------------------
from polaris.kernelone.tool_execution.plan_parser import (
    _normalize_tool_plan_dict_step,
    _parse_key_value_token,
    extract_tool_budget,
    extract_tool_plan,
    parse_tool_plan_item,
)

# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------
from polaris.kernelone.tool_execution.security import (
    is_command_allowed,
    is_command_blocked,
)

# ---------------------------------------------------------------------------
# Utils
# ---------------------------------------------------------------------------
from polaris.kernelone.tool_execution.utils import (
    append_log,
    as_list,
    safe_int,
    sanitize_tool_name,
    split_list_value,
    split_tool_step,
)

# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------
from polaris.kernelone.tool_execution.validators import (
    ERROR_ARRAY_TOO_LONG,
    ERROR_ARRAY_TOO_SHORT,
    ERROR_INTEGER_TOO_LARGE,
    ERROR_INTEGER_TOO_SMALL,
    # Error codes
    ERROR_INVALID_TYPE,
    ERROR_REQUIRED_MISSING,
    ERROR_STRING_PATTERN_MISMATCH,
    ERROR_STRING_TOO_LONG,
    ERROR_STRING_TOO_SHORT,
    ArrayValidator,
    BaseValidator,
    BooleanValidator,
    IntegerValidator,
    StringValidator,
    ValidationError,
    ValidationResult,
    get_validator,
)

__all__ = [
    # Constants
    "ALLOWED_EXECUTION_COMMANDS",
    "ALLOWED_TOOLS",
    "BLOCKED_COMMAND_PATTERNS",
    "DEFAULT_MAX_RETRIES",
    "DEFAULT_READ_RADIUS",
    "ERROR_ARRAY_TOO_LONG",
    "ERROR_ARRAY_TOO_SHORT",
    "ERROR_INTEGER_TOO_LARGE",
    "ERROR_INTEGER_TOO_SMALL",
    "ERROR_INVALID_TOOL_ARGS",
    # Validator error codes
    "ERROR_INVALID_TYPE",
    "ERROR_MAXIMUM",
    "ERROR_MAX_LENGTH",
    "ERROR_MINIMUM",
    "ERROR_MIN_LENGTH",
    "ERROR_PATTERN",
    "ERROR_REQUIRED_MISSING",
    "ERROR_STRING_PATTERN_MISMATCH",
    "ERROR_STRING_TOO_LONG",
    "ERROR_STRING_TOO_SHORT",
    # Contract error codes
    "ERROR_UNKNOWN_TOOL",
    "KV_ALLOWED_KEYS",
    "MAX_EVENT_CONTENT_LINES",
    "MAX_TOOL_READ_LINES",
    "READ_ONLY_TOOLS",
    "WRITE_TOOLS",
    # Validators
    "ArrayValidator",
    "BaseValidator",
    "BooleanValidator",
    # Command validation
    "CommandValidationResult",
    "CommandWhitelistValidator",
    "IntegerValidator",
    "StringValidator",
    # Models
    "ToolChainResult",
    "ToolChainStep",
    "ValidationError",
    "ValidationResult",
    # Plan parser
    "_normalize_tool_plan_dict_step",
    "_parse_key_value_token",
    # Output
    "analyze_slice_content",
    "annotate_rg_output",
    # Utils
    "append_log",
    "as_list",
    "build_refs",
    # CLI builder
    "build_tool_cli_args",
    # Contracts
    "canonicalize_tool_name",
    "compact_tool_output",
    "count_tool_output_lines",
    "extract_tool_budget",
    "extract_tool_plan",
    "get_validator",
    # Security
    "is_command_allowed",
    "is_command_blocked",
    "list_tool_contracts",
    "normalize_tool_args",
    # Chain
    "normalize_tool_plan",
    "parse_tool_chain_step",
    "parse_tool_plan_item",
    "persist_tool_raw_output",
    "render_tool_contract_for_prompt",
    "safe_int",
    "sanitize_tool_name",
    "score_hit",
    "split_list_value",
    "split_tool_step",
    "suggest_radius",
    "supported_tool_names",
    "validate_tool_step",
]
