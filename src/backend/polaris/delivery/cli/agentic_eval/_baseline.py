"""Baseline / rerun audit resolution and score-diffing for agentic-eval.

Resolves ``--compare-baseline`` and ``--rerun-failed`` references to
AGENTIC_EVAL_AUDIT.json files and computes the score/case/check delta
between the current run and a baseline.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from polaris.kernelone.storage import resolve_runtime_path

from ._coerce import _as_dict, _as_list, _to_float, _to_int

__all__ = [
    "_build_baseline_comparison",
    "_extract_failed_case_ids",
    "_extract_failed_check_codes",
    "_read_json_file",
    "_resolve_baseline_audit_path",
    "_resolve_rerun_audit_path",
]

_DEFAULT_METADATA_DIR = ".polaris"


def _read_json_file(path: Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    return _as_dict(payload)


def _metadata_evaluation_roots(workspace: str) -> tuple[Path, ...]:
    """Return workspace-local evaluation roots in deterministic lookup order."""
    from polaris.kernelone._runtime_config import get_workspace_metadata_dir_name

    workspace_root = Path(workspace).resolve()
    metadata_dir = get_workspace_metadata_dir_name()
    metadata_names = [metadata_dir]
    if metadata_dir != _DEFAULT_METADATA_DIR:
        metadata_names.append(_DEFAULT_METADATA_DIR)
    return tuple(workspace_root / metadata_name / "runtime" / "llm_evaluations" for metadata_name in metadata_names)


def _resolve_baseline_audit_path(workspace: str, baseline_ref: str) -> Path:
    token = str(baseline_ref or "").strip()
    if not token:
        raise ValueError("empty baseline reference")

    candidate = Path(token)
    if candidate.is_file():
        return candidate.resolve()

    workspace_candidate = Path(workspace).resolve() / token
    if workspace_candidate.is_file():
        return workspace_candidate.resolve()

    run_id_candidate = Path(
        resolve_runtime_path(str(workspace), f"runtime/llm_evaluations/{token}/AGENTIC_EVAL_AUDIT.json")
    )
    if run_id_candidate.is_file():
        return run_id_candidate.resolve()

    for evaluation_root in _metadata_evaluation_roots(workspace):
        baseline_pull_candidate = evaluation_root / "baselines" / token / "BASELINE_LIBRARY_PULL.json"
        if baseline_pull_candidate.is_file():
            raise ValueError(
                "compare-baseline expects AGENTIC_EVAL_AUDIT.json baseline, "
                "but received baseline pull manifest. Run agentic-eval first to produce a score baseline."
            )

    raise FileNotFoundError(f"baseline audit not found: {token}")


def _extract_failed_case_ids(payload: Mapping[str, Any]) -> set[str]:
    failures = _as_list(payload.get("failures"))
    output: set[str] = set()
    for raw in failures:
        token = str(_as_dict(raw).get("case_id") or "").strip()
        if token:
            output.add(token)
    return output


def _resolve_rerun_audit_path(workspace: str, rerun_ref: str) -> tuple[Path, dict[str, Any]]:
    """Resolve the audit path for --rerun-failed and load the payload.

    Args:
        workspace: Workspace directory path.
        rerun_ref: Either a run_id (e.g., 'f6d7bb13') or an explicit JSON path.

    Returns:
        Tuple of (resolved_path, payload_dict).

    Raises:
        FileNotFoundError: If the audit file cannot be found.
    """
    token = str(rerun_ref or "").strip()
    if not token:
        raise ValueError("empty --rerun-failed reference")

    # Try as explicit file path first
    candidate = Path(token)
    if candidate.is_file():
        return candidate.resolve(), _read_json_file(candidate.resolve())

    # Try as workspace-relative path
    workspace_candidate = Path(workspace).resolve() / token
    if workspace_candidate.is_file():
        return workspace_candidate.resolve(), _read_json_file(workspace_candidate.resolve())

    # Try as run_id under runtime/llm_evaluations/
    run_id_candidate = Path(
        resolve_runtime_path(str(workspace), f"runtime/llm_evaluations/{token}/AGENTIC_EVAL_AUDIT.json")
    )
    if run_id_candidate.is_file():
        return run_id_candidate.resolve(), _read_json_file(run_id_candidate.resolve())

    metadata_candidates: list[Path] = []
    for evaluation_root in _metadata_evaluation_roots(workspace):
        metadata_candidate = evaluation_root / token / "AGENTIC_EVAL_AUDIT.json"
        metadata_candidates.append(metadata_candidate)
        if metadata_candidate.is_file():
            return metadata_candidate.resolve(), _read_json_file(metadata_candidate.resolve())

    metadata_attempts = "\n".join(f"  - Metadata path: {path}" for path in metadata_candidates)

    raise FileNotFoundError(
        f"audit file not found for --rerun-failed: {rerun_ref}\n"
        f"Tried:\n"
        f"  - Explicit path: {candidate}\n"
        f"  - Workspace-relative: {workspace_candidate}\n"
        f"  - Runtime path: {run_id_candidate}\n"
        f"{metadata_attempts}"
    )


def _extract_failed_check_codes(payload: Mapping[str, Any]) -> set[str]:
    failures = _as_list(payload.get("failures"))
    output: set[str] = set()
    for raw in failures:
        failure = _as_dict(raw)
        for check_raw in _as_list(failure.get("failed_checks")):
            code = str(_as_dict(check_raw).get("code") or "").strip()
            if code:
                output.add(code)
    return output


def _build_baseline_comparison(
    *,
    current_payload: Mapping[str, Any],
    baseline_payload: Mapping[str, Any],
    baseline_path: Path,
    baseline_ref: str,
) -> dict[str, Any]:
    current_score = _as_dict(current_payload.get("score"))
    baseline_score = _as_dict(baseline_payload.get("score"))
    current_benchmark = _as_dict(current_payload.get("benchmark"))
    baseline_benchmark = _as_dict(baseline_payload.get("benchmark"))
    current_tool_audit = _as_dict(current_payload.get("tool_audit"))
    baseline_tool_audit = _as_dict(baseline_payload.get("tool_audit"))

    current_fail_cases = _extract_failed_case_ids(current_payload)
    baseline_fail_cases = _extract_failed_case_ids(baseline_payload)
    new_failures = sorted(current_fail_cases - baseline_fail_cases)
    resolved_failures = sorted(baseline_fail_cases - current_fail_cases)
    persistent_failures = sorted(current_fail_cases & baseline_fail_cases)

    current_check_codes = _extract_failed_check_codes(current_payload)
    baseline_check_codes = _extract_failed_check_codes(baseline_payload)
    new_check_codes = sorted(current_check_codes - baseline_check_codes)
    resolved_check_codes = sorted(baseline_check_codes - current_check_codes)

    current_overall = _to_float(current_score.get("overall_percent"), 0.0)
    baseline_overall = _to_float(baseline_score.get("overall_percent"), 0.0)
    current_pass_rate = _to_float(current_score.get("pass_rate"), 0.0)
    baseline_pass_rate = _to_float(baseline_score.get("pass_rate"), 0.0)
    current_tool_calls = _to_int(current_tool_audit.get("total_calls"), 0)
    baseline_tool_calls = _to_int(baseline_tool_audit.get("total_calls"), 0)

    if new_failures and resolved_failures:
        trend = "mixed"
    elif new_failures:
        trend = "regressed"
    elif resolved_failures or round(current_overall - baseline_overall, 2) > 0.0:
        trend = "improved"
    elif round(current_overall - baseline_overall, 2) < 0.0:
        trend = "regressed"
    else:
        trend = "unchanged"

    return {
        "enabled": True,
        "baseline_ref": baseline_ref,
        "baseline_path": str(baseline_path),
        "trend": trend,
        "current": {
            "run_id": str(current_benchmark.get("run_id") or "").strip(),
            "suite": str(current_benchmark.get("suite") or "").strip(),
            "status": str(current_payload.get("status") or "").strip(),
            "overall_percent": current_overall,
            "pass_rate": current_pass_rate,
            "failed_cases": _to_int(current_score.get("failed_cases"), 0),
            "tool_calls": current_tool_calls,
        },
        "baseline": {
            "run_id": str(baseline_benchmark.get("run_id") or "").strip(),
            "suite": str(baseline_benchmark.get("suite") or "").strip(),
            "status": str(baseline_payload.get("status") or "").strip(),
            "overall_percent": baseline_overall,
            "pass_rate": baseline_pass_rate,
            "failed_cases": _to_int(baseline_score.get("failed_cases"), 0),
            "tool_calls": baseline_tool_calls,
        },
        "delta": {
            "overall_percent": round(current_overall - baseline_overall, 2),
            "pass_rate": round(current_pass_rate - baseline_pass_rate, 4),
            "failed_cases": _to_int(current_score.get("failed_cases"), 0)
            - _to_int(baseline_score.get("failed_cases"), 0),
            "tool_calls": current_tool_calls - baseline_tool_calls,
        },
        "cases": {
            "new_failures": new_failures,
            "resolved_failures": resolved_failures,
            "persistent_failures": persistent_failures,
        },
        "checks": {
            "new_failed_check_codes": new_check_codes,
            "resolved_check_codes": resolved_check_codes,
        },
    }
