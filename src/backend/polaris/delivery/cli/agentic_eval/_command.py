"""Top-level ``run_agentic_eval_command`` orchestrator for the CLI.

Wires together argument parsing, the pre-flight probe, baseline pull /
compare, suite/mode dispatch, audit-package building, persistence and
rendering into a single command entry point.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from polaris.cells.llm.evaluation.public.service import (
    list_baseline_library_sources,
    pull_baseline_library,
)

from ._audit_package import _default_output_path, build_agentic_eval_audit_package
from ._baseline import (
    _build_baseline_comparison,
    _extract_failed_case_ids,
    _read_json_file,
    _resolve_baseline_audit_path,
    _resolve_rerun_audit_path,
)
from ._coerce import (
    _as_dict,
    _normalise_case_ids,
    _normalize_matrix_transport,
    _normalize_tokens,
    _to_float,
    _to_int,
)
from ._persistence import _persist_audit_package
from ._probe import _print_probe_human, _run_probe_async
from ._render import (
    _build_progress_callback,
    _print_baseline_pull_human,
    _print_human,
    _report_context_projection_matrix,
    _report_projection_adaptive_matrix,
    _report_speculation_matrix,
)
from ._routing import (
    _expand_level_range_to_case_ids,
    _normalize_suite_name,
    _run_benchmark_by_mode,
    _suite_runners,
)

if TYPE_CHECKING:
    import argparse

__all__ = [
    "run_agentic_eval_command",
]


def run_agentic_eval_command(args: argparse.Namespace) -> int:
    # The tool-calling matrix measures the MODEL's own tool-call sequence. Speculative
    # execution is a latency optimization that transforms the observed calls — it
    # dedups/adopts repeated reads (failing required_tool_call_count) and aborts
    # post-hoc recovered writes — corrupting that measurement. Disable it for the
    # matrix BEFORE kernel bootstrap reads the flag; respect an explicit user override.
    _eval_suite = _normalize_suite_name(getattr(args, "suite", "agentic_benchmark"))
    _prev_speculative = os.environ.get("ENABLE_SPECULATIVE_EXECUTION")
    _disable_speculative = _eval_suite == "tool_calling_matrix" and _prev_speculative is None
    if _disable_speculative:
        os.environ["ENABLE_SPECULATIVE_EXECUTION"] = "0"

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
        except (RuntimeError, ValueError, FileNotFoundError, OSError) as exc:
            print(f"Error: invalid --compare-baseline reference ({exc})")
            return 1

    options: dict[str, Any] = {"provider_id": provider_id}
    if case_ids:
        options["benchmark_case_ids"] = case_ids
        options["matrix_case_ids"] = case_ids
    if suite in (
        "tool_calling_matrix",
        "speculation_matrix",
        "context_projection_matrix",
        "projection_adaptive_matrix",
    ):
        options["matrix_transport"] = matrix_transport
        options["observable"] = observable
        # Add level prefixes for range filtering (e.g., l1-l3 -> ["l1_", "l2_", "l3_"])
        if level_prefixes:
            existing = options.get("matrix_case_ids", [])
            options["matrix_case_ids"] = list(existing) + level_prefixes
    if max_failed > 0:
        options["max_failed"] = max_failed
    _repeats = _to_int(getattr(args, "repeats", None), default=1)
    if _repeats > 1:
        options["repeats"] = _repeats

    context = {"provider_id": provider_id}
    progress_callback = _build_progress_callback(enabled=output_format == "human")
    if progress_callback is not None:
        context["progress_callback"] = progress_callback

    # Get mode from args - default handled by argparse, but safe fallback
    mode = str(getattr(args, "mode", "agentic") or "agentic").strip().lower() or "agentic"

    try:
        if suite in (
            "tool_calling_matrix",
            "speculation_matrix",
            "context_projection_matrix",
            "projection_adaptive_matrix",
            "scout_matrix",
        ):
            # matrix-style suites use their own runner (ignores mode)
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
    finally:
        if _disable_speculative:
            os.environ.pop("ENABLE_SPECULATIVE_EXECUTION", None)

    # speculation_matrix 是差分评测，结果结构与 agentic 审计格式不同，单独呈现。
    if suite == "speculation_matrix":
        return _report_speculation_matrix(
            _as_dict(run_result),
            workspace=workspace,
            output_format=output_format,
        )

    # context_projection_matrix 是确定性 ProjectionEngine 矩阵，单独呈现。
    if suite == "context_projection_matrix":
        return _report_context_projection_matrix(_as_dict(run_result), output_format=output_format)

    # projection_adaptive_matrix 是自适应排序 A/B 评测，单独呈现。
    if suite == "projection_adaptive_matrix":
        return _report_projection_adaptive_matrix(_as_dict(run_result), output_format=output_format)

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
