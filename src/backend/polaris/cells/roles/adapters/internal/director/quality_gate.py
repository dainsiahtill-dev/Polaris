"""Director artifact-quality collection + quality-repair flow.

Artifact-quality collection, error-path parsing, and the LLM-driven quality
repair flow (including ``scan_workspace_artifact_quality`` orchestration),
extracted verbatim from ``execute_method.py`` during the lossless
decomposition of that god-module.

The ``scan_workspace_artifact_quality`` reference and the
``quality_gate`` <-> ``deterministic_repairs`` reference cycle are resolved
through ``execute_method`` (aliased ``_em``) at call time so a test
``monkeypatch`` on the ``execute_method`` module namespace still takes effect.
The canonical import path remains ``execute_method`` (which re-exports every
symbol here).
"""

from __future__ import annotations

import ast
import contextlib
import os
import re
import shlex
import subprocess
from pathlib import Path
from typing import Any

from . import execute_method as _em
from .contract_verify import resolve_contract_step_verify_command
from .execution_tools import DirectorToolExecutor
from .helpers import has_successful_write_tool
from .materialization_quality_repair_bridge import (
    has_materialization_quality_runtime_repair_coverage,
    run_materialization_quality_repairs,
    run_typescript_semantic_quality_repairs,
)
from .repair_profile_projection import project_repair_kernel_summary
from .task_scope_paths import (
    _dedupe_preserve_order,
    _extract_task_path_candidates,
    _extract_task_target_path_candidates,
    _filter_diff_to_task_declared_paths,
    _normalize_declared_task_path,
    _path_candidate_exists_in_file_set,
    _task_has_declared_target_files,
    _task_text_blob,
    _workspace_path_exists_case_insensitive,
)


def _summarize_llm_stage_result(result: dict[str, Any], *, stage: str) -> dict[str, Any]:
    """Build compact evidence for whether the configured role LLM produced output."""

    raw_response = result.get("raw_response")
    raw_payload: dict[str, Any] = raw_response if isinstance(raw_response, dict) else {}
    metadata_raw = raw_payload.get("metadata")
    metadata: dict[str, Any] = metadata_raw if isinstance(metadata_raw, dict) else {}
    execution_stats_raw = raw_payload.get("execution_stats")
    execution_stats: dict[str, Any] = execution_stats_raw if isinstance(execution_stats_raw, dict) else {}
    content = str(result.get("content") or result.get("response") or raw_payload.get("response") or "")
    provider = (
        str(result.get("provider") or "").strip()
        or str(raw_payload.get("provider") or raw_payload.get("provider_id") or "").strip()
        or str(metadata.get("provider") or metadata.get("provider_id") or "").strip()
    )
    model = (
        str(result.get("model") or "").strip()
        or str(raw_payload.get("model") or "").strip()
        or str(metadata.get("model") or execution_stats.get("model") or "").strip()
    )
    return {
        "stage": stage,
        "success": bool(result.get("success")),
        "provider": provider,
        "model": model,
        "content_length": len(content),
        "error": str(result.get("error") or raw_payload.get("error") or "").strip(),
        "llm_calls": _safe_int(execution_stats.get("llm_calls")),
    }


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _quality_repair_write_file_tool_definition() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write a complete UTF-8 text file at the requested target path.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file": {"type": "string"},
                    "content": {"type": "string", "minLength": 1},
                },
                "required": ["file", "content"],
            },
        },
    }


def _quality_repair_edit_file_tool_definition() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": (
                "Replace one exact UTF-8 search string in an existing file. "
                "Use this for compiler/test repair when preserving the rest of the file."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file": {"type": "string"},
                    "search": {"type": "string", "minLength": 1},
                    "replace": {"type": "string"},
                },
                "required": ["file", "search", "replace"],
            },
        },
    }


def _format_unresolved_relative_import_error_for_repair_prompt(error: Any) -> str | None:
    """Return a path-safe repair prompt line for unresolved relative imports."""

    match = _em._UNRESOLVED_RELATIVE_IMPORT_ERROR_RE.search(str(error or ""))
    if not match:
        return None
    importer_rel = _normalize_declared_task_path(match.group("path"))
    specifier = str(match.group("specifier") or "").strip()
    if not importer_rel or not specifier.startswith("."):
        return None
    candidates = _em._relative_import_repair_target_candidates(
        root=Path("/__polaris_workspace__"),
        importer_rel=importer_rel,
        specifier=specifier,
    )
    target = candidates[0] if candidates else ""
    safe_specifier = _relative_import_specifier_safe_for_repair_prompt(specifier)
    if not target:
        if safe_specifier:
            return f"Artifact quality scan failed: unresolved relative import '{specifier}' in {importer_rel}."
        return (
            "Artifact quality scan failed: unresolved relative import "
            f"in {importer_rel}; Raw relative specifier omitted for path safety."
        )
    if safe_specifier:
        return (
            "Artifact quality scan failed: unresolved relative import "
            f"'{specifier}' in {importer_rel}; create missing module target {target} and export the imported symbols."
        )
    return (
        "Artifact quality scan failed: unresolved relative import "
        f"in {importer_rel}; create missing module target {target} and export the imported symbols. "
        "Raw relative specifier omitted for path safety."
    )


def _relative_import_specifier_safe_for_repair_prompt(specifier: str) -> bool:
    token = str(specifier or "").strip()
    if not token.startswith("./") or "\\" in token:
        return False
    if any(marker in token for marker in ("*", "?", "\x00", ":")):
        return False
    parts = [part for part in token.split("/") if part]
    return bool(parts) and all(part not in {".", ".."} for part in parts[1:])


_TSC_PROJECT_DIAGNOSTIC_RE = re.compile(
    r"TypeScript project typecheck failed:\s*"
    r"(?P<path>[^\s:()]+(?:\.d\.ts|\.tsx?))"
    r"(?P<loc>\(\d+,\d+\)|:\d+:\d+)?"
    r":\s*error\s+(?P<code>TS\d+):\s*(?P<detail>.*)",
    re.IGNORECASE | re.DOTALL,
)
_TOOL_RECEIPT_CONTAMINATION_TOKENS = (
    "**write_file**: error",
    "**edit_file**: error",
    "**append_to_file**: error",
    "destructive shrink rejected",
    "director_write_policy_denied",
    "handler_error_type",
)
_QUALITY_SYNTAX_ERROR_PATH_RE = re.compile(
    r"syntax error in (?P<path>(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.[A-Za-z0-9_.-]+):",
    re.IGNORECASE,
)
_NPM_SCRIPT_MISSING_LOCAL_ENTRYPOINT_RE = re.compile(
    r"npm package manifest script '(?P<script>[^']+)' references missing local entrypoint '(?P<path>[^']+)'",
    re.IGNORECASE,
)
_NPM_SCRIPT_REPAIRABLE_SOURCE_PREFIXES = (
    "__tests__/",
    "scripts/",
    "spec/",
    "src/",
    "test/",
    "tests/",
)
_NPM_SCRIPT_GENERATED_OUTPUT_PREFIXES = (
    ".cache/",
    "build/",
    "coverage/",
    "dist/",
    "node_modules/",
    "out/",
)
_JS_MISSING_NAMED_EXPORT_RE = re.compile(
    r"requested module ['\"](?P<module>[^'\"]+)['\"] does not provide an export named ['\"](?P<symbol>[^'\"]+)['\"]",
    re.IGNORECASE,
)
_JS_NAMED_IMPORT_RE = re.compile(
    r"import\s+(?:(?P<default>[A-Za-z_$][\w$]*)\s*,\s*)?"
    r"\{(?P<symbols>[^}]+)\}\s*from\s*['\"](?P<module>[^'\"]+)['\"]",
    re.IGNORECASE,
)
_JS_MODULE_SYSTEM_REPAIR_MARKERS = (
    "require is not defined in es module scope",
    "module is not defined in es module scope",
    "exports is not defined in es module scope",
    "cannot use import statement outside a module",
    "declares type=module but workspace javascript uses commonjs runtime syntax",
    'contains "type": "module"',
)


def _quality_error_path_safe_for_repair_prompt(path: str) -> bool:
    token = str(path or "").strip().replace("\\", "/")
    if not token or token.startswith(("/", "./", "../", "~")):
        return False
    if re.match(r"^[A-Za-z]:", token):
        return False
    if ".." in token.split("/"):
        return False
    if "node_modules" in token.split("/"):
        return False
    return bool(_normalize_declared_task_path(token))


def _format_typescript_project_typecheck_error_for_repair_prompt(error: Any) -> str:
    text = str(error or "")
    match = _TSC_PROJECT_DIAGNOSTIC_RE.search(text)
    if not match:
        return ""
    path = str(match.group("path") or "").strip()
    if _quality_error_path_safe_for_repair_prompt(path):
        return text
    code = str(match.group("code") or "TS").strip()
    detail = re.sub(r"\s+", " ", str(match.group("detail") or "")).strip()
    if len(detail) > 180:
        detail = detail[:180].rstrip() + " ..."
    suffix = f" {code}: {detail}" if detail else f" {code}"
    return (
        "Artifact quality scan failed: TypeScript project typecheck failed:"
        f" external dependency diagnostic{suffix}. Path omitted for workspace safety."
    )


def _looks_like_tool_receipt_contamination_text(text: str) -> bool:
    lowered = str(text or "").lower()
    return any(token in lowered for token in _TOOL_RECEIPT_CONTAMINATION_TOKENS)


def _format_tool_receipt_contamination_error_for_repair_prompt(error: Any) -> str:
    text = str(error or "")
    if not _looks_like_tool_receipt_contamination_text(text):
        return ""
    target = "the contaminated artifact"
    match = _QUALITY_SYNTAX_ERROR_PATH_RE.search(text)
    if match:
        rel = _normalize_declared_task_path(match.group("path"))
        if rel and _quality_error_path_safe_for_repair_prompt(rel):
            target = rel
    return (
        "Artifact quality scan failed: tool execution receipt contamination in "
        f"{target}; the file contains a Polaris tool failure receipt instead of source code. "
        "Rewrite that artifact with real UTF-8 project code. Do not copy any write_file/edit_file "
        "error receipt, destructive-shrink diagnostic, handler_error_type, or tool result text into files."
    )


def _format_quality_error_for_repair_prompt(error: Any) -> str:
    """Format quality errors for repair prompts without unsafe path tokens."""

    return (
        _format_tool_receipt_contamination_error_for_repair_prompt(error)
        or _format_unresolved_relative_import_error_for_repair_prompt(error)
        or _format_typescript_project_typecheck_error_for_repair_prompt(error)
        or str(error)
    )


def _parse_js_named_import_symbols(symbols_text: str) -> list[str]:
    symbols: list[str] = []
    for raw_item in str(symbols_text or "").split(","):
        token = raw_item.strip()
        if not token:
            continue
        imported = re.split(r"\s+as\s+", token, maxsplit=1, flags=re.IGNORECASE)[0].strip()
        if imported and imported not in symbols:
            symbols.append(imported)
    return symbols


def _js_imported_symbols_for_module(text: str, module: str) -> list[str]:
    expected_module = str(module or "").strip()
    if not expected_module:
        return []
    symbols: list[str] = []
    for match in _JS_NAMED_IMPORT_RE.finditer(str(text or "")):
        if str(match.group("module") or "").strip() != expected_module:
            continue
        for symbol in _parse_js_named_import_symbols(match.group("symbols")):
            if symbol not in symbols:
                symbols.append(symbol)
    return symbols


def _js_default_imports_for_module(text: str, module: str) -> list[str]:
    expected_module = str(module or "").strip()
    if not expected_module:
        return []
    aliases: list[str] = []
    for match in _JS_NAMED_IMPORT_RE.finditer(str(text or "")):
        if str(match.group("module") or "").strip() != expected_module:
            continue
        alias = str(match.group("default") or "").strip()
        if alias and alias not in aliases:
            aliases.append(alias)
    return aliases


def _build_javascript_named_export_repair_block(artifact_quality_errors: list[str]) -> str:
    obligations: list[str] = []
    for item in artifact_quality_errors:
        text = str(item or "")
        match = _JS_MISSING_NAMED_EXPORT_RE.search(text)
        if not match:
            continue
        module = str(match.group("module") or "").strip()
        missing_symbol = str(match.group("symbol") or "").strip()
        symbols = _js_imported_symbols_for_module(text, module)
        default_aliases = _js_default_imports_for_module(text, module)
        if missing_symbol and missing_symbol not in symbols:
            symbols.append(missing_symbol)
        if not module or not symbols:
            continue
        rendered_symbols = ", ".join(f"'{symbol}'" for symbol in symbols[:12])
        obligations.append(f"- Module '{module}' must provide named export(s): {rendered_symbols}.")
        if default_aliases:
            rendered_aliases = ", ".join(f"'{alias}'" for alias in default_aliases[:6])
            obligations.append(
                f"- Module '{module}' is also imported through default binding(s): {rendered_aliases}; "
                "keep a valid default export, preferably an object exposing the same named API."
            )
    if not obligations:
        return ""
    return (
        "JAVASCRIPT NAMED EXPORT REPAIR: an existing importer already declares the public API it needs. "
        "Do not remove, weaken, or skip that import to make tests pass. Update the exporting module named "
        "in the requested-module error so it defines and exports the named symbol(s) below. If the importing "
        "file is also a repair target, preserve its import contract unless every dependent target is changed "
        "coherently in the same batch. Do not read files first. Do not list directories. Do not explain.\n"
        + "\n".join(obligations[:12])
        + "\n"
    )


def _build_javascript_module_system_repair_block(
    artifact_quality_errors: list[str],
    repair_target_files: list[str] | None = None,
) -> str:
    combined = "\n".join(str(item or "") for item in artifact_quality_errors).lower()
    if not any(marker in combined for marker in _JS_MODULE_SYSTEM_REPAIR_MARKERS):
        return ""
    normalized_targets = {
        normalized
        for normalized in (_normalize_declared_task_path(item) for item in repair_target_files or [])
        if normalized
    }
    if "package.json" in normalized_targets:
        package_scope_rule = (
            "Because package.json is an authorized failed repair target, it may be rewritten only if the "
            "resulting package config and every repaired importer/source file use the same module system. "
        )
    else:
        package_scope_rule = (
            "If package.json is not listed as a failed repair target above, treat its existing module "
            "declaration as fixed input: do not write package.json, and rewrite only the authorized "
            "JavaScript source/test files to match it. "
        )
    return (
        "JAVASCRIPT MODULE SYSTEM REPAIR: package.json, executable source files, and tests must use one "
        "coherent module system. When package.json declares type=module or a test uses `import { ... } from`, "
        "do not leave `require(...)`, `module.exports`, or `exports.*` in files executed by npm start/test. "
        f"{package_scope_rule}"
        "Prefer preserving the named ESM import/export contract and rewrite the exporting entry module with "
        "`export` declarations. Only switch to CommonJS if package.json and every importer/test in the repair "
        "batch are also rewritten coherently and npm test plus the entrypoint smoke can run.\n"
    )


def _tool_receipt_safe_quality_errors(errors: list[str]) -> list[str]:
    return [_format_tool_receipt_contamination_error_for_repair_prompt(error) or str(error or "") for error in errors]


_RAW_SINGLE_TARGET_CODE_FENCE_RE = re.compile(
    r"^\s*```(?:[A-Za-z0-9_.+-]+)?\s*\n(?P<body>.*?)(?:\n)?```\s*$",
    re.DOTALL,
)


def _normalize_raw_single_target_write_content(content: str) -> str:
    text = str(content or "").strip()
    if not text:
        return ""
    fence = _RAW_SINGLE_TARGET_CODE_FENCE_RE.match(text)
    if fence:
        return str(fence.group("body") or "").strip()
    return text


def _coerce_raw_single_target_repair_to_write_file(
    adapter: Any,
    *,
    task_id: str,
    repair_target_files: list[str],
    content: str,
) -> list[dict[str, Any]]:
    """Normalize weak-model raw file bodies into write_file for one exact target.

    This path is deliberately narrow: it only fires after native/tool-text
    extraction produced no successful write, and only when the platform already
    pinned the repair to exactly one target file.
    """

    if len(repair_target_files) != 1:
        return []
    target_file = _normalize_declared_task_path(repair_target_files[0])
    if not target_file or any(marker in target_file for marker in ("*", "?")):
        return []
    normalized_content = _normalize_raw_single_target_write_content(content)
    if not normalized_content:
        return []
    if _looks_like_tool_receipt_contamination_text(normalized_content):
        return []
    workspace_full = str(getattr(adapter, "workspace", "") or "")
    if not workspace_full:
        return []
    message_bus = getattr(getattr(adapter, "_execution", None), "_message_bus", None)
    write_result = DirectorToolExecutor(
        workspace_full,
        message_bus=message_bus,
        worker_id="director",
    ).execute_tool(
        "write_file",
        {"file": target_file, "content": normalized_content},
        task_id=task_id,
    )
    if not bool(write_result.get("ok")):
        return []
    with contextlib.suppress(OSError, RuntimeError, TypeError, ValueError):
        adapter._update_task_progress(task_id, "executing", current_file=target_file)
    return [
        {
            "tool": "write_file",
            "tool_name": "write_file",
            "success": True,
            "result": {
                "ok": True,
                "source_tool": "director_quality_repair_raw_single_target_write_file",
                "file": target_file,
                "bytes_written": int(write_result.get("bytes_written") or len(normalized_content.encode("utf-8"))),
                "operation": str(write_result.get("operation") or "modify"),
                "broadcast_ok": bool(write_result.get("broadcast_ok")),
                "director_policy": write_result.get("director_policy"),
            },
        }
    ]


def _deterministic_single_missing_quality_repair_to_write_file(
    adapter: Any,
    *,
    task_id: str,
    repair_target_files: list[str],
    artifact_quality_errors: list[str],
) -> list[dict[str, Any]]:
    """Create narrow, non-domain missing metadata files after LLM repair misses."""

    if len(repair_target_files) != 1:
        return []
    target_file = _normalize_declared_task_path(repair_target_files[0])
    if target_file != "requirements.txt":
        return []
    required_dependencies = _requirements_txt_declared_dependencies(artifact_quality_errors)
    joined_errors = "\n".join(str(item or "").lower() for item in artifact_quality_errors)
    if "requirements.txt" not in joined_errors:
        return []
    if (
        "no such file or directory" not in joined_errors
        and "must exist at" not in joined_errors
        and not required_dependencies
    ):
        return []
    workspace_full = str(getattr(adapter, "workspace", "") or "")
    if not workspace_full:
        return []
    workspace_root = Path(workspace_full).resolve()
    if _workspace_path_exists_case_insensitive(workspace_root, target_file) and not required_dependencies:
        return []
    message_bus = getattr(getattr(adapter, "_execution", None), "_message_bus", None)
    content = "".join(f"{item}\n" for item in required_dependencies) or "# standard library only\n"
    write_result = DirectorToolExecutor(
        workspace_full,
        message_bus=message_bus,
        worker_id="director",
    ).execute_tool(
        "write_file",
        {"file": target_file, "content": content},
        task_id=task_id,
    )
    if not bool(write_result.get("ok")):
        return []
    with contextlib.suppress(OSError, RuntimeError, TypeError, ValueError):
        adapter._update_task_progress(task_id, "executing", current_file=target_file)
    return [
        {
            "tool": "write_file",
            "tool_name": "write_file",
            "success": True,
            "result": {
                "ok": True,
                "source_tool": "director_quality_repair_deterministic_missing_requirements_write_file",
                "file": target_file,
                "bytes_written": int(write_result.get("bytes_written") or len(content.encode("utf-8"))),
                "operation": str(write_result.get("operation") or "create"),
                "broadcast_ok": bool(write_result.get("broadcast_ok")),
                "director_policy": write_result.get("director_policy"),
            },
        }
    ]


def _deterministic_single_missing_python_module_alias_to_write_file(
    adapter: Any,
    *,
    task_id: str,
    repair_target_files: list[str],
    artifact_quality_errors: list[str],
) -> list[dict[str, Any]]:
    """Create a narrow source-root shim for tests importing a nested Python module."""

    if len(repair_target_files) != 1:
        return []
    target_file = _normalize_declared_task_path(repair_target_files[0])
    if not target_file or not target_file.startswith("src/") or not target_file.endswith(".py"):
        return []
    joined_errors = "\n".join(str(item or "").lower() for item in artifact_quality_errors)
    if "modulenotfounderror" not in joined_errors:
        return []
    workspace_full = str(getattr(adapter, "workspace", "") or "")
    if not workspace_full:
        return []
    workspace_root = Path(workspace_full).resolve()
    if _workspace_path_exists_case_insensitive(workspace_root, target_file):
        return []
    source_rel = _find_python_module_alias_source(workspace_root, target_file)
    if not source_rel:
        return []
    import_module = source_rel[:-3].replace("/", ".")
    content = (
        '"""Compatibility exports for tests importing this module from the src root."""\n\n'
        f"from {import_module} import *  # noqa: F401,F403\n"
    )
    message_bus = getattr(getattr(adapter, "_execution", None), "_message_bus", None)
    write_result = DirectorToolExecutor(
        workspace_full,
        message_bus=message_bus,
        worker_id="director",
    ).execute_tool(
        "write_file",
        {"file": target_file, "content": content},
        task_id=task_id,
    )
    if not bool(write_result.get("ok")):
        return []
    with contextlib.suppress(OSError, RuntimeError, TypeError, ValueError):
        adapter._update_task_progress(task_id, "executing", current_file=target_file)
    return [
        {
            "tool": "write_file",
            "tool_name": "write_file",
            "success": True,
            "result": {
                "ok": True,
                "source_tool": "director_quality_repair_deterministic_python_module_alias_write_file",
                "file": target_file,
                "source_file": source_rel,
                "bytes_written": int(write_result.get("bytes_written") or len(content.encode("utf-8"))),
                "operation": str(write_result.get("operation") or "create"),
                "broadcast_ok": bool(write_result.get("broadcast_ok")),
                "director_policy": write_result.get("director_policy"),
            },
        }
    ]


def _stage_summary_has_recoverable_no_write_mutation_contract_exception(
    summary: dict[str, Any] | None,
) -> bool:
    if not isinstance(summary, dict):
        return False
    return any(
        _is_recoverable_no_write_mutation_contract_error_text(text) for text in _iter_stage_summary_error_texts(summary)
    )


def _iter_stage_summary_error_texts(value: Any, *, depth: int = 0) -> list[str]:
    if depth > 5:
        return []
    if isinstance(value, dict):
        texts: list[str] = []
        for key, raw in value.items():
            key_text = str(key or "").lower()
            if key_text in {"error", "error_message", "message", "detail", "details"} and isinstance(raw, str):
                texts.append(raw)
            elif key_text in {
                "primary_llm",
                "adapter_result",
                "stage_summary",
                "summary",
                "raw_response",
            } or isinstance(raw, (dict, list, tuple)):
                texts.extend(_iter_stage_summary_error_texts(raw, depth=depth + 1))
        return texts
    if isinstance(value, (list, tuple)):
        texts = []
        for item in value:
            texts.extend(_iter_stage_summary_error_texts(item, depth=depth + 1))
        return texts
    return []


def _is_recoverable_no_write_mutation_contract_exception(exc: BaseException) -> bool:
    return _is_recoverable_no_write_mutation_contract_error_text(str(exc))


def _is_recoverable_no_write_mutation_contract_error_text(text: str) -> bool:
    token = str(text or "").strip().lower()
    if "single_batch_contract_violation" not in token:
        return False
    unsafe_hints = (
        "target drift",
        "path traversal",
        "outside narrowed set",
        "stale_edit",
        "tool_failure_circuit_breaker",
        "cannot mix read tools",
        "unauthorized",
    )
    if any(hint in token for hint in unsafe_hints):
        return False
    recoverable_hints = (
        "no write tool invocation",
        "requires write tools",
        "did not produce a valid tool batch",
    )
    return any(hint in token for hint in recoverable_hints)


_ACCEPTANCE_VERIFY_EXISTS_RE = re.compile(r"^verify\s+(?P<path>\S+)\s+exists$", re.IGNORECASE)
_ACCEPTANCE_TEST_FILE_FLAGS = {"-d", "-e", "-f", "-s"}


def _evaluate_acceptance_verify_exists(
    *,
    task: dict[str, Any],
    workspace_full: str,
    write_tool_evidence: bool,
) -> tuple[bool, dict[str, Any]]:
    """Evaluate machine-checkable file-existence acceptance assertions.

    The PM task quality gate emits acceptance criteria in this canonical form
    (task_quality_gate ``f"verify {scope_path} exists"``). When the Director
    produced no NEW diff but every such assertion already holds — e.g. a
    rewrite with identical content — failing with
    ``director_no_materialized_changes`` punishes a satisfied contract.
    CE fission also emits direct POSIX checks such as ``test -f file`` and
    ``test -f README.md && grep -q 'literal' README.md``. Parse only this
    tiny allowlist; never execute shell. Strictly gated: requires at least one
    recognized assertion, ALL recognized assertions passing, successful
    write-tool evidence (the model demonstrably did the work), and a real
    workspace. Path existence is case-insensitive, consistent with declared-
    target matching.
    """
    evidence: dict[str, Any] = {"checked": 0, "passed": [], "missing": []}
    if not write_tool_evidence or not workspace_full:
        return False, evidence
    criteria: list[str] = []
    metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
    for record in (task, metadata):
        if not isinstance(record, dict):
            continue
        for key in ("acceptance_criteria", "acceptance"):
            value = record.get(key)
            if isinstance(value, list):
                criteria.extend(str(item or "").strip() for item in value)
            elif isinstance(value, str):
                criteria.append(value.strip())
    root = Path(workspace_full)
    if not root.is_dir():
        return False, evidence
    for criterion in criteria:
        assertion = _evaluate_machine_checkable_acceptance_criterion(criterion, root)
        if assertion is None:
            continue
        evidence["checked"] += 1
        passed_paths, missing_paths = assertion
        evidence["passed"].extend(passed_paths)
        evidence["missing"].extend(missing_paths)
    evidence["passed"] = _dedupe_preserve_order([str(item) for item in evidence["passed"]])
    evidence["missing"] = _dedupe_preserve_order([str(item) for item in evidence["missing"]])
    satisfied = evidence["checked"] > 0 and not evidence["missing"]
    return satisfied, evidence


def _evaluate_machine_checkable_acceptance_criterion(
    criterion: str,
    root: Path,
) -> tuple[list[str], list[str]] | None:
    token = str(criterion or "").strip()
    if not token:
        return None

    match = _ACCEPTANCE_VERIFY_EXISTS_RE.match(token)
    if match:
        rel = _normalize_declared_task_path(match.group("path"))
        if rel and _workspace_path_satisfies_flag(root, rel, "-e"):
            return [rel], []
        return [], [rel or match.group("path")]

    clauses = [part.strip() for part in token.split("&&") if part.strip()]
    if not clauses:
        return None
    passed: list[str] = []
    missing: list[str] = []
    for clause in clauses:
        clause_result = _evaluate_safe_acceptance_clause(clause, root)
        if clause_result is None:
            return None
        path, ok = clause_result
        if ok:
            passed.append(path)
        else:
            missing.append(path)
    return passed, missing


def _evaluate_safe_acceptance_clause(clause: str, root: Path) -> tuple[str, bool] | None:
    try:
        parts = shlex.split(clause)
    except ValueError:
        return None
    if not parts:
        return None

    if parts[0] == "test" and len(parts) == 3 and parts[1] in _ACCEPTANCE_TEST_FILE_FLAGS:
        rel = _normalize_declared_task_path(parts[2])
        if not rel:
            return parts[2], False
        return rel, _workspace_path_satisfies_flag(root, rel, parts[1])

    if parts[0] == "[" and len(parts) == 4 and parts[3] == "]" and parts[1] in _ACCEPTANCE_TEST_FILE_FLAGS:
        rel = _normalize_declared_task_path(parts[2])
        if not rel:
            return parts[2], False
        return rel, _workspace_path_satisfies_flag(root, rel, parts[1])

    if len(parts) in {4, 5} and parts[0] == "grep" and parts[1] == "-q":
        rest = parts[2:]
        if rest and rest[0] == "--":
            rest = rest[1:]
        if len(rest) != 2:
            return None
        literal, raw_path = rest
        rel = _normalize_declared_task_path(raw_path)
        if not rel:
            return raw_path, False
        path = _resolve_workspace_path_case_insensitive(root, rel)
        if path is None or not path.is_file():
            return rel, False
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return rel, False
        return rel, literal in text

    return None


def _workspace_path_satisfies_flag(root: Path, rel_path: str, flag: str) -> bool:
    path = _resolve_workspace_path_case_insensitive(root, rel_path)
    if path is None:
        return False
    if flag == "-e":
        return path.exists()
    if flag == "-f":
        return path.is_file()
    if flag == "-d":
        return path.is_dir()
    if flag == "-s":
        try:
            return path.is_file() and path.stat().st_size > 0
        except OSError:
            return False
    return False


def _resolve_workspace_path_case_insensitive(root: Path, rel_path: str) -> Path | None:
    candidate = root / rel_path
    if candidate.exists():
        return candidate
    current = root
    for part in rel_path.split("/"):
        if not current.is_dir():
            return None
        try:
            matched = next((entry for entry in current.iterdir() if entry.name.casefold() == part.casefold()), None)
        except OSError:
            return None
        if matched is None:
            return None
        current = matched
    return current if current.exists() else None


def _collect_workspace_code_diff(
    adapter: Any,
    baseline_files: dict[str, str],
    *,
    task: dict[str, Any] | None = None,
    workspace_name: str = "",
) -> tuple[dict[str, str], list[str], list[str], list[str]]:
    """Collect workspace fingerprints and compute task-relevant changed files."""

    current_files = adapter._state_tracker.collect_workspace_code_files()
    new_files = sorted(set(current_files.keys()) - set(baseline_files.keys()))
    modified_files = [
        rel_path
        for rel_path, fingerprint in current_files.items()
        if rel_path in baseline_files and baseline_files[rel_path] != fingerprint
    ]
    if task is not None:
        new_files, modified_files = _filter_diff_to_task_declared_paths(
            task=task,
            new_files=new_files,
            modified_files=modified_files,
            workspace_name=workspace_name,
        )
    all_affected_files = sorted(set(new_files + modified_files))
    return current_files, new_files, modified_files, all_affected_files


def _collect_workspace_out_of_scope_diff(
    *,
    task: dict[str, Any],
    baseline_files: dict[str, str],
    current_files: dict[str, str],
    workspace_name: str = "",
) -> dict[str, Any]:
    """Return real workspace changes that were filtered out by task path scope."""

    raw_new_files = sorted(set(current_files.keys()) - set(baseline_files.keys()))
    raw_modified_files = sorted(
        rel_path
        for rel_path, fingerprint in current_files.items()
        if rel_path in baseline_files and baseline_files[rel_path] != fingerprint
    )
    if not raw_new_files and not raw_modified_files:
        return {"new_files": [], "modified_files": [], "affected_files": []}

    scoped_new_files, scoped_modified_files = _filter_diff_to_task_declared_paths(
        task=task,
        new_files=raw_new_files,
        modified_files=raw_modified_files,
        workspace_name=workspace_name,
    )
    scoped_new = set(scoped_new_files)
    scoped_modified = set(scoped_modified_files)
    out_of_scope_new = [path for path in raw_new_files if path not in scoped_new]
    out_of_scope_modified = [path for path in raw_modified_files if path not in scoped_modified]
    return {
        "new_files": out_of_scope_new,
        "modified_files": out_of_scope_modified,
        "affected_files": sorted(set(out_of_scope_new + out_of_scope_modified)),
    }


def _first_failing_verify_clause(verify: str, *, cwd: str) -> str:
    """Clause-level teaching diagnosis — delegates to the KernelOne toolkit
    (single source of truth for the three verify touchpoints; includes the
    T2 measured-vs-required residual for machine-measurable clauses)."""
    from polaris.kernelone.quality.step_verify import first_failing_verify_clause

    return first_failing_verify_clause(verify, cwd=cwd)


_VERIFY_TEST_FILE_RE = re.compile(r"^test\s+-[fe]\s+(?P<path>\S+)$")
_VERIFY_GREP_FILE_RE = re.compile(r"^grep\s+(?:-[A-Za-z]+\s+)*(?P<quote>['\"]).*?(?P=quote)\s+(?P<path>\S+)\s*$")
_VERIFY_WC_PATH_RE = re.compile(r"wc\s+-l\s*<\s*(?P<path>[^)\]\s]+)")


def _clean_verify_path_token(raw: str) -> str:
    value = str(raw or "").strip().strip("'\"")
    if not value:
        return ""
    normalized = value.replace("\\", "/").removeprefix("./")
    if normalized.startswith("/") or normalized.startswith("~") or ".." in normalized.split("/"):
        return ""
    return normalized


def _verify_referenced_file_paths(verify: str) -> list[str]:
    from polaris.kernelone.quality.step_verify import split_verify_clauses

    paths: list[str] = []
    for clause in split_verify_clauses(verify):
        for pattern in (_VERIFY_TEST_FILE_RE, _VERIFY_GREP_FILE_RE):
            match = pattern.match(clause)
            if match is not None:
                cleaned = _clean_verify_path_token(match.group("path"))
                if cleaned:
                    paths.append(cleaned)
                break
        for match in _VERIFY_WC_PATH_RE.finditer(clause):
            cleaned = _clean_verify_path_token(match.group("path"))
            if cleaned:
                paths.append(cleaned)
    return _dedupe_preserve_order(paths)


def _path_stem_identity(path: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", Path(path).stem.lower())


def _near_miss_verify_target_paths(*, target_file: str, verify_paths: list[str]) -> list[str]:
    target = _clean_verify_path_token(target_file)
    if not target:
        return []
    if target in verify_paths:
        return []
    target_path = Path(target)
    target_suffix = target_path.suffix.lower()
    target_stem = _path_stem_identity(target)
    if not target_suffix or not target_stem:
        return []
    drifted: list[str] = []
    for item in verify_paths:
        candidate = Path(item)
        if candidate == target_path:
            return []
        if candidate.parent != target_path.parent:
            continue
        if candidate.suffix.lower() != target_suffix:
            continue
        if _path_stem_identity(item) == target_stem:
            drifted.append(item)
    return _dedupe_preserve_order(drifted)


def _step_verify_target_mismatch_error(step: dict[str, Any], verify: str) -> str:
    target = _single_file_step_target({"construction_step": step})
    if not target:
        return ""
    near_miss_paths = _near_miss_verify_target_paths(
        target_file=target,
        verify_paths=_verify_referenced_file_paths(verify),
    )
    if not near_miss_paths:
        return ""
    return (
        f"step verify target mismatch for {target}: verify references near-miss path(s) "
        f"{', '.join(near_miss_paths)}. Align construction_step.target_file and verify path before rerunning. "
        f"full: {verify}"
    )


def _collect_step_verify_errors(adapter: Any, context: dict[str, Any] | None) -> list[str]:
    """写后即查（三层裂变 DO 层自查）: run the construction step's machine
    verify inside the execution turn so the repair ladder sees the failure
    while the feedback loop is still seconds long — the exec→QA→bounce→exec
    market round trip costs ~3 cycles (~30min) per blind retry (live I3-r11).
    """
    if not isinstance(context, dict):
        return []
    from polaris.kernelone.quality.step_verify import (
        assess_legacy_step_verify_command_safety,
    )

    verify = resolve_contract_step_verify_command(context)
    if not verify:
        return []
    safety = assess_legacy_step_verify_command_safety(verify)
    if not safety.allowed:
        return [f"step verify command rejected by safety policy: {safety.reason} :: {verify!r}"]
    step = context.get("construction_step")
    if isinstance(step, dict):
        target_mismatch = _step_verify_target_mismatch_error(step, verify)
        if target_mismatch:
            return [target_mismatch]
    workspace = str(getattr(adapter, "workspace", "") or "")
    if not workspace or not os.path.isdir(workspace):
        return []
    try:
        proc = subprocess.run(
            verify,
            shell=True,
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return [f"step verify could not run: {exc} :: {verify!r}"]
    if proc.returncode == 0:
        return []
    output_tail = ((proc.stdout or "") + (proc.stderr or ""))[-300:]
    clause_detail = _first_failing_verify_clause(verify, cwd=workspace)
    # The actionable clause goes FIRST: downstream teaching channels truncate
    # (fail_task_stage 600 chars, blueprint step card 240) and a long verify
    # command would push the diagnosis off the visible end.
    if clause_detail:
        return [
            f"step verify failed (exit {proc.returncode}) | {clause_detail} | full: {verify} :: {output_tail}".strip()
        ]
    return [f"step verify failed (exit {proc.returncode}): {verify} :: {output_tail}".strip()]


def _single_file_step_target(source: Any) -> str:
    """Pin-eligibility mirror of roles.kernel ``extract_declared_step_target_files``:
    a single clean relative path, or "" when the turn is not a pinned step turn.
    """
    if not isinstance(source, dict):
        return ""
    step = source.get("construction_step")
    if not isinstance(step, dict):
        return ""
    target = str(step.get("target_file") or "").strip()
    if not target:
        return ""
    if any(ch in target for ch in ("*", "?", "[", "]", ",", " ", "\t", "\n", "\\")):
        return ""
    if target.startswith("/") or target.startswith("~") or ".." in target.split("/"):
        return ""
    return target.removeprefix("./")


def _collect_materialization_quality_errors(
    adapter: Any,
    *,
    task: dict[str, Any],
    all_affected_files: list[str],
    workspace_name: str,
    context: dict[str, Any] | None = None,
) -> list[str]:
    workspace_full = str(getattr(adapter, "workspace", "") or "")
    step_target = _single_file_step_target(context) or _single_file_step_target(task)
    if step_target:
        # Adversarial-review C-fix: a pinned single-file step turn is judged
        # only on the file it owns. Scanning package.json or other affected
        # files would demand repairs the enum-pinned write tools cannot
        # perform — a bounce loop that can never converge; junk in other
        # files belongs to the steps that own them.
        quality_scan_paths = [step_target]
    else:
        declared_target_paths = [
            normalized
            for candidate in _extract_task_target_path_candidates(task)
            if (
                normalized := _normalize_declared_task_path(
                    candidate,
                    workspace_name=workspace_name,
                )
            )
            and Path(normalized).suffix
            and "*" not in normalized
            and "?" not in normalized
        ]
        quality_scan_paths = _materialization_quality_scan_paths_with_package_manifest(
            workspace_full=workspace_full,
            affected_files=[*all_affected_files, *declared_target_paths],
        )
    errors = _em.scan_workspace_artifact_quality(
        workspace_full,
        relative_paths=quality_scan_paths,
    )
    errors.extend(
        _declared_target_file_quality_errors(
            workspace_full=workspace_full,
            task=task,
            workspace_name=workspace_name,
        )
    )
    return _dedupe_preserve_order(errors)


def _materialization_quality_scan_paths_with_package_manifest(
    *,
    workspace_full: str,
    affected_files: list[str],
) -> list[str]:
    paths = _dedupe_preserve_order(
        [_normalize_declared_task_path(path) for path in affected_files if _normalize_declared_task_path(path)]
    )
    if _node_package_manifest_should_be_rescanned_for_test_files(workspace_full=workspace_full, paths=paths):
        paths.append("package.json")
    return _dedupe_preserve_order(paths)


def _node_package_manifest_should_be_rescanned_for_test_files(*, workspace_full: str, paths: list[str]) -> bool:
    package_path = Path(str(workspace_full or "")).resolve() / "package.json"
    if not package_path.is_file():
        return False
    return any(_is_node_runtime_source_path(path) for path in paths)


def _is_node_runtime_source_path(path: str) -> bool:
    normalized = str(path or "").strip().replace("\\", "/").lower()
    if not normalized:
        return False
    name = Path(normalized).name
    if "/tests/" in f"/{normalized}" or "/test/" in f"/{normalized}" or ".test." in name or ".spec." in name:
        return False
    return Path(normalized).suffix in {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}


def _case_insensitive_file_match(target_path: Path) -> bool:
    """Return True when a sibling file matches ``target_path``'s name ignoring case.

    PM/CE often declare a target with different casing than the file the
    Director actually wrote (declared ``readme.md`` vs disk ``README.md``). On a
    case-sensitive filesystem the strict ``is_file`` check below would report the
    declared target missing and drive a spurious materialization-quality repair
    loop that never clears — failing an otherwise-complete, runnable product.
    The write-side already collapses case variants (the case-variant redirect),
    so this scan must agree. Mirrors the existence-gate / soft-check
    case-insensitive matching (F19/F20).
    """
    name_lower = target_path.name.lower()
    if not name_lower:
        return False
    try:
        return any(entry.name.lower() == name_lower and entry.is_file() for entry in target_path.parent.iterdir())
    except (FileNotFoundError, NotADirectoryError, PermissionError, OSError):
        return False


def _declared_target_file_quality_errors(
    *,
    workspace_full: str,
    task: dict[str, Any],
    workspace_name: str = "",
) -> list[str]:
    try:
        workspace_path = Path(workspace_full).resolve()
    except (OSError, RuntimeError, ValueError):
        return []
    if not workspace_path.exists() or not workspace_path.is_dir():
        return []

    errors: list[str] = []
    for candidate in _extract_task_target_path_candidates(task):
        normalized = _normalize_declared_task_path(candidate, workspace_name=workspace_name)
        if not normalized or any(ch in normalized for ch in ("*", "?")):
            continue
        target_path = (workspace_path / normalized).resolve()
        try:
            target_path.relative_to(workspace_path)
        except ValueError:
            continue
        if not Path(normalized).suffix:
            continue
        if not target_path.is_file() and not _case_insensitive_file_match(target_path):
            errors.append(f"Artifact quality scan failed: declared target file missing {normalized!r}")
    return errors


def _extract_successful_write_paths(tool_results: list[dict[str, Any]]) -> list[str]:
    paths: list[str] = []
    for item in tool_results:
        if not isinstance(item, dict) or not bool(item.get("success")):
            continue
        raw_result = item.get("result")
        result: dict[str, Any] = raw_result if isinstance(raw_result, dict) else {}
        for key in ("file", "path"):
            value = result.get(key)
            if isinstance(value, str) and value.strip():
                paths.append(_normalize_declared_task_path(value))
                break
    return _dedupe_preserve_order([path for path in paths if path])


def _merge_successful_write_paths(all_affected_files: list[str], write_paths: list[str]) -> list[str]:
    return sorted({*all_affected_files, *write_paths})


def _materialization_quality_scan_paths(
    all_affected_files: list[str],
    tool_results: list[dict[str, Any]],
) -> list[str]:
    return _merge_successful_write_paths(
        all_affected_files,
        _extract_successful_write_paths(tool_results),
    )


async def _run_materialization_quality_repair_retry(
    adapter: Any,
    *,
    task: dict[str, Any],
    target_task_id: str,
    run_id: str,
    context: dict[str, Any],
    original_message: str,
    llm_call_timeout: float,
    artifact_quality_errors: list[str],
    changed_files: list[str],
    repair_attempt: int = 1,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Ask Director for one concrete repair when changed artifacts fail quality gates."""

    if not artifact_quality_errors:
        return [], {"attempted": False, "reason": "no_artifact_quality_errors"}

    workspace_full = str(getattr(adapter, "workspace", "") or "")
    repair_quality_errors = _tool_receipt_safe_quality_errors(artifact_quality_errors)
    missing_target_files = _missing_materialization_quality_repair_target_files(
        task,
        workspace_full,
        repair_quality_errors,
    )
    missing_script_entrypoint_files = _missing_npm_script_entrypoint_repair_target_files(
        artifact_quality_errors=repair_quality_errors,
        workspace_full=workspace_full,
    )
    missing_target_files = _dedupe_preserve_order([*missing_target_files, *missing_script_entrypoint_files])
    runtime_smoke_target_files = _dedupe_preserve_order(
        [
            *_python_runtime_smoke_repair_target_files(
                artifact_quality_errors=repair_quality_errors,
                changed_files=changed_files,
                workspace_full=workspace_full,
            ),
            *_javascript_runtime_smoke_repair_target_files(
                artifact_quality_errors=repair_quality_errors,
                changed_files=changed_files,
                workspace_full=workspace_full,
            ),
            *_go_runtime_smoke_repair_target_files(
                artifact_quality_errors=repair_quality_errors,
                changed_files=changed_files,
                workspace_full=workspace_full,
            ),
        ]
    )
    semantic_quality_target_files = _semantic_quality_repair_target_files(
        artifact_quality_errors=repair_quality_errors,
        changed_files=changed_files,
        workspace_full=workspace_full,
    )
    explicit_quality_target_files = _explicit_artifact_quality_repair_target_files(
        artifact_quality_errors=repair_quality_errors,
        changed_files=changed_files,
        workspace_full=workspace_full,
    )
    explicit_missing_quality_targets = _dedupe_preserve_order(
        [
            *[
                rel
                for item in _em._parse_missing_declared_target_files(repair_quality_errors)
                if (rel := _normalize_declared_task_path(item))
            ],
            *_em._missing_unresolved_relative_import_target_files(repair_quality_errors, workspace_full),
            *_missing_workspace_file_quality_repair_target_files(
                artifact_quality_errors=repair_quality_errors,
                workspace_full=workspace_full,
            ),
            *_missing_python_module_alias_repair_target_files(
                artifact_quality_errors=repair_quality_errors,
                workspace_full=workspace_full,
            ),
            *missing_script_entrypoint_files,
        ]
    )
    should_merge_missing_targets = bool(explicit_missing_quality_targets) or not (
        runtime_smoke_target_files or semantic_quality_target_files or explicit_quality_target_files
    )
    repair_target_candidates = _ordered_materialization_quality_repair_target_candidates(
        missing_target_files=missing_target_files,
        runtime_smoke_target_files=runtime_smoke_target_files,
        semantic_quality_target_files=semantic_quality_target_files,
        explicit_quality_target_files=explicit_quality_target_files,
        should_merge_missing_targets=should_merge_missing_targets,
    )
    rotate_repair_targets = bool(
        len(repair_target_candidates) > 1
        and semantic_quality_target_files
        and _should_rotate_materialization_quality_repair_targets(repair_quality_errors)
    )
    repair_target_files = _select_materialization_quality_repair_target_batch(
        repair_target_candidates,
        repair_attempt=repair_attempt,
        rotate_after_first_attempt=rotate_repair_targets,
        preserve_batch_after_first_attempt=_should_preserve_materialization_quality_repair_batch(
            repair_quality_errors,
            repair_target_candidates=repair_target_candidates,
        ),
    )
    missing_target_set = set(missing_target_files)
    missing_repair_target_files = [path for path in repair_target_files if path in missing_target_set]
    existing_repair_target_files = [path for path in repair_target_files if path not in missing_target_set]
    deterministic_quality_tool_results: list[dict[str, Any]] = []
    deterministic_quality_summary: dict[str, Any] = {}
    if _has_scaffold_marker_quality_error(repair_quality_errors) or has_materialization_quality_runtime_repair_coverage(
        repair_quality_errors
    ):
        deterministic_quality_tool_results, deterministic_quality_summary = run_materialization_quality_repairs(
            adapter,
            task=task,
            task_id=target_task_id,
            artifact_quality_errors=repair_quality_errors,
        )
    if deterministic_quality_tool_results and has_successful_write_tool(deterministic_quality_tool_results):
        summary = dict(deterministic_quality_summary or {})
        summary.update(
            {
                "stage": "deterministic_materialization_quality_repair",
                "attempted": True,
                "attempt": repair_attempt,
                "success": False,
                "success_reason": "repair_actions_require_quality_gate_rerun",
                "tool_results": len(deterministic_quality_tool_results),
                "write_tool_evidence": True,
                "missing_target_files": missing_target_files[:12],
                "runtime_smoke_target_files": runtime_smoke_target_files[:12],
                "semantic_quality_target_files": semantic_quality_target_files[:12],
                "explicit_quality_target_files": explicit_quality_target_files[:12],
                "repair_target_files": repair_target_files[:12],
                "rotated_repair_targets": rotate_repair_targets,
                "repair_kernel": project_repair_kernel_summary(
                    stage="deterministic_materialization_quality_repair",
                    tool_results=deterministic_quality_tool_results,
                    artifact_quality_errors=repair_quality_errors,
                ),
            }
        )
        return deterministic_quality_tool_results, summary
    deterministic_semantic_tool_results = run_typescript_semantic_quality_repairs(
        adapter,
        task_id=target_task_id,
        artifact_quality_errors=repair_quality_errors,
    )
    if deterministic_semantic_tool_results and has_successful_write_tool(deterministic_semantic_tool_results):
        source_tools: list[str] = []
        for item in deterministic_semantic_tool_results:
            result = item.get("result")
            if isinstance(result, dict):
                source_tools.append(str(result.get("source_tool") or ""))
        return deterministic_semantic_tool_results, {
            "stage": "deterministic_semantic_quality_repair",
            "attempted": True,
            "attempt": repair_attempt,
            "success": False,
            "success_reason": "repair_actions_require_quality_gate_rerun",
            "tool_results": len(deterministic_semantic_tool_results),
            "write_tool_evidence": True,
            "missing_target_files": missing_target_files[:12],
            "runtime_smoke_target_files": runtime_smoke_target_files[:12],
            "semantic_quality_target_files": semantic_quality_target_files[:12],
            "explicit_quality_target_files": explicit_quality_target_files[:12],
            "repair_target_files": repair_target_files[:12],
            "rotated_repair_targets": rotate_repair_targets,
            "source_tools": source_tools,
            "repair_kernel": project_repair_kernel_summary(
                stage="deterministic_semantic_quality_repair",
                tool_results=deterministic_semantic_tool_results,
                artifact_quality_errors=repair_quality_errors,
            ),
        }
    prompt_artifact_quality_errors = _filter_materialization_quality_errors_for_repair_targets(
        artifact_quality_errors,
        repair_target_files,
    )
    prompt_safe_artifact_quality_errors = [
        _format_quality_error_for_repair_prompt(error) for error in prompt_artifact_quality_errors[:20]
    ]
    repair_message = _build_materialization_quality_repair_message(
        original_message=original_message,
        artifact_quality_errors=prompt_artifact_quality_errors,
        directive_artifact_quality_errors=artifact_quality_errors,
        changed_files=changed_files,
        missing_target_files=missing_repair_target_files,
        repair_target_files=existing_repair_target_files,
        workspace_full=workspace_full,
    )
    repair_context = {
        **dict(context or {}),
        "run_id": run_id,
        "task_id": target_task_id,
        "delivery_mode": "materialize_changes",
        "director_quality_repair": {
            "artifact_quality_errors": prompt_safe_artifact_quality_errors,
            "changed_files": changed_files[:40],
            "missing_target_files": missing_target_files[:20],
            "runtime_smoke_target_files": runtime_smoke_target_files[:20],
            "semantic_quality_target_files": semantic_quality_target_files[:20],
            "explicit_quality_target_files": explicit_quality_target_files[:20],
            "repair_target_files": repair_target_files[:12],
        },
    }
    if repair_target_files:
        repair_context["repair_target_files"] = repair_target_files[:12]
    repair_metadata = repair_context.get("metadata")
    if not isinstance(repair_metadata, dict):
        repair_metadata = {}
        repair_context["metadata"] = repair_metadata
    repair_metadata["delivery_mode"] = "materialize_changes"
    repair_metadata["task_id"] = target_task_id
    if repair_target_files:
        if missing_repair_target_files and not existing_repair_target_files:
            # Missing-file repair is creation, so keep the historically narrow
            # write-only path. Existing-file compiler/test repair is different:
            # forcing whole-file writes steers weak models into destructive
            # shrink attempts, so that case uses edit-preferred tools below.
            repair_context["_transaction_kernel_forced_tool_choice"] = {
                "type": "function",
                "function": {"name": "write_file"},
            }
            repair_context["_transaction_kernel_forced_tool_definitions"] = [
                _quality_repair_write_file_tool_definition()
            ]
            repair_context["_transaction_kernel_force_exact_tools"] = True
        else:
            repair_context["_transaction_kernel_forced_tool_definitions"] = [
                _quality_repair_edit_file_tool_definition(),
                _quality_repair_write_file_tool_definition(),
            ]
            repair_context["director_quality_repair"]["edit_preferred_target_files"] = existing_repair_target_files[:12]
        if len(missing_repair_target_files) == 1 and not existing_repair_target_files:
            # Single-missing: also name the specific target file in the
            # context, so any downstream code that special-cases a single
            # target can read it from director_quality_repair.
            repair_context["director_quality_repair"]["write_only_single_target"] = {
                "tool": "write_file",
                "target_file": missing_repair_target_files[0],
            }
    try:
        result = await adapter._invoke_role_dialogue_with_timeout(
            repair_message,
            context=repair_context,
            timeout_seconds=_resolve_quality_repair_timeout_seconds(llm_call_timeout),
            stage_label="quality_repair" if repair_attempt <= 1 else f"quality_repair_{repair_attempt}",
        )
    except Exception as exc:  # noqa: BLE001 - quality repair is a structured fallback boundary.
        repair_tool_results: list[dict[str, Any]] = []
        repair_tool_results.extend(
            _deterministic_single_missing_quality_repair_to_write_file(
                adapter,
                task_id=target_task_id,
                repair_target_files=repair_target_files,
                artifact_quality_errors=repair_quality_errors,
            )
        )
        if not repair_tool_results or not has_successful_write_tool(repair_tool_results):
            repair_tool_results.extend(
                _deterministic_single_missing_python_module_alias_to_write_file(
                    adapter,
                    task_id=target_task_id,
                    repair_target_files=repair_target_files,
                    artifact_quality_errors=repair_quality_errors,
                )
            )
        return repair_tool_results, {
            "attempted": True,
            "attempt": repair_attempt,
            "success": False,
            "error": str(exc),
            "tool_results": len(repair_tool_results),
            "write_tool_evidence": has_successful_write_tool(repair_tool_results),
            "missing_target_files": missing_target_files[:12],
            "runtime_smoke_target_files": runtime_smoke_target_files[:12],
            "semantic_quality_target_files": semantic_quality_target_files[:12],
            "explicit_quality_target_files": explicit_quality_target_files[:12],
            "repair_target_files": repair_target_files[:12],
            "rotated_repair_targets": rotate_repair_targets,
        }

    content = str(result.get("content") or "")
    repair_tool_results = adapter._execution.extract_kernel_tool_results(result)
    if not repair_tool_results or not has_successful_write_tool(repair_tool_results):
        allowed_tool_names = None
        allow_patch_fallback = True
        if repair_target_files and not existing_repair_target_files:
            allowed_tool_names = {"write_file"}
            allow_patch_fallback = False
        elif repair_target_files:
            allowed_tool_names = {"edit_file", "write_file"}
        fallback_tool_results = await adapter._execution.execute_tools(
            content,
            target_task_id,
            adapter._update_task_progress,
            allowed_tool_names=allowed_tool_names,
            allow_patch_fallback=allow_patch_fallback,
        )
        if fallback_tool_results:
            repair_tool_results.extend(fallback_tool_results)
    if not repair_tool_results or not has_successful_write_tool(repair_tool_results):
        repair_tool_results.extend(
            _coerce_raw_single_target_repair_to_write_file(
                adapter,
                task_id=target_task_id,
                repair_target_files=repair_target_files,
                content=content,
            )
        )
    if not repair_tool_results or not has_successful_write_tool(repair_tool_results):
        repair_tool_results.extend(
            _deterministic_single_missing_quality_repair_to_write_file(
                adapter,
                task_id=target_task_id,
                repair_target_files=repair_target_files,
                artifact_quality_errors=repair_quality_errors,
            )
        )
    if not repair_tool_results or not has_successful_write_tool(repair_tool_results):
        repair_tool_results.extend(
            _deterministic_single_missing_python_module_alias_to_write_file(
                adapter,
                task_id=target_task_id,
                repair_target_files=repair_target_files,
                artifact_quality_errors=repair_quality_errors,
            )
        )

    summary = _summarize_llm_stage_result(result, stage="quality_repair")
    summary.update(
        {
            "attempted": True,
            "attempt": repair_attempt,
            "tool_results": len(repair_tool_results),
            "write_tool_evidence": has_successful_write_tool(repair_tool_results),
            "missing_target_files": missing_target_files[:12],
            "runtime_smoke_target_files": runtime_smoke_target_files[:12],
            "semantic_quality_target_files": semantic_quality_target_files[:12],
            "explicit_quality_target_files": explicit_quality_target_files[:12],
            "repair_target_files": repair_target_files[:12],
            "rotated_repair_targets": rotate_repair_targets,
        }
    )
    return repair_tool_results, summary


_QUALITY_REPAIR_BASE_ATTEMPTS = 2


_QUALITY_REPAIR_ATTEMPT_HARD_CAP = 5


_QUALITY_REPAIR_TARGET_BATCH_LIMIT = 12


_DEFAULT_QUALITY_REPAIR_TIMEOUT_SECONDS = 180.0


def _resolve_quality_repair_timeout_seconds(primary_timeout_seconds: float) -> float:
    raw_timeout = os.environ.get("KERNELONE_DIRECTOR_QUALITY_REPAIR_TIMEOUT_SECONDS")
    configured = _DEFAULT_QUALITY_REPAIR_TIMEOUT_SECONDS
    if raw_timeout is not None:
        try:
            parsed = float(raw_timeout)
        except (TypeError, ValueError):
            parsed = configured
        if parsed > 0:
            configured = parsed
    configured = max(30.0, min(configured, 300.0))
    try:
        primary = float(primary_timeout_seconds)
    except (TypeError, ValueError):
        primary = configured
    if primary <= 0:
        primary = configured
    return max(0.1, min(primary, configured))


def _select_materialization_quality_repair_target_batch(
    missing_target_files: list[str],
    *,
    repair_attempt: int = 1,
    rotate_after_first_attempt: bool = False,
    preserve_batch_after_first_attempt: bool = False,
) -> list[str]:
    """Select the missing targets to repair in a single LLM attempt."""

    if preserve_batch_after_first_attempt:
        return list(missing_target_files[:_QUALITY_REPAIR_TARGET_BATCH_LIMIT])
    if repair_attempt > 1 and missing_target_files:
        if rotate_after_first_attempt:
            target_index = (repair_attempt - 1) % len(missing_target_files)
            return [missing_target_files[target_index]]
        return [missing_target_files[0]]
    return list(missing_target_files[:_QUALITY_REPAIR_TARGET_BATCH_LIMIT])


def _ordered_materialization_quality_repair_target_candidates(
    *,
    missing_target_files: list[str],
    runtime_smoke_target_files: list[str],
    semantic_quality_target_files: list[str],
    explicit_quality_target_files: list[str],
    should_merge_missing_targets: bool,
) -> list[str]:
    missing_repair_candidates = missing_target_files if should_merge_missing_targets else []
    runtime_targets_precede_missing = _runtime_quality_targets_should_precede_missing(runtime_smoke_target_files)
    if semantic_quality_target_files or explicit_quality_target_files:
        source_missing_candidates = [
            path for path in missing_repair_candidates if not _is_generated_quality_repair_target(path)
        ]
        generated_missing_candidates = [
            path for path in missing_repair_candidates if _is_generated_quality_repair_target(path)
        ]
        return _dedupe_preserve_order(
            [
                *source_missing_candidates,
                *semantic_quality_target_files,
                *explicit_quality_target_files,
                *runtime_smoke_target_files,
                *generated_missing_candidates,
            ]
        )
    if runtime_targets_precede_missing:
        return _dedupe_preserve_order(
            [
                *runtime_smoke_target_files,
                *missing_repair_candidates,
                *semantic_quality_target_files,
                *explicit_quality_target_files,
            ]
        )
    return _dedupe_preserve_order(
        [
            *missing_repair_candidates,
            *runtime_smoke_target_files,
            *semantic_quality_target_files,
            *explicit_quality_target_files,
        ]
    )


def _runtime_quality_targets_should_precede_missing(runtime_smoke_target_files: list[str]) -> bool:
    return any(str(path or "").endswith(".go") for path in runtime_smoke_target_files)


def _filter_materialization_quality_errors_for_repair_targets(
    artifact_quality_errors: list[str],
    repair_target_files: list[str],
) -> list[str]:
    """Keep prompt feedback aligned with the currently leased repair scope."""

    normalized_targets = [
        target for target in (_normalize_declared_task_path(item) for item in repair_target_files) if target
    ]
    if not normalized_targets:
        return list(artifact_quality_errors)
    filtered = [
        error
        for error in artifact_quality_errors
        if any(target in str(error or "").replace("\\", "/") for target in normalized_targets)
    ]
    return filtered or list(artifact_quality_errors)


def _should_rotate_materialization_quality_repair_targets(artifact_quality_errors: list[str]) -> bool:
    joined_errors = "\n".join(str(item or "").lower() for item in artifact_quality_errors)
    return any(
        hint in joined_errors
        for hint in (
            "typescript project typecheck failed",
            "tsc --noemit failed",
            "error ts",
        )
    )


def _should_preserve_materialization_quality_repair_batch(
    artifact_quality_errors: list[str],
    *,
    repair_target_candidates: list[str] | None = None,
) -> bool:
    joined_errors = "\n".join(str(item or "").lower() for item in artifact_quality_errors)
    if _looks_like_go_workspace_quality_error(joined_errors) and _GO_COMPILE_PATH_RE.search(joined_errors):
        return True
    if _artifact_quality_failed_test_count(artifact_quality_errors) >= 2:
        return True
    if "python runtime smoke" in joined_errors and (
        "assertregex" in joined_errors or "regex didn't match" in joined_errors
    ):
        return True
    if (
        "referenceerror: require is not defined" in joined_errors
        or "module is not defined in es module scope" in joined_errors
        or "is not defined in es module scope" in joined_errors
        or "cannot use import statement outside a module" in joined_errors
    ):
        return True
    if "unresolved import symbol" in joined_errors or "has no exported member" in joined_errors:
        return True
    if _looks_like_python_missing_module_failure(joined_errors) and _has_non_test_python_traceback_source(
        joined_errors
    ):
        return True
    if "ts18046" in joined_errors or "is of type 'unknown'" in joined_errors or 'is of type "unknown"' in joined_errors:
        return True
    if "ts2693" in joined_errors or "only refers to a type" in joined_errors:
        return True
    if repair_target_candidates and _should_preserve_python_cross_language_harness_repair_batch(
        joined_errors,
        repair_target_candidates,
    ):
        return True
    coupled_hints = (
        "unresolved import symbol",
        "typescript project typecheck failed",
        "npm package manifest script",
        "npm package manifest has test runner script",
    )
    return sum(1 for hint in coupled_hints if hint in joined_errors) >= 2


def _should_preserve_python_cross_language_harness_repair_batch(
    joined_errors: str,
    repair_target_candidates: list[str],
) -> bool:
    if not _looks_like_python_test_behavior_failure(joined_errors):
        return False
    if not _looks_like_python_test_harness_quality_failure(joined_errors):
        return False
    production_targets: list[str] = []
    for item in repair_target_candidates:
        rel = _normalize_declared_task_path(str(item or ""))
        if not rel:
            continue
        suffix = Path(rel).suffix.lower()
        if suffix not in _SOURCE_REPAIR_EXTENSIONS:
            continue
        if _is_test_like_python_path(rel) or _is_test_like_javascript_path(rel):
            continue
        production_targets.append(rel)
    if len(_dedupe_preserve_order(production_targets)) < 2:
        return False
    return any(not target.endswith(".py") for target in production_targets)


def _has_scaffold_marker_quality_error(artifact_quality_errors: list[str]) -> bool:
    joined_errors = "\n".join(str(item or "").lower() for item in artifact_quality_errors)
    return "generic/placeholder content detected" in joined_errors or "deterministic scaffold marker" in joined_errors


_PYTHON_RUNTIME_SMOKE_TARGET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"python runtime smoke (?:crashed|timed out|was killed) for (?P<target>['\"`][^'\"`]+['\"`]|[^:\s;]+)",
        re.IGNORECASE,
    ),
    re.compile(
        r"python runtime smoke could not launch (?P<target>['\"`][^'\"`]+['\"`]|[^:\s;]+)",
        re.IGNORECASE,
    ),
)

_PYTHON_TRACEBACK_FILE_RE = re.compile(r'File "(?P<path>[^"]+)", line \d+', re.IGNORECASE)
_MISSING_WORKSPACE_FILE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"No such file or directory:\s*(?P<path>['\"`][^'\"`]+['\"`]|[^;\n]+)",
        re.IGNORECASE,
    ),
    re.compile(
        r"must exist at\s+(?P<path>['\"`][^'\"`]+['\"`]|[^\s;\n]+)",
        re.IGNORECASE,
    ),
    re.compile(
        r"missing or empty:\s*(?P<path>['\"`][^'\"`]+['\"`]|[^\s;\n]+)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?P<path>(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.(?:c|cc|cfg|conf|cpp|css|cxx|env|go|h|hpp|html|ini|java|js|jsx|json|lock|md|py|rs|rst|toml|ts|tsx|txt|xml|yaml|yml))"
        r"\s+must\s+(?:be|contain|declare|exist|include|provide)\b",
        re.IGNORECASE,
    ),
)
_REQUIREMENTS_TXT_ASSERT_IN_DEP_RE = re.compile(
    r"assertIn\(\s*['\"](?P<package>[A-Za-z0-9][A-Za-z0-9_.-]*)['\"]",
    re.IGNORECASE,
)
_REQUIREMENTS_TXT_MUST_DECLARE_DEP_RE = re.compile(
    r"requirements\.txt\s+must\s+declare\s+(?P<package>[A-Za-z0-9][A-Za-z0-9_.-]*)",
    re.IGNORECASE,
)
_REQUIREMENTS_TXT_NON_PACKAGE_WORDS: frozenset[str] = frozenset(
    {
        "a",
        "an",
        "at",
        "dependency",
        "dependencies",
        "least",
        "one",
        "package",
        "packages",
    }
)
_MISSING_WORKSPACE_ROOT_FILE_ALLOWLIST: frozenset[str] = frozenset(
    {
        "README.md",
        "README.rst",
        "app.py",
        "index.html",
        "main.py",
        "package.json",
        "pyproject.toml",
        "requirements.txt",
        "setup.cfg",
        "setup.py",
        "tsconfig.json",
    }
)
_MISSING_WORKSPACE_FILE_ALLOWED_PREFIXES: tuple[str, ...] = (
    "config/",
    "configs/",
    "data/",
    "docs/",
    "scripts/",
    "src/",
    "test/",
    "tests/",
)
_PYTHON_MODULE_NOT_FOUND_RE = re.compile(
    r"ModuleNotFoundError:\s+No module named ['\"](?P<module>[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)['\"]",
    re.IGNORECASE,
)
_SEMANTIC_QUALITY_EXPLICIT_PATH_RE = re.compile(
    r"(?P<path>(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.(?:c|cc|cpp|cxx|go|h|hpp|html|java|js|jsx|json|md|py|rs|ts|tsx|css))(?=[:\s(]|$)",
    re.IGNORECASE,
)
_RUST_COMPILE_PATH_RE = re.compile(
    r"(?P<path>(?:[A-Za-z]:)?[^\s:()'\"\n>]+?\.rs):\d+:\d+",
    re.IGNORECASE,
)
_FAILED_TEST_TITLE_RE = re.compile(
    r"^\s*(?:not\s+ok\s+\d+|failed|fail)\s*(?:[-:]\s*)?(?P<title>.+)$",
    re.IGNORECASE | re.MULTILINE,
)
_TAP_FAILED_TEST_RE = re.compile(r"^\s*not\s+ok\s+\d+\b", re.IGNORECASE | re.MULTILINE)
_TEST_SUMMARY_FAIL_RE = re.compile(r"^\s*#?\s*fail\s+(?P<count>\d+)\s*$", re.IGNORECASE | re.MULTILINE)
_PYTHON_UNITTEST_RESULT_LINE_RE = re.compile(
    r"^\s*\S+\s+\((?P<module>[^)]+)\)\s+\.\.\.\s+(?:ERROR|FAIL|FAILED)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_TS_NO_EXPORTED_MEMBER_QUALITY_RE = re.compile(
    r"(?P<path>(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.tsx?)\(\d+,\d+\):\s*"
    r"error\s+TS2305:\s*Module\s+['\"](?P<module>.+?)['\"]\s+has no exported member",
    re.IGNORECASE,
)
_TS_DIAGNOSTIC_PATH_RE = re.compile(
    r"(?P<path>(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.tsx?)\(\d+,\d+\):\s*error\s+TS\d+:",
    re.IGNORECASE,
)
_TS_UNKNOWN_VALUE_QUALITY_RE = re.compile(
    r"(?P<path>(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.tsx?)\(\d+,\d+\):\s*"
    r"error\s+TS18046:\s*['\"](?P<symbol>[A-Za-z_$][A-Za-z0-9_$]*)['\"]\s+is\s+of\s+type\s+"
    r"['\"]unknown['\"]",
    re.IGNORECASE,
)
_TS_TYPE_ONLY_VALUE_QUALITY_RE = re.compile(
    r"(?P<path>(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.tsx?)\(\d+,\d+\):\s*"
    r"error\s+TS2693:\s*['\"](?P<symbol>[A-Za-z_$][A-Za-z0-9_$]*)['\"]\s+only\s+refers\s+"
    r"to\s+a\s+type,\s+but\s+is\s+being\s+used\s+as\s+a\s+value\s+here",
    re.IGNORECASE,
)
_TS_EXPORTED_DECLARATION_TEMPLATE = (
    r"\bexport\s+(?:declare\s+)?(?:(?:const|let|var|function|class|interface|type)\s+)"
    r"{symbol}\b"
)


def _python_runtime_smoke_repair_target_files(
    *,
    artifact_quality_errors: list[str],
    changed_files: list[str],
    workspace_full: str = "",
) -> list[str]:
    """Extract existing Python files that failed Polaris' own runtime smoke.

    This intentionally trusts only quality-gate error strings emitted by
    ``_apply_deterministic_python_runtime_smoke``. The target may come from a
    prior task in the same Director run, so accept it when it is either one of
    the files written in the current repair turn or an existing Python file
    inside the workspace. That keeps arbitrary traceback paths from seeding
    repair scope while still repairing cross-task runtime smoke failures.
    """

    changed_python_files = {
        rel for item in changed_files if (rel := _normalize_declared_task_path(str(item or ""))) and rel.endswith(".py")
    }
    workspace_root = Path(str(workspace_full or "")).resolve() if str(workspace_full or "").strip() else None

    targets: list[str] = []
    for item in artifact_quality_errors:
        text = str(item or "")
        if not _looks_like_python_runtime_smoke_quality_error(text):
            continue
        if workspace_root is not None and workspace_root.is_dir() and _looks_like_python_test_behavior_failure(text):
            targets.extend(_embedded_rust_compile_repair_target_files(text, workspace_root))
            if _looks_like_python_missing_module_failure(text):
                targets.extend(
                    rel
                    for rel in _python_runtime_smoke_traceback_repair_target_files(text, workspace_root)
                    if not _is_test_like_python_path(rel)
                )
            regex_source_failure = _looks_like_python_regex_source_quality_failure(text)
            if regex_source_failure:
                targets.extend(_changed_source_repair_target_files(changed_files, workspace_root))
            cli_subcommand_failure = _looks_like_cli_subcommand_quality_failure(text)
            if cli_subcommand_failure:
                entrypoints = _workspace_cli_entrypoint_repair_target_files(workspace_root)
                targets.extend(entrypoints or _changed_source_repair_target_files(changed_files, workspace_root))
            if not regex_source_failure and not cli_subcommand_failure:
                targets.extend(_python_test_harness_changed_source_target_files(text, changed_files, workspace_root))
            failed_test_targets = _python_unittest_failure_test_target_files(text, workspace_root)
            for rel in failed_test_targets:
                targets.extend(
                    _python_runtime_smoke_imported_source_target_files(
                        rel,
                        workspace_root,
                        include_missing_src_imports=True,
                    )
                )
            targets.extend(_python_runtime_smoke_missing_module_source_targets(text, workspace_root))
            targets.extend(failed_test_targets)
        for pattern in _PYTHON_RUNTIME_SMOKE_TARGET_PATTERNS:
            match = pattern.search(text)
            if not match:
                continue
            rel = _normalize_declared_task_path(match.group("target"))
            workspace_target_exists = (
                workspace_root is not None
                and workspace_root.is_dir()
                and _workspace_path_exists_case_insensitive(workspace_root, rel)
            )
            if rel.endswith(".py") and (rel in changed_python_files or workspace_target_exists):
                if workspace_root is not None and workspace_root.is_dir():
                    if _is_test_like_python_path(rel):
                        targets.extend(
                            item
                            for item in _python_runtime_smoke_traceback_repair_target_files(text, workspace_root)
                            if item != rel and not _is_test_like_python_path(item)
                        )
                        targets.extend(
                            _python_runtime_smoke_imported_source_target_files(
                                rel,
                                workspace_root,
                                include_missing_src_imports=True,
                            )
                        )
                        targets.append(rel)
                    elif _looks_like_python_missing_module_failure(text):
                        targets.append(rel)
                        targets.extend(_python_runtime_smoke_imported_source_target_files(rel, workspace_root))
                    elif _looks_like_python_module_coupling_failure(text):
                        targets.extend(_python_runtime_smoke_imported_source_target_files(rel, workspace_root))
                        targets.append(rel)
                    else:
                        targets.append(rel)
                else:
                    targets.append(rel)
            break
    return _dedupe_preserve_order(targets)


def _embedded_rust_compile_repair_target_files(text: str, workspace_root: Path) -> list[str]:
    if not _looks_like_embedded_rust_compile_failure(text):
        return []
    targets: list[str] = []
    for match in _RUST_COMPILE_PATH_RE.finditer(str(text or "")):
        rel = _workspace_relative_rust_repair_target(str(match.group("path") or ""), workspace_root)
        if rel:
            targets.append(rel)
    lowered = str(text or "").lower()
    if (
        "could not compile" in lowered
        or "previous errors" in lowered
        or "some errors have detailed explanations" in lowered
    ):
        targets.extend(_workspace_rust_source_repair_target_files(workspace_root))
    return _dedupe_preserve_order(targets)[:_QUALITY_REPAIR_TARGET_BATCH_LIMIT]


def _looks_like_embedded_rust_compile_failure(text: str) -> bool:
    lowered = str(text or "").lower()
    if not any(hint in lowered for hint in ("cargo check", "cargo build", "cargo test", "rustc", "could not compile")):
        return False
    return "error[" in lowered or ".rs:" in lowered or "could not compile" in lowered


def _workspace_relative_rust_repair_target(raw_path: str, workspace_root: Path) -> str:
    token = str(raw_path or "").strip().strip("'\"`>").replace("\\", "/")
    if not token:
        return ""
    candidate = Path(token)
    if candidate.is_absolute():
        try:
            rel = candidate.resolve().relative_to(workspace_root.resolve()).as_posix()
        except (OSError, RuntimeError, ValueError):
            return ""
    else:
        rel = _normalize_declared_task_path(token)
    if not rel.endswith(".rs"):
        return ""
    if not _workspace_path_exists_case_insensitive(workspace_root, rel):
        return ""
    return rel


def _workspace_rust_source_repair_target_files(workspace_root: Path) -> list[str]:
    src_dir = workspace_root / "src"
    if not src_dir.is_dir():
        return []
    targets: list[str] = []
    for path in sorted(src_dir.rglob("*.rs"), key=lambda item: item.as_posix()):
        if not path.is_file():
            continue
        try:
            targets.append(path.relative_to(workspace_root).as_posix())
        except ValueError:
            continue
    return _dedupe_preserve_order(targets)[:_QUALITY_REPAIR_TARGET_BATCH_LIMIT]


_GO_RUN_COMMAND_TARGET_RE = re.compile(r"(?:^|[\s(>])go\s+run\s+(?P<target>(?!-)[^\s'\"\n]+)")
_GO_COMPILE_PATH_RE = re.compile(r"(?P<path>(?:[A-Za-z]:)?[^\s:()'\"\n]+?\.go):\d+:\d+")
_GO_MISSING_MEMBER_TYPE_RE = re.compile(
    r"type\s+\*?(?:(?P<package_name>[A-Za-z_][A-Za-z0-9_]*)\.)?"
    r"(?P<type_name>[A-Za-z_][A-Za-z0-9_]*)\s+has\s+no\s+field\s+or\s+method",
    re.IGNORECASE,
)
_GO_IMPORT_SPEC_RE = re.compile(r"(?m)^\s*(?:(?P<alias>[A-Za-z_][A-Za-z0-9_]*|\.)\s+)?\"(?P<import_path>[^\"]+)\"")
_GO_TEST_FAILURE_TITLE_RE = re.compile(
    r"(?:^|[\n:])\s*---\s+FAIL:\s+(?P<title>[A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE | re.MULTILINE,
)


def _go_runtime_smoke_repair_target_files(
    *,
    artifact_quality_errors: list[str],
    changed_files: list[str],
    workspace_full: str = "",
) -> list[str]:
    """Extract Go entrypoint/source files that failed a workspace runtime smoke."""

    changed_go_files = {
        rel for item in changed_files if (rel := _normalize_declared_task_path(str(item or ""))) and rel.endswith(".go")
    }
    workspace_root = Path(str(workspace_full or "")).resolve() if str(workspace_full or "").strip() else None
    targets: list[str] = []
    for item in artifact_quality_errors:
        text = str(item or "")
        if not _looks_like_go_workspace_quality_error(text):
            continue
        if workspace_root is not None and workspace_root.is_dir():
            targets.extend(_go_compile_error_target_files(text, workspace_root))
            targets.extend(_go_runtime_smoke_command_target_files(text, workspace_root))
            targets.extend(
                _go_test_behavior_repair_target_files(
                    text=text,
                    changed_files=changed_go_files,
                    workspace_root=workspace_root,
                )
            )
            if not targets:
                targets.extend(_workspace_go_entrypoint_repair_target_files(workspace_root))
        if not targets:
            targets.extend(sorted(changed_go_files))
    return _dedupe_preserve_order(targets)


def _looks_like_go_workspace_quality_error(text: str) -> bool:
    lowered = str(text or "").lower()
    if "workspace validation command failed" in lowered and ("go run" in lowered or "go test" in lowered):
        return True
    return ("go test" in lowered or "go compile" in lowered) and ".go:" in lowered


def _go_runtime_smoke_command_target_files(text: str, workspace_root: Path) -> list[str]:
    targets: list[str] = []
    for match in _GO_RUN_COMMAND_TARGET_RE.finditer(text):
        raw_target = str(match.group("target") or "").strip()
        if not raw_target:
            continue
        if raw_target in {".", "./"}:
            targets.extend(_workspace_go_entrypoint_repair_target_files(workspace_root))
            continue
        rel = _normalize_declared_task_path(raw_target)
        if not rel:
            continue
        candidate = workspace_root / rel
        if candidate.is_file() and candidate.suffix.lower() == ".go":
            targets.append(rel)
            continue
        if candidate.is_dir():
            targets.extend(_go_files_in_directory(candidate, workspace_root))
    return _dedupe_preserve_order(targets)


def _go_test_behavior_repair_target_files(
    *,
    text: str,
    changed_files: set[str],
    workspace_root: Path,
) -> list[str]:
    lowered = str(text or "").lower()
    if "go test" not in lowered or "--- fail:" not in lowered:
        return []

    production_files = [
        rel
        for rel in sorted(changed_files)
        if rel.endswith(".go")
        and not Path(rel).name.endswith("_test.go")
        and _workspace_path_exists_case_insensitive(workspace_root, rel)
    ]
    if not production_files:
        return []

    matched: list[str] = []
    for title_match in _GO_TEST_FAILURE_TITLE_RE.finditer(str(text or "")):
        tokens = _go_test_title_tokens(str(title_match.group("title") or ""))
        if tokens:
            matched.extend(_go_production_files_matching_tokens(production_files, tokens))

    return _dedupe_preserve_order([*matched, *production_files])


def _go_test_title_tokens(title: str) -> list[str]:
    normalized = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", str(title or ""))
    raw_tokens = re.split(r"[^A-Za-z0-9]+", normalized)
    stop_words = {"test", "tests", "and", "or", "the", "flow", "path", "case", "output", "entrypoint"}
    return _dedupe_preserve_order(
        [token.lower() for token in raw_tokens if len(token) >= 3 and token.lower() not in stop_words]
    )


def _go_production_files_matching_tokens(production_files: list[str], tokens: list[str]) -> list[str]:
    scored_matches: list[tuple[int, int, str]] = []
    primary_token = tokens[0] if tokens else ""
    for index, rel in enumerate(production_files):
        path_tokens = [
            token.lower()
            for token in re.split(r"[^A-Za-z0-9]+", f"{Path(rel).stem} {' '.join(Path(rel).parts)}")
            if len(token) >= 3
        ]
        score = 99
        if primary_token and primary_token in path_tokens:
            score = 0
        elif any(token in path_tokens for token in tokens):
            score = 1
        elif any(token in path_token or path_token in token for token in tokens for path_token in path_tokens):
            score = 2
        if score < 99:
            scored_matches.append((score, index, rel))
    if not scored_matches:
        return []
    best_score = min(score for score, _index, _rel in scored_matches)
    return [rel for score, _index, rel in sorted(scored_matches) if score == best_score]


def _go_compile_error_target_files(text: str, workspace_root: Path) -> list[str]:
    targets: list[str] = []
    for match in _GO_COMPILE_PATH_RE.finditer(text):
        raw_path = str(match.group("path") or "").strip()
        rel = _workspace_relative_go_repair_target(raw_path, workspace_root)
        if rel:
            targets.append(rel)
    targets.extend(
        _go_missing_member_type_definition_target_files(
            text=text,
            direct_targets=targets,
            workspace_root=workspace_root,
        )
    )
    return _dedupe_preserve_order(targets)


def _go_missing_member_type_definition_target_files(
    *,
    text: str,
    direct_targets: list[str],
    workspace_root: Path,
) -> list[str]:
    missing_member_refs = _go_missing_member_type_refs(text)
    if not missing_member_refs or not direct_targets:
        return []

    directories = _dedupe_preserve_order(
        [
            str(Path(rel).parent).replace("\\", "/")
            for rel in direct_targets
            if str(Path(rel).parent).replace("\\", "/") not in {"", "."}
        ]
    )
    for package_name, _type_name in missing_member_refs:
        if package_name:
            directories.extend(
                _go_package_qualifier_target_directories(
                    package_name=package_name,
                    direct_targets=direct_targets,
                    workspace_root=workspace_root,
                )
            )
    directories = _dedupe_preserve_order(directories)
    targets: list[str] = []
    type_names = _dedupe_preserve_order([type_name for _package_name, type_name in missing_member_refs])
    for directory in directories:
        package_dir = (workspace_root / directory).resolve()
        with contextlib.suppress(OSError, RuntimeError, ValueError):
            package_dir.relative_to(workspace_root.resolve())
            if not package_dir.is_dir():
                continue
            for path in sorted(package_dir.glob("*.go"), key=lambda item: item.as_posix()):
                if path.name.endswith("_test.go"):
                    continue
                try:
                    content = path.read_text(encoding="utf-8")
                    rel = path.relative_to(workspace_root).as_posix()
                except (OSError, RuntimeError, UnicodeDecodeError, ValueError):
                    continue
                if any(re.search(rf"\btype\s+{re.escape(type_name)}\b", content) for type_name in type_names):
                    targets.append(rel)
    return _dedupe_preserve_order(targets)


def _go_missing_member_type_refs(text: str) -> list[tuple[str, str]]:
    refs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for match in _GO_MISSING_MEMBER_TYPE_RE.finditer(str(text or "")):
        package_name = str(match.group("package_name") or "").strip()
        type_name = str(match.group("type_name") or "").strip()
        ref = (package_name, type_name)
        if type_name and ref not in seen:
            seen.add(ref)
            refs.append(ref)
    return refs


def _go_package_qualifier_target_directories(
    *,
    package_name: str,
    direct_targets: list[str],
    workspace_root: Path,
) -> list[str]:
    qualifier = str(package_name or "").strip()
    if not qualifier:
        return []

    candidates: list[str] = []
    direct_target_contents: list[str] = []
    for rel in direct_targets:
        normalized = _normalize_declared_task_path(rel)
        if not normalized:
            continue
        with contextlib.suppress(OSError, RuntimeError, UnicodeDecodeError, ValueError):
            path = (workspace_root / normalized).resolve()
            path.relative_to(workspace_root.resolve())
            if path.is_file():
                direct_target_contents.append(path.read_text(encoding="utf-8"))

    for content in direct_target_contents:
        for match in _GO_IMPORT_SPEC_RE.finditer(content):
            alias = str(match.group("alias") or "").strip()
            import_path = str(match.group("import_path") or "").strip()
            if not import_path:
                continue
            default_package = import_path.rstrip("/").rsplit("/", 1)[-1]
            if qualifier not in {alias, default_package}:
                continue
            candidates.extend(_go_import_path_workspace_directories(import_path, workspace_root))

    direct_candidate = workspace_root / qualifier
    if direct_candidate.is_dir():
        candidates.append(qualifier)
    return _dedupe_preserve_order(candidates)


def _go_import_path_workspace_directories(import_path: str, workspace_root: Path) -> list[str]:
    parts = [part for part in str(import_path or "").strip().split("/") if part and part != "."]
    candidates: list[str] = []
    for index in range(len(parts)):
        rel = "/".join(parts[index:])
        if not rel:
            continue
        candidate = (workspace_root / rel).resolve()
        with contextlib.suppress(OSError, RuntimeError, ValueError):
            candidate.relative_to(workspace_root.resolve())
            if candidate.is_dir():
                candidates.append(rel)
    return _dedupe_preserve_order(candidates)


def _workspace_relative_go_repair_target(raw_path: str, workspace_root: Path) -> str:
    token = str(raw_path or "").strip()
    if not token:
        return ""
    candidate = Path(token)
    if candidate.is_absolute():
        try:
            rel = candidate.resolve().relative_to(workspace_root.resolve()).as_posix()
        except (OSError, RuntimeError, ValueError):
            return ""
    else:
        rel = _normalize_declared_task_path(token)
    if not rel.endswith(".go"):
        return ""
    if _workspace_path_exists_case_insensitive(workspace_root, rel):
        return rel
    return ""


def _workspace_go_entrypoint_repair_target_files(workspace_root: Path) -> list[str]:
    targets: list[str] = []
    root_main = workspace_root / "main.go"
    if root_main.is_file():
        targets.append("main.go")
    cmd_root = workspace_root / "cmd"
    if cmd_root.is_dir():
        for path in sorted(cmd_root.glob("*/main.go"), key=lambda item: item.as_posix()):
            try:
                targets.append(path.relative_to(workspace_root).as_posix())
            except ValueError:
                continue
    return _dedupe_preserve_order(targets)


def _go_files_in_directory(directory: Path, workspace_root: Path) -> list[str]:
    targets: list[str] = []
    for path in sorted(directory.glob("*.go"), key=lambda item: item.as_posix()):
        try:
            targets.append(path.relative_to(workspace_root).as_posix())
        except ValueError:
            continue
    return targets


_NODE_STACK_JS_PATH_RE = re.compile(r"(?P<path>(?:[A-Za-z]:)?[/\\][^\s()'\"\n]+?\.js):\d+:\d+")
_NODE_COMMAND_JS_TARGET_RE = re.compile(r"(?:^|[\s(>])node\s+(?P<target>(?!-)[^\s'\"\n]+?\.js)\b")


def _javascript_runtime_smoke_repair_target_files(
    *,
    artifact_quality_errors: list[str],
    changed_files: list[str],
    workspace_full: str = "",
) -> list[str]:
    """Extract workspace JavaScript entrypoints that failed Node/npm smoke."""

    changed_js_files = {
        rel for item in changed_files if (rel := _normalize_declared_task_path(str(item or ""))) and rel.endswith(".js")
    }
    workspace_root = Path(str(workspace_full or "")).resolve() if str(workspace_full or "").strip() else None
    targets: list[str] = []
    for item in artifact_quality_errors:
        text = str(item or "")
        if not _looks_like_javascript_runtime_smoke_quality_error(text):
            continue
        for raw_path in _javascript_runtime_smoke_path_candidates(text):
            rel = _workspace_relative_javascript_repair_target(
                raw_path,
                changed_js_files=changed_js_files,
                workspace_root=workspace_root,
            )
            if rel:
                targets.append(rel)
    return _dedupe_preserve_order(targets)


def _looks_like_javascript_runtime_smoke_quality_error(text: str) -> bool:
    token = str(text or "").lower()
    if "workspace validation command failed" not in token:
        return False
    has_node_command = "npm run start" in token or "npm start" in token or re.search(r"\bnode\s+\S+\.js\b", token)
    if not has_node_command:
        return False
    return "node.js v" in token or "typeerror:" in token or "referenceerror:" in token or "syntaxerror:" in token


def _javascript_runtime_smoke_path_candidates(text: str) -> list[str]:
    candidates: list[str] = []
    for pattern in (_NODE_COMMAND_JS_TARGET_RE, _NODE_STACK_JS_PATH_RE):
        candidates.extend(
            str(match.group("target" if "target" in match.groupdict() else "path") or "")
            for match in pattern.finditer(text)
        )
    return _dedupe_preserve_order(candidates)


def _workspace_relative_javascript_repair_target(
    raw_path: str,
    *,
    changed_js_files: set[str],
    workspace_root: Path | None,
) -> str:
    token = str(raw_path or "").strip().strip("'\"`")
    if not token:
        return ""
    rel = ""
    candidate = Path(token)
    if candidate.is_absolute():
        if workspace_root is None or not workspace_root.is_dir():
            return ""
        try:
            rel = str(candidate.resolve().relative_to(workspace_root)).replace("\\", "/")
        except (OSError, ValueError):
            return ""
    else:
        rel = _normalize_declared_task_path(token)
    if not rel.endswith(".js") or rel.startswith("../") or "/../" in rel:
        return ""
    workspace_target_exists = (
        workspace_root is not None
        and workspace_root.is_dir()
        and _workspace_path_exists_case_insensitive(workspace_root, rel)
    )
    if rel in changed_js_files or workspace_target_exists:
        return rel
    return ""


def _looks_like_python_runtime_smoke_quality_error(text: str) -> bool:
    """Return true for Polaris-owned Python runtime/test gate failures."""

    token = str(text or "").lower()
    if "python runtime smoke" in token:
        return True
    return (
        "workspace validation command failed" in token
        or "python unittest failed" in token
        or "python tests failed" in token
    ) and _looks_like_python_test_behavior_failure(text)


def _is_test_like_python_path(rel_path: str) -> bool:
    normalized = str(rel_path or "").replace("\\", "/").lower()
    name = Path(normalized).name
    return normalized.endswith(".py") and (
        name.startswith("test_") or name.endswith("_test.py") or "/tests/" in normalized
    )


def _looks_like_python_regex_source_quality_failure(text: str) -> bool:
    token = str(text or "").lower()
    if "assertregex" not in token and "regex didn't match" not in token:
        return False
    return "not found" in token or "read_source(" in token or "src =" in token


def _looks_like_cli_subcommand_quality_failure(text: str) -> bool:
    token = str(text or "").lower()
    return any(
        hint in token
        for hint in (
            "unknown subcommand",
            "unrecognized subcommand",
            "invalid subcommand",
            "no such subcommand",
        )
    )


_SOURCE_REPAIR_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".c",
        ".cc",
        ".cpp",
        ".css",
        ".go",
        ".html",
        ".java",
        ".js",
        ".jsx",
        ".kt",
        ".rs",
        ".ts",
        ".tsx",
    }
)


_CLI_ENTRYPOINT_REPAIR_CANDIDATES: tuple[str, ...] = (
    "src/main.rs",
    "main.rs",
    "src/main.py",
    "main.py",
    "src/cli.py",
    "cli.py",
    "src/index.js",
    "index.js",
    "src/index.ts",
    "index.ts",
    "src/main.ts",
    "main.ts",
    "cmd/main.go",
    "main.go",
)


def _workspace_cli_entrypoint_repair_target_files(workspace_root: Path) -> list[str]:
    try:
        root = workspace_root.resolve()
    except (OSError, RuntimeError, ValueError):
        return []
    targets: list[str] = []
    for rel in _CLI_ENTRYPOINT_REPAIR_CANDIDATES:
        if _workspace_path_exists_case_insensitive(root, rel):
            targets.append(rel)
    return _dedupe_preserve_order(targets)


def _changed_source_repair_target_files(changed_files: list[str], workspace_root: Path) -> list[str]:
    targets: list[str] = []
    try:
        root = workspace_root.resolve()
    except (OSError, RuntimeError, ValueError):
        return []
    for item in changed_files:
        rel = _normalize_declared_task_path(str(item or ""))
        if not rel:
            continue
        if _is_test_like_python_path(rel):
            continue
        if Path(rel).suffix.lower() not in _SOURCE_REPAIR_EXTENSIONS:
            continue
        if _workspace_path_exists_case_insensitive(root, rel):
            targets.append(rel)
    return _dedupe_preserve_order(targets)


_PYTHON_TEST_HARNESS_PATH_RE = re.compile(
    r"(?:^|[\s'\"`(])(?P<path>(?:[A-Za-z]:)?[^\s'\"`()]*?(?:tests?/[^\s'\"`()]*test[^\s'\"`()]*\.py|test_[^\s'\"`()]*\.py))",
    re.IGNORECASE,
)


def _looks_like_python_test_harness_quality_failure(text: str) -> bool:
    token = str(text or "").lower()
    if any(
        hint in token
        for hint in (
            "python runtime smoke",
            "python -m unittest",
            "pytest",
            "unittest discover",
        )
    ):
        return True
    return bool(_PYTHON_TEST_HARNESS_PATH_RE.search(str(text or "")))


def _python_test_harness_changed_source_target_files(
    text: str,
    changed_files: list[str],
    workspace_root: Path,
) -> list[str]:
    if not _looks_like_python_test_behavior_failure(text):
        return []
    if not _looks_like_python_test_harness_quality_failure(text):
        return []
    targets = _changed_source_repair_target_files(changed_files, workspace_root)
    if not any(not target.endswith(".py") for target in targets):
        return []
    return targets


def _python_runtime_smoke_traceback_repair_target_files(text: str, workspace_root: Path) -> list[str]:
    targets: list[str] = []
    try:
        root = workspace_root.resolve()
    except (OSError, RuntimeError, ValueError):
        return []

    for match in _PYTHON_TRACEBACK_FILE_RE.finditer(str(text or "")):
        raw_path = str(match.group("path") or "").strip()
        if not raw_path:
            continue
        candidate = Path(raw_path)
        if candidate.is_absolute():
            try:
                rel = candidate.resolve().relative_to(root).as_posix()
            except (OSError, RuntimeError, ValueError):
                continue
        else:
            rel = _normalize_declared_task_path(raw_path)
        if not rel.endswith(".py"):
            continue
        if _workspace_path_exists_case_insensitive(root, rel):
            targets.append(rel)
    return _dedupe_preserve_order(targets)


def _python_runtime_smoke_imported_source_target_files(
    rel_path: str,
    workspace_root: Path,
    *,
    include_missing_src_imports: bool = False,
) -> list[str]:
    """Infer local source modules imported by a failing Python test script."""

    rel = _normalize_declared_task_path(rel_path)
    if not rel.endswith(".py"):
        return []
    try:
        root = workspace_root.resolve()
        source_path = (root / rel).resolve()
        source_path.relative_to(root)
        text = source_path.read_text(encoding="utf-8")
        tree = ast.parse(text)
    except (OSError, RuntimeError, SyntaxError, UnicodeDecodeError, ValueError):
        return []

    src_root_exists = include_missing_src_imports and _workspace_path_exists_case_insensitive(root, "src")
    candidates: list[str] = []
    for node in ast.walk(tree):
        module_names: list[str] = []
        if isinstance(node, ast.Import):
            module_names.extend(str(alias.name or "").strip() for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            module_names.append(str(node.module or "").strip())
        for module_name in module_names:
            if not module_name:
                continue
            module_parts = [part for part in module_name.split(".") if part]
            if not module_parts:
                continue
            allow_missing_src_import = bool(src_root_exists and module_parts[0] == "src" and len(module_parts) > 1)
            module_path = "/".join(module_parts)
            for candidate in (f"{module_path}.py", f"{module_path}/__init__.py"):
                normalized = _normalize_declared_task_path(candidate)
                if _is_test_like_python_path(normalized):
                    continue
                if _workspace_path_exists_case_insensitive(root, normalized):
                    candidates.append(normalized)
                    break
                if allow_missing_src_import and normalized.endswith(".py"):
                    candidates.append(normalized)
                    break
    return _dedupe_preserve_order(candidates)


def _python_runtime_smoke_missing_module_source_targets(text: str, workspace_root: Path) -> list[str]:
    """Infer missing import-root Python module targets from traceback text."""

    try:
        root = workspace_root.resolve()
    except (OSError, RuntimeError, ValueError):
        return []
    if not _workspace_path_exists_case_insensitive(root, "src"):
        return []

    targets: list[str] = []
    for match in _PYTHON_MODULE_NOT_FOUND_RE.finditer(str(text or "")):
        module_name = str(match.group("module") or "").strip()
        if not module_name or module_name.startswith(("pytest", "unittest")):
            continue
        module_parts = [part for part in module_name.split(".") if part]
        if not module_parts:
            continue
        if module_parts[0] == "src":
            if len(module_parts) == 1:
                continue
            module_parts = module_parts[1:]
        module_path = "/".join(module_parts)
        candidate_paths = (f"src/{module_path}.py", f"src/{module_path}/__init__.py")
        existing_candidate = next(
            (candidate for candidate in candidate_paths if _workspace_path_exists_case_insensitive(root, candidate)),
            "",
        )
        targets.append(existing_candidate or candidate_paths[0])
    return _dedupe_preserve_order(targets)


def _python_unittest_failure_test_target_files(text: str, workspace_root: Path) -> list[str]:
    """Map unittest result lines back to physical test files in the workspace."""

    try:
        root = workspace_root.resolve()
    except (OSError, RuntimeError, ValueError):
        return []

    targets: list[str] = []
    for match in _PYTHON_UNITTEST_RESULT_LINE_RE.finditer(str(text or "")):
        module_name = str(match.group("module") or "").strip()
        if not module_name:
            continue
        for candidate in _python_unittest_module_candidate_paths(module_name):
            if _workspace_path_exists_case_insensitive(root, candidate):
                targets.append(candidate)
                break
    return _dedupe_preserve_order(targets)


def _python_unittest_module_candidate_paths(module_name: str) -> list[str]:
    parts = [item for item in str(module_name or "").split(".") if item]
    candidates: list[str] = []
    for index, part in enumerate(parts):
        if not part.startswith("test_"):
            continue
        if index > 0 and parts[0] == "tests":
            candidates.append(f"{'/'.join(parts[: index + 1])}.py")
        candidates.append(f"tests/{part}.py")
        candidates.append(f"{part}.py")
        break
    return _dedupe_preserve_order(candidates)


_SEMANTIC_QUALITY_SINGLE_TARGET_HINTS: tuple[str, ...] = (
    "no project-domain signal found in changed files",
    "deterministic scaffold marker",
    "generic/placeholder content detected",
    "placeholder-only",
    "structural-only",
    "repeated trivial arithmetic placeholder tests",
    "generic payload/index store scaffold",
    "npm default failing test script",
    "npm package manifest has test runner script",
    "npm package manifest script",
    "npm package manifest contains",
    "npm package manifest declares",
    "typescript project typecheck failed",
    "unresolved import symbol",
    "error ts",
    "step verify target mismatch",
)

_SEMANTIC_QUALITY_REPAIR_SOURCE_SUFFIXES: frozenset[str] = frozenset(
    {
        ".css",
        ".c",
        ".cc",
        ".cpp",
        ".cxx",
        ".go",
        ".h",
        ".hpp",
        ".html",
        ".java",
        ".js",
        ".jsx",
        ".json",
        ".md",
        ".py",
        ".rs",
        ".ts",
        ".tsx",
    }
)

_EXPLICIT_ARTIFACT_QUALITY_TARGET_HINTS: tuple[str, ...] = (
    "assertionerror",
    "failed tests",
    "test failed",
    "vitest",
    "jest",
    "prettier",
    "syntaxerror",
    "syntax error",
    "parse error",
    "referenceerror",
    "is not defined in es module scope",
    "cannot use import statement outside a module",
    "unterminated string literal",
    "unexpected token",
    "unexpected end of input",
    "was never closed",
    "py_compile failed",
    "pytest",
    "unittest",
    "typeerror",
    "traceback (most recent call last)",
    "ruff failed",
    "format failed",
)


def _explicit_artifact_quality_repair_target_files(
    *,
    artifact_quality_errors: list[str],
    changed_files: list[str],
    workspace_full: str,
) -> list[str]:
    """Return explicit failing artifact paths from syntax/format quality errors."""

    joined_errors = "\n".join(str(item or "").lower() for item in artifact_quality_errors)
    if not any(hint in joined_errors for hint in _EXPLICIT_ARTIFACT_QUALITY_TARGET_HINTS):
        return []

    workspace_root = Path(str(workspace_full or "")).resolve() if str(workspace_full or "").strip() else None
    if workspace_root is None or not workspace_root.is_dir():
        return []

    changed_source_files = _dedupe_preserve_order(
        [
            rel
            for item in changed_files
            if (rel := _normalize_declared_task_path(str(item or "")))
            and Path(rel).suffix.lower() in _SEMANTIC_QUALITY_REPAIR_SOURCE_SUFFIXES
        ]
    )
    changed_source_set = set(changed_source_files)
    changed_by_lower = {item.lower(): item for item in changed_source_files}
    candidates: list[str] = []
    priority_candidates: list[str] = []
    traceback_source_candidates: list[str] = []
    imported_source_candidates: list[str] = []
    for item in artifact_quality_errors:
        text = str(item or "")
        if not any(hint in text.lower() for hint in _EXPLICIT_ARTIFACT_QUALITY_TARGET_HINTS):
            continue
        failed_title_targets = _failed_test_title_target_files(text, workspace_root, changed_source_files)
        candidates.extend(failed_title_targets)
        priority_candidates.extend(
            rel
            for rel in failed_title_targets
            if not _is_test_like_python_path(rel) and not _is_test_like_javascript_path(rel)
        )
        if _looks_like_javascript_module_system_failure(text) and "package.json" in changed_source_set:
            candidates.append("package.json")
            priority_candidates.append("package.json")
        explicit_paths = [match.group("path") for match in _SEMANTIC_QUALITY_EXPLICIT_PATH_RE.finditer(text)]
        if _looks_like_python_test_behavior_failure(text):
            for rel in _python_unittest_failure_test_target_files(text, workspace_root):
                imported_sources = _python_runtime_smoke_imported_source_target_files(
                    rel,
                    workspace_root,
                    include_missing_src_imports=True,
                )
                candidates.extend(imported_sources)
                imported_source_candidates.extend(imported_sources)
                candidates.append(rel)
            traceback_targets = _python_runtime_smoke_traceback_repair_target_files(text, workspace_root)
            if _looks_like_python_missing_module_failure(text):
                traceback_sources = [rel for rel in traceback_targets if not _is_test_like_python_path(rel)]
                candidates.extend(traceback_sources)
                traceback_source_candidates.extend(traceback_sources)
            missing_module_sources = _python_runtime_smoke_missing_module_source_targets(text, workspace_root)
            candidates.extend(missing_module_sources)
            if not _looks_like_python_missing_module_failure(text):
                imported_source_candidates.extend(missing_module_sources)
            harness_sources = _python_test_harness_changed_source_target_files(text, changed_files, workspace_root)
            candidates.extend(harness_sources)
            imported_source_candidates.extend(harness_sources)
            explicit_paths.extend(traceback_targets)
        for raw_path in explicit_paths:
            rel = _map_quality_error_path_to_changed_file(raw_path, changed_by_lower)
            if not rel:
                continue
            if Path(rel).suffix.lower() not in _SEMANTIC_QUALITY_REPAIR_SOURCE_SUFFIXES:
                continue
            if changed_source_set and rel not in changed_source_set:
                continue
            if _workspace_path_exists_case_insensitive(workspace_root, rel):
                appended_rel = False
                if _is_test_like_javascript_path(rel) and _looks_like_javascript_test_behavior_failure(text):
                    imported_sources = _javascript_test_imported_source_target_files(rel, workspace_root)
                    candidates.extend(imported_sources)
                    imported_source_candidates.extend(imported_sources)
                if (_is_test_like_python_path(rel) and _looks_like_python_test_behavior_failure(text)) or (
                    rel.endswith(".py") and _looks_like_python_module_coupling_failure(text)
                ):
                    if rel.endswith(".py") and _looks_like_python_missing_module_failure(text):
                        candidates.append(rel)
                        appended_rel = True
                        if rel.lower() in {item.lower() for item in traceback_source_candidates}:
                            traceback_source_candidates.append(rel)
                    imported_sources = _python_runtime_smoke_imported_source_target_files(
                        rel,
                        workspace_root,
                        include_missing_src_imports=_is_test_like_python_path(rel),
                    )
                    candidates.extend(imported_sources)
                    imported_source_candidates.extend(imported_sources)
                if not appended_rel:
                    candidates.append(rel)
    deduped_candidates = _dedupe_preserve_order(candidates)
    if not changed_source_files:
        return deduped_candidates
    changed_order = {item.lower(): index for index, item in enumerate(changed_source_files)}
    original_order = {item.lower(): index for index, item in enumerate(deduped_candidates)}
    imported_source_order = {
        item.lower(): index for index, item in enumerate(_dedupe_preserve_order(imported_source_candidates))
    }
    traceback_source_order = {
        item.lower(): index for index, item in enumerate(_dedupe_preserve_order(traceback_source_candidates))
    }
    priority_order = {item.lower(): index for index, item in enumerate(_dedupe_preserve_order(priority_candidates))}
    return sorted(
        deduped_candidates,
        key=lambda item: (
            (
                0
                if item.lower() in priority_order
                else 1
                if item.lower() in traceback_source_order
                else 2
                if item.lower() in imported_source_order
                else 3
            ),
            priority_order.get(item.lower(), len(priority_order)),
            traceback_source_order.get(item.lower(), len(traceback_source_order)),
            imported_source_order.get(item.lower(), len(imported_source_order)),
            changed_order.get(item.lower(), len(changed_order)),
            original_order.get(item.lower(), len(original_order)),
        ),
    )


def _failed_test_title_target_files(text: str, workspace_root: Path, changed_source_files: list[str]) -> list[str]:
    """Prefer artifacts named by the failed test title over stack frames."""

    changed_by_lower = {item.lower(): item for item in changed_source_files}
    targets: list[str] = []
    for match in _FAILED_TEST_TITLE_RE.finditer(str(text or "")):
        title = str(match.group("title") or "")
        for path_match in _SEMANTIC_QUALITY_EXPLICIT_PATH_RE.finditer(title):
            rel = _map_quality_error_path_to_changed_file(path_match.group("path"), changed_by_lower)
            if not rel:
                continue
            if changed_by_lower and rel.lower() not in changed_by_lower:
                continue
            if Path(rel).suffix.lower() not in _SEMANTIC_QUALITY_REPAIR_SOURCE_SUFFIXES:
                continue
            if _workspace_path_exists_case_insensitive(workspace_root, rel):
                targets.append(rel)
    return _dedupe_preserve_order(targets)


def _map_quality_error_path_to_changed_file(raw_path: str, changed_by_lower: dict[str, str]) -> str:
    """Map relative or workspace-absolute quality-log paths to changed files."""

    rel = _normalize_declared_task_path(raw_path)
    if rel and (not changed_by_lower or rel.lower() in changed_by_lower):
        return changed_by_lower.get(rel.lower(), rel)

    normalized = str(raw_path or "").strip().strip("'\"`").replace("\\", "/").strip("/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    normalized_lower = normalized.lower()
    for candidate_lower, candidate in changed_by_lower.items():
        if normalized_lower == candidate_lower or normalized_lower.endswith(f"/{candidate_lower}"):
            return candidate
    return rel


def _artifact_quality_failed_test_count(artifact_quality_errors: list[str]) -> int:
    """Return the physical test failure count reported by common CLI formats."""

    text = "\n".join(str(item or "") for item in artifact_quality_errors)
    failed_count = len(_TAP_FAILED_TEST_RE.findall(text))
    failed_count = max(failed_count, len(_PYTHON_UNITTEST_RESULT_LINE_RE.findall(text)))
    for match in _TEST_SUMMARY_FAIL_RE.finditer(text):
        try:
            failed_count = max(failed_count, int(match.group("count")))
        except (TypeError, ValueError):
            continue
    return failed_count


def _looks_like_javascript_test_behavior_failure(text: str) -> bool:
    token = str(text or "").lower()
    return any(hint in token for hint in ("assertionerror", "failed tests", "test failed", "vitest", "jest"))


def _looks_like_javascript_module_system_failure(text: str) -> bool:
    token = str(text or "").lower()
    return (
        "referenceerror: require is not defined" in token
        or "module is not defined in es module scope" in token
        or "is not defined in es module scope" in token
        or "cannot use import statement outside a module" in token
    )


def _looks_like_python_test_behavior_failure(text: str) -> bool:
    token = str(text or "").lower()
    return any(
        hint in token
        for hint in (
            "python -m unittest",
            "unittest",
            "pytest",
            "traceback (most recent call last)",
            "assertionerror",
            "typeerror",
            "failed tests",
            "test failed",
        )
    )


def _looks_like_python_module_coupling_failure(text: str) -> bool:
    token = str(text or "").lower()
    return any(
        hint in token
        for hint in (
            "importerror",
            "cannot import name",
            "modulenotfounderror",
            "attributeerror",
            "typeerror",
            "unexpected keyword argument",
            "has no attribute",
        )
    )


def _looks_like_python_missing_module_failure(text: str) -> bool:
    token = str(text or "").lower()
    return "modulenotfounderror" in token or "no module named" in token


def _has_non_test_python_traceback_source(text: str) -> bool:
    for match in _PYTHON_TRACEBACK_FILE_RE.finditer(str(text or "")):
        raw_path = str(match.group("path") or "").strip()
        if raw_path.endswith(".py") and not _is_test_like_python_path(raw_path):
            return True
    return False


def _is_test_like_javascript_path(rel_path: str) -> bool:
    normalized = str(rel_path or "").replace("\\", "/").lower()
    name = Path(normalized).name
    return normalized.endswith((".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")) and (
        ".test." in name or ".spec." in name or "/tests/" in normalized
    )


def _javascript_test_imported_source_target_files(rel_path: str, workspace_root: Path) -> list[str]:
    rel = _normalize_declared_task_path(rel_path)
    if not _is_test_like_javascript_path(rel):
        return []
    try:
        root = workspace_root.resolve()
        test_path = (root / rel).resolve()
        test_path.relative_to(root)
        text = test_path.read_text(encoding="utf-8")
    except (OSError, RuntimeError, UnicodeDecodeError, ValueError):
        return []

    candidates: list[str] = []
    for import_ref in _javascript_relative_import_refs(text):
        target = _resolve_javascript_relative_import_target(test_path.parent, import_ref, root)
        if target is None:
            continue
        target_rel = target.relative_to(root).as_posix()
        if _is_test_like_javascript_path(target_rel):
            continue
        candidates.append(target_rel)
    return _dedupe_preserve_order(candidates)


def _javascript_relative_import_refs(text: str) -> list[str]:
    refs: list[str] = []
    patterns = (
        re.compile(r"\bfrom\s+['\"](?P<ref>\.{1,2}/[^'\"]+)['\"]"),
        re.compile(r"\brequire\(\s*['\"](?P<ref>\.{1,2}/[^'\"]+)['\"]\s*\)"),
    )
    for pattern in patterns:
        for match in pattern.finditer(str(text or "")):
            refs.append(str(match.group("ref") or "").strip())
    return _dedupe_preserve_order([ref for ref in refs if ref])


def _resolve_javascript_relative_import_target(
    importer_dir: Path, import_ref: str, workspace_root: Path
) -> Path | None:
    raw = str(import_ref or "").strip()
    if not raw.startswith(("./", "../")):
        return None
    try:
        base = (importer_dir / raw).resolve()
        base.relative_to(workspace_root)
    except (OSError, RuntimeError, ValueError):
        return None
    candidates: list[Path] = []
    if base.suffix:
        candidates.append(base)
    else:
        candidates.extend(base.with_suffix(suffix) for suffix in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"))
        candidates.extend(base / f"index{suffix}" for suffix in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"))
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
            resolved.relative_to(workspace_root)
        except (OSError, RuntimeError, ValueError):
            continue
        if resolved.is_file():
            return resolved
    return None


def _semantic_quality_exporting_module_targets(
    artifact_quality_errors: list[str],
    workspace_root: Path,
) -> tuple[list[str], set[str]]:
    """Return exporter files named by unresolved-symbol quality errors.

    The failing path in these errors is usually the importing file. Repairing
    that file can make the next typecheck worse; the useful target is the
    module referenced by ``from ...``.
    """

    targets: list[str] = []
    importing_files: set[str] = set()
    for item in artifact_quality_errors:
        text = str(item or "")
        symbol_match = _em._UNRESOLVED_IMPORT_SYMBOL_ERROR_RE.search(text)
        if symbol_match:
            importer_rel = _normalize_declared_task_path(symbol_match.group("path"))
            module_ref = str(symbol_match.group("module") or "").strip().strip("\"'")
            if importer_rel:
                importing_files.add(importer_rel)
            target = _resolve_quality_error_module_target(
                importer_rel=importer_rel,
                module_ref=module_ref,
                workspace_root=workspace_root,
            )
            if target:
                targets.append(target)
        for ts_match in _TS_NO_EXPORTED_MEMBER_QUALITY_RE.finditer(text):
            importer_rel = _normalize_declared_task_path(ts_match.group("path"))
            module_ref = str(ts_match.group("module") or "").strip().strip("\"'")
            if importer_rel:
                importing_files.add(importer_rel)
            target = _resolve_quality_error_module_target(
                importer_rel=importer_rel,
                module_ref=module_ref,
                workspace_root=workspace_root,
            )
            if target:
                targets.append(target)
    return _dedupe_preserve_order(targets), importing_files


def _typescript_diagnostic_target_files(
    artifact_quality_errors: list[str],
    workspace_root: Path,
) -> list[str]:
    targets: list[str] = []
    for item in artifact_quality_errors:
        for match in _TS_DIAGNOSTIC_PATH_RE.finditer(str(item or "")):
            rel = _normalize_declared_task_path(match.group("path"))
            if (
                rel
                and Path(rel).suffix.lower() in {".ts", ".tsx"}
                and _workspace_path_exists_case_insensitive(workspace_root, rel)
            ):
                targets.append(rel)
    return _dedupe_preserve_order(targets)


def _typescript_unknown_exporter_target_files(
    artifact_quality_errors: list[str],
    workspace_root: Path,
) -> list[str]:
    symbols: list[str] = []
    for item in artifact_quality_errors:
        for pattern in (_TS_UNKNOWN_VALUE_QUALITY_RE, _TS_TYPE_ONLY_VALUE_QUALITY_RE):
            for match in pattern.finditer(str(item or "")):
                symbol = str(match.group("symbol") or "").strip()
                if symbol and symbol not in symbols:
                    symbols.append(symbol)
    if not symbols:
        return []

    targets: list[str] = []
    try:
        root = workspace_root.resolve()
        candidates = [
            path
            for path in root.rglob("*")
            if path.is_file() and path.suffix.lower() in {".ts", ".tsx"} and "node_modules" not in path.parts
        ]
    except OSError:
        return []
    for path in candidates:
        try:
            rel = path.relative_to(root).as_posix()
            text = path.read_text(encoding="utf-8")
        except (OSError, RuntimeError, UnicodeDecodeError, ValueError):
            continue
        for symbol in symbols:
            declaration_re = re.compile(
                _TS_EXPORTED_DECLARATION_TEMPLATE.format(symbol=re.escape(symbol)),
                re.MULTILINE,
            )
            if declaration_re.search(text):
                targets.append(rel)
                break
    return _dedupe_preserve_order(targets)


def _typescript_type_only_usage_files(artifact_quality_errors: list[str]) -> set[str]:
    usage_files: set[str] = set()
    for item in artifact_quality_errors:
        for match in _TS_TYPE_ONLY_VALUE_QUALITY_RE.finditer(str(item or "")):
            rel = _normalize_declared_task_path(match.group("path"))
            if rel:
                usage_files.add(rel)
    return usage_files


def _repair_target_context_block(
    *,
    workspace_full: str,
    repair_target_files: list[str],
) -> str:
    workspace = Path(str(workspace_full or "")).resolve() if str(workspace_full or "").strip() else None
    if workspace is None or not workspace.is_dir() or not repair_target_files:
        return ""

    blocks: list[str] = []
    total_budget = 12000
    per_file_budget = 4000
    used = 0
    for rel_path in repair_target_files[:6]:
        rel = _normalize_declared_task_path(rel_path)
        if not rel or "node_modules" in Path(rel).parts:
            continue
        try:
            target = (workspace / rel).resolve()
            target.relative_to(workspace)
            content = target.read_text(encoding="utf-8")
        except (OSError, RuntimeError, UnicodeDecodeError, ValueError):
            continue
        remaining = max(0, total_budget - used)
        if remaining <= 0:
            break
        excerpt = content[: min(per_file_budget, remaining)]
        used += len(excerpt)
        suffix = "\n[truncated]\n" if len(content) > len(excerpt) else ""
        blocks.append(f"--- {rel} ---\n```text\n{excerpt}{suffix}```")
    if not blocks:
        return ""
    return (
        "CURRENT UTF-8 CONTENT OF REPAIR TARGETS:\n"
        "Use these current file bodies as the source of truth for cross-file API coherence. "
        "Preserve existing public contracts unless changing every dependent target in this repair batch.\n"
        + "\n".join(blocks)
        + "\n"
    )


def _is_typescript_command_config_path(rel_path: str) -> bool:
    return Path(str(rel_path or "")).name.lower() in {"tsconfig.json", "jsconfig.json"}


def _is_generated_quality_repair_target(rel_path: str) -> bool:
    normalized = _normalize_declared_task_path(rel_path).lower()
    if not normalized:
        return False
    parts = set(Path(normalized).parts)
    if parts.intersection({"dist", "build", "out", "coverage", "node_modules"}):
        return True
    return normalized.endswith(".d.ts")


def _resolve_quality_error_module_target(
    *,
    importer_rel: str,
    module_ref: str,
    workspace_root: Path,
) -> str:
    importer = _normalize_declared_task_path(importer_rel)
    module = str(module_ref or "").strip().strip("\"'")
    if not importer or not module:
        return ""
    try:
        root = workspace_root.resolve()
        importer_path = (root / importer).resolve()
        importer_path.relative_to(root)
    except (OSError, RuntimeError, ValueError):
        return ""
    if module.startswith(("./", "../")):
        target = _resolve_javascript_relative_import_target(importer_path.parent, module, root)
        if target is None:
            return ""
        target_rel = target.relative_to(root).as_posix()
        if Path(target_rel).suffix.lower() in _SEMANTIC_QUALITY_REPAIR_SOURCE_SUFFIXES:
            return target_rel
        return ""

    module_path = module.replace(".", "/")
    for candidate in (f"{module_path}.py", f"{module_path}/__init__.py"):
        normalized = _normalize_declared_task_path(candidate)
        if _workspace_path_exists_case_insensitive(root, normalized):
            return normalized
    return ""


def _semantic_quality_repair_target_files(
    *,
    artifact_quality_errors: list[str],
    changed_files: list[str],
    workspace_full: str,
) -> list[str]:
    """Return a single changed source artifact for semantic quality repair.

    Generic semantic failures such as "no project-domain signal" are produced
    after Director already wrote a low-value artifact. If exactly one changed
    source file exists in the workspace, it is the failing artifact and should
    be rewritten with ``write_file`` instead of asking a weak model to format
    an ``edit_blocks`` patch.
    """

    joined_errors = "\n".join(str(item or "").lower() for item in artifact_quality_errors)
    if not any(hint in joined_errors for hint in _SEMANTIC_QUALITY_SINGLE_TARGET_HINTS):
        return []

    workspace_root = Path(str(workspace_full or "")).resolve() if str(workspace_full or "").strip() else None
    if workspace_root is None or not workspace_root.is_dir():
        return []

    exporting_targets, importing_files = _semantic_quality_exporting_module_targets(
        artifact_quality_errors,
        workspace_root,
    )
    unknown_exporter_targets = _typescript_unknown_exporter_target_files(
        artifact_quality_errors,
        workspace_root,
    )
    type_only_usage_files = _typescript_type_only_usage_files(artifact_quality_errors)
    diagnostic_targets = _typescript_diagnostic_target_files(artifact_quality_errors, workspace_root)
    candidates: list[str] = []
    for item in changed_files:
        rel = _normalize_declared_task_path(str(item or ""))
        if not rel:
            continue
        if Path(rel).suffix.lower() not in _SEMANTIC_QUALITY_REPAIR_SOURCE_SUFFIXES:
            continue
        if _workspace_path_exists_case_insensitive(workspace_root, rel):
            candidates.append(rel)

    unique_candidates = _dedupe_preserve_order(candidates)
    explicit_candidates: list[str] = []
    explicit_candidates.extend(
        _failed_test_title_target_files(
            "\n".join(str(item or "") for item in artifact_quality_errors),
            workspace_root,
            unique_candidates,
        )
    )
    for item in artifact_quality_errors:
        for match in _SEMANTIC_QUALITY_EXPLICIT_PATH_RE.finditer(str(item or "")):
            rel = _normalize_declared_task_path(match.group("path"))
            if (
                rel
                and Path(rel).suffix.lower() in _SEMANTIC_QUALITY_REPAIR_SOURCE_SUFFIXES
                and _workspace_path_exists_case_insensitive(workspace_root, rel)
            ):
                explicit_candidates.append(rel)
    explicit_unique = _dedupe_preserve_order(explicit_candidates)
    if exporting_targets:
        coupled_importers = [
            rel
            for rel in importing_files
            if Path(rel).suffix.lower() in _SEMANTIC_QUALITY_REPAIR_SOURCE_SUFFIXES
            and _workspace_path_exists_case_insensitive(workspace_root, rel)
        ]
        filtered_diagnostics = [rel for rel in diagnostic_targets if rel not in importing_files]
        filtered_explicit = [
            rel
            for rel in explicit_unique
            if rel not in importing_files and not (diagnostic_targets and _is_typescript_command_config_path(rel))
        ]
        return _dedupe_preserve_order(
            [*exporting_targets, *coupled_importers, *filtered_diagnostics, *filtered_explicit]
        )
    if unknown_exporter_targets:
        type_only_importers = [
            rel
            for rel in type_only_usage_files
            if Path(rel).suffix.lower() in _SEMANTIC_QUALITY_REPAIR_SOURCE_SUFFIXES
            and _workspace_path_exists_case_insensitive(workspace_root, rel)
        ]
        filtered_explicit = [
            rel
            for rel in explicit_unique
            if not _is_typescript_command_config_path(rel) and rel not in type_only_usage_files
        ]
        filtered_diagnostics = [rel for rel in diagnostic_targets if rel not in type_only_usage_files]
        return _dedupe_preserve_order(
            [*unknown_exporter_targets, *type_only_importers, *filtered_explicit, *filtered_diagnostics]
        )
    if diagnostic_targets:
        filtered_explicit = [rel for rel in explicit_unique if not _is_typescript_command_config_path(rel)]
        return _dedupe_preserve_order([*filtered_explicit, *diagnostic_targets])
    if explicit_unique:
        return explicit_unique

    if len(unique_candidates) != 1:
        return []
    return unique_candidates


def _missing_declared_target_files(task: dict[str, Any], workspace_full: str) -> list[str]:
    """Machine-derive the declared target files absent from the workspace.

    Deterministic ground truth for repair targeting: the task contract names
    the files, the filesystem says which exist (case-insensitive, consistent
    with declared-path matching).
    """
    workspace = str(workspace_full or "").strip()
    if not workspace:
        return []
    root = Path(workspace)
    if not root.is_dir():
        return []
    missing: list[str] = []
    for candidate in _extract_task_target_path_candidates(task):
        rel = _normalize_declared_task_path(candidate)
        if not rel or any(ch in rel for ch in ("*", "?")):
            continue
        if not Path(rel).suffix:
            continue
        if not _workspace_path_exists_case_insensitive(root, rel):
            missing.append(rel)
    return missing


def _missing_materialization_quality_repair_target_files(
    task: dict[str, Any],
    workspace_full: str,
    artifact_quality_errors: list[str],
) -> list[str]:
    explicit_missing_declared = _em._parse_missing_declared_target_files(artifact_quality_errors)
    declared_missing_now = _missing_declared_target_files(task, workspace_full)
    declared_missing_set = set(declared_missing_now)
    missing = [
        rel
        for item in explicit_missing_declared
        if (rel := _normalize_declared_task_path(item)) and rel in declared_missing_set
    ]
    missing.extend(_em._missing_unresolved_relative_import_target_files(artifact_quality_errors, workspace_full))
    missing.extend(
        _missing_workspace_file_quality_repair_target_files(
            artifact_quality_errors=artifact_quality_errors,
            workspace_full=workspace_full,
        )
    )
    missing.extend(
        _missing_python_module_alias_repair_target_files(
            artifact_quality_errors=artifact_quality_errors,
            workspace_full=workspace_full,
        )
    )
    missing.extend(declared_missing_now)
    return _dedupe_preserve_order(missing)


def _missing_workspace_file_quality_repair_target_files(
    *,
    artifact_quality_errors: list[str],
    workspace_full: str,
) -> list[str]:
    """Return concrete missing workspace files named by physical gate errors."""

    workspace = Path(str(workspace_full or "")).resolve() if str(workspace_full or "").strip() else None
    if workspace is None or not workspace.is_dir():
        return []

    targets: list[str] = []
    for error in artifact_quality_errors:
        text = str(error or "")
        for pattern in _MISSING_WORKSPACE_FILE_PATTERNS:
            for match in pattern.finditer(text):
                rel = _missing_workspace_file_path_to_relative(match.group("path"), workspace)
                require_missing = not _workspace_file_contract_assertion_allows_existing_target(text, rel)
                if rel and _missing_workspace_file_target_allowed(rel, workspace, require_missing=require_missing):
                    targets.append(rel)
    return _dedupe_preserve_order(targets)


def _missing_workspace_file_path_to_relative(raw_path: str, workspace_root: Path) -> str:
    token = str(raw_path or "").strip().strip("'\"`").rstrip(".,:;)")
    if not token:
        return ""
    candidate = Path(token)
    if candidate.is_absolute():
        try:
            return candidate.resolve(strict=False).relative_to(workspace_root).as_posix()
        except (OSError, RuntimeError, ValueError):
            return ""
    return _normalize_declared_task_path(token)


def _requirements_txt_declared_dependencies(artifact_quality_errors: list[str]) -> list[str]:
    dependencies: list[str] = []
    for error in artifact_quality_errors:
        text = str(error or "")
        lowered = text.lower()
        for pattern in (_REQUIREMENTS_TXT_ASSERT_IN_DEP_RE, _REQUIREMENTS_TXT_MUST_DECLARE_DEP_RE):
            for match in pattern.finditer(text):
                package = str(match.group("package") or "").strip().lower()
                if not package or package in _REQUIREMENTS_TXT_NON_PACKAGE_WORDS:
                    continue
                if package not in dependencies:
                    dependencies.append(package)
        if "requirements.txt must declare at least one dependency" in lowered:
            default_dependency = "typing-extensions>=4.0"
            if default_dependency not in dependencies:
                dependencies.append(default_dependency)
    return dependencies


def _workspace_file_contract_assertion_allows_existing_target(text: str, rel_path: str) -> bool:
    rel = _normalize_declared_task_path(rel_path).lower()
    if not rel:
        return False
    lowered = str(text or "").lower()
    if rel not in lowered:
        return False
    return any(
        marker in lowered
        for marker in (
            "must be",
            "must contain",
            "must declare",
            "must include",
            "must provide",
        )
    )


def _missing_workspace_file_target_allowed(
    rel_path: str,
    workspace_root: Path,
    *,
    require_missing: bool = True,
) -> bool:
    rel = _normalize_declared_task_path(rel_path)
    if not rel or any(ch in rel for ch in ("*", "?", "\x00")):
        return False
    if require_missing and _workspace_path_exists_case_insensitive(workspace_root, rel):
        return False
    if "__pycache__" in rel.split("/"):
        return False
    if rel.startswith(_NPM_SCRIPT_GENERATED_OUTPUT_PREFIXES):
        return False
    suffix = Path(rel).suffix.lower()
    if suffix not in _SEMANTIC_QUALITY_REPAIR_SOURCE_SUFFIXES and suffix not in {".cfg", ".toml", ".txt"}:
        return False
    if "/" not in rel:
        return Path(rel).name in _MISSING_WORKSPACE_ROOT_FILE_ALLOWLIST
    return rel.startswith(_MISSING_WORKSPACE_FILE_ALLOWED_PREFIXES)


def _missing_python_module_alias_repair_target_files(
    *,
    artifact_quality_errors: list[str],
    workspace_full: str,
) -> list[str]:
    """Return missing Python module shim targets implied by test import errors."""

    workspace = Path(str(workspace_full or "")).resolve() if str(workspace_full or "").strip() else None
    if workspace is None or not workspace.is_dir():
        return []

    targets: list[str] = []
    for error in artifact_quality_errors:
        text = str(error or "")
        if "modulenotfounderror" not in text.lower():
            continue
        for match in _PYTHON_MODULE_NOT_FOUND_RE.finditer(text):
            module_name = str(match.group("module") or "").strip()
            target = _python_missing_module_target(module_name, workspace)
            if target:
                targets.append(target)
    return _dedupe_preserve_order(targets)


def _python_missing_module_target(module_name: str, workspace_root: Path) -> str:
    if not module_name:
        return ""
    parts = module_name.split(".")
    if not all(re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", part) for part in parts):
        return ""
    if len(parts) > 1:
        rel = _normalize_declared_task_path(f"{'/'.join(parts)}.py")
        if rel and _missing_workspace_file_target_allowed(rel, workspace_root):
            return rel
        return ""

    module = parts[0]
    src_dir = workspace_root / "src"
    direct_src_module = _normalize_declared_task_path(f"src/{module}.py")
    if not src_dir.is_dir() or _workspace_path_exists_case_insensitive(workspace_root, direct_src_module):
        return ""
    if _find_python_module_alias_source(workspace_root, direct_src_module):
        return direct_src_module
    return ""


def _find_python_module_alias_source(workspace_root: Path, target_rel: str) -> str:
    target = _normalize_declared_task_path(target_rel)
    if not target or not target.startswith("src/") or not target.endswith(".py"):
        return ""
    module_stem = Path(target).stem
    try:
        root = workspace_root.resolve()
        src_root = (root / "src").resolve()
        src_root.relative_to(root)
    except (OSError, RuntimeError, ValueError):
        return ""
    for candidate in sorted(src_root.rglob(f"{module_stem}.py")):
        try:
            rel = candidate.resolve().relative_to(root).as_posix()
        except (OSError, RuntimeError, ValueError):
            continue
        if rel == target or _is_test_like_python_path(rel):
            continue
        return rel
    return ""


def _missing_npm_script_entrypoint_repair_target_files(
    *,
    artifact_quality_errors: list[str],
    workspace_full: str,
) -> list[str]:
    workspace = Path(str(workspace_full or "")).resolve() if str(workspace_full or "").strip() else None
    if workspace is None or not workspace.is_dir():
        return []

    missing: list[str] = []
    for error in artifact_quality_errors:
        match = _NPM_SCRIPT_MISSING_LOCAL_ENTRYPOINT_RE.search(str(error or ""))
        if not match:
            continue
        script_name = str(match.group("script") or "").strip().lower()
        entrypoint = str(match.group("path") or "").strip()
        for candidate in _npm_script_entrypoint_repair_target_candidates(script_name, entrypoint):
            if not _workspace_path_exists_case_insensitive(workspace, candidate):
                missing.append(candidate)
    return _dedupe_preserve_order(missing)


def _npm_script_entrypoint_repair_target_candidates(script_name: str, entrypoint: str) -> list[str]:
    normalized = _normalize_declared_task_path(entrypoint)
    if not normalized:
        return []
    if any(marker in normalized for marker in ("*", "?")):
        concrete = _concrete_npm_test_glob_repair_target(script_name, normalized)
        return [concrete] if concrete else []
    if not _npm_script_entrypoint_repair_target_allowed(script_name, normalized):
        return []
    return [normalized]


def _concrete_npm_test_glob_repair_target(script_name: str, pattern: str) -> str:
    if script_name != "test":
        return ""
    prefix = re.split(r"[*?]", pattern, maxsplit=1)[0].rstrip("/")
    directory = _normalize_declared_task_path(prefix) or "tests"
    if not directory:
        return ""
    if directory.startswith(_NPM_SCRIPT_GENERATED_OUTPUT_PREFIXES) or directory == "node_modules":
        return ""
    if not directory.startswith(("__tests__", "spec", "test", "tests")):
        return ""

    lower = pattern.lower()
    suffix_pairs = (
        (".test.tsx", ".test.tsx"),
        (".test.ts", ".test.ts"),
        (".spec.tsx", ".spec.tsx"),
        (".spec.ts", ".spec.ts"),
        (".test.jsx", ".test.jsx"),
        (".test.js", ".test.js"),
        (".spec.jsx", ".spec.jsx"),
        (".spec.js", ".spec.js"),
        (".tsx", ".test.tsx"),
        (".ts", ".test.ts"),
        (".jsx", ".test.jsx"),
        (".js", ".test.js"),
    )
    suffix = next((target_suffix for source_suffix, target_suffix in suffix_pairs if lower.endswith(source_suffix)), "")
    if not suffix:
        return ""
    return _normalize_declared_task_path(f"{directory}/generated{suffix}")


def _npm_script_entrypoint_repair_target_allowed(script_name: str, target: str) -> bool:
    if any(marker in target for marker in ("*", "?", "\x00")):
        return False
    if target.startswith(_NPM_SCRIPT_GENERATED_OUTPUT_PREFIXES):
        return False
    if Path(target).suffix.lower() not in _SEMANTIC_QUALITY_REPAIR_SOURCE_SUFFIXES:
        return False
    if target.startswith(_NPM_SCRIPT_REPAIRABLE_SOURCE_PREFIXES):
        return True
    return script_name in {"test", "verify"} and "/" not in target


def _build_materialization_quality_repair_message(
    *,
    original_message: str,
    artifact_quality_errors: list[str],
    directive_artifact_quality_errors: list[str] | None = None,
    changed_files: list[str],
    missing_target_files: list[str] | None = None,
    repair_target_files: list[str] | None = None,
    workspace_full: str = "",
) -> str:
    directive_quality_errors = (
        directive_artifact_quality_errors if directive_artifact_quality_errors is not None else artifact_quality_errors
    )
    error_lines = "\n".join(
        f"- {_format_quality_error_for_repair_prompt(item)}" for item in artifact_quality_errors[:12]
    )
    # Already-written files are reported as a COUNT, not paths: every
    # path-shaped token in this message seeds the retry target extractor
    # (extract_target_files_from_message), and naming the files that already
    # exist steered a weak model into rewriting src/main.js instead of
    # creating the missing src/styles.css (live factory-bench L2-10 r3).
    changed_line = f"{len(changed_files)} file(s) were already written and must NOT be rewritten."
    missing_block = ""
    single_missing_block = ""
    existing_repair_block = ""
    single_existing_repair_block = ""
    missing_target_set = set(missing_target_files or [])
    existing_repair_target_files = [item for item in repair_target_files or [] if item not in missing_target_set]
    if missing_target_files:
        missing_lines = "\n".join(f"- {item}" for item in missing_target_files[:12])
        missing_block = (
            f"MISSING TARGET FILES — create these exact paths NOW, one write_file call per path:\n{missing_lines}\n"
        )
        if len(missing_target_files) == 1:
            single_missing = missing_target_files[0]
            single_missing_block = (
                "SINGLE MISSING TARGET REPAIR:\n"
                "[director_quality_repair:write_only_single_target]\n"
                f"- Target path: {single_missing}\n"
                "- Emit exactly one write_file tool call for that target path.\n"
                "- The write_file content must be the complete non-empty file body.\n"
                "- Do not read files first. Do not list directories. Do not explore. Do not explain.\n"
            )
    if existing_repair_target_files:
        repair_lines = "\n".join(f"- {item}" for item in existing_repair_target_files[:12])
        existing_repair_block = (
            "EXISTING FAILED TARGET FILES — repair these exact paths NOW. Prefer edit_file search/replace "
            "for the minimal lines needed to satisfy the quality errors; use write_file only when you emit "
            "a complete corrected file body that preserves unrelated existing code. Use the CURRENT UTF-8 "
            "CONTENT block below to choose exact edit_file SEARCH strings; if the tool policy requires a "
            "fresh read before edit_file, read the target file first and then apply the edit:\n"
            f"{repair_lines}\n"
        )
        if len(existing_repair_target_files) == 1:
            single_target = existing_repair_target_files[0]
            single_existing_repair_block = (
                "SINGLE FAILED TARGET REPAIR:\n"
                "[director_quality_repair:edit_preferred_single_target]\n"
                f"- Target path: {single_target}\n"
                "- For edit_file, use an exact SEARCH string copied from the CURRENT UTF-8 CONTENT block below.\n"
                "- If edit_file is not enough, write_file must contain the complete corrected UTF-8 file body, "
                "not a shortened replacement.\n"
                "- If using edit_file, call read_file for this target first when required by tool policy. "
                "Do not list directories. Do not explore. Do not explain.\n"
            )
    prompt_repair_target_files = [*(missing_target_files or []), *existing_repair_target_files]
    repair_context_block = _repair_target_context_block(
        workspace_full=workspace_full,
        repair_target_files=prompt_repair_target_files,
    )
    # C7-text W3 (2026-06-16 deliberation): cross-file coherence repair. An
    # unresolved relative import means the importer references a module that
    # does not exist yet; QA detects it, but the bare "MISSING TARGET FILES"
    # list does not tell the weak Director WHY the file must exist or WHAT it
    # must expose — the #54 repair-mode cross-file-symbol-consistency wall.
    # Reframing each unresolved import as a coherence obligation ("create the
    # module this import resolves to, exporting what the importer uses") gives
    # the laborer the missing linkage. The path tokens are already present in
    # the "Quality errors" block below, so this introduces no new target-
    # extractor seeding, and it explicitly forbids editing the importing file.
    # Floor-safe: empty unless an unresolved-import error is present (the L2
    # success path never reaches here with one) -> message byte-for-byte
    # unchanged. Generic import reasoning only, no project specifics (§8).
    coherence_block = ""
    unresolved_import_errors = [
        _format_quality_error_for_repair_prompt(item)
        for item in artifact_quality_errors
        if "unresolved relative import" in str(item).lower()
    ]
    if unresolved_import_errors:
        coherence_lines = "\n".join(f"- {item}" for item in unresolved_import_errors[:12])
        coherence_block = (
            "CROSS-FILE COHERENCE REPAIR: each unresolved import below points at a module that does "
            "not exist yet. Create the missing module at the path the import resolves to, and make it "
            "EXPORT exactly the symbols the importer uses (its named imports / default export). Do not "
            "edit the importing file.\n"
            f"{coherence_lines}\n"
        )
    symbol_repair_block = _em._build_unresolved_import_symbol_repair_block(artifact_quality_errors)
    javascript_named_export_block = _build_javascript_named_export_repair_block(artifact_quality_errors)
    javascript_module_system_block = _build_javascript_module_system_repair_block(
        directive_quality_errors,
        repair_target_files=prompt_repair_target_files,
    )
    runtime_smoke_text = "\n".join(str(item or "") for item in directive_quality_errors).lower()
    html5_canvas_entrypoint_block = ""
    if "canvas entrypoint did not render non-empty pixels" in runtime_smoke_text or (
        "canvas" in runtime_smoke_text and "non-empty" in runtime_smoke_text
    ):
        html5_canvas_entrypoint_block = (
            "HTML5 CANVAS ENTRYPOINT REPAIR: the browser entrypoint loaded but did not paint visible pixels. "
            "Repair the browser path, not just tests: index.html must contain a real canvas, the referenced "
            "script/assets must exist after build, the bootstrap must run after the DOM/canvas exists, and it "
            'must draw a non-empty first frame before user interaction. Do not point <script type="module"> '
            "at a Node-only CLI entrypoint; use a browser bootstrap or inline browser code.\n"
        )
    if symbol_repair_block:
        changed_line = (
            f"{len(changed_files)} file(s) were already written; do not rewrite unrelated files. "
            "For CROSS-FILE SYMBOL REPAIR, only edit the exporting module named above."
        )
    syntax_block = ""
    truncation_signatures = ("unexpected end of input", "truncated/incomplete html", "was never closed")
    if any(
        any(signature in str(item).lower() for signature in truncation_signatures) for item in artifact_quality_errors
    ):
        # Rewrites at the same output limit truncate at the same place forever
        # (live factory-bench L2-11 r6: index.html rewritten three times, all
        # truncated). Only appending the remainder converges.
        syntax_block = (
            "TRUNCATED FILE DIRECTIVE: a file below was CUT OFF by the output "
            "limit. Do NOT rewrite it. read_file its tail, then call "
            "append_to_file with ONLY the missing remainder, continuing "
            "exactly after the current end of the file.\n"
        )
    elif any("syntax error" in str(item).lower() for item in artifact_quality_errors):
        # The narrow-edit-only directive (added L2-11 r2, where a full rewrite
        # reproduced the `endTime: null;` slip) backfired on weak local models:
        # live I3-r15, qwen could not form edit_blocks at all (121x "missing
        # blocks or start") and was simultaneously forbidden the write_file
        # rewrite it CAN do — leaving no usable repair path, so main.js
        # dead-lettered. Give the laborer an executable path: a targeted rewrite
        # changing ONLY the quoted line, with edit_blocks as a copy-verbatim
        # alternative. Naming the common slip (object-literal ';' -> ',') keeps
        # attention on the line rather than regenerating the whole file.
        syntax_block = (
            "SYNTAX REPAIR DIRECTIVE: a quoted line below (see Quality errors) is syntactically "
            "broken — most often an object-literal property ending in ';' that must be ',', or an "
            "unclosed '{'. Fix ONLY that line, keeping every other line byte-for-byte identical.\n"
            "  • Easiest reliable path: call write_file with the full file content, changed at that "
            "ONE line only.\n"
            "  • Or, surgically: edit_blocks with a SEARCH/REPLACE block whose SEARCH is the broken "
            "line copied VERBATIM and REPLACE is the corrected line.\n"
            "Do not change any other line; do not regenerate unrelated code.\n"
        )
    cli_entrypoint_block = ""
    if "python runtime smoke" in runtime_smoke_text and (
        "no expression provided" in runtime_smoke_text
        or "usage:" in runtime_smoke_text
        or "required argument" in runtime_smoke_text
        or "the following arguments are required" in runtime_smoke_text
    ):
        cli_entrypoint_block = (
            "PYTHON CLI ENTRYPOINT REPAIR: Polaris runs the target script as `python <script>` with no "
            "positional arguments during runtime smoke. That no-argument path must not crash or exit non-zero. "
            "If the task asks for an interactive CLI/input loop, no-argument mode must start that loop, read "
            "user input with input(), and exit cleanly on EOF, KeyboardInterrupt, `quit`, or `exit`. Do not require "
            "positional argv for the default path; optional argv shortcuts are allowed only in addition to the "
            "safe no-argument behavior.\n"
        )
    npm_manifest_block = ""
    if "npm package manifest" in runtime_smoke_text or "npm default failing test script" in runtime_smoke_text:
        npm_manifest_block = (
            "NPM PACKAGE MANIFEST REPAIR: if package.json is the repair target, emit one complete "
            "strict JSON file body. JSON keys and string values must use double quotes; do not output "
            "JavaScript object syntax or comments. If there is no real test/spec file in the workspace, "
            "do not use jest, vitest, mocha, or ava in the test script. Make `npm test` run a concrete "
            "local check that exists now, such as a node-based package/runtime check or an existing "
            "verification script. The test script must not be placeholder-only and must exit non-zero "
            "when the checked rule fails.\n"
        )
    forbidden_marker_block = ""
    if (
        "generic/placeholder content detected" in runtime_smoke_text
        or "deterministic scaffold marker" in runtime_smoke_text
    ):
        forbidden_marker_block = (
            "FORBIDDEN MARKER REPAIR: remove the reported scaffold marker without introducing another forbidden "
            "marker. Do not replace placeholder with stub, TODO, FIXME, TBD, NotImplemented, or placeholder-only "
            "phrasing. Use concrete neutral words such as sample-check, verified sample, implemented path, or "
            "real output.\n"
        )
    if existing_repair_target_files and not missing_target_files and not symbol_repair_block:
        changed_line = (
            f"{len(changed_files)} file(s) were already written; edit only the existing failed target "
            "file(s) named above, preserving unrelated code."
        )
    elif not missing_target_files and not symbol_repair_block:
        if syntax_block and "TRUNCATED FILE DIRECTIVE" in syntax_block:
            changed_line = (
                f"{len(changed_files)} file(s) were already written; the truncated artifact is the repair target. "
                "Do not rewrite it; append only the missing remainder."
            )
        else:
            changed_line = (
                f"{len(changed_files)} file(s) were already written and failed quality gates; "
                "rewrite only the failing changed artifact(s), not unrelated files."
            )
    return (
        "[mode:materialize]\n"
        '<SESSION_PATCH>{"delivery_mode":"materialize_changes","task_progress":"implementing"}</SESSION_PATCH>\n'
        f"{original_message}\n\n"
        "MATERIALIZATION QUALITY REPAIR MODE:\n"
        "The previous write reached the workspace but failed Polaris artifact quality gates.\n"
        f"{missing_block}"
        f"{single_missing_block}"
        f"{existing_repair_block}"
        f"{single_existing_repair_block}"
        f"{repair_context_block}"
        f"{coherence_block}"
        f"{symbol_repair_block}"
        f"{javascript_named_export_block}"
        f"{javascript_module_system_block}"
        f"{html5_canvas_entrypoint_block}"
        f"{syntax_block}"
        f"{cli_entrypoint_block}"
        f"{npm_manifest_block}"
        f"{forbidden_marker_block}"
        "Do not repeat the same package/script/test scaffold. Replace the bad artifact with concrete runnable code, "
        "source files, and executable tests required by the task contract.\n"
        "If package.json has an npm test script, it must run a real local test/check and must not contain "
        "`no test specified`, structural-only success output, TODO, placeholder, stub, or audit seed text.\n"
        f"{changed_line}\n"
        "Quality errors:\n"
        f"{error_lines}\n"
        "Return tool calls only for the minimal files needed to make the task materially complete."
    )


def _task_requires_fresh_materialization(task: dict[str, Any]) -> bool:
    """Return true when an existing file scope is not enough evidence.

    Repair and verification tasks are about changing or validating observed
    behavior. They must not be completed only because their scope files exist.
    """
    raw_metadata = task.get("metadata")
    metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
    raw_adapter_result = metadata.get("adapter_result")
    adapter_result: dict[str, Any] = raw_adapter_result if isinstance(raw_adapter_result, dict) else {}
    phase = str(task.get("phase") or metadata.get("phase") or "").strip().lower()
    verification_phases = {"verification", "validation", "verify", "qa", "test", "testing"}
    if phase in verification_phases and bool(metadata.get("qa_rework_verification_only")):
        return False

    if bool(metadata.get("qa_rework_requested")) or (
        str(adapter_result.get("qa_rework_reason") or metadata.get("qa_rework_reason") or "").strip()
        and not bool(adapter_result.get("qa_passed"))
    ):
        return True

    if phase in {"requirements", "analysis", "discovery", "investigation", "research"}:
        return False

    token = _task_text_blob(task).lower()
    if not token:
        return phase in {"implementation", "development", "coding", "build"}
    if phase in {"implementation", "development", "coding", "build"}:
        return True
    if phase in verification_phases and _task_has_declared_target_files(task):
        return True
    fresh_hints = (
        "implement",
        "implementation",
        "create",
        "add",
        "build",
        "write",
        "deliver",
        "repair",
        "fix",
        "bug",
        "regression",
        "update",
        "modify",
        "change",
        "replace",
        "remove",
        "cleanup",
        "clean up",
        "placeholder",
        "scaffold",
        "smallest code change",
        "minimal",
        "测试失败",
        "实现",
        "创建",
        "新增",
        "添加",
        "编写",
        "交付",
        "修复",
        "更新",
        "修改",
        "替换",
        "移除",
        "删除",
        "清理",
        "占位",
        "测试",
        "验收",
        "补齐",
        "补充",
        "覆盖",
        "通过测试",
        "最小变更",
    )
    return any(hint in token for hint in fresh_hints)


def _can_accept_existing_workspace_scope(
    *,
    task: dict[str, Any],
    requires_fresh_materialization: bool,
    write_tool_evidence: bool,
    primary_llm_summary: dict[str, Any] | None,
) -> bool:
    """Return True when no-diff execution can complete from existing scope evidence."""
    if not requires_fresh_materialization:
        return True
    if write_tool_evidence:
        return True
    raw_metadata = task.get("metadata")
    metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
    raw_adapter_result = metadata.get("adapter_result")
    adapter_result: dict[str, Any] = raw_adapter_result if isinstance(raw_adapter_result, dict) else {}
    if (
        bool(metadata.get("autofix"))
        or bool(metadata.get("qa_rework_requested"))
        or bool(adapter_result.get("qa_rework_requested"))
        or (
            str(adapter_result.get("qa_rework_reason") or metadata.get("qa_rework_reason") or "").strip()
            and not bool(adapter_result.get("qa_passed"))
        )
    ):
        return False
    phase = str(task.get("phase") or metadata.get("phase") or "").strip().lower()
    if phase in {"verification", "validation", "verify", "qa", "test", "testing"} and _task_has_declared_target_files(
        task
    ):
        return True
    primary_summary = primary_llm_summary or {}
    if bool(primary_summary.get("success")) and _safe_int(primary_summary.get("content_length")) > 0:
        return True
    error = str(primary_summary.get("error") or "").strip().lower()
    transient_unavailable_hints = (
        "single_batch_contract_violation",
        "circuit_open",
        "too many requests",
        "429",
        "rate limit",
        "rate_limit",
    )
    return any(hint in error for hint in transient_unavailable_hints)


def _director_direct_text_patch_only_enabled(context: dict[str, Any]) -> bool:
    """Return whether Director should bypass role-kernel tool mode for text patches."""
    raw = ""
    if isinstance(context, dict):
        raw = str(context.get("director_direct_text_patch_only") or "").strip().lower()
    if not raw:
        raw = str(os.environ.get("KERNELONE_DIRECTOR_DIRECT_TEXT_PATCH_ONLY", "")).strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _director_existing_scope_preflight_enabled(context: dict[str, Any]) -> bool:
    """Return whether Director may complete task scope that already exists.

    The default is enabled because QA remains the final semantic gate; this only
    avoids expensive LLM/tool calls for already-materialized declared paths.
    """
    raw = ""
    if isinstance(context, dict):
        raw = str(context.get("director_existing_scope_preflight") or "").strip().lower()
    if not raw:
        raw = str(os.environ.get("KERNELONE_DIRECTOR_EXISTING_SCOPE_PREFLIGHT", "")).strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _build_existing_workspace_task_evidence(
    *,
    task: dict[str, Any],
    current_files: dict[str, str],
    workspace_full: str = "",
    workspace_name: str = "",
) -> dict[str, Any]:
    """Build generic evidence that a task's declared scope is already present.

    This is intentionally scope-driven, not domain-driven: Polaris may verify an
    already-materialized task only when the PM contract names concrete files or
    directories that can be observed in the workspace. QA remains the final
    semantic gate.
    """
    path_candidates = _extract_task_path_candidates(task)
    if not path_candidates:
        return {
            "ok": False,
            "reason": "no_declared_scope_paths",
            "candidate_paths": [],
            "existing_paths": [],
            "missing_paths": [],
        }

    current = {str(path or "").replace("\\", "/").strip().lstrip("/") for path in current_files if str(path).strip()}
    existing: list[str] = []
    missing: list[str] = []
    for candidate in path_candidates:
        normalized = _normalize_declared_task_path(candidate, workspace_name=workspace_name)
        if not normalized:
            continue
        if _path_candidate_exists_in_file_set(normalized, current):
            existing.append(normalized)
        else:
            missing.append(normalized)

    existing = _dedupe_preserve_order(existing)
    missing = [item for item in _dedupe_preserve_order(missing) if item not in set(existing)]
    candidate_count = len(existing) + len(missing)
    existing_count = len(existing)
    coverage = existing_count / max(candidate_count, 1)
    minimum_existing = min(3, max(1, candidate_count))
    ok = existing_count >= minimum_existing and not missing
    artifact_quality_errors: list[str] = []
    if ok and str(workspace_full or "").strip() and existing:
        artifact_quality_errors = _em.scan_workspace_artifact_quality(
            str(workspace_full),
            relative_paths=existing,
        )
        if artifact_quality_errors:
            ok = False
    return {
        "ok": ok,
        "reason": (
            "declared_scope_present"
            if ok
            else "declared_scope_quality_failed"
            if artifact_quality_errors
            else "declared_scope_incomplete"
        ),
        "candidate_paths": _dedupe_preserve_order([*existing, *missing])[:40],
        "existing_paths": existing[:40],
        "missing_paths": missing[:40],
        "coverage": round(coverage, 3),
        **({"artifact_quality_errors": artifact_quality_errors[:20]} if artifact_quality_errors else {}),
    }
