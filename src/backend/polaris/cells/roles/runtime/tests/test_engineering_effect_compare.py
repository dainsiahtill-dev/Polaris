from __future__ import annotations

import json

from polaris.cells.roles.runtime.internal.engineering_effect_compare import (
    compare_effect_reports,
    load_effect_reports,
)


def _write_report(root, name: str, payload: dict) -> None:
    directory = root / ".polaris" / "runtime" / "contextos" / "effect-reports"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(json.dumps(payload), encoding="utf-8")


def test_load_and_compare_effect_reports(tmp_path) -> None:
    _write_report(
        tmp_path,
        "a.json",
        {
            "schema_version": "contextos.effect_report.v1",
            "session_id": "a",
            "status": "completed",
            "verification": "passed",
            "turns": 40,
            "fold_rate": 0.6,
            "cards_folded": 12,
            "memory_injected_requests": 30,
            "cross_run_injected_requests": 40,
            "evidence_recoveries": 2,
            "provider_transport_failures": 0,
            "verification_attempts": 3,
            "verification_passed": 1,
        },
    )
    _write_report(
        tmp_path,
        "b.json",
        {
            "schema_version": "contextos.effect_report.v1",
            "session_id": "b",
            "status": "failed",
            "verification": "not_run",
            "turns": 80,
            "fold_rate": 0.3,
            "cards_folded": 8,
            "memory_injected_requests": 50,
            "cross_run_injected_requests": 80,
            "evidence_recoveries": 5,
            "provider_transport_failures": 2,
            "verification_attempts": 9,
            "verification_passed": 0,
        },
    )
    reports = load_effect_reports(tmp_path)
    assert [report["session_id"] for report in reports] == ["b", "a"]
    comparison = compare_effect_reports(reports)
    assert comparison["schema_version"] == "contextos.effect_compare.v1"
    assert comparison["best_fold_rate"] == 0.6
    assert comparison["fewest_provider_transport_failures"] == 0


def test_compare_handles_empty() -> None:
    comparison = compare_effect_reports([])
    assert comparison["runs"] == [] and comparison["best_fold_rate"] is None
