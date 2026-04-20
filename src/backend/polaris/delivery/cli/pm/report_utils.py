"""Report utilities for PM orchestration.

This module contains report generation functions extracted from
orchestration_engine.py.
"""

from __future__ import annotations

import json
from typing import Any

from polaris.infrastructure.compat.io_utils import ensure_parent_dir

# ============ Report Generation ============


def append_pm_report(path: str, content: str) -> None:
    """Append content to PM report path."""
    if not path:
        return
    ensure_parent_dir(path)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(content)
        if not content.endswith("\n"):
            handle.write("\n")


def format_director_summary_for_report(engine_dispatch: Any) -> str:
    """Format Director dispatch summary for PM report."""
    if not isinstance(engine_dispatch, dict):
        return "skipped"
    summary = engine_dispatch.get("summary")
    if not isinstance(summary, dict):
        return json.dumps({}, ensure_ascii=False)
    if summary.get("dispatch_anomaly") == "empty_dispatch_with_active_tasks":
        expected = int(summary.get("expected_dispatchable") or 0)
        return (
            "anomaly(empty_dispatch_with_active_tasks): "
            f"expected_dispatchable={expected}; summary={json.dumps(summary, ensure_ascii=False)}"
        )
    return json.dumps(summary, ensure_ascii=False)


def format_chief_engineer_for_report(payload: Any) -> str:
    """Format ChiefEngineer result for PM report."""
    if not isinstance(payload, dict):
        return "skipped"
    mode = str(payload.get("mode") or "").strip() or "auto"
    if payload.get("ran") is not True:
        reason = str(payload.get("reason") or "").strip() or "skipped"
        return f"skipped(mode={mode}, reason={reason})"
    verdict = "OK" if payload.get("hard_failure") is not True else "FAIL"
    summary = str(payload.get("summary") or "").strip() or str(payload.get("reason") or "").strip()
    return f"{verdict}(mode={mode}): {summary}"


def format_integration_qa_for_report(payload: Any) -> str:
    """Format Integration QA result for PM report."""
    if not isinstance(payload, dict):
        return "skipped"
    if payload.get("ran") is not True:
        return f"skipped(reason={str(payload.get('reason') or '').strip()})"
    verdict = "PASS" if payload.get("passed") is True else "FAIL"
    summary = str(payload.get("summary") or "").strip()
    if not summary:
        summary = str(payload.get("reason") or "").strip()
    return f"{verdict}: {summary}"


def build_pm_report_header(
    timestamp: str,
    iteration: int,
    run_id: str,
    backend: str,
) -> str:
    """Build PM report header for iteration start."""
    return (
        f"\n\n## {timestamp} (iteration {iteration}) - start\nRun ID: {run_id}\nBackend: {backend}\nStatus: running\n"
    )


def build_pm_report_complete(
    timestamp: str,
    iteration: int,
    exit_code: int,
    task_count: int,
    chief_engineer_result: dict[str, Any] | None,
    engine_dispatch: dict[str, Any] | None,
    integration_qa_result: dict[str, Any] | None,
) -> str:
    """Build PM report content for iteration complete."""
    lines = [
        f"## {timestamp} (iteration {iteration}) - complete",
        f"Exit code: {exit_code}",
        f"Task count: {task_count}",
    ]

    if isinstance(chief_engineer_result, dict):
        lines.append(f"ChiefEngineer: {format_chief_engineer_for_report(chief_engineer_result)}")

    if isinstance(engine_dispatch, dict):
        lines.append(f"Director summary: {format_director_summary_for_report(engine_dispatch)}")
    else:
        lines.append("Director summary: skipped")

    if isinstance(integration_qa_result, dict):
        lines.append(f"Integration QA: {format_integration_qa_for_report(integration_qa_result)}")

    return "\n".join(lines) + "\n"


__all__ = [
    "append_pm_report",
    "build_pm_report_complete",
    "build_pm_report_header",
    "format_chief_engineer_for_report",
    "format_director_summary_for_report",
    "format_integration_qa_for_report",
]
