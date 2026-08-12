"""Internal implementation module for quality_gate package (lossless split)."""

# Cross-module free names are injected by package __init__
# (_wire_cross_module_namespace). Static F821 is expected and lossless.
# Imports are intentionally complete for lossless behavior; do not strip.
# ruff: noqa: F401, F821

from __future__ import annotations

import ast
import contextlib
import hashlib
import json
import os
import re
import shlex
import time
from pathlib import Path
from typing import Any, Iterable, Mapping, cast

from polaris.cells.director.runtime.public.contracts import DirectorInterfaceDiscrepancyReceiptV1
from polaris.cells.runtime.task_runtime.public import (
    TaskRuntimeExecutionAttemptAuthorityV1,
    TaskRuntimeExecutionAttemptIdentityV1,
)
from polaris.kernelone.quality import (
    artifact_quality_issue_raw,
    artifact_quality_issues_for_errors,
    artifact_quality_issues_from_errors,
    build_scope_authority_decision,
    partition_paths_by_declared_scope,
    scope_authority_decision_summary,
)

from .. import execute_method as _em
from ..artifact_quality_diagnostics import (
    _UNRESOLVED_IMPORT_SYMBOL_ERROR_RE,
    _UNRESOLVED_RELATIVE_IMPORT_ERROR_RE,
    _build_unresolved_import_symbol_repair_block,
    _missing_unresolved_relative_import_target_files,
    _parse_missing_declared_target_files,
    _relative_import_repair_target_candidates,
)
from ..contract_verify import resolve_contract_step_verify
from ..helpers import has_successful_write_tool
from ..materialization_quality_boundary import run_materialization_quality_public_boundary
from ..materialization_quality_runtime_ports import has_materialization_quality_runtime_repair_coverage
from ..repair_profile_projection import project_repair_kernel_summary
from ..runtime_repair_tool_adapter import (
    defer_director_command_with_director_tools,
    run_runtime_repair_with_director_tools,
)
from ..task_scope_paths import (
    _dedupe_preserve_order,
    _extract_project_declared_target_path_candidates,
    _extract_task_path_candidates,
    _extract_task_target_path_candidates,
    _filter_diff_to_task_declared_paths,
    _normalize_declared_task_path,
    _path_candidate_exists_in_file_set,
    _task_has_declared_target_files,
    _task_text_blob,
    _workspace_path_exists_case_insensitive,
)
from ._package_ns import package_attr

# Cross-module symbols (defined in sibling submodules). Bare annotations
# satisfy mypy; package __init__._wire_cross_module_namespace injects
# real values into this module's __dict__ at import time.
_annotate_current_task_missing_target_continuation: Any
_find_python_module_alias_sources: Any
_requirements_txt_declared_dependencies: Any
_task_boundary_scope_filter_evidence: Any


def _run_materialization_quality_public_boundary(
    adapter: Any,
    *,
    task: dict[str, Any],
    task_id: str,
    artifact_quality_errors: list[str],
    artifact_quality_issues: tuple[dict[str, Any], ...] = (),
    convergence_verifier: Any = None,
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1 | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Execute materialization-quality repair via the typed roles public boundary."""

    tool_results, summary = package_attr("run_materialization_quality_public_boundary")(
        adapter,
        task=task,
        task_id=task_id,
        artifact_quality_errors=artifact_quality_errors,
        artifact_quality_issues=artifact_quality_issues,
        convergence_verifier=convergence_verifier,
        execution_attempt=execution_attempt,
    )
    return tool_results, _annotate_current_task_missing_target_continuation(
        summary,
        task=task,
        workspace_full=str(getattr(adapter, "workspace", "") or ""),
        artifact_quality_errors=artifact_quality_errors,
        artifact_quality_issues=artifact_quality_issues,
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
    summary: dict[str, Any] = {
        "stage": stage,
        "success": bool(result.get("success")),
        "provider": provider,
        "model": model,
        "content_length": len(content),
        "error": str(result.get("error") or raw_payload.get("error") or "").strip(),
        "error_category": str(
            result.get("error_category") or raw_payload.get("error_category") or metadata.get("error_category") or ""
        ).strip(),
        "last_transport_error": str(
            result.get("last_transport_error")
            or raw_payload.get("last_transport_error")
            or metadata.get("last_transport_error")
            or ""
        ).strip(),
        "platform_retry_exhausted": bool(
            result.get("platform_retry_exhausted")
            or raw_payload.get("platform_retry_exhausted")
            or metadata.get("platform_retry_exhausted")
        ),
        "llm_calls": _safe_int(execution_stats.get("llm_calls")),
    }
    # Carry lifecycle evidence forward so downstream attribution can consume
    # Run Ledger public helpers instead of only relying on error text fields.
    if metadata:
        summary["metadata"] = metadata
    lifecycle_summary = raw_payload.get("tool_lifecycle_summary")
    if isinstance(lifecycle_summary, dict) and lifecycle_summary:
        summary["tool_lifecycle_summary"] = lifecycle_summary
    # R129: preserve DEO batch receipts / tool_results so dependent-task
    # actual_sibling_exports can bind parent physical effect receipts.
    batch_receipt = result.get("batch_receipt")
    if not isinstance(batch_receipt, dict):
        batch_receipt = raw_payload.get("batch_receipt")
    if not isinstance(batch_receipt, dict):
        batch_receipt = metadata.get("batch_receipt")
    if not isinstance(batch_receipt, dict):
        batch_receipt = execution_stats.get("batch_receipt")
    if isinstance(batch_receipt, dict) and batch_receipt:
        summary["batch_receipt"] = dict(batch_receipt)
        if "metadata" not in summary:
            summary["metadata"] = {}
        if isinstance(summary["metadata"], dict) and "batch_receipt" not in summary["metadata"]:
            summary["metadata"]["batch_receipt"] = dict(batch_receipt)
    tool_results = result.get("tool_results")
    if not isinstance(tool_results, list):
        tool_results = raw_payload.get("tool_results")
    if not isinstance(tool_results, list):
        tool_results = metadata.get("tool_results")
    if isinstance(tool_results, list) and tool_results:
        summary["tool_results"] = [dict(item) for item in tool_results if isinstance(item, dict)][:64]
    return summary


def _build_materialization_quality_failure_evidence_context(
    *,
    artifact_quality_errors: list[str],
    missing_target_files: list[str],
    repair_target_files: list[str],
    changed_files: list[str],
    repair_attempt: int,
) -> dict[str, Any]:
    """Project quality-repair diagnostics into final-request evidence slots."""

    quality_errors = [str(error).strip() for error in artifact_quality_errors if str(error or "").strip()]
    missing_targets = [str(path).strip() for path in missing_target_files if str(path or "").strip()]
    repair_targets = [str(path).strip() for path in repair_target_files if str(path or "").strip()]
    changed = [str(path).strip() for path in changed_files if str(path or "").strip()]
    failure_class = "INCOMPLETE_MATERIALIZATION" if missing_targets else "WORKSPACE_QUALITY_GATE_FAILED"
    return {
        "schema_version": "polaris.failure_evidence.v1",
        "source": "director.materialization_quality_repair",
        "failure_class": failure_class,
        "responsible_layer": "director",
        "repairable_by_director": True,
        "requires_ce_replan": False,
        "requires_pm_revision": False,
        "quality_errors": quality_errors[:20],
        "failed_checks": ["artifact_quality", "task_boundary"],
        "missing_target_files": missing_targets[:20],
        "repair_target_files": repair_targets[:20],
        "changed_files": changed[:40],
        "attempt": repair_attempt,
    }


def _build_materialization_quality_workspace_evidence_context(
    *,
    artifact_quality_errors: list[str],
    missing_target_files: list[str],
    repair_target_files: list[str],
    changed_files: list[str],
    repair_attempt: int,
) -> dict[str, Any]:
    """Project failed workspace quality state into a structured context slot."""

    quality_errors = [str(error).strip() for error in artifact_quality_errors if str(error or "").strip()]
    missing_targets = [str(path).strip() for path in missing_target_files if str(path or "").strip()]
    repair_targets = [str(path).strip() for path in repair_target_files if str(path or "").strip()]
    changed = [str(path).strip() for path in changed_files if str(path or "").strip()]
    return {
        "schema_version": "polaris.workspace_quality_evidence.v1",
        "source": "director.materialization_quality_repair",
        "all_checks_passed": False,
        "quality_errors": quality_errors[:20],
        "missing_required_modalities": ["code"] if missing_targets else [],
        "failed_required_modalities": ["command"] if quality_errors else [],
        "missing_target_files": missing_targets[:20],
        "repair_target_files": repair_targets[:20],
        "changed_files": changed[:40],
        "attempt": repair_attempt,
    }


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _quality_repair_write_file_tool_definition() -> dict[str, Any]:
    """Registry-faithful write_file schema via Forced Tool Surface SSOT (R127)."""
    from polaris.kernelone.tool_execution.forced_tool_surface import resolve_registry_tool_schema

    return resolve_registry_tool_schema("write_file")


def _quality_repair_edit_file_tool_definition() -> dict[str, Any]:
    """Registry-faithful edit_file schema via Forced Tool Surface SSOT (R127).

    Final provider qualification compares forced tools against ToolSpecRegistry.
    Repair guidance belongs in the prompt/SESSION_PATCH only.
    """
    from polaris.kernelone.tool_execution.forced_tool_surface import resolve_registry_tool_schema

    return resolve_registry_tool_schema("edit_file")


def _quality_repair_execute_command_tool_definition() -> dict[str, Any]:
    """Registry-faithful execute_command schema via Forced Tool Surface SSOT (R127)."""
    from polaris.kernelone.tool_execution.forced_tool_surface import resolve_registry_tool_schema

    return resolve_registry_tool_schema("execute_command")


def _director_repair_force_existing_write_enabled() -> bool:
    """Default OFF -> byte-identical. Opt in so an existing-file quality repair forces
    a write/edit tool call (tool_choice=required, execute_command dropped) instead of
    leaving tool_choice auto with execute_command available -- which let weak models
    explore or return empty content, producing single_batch_contract_violation: no
    write tool invocation (factory_bench L3-01 repair could not materialize the fix)."""
    return str(os.environ.get("KERNELONE_DIRECTOR_REPAIR_FORCE_EXISTING_WRITE", "")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _quality_repair_existing_target_tool_definitions() -> list[dict[str, Any]]:
    """Existing-file quality repair forced tools — registry SSOT only (R127)."""
    from polaris.kernelone.tool_execution.forced_tool_surface import build_forced_tool_surface

    return build_forced_tool_surface(("edit_file", "write_file", "execute_command"))


def _format_unresolved_relative_import_error_for_repair_prompt(error: Any) -> str | None:
    """Return a path-safe repair prompt line for unresolved relative imports."""

    match = _UNRESOLVED_RELATIVE_IMPORT_ERROR_RE.search(str(error or ""))
    if not match:
        return None
    importer_rel = _normalize_declared_task_path(match.group("path"))
    specifier = str(match.group("specifier") or "").strip()
    if not importer_rel or not specifier.startswith("."):
        return None
    candidates = _relative_import_repair_target_candidates(
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

_NPM_SCRIPT_MISSING_LOCAL_MODULE_RE = re.compile(
    r"npm package manifest script '(?P<script>[^']+)' local entrypoint '(?P<entrypoint>[^']+)' "
    r"requires missing local module: (?P<module>\S+)",
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


def _reject_raw_single_target_repair_body(
    adapter: Any,
    *,
    task_id: str,
    repair_target_files: list[str],
    content: str,
) -> list[dict[str, Any]]:
    """Record that an unstructured LLM body has no mutation authority."""

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
    del adapter
    return [
        {
            "tool": "raw_single_target_body",
            "tool_name": "raw_single_target_body",
            "success": False,
            "status": "blocked",
            "result": {
                "ok": False,
                "source_tool": "director_quality_repair_raw_single_target_body",
                "error_code": "raw_single_target_body_not_authoritative",
                "error_message": "raw LLM response content cannot authorize a workspace mutation",
                "writes_allowed": False,
                "task_id": task_id,
                "file": target_file,
                "content_hash_only": hashlib.sha256(normalized_content.encode("utf-8")).hexdigest(),
            },
        }
    ]


def _quality_repair_execution_attempt(
    context: Mapping[str, Any],
) -> TaskRuntimeExecutionAttemptIdentityV1 | None:
    authority = context.get("task_runtime_execution_attempt_authority")
    if type(authority) is not TaskRuntimeExecutionAttemptAuthorityV1:
        return None
    typed_authority = cast(TaskRuntimeExecutionAttemptAuthorityV1, authority)
    snapshot = typed_authority.snapshot(lock_timeout_seconds=5.0)
    if snapshot.success is not True or snapshot.closed:
        return None
    if type(snapshot.identity) is not TaskRuntimeExecutionAttemptIdentityV1:
        return None
    return snapshot.identity


def _quality_repair_base_files(
    workspace_root: Path,
    candidates: Iterable[str],
) -> dict[str, str]:
    """Read only explicit repair inputs into the Director Runtime planning snapshot."""

    base_files: dict[str, str] = {}
    for raw_path in candidates:
        rel_path = _normalize_declared_task_path(str(raw_path or ""))
        if not rel_path or rel_path in base_files:
            continue
        path = (workspace_root / rel_path).resolve()
        if not path.is_relative_to(workspace_root) or not path.is_file() or path.is_symlink():
            continue
        try:
            base_files[rel_path] = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
    return base_files


def _deterministic_single_missing_quality_repair_to_write_file(
    adapter: Any,
    *,
    task_id: str,
    repair_target_files: list[str],
    artifact_quality_errors: list[str],
    context: Mapping[str, Any],
    base_file_candidates: Iterable[str],
) -> list[dict[str, Any]]:
    """Plan a requirements repair and defer its effects to the governed boundary."""

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
    base_files = _quality_repair_base_files(workspace_root, base_file_candidates)
    return run_runtime_repair_with_director_tools(
        adapter,
        workspace_path=workspace_root,
        task_id=task_id,
        source_tool="deterministic_runtime_dependency_repair",
        execution_attempt=_quality_repair_execution_attempt(context),
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        allowed_paths=(target_file,),
        max_rounds=1,
    )


def _deterministic_single_missing_python_module_alias_to_write_file(
    adapter: Any,
    *,
    task_id: str,
    repair_target_files: list[str],
    artifact_quality_errors: list[str],
    context: Mapping[str, Any],
    base_file_candidates: Iterable[str],
) -> list[dict[str, Any]]:
    """Plan a Python source-root bridge and defer its physical effects."""

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
    source_candidates = _find_python_module_alias_sources(workspace_root, target_file)
    if len(source_candidates) > 1:
        return [
            {
                "tool": "director_repair_kernel",
                "tool_name": "director_repair_kernel",
                "success": False,
                "result": {
                    "ok": False,
                    "source_tool": "deterministic_python_missing_module_alias_repair",
                    "error_code": "python_module_alias_candidate_ambiguous",
                    "error_message": "multiple same-name Python modules match the missing top-level alias",
                    "repair_applied": False,
                    "candidate_paths": list(source_candidates),
                    "repair_kernel": {
                        "owner_cell": "director.runtime",
                        "execution_skipped": True,
                        "execution_skip_reason": "ambiguous_source_candidates",
                        "physical_executor_owned": False,
                    },
                },
            }
        ]
    if not source_candidates:
        return []
    source_rel = source_candidates[0]
    base_files = _quality_repair_base_files(workspace_root, (*base_file_candidates, source_rel))
    results = run_runtime_repair_with_director_tools(
        adapter,
        workspace_path=workspace_root,
        task_id=task_id,
        source_tool="deterministic_python_missing_module_alias_repair",
        execution_attempt=_quality_repair_execution_attempt(context),
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        allowed_paths=(target_file,),
        max_rounds=1,
    )
    if results:
        return results
    return [
        {
            "tool": "director_repair_kernel",
            "tool_name": "director_repair_kernel",
            "success": False,
            "result": {
                "ok": False,
                "source_tool": "deterministic_python_missing_module_alias_repair",
                "error_code": "director_quality_repair_covered_unplannable",
                "error_message": "the matched Director Runtime source tool produced no executable plan",
                "repair_applied": False,
                "repair_kernel": {
                    "owner_cell": "director.runtime",
                    "execution_skipped": True,
                    "execution_skip_reason": "covered_unplannable",
                    "physical_executor_owned": False,
                },
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
    workspace: str = "",
    cache_root: str = "",
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
    affected_files = sorted(set(out_of_scope_new + out_of_scope_modified))
    result: dict[str, Any] = {
        "new_files": out_of_scope_new,
        "modified_files": out_of_scope_modified,
        "affected_files": affected_files,
    }
    if affected_files:
        result["task_boundary_scope_filter"] = _task_boundary_scope_filter_evidence(
            task,
            target_files=affected_files,
            reason="director_materialized_out_of_scope",
            workspace=workspace or workspace_name,
            cache_root=cache_root,
        )
    return result


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


def _collect_step_verify_errors(
    adapter: Any,
    context: dict[str, Any] | None,
    *,
    task_id: str,
    task: dict[str, Any] | None = None,
    workspace_name: str = "",
) -> tuple[list[str], list[dict[str, Any]]]:
    """写后即查（三层裂变 DO 层自查）: run the construction step's machine
    verify inside the execution turn so the repair ladder sees the failure
    while the feedback loop is still seconds long — the exec→QA→bounce→exec
    market round trip costs ~3 cycles (~30min) per blind retry (live I3-r11).
    """
    if not isinstance(context, dict):
        return [], []
    from polaris.kernelone.quality.step_verify import (
        assess_legacy_step_verify_command_safety,
    )

    resolution = resolve_contract_step_verify(context, task=task)
    if resolution.disposition == "deferred":
        _record_deferred_step_verify_obligation(context, resolution.to_dict())
        return [], []
    verify = resolution.command
    if not verify:
        return [], []
    safety = assess_legacy_step_verify_command_safety(verify)
    if not safety.allowed:
        return [f"step verify command rejected by safety policy: {safety.reason} :: {verify!r}"], []
    step = context.get("construction_step")
    if isinstance(step, dict):
        target_mismatch = _step_verify_target_mismatch_error(step, verify)
        if target_mismatch:
            return [target_mismatch], []
    workspace = str(getattr(adapter, "workspace", "") or "")
    if not workspace or not os.path.isdir(workspace):
        return [], []
    workspace_path = Path(workspace).resolve()
    execution_attempt = _quality_repair_execution_attempt(context)
    commands: list[tuple[str, int, str]] = []
    for index, plan in enumerate(_step_verify_environment_prep_plans(verify, workspace=workspace)):
        command = shlex.join(tuple(str(part) for part in plan.get("command") or () if str(part).strip()))
        if command:
            commands.append(
                (
                    command,
                    max(1, min(int(plan.get("timeout_seconds") or 120), 300)),
                    f"00_environment_prep_{index:03d}",
                )
            )
    from polaris.kernelone.quality.step_verify import split_verify_directed_effect_commands

    commands.extend(
        (clause, 60, f"10_step_verify_{index:03d}")
        for index, clause in enumerate(split_verify_directed_effect_commands(verify))
        if str(clause or "").strip()
    )
    tool_results: list[dict[str, Any]] = []
    errors: list[str] = []
    for command, timeout_seconds, purpose in commands:
        result = defer_director_command_with_director_tools(
            workspace_path=workspace_path,
            task_id=task_id,
            execution_attempt=execution_attempt,
            command=command,
            timeout_seconds=timeout_seconds,
            purpose=purpose,
        )
        if result.get("success") is True:
            tool_results.append(result)
            continue
        raw_payload = result.get("result")
        payload = raw_payload if isinstance(raw_payload, dict) else {}
        errors.append(
            "step verify could not be admitted to directed-effect authority: "
            f"{payload.get('error_code') or 'deo_deferred_command_request_failed'}"
        )
    _record_deferred_step_verify_obligation(
        context,
        {
            **resolution.to_dict(),
            "disposition": "directed_effect_followup",
            "deferred_command_count": len(tool_results),
        },
    )
    return errors, tool_results


def _record_deferred_step_verify_obligation(
    context: dict[str, Any] | None,
    resolution: Mapping[str, Any],
) -> None:
    """Record a deferred project verifier without treating it as a failure."""

    if not isinstance(context, dict):
        return
    record = dict(resolution)
    existing = context.get("director_task_boundary_deferred_verification_obligations")
    if isinstance(existing, list):
        if record not in existing:
            existing.append(record)
        return
    context["director_task_boundary_deferred_verification_obligations"] = [record]


_STEP_VERIFY_NODE_ENV_COMMAND_RE = re.compile(
    r"(?:^|[\s;&|()])(?:npm|pnpm|yarn|npx|vitest|jest|tsc)\b",
    re.IGNORECASE,
)


def _step_verify_environment_prep_plans(verify: str, *, workspace: str) -> list[dict[str, Any]]:
    command_text = str(verify or "").strip()
    workspace_path = Path(str(workspace or "")).resolve()
    if not command_text or not workspace_path.is_dir():
        return []
    if not _STEP_VERIFY_NODE_ENV_COMMAND_RE.search(command_text):
        return []
    if not (workspace_path / "package.json").is_file():
        return []
    if (workspace_path / "node_modules").is_dir() and not _node_environment_has_missing_declared_packages(
        workspace_path
    ):
        return []
    try:
        from polaris.cells.director.runtime.public import (
            QueryDirectorRepairEnvironmentRefreshRequirementsV1,
            RepairReceiptV1,
            query_director_repair_environment_refresh_requirements,
        )

        receipt = RepairReceiptV1(
            receipt_id="step_verify_environment_requirement",
            plan_id="step_verify_environment_requirement",
            source_tool="director_step_verify_environment_requirement",
            status="succeeded",
            authoritative=False,
            files_changed=("package.json",),
            metadata={
                "environment_refresh_reason": "step_verify_requires_package_environment",
                "effect_boundary": "adapter_verifier_environment_prep_probe",
            },
        )
        result = query_director_repair_environment_refresh_requirements(
            QueryDirectorRepairEnvironmentRefreshRequirementsV1(
                receipts=(receipt,),
                workspace=str(workspace_path),
            )
        )
    except (ImportError, OSError, RuntimeError, TypeError, ValueError):
        return []
    return [plan.to_dict() for plan in result.plans]


def _node_environment_has_missing_declared_packages(workspace_path: Path) -> bool:
    package_path = workspace_path / "package.json"
    try:
        payload = json.loads(package_path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return False
    if not isinstance(payload, dict):
        return False

    declared: set[str] = set()
    for section in ("dependencies", "devDependencies", "optionalDependencies"):
        items = payload.get(section)
        if isinstance(items, dict):
            declared.update(str(name).strip() for name in items if str(name).strip())
    if not declared:
        return False

    lock_packages: set[str] = set()
    lock_path = workspace_path / "package-lock.json"
    if lock_path.is_file():
        try:
            lock_payload = json.loads(lock_path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError):
            lock_payload = {}
        packages = lock_payload.get("packages") if isinstance(lock_payload, dict) else None
        if isinstance(packages, dict):
            for key in packages:
                normalized = str(key or "").removeprefix("node_modules/").strip()
                if normalized:
                    lock_packages.add(normalized)

    for package_name in declared:
        package_dir = workspace_path / "node_modules" / package_name
        in_node_modules = package_dir.exists()
        in_lockfile = not lock_packages or package_name in lock_packages
        if not in_node_modules or not in_lockfile:
            return True
    return False


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


def _task_write_scope_candidates(task: dict[str, Any], *, workspace_name: str = "") -> list[str]:
    return _dedupe_preserve_order(
        [
            normalized
            for candidate in _extract_task_target_path_candidates(task)
            if (
                normalized := _normalize_declared_task_path(
                    str(candidate or ""),
                    workspace_name=workspace_name,
                )
            )
        ]
    )


def _path_within_task_write_scope(path: str, *, task: dict[str, Any], workspace_name: str = "") -> bool:
    in_scope, _out_of_scope = partition_paths_by_declared_scope(
        [path],
        _task_write_scope_candidates(task, workspace_name=workspace_name),
        workspace_name=workspace_name,
    )
    return bool(in_scope)


def _partition_paths_by_task_write_scope(
    paths: list[str],
    *,
    task: dict[str, Any],
    workspace_name: str = "",
) -> tuple[list[str], list[str]]:
    in_scope, out_of_scope = partition_paths_by_declared_scope(
        _dedupe_preserve_order(paths),
        _task_write_scope_candidates(task, workspace_name=workspace_name),
        workspace_name=workspace_name,
    )
    return list(in_scope), list(out_of_scope)


def _record_deferred_task_boundary_quality_errors(
    context: dict[str, Any] | None,
    *,
    errors: list[str],
    target_files: list[str],
    reason: str,
    issue_payloads: tuple[dict[str, Any], ...] = (),
) -> None:
    if not isinstance(context, dict) or not errors:
        return
    issues = artifact_quality_issues_for_errors(errors, issue_payloads) if issue_payloads else ()
    record: dict[str, Any] = {
        "schema_version": "director.task_boundary.deferred_quality_errors.v1",
        "reason": reason,
        "artifact_quality_errors": errors[:20],
        "target_files": target_files[:20],
    }
    if issues:
        record["artifact_quality_issues"] = [dict(issue) for issue in issues[:20]]
    existing = context.get("director_task_boundary_deferred_quality_errors")
    if isinstance(existing, list):
        existing.append(record)
    else:
        context["director_task_boundary_deferred_quality_errors"] = [record]


__all__ = [
    "_ACCEPTANCE_TEST_FILE_FLAGS",
    "_ACCEPTANCE_VERIFY_EXISTS_RE",
    "_JS_MISSING_NAMED_EXPORT_RE",
    "_JS_MODULE_SYSTEM_REPAIR_MARKERS",
    "_JS_NAMED_IMPORT_RE",
    "_NPM_SCRIPT_GENERATED_OUTPUT_PREFIXES",
    "_NPM_SCRIPT_MISSING_LOCAL_ENTRYPOINT_RE",
    "_NPM_SCRIPT_MISSING_LOCAL_MODULE_RE",
    "_NPM_SCRIPT_REPAIRABLE_SOURCE_PREFIXES",
    "_QUALITY_SYNTAX_ERROR_PATH_RE",
    "_RAW_SINGLE_TARGET_CODE_FENCE_RE",
    "_STEP_VERIFY_NODE_ENV_COMMAND_RE",
    "_TOOL_RECEIPT_CONTAMINATION_TOKENS",
    "_TSC_PROJECT_DIAGNOSTIC_RE",
    "_VERIFY_GREP_FILE_RE",
    "_VERIFY_TEST_FILE_RE",
    "_VERIFY_WC_PATH_RE",
    "_build_javascript_module_system_repair_block",
    "_build_javascript_named_export_repair_block",
    "_build_materialization_quality_failure_evidence_context",
    "_build_materialization_quality_workspace_evidence_context",
    "_clean_verify_path_token",
    "_collect_step_verify_errors",
    "_collect_workspace_code_diff",
    "_collect_workspace_out_of_scope_diff",
    "_deterministic_single_missing_python_module_alias_to_write_file",
    "_deterministic_single_missing_quality_repair_to_write_file",
    "_director_repair_force_existing_write_enabled",
    "_evaluate_acceptance_verify_exists",
    "_evaluate_machine_checkable_acceptance_criterion",
    "_evaluate_safe_acceptance_clause",
    "_format_quality_error_for_repair_prompt",
    "_format_tool_receipt_contamination_error_for_repair_prompt",
    "_format_typescript_project_typecheck_error_for_repair_prompt",
    "_format_unresolved_relative_import_error_for_repair_prompt",
    "_is_recoverable_no_write_mutation_contract_error_text",
    "_is_recoverable_no_write_mutation_contract_exception",
    "_iter_stage_summary_error_texts",
    "_js_default_imports_for_module",
    "_js_imported_symbols_for_module",
    "_looks_like_tool_receipt_contamination_text",
    "_near_miss_verify_target_paths",
    "_node_environment_has_missing_declared_packages",
    "_normalize_raw_single_target_write_content",
    "_parse_js_named_import_symbols",
    "_partition_paths_by_task_write_scope",
    "_path_stem_identity",
    "_path_within_task_write_scope",
    "_quality_error_path_safe_for_repair_prompt",
    "_quality_repair_base_files",
    "_quality_repair_edit_file_tool_definition",
    "_quality_repair_execute_command_tool_definition",
    "_quality_repair_execution_attempt",
    "_quality_repair_existing_target_tool_definitions",
    "_quality_repair_write_file_tool_definition",
    "_record_deferred_step_verify_obligation",
    "_record_deferred_task_boundary_quality_errors",
    "_reject_raw_single_target_repair_body",
    "_relative_import_specifier_safe_for_repair_prompt",
    "_resolve_workspace_path_case_insensitive",
    "_run_materialization_quality_public_boundary",
    "_safe_int",
    "_single_file_step_target",
    "_stage_summary_has_recoverable_no_write_mutation_contract_exception",
    "_step_verify_environment_prep_plans",
    "_step_verify_target_mismatch_error",
    "_summarize_llm_stage_result",
    "_task_write_scope_candidates",
    "_tool_receipt_safe_quality_errors",
    "_verify_referenced_file_paths",
    "_workspace_path_satisfies_flag",
]
