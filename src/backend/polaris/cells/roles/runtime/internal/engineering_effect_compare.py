"""Compare ContextOS effect reports across runs of the same workspace."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


def load_effect_reports(workspace: Path, *, limit: int = 10) -> list[dict[str, Any]]:
    """Load effect reports newest-first from the workspace runtime root."""

    root = workspace / ".polaris" / "runtime" / "contextos" / "effect-reports"
    if not root.is_dir():
        return []
    reports: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.json"), key=lambda item: (item.stat().st_mtime, item.name), reverse=True)[
        : max(1, limit)
    ]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict) and payload.get("schema_version") == "contextos.effect_report.v1":
            reports.append(payload)
    return reports


def compare_effect_reports(reports: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Build a compact comparison table from two or more effect reports."""

    rows = [
        {
            "session_id": report.get("session_id"),
            "status": report.get("status"),
            "verification": report.get("verification"),
            "turns": report.get("turns"),
            "fold_rate": report.get("fold_rate"),
            "cards_folded": report.get("cards_folded"),
            "memory_injected_requests": report.get("memory_injected_requests"),
            "cross_run_injected_requests": report.get("cross_run_injected_requests"),
            "evidence_recoveries": report.get("evidence_recoveries"),
            "provider_transport_failures": report.get("provider_transport_failures"),
            "verification_attempts": report.get("verification_attempts"),
            "verification_passed": report.get("verification_passed"),
        }
        for report in reports
    ]
    return {
        "schema_version": "contextos.effect_compare.v1",
        "runs": rows,
        "best_fold_rate": max((row["fold_rate"] or 0.0) for row in rows) if rows else None,
        "fewest_provider_transport_failures": min((row["provider_transport_failures"] or 0) for row in rows)
        if rows
        else None,
    }
