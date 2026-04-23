"""One-command deterministic agentic benchmark runner for Polaris CLI.

This command executes ``llm.evaluation`` benchmark cases and emits:
- score and pass/fail status
- failed deterministic checks (root causes)
- tool-call audit summary
- aggregated deterministic repair plan
- UTF-8 JSON audit package persisted under workspace runtime
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from polaris.cells.llm.evaluation.public.service import (
    list_baseline_library_sources,
    pull_baseline_library,
    run_agentic_benchmark_suite,
    run_context_benchmark_suite,
    run_strategy_benchmark_suite,
    run_tool_calling_matrix_suite,
)
from polaris.infrastructure.storage import LocalFileSystemAdapter
from polaris.kernelone.fs.runtime import KernelFileSystem
from polaris.kernelone.storage import resolve_runtime_path
from polaris.kernelone.utils.time_utils import utc_now_iso

if TYPE_CHECKING:
    import argparse

# Probe timeout per role (seconds)
_DEFAULT_PROBE_TIMEOUT = 30.0
_PROBE_MESSAGE = "Hello, respond with just the word 'ok'."
_ALL_PROBE_ROLES = ("pm", "architect", "chief_engineer", "director", "qa")

_CHECK_CATEGORY_ORDER = {
    "safety": 0,
    "contract": 1,
    "tooling": 2,
    "evidence": 3,
}


async def _probe_role(workspace: str, role: str, timeout_seconds: float) -> dict[str, Any]:
    """Probe a single role's LLM accessibility.

    Sends a minimal message and verifies the role can respond without errors.
    Uses validate_output=False to avoid false negatives from role-specific
    output schema validation (the probe only checks connectivity).

    Returns:
        {"role": str, "ok": bool, "error": str or None,
         "output_preview": str or None, "duration_ms": int}
    """
    started_at = time.perf_counter()
    error_msg: str | None = None
    output_preview: str | None = None
    ok_flag = False

    try:
        # Use generate_role_response with validation disabled — we only care
        # that the role's LLM is reachable, not that its output conforms to
        # the role's schema.
        from polaris.cells.llm.dialogue.public import (
            generate_role_response,
        )

        result = await asyncio.wait_for(
            generate_role_response(
                workspace=workspace,
                settings={},
                role=role,
                message=_PROBE_MESSAGE,
                validate_output=False,
                max_retries=0,
            ),
            timeout=timeout_seconds,
        )
        result_dict = _as_dict(result)
        ok_flag = not bool(result_dict.get("error"))
        output_preview = str(result_dict.get("response") or "").strip()[:120]
        error_text = str(result_dict.get("error") or "").strip()
        if error_text:
            error_msg = error_text
        elif not output_preview:
            error_msg = "empty response from role"
        # Resolve provider/model from runtime config (authoritative binding)
        from polaris.kernelone.llm.runtime_config import RuntimeConfigManager

        role_cfg = RuntimeConfigManager().get_role_config(role)
        if role_cfg:
            provider_id = role_cfg.provider_id
            model_name = role_cfg.model
        else:
            provider_id = str(result_dict.get("provider") or "unknown").strip()
            model_name = str(result_dict.get("model") or "unknown").strip()
    except asyncio.TimeoutError:
        error_msg = f"timeout after {timeout_seconds}s"
        provider_id = "unknown"
        model_name = "unknown"
    except (RuntimeError, ValueError) as exc:
        error_msg = f"exception: {exc}"
        provider_id = "unknown"
        model_name = "unknown"

    duration_ms = round((time.perf_counter() - started_at) * 1000)
    return {
        "role": role,
        "ok": ok_flag,
        "error": error_msg,
        "output_preview": output_preview,
        "duration_ms": duration_ms,
        "provider": provider_id,
        "model": model_name,
    }


async def _run_probe_async(
    workspace: str,
    roles: tuple[str, ...] | None = None,
    timeout_seconds: float = _DEFAULT_PROBE_TIMEOUT,
) -> dict[str, Any]:
    """Run probe for all specified roles concurrently.

    Args:
        workspace: Workspace directory path.
        roles: Tuple of role names to probe. Defaults to all 5 roles.
        timeout_seconds: Timeout per individual role probe.

    Returns:
        {"ok": bool, "roles": {role: probe_result}, "failed_roles": [role],
         "passed_roles": [role]}
    """
    target_roles = tuple(roles) if roles else _ALL_PROBE_ROLES

    tasks = [_probe_role(workspace, role, timeout_seconds) for role in target_roles]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    role_results: dict[str, dict[str, Any]] = {}
    failed_roles: list[str] = []
    passed_roles: list[str] = []

    for role, result in zip(target_roles, results, strict=True):
        if isinstance(result, Exception):
            role_results[role] = {
                "role": role,
                "ok": False,
                "error": f"exception: {result}",
                "output_preview": None,
                "duration_ms": 0,
            }
            failed_roles.append(role)
        else:
            role_results[role] = result  # type: ignore[assignment]
            if isinstance(result, dict) and result.get("ok"):
                passed_roles.append(role)
            else:
                failed_roles.append(role)

    all_ok = len(failed_roles) == 0
    return {
        "ok": all_ok,
        "roles": role_results,
        "failed_roles": failed_roles,
        "passed_roles": passed_roles,
    }


def run_probe(
    workspace: str,
    roles: list[str] | tuple[str, ...] | None = None,
    timeout_seconds: float = _DEFAULT_PROBE_TIMEOUT,
    output_format: str = "human",
) -> dict[str, Any]:
    """Synchronous entry point for the role probe.

    Args:
        workspace: Workspace directory path.
        roles: List or tuple of role names to probe. Defaults to all 5 roles.
        timeout_seconds: Timeout per role probe (default 30s).
        output_format: "human" or "json".

    Returns:
        Dict with overall ok status and per-role results. Exits process
        with non-zero code if any role fails when output_format is "human".
    """
    # Normalise roles to tuple
    if roles is None:
        target_roles: tuple[str, ...] = _ALL_PROBE_ROLES
    elif isinstance(roles, list):
        target_roles = tuple(roles)
    else:
        target_roles = roles

    probe_result = asyncio.run(_run_probe_async(workspace, target_roles, timeout_seconds))

    if output_format == "json":
        print(json.dumps(probe_result, ensure_ascii=False, indent=2))
    else:
        _print_probe_human(probe_result)

    return probe_result


def _print_probe_human(probe_result: Mapping[str, Any]) -> None:
    """Print probe results in human-readable format."""
    role_results = _as_dict(probe_result.get("roles"))
    failed_roles = _as_list(probe_result.get("failed_roles"))
    passed_roles = _as_list(probe_result.get("passed_roles"))

    print(f"[agentic-eval probe] status={'PASS' if probe_result.get('ok') else 'FAIL'}")
    print(
        f"[agentic-eval probe] passed={len(passed_roles)}/{len(role_results)} "
        f"failed={len(failed_roles)}/{len(role_results)}"
    )

    for role, result in role_results.items():
        result_dict = _as_dict(result)
        status_tag = "PASS" if result_dict.get("ok") else "FAIL"
        duration_ms = result_dict.get("duration_ms", 0)
        provider = result_dict.get("provider") or "unknown"
        model = result_dict.get("model") or "unknown"
        error = result_dict.get("error")
        preview = result_dict.get("output_preview")
        binding = f"{provider}/{model}"
        error_suffix = f" error={error}" if error else ""
        preview_suffix = f" output={preview!r}" if preview else ""
        print(
            f"[agentic-eval probe] {role}: {status_tag} "
            f"binding={binding} duration_ms={duration_ms}{error_suffix}{preview_suffix}"
        )

    if failed_roles:
        print(f"[agentic-eval probe] failed_roles={','.join(failed_roles)}")
    else:
        print("[agentic-eval probe] all roles accessible")


def _suite_runners() -> dict[str, Any]:
    return {
        "agentic_benchmark": run_agentic_benchmark_suite,
        "tool_calling_matrix": run_tool_calling_matrix_suite,
    }


def _run_benchmark_by_mode(
    mode: str,
    provider_cfg: dict[str, Any],
    model: str | None,
    role: str,
    workspace: str,
    context: Mapping[str, Any],
    options: Mapping[str, Any],
) -> dict[str, Any]:
    """Route benchmark execution based on mode.

    Args:
        mode: Benchmark mode - "agentic", "strategy", "context", or "all"
        provider_cfg: Provider configuration dict
        model: Model name
        role: Role identifier
        workspace: Workspace path
        context: Context mapping
        options: Options mapping

    Returns:
        For single mode: benchmark result dict
        For "all" mode: aggregated result dict
    """
    if mode == "all":
        # Run all three suites sequentially and aggregate results
        agentic_result = asyncio.run(
            run_agentic_benchmark_suite(
                provider_cfg,
                model,
                role,
                workspace=workspace,
                context=context,
                options=options,
            )
        )

        # Context and strategy get role from options/context
        context_options = dict(options)
        context_options["role"] = role
        context_result = asyncio.run(
            run_context_benchmark_suite(
                provider_cfg,
                model,
                workspace=workspace,
                context=context,
                options=context_options,
            )
        )

        strategy_options = dict(options)
        strategy_options["role"] = role
        strategy_result = asyncio.run(
            run_strategy_benchmark_suite(
                provider_cfg,
                model,
                workspace=workspace,
                context=context,
                options=strategy_options,
            )
        )

        # Aggregate results from all three modes
        return _aggregate_all_mode_results(
            agentic=agentic_result,
            context=context_result,
            strategy=strategy_result,
        )

    # Single mode - route to appropriate runner
    runner: Any
    if mode == "agentic":
        runner = run_agentic_benchmark_suite
    elif mode == "context":
        runner = run_context_benchmark_suite
    elif mode == "strategy":
        runner = run_strategy_benchmark_suite
    else:
        return {"ok": False, "error": f"unknown mode: {mode}", "details": {}}

    # For context/strategy, role goes in options
    if mode in ("context", "strategy"):
        opts = dict(options)
        opts["role"] = role
        return asyncio.run(
            runner(
                provider_cfg,
                model,
                workspace=workspace,
                context=context,
                options=opts,
            )
        )

    # For agentic, role is a direct parameter
    return asyncio.run(
        runner(
            provider_cfg,
            model,
            role,
            workspace=workspace,
            context=context,
            options=options,
        )
    )


def _aggregate_all_mode_results(
    agentic: dict[str, Any],
    context: dict[str, Any],
    strategy: dict[str, Any],
) -> dict[str, Any]:
    """Aggregate results from agentic, context, and strategy benchmarks.

    Returns a combined result dict with aggregated scores and status.
    """
    # Extract ok status and scores from each mode
    agentic_ok = bool(agentic.get("ok", False))
    context_ok = bool(context.get("ok", False))
    strategy_ok = bool(strategy.get("ok", False))

    # Get scores from each mode
    def _get_score(result: dict[str, Any]) -> float:
        if "details" in result:
            # Legacy agentic format
            details = result.get("details", {})
            return float(details.get("average_score", 0.0))
        # New format from context/strategy
        summary = result.get("summary", {})
        return float(summary.get("average_score", 0.0))

    def _get_total(result: dict[str, Any]) -> int:
        if "details" in result:
            return int(result.get("details", {}).get("total_cases", 0))
        return int(result.get("summary", {}).get("total", 0))

    def _get_passed(result: dict[str, Any]) -> int:
        if "details" in result:
            return int(result.get("details", {}).get("passed_cases", 0))
        return int(result.get("summary", {}).get("passed", 0))

    total_cases = sum(_get_total(r) for r in (agentic, context, strategy))
    total_passed = sum(_get_passed(r) for r in (agentic, context, strategy))
    total_failed = total_cases - total_passed
    avg_score = sum(_get_score(r) for r in (agentic, context, strategy)) / 3.0 if total_cases > 0 else 0.0

    # Use agentic's artifact_path and run_id as primary
    agentic_details = agentic.get("details", {})
    agentic_report = agentic_details.get("report", {})
    run_id = agentic_report.get("test_run_id", "all-mode")

    return {
        "ok": agentic_ok and context_ok and strategy_ok,
        "error": "",
        "details": {
            "cases": agentic_details.get("cases", []),
            "artifact_path": agentic_details.get("artifact_path", ""),
            "report": {
                "suite": "all_benchmark",
                "test_run_id": run_id,
                "summary": {
                    "total_cases": total_cases,
                    "passed_cases": total_passed,
                    "failed_cases": total_failed,
                    "average_score": avg_score,
                },
            },
            "total_cases": total_cases,
            "passed_cases": total_passed,
            "failed_cases": total_failed,
            "average_score": avg_score,
            "mode_results": {
                "agentic": agentic,
                "context": context,
                "strategy": strategy,
            },
        },
    }


def _normalize_suite_name(value: Any) -> str:
    token = str(value or "").strip().lower()
    if token in _suite_runners():
        return token
    return "agentic_benchmark"


# Backward compatibility alias
_utc_now = utc_now_iso


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (RuntimeError, ValueError, TypeError):
        return int(default)


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (RuntimeError, ValueError, TypeError):
        return float(default)


def _to_percent(value: Any) -> float:
    return round(_to_float(value) * 100.0, 2)


def _normalise_case_ids(values: Iterable[Any] | None) -> list[str]:
    if values is None:
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for item in values:
        token = str(item or "").strip()
        if not token or token in seen:
            continue
        seen.add(token)
        normalized.append(token)
    return normalized


def _normalize_tokens(values: Iterable[Any] | None) -> list[str]:
    if values is None:
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for item in values:
        token = str(item or "").strip().lower()
        if not token or token in seen:
            continue
        seen.add(token)
        normalized.append(token)
    return normalized


def _normalize_matrix_transport(value: Any) -> str:
    token = str(value or "").strip().lower()
    if token in {"stream", "non_stream"}:
        return token
    return "stream"


# Level to case prefix mapping for tool_calling_matrix suite
_TOOL_CALLING_MATRIX_LEVEL_PREFIXES: dict[int, str] = {
    1: "l1_",
    2: "l2_",
    3: "l3_",
    4: "l4_",
    5: "l5_",
    6: "l6_",
    7: "l7_",
    8: "l8_",
    9: "l9_",
}


def _parse_level_range(range_str: str) -> set[int]:
    """Parse a level range string like 'l1-l3' into a set of level numbers.

    Supports formats:
    - 'l1' or '1' -> single level {1}
    - 'l1-l3' or '1-3' -> levels {1, 2, 3}
    - 'l1,l3' or '1,3' -> levels {1, 3}

    Returns:
        Set of level numbers (1-9).
    """
    result: set[int] = set()
    token = str(range_str or "").strip().lower()
    if not token:
        return result

    # Remove single 'l' prefix if present (but not all 'l' chars)
    if token.startswith("l") and len(token) > 1 and token[1].isdigit():
        token = token[1:]

    # Handle comma-separated values
    for part in token.split(","):
        part = part.strip()
        if not part:
            continue
        # Handle range like '1-3' or 'l1-l3'
        if "-" in part:
            range_parts = part.split("-")
            if len(range_parts) == 2:
                # Strip 'l' prefix from each part if present
                start_str = range_parts[0].strip()
                end_str = range_parts[1].strip()
                if start_str.startswith("l") and len(start_str) > 1 and start_str[1].isdigit():
                    start_str = start_str[1:]
                if end_str.startswith("l") and len(end_str) > 1 and end_str[1].isdigit():
                    end_str = end_str[1:]
                try:
                    start = int(start_str)
                    end = int(end_str)
                    for level in range(start, end + 1):
                        if 1 <= level <= 9:
                            result.add(level)
                except ValueError:
                    pass
        else:
            # Single number - strip 'l' prefix if present
            if part.startswith("l") and len(part) > 1 and part[1].isdigit():
                part = part[1:]
            try:
                level = int(part)
                if 1 <= level <= 9:
                    result.add(level)
            except ValueError:
                pass
    return result


def _expand_level_range_to_case_ids(level_ranges: Iterable[Any] | None) -> list[str]:
    """Expand level range strings like 'l1-l3' into case ID prefixes for filtering.

    Returns list of case ID prefixes like ['l1_', 'l2_', 'l3_'].
    """
    if level_ranges is None:
        return []
    levels: set[int] = set()
    for item in level_ranges:
        token = str(item or "").strip()
        if not token:
            continue
        levels.update(_parse_level_range(token))

    prefixes: list[str] = []
    for level in sorted(levels):
        prefix = _TOOL_CALLING_MATRIX_LEVEL_PREFIXES.get(level)
        if prefix:
            prefixes.append(prefix)
    return prefixes


def _filter_cases_by_level_prefix(cases: list[Any], level_prefixes: list[str]) -> list[Any]:
    """Filter a list of cases by level prefixes (e.g., 'l1_', 'l2_')."""
    if not level_prefixes:
        return cases
    return [
        case for case in cases if any(str(getattr(case, "case_id", "") or "").startswith(p) for p in level_prefixes)
    ]


def _read_json_file(path: Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    return _as_dict(payload)


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

    from polaris.kernelone._runtime_config import get_workspace_metadata_dir_name

    metadata_dir = get_workspace_metadata_dir_name()
    baseline_pull_candidate = (
        Path(workspace).resolve()
        / metadata_dir
        / "runtime"
        / "llm_evaluations"
        / "baselines"
        / token
        / "BASELINE_LIBRARY_PULL.json"
    )
    if baseline_pull_candidate.is_file():
        raise ValueError(
            "compare-baseline expects AGENTIC_EVAL_AUDIT.json baseline, "
            "but received baseline pull manifest. Run agentic-eval first to produce a score baseline."
        )

    # Backward compat: also check legacy .polaris path
    legacy_baseline = (
        Path(workspace).resolve()
        / ".polaris"
        / "runtime"
        / "llm_evaluations"
        / "baselines"
        / token
        / "BASELINE_LIBRARY_PULL.json"
    )
    if legacy_baseline.is_file():
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

    # Try as run_id under .polaris/runtime/llm_evaluations/
    legacy_candidate = (
        Path(workspace).resolve() / ".polaris" / "runtime" / "llm_evaluations" / token / "AGENTIC_EVAL_AUDIT.json"
    )
    if legacy_candidate.is_file():
        return legacy_candidate.resolve(), _read_json_file(legacy_candidate.resolve())

    raise FileNotFoundError(
        f"audit file not found for --rerun-failed: {rerun_ref}\n"
        f"Tried:\n"
        f"  - Explicit path: {candidate}\n"
        f"  - Workspace-relative: {workspace_candidate}\n"
        f"  - Runtime path: {run_id_candidate}\n"
        f"  - Legacy path: {legacy_candidate}"
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


def _normalize_check_code(check_code: str) -> str:
    token = str(check_code or "").strip().lower()
    if token.startswith("stream:") or token.startswith("non_stream:"):
        _, remainder = token.split(":", 1)
        return remainder
    return token


def _repair_hint(check_code: str, category: str) -> str:
    code = _normalize_check_code(check_code)
    cat = str(category or "").strip().lower()

    if code.startswith("parity:"):
        return "Align stream/non-stream execution policy so both transports emit equivalent tool traces."
    if code.startswith("required_tool:"):
        tool = code.split(":", 1)[-1]
        return f"Enforce required tool `{tool}` in role policy and add trace assertion in regression suite."
    if code.startswith("required_tool_argument:"):
        return "Pin required file/path evidence in tool args before final answer."
    if code in {"min_tool_calls", "max_tool_calls"}:
        return "Tune tool-call loop policy to keep calls within benchmark bounds."
    if code == "textual_tool_protocol_without_trace":
        return (
            "Provider/runtime is emitting textual pseudo-tool calls instead of native tool traces; "
            "verify tool schema binding, provider tool support, and suppress `[TOOL_CALL]` wrappers."
        )
    if code.startswith("forbidden_tool:") or code.startswith("forbidden_tool_argument:"):
        return "Tighten tool/path allowlist and block unsafe write scopes at policy layer."
    if code.startswith("required_output:"):
        return "Harden output contract template so required fields/tokens are always emitted."
    if code.startswith("forbidden_output:"):
        return "Add output sanitizer to strip forbidden markers before response emission."
    if code == "validator:no_prompt_leakage":
        return "Prevent prompt leakage by filtering system/thinking/tool tags in final output."
    if code == "validator:pm_plan_json":
        return "Enforce strict PM JSON schema (`goal`, `backlog`, `timeline`) and schema-validate before return."
    if code == "validator:qa_passfail_json":
        return "Enforce strict QA JSON schema (`passed`, `findings`) and reject free-form text."
    if code == "validator:director_safe_scope":
        return "Constrain Director plans to allowed paths and include explicit verification step."
    if code == "validator:no_hallucinated_paths":
        return "Require path mentions to come from actual workspace file listing."
    if code == "validator:structured_steps":
        return "Use numbered step output template to guarantee structured plans."

    if cat == "safety":
        return "Add safety guardrails for tool usage and output sanitization."
    if cat == "contract":
        return "Strengthen response schema validation in role post-processor."
    if cat == "tooling":
        return "Adjust tool invocation policy to satisfy deterministic benchmark expectations."
    if cat == "evidence":
        return "Require explicit local evidence references before verdict output."
    return "Review failed check and add deterministic policy + regression assertion."


def _check_priority(check: Mapping[str, Any]) -> tuple[int, int, str]:
    critical = 0 if bool(check.get("critical")) else 1
    category_rank = _CHECK_CATEGORY_ORDER.get(str(check.get("category") or "").strip().lower(), 9)
    code = str(check.get("code") or "")
    return critical, category_rank, code


def _failure_priority(check: Mapping[str, Any]) -> int:
    if bool(check.get("critical")):
        return 0
    category = str(check.get("category") or "").strip().lower()
    if category == "safety":
        return 0
    if category == "contract":
        return 1
    return 2


def _priority_label(priority: int) -> str:
    if priority <= 0:
        return "P0"
    if priority == 1:
        return "P1"
    return "P2"


def _default_output_path(run_id: str) -> str:
    token = str(run_id or "").strip() or datetime.now(timezone.utc).strftime("cli-%Y%m%d%H%M%S")
    return f"runtime/llm_evaluations/{token}/AGENTIC_EVAL_AUDIT.json"


def _extract_failed_checks(case_payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    judge = _as_dict(case_payload.get("judge"))
    checks = _as_list(judge.get("checks"))
    failed: list[dict[str, Any]] = []
    for raw in checks:
        check = _as_dict(raw)
        if not check or bool(check.get("passed")):
            continue
        failed.append(
            {
                "code": str(check.get("code") or "").strip(),
                "category": str(check.get("category") or "").strip().lower(),
                "critical": bool(check.get("critical")),
                "message": str(check.get("message") or "").strip(),
                "evidence": _as_dict(check.get("evidence")),
            }
        )
    failed.sort(key=_check_priority)
    return failed


def _truncate_text(value: Any, limit: int = 220) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _format_counter(counter: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for key, value in counter.items():
        token = str(key or "").strip() or "unknown"
        parts.append(f"{token}:{_to_int(value, 0)}")
    return ", ".join(parts)


def _event_type_histogram(raw_events: Iterable[Any]) -> dict[str, int]:
    histogram: Counter[str] = Counter()
    for raw in raw_events:
        event = _as_dict(raw)
        event_type = str(event.get("type") or "").strip() or "unknown"
        histogram[event_type] += 1
    return dict(sorted(histogram.items()))


def _summarize_tool_calls(tool_calls: Iterable[Any], *, limit: int = 5) -> list[dict[str, Any]]:
    summary: list[dict[str, Any]] = []
    for index, raw in enumerate(tool_calls):
        if index >= limit:
            break
        call = _as_dict(raw)
        args = _as_dict(call.get("args"))
        try:
            args_text = json.dumps(args, ensure_ascii=False, sort_keys=True)
        except (RuntimeError, ValueError):
            args_text = str(args)
        summary.append(
            {
                "tool": str(call.get("tool") or "").strip(),
                "args_preview": _truncate_text(args_text, limit=180),
                "event_index": _to_int(call.get("event_index"), 0),
            }
        )
    return summary


def _summarize_raw_events(raw_events: Iterable[Any], *, limit: int = 8) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_events):
        if index >= limit:
            break
        event = _as_dict(raw)
        event_type = str(event.get("type") or "").strip() or "unknown"
        sample: dict[str, Any] = {"index": index, "type": event_type}
        if event_type in {"content_chunk", "thinking_chunk", "chunk", "reasoning_chunk"}:
            sample["content_preview"] = _truncate_text(event.get("content"), limit=160)
        if event_type == "tool_call":
            sample["tool"] = str(event.get("tool") or "").strip()
            sample["args_preview"] = _truncate_text(
                json.dumps(_as_dict(event.get("args")), ensure_ascii=False, sort_keys=True),
                limit=160,
            )
        if event_type == "error":
            sample["error"] = _truncate_text(event.get("error"), limit=160)
        if event_type == "fingerprint":
            fingerprint = {
                key: event.get(key)
                for key in ("profile_id", "profile_hash", "bundle_id", "bundle_version", "run_id", "turn_index")
                if event.get(key) not in (None, "")
            }
            if fingerprint:
                sample["fingerprint"] = fingerprint
        if len(sample) == 2:
            sample["keys"] = sorted(event.keys())
        samples.append(sample)
    return samples


def _extract_textual_tool_markers(
    *,
    failed_checks: Iterable[Mapping[str, Any]],
    observed: Mapping[str, Any],
) -> list[str]:
    for raw in failed_checks:
        check = _as_dict(raw)
        if str(check.get("code") or "").strip().lower() != "textual_tool_protocol_without_trace":
            continue
        evidence = _as_dict(check.get("evidence"))
        markers = [str(item).strip() for item in _as_list(evidence.get("markers")) if str(item).strip()]
        if markers:
            return markers
    combined_text = "\n".join(
        str(item or "").strip()
        for item in (observed.get("output"), observed.get("thinking"))
        if str(item or "").strip()
    )
    found_markers: list[str] = []
    for token in ("[TOOL_CALL]", "[/TOOL_CALL]", "<tool_call>", "</tool_call>"):
        if token.lower() in combined_text.lower():
            found_markers.append(token)
    return found_markers


def _build_failure_diagnosis(
    *,
    failed_checks: Iterable[Mapping[str, Any]],
    observed: Mapping[str, Any],
    stream_observed: Mapping[str, Any] | None = None,
    non_stream_observed: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    missing_required_tools: list[str] = []
    forbidden_tools_triggered: list[str] = []
    missing_output_tokens: list[str] = []
    failed_validators: list[str] = []

    for raw in failed_checks:
        check = _as_dict(raw)
        code = str(check.get("code") or "").strip()
        if code.startswith("required_tool:"):
            missing_required_tools.append(code.split(":", 1)[-1])
        elif code.startswith("forbidden_tool:"):
            forbidden_tools_triggered.append(code.split(":", 1)[-1])
        elif code.startswith("required_output:"):
            missing_output_tokens.append(code.split(":", 1)[-1])
        elif code.startswith("validator:"):
            failed_validators.append(code.split(":", 1)[-1])

    stream_payload = _as_dict(stream_observed)
    non_stream_payload = _as_dict(non_stream_observed)
    transport_errors: dict[str, str] = {}
    transport_tool_counts: dict[str, int] = {}
    for mode, payload in (("stream", stream_payload), ("non_stream", non_stream_payload)):
        if not payload:
            continue
        transport_tool_counts[mode] = len(_as_list(payload.get("tool_calls")))
        error_text = str(payload.get("error") or "").strip()
        if error_text:
            transport_errors[mode] = error_text

    return {
        "missing_required_tools": missing_required_tools,
        "forbidden_tools_triggered": forbidden_tools_triggered,
        "missing_output_tokens": missing_output_tokens,
        "failed_validators": failed_validators,
        "textual_tool_protocol_markers": _extract_textual_tool_markers(
            failed_checks=failed_checks,
            observed=observed,
        ),
        "has_native_tool_trace": bool(_as_list(observed.get("tool_calls"))),
        "transport_errors": transport_errors,
        "transport_tool_counts": transport_tool_counts,
    }


def _build_transport_observation(
    *,
    observed: Mapping[str, Any] | None,
) -> dict[str, Any]:
    payload = _as_dict(observed)
    if not payload:
        return {}
    tool_calls = _as_list(payload.get("tool_calls"))
    return {
        "tool_call_count": len(tool_calls),
        "tool_names": [
            str(_as_dict(item).get("tool") or "").strip()
            for item in tool_calls
            if str(_as_dict(item).get("tool") or "").strip()
        ],
        "tool_calls_preview": _summarize_tool_calls(tool_calls),
        "output_preview": _truncate_text(payload.get("output"), limit=240),
        "thinking_preview": _truncate_text(payload.get("thinking"), limit=180),
        "error": str(payload.get("error") or "").strip(),
        "duration_ms": _to_int(payload.get("duration_ms"), 0),
        "event_count": _to_int(payload.get("event_count"), 0),
    }


def _build_observed_trace(
    *,
    observed: Mapping[str, Any],
    raw_events: Iterable[Any],
    workspace_files: Iterable[Any],
    stream_observed: Mapping[str, Any] | None = None,
    non_stream_observed: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    tool_calls = _as_list(observed.get("tool_calls"))
    event_histogram = _event_type_histogram(raw_events)
    transport_observations: dict[str, dict[str, Any]] = {}
    stream_trace = _build_transport_observation(observed=stream_observed)
    non_stream_trace = _build_transport_observation(observed=non_stream_observed)
    if stream_trace:
        transport_observations["stream"] = stream_trace
    if non_stream_trace:
        transport_observations["non_stream"] = non_stream_trace
    return {
        "tool_call_count": len(tool_calls),
        "tool_names": [
            str(_as_dict(item).get("tool") or "").strip()
            for item in tool_calls
            if str(_as_dict(item).get("tool") or "").strip()
        ],
        "tool_calls_preview": _summarize_tool_calls(tool_calls),
        "output_preview": _truncate_text(observed.get("output"), limit=320),
        "thinking_preview": _truncate_text(observed.get("thinking"), limit=240),
        "error": str(observed.get("error") or "").strip(),
        "duration_ms": _to_int(observed.get("duration_ms"), 0),
        "event_count": _to_int(observed.get("event_count"), default=len(list(raw_events))),
        "event_type_histogram": event_histogram,
        "raw_event_samples": _summarize_raw_events(raw_events),
        "workspace_files_sample": [str(item).strip() for item in list(workspace_files)[:12] if str(item).strip()],
        "fingerprint": _as_dict(observed.get("fingerprint")),
        "transport_observations": transport_observations,
        "transport_modes": sorted(transport_observations.keys()),
    }


def _render_progress_bar(completed: int, total: int, *, width: int = 24) -> str:
    total_value = max(0, int(total))
    completed_value = max(0, min(int(completed), total_value))
    if total_value <= 0:
        return "[" + ("-" * width) + "]"
    filled = round((completed_value / total_value) * width)
    filled = max(0, min(filled, width))
    return "[" + ("#" * filled) + ("-" * (width - filled)) + "]"


def _build_progress_callback(*, enabled: bool) -> Any:
    if not enabled:
        return None

    state: dict[str, Any] = {
        "suite": "",
        "run_id": "",
        "total_cases": 0,
        "completed_cases": 0,
        "started_at": 0.0,
    }

    def emit(event: Mapping[str, Any]) -> None:
        event_payload = dict(event or {})
        event_type = str(event_payload.get("type") or "").strip().lower()
        if not event_type:
            return
        if event_type == "suite_started":
            state["suite"] = str(event_payload.get("suite") or "").strip()
            state["run_id"] = str(event_payload.get("run_id") or "").strip()
            state["total_cases"] = _to_int(event_payload.get("total_cases"), 0)
            state["completed_cases"] = 0
            state["started_at"] = time.perf_counter()
            print(
                "[agentic-eval] "
                f"progress {_render_progress_bar(0, state['total_cases'])} "
                f"start suite={state['suite']} run_id={state['run_id']} total={state['total_cases']}",
                file=sys.stderr,
                flush=True,
            )
            return
        if event_type == "case_started":
            total = _to_int(event_payload.get("total_cases"), _to_int(state.get("total_cases"), 0))
            index = _to_int(event_payload.get("index"), 0)
            bar = _render_progress_bar(max(0, index - 1), total)
            level = str(event_payload.get("level") or "").strip()
            level_prefix = f"{level} " if level else ""
            print(
                "[agentic-eval] "
                f"progress {bar} case {index}/{max(total, 1)} "
                f"{event_payload.get('case_id')} :: {level_prefix}{event_payload.get('title')}",
                file=sys.stderr,
                flush=True,
            )
            return
        if event_type == "phase_started":
            phase = str(event_payload.get("phase") or "").strip()
            if not phase:
                return
            print(
                f"[agentic-eval] phase={phase} case={event_payload.get('case_id')} title={event_payload.get('title')}",
                file=sys.stderr,
                flush=True,
            )
            return
        if event_type == "case_completed":
            total = _to_int(event_payload.get("total_cases"), _to_int(state.get("total_cases"), 0))
            index = _to_int(event_payload.get("index"), 0)
            state["completed_cases"] = max(_to_int(state.get("completed_cases"), 0), index)
            bar = _render_progress_bar(index, total)
            status = "PASS" if bool(event_payload.get("passed")) else "FAIL"
            started_at = float(state.get("started_at") or 0.0)
            elapsed_s = max(0.0, time.perf_counter() - started_at) if started_at > 0.0 else 0.0
            print(
                "[agentic-eval] "
                f"progress {bar} done {index}/{max(total, 1)} "
                f"{event_payload.get('case_id')} status={status} "
                f"score={_to_percent(event_payload.get('score'))} "
                f"duration_ms={_to_int(event_payload.get('duration_ms'), 0)} "
                f"elapsed_s={round(elapsed_s, 1)}",
                file=sys.stderr,
                flush=True,
            )
            return
        if event_type == "suite_completed":
            total = _to_int(event_payload.get("total_cases"), _to_int(state.get("total_cases"), 0))
            started_at = float(state.get("started_at") or 0.0)
            elapsed_s = max(0.0, time.perf_counter() - started_at) if started_at > 0.0 else 0.0
            print(
                "[agentic-eval] "
                f"progress {_render_progress_bar(total, total)} "
                f"complete suite={event_payload.get('suite')} "
                f"passed={event_payload.get('passed_cases')}/{total} "
                f"failed={event_payload.get('failed_cases')} "
                f"artifact={event_payload.get('artifact_path')} "
                f"elapsed_s={round(elapsed_s, 1)}",
                file=sys.stderr,
                flush=True,
            )

    return emit


def build_agentic_eval_audit_package(
    *,
    workspace: str,
    scope_role: str,
    provider_id: str,
    model: str,
    run_result: Mapping[str, Any],
    max_fixes: int,
) -> dict[str, Any]:
    details = _as_dict(run_result.get("details"))
    report = _as_dict(details.get("report"))
    report_summary = _as_dict(report.get("summary"))
    benchmark_cases = _as_list(report.get("cases"))

    total_cases = _to_int(report_summary.get("total_cases"), default=len(benchmark_cases))
    passed_cases = _to_int(
        report_summary.get("passed_cases"),
        default=sum(1 for case_payload in benchmark_cases if bool(_as_dict(case_payload.get("judge")).get("passed"))),
    )
    failed_cases = max(total_cases - passed_cases, 0)
    average_score = _to_float(
        report_summary.get("average_score"),
        default=(
            sum(_to_float(_as_dict(case_payload.get("judge")).get("score")) for case_payload in benchmark_cases)
            / total_cases
            if total_cases > 0
            else 0.0
        ),
    )
    pass_rate = (passed_cases / total_cases) if total_cases > 0 else 0.0

    failure_entries: list[dict[str, Any]] = []
    repair_index: dict[str, dict[str, Any]] = {}
    tool_histogram: Counter[str] = Counter()
    total_tool_calls = 0
    safety_violations: list[dict[str, Any]] = []
    critical_failures = 0

    for case_payload_raw in benchmark_cases:
        case_payload = _as_dict(case_payload_raw)
        case_meta = _as_dict(case_payload.get("case"))
        observed = _as_dict(case_payload.get("observed"))
        stream_observed = _as_dict(case_payload.get("stream_observed"))
        non_stream_observed = _as_dict(case_payload.get("non_stream_observed"))
        judge = _as_dict(case_payload.get("judge"))
        raw_events = _as_list(case_payload.get("raw_events"))
        workspace_files = _as_list(case_payload.get("workspace_files"))
        failed_checks = _extract_failed_checks(case_payload)

        for tool_call_raw in _as_list(observed.get("tool_calls")):
            tool_call = _as_dict(tool_call_raw)
            tool = str(tool_call.get("tool") or "").strip()
            if not tool:
                continue
            tool_histogram[tool] += 1
            total_tool_calls += 1

        for check in failed_checks:
            code = str(check.get("code") or "")
            category = str(check.get("category") or "")
            if bool(check.get("critical")):
                critical_failures += 1
            if category == "safety":
                safety_violations.append(
                    {
                        "case_id": str(case_meta.get("case_id") or ""),
                        "check_code": code,
                        "message": str(check.get("message") or ""),
                    }
                )
            hint = _repair_hint(code, category)
            bucket = repair_index.get(hint)
            if bucket is None:
                bucket = {
                    "action": hint,
                    "priority_rank": _failure_priority(check),
                    "case_ids": set(),
                    "check_codes": set(),
                }
                repair_index[hint] = bucket
            else:
                bucket["priority_rank"] = min(int(bucket["priority_rank"]), _failure_priority(check))
            bucket["case_ids"].add(str(case_meta.get("case_id") or "").strip())
            bucket["check_codes"].add(code)

        if not bool(judge.get("passed")):
            diagnosis = _build_failure_diagnosis(
                failed_checks=failed_checks,
                observed=observed,
                stream_observed=stream_observed,
                non_stream_observed=non_stream_observed,
            )
            observed_trace = _build_observed_trace(
                observed=observed,
                raw_events=raw_events,
                workspace_files=workspace_files,
                stream_observed=stream_observed,
                non_stream_observed=non_stream_observed,
            )
            failure_entries.append(
                {
                    "case_id": str(case_meta.get("case_id") or "").strip(),
                    "role": str(case_meta.get("role") or "").strip(),
                    "title": str(case_meta.get("title") or "").strip(),
                    "score_percent": _to_percent(judge.get("score")),
                    "threshold_percent": _to_percent(judge.get("threshold")),
                    "summary": str(judge.get("summary") or "").strip(),
                    "root_cause": failed_checks[0] if failed_checks else {},
                    "failed_checks": failed_checks,
                    "repair_suggestions": [_repair_hint(item["code"], item["category"]) for item in failed_checks[:3]],
                    "expected_contract": {
                        "prompt_preview": _truncate_text(case_meta.get("prompt"), limit=220),
                        "tags": [str(item).strip() for item in _as_list(case_meta.get("tags")) if str(item).strip()],
                        "judge": _as_dict(case_meta.get("judge")),
                    },
                    "observed_trace": observed_trace,
                    "diagnosis": diagnosis,
                    "evidence": {
                        "sandbox_workspace": str(case_payload.get("sandbox_workspace") or ""),
                        "raw_event_count": len(raw_events),
                        "raw_event_types": observed_trace.get("event_type_histogram"),
                        "tool_call_count": len(_as_list(observed.get("tool_calls"))),
                        "benchmark_artifact": str(details.get("artifact_path") or "").strip(),
                    },
                }
            )

    failure_entries.sort(
        key=lambda item: (
            _failure_priority(_as_dict(item.get("root_cause"))),
            str(item.get("case_id") or ""),
        )
    )

    repair_plan: list[dict[str, Any]] = []
    for entry in sorted(
        repair_index.values(),
        key=lambda item: (
            int(item.get("priority_rank", 2)),
            -len(item.get("case_ids", set())),
            str(item.get("action") or ""),
        ),
    )[: max(1, int(max_fixes))]:
        repair_plan.append(
            {
                "priority": _priority_label(int(entry.get("priority_rank", 2))),
                "action": str(entry.get("action") or ""),
                "case_ids": sorted(str(item) for item in entry.get("case_ids", set()) if str(item).strip()),
                "check_codes": sorted(str(item) for item in entry.get("check_codes", set()) if str(item).strip()),
            }
        )

    benchmark_run_id = str(report.get("test_run_id") or "").strip()
    benchmark_artifact_path = str(details.get("artifact_path") or "").strip()
    report_target = _as_dict(report.get("target"))
    status = "PASS" if bool(run_result.get("ok")) and failed_cases == 0 and total_cases > 0 else "FAIL"

    return {
        "status": status,
        "workspace": str(Path(workspace).resolve()),
        "generated_at": _utc_now(),
        "benchmark": {
            "suite": str(report.get("suite") or "agentic_benchmark"),
            "run_id": benchmark_run_id,
            "role_scope": str(scope_role or "").strip().lower() or "all",
            "provider_id": str(provider_id or "").strip() or "runtime_binding",
            "model": str(model or "").strip() or "runtime_binding",
            "transport_mode": str(report_target.get("transport_mode") or "").strip() or "stream",
        },
        "score": {
            "overall_percent": _to_percent(average_score),
            "average_score": round(average_score, 4),
            "pass_rate": round(pass_rate, 4),
            "total_cases": total_cases,
            "passed_cases": passed_cases,
            "failed_cases": failed_cases,
        },
        "failures": failure_entries,
        "tool_audit": {
            "total_calls": total_tool_calls,
            "by_tool": dict(sorted(tool_histogram.items())),
            "critical_failures": critical_failures,
            "safety_violations": safety_violations,
        },
        "repair_plan": repair_plan,
        "errors": [str(run_result.get("error") or "").strip()] if str(run_result.get("error") or "").strip() else [],
        "evidence_paths": {
            "benchmark_artifact": benchmark_artifact_path,
            "audit_package": "",
            "case_sandboxes": [
                str(_as_dict(item).get("sandbox_workspace") or "")
                for item in benchmark_cases
                if str(_as_dict(item).get("sandbox_workspace") or "").strip()
            ],
        },
    }


def _persist_audit_package(
    *,
    workspace: str,
    output_path: str,
    payload: Mapping[str, Any],
) -> dict[str, str]:
    fs = KernelFileSystem(str(Path(workspace).resolve()), LocalFileSystemAdapter())
    # Convert runtime-relative path to absolute path for workspace_write_text
    # output_path is like "runtime/llm_evaluations/<run_id>/AGENTIC_EVAL_AUDIT.json"
    # Use direct workspace-relative resolution to avoid cross-drive .polaris runtime path
    # rejection on Windows (workspace on C: vs .polaris on X:).
    absolute_output_path = str(Path(workspace).resolve() / Path(output_path))
    receipt = fs.workspace_write_text(
        absolute_output_path,
        json.dumps(dict(payload), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "relative_path": str(receipt.logical_path),
        "absolute_path": str(receipt.absolute_path),
    }


def _print_human(payload: Mapping[str, Any]) -> None:
    score = _as_dict(payload.get("score"))
    benchmark = _as_dict(payload.get("benchmark"))
    failures = _as_list(payload.get("failures"))
    tool_audit = _as_dict(payload.get("tool_audit"))
    repair_plan = _as_list(payload.get("repair_plan"))
    evidence_paths = _as_dict(payload.get("evidence_paths"))
    comparison = _as_dict(payload.get("comparison"))

    print(
        "[agentic-eval] "
        f"status={payload.get('status')} "
        f"score={score.get('overall_percent')} "
        f"passed={score.get('passed_cases')}/{score.get('total_cases')}"
    )
    print(
        "[agentic-eval] "
        f"run_id={benchmark.get('run_id')} role={benchmark.get('role_scope')} "
        f"provider={benchmark.get('provider_id')} model={benchmark.get('model')} "
        f"transport={benchmark.get('transport_mode')}"
    )
    print(
        "[agentic-eval] "
        f"tool_calls={tool_audit.get('total_calls')} critical_failures={tool_audit.get('critical_failures')}"
    )
    benchmark_artifact = str(evidence_paths.get("benchmark_artifact") or "").strip()
    if benchmark_artifact:
        print(f"[agentic-eval] benchmark_artifact={benchmark_artifact}")

    # Print rerun information if applicable
    rerun_info = _as_dict(payload.get("rerun_info"))
    if rerun_info:
        prev_path = str(rerun_info.get("previous_audit_path") or "").strip()
        prev_failed = _to_int(rerun_info.get("previous_failed_count"), 0)
        prev_passed = _to_int(rerun_info.get("previous_passed_count"), 0)
        prev_total = _to_int(rerun_info.get("previous_total_count"), 0)
        if prev_path:
            print(f"[agentic-eval] rerun_from={prev_path}")
        if prev_total > 0:
            print(
                f"[agentic-eval] previous_run passed={prev_passed}/{prev_total} failed={prev_failed} "
                f"score={rerun_info.get('previous_score')}"
            )

    if failures:
        print("[agentic-eval] top_failures:")
        for item in failures[:5]:
            root = _as_dict(_as_dict(item).get("root_cause"))
            print(f"  - {item.get('case_id')} [{root.get('category')}/{root.get('code')}] {root.get('message')}")
        print("[agentic-eval] failure_diagnostics:")
        for item in failures[:5]:
            diagnosis = _as_dict(_as_dict(item).get("diagnosis"))
            observed_trace = _as_dict(_as_dict(item).get("observed_trace"))
            evidence = _as_dict(_as_dict(item).get("evidence"))
            failed_checks = _as_list(item.get("failed_checks"))
            print(
                "  - "
                f"{item.get('case_id')} role={item.get('role')} "
                f"score={item.get('score_percent')}/{item.get('threshold_percent')} "
                f"sandbox={evidence.get('sandbox_workspace')}"
            )
            print(f"    title={item.get('title')}")
            print(
                "    observed_tools="
                + (
                    ",".join(
                        str(tool).strip() for tool in _as_list(observed_trace.get("tool_names")) if str(tool).strip()
                    )
                    or "none"
                )
                + f" tool_calls={observed_trace.get('tool_call_count')} "
                + f"raw_events={observed_trace.get('event_count')}"
            )
            event_types = _format_counter(_as_dict(observed_trace.get("event_type_histogram")))
            if event_types:
                print(f"    event_types={event_types}")
            transport_observations = _as_dict(observed_trace.get("transport_observations"))
            if transport_observations:
                for mode in ("stream", "non_stream"):
                    mode_trace = _as_dict(transport_observations.get(mode))
                    if not mode_trace:
                        continue
                    mode_tools = (
                        ",".join(
                            str(tool).strip() for tool in _as_list(mode_trace.get("tool_names")) if str(tool).strip()
                        )
                        or "none"
                    )
                    mode_error = str(mode_trace.get("error") or "").strip() or "none"
                    print(
                        "    "
                        f"{mode}_trace tools={mode_tools} "
                        f"tool_calls={mode_trace.get('tool_call_count')} "
                        f"duration_ms={mode_trace.get('duration_ms')} "
                        f"error={mode_error}"
                    )
            markers = _as_list(diagnosis.get("textual_tool_protocol_markers"))
            if markers:
                print(
                    "    textual_markers=" + ",".join(str(marker).strip() for marker in markers if str(marker).strip())
                )
            missing_tools = _as_list(diagnosis.get("missing_required_tools"))
            if missing_tools:
                print(
                    "    missing_required_tools="
                    + ",".join(str(tool).strip() for tool in missing_tools if str(tool).strip())
                )
            missing_output = _as_list(diagnosis.get("missing_output_tokens"))
            if missing_output:
                print(
                    "    missing_output_tokens="
                    + ",".join(str(token).strip() for token in missing_output if str(token).strip())
                )
            failed_validators = _as_list(diagnosis.get("failed_validators"))
            if failed_validators:
                print(
                    "    failed_validators="
                    + ",".join(str(name).strip() for name in failed_validators if str(name).strip())
                )
            transport_errors = _as_dict(diagnosis.get("transport_errors"))
            if transport_errors:
                print(
                    "    transport_errors="
                    + ", ".join(
                        f"{mode}:{_truncate_text(message, limit=120)}" for mode, message in transport_errors.items()
                    )
                )
            if failed_checks:
                print(
                    "    failed_checks="
                    + ", ".join(str(_as_dict(check).get("code") or "").strip() for check in failed_checks[:4])
                )
            output_preview = str(observed_trace.get("output_preview") or "").strip()
            if output_preview:
                print(f"    output_preview={output_preview}")

    if repair_plan:
        print("[agentic-eval] repair_plan:")
        for item in repair_plan:
            print(f"  - {item.get('priority')} {item.get('action')}")

    if bool(comparison.get("enabled")):
        current = _as_dict(comparison.get("current"))
        baseline = _as_dict(comparison.get("baseline"))
        delta = _as_dict(comparison.get("delta"))
        cases = _as_dict(comparison.get("cases"))
        print(
            "[agentic-eval] "
            f"baseline_compare trend={comparison.get('trend')} "
            f"ref={comparison.get('baseline_ref')} "
            f"delta_score={delta.get('overall_percent')} "
            f"delta_failed_cases={delta.get('failed_cases')}"
        )
        print(
            "  "
            f"current(run_id={current.get('run_id')} status={current.get('status')} score={current.get('overall_percent')}) "
            f"vs baseline(run_id={baseline.get('run_id')} status={baseline.get('status')} score={baseline.get('overall_percent')})"
        )
        new_failures = _as_list(cases.get("new_failures"))
        if new_failures:
            print("  new_failures=" + ", ".join(str(item) for item in new_failures))
        resolved_failures = _as_list(cases.get("resolved_failures"))
        if resolved_failures:
            print("  resolved_failures=" + ", ".join(str(item) for item in resolved_failures))

    audit_path = str(evidence_paths.get("audit_package") or "").strip()
    if audit_path:
        print(f"[agentic-eval] audit_package={audit_path}")


def _print_baseline_pull_human(payload: Mapping[str, Any]) -> None:
    sources = _as_list(payload.get("source_results"))
    unknown = _as_list(payload.get("unknown_sources"))
    mode_tokens: list[str] = []
    if bool(payload.get("check_only")):
        mode_tokens.append("cache_check")
    if bool(payload.get("refresh_cache")):
        mode_tokens.append("refresh")
    if bool(payload.get("use_cache")) and not bool(payload.get("refresh_cache")):
        mode_tokens.append("cache_enabled")
    mode_label = ",".join(mode_tokens) if mode_tokens else "standard"
    print(
        "[agentic-eval] "
        f"baseline_pull status={'PASS' if bool(payload.get('ok')) else 'FAIL'} "
        f"sources={len(sources)} unknown={len(unknown)} mode={mode_label}"
    )
    print(f"[agentic-eval] pull_id={payload.get('pull_id')} output={payload.get('output_root')}")
    cache_root = str(payload.get("cache_root") or "").strip()
    if cache_root:
        print(f"[agentic-eval] baseline_cache_root={cache_root}")
    if unknown:
        print("[agentic-eval] unknown_sources:")
        for token in unknown:
            print(f"  - {token}")
    for source in sources:
        row = _as_dict(source)
        print(
            "[agentic-eval] "
            f"source={row.get('source')} status={row.get('status')} "
            f"downloaded={row.get('downloaded_count')} failed={row.get('failed_count')} "
            f"cache_hits={row.get('cache_hits')} cache_misses={row.get('cache_misses')} "
            f"network_downloads={row.get('network_downloads')}"
        )
        manifest_path = str(row.get("manifest_path") or "").strip()
        if manifest_path:
            print(f"  manifest={manifest_path}")
    manifest_path = str(payload.get("manifest_path") or "").strip()
    if manifest_path:
        print(f"[agentic-eval] baseline_manifest={manifest_path}")


def run_agentic_eval_command(args: argparse.Namespace) -> int:
    # Ensure minimal kernel bindings (including audit store factory) are registered.
    # Without this, audit events cannot be persisted to disk.
    from polaris.bootstrap.assembly import assemble_core_services

    assemble_core_services(container=None, settings=None)

    workspace = str(Path(getattr(args, "workspace", ".") or ".").resolve())
    role = str(getattr(args, "role", "all") or "all").strip().lower() or "all"
    suite = _normalize_suite_name(getattr(args, "suite", "agentic_benchmark"))
    _raw_provider = str(getattr(args, "provider_id", "") or "").strip()
    _raw_model = str(getattr(args, "model", "") or "").strip()
    # Only use explicit values if they are non-placeholder; otherwise pass None
    # so the suite can auto-resolve via role binding (get_role_model).
    provider_id = _raw_provider if _raw_provider and _raw_provider not in ("runtime_binding", "") else None
    model = _raw_model if _raw_model and _raw_model not in ("runtime_binding", "") else None
    output_format = str(getattr(args, "format", "human") or "human").strip().lower() or "human"
    output_path = str(getattr(args, "output", "") or "").strip()
    max_fixes = max(1, _to_int(getattr(args, "max_fixes", 8), default=8))
    case_ids = _normalise_case_ids(getattr(args, "case_id", []))
    # Expand --levels range syntax (e.g., l1-l3) to case ID prefixes for tool_calling_matrix
    level_prefixes = _expand_level_range_to_case_ids(getattr(args, "levels", []))
    baseline_pull_sources = _normalize_tokens(getattr(args, "baseline_pull", []))
    baseline_only = bool(getattr(args, "baseline_only", False))
    baseline_output = str(
        getattr(args, "baseline_output", "runtime/llm_evaluations/baselines") or "runtime/llm_evaluations/baselines"
    ).strip()
    baseline_timeout = max(1.0, _to_float(getattr(args, "baseline_timeout", 20.0), default=20.0))
    baseline_retries = max(0, _to_int(getattr(args, "baseline_retries", 2), default=2))
    baseline_cache_check = bool(getattr(args, "baseline_cache_check", False))
    baseline_refresh = bool(getattr(args, "baseline_refresh", False))
    compare_baseline_ref = str(getattr(args, "compare_baseline", "") or "").strip()
    matrix_transport = _normalize_matrix_transport(getattr(args, "matrix_transport", "stream"))
    observable = bool(getattr(args, "observable", False))
    max_failed = max(0, _to_int(getattr(args, "max_failed", None), default=0))
    rerun_failed_ref = str(getattr(args, "rerun_failed", "") or "").strip()
    list_failed_only = bool(getattr(args, "list_failed", False))

    # Force runtime artifacts to RAMDISK X:/ for benchmark runs.
    # This must be set before ensure_minimal_kernelone_bindings() so that
    # storage-root resolution picks it up from the cache key.
    import os

    os.environ.setdefault("KERNELONE_RUNTIME_ROOT", "X:/")

    # Clear storage roots cache so the new runtime_root takes effect.
    from polaris.kernelone.storage.layout import clear_storage_roots_cache

    clear_storage_roots_cache()

    # ── Handle --rerun-failed and --list-failed ─────────────────────────────────
    rerun_failed_cases: list[str] = []
    rerun_audit_path: Path | None = None

    if rerun_failed_ref or list_failed_only:
        if not rerun_failed_ref:
            print("Error: --list-failed requires --rerun-failed to specify which run to list failures from")
            return 1
        try:
            rerun_audit_path, rerun_payload = _resolve_rerun_audit_path(workspace, rerun_failed_ref)
        except FileNotFoundError as exc:
            print(f"Error: {exc}")
            return 1

        rerun_failed_cases = sorted(_extract_failed_case_ids(rerun_payload))
        rerun_score = _as_dict(rerun_payload.get("score"))
        rerun_benchmark = _as_dict(rerun_payload.get("benchmark"))

        if list_failed_only:
            # Just list the failed cases and exit
            failed_count = _to_int(rerun_score.get("failed_cases"), 0)
            passed_count = _to_int(rerun_score.get("passed_cases"), 0)
            total_count = _to_int(rerun_score.get("total_cases"), 0)
            run_id = str(rerun_benchmark.get("run_id") or "").strip()
            print(f"[agentic-eval] run_id={run_id}")
            print(f"[agentic-eval] status={rerun_payload.get('status')} score={rerun_score.get('overall_percent')}")
            print(f"[agentic-eval] passed={passed_count}/{total_count} failed={failed_count}")
            print(f"[agentic-eval] audit_path={rerun_audit_path}")
            print(f"[agentic-eval] failed_cases ({len(rerun_failed_cases)}):")
            for case_id in rerun_failed_cases:
                print(f"  - {case_id}")
            return 0

        # Override case_ids with the failed cases from the previous run
        case_ids = rerun_failed_cases
        print(
            f"[agentic-eval] --rerun-failed: restoring {len(rerun_failed_cases)} failed cases from {rerun_audit_path}"
        )

    if baseline_only and compare_baseline_ref:
        print("Error: --baseline-only cannot be combined with --compare-baseline")
        return 1
    if baseline_only and not baseline_pull_sources:
        print("Error: --baseline-only requires at least one --baseline-pull source")
        return 1
    if baseline_cache_check and baseline_refresh:
        print("Error: --baseline-cache-check cannot be combined with --baseline-refresh")
        return 1
    if baseline_cache_check and not baseline_pull_sources:
        print("Error: --baseline-cache-check requires at least one --baseline-pull source")
        return 1

    if baseline_pull_sources:
        sources_catalog = list_baseline_library_sources()
        if "all" not in baseline_pull_sources:
            valid_sources = set(sources_catalog.keys())
            invalid_sources = [item for item in baseline_pull_sources if item not in valid_sources]
            if invalid_sources:
                message = {
                    "ok": False,
                    "error": "invalid_baseline_sources",
                    "invalid_sources": invalid_sources,
                    "available_sources": sorted(valid_sources),
                }
                if output_format == "json":
                    print(json.dumps(message, ensure_ascii=False, indent=2))
                else:
                    print("[agentic-eval] baseline_pull status=FAIL invalid sources")
                    print("  available_sources=" + ", ".join(sorted(valid_sources)))
                    for token in invalid_sources:
                        print(f"  invalid_source={token}")
                return 1

        baseline_payload = pull_baseline_library(
            workspace=workspace,
            sources=baseline_pull_sources,
            output_root=baseline_output,
            timeout_seconds=baseline_timeout,
            max_retries=baseline_retries,
            use_cache=True,
            check_only=baseline_cache_check,
            refresh_cache=baseline_refresh,
        )
        if output_format == "json":
            print(json.dumps(baseline_payload, ensure_ascii=False, indent=2))
        else:
            _print_baseline_pull_human(baseline_payload)
        if baseline_only:
            return 0 if bool(baseline_payload.get("ok")) else 1

    # ── Pre-flight probe ─────────────────────────────────────────────────────
    if bool(getattr(args, "probe", False)):
        probe_timeout = max(5.0, float(getattr(args, "probe_timeout", 30.0) or 30.0))
        probe_roles: tuple[str, ...] | None = None
        # If role is a single specific role (not "all"), probe only that role
        if role and role != "all":
            probe_roles = (role,)
        probe_result = asyncio.run(_run_probe_async(workspace, probe_roles, probe_timeout))
        if output_format == "json":
            print(json.dumps(probe_result, ensure_ascii=False, indent=2))
        else:
            _print_probe_human(probe_result)
        if not probe_result.get("ok", False):
            failed = ", ".join(probe_result.get("failed_roles", []))
            print(f"[agentic-eval] probe FAILED — cannot run benchmark with unreachable roles: {failed}")
            return 1
        print("[agentic-eval] probe PASSED — all roles accessible, proceeding with benchmark")

    baseline_compare_path: Path | None = None
    baseline_compare_payload: dict[str, Any] | None = None
    if compare_baseline_ref:
        try:
            baseline_compare_path = _resolve_baseline_audit_path(workspace, compare_baseline_ref)
            baseline_compare_payload = _read_json_file(baseline_compare_path)
        except (RuntimeError, ValueError) as exc:
            print(f"Error: invalid --compare-baseline reference ({exc})")
            return 1

    options: dict[str, Any] = {"provider_id": provider_id}
    if case_ids:
        options["benchmark_case_ids"] = case_ids
        options["matrix_case_ids"] = case_ids
    if suite == "tool_calling_matrix":
        options["matrix_transport"] = matrix_transport
        options["observable"] = observable
        # Add level prefixes for range filtering (e.g., l1-l3 -> ["l1_", "l2_", "l3_"])
        if level_prefixes:
            existing = options.get("matrix_case_ids", [])
            options["matrix_case_ids"] = list(existing) + level_prefixes
    if max_failed > 0:
        options["max_failed"] = max_failed

    context = {"provider_id": provider_id}
    progress_callback = _build_progress_callback(enabled=output_format == "human")
    if progress_callback is not None:
        context["progress_callback"] = progress_callback

    # Get mode from args - default handled by argparse, but safe fallback
    mode = str(getattr(args, "mode", "agentic") or "agentic").strip().lower() or "agentic"

    try:
        if suite == "tool_calling_matrix":
            # tool_calling_matrix uses its own runner (ignores mode)
            suite_runner = _suite_runners()[suite]
            run_result = asyncio.run(
                suite_runner(
                    {},
                    model,
                    role,
                    workspace=workspace,
                    context=context,
                    options=options,
                )
            )
        else:
            # agentic_benchmark suite - route by mode
            run_result = _run_benchmark_by_mode(
                mode=mode,
                provider_cfg={},
                model=model,
                role=role,
                workspace=workspace,
                context=context,
                options=options,
            )
    except (RuntimeError, ValueError) as exc:
        run_result = {"ok": False, "error": str(exc), "details": {}}

    package = build_agentic_eval_audit_package(
        workspace=workspace,
        scope_role=role,
        provider_id=provider_id or "",  # type: ignore[arg-type]
        model=model or "",  # type: ignore[arg-type]
        run_result=_as_dict(run_result),
        max_fixes=max_fixes,
    )

    if baseline_compare_payload is not None and baseline_compare_path is not None:
        package["comparison"] = _build_baseline_comparison(
            current_payload=package,
            baseline_payload=baseline_compare_payload,
            baseline_path=baseline_compare_path,
            baseline_ref=compare_baseline_ref,
        )

    run_id = str(_as_dict(package.get("benchmark")).get("run_id") or "").strip()
    resolved_output_path = output_path or _default_output_path(run_id)
    try:
        output_info = _persist_audit_package(
            workspace=workspace,
            output_path=resolved_output_path,
            payload=package,
        )
    except (RuntimeError, ValueError) as exc:
        print(f"Error: failed to persist audit package ({exc})")
        return 1

    package["evidence_paths"]["audit_package"] = output_info["absolute_path"]

    # Add rerun info if this is a rerun run
    if rerun_audit_path is not None and rerun_payload is not None:
        prev_score = _as_dict(rerun_payload.get("score"))
        package["rerun_info"] = {
            "is_rerun": True,
            "previous_audit_path": str(rerun_audit_path),
            "previous_run_id": str(_as_dict(rerun_payload.get("benchmark")).get("run_id") or "").strip(),
            "previous_score": str(prev_score.get("overall_percent") or "").strip(),
            "previous_passed_count": _to_int(prev_score.get("passed_cases"), 0),
            "previous_failed_count": _to_int(prev_score.get("failed_cases"), 0),
            "previous_total_count": _to_int(prev_score.get("total_cases"), 0),
            "rerun_case_count": len(rerun_failed_cases),
        }

    # Persist again with final self-reference path.
    try:
        _persist_audit_package(
            workspace=workspace,
            output_path=resolved_output_path,
            payload=package,
        )
    except (RuntimeError, ValueError) as exc:
        print(f"Error: failed to finalize audit package ({exc})")
        return 1

    if output_format == "json":
        print(json.dumps(package, ensure_ascii=False, indent=2))
    else:
        _print_human(package)

    return 0 if str(package.get("status") or "").strip().upper() == "PASS" else 1


__all__ = [
    "build_agentic_eval_audit_package",
    "run_agentic_eval_command",
]
