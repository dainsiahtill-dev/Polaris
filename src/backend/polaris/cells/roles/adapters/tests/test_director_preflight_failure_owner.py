"""Pre-provider authority rejection keeps its owner through adapter failure."""

from pathlib import Path

import pytest
from polaris.cells.control_plane.run_ledger.public import FailureClassV1
from polaris.cells.roles.adapters.internal.director.execute_method import (
    MaterializationState,
    _phase_no_materialized_changes,
)
from polaris.cells.roles.adapters.tests.test_director_adapter_failure_closure_b import _make_adapter

AUTHORITY_ERROR = "verification_command_authority_rows_missing_or_mismatched"


def preflight_summary() -> dict:
    # Native event 18, factory_a0bbc25460a9: runtime rejected before any turn.
    return {
        "success": False,
        "error": AUTHORITY_ERROR,
        "llm_calls": 0,
        "metadata": {
            "role_runtime_entrypoint": "roles.runtime.execute_role_session",
            "continuous_execution": {
                "enabled": True,
                "stop_reason": AUTHORITY_ERROR,
                "turns_executed": 0,
            },
            "session_turn_commit_receipts": [],
            "tool_results": [],
            "batch_receipt": {
                "batch_receipts": [], "results": [], "success_count": 0,
                "failure_count": 0, "projection_only": True,
            },
        },
    }


def test_native_preflight_authority_failure_preserves_owner(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    result = _phase_no_materialized_changes(
        adapter,
        baseline_files={}, board_claim_applied=False,
        can_accept_existing_scope=False, context={},
        direct_fallback_summary=None, empty_write_content_retry_summary=None,
        no_write_materialization_retry_summary=None,
        existing_contract_evidence={"ok": False},
        primary_llm_summary=preflight_summary(), requires_fresh_materialization=True,
        run_id="director-preflight", target_task_id="1",
        task={"target_files": ["src/index.ts"]}, task_claim_session_id="",
        workspace_name=tmp_path.name, write_tool_evidence=False,
        state=MaterializationState(
            current_files={}, new_files=[], modified_files=[], all_affected_files=[],
            tool_results=[{"tool": "deterministic_repair_summary", "success": True, "files": []}],
        ),
    )
    assert result is not None and result["success"] is False
    assert result["error_code"] == AUTHORITY_ERROR
    assert result["failure_class"] == FailureClassV1.BLUEPRINT_VERIFY_INVALID.value
    assert result["responsible_layer"] == "execution_control_plane"
    assert result["failure_stage"] == "director_context_preflight"
    assert result["failure_evidence"][0]["metadata"]["error_code"] == AUTHORITY_ERROR
    assert "task_boundary_verdict" not in result


@pytest.mark.parametrize("change", ["one_turn", "one_call", "different_reason"])
def test_authority_words_without_exact_zero_turn_preflight_are_not_reclassified(
    tmp_path: Path, change: str,
) -> None:
    from polaris.cells.roles.adapters.internal.director.execute_method._phases_failure import (
        _primary_llm_provider_failure_payload,
    )

    summary = preflight_summary()
    if change == "one_turn":
        summary["metadata"]["continuous_execution"]["turns_executed"] = 1
    elif change == "one_call":
        summary["llm_calls"] = 1
    else:
        summary["metadata"]["continuous_execution"]["stop_reason"] = "tool_failed"
    assert _primary_llm_provider_failure_payload(summary) is None
