"""Constants and type aliases for post-execution repair bridge."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from polaris.cells.director.runtime.public.service import RepairAdvisoryV1

StepRunner = Callable[[Any, Path, str], list[dict[str, Any]]]
RuntimeAdvisorNotes = tuple[RepairAdvisoryV1, ...]
ConvergenceVerifier = Callable[[Any], Any]

_CPP_REPAIR_FILE_SUFFIXES = (".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx")
_POST_EXECUTION_REPAIR_MAX_ROUNDS = 3
_CALLBACK_RECEIPT_MIGRATION_BLOCKER = "adapter schedule runners still return tool_results instead of RepairReceipt"
_RUST_BASE_FILE_IGNORES = frozenset({".git", ".venv", "__pycache__", "node_modules", "target"})
_RUST_TYPED_RECEIPT_CUTOVER_SOURCE_TOOLS = frozenset(
    {
        "deterministic_rust_missing_fields_repair",
        "deterministic_rust_lib_root_facade_repair",
    }
)
_RUST_MISSING_FIELDS_SOURCE_TOOL = "deterministic_rust_missing_fields_repair"
_RUST_LIB_ROOT_FACADE_SOURCE_TOOL = "deterministic_rust_lib_root_facade_repair"
_RUST_MISSING_FIELDS_FIELD_DECLARATION_SUBCASE = f"{_RUST_MISSING_FIELDS_SOURCE_TOOL}:field_declaration"
_RUST_LIB_ROOT_FACADE_PATH_REWRITE_SUBCASE = f"{_RUST_LIB_ROOT_FACADE_SOURCE_TOOL}:path_rewrite"
_RUST_LIB_ROOT_FACADE_EXPORT_OR_MODULE_DECLARATION_SUBCASE = (
    f"{_RUST_LIB_ROOT_FACADE_SOURCE_TOOL}:export_or_module_declaration"
)
_RUST_TYPED_RECEIPT_CUTOVER_SUBCASES_BY_SOURCE_TOOL: Mapping[str, frozenset[str]] = {
    _RUST_MISSING_FIELDS_SOURCE_TOOL: frozenset({_RUST_MISSING_FIELDS_FIELD_DECLARATION_SUBCASE}),
    _RUST_LIB_ROOT_FACADE_SOURCE_TOOL: frozenset(
        {
            _RUST_LIB_ROOT_FACADE_EXPORT_OR_MODULE_DECLARATION_SUBCASE,
            _RUST_LIB_ROOT_FACADE_PATH_REWRITE_SUBCASE,
        }
    ),
}
_RUST_TYPED_RECEIPT_SOURCE_TOOL_BLOCKER = "rust_typed_receipt_source_tool_not_runtime_executable"
_GO_POST_EXECUTION_RUNTIME_SOURCE_TOOLS = (
    "deterministic_go_bare_import_string_repair",
    "deterministic_go_nested_import_repair",
    "deterministic_go_module_import_repair",
    "deterministic_go_bare_import_repair",
    "deterministic_go_subpath_repair",
    "deterministic_go_unused_import_repair",
    "deterministic_go_error_string_helper_repair",
    "deterministic_go_dedup_repair",
)
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_RUST_E0583_HELP_LINE_RE = re.compile(
    r"to create the module [`'\"](?P<module>[A-Za-z_][A-Za-z0-9_]*)[`'\"].*?"
    r"create file (?P<candidates>[^\n]+)",
    re.IGNORECASE,
)
_RUST_QUOTED_RS_PATH_RE = re.compile(r"[`'\"](?P<path>[^`'\"]+\.rs)[`'\"]")
