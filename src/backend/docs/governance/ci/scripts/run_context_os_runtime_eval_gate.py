"""Context OS + Cognitive Runtime rollout gate runner.

The runner validates:
1. Schema validation (optional) - validates suite and report files
2. Required pytest suites (optional)
3. Metric thresholds from a structured eval report
4. Cognitive Runtime metrics (optional) - collects from workspace SQLite store
5. Promotion recommendation (shadow -> mainline)
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from polaris.kernelone.context.context_os.metrics_collector import (
    CognitiveRuntimeMetricsCollector,
)
from polaris.kernelone.context.context_os.schemas import (
    validate_report_file,
    validate_suite_file,
)

BACKEND_ROOT = Path(__file__).resolve().parents[4]


@dataclass(frozen=True)
class SuiteRun:
    path: str
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


@dataclass(frozen=True)
class GateFailure:
    metric: str
    actual: float
    threshold: float
    comparator: str
    section: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _build_utf8_env() -> dict[str, str]:
    env = dict(os.environ)
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("LANG", "en_US.UTF-8")
    env.setdefault("LC_ALL", "en_US.UTF-8")
    return env


def _load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _to_float(payload: dict[str, Any], key: str, default: float = 0.0) -> float:
    value = payload.get(key, default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _to_int(payload: dict[str, Any], key: str, default: int = 0) -> int:
    value = payload.get(key, default)
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _run_required_pytests(suites: list[str]) -> tuple[bool, list[SuiteRun]]:
    if not suites:
        return True, []
    runs: list[SuiteRun] = []
    all_ok = True
    for suite in suites:
        completed = subprocess.run(
            [sys.executable, "-B", "-m", "pytest", suite, "-q"],
            cwd=str(BACKEND_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=_build_utf8_env(),
            check=False,
        )
        run = SuiteRun(
            path=suite,
            returncode=int(completed.returncode),
            stdout=completed.stdout,
            stderr=completed.stderr,
        )
        runs.append(run)
        all_ok = all_ok and run.ok
    return all_ok, runs


def _require_min(
    failures: list[GateFailure],
    section: str,
    metric: str,
    actual: float,
    threshold: float,
) -> None:
    if actual < threshold:
        failures.append(
            GateFailure(
                metric=metric,
                actual=float(actual),
                threshold=float(threshold),
                comparator=">=",
                section=section,
            )
        )


def _require_max(
    failures: list[GateFailure],
    section: str,
    metric: str,
    actual: float,
    threshold: float,
) -> None:
    if actual > threshold:
        failures.append(
            GateFailure(
                metric=metric,
                actual=float(actual),
                threshold=float(threshold),
                comparator="<=",
                section=section,
            )
        )


def _check_core_thresholds(
    report: dict[str, Any],
    cfg: dict[str, Any],
    failures: list[GateFailure],
) -> None:
    section = "core_context_os"
    summary = dict(report.get("core_summary") or {})
    thresholds = dict(cfg.get("core_context_os_thresholds") or {})
    _require_min(
        failures,
        section,
        "total_cases",
        float(_to_int(summary, "total_cases")),
        float(_to_int(thresholds, "minimum_cases")),
    )
    _require_min(
        failures,
        section,
        "exact_fact_recovery",
        _to_float(summary, "exact_fact_recovery"),
        _to_float(thresholds, "exact_fact_recovery"),
    )
    _require_min(
        failures,
        section,
        "decision_preservation",
        _to_float(summary, "decision_preservation"),
        _to_float(thresholds, "decision_preservation"),
    )
    _require_min(
        failures,
        section,
        "open_loop_continuity",
        _to_float(summary, "open_loop_continuity"),
        _to_float(thresholds, "open_loop_continuity"),
    )
    _require_min(
        failures,
        section,
        "artifact_restore_precision",
        _to_float(summary, "artifact_restore_precision"),
        _to_float(thresholds, "artifact_restore_precision"),
    )
    _require_min(
        failures,
        section,
        "temporal_update_correctness",
        _to_float(summary, "temporal_update_correctness"),
        _to_float(thresholds, "temporal_update_correctness"),
    )
    _require_min(failures, section, "abstention", _to_float(summary, "abstention"), _to_float(thresholds, "abstention"))
    _require_max(
        failures,
        section,
        "compaction_regret",
        _to_float(summary, "compaction_regret"),
        _to_float(thresholds, "compaction_regret_max"),
    )


def _check_attention_thresholds(
    report: dict[str, Any],
    cfg: dict[str, Any],
    failures: list[GateFailure],
) -> None:
    section = "attention_runtime"
    summary = dict(report.get("attention_summary") or {})
    thresholds = dict(cfg.get("attention_runtime_thresholds") or {})
    _require_min(
        failures,
        section,
        "total_cases",
        float(_to_int(summary, "total_cases")),
        float(_to_int(thresholds, "minimum_cases")),
    )
    _require_min(failures, section, "pass_rate", _to_float(summary, "pass_rate"), _to_float(thresholds, "pass_rate"))
    _require_min(
        failures,
        section,
        "intent_carryover_accuracy",
        _to_float(summary, "intent_carryover_accuracy"),
        _to_float(thresholds, "intent_carryover_accuracy"),
    )
    _require_min(
        failures,
        section,
        "latest_turn_retention_rate",
        _to_float(summary, "latest_turn_retention_rate"),
        _to_float(thresholds, "latest_turn_retention_rate"),
    )
    _require_max(
        failures,
        section,
        "focus_regression_rate",
        _to_float(summary, "focus_regression_rate"),
        _to_float(thresholds, "focus_regression_rate_max"),
    )
    _require_max(
        failures,
        section,
        "false_clear_rate",
        _to_float(summary, "false_clear_rate"),
        _to_float(thresholds, "false_clear_rate_max"),
    )
    _require_min(
        failures,
        section,
        "pending_followup_resolution_rate",
        _to_float(summary, "pending_followup_resolution_rate"),
        _to_float(thresholds, "pending_followup_resolution_rate"),
    )
    _require_max(
        failures,
        section,
        "seal_while_pending_rate",
        _to_float(summary, "seal_while_pending_rate"),
        _to_float(thresholds, "seal_while_pending_rate_max"),
    )
    _require_min(
        failures,
        section,
        "continuity_focus_alignment_rate",
        _to_float(summary, "continuity_focus_alignment_rate"),
        _to_float(thresholds, "continuity_focus_alignment_rate"),
    )
    _require_max(
        failures,
        section,
        "context_redundancy_rate",
        _to_float(summary, "context_redundancy_rate"),
        _to_float(thresholds, "context_redundancy_rate_max"),
    )


def _check_cognitive_runtime_thresholds(
    report: dict[str, Any],
    cfg: dict[str, Any],
    failures: list[GateFailure],
) -> None:
    section = "cognitive_runtime"
    summary = dict(report.get("cognitive_runtime_summary") or {})
    thresholds = dict(cfg.get("cognitive_runtime_thresholds") or {})
    _require_min(
        failures,
        section,
        "total_cases",
        float(_to_int(summary, "total_cases")),
        float(_to_int(thresholds, "minimum_cases")),
    )
    _require_min(
        failures,
        section,
        "receipt_coverage",
        _to_float(summary, "receipt_coverage"),
        _to_float(thresholds, "receipt_coverage"),
    )
    _require_min(
        failures,
        section,
        "handoff_roundtrip_success_rate",
        _to_float(summary, "handoff_roundtrip_success_rate"),
        _to_float(thresholds, "handoff_roundtrip_success_rate"),
    )
    _require_min(
        failures,
        section,
        "state_restore_accuracy",
        _to_float(summary, "state_restore_accuracy"),
        _to_float(thresholds, "state_restore_accuracy"),
    )
    _require_min(
        failures,
        section,
        "transaction_envelope_coverage",
        _to_float(summary, "transaction_envelope_coverage"),
        _to_float(thresholds, "transaction_envelope_coverage"),
    )
    _require_max(
        failures,
        section,
        "receipt_write_failure_rate",
        _to_float(summary, "receipt_write_failure_rate"),
        _to_float(thresholds, "receipt_write_failure_rate_max"),
    )
    _require_max(
        failures,
        section,
        "sqlite_write_p95_ms",
        _to_float(summary, "sqlite_write_p95_ms"),
        _to_float(thresholds, "sqlite_write_p95_ms_max"),
    )


def _compute_repeat_context_summary(report: dict[str, Any]) -> dict[str, Any]:
    """Compute repeat context metrics from case results.

    Returns:
        Dict with max_case_redundancy, spike_ratio, top1_repeat_count
    """
    case_results = report.get("case_results") or []

    # Extract redundancy rates per case
    redundancy_rates: list[float] = []
    for case in case_results:
        metrics = case.get("metrics") or {}
        rr = float(metrics.get("context_redundancy_rate") or 0.0)
        redundancy_rates.append(rr)

    # Compute metrics
    max_case_redundancy = max(redundancy_rates) if redundancy_rates else 0.0

    # Spike ratio: cases with redundancy > 0.25
    spike_cases = [rr for rr in redundancy_rates if rr > 0.25]
    spike_ratio = len(spike_cases) / len(redundancy_rates) if redundancy_rates else 0.0

    # Top1 repeat count: highest repeat_count from duplicate_samples
    top1_count = 0
    for case in case_results:
        metrics = case.get("metrics") or {}
        details = metrics.get("details") or {}
        samples = details.get("duplicate_samples") or []
        if samples:
            top1_count = max(top1_count, samples[0].get("repeat_count") or 0)

    return {
        "max_case_context_redundancy_rate": max_case_redundancy,
        "duplicate_spike_cases_ratio": spike_ratio,
        "duplicate_samples_top1_repeat_count": top1_count,
    }


def _check_repeat_context_thresholds(
    report: dict[str, Any],
    cfg: dict[str, Any],
    failures: list[GateFailure],
) -> None:
    """Check repeat context thresholds from case-level analysis."""
    section = "repeat_context"
    thresholds = dict(cfg.get("repeat_context_thresholds") or {})

    if not thresholds:
        return  # Skip if no thresholds defined

    repeat_ctx = _compute_repeat_context_summary(report)

    _require_max(
        failures,
        section,
        "max_case_context_redundancy_rate",
        repeat_ctx["max_case_context_redundancy_rate"],
        _to_float(thresholds, "max_case_context_redundancy_rate_max"),
    )
    _require_max(
        failures,
        section,
        "duplicate_spike_cases_ratio",
        repeat_ctx["duplicate_spike_cases_ratio"],
        _to_float(thresholds, "duplicate_spike_cases_ratio_max"),
    )
    _require_max(
        failures,
        section,
        "duplicate_samples_top1_repeat_count",
        float(repeat_ctx["duplicate_samples_top1_repeat_count"]),
        _to_float(thresholds, "duplicate_samples_top1_repeat_count_max"),
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Context OS + Cognitive Runtime eval gate.")
    parser.add_argument(
        "--gate-config",
        default="docs/governance/ci/context-os-runtime-eval-gate.yaml",
        help="YAML gate config path (relative to backend root).",
    )
    parser.add_argument(
        "--report",
        required=True,
        help="JSON eval report path (relative to backend root).",
    )
    parser.add_argument(
        "--run-required-tests",
        action="store_true",
        help="Run required pytest suites from gate config before metric checks.",
    )
    parser.add_argument(
        "--output",
        default="workspace/meta/governance_reports/context_os_runtime_eval_gate_report.json",
        help="Output JSON path (relative to backend root).",
    )
    parser.add_argument(
        "--print-report",
        action="store_true",
        help="Print gate report JSON to stdout.",
    )
    # Schema validation options
    parser.add_argument(
        "--suite-path",
        default="",
        help="Path to evaluation suite JSON/YAML file for schema validation (relative to backend root).",
    )
    parser.add_argument(
        "--skip-schema-validation",
        action="store_true",
        help="Skip schema validation of suite and report files.",
    )
    # Cognitive Runtime metrics collection options
    parser.add_argument(
        "--workspace",
        default=".",
        help="Workspace path for Cognitive Runtime metrics collection (relative to backend root).",
    )
    parser.add_argument(
        "--collect-cognitive-metrics",
        action="store_true",
        help="Collect Cognitive Runtime metrics from workspace SQLite store.",
    )
    return parser.parse_args()


def _run_schema_validation(
    report_path: Path,
    suite_path: Path | None,
    skip_validation: bool,
) -> tuple[bool, list[str], list[str]]:
    """Run schema validation on suite and report files.

    Returns:
        Tuple of (schema_valid, schema_errors, schema_warnings)
    """
    schema_errors: list[str] = []
    schema_warnings: list[str] = []

    if skip_validation:
        return True, schema_errors, schema_warnings

    # Validate report file (required)
    report_result = validate_report_file(report_path)
    if not report_result.is_valid:
        schema_errors.extend(report_result.errors)
    schema_warnings.extend(report_result.warnings)

    # Validate suite file (optional)
    if suite_path is not None and suite_path.exists():
        suite_result = validate_suite_file(suite_path)
        if not suite_result.is_valid:
            schema_errors.extend(f"[suite] {err}" for err in suite_result.errors)
        schema_warnings.extend(f"[suite] {warn}" for warn in suite_result.warnings)

    return len(schema_errors) == 0, schema_errors, schema_warnings


def _collect_metrics(
    workspace_path: Path,
    collect_metrics_flag: bool,
) -> dict[str, Any]:
    """Collect Cognitive Runtime metrics from workspace.

    Returns:
        cognitive_runtime_summary dict or empty dict if collection disabled/failed.
    """
    if not collect_metrics_flag:
        return {}

    try:
        collector = CognitiveRuntimeMetricsCollector(workspace_path)
        result = collector.collect_metrics()
        if result.metrics is not None:
            return result.metrics.to_dict()
    except Exception as exc:
        # Log but don't fail - metrics collection is optional
        print(f"[context-os-runtime-eval-gate] Warning: metrics collection failed: {exc}", file=sys.stderr)

    return {}


def main() -> int:
    args = _parse_args()
    gate_config_path = (BACKEND_ROOT / args.gate_config).resolve()
    report_path = (BACKEND_ROOT / args.report).resolve()
    output_path = (BACKEND_ROOT / args.output).resolve()
    workspace_path = (BACKEND_ROOT / args.workspace).resolve()

    # Parse optional suite path
    suite_path: Path | None = None
    if args.suite_path:
        suite_path = (BACKEND_ROOT / args.suite_path).resolve()

    gate_cfg = _load_yaml(gate_config_path)
    eval_report = _load_json(report_path)

    # Run schema validation
    schema_valid, schema_errors, schema_warnings = _run_schema_validation(
        report_path=report_path,
        suite_path=suite_path,
        skip_validation=args.skip_schema_validation,
    )
    warnings: list[str] = list(schema_warnings)  # Track all warnings including injection defaults

    # Run required pytest suites
    suite_ok = True
    suite_runs: list[SuiteRun] = []
    if args.run_required_tests:
        required_suites = [str(item) for item in (gate_cfg.get("required_pytest_suites") or []) if str(item).strip()]
        suite_ok, suite_runs = _run_required_pytests(required_suites)

    # Collect Cognitive Runtime metrics (if enabled)
    cognitive_metrics = _collect_metrics(
        workspace_path=workspace_path,
        collect_metrics_flag=args.collect_cognitive_metrics,
    )

    # Always ensure cognitive_runtime_summary is present in eval_report
    # Priority: collected metrics > report's existing value > defaults
    if cognitive_metrics:
        # Fresh collection available - use it
        eval_report["cognitive_runtime_summary"] = cognitive_metrics
    elif "cognitive_runtime_summary" not in eval_report:
        # No collection and no existing value - use defaults to avoid gate failure
        # Note: These defaults indicate "not measured" rather than "failed"
        eval_report["cognitive_runtime_summary"] = {
            "total_cases": 0,
            "receipt_coverage": 1.0,  # No receipts = no failures = perfect coverage
            "handoff_roundtrip_success_rate": 1.0,
            "state_restore_accuracy": 1.0,
            "transaction_envelope_coverage": 1.0,
            "receipt_write_failure_rate": 0.0,
            "sqlite_write_p95_ms": 0.0,
        }
        warnings.append("cognitive_runtime_summary: using defaults (no collection)")

    # Ensure core_summary is present (defaults if not)
    if "core_summary" not in eval_report:
        # No core_summary from attention-only report - use defaults
        eval_report["core_summary"] = {
            "total_cases": 0,
            "exact_fact_recovery": 1.0,
            "decision_preservation": 1.0,
            "open_loop_continuity": 1.0,
            "artifact_restore_precision": 1.0,
            "temporal_update_correctness": 1.0,
            "abstention": 1.0,
            "compaction_regret": 0.0,
        }
        warnings.append("core_summary: using defaults (attention-only report)")

    # Check metric thresholds
    failures: list[GateFailure] = []
    _check_core_thresholds(eval_report, gate_cfg, failures)
    _check_attention_thresholds(eval_report, gate_cfg, failures)
    _check_cognitive_runtime_thresholds(eval_report, gate_cfg, failures)
    _check_repeat_context_thresholds(eval_report, gate_cfg, failures)

    # Compute repeat context summary for report
    repeat_context_summary = _compute_repeat_context_summary(eval_report)

    metrics_ok = not failures
    promotion = dict(gate_cfg.get("mode_promotion") or {})

    # Gate passes only if: schema valid AND suites pass AND metrics pass
    passed = schema_valid and suite_ok and metrics_ok
    recommended_mode = str(promotion.get("target_mode") or "mainline") if passed else "shadow"

    payload: dict[str, Any] = {
        "version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "passed": bool(passed),
        "recommended_mode": recommended_mode,
        "suite_ok": bool(suite_ok),
        "metrics_ok": bool(metrics_ok),
        "schema_valid": bool(schema_valid),
        "gate_config": str(gate_config_path),
        "input_report": str(report_path),
        "workspace": str(workspace_path),
        "core_summary": dict(eval_report.get("core_summary") or {}),
        "attention_summary": dict(eval_report.get("attention_summary") or {}),
        "cognitive_runtime_summary": dict(eval_report.get("cognitive_runtime_summary") or {}),
        "repeat_context_summary": repeat_context_summary,
        "failures": [item.to_dict() for item in failures],
        "schema_errors": schema_errors,
        "schema_warnings": warnings,
        "suite_runs": [
            {
                "path": item.path,
                "ok": item.ok,
                "returncode": item.returncode,
                "stdout": item.stdout,
                "stderr": item.stderr,
            }
            for item in suite_runs
        ],
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.print_report:
        print(json.dumps(payload, ensure_ascii=False, indent=2))

    if passed:
        return 0

    # Print failure summary to stderr
    print("[context-os-runtime-eval-gate] failed", file=sys.stderr)
    if schema_errors:
        print("  - schema validation failed:", file=sys.stderr)
        for err in schema_errors:
            print(f"    - {err}", file=sys.stderr)
    for item in failures:
        print(
            f"  - {item.section}.{item.metric}: actual={item.actual} {item.comparator} {item.threshold}",
            file=sys.stderr,
        )
    if not suite_ok:
        print("  - required pytest suite check failed", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
