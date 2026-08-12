"""Pure workspace-quality repair evidence, discrepancy, and projection helpers.

Extracted from ``OrchestrationStageExecutor``. Every function is pure (no
``self``) and operates on repair result dicts, quality-check summaries, and
artifact quality error lists.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from polaris.cells.director.runtime.public.contracts import DirectorInterfaceDiscrepancyReceiptV1
from polaris.kernelone.tools.tool_kinds import WRITE_TOOLS

_WORKSPACE_QUALITY_MUTATION_TOKENS = WRITE_TOOLS | frozenset({"create_file", "text_replace"})

_LANGUAGE_NEUTRAL_FILENAMES: frozenset[str] = frozenset(
    {
        "go.mod",
        "go.sum",
        "package.json",
        "requirements.txt",
        "pyproject.toml",
        "cmakelists.txt",
    }
)

_WORKSPACE_QUALITY_REPAIR_SOURCE_SUFFIXES = frozenset(
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

_LEDGER_REPAIR_LIST_LIMIT = 24
_LEDGER_REPAIR_TEXT_LIMIT = 512


def _is_workspace_quality_repair_path(path: str) -> bool:
    normalized = os.path.normpath(str(path or "").strip().replace("\\", "/")).replace("\\", "/")
    if not normalized or normalized == "." or normalized.startswith("../") or normalized.startswith("/"):
        return False
    candidate = Path(normalized)
    return (
        candidate.suffix.lower() in _WORKSPACE_QUALITY_REPAIR_SOURCE_SUFFIXES
        or candidate.name.lower() in _LANGUAGE_NEUTRAL_FILENAMES
    )


def _dedupe_workspace_repair_paths(paths: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for raw_path in paths:
        normalized = os.path.normpath(str(raw_path or "").strip().replace("\\", "/")).replace("\\", "/")
        if not normalized or normalized == "." or normalized.startswith("../") or normalized.startswith("/"):
            continue
        if not _is_workspace_quality_repair_path(normalized):
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped


def workspace_quality_repair_result_has_mutation(item: dict[str, Any]) -> bool:
    """Return true only for a path-bound, non-no-op physical write receipt.

    A successful write-shaped tool row proves dispatch, not mutation.  Quality
    repair settlement must additionally carry the affected path and the
    before/after content hashes; otherwise a rejected/no-op ``edit_file`` can
    incorrectly complete the Director task without changing the workspace.
    """

    if not isinstance(item, dict) or not bool(item.get("success")):
        return False
    raw_result = item.get("result")
    result: dict[str, Any] = raw_result if isinstance(raw_result, dict) else {}
    tool_name = str(
        item.get("tool")
        or item.get("tool_name")
        or result.get("tool")
        or result.get("tool_name")
        or result.get("operation")
        or ""
    ).strip()
    operation = str(result.get("operation") or "").strip()
    if tool_name not in _WORKSPACE_QUALITY_MUTATION_TOKENS and operation not in _WORKSPACE_QUALITY_MUTATION_TOKENS:
        return False
    file_name = str(result.get("file") or result.get("path") or "").strip()
    if not _is_workspace_quality_repair_path(file_name):
        return False
    before_hash = str(result.get("before_sha256") or result.get("before_hash") or "").strip().lower()
    after_hash = str(result.get("after_sha256") or result.get("after_hash") or "").strip().lower()
    valid_hash_tokens = {"file_absent"}

    def valid_hash(value: str) -> bool:
        return value in valid_hash_tokens or (len(value) == 64 and all(char in "0123456789abcdef" for char in value))

    return bool(valid_hash(before_hash) and valid_hash(after_hash) and before_hash != after_hash)


def workspace_quality_repair_evidence(repair_results: list[dict[str, Any]]) -> list[str]:
    evidence: list[str] = []
    for item in repair_results:
        if not isinstance(item, dict) or not bool(item.get("success")):
            continue
        raw_result = item.get("result")
        result: dict[str, Any] = raw_result if isinstance(raw_result, dict) else {}
        source_tool = str(result.get("source_tool") or item.get("source_tool") or "").strip()
        file_name = str(result.get("file") or result.get("path") or "").strip()
        operation = str(result.get("operation") or "").strip()
        if source_tool or file_name:
            evidence.append(
                "repair_write:"
                f"tool={source_tool or str(item.get('tool') or item.get('tool_name') or 'unknown')};"
                f"file={file_name or 'unknown'};"
                f"operation={operation or 'unknown'}"
            )
        before_hash = str(result.get("before_sha256") or "").strip()
        after_hash = str(result.get("after_sha256") or "").strip()
        if before_hash or after_hash:
            evidence.append(
                f"repair_hash:file={file_name or 'unknown'};before={before_hash[:16]};after={after_hash[:16]}"
            )
        diff_excerpt = str(result.get("diff_excerpt") or "").strip()
        if diff_excerpt:
            compact_diff = " ".join(diff_excerpt.split())
            evidence.append(f"repair_diff:file={file_name or 'unknown'};excerpt={compact_diff[:360]}")
        if len(evidence) >= 12:
            break
    return evidence


def workspace_quality_summary_requires_task_boundary_triage(summary: dict[str, Any]) -> bool:
    if bool(summary.get("task_boundary_interface_discrepancy_retry_authorized")):
        return False
    stage = str(summary.get("stage") or "").strip()
    if stage == "runtime_plan_probe_unplannable":
        return True
    evidence = summary.get("interface_discrepancy_evidence")
    if (
        isinstance(evidence, dict)
        and str(evidence.get("reason") or "") == "coverage_matched_but_unplannable"
        and not bool(evidence.get("director_retry_allowed"))
    ):
        return True
    plan_probe = summary.get("plan_probe_preaudit")
    if not isinstance(plan_probe, dict):
        return False
    return str(plan_probe.get("status") or "").strip() == "coverage_matched_but_unplannable" and not bool(
        plan_probe.get("plannable_source_tools")
    )


def workspace_quality_deferred_owner_targets(summary: dict[str, Any]) -> list[str]:
    """Return precise targets deferred because the first repair task did not own them."""

    if str(summary.get("stage") or "").strip() != "task_boundary_repair_targets_deferred":
        return []
    scope_filter = summary.get("task_boundary_scope_filter")
    if not isinstance(scope_filter, Mapping):
        return []
    raw_targets = scope_filter.get("out_of_scope_repair_target_files")
    if not isinstance(raw_targets, list | tuple | set):
        return []
    return _dedupe_workspace_repair_paths([str(item or "") for item in raw_targets if str(item or "").strip()])


def workspace_quality_interface_discrepancy_evidence(
    summary: dict[str, Any],
    artifact_quality_errors: list[str] | None = None,
) -> dict[str, Any]:
    raw_evidence = summary.get("interface_discrepancy_evidence")
    evidence: dict[str, Any] = dict(raw_evidence) if isinstance(raw_evidence, dict) else {}
    plan_probe = summary.get("plan_probe_preaudit")
    plan_probe_payload = plan_probe if isinstance(plan_probe, dict) else {}
    covered_unplannable_source_tools = [
        str(item)
        for item in plan_probe_payload.get(
            "covered_unplannable_source_tools",
            evidence.get("covered_unplannable_source_tools", []),
        )
        if str(item or "").strip()
    ]
    if not evidence:
        evidence = {
            "schema_version": "director.interface_discrepancy_receipt.v1",
            "route": "task_boundary_quality_loop",
            "plan_probe_status": str(plan_probe_payload.get("status") or ""),
            "covered_unplannable_source_tools": covered_unplannable_source_tools,
            "covered_unplannable_diagnostic_count": int(
                plan_probe_payload.get("covered_unplannable_diagnostic_count") or 0
            ),
            "coverage_gap_count": int(plan_probe_payload.get("coverage_gap_count") or 0),
            "reason": "coverage_matched_but_unplannable",
        }
    diagnostic_blob = "\n".join(
        [
            json.dumps(plan_probe_payload, ensure_ascii=False, sort_keys=True),
            json.dumps(evidence, ensure_ascii=False, sort_keys=True),
            *[str(item or "") for item in artifact_quality_errors or []],
        ]
    ).lower()
    cross_artifact_markers = (
        "unresolved import",
        "unresolved relative import",
        "cannot find module",
        "has no exported member",
        "module has no exported member",
        "does not provide an export",
        "sibling module does not define",
        "is not exported",
        "undefined:",
        "undefined symbol",
        "unresolved external symbol",
        "undefined reference",
        "cannot find symbol",
        "cannot find type",
        "could not find",
        "no such file or directory",
        "file not found for module",
        "unresolved import `",
        "no `",
        "not found in",
        "was not declared in this scope",
        "no member named",
        "has no member named",
        "ts2305",
        "ts2306",
        "ts2307",
        "ts2459",
        "e0432",
        "e0583",
        "e0761",
    )
    local_implementation_markers = (
        "ts2322",
        "ts2339",
        "ts2345",
        "ts2552",
        "property ",
        "does not exist on type",
        "cannot find name",
        "type ",
        "is not assignable to type",
    )
    cross_artifact = any(marker in diagnostic_blob for marker in cross_artifact_markers)
    local_implementation = any(marker in diagnostic_blob for marker in local_implementation_markers)
    if cross_artifact:
        recommended_owner = "chief_engineer"
        recommended_route = "pending_design_interface_contract"
        cross_artifact_route = "contract_amendment_request"
    elif local_implementation:
        recommended_owner = "director"
        recommended_route = "director_retry_with_interface_discrepancy_context"
        cross_artifact_route = "director_repair_within_contract"
    else:
        recommended_owner = str(evidence.get("recommended_owner") or "chief_engineer")
        recommended_route = str(evidence.get("recommended_route") or "pending_design_interface_contract")
        cross_artifact_route = (
            "director_repair_within_contract" if recommended_owner == "director" else "contract_amendment_request"
        )
    director_retry_allowed = (
        recommended_owner == "director" and recommended_route == "director_retry_with_interface_discrepancy_context"
    )
    plan_probe_status = str(evidence.get("plan_probe_status") or plan_probe_payload.get("status") or "")
    covered_unplannable_diagnostic_count = int(
        plan_probe_payload.get(
            "covered_unplannable_diagnostic_count",
            evidence.get("covered_unplannable_diagnostic_count") or 0,
        )
        or 0
    )
    coverage_gap_count = int(plan_probe_payload.get("coverage_gap_count", evidence.get("coverage_gap_count") or 0) or 0)
    metadata_raw = evidence.get("metadata")
    metadata = dict(metadata_raw) if isinstance(metadata_raw, dict) else {}
    metadata.update(
        {
            "route": "task_boundary_quality_loop",
            "cross_artifact_route": cross_artifact_route,
            "coverage_gap_count": coverage_gap_count,
        }
    )
    canonical = DirectorInterfaceDiscrepancyReceiptV1.from_mapping(
        {
            **evidence,
            "task_id": str(
                summary.get("task_id") or summary.get("target_task_id") or summary.get("run_id") or "workspace-quality"
            ),
            "source": evidence.get("source") or "factory.pipeline.workspace_quality",
            "plan_probe_status": plan_probe_status,
            "covered_unplannable_source_tools": covered_unplannable_source_tools,
            "recommended_owner": recommended_owner,
            "recommended_route": recommended_route,
            "director_retry_allowed": director_retry_allowed,
            "llm_fallback_blocked": not director_retry_allowed,
            "reason": "coverage_matched_but_unplannable",
            "metadata": metadata,
        },
    ).to_dict()
    canonical.update(
        {
            "route": "task_boundary_quality_loop",
            "cross_artifact_route": cross_artifact_route,
            "coverage_gap_count": coverage_gap_count,
            "covered_unplannable_diagnostic_count": covered_unplannable_diagnostic_count,
        }
    )
    return canonical


def workspace_quality_interface_discrepancy_allows_director_retry(evidence: dict[str, Any]) -> bool:
    return bool(evidence.get("director_retry_allowed")) and (
        str(evidence.get("recommended_owner") or "") == "director"
        and str(evidence.get("recommended_route") or "") == "director_retry_with_interface_discrepancy_context"
    )


def workspace_quality_repair_summary_projection(
    summary: dict[str, Any],
    artifact_quality_errors: list[str] | None = None,
) -> dict[str, Any]:
    projected: dict[str, Any] = {}
    for key in (
        "stage",
        "attempt",
        "success",
        "success_reason",
        "reason",
        "error_code",
        "error",
        "repair_mode",
        "missing_target_files",
        "runtime_smoke_target_files",
        "semantic_quality_target_files",
        "explicit_quality_target_files",
        "repair_target_files",
        "rotated_repair_targets",
        "task_boundary_scope_filter",
        "deferred_owner_rebind",
        "plan_probe_preaudit",
        "interface_discrepancy_evidence",
        "deterministic_no_materialized_evidence",
        "repair_kernel",
        "deadline_decision",
    ):
        if key in summary:
            projected[key] = summary[key]
    if projected:
        task_boundary_triage_required = workspace_quality_summary_requires_task_boundary_triage(summary)
        projected["task_boundary_triage_required"] = task_boundary_triage_required
        if task_boundary_triage_required:
            projected["triage_stage"] = "runtime_plan_probe_unplannable"
            projected["interface_discrepancy_evidence"] = workspace_quality_interface_discrepancy_evidence(
                summary,
                artifact_quality_errors,
            )
    return projected


def _bounded_ledger_strings(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    return [
        str(item or "").strip()[:_LEDGER_REPAIR_TEXT_LIMIT]
        for item in value
        if str(item or "").strip()
    ][:_LEDGER_REPAIR_LIST_LIMIT]


def _compact_repair_probe(value: Any) -> dict[str, Any]:
    source = value if isinstance(value, Mapping) else {}
    projected: dict[str, Any] = {}
    for key in (
        "status",
        "total_diagnostics",
        "covered_diagnostic_count",
        "uncovered_diagnostic_count",
        "executable_runtime_plan_count",
        "metadata_only_diagnostic_count",
        "coverage_gap_count",
        "covered_unplannable_diagnostic_count",
    ):
        if key in source:
            projected[key] = source[key]
    for key in (
        "plannable_source_tools",
        "covered_unplannable_source_tools",
        "matched_source_tools",
        "source_tools",
    ):
        bounded = _bounded_ledger_strings(source.get(key))
        if bounded:
            projected[key] = bounded
    return projected


def _compact_repair_round(value: Any) -> dict[str, Any]:
    source = value if isinstance(value, Mapping) else {}
    projected: dict[str, Any] = {}
    for key in (
        "round",
        "attempted",
        "tool_results",
        "write_tool_evidence",
        "verifier_effect",
        "verifier_authoritative_success",
        "diagnostic_count_before",
        "diagnostic_count_after",
    ):
        if key in source:
            projected[key] = source[key]
    summary = source.get("repair_summary")
    summary = summary if isinstance(summary, Mapping) else {}
    summary_projection = {
        key: summary[key]
        for key in (
            "stage",
            "success",
            "success_reason",
            "reason",
            "error_code",
            "repair_mode",
            "success_authority",
            "verifier_effect",
            "task_boundary_triage_required",
        )
        if key in summary
    }
    if summary_projection:
        projected["repair_summary"] = summary_projection
    return projected


def workspace_quality_repair_ledger_projection(
    repair: dict[str, Any],
    *,
    full_evidence_ref: str,
) -> dict[str, Any]:
    """Project full repair evidence into a bounded Run Ledger receipt.

    Full workspace-quality evidence is already durable in
    ``runtime/qa/workspace-validation.json``.  Re-embedding its nested coverage
    reports in every Run Ledger event duplicated megabytes of data and then
    exceeded NATS' 1 MiB transport limit.  Ledger keeps decision-critical
    scalars plus a content hash/reference; the artifact remains the evidence
    authority.
    """

    canonical = json.dumps(repair, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    projected: dict[str, Any] = {
        "schema_version": "factory.workspace_quality_repair_ledger_projection.v1",
        "full_evidence_ref": str(full_evidence_ref or "").strip(),
        "full_evidence_sha256": hashlib.sha256(canonical).hexdigest(),
        "full_evidence_bytes": len(canonical),
    }
    for key in (
        "attempted",
        "success",
        "revalidated",
        "write_tool_evidence",
        "tool_results",
        "residual_error_count",
        "max_rounds",
        "consecutive_stagnant_rounds",
        "convergence_stop_reason",
        "stage",
        "error_code",
        "repair_mode",
    ):
        if key in repair:
            projected[key] = repair[key]
    for key in ("source_tools", "evidence", "artifact_quality_errors", "residual_errors"):
        bounded = _bounded_ledger_strings(repair.get(key))
        if bounded:
            projected[key] = bounded
            projected[f"{key}_total"] = len(repair.get(key) or [])
    for key in ("plan_probe_preaudit", "director_runtime_repair_coverage"):
        compact = _compact_repair_probe(repair.get(key))
        if compact:
            projected[key] = compact
    rounds = [_compact_repair_round(item) for item in repair.get("rounds", []) if isinstance(item, Mapping)]
    if rounds:
        projected["rounds"] = rounds[:_LEDGER_REPAIR_LIST_LIMIT]
        projected["round_count"] = len(repair.get("rounds") or [])
    return projected
