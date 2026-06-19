"""Graded fail-closed exit codes and chain-summary builder.

Extracted from ``orchestration_engine``. These helpers translate Director/QA
phase outcomes into graded exit codes and produce the machine-readable
``chain-summary/1`` payload consumed by external runners and auditors.

Bodies are byte-for-byte identical to the original ``orchestration_engine``
definitions and are re-exported from that module to preserve the canonical
import path.
"""

from __future__ import annotations

from typing import Any


def grade_director_exit_code(director_status: str, completed_count: int) -> int:
    """Graded fail-closed exit for the Director phase.

    0 = director not failed; 4 = partial progress (>=1 task succeeded but
    failures/blocked present, integration QA evidence may be partial);
    1 = zero-success hard failure. Codes 2 (stop condition) and 3
    (manual/AGENTS confirmation) are reserved by run_once; any nonzero
    value still trips --stop-on-failure.
    """
    if director_status in {"failed", "blocked"}:
        return 4 if completed_count > 0 else 1
    return 0


def grade_qa_exit_code(director_status: str, current_exit_code: int) -> int:
    """Exit grade once integration QA reports failure.

    5 = Director fully succeeded but QA failed; otherwise preserve the graded
    Director exit (4/1) instead of flattening everything back to 1.
    """
    return 5 if director_status == "success" else (current_exit_code or 1)


def build_chain_summary(
    *,
    workflow_exit_code: int,
    director_result: dict[str, Any],
    integration_qa_result: Any,
    qa_passed: bool,
    qa_reason: str,
    generated_at: str,
) -> dict[str, Any]:
    """Machine-readable chain outcome (schema chain-summary/1) for runners."""
    exit_class_by_code = {0: "clean", 4: "director_partial", 5: "qa_failed"}
    return {
        "schema_version": "chain-summary/1",
        "exit_code": int(workflow_exit_code),
        "exit_class": exit_class_by_code.get(int(workflow_exit_code), "hard_failed"),
        "planning_ok": True,
        "director": {
            "status": str(director_result.get("status") or ""),
            "total": int(director_result.get("total") or 0),
            "successes": int(director_result.get("successes") or 0),
            "failures": int(director_result.get("failures") or 0),
            "blocked": int(director_result.get("blocked") or 0),
        },
        "integration_qa": {
            "ran": bool(isinstance(integration_qa_result, dict) and integration_qa_result.get("ran") is True),
            "passed": bool(qa_passed),
            "reason": qa_reason,
        },
        "generated_at": generated_at,
    }
