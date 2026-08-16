"""Forced write retry persist miss must not settle as no_materialized_changes."""

from __future__ import annotations

from pathlib import Path

from polaris.cells.control_plane.run_ledger.public import FailureClassV1
from polaris.cells.roles.adapters.internal.director.execute_method import (
    MaterializationState,
    _phase_no_materialized_changes,
)
from polaris.cells.roles.adapters.internal.director.execute_method._phases_failure import (
    _no_write_retry_platform_failure_payload,
)
from polaris.cells.roles.adapters.tests.test_director_adapter_failure_closure_b import (
    _make_adapter,
)


def test_no_write_retry_persist_failed_is_platform_payload() -> None:
    payload = _no_write_retry_platform_failure_payload(
        {
            "success": False,
            "error": "TransactionKernel execution failed: final_physical_context_snapshot_persist_failed",
            "llm_calls": 0,
            "metadata": {"error_message": "final_physical_context_snapshot_persist_failed"},
        }
    )
    assert payload is not None
    assert payload["error_code"] == "final_physical_context_snapshot_persist_failed"
    assert payload["failure_class"] == FailureClassV1.EXECUTION_EVIDENCE_MISSING.value
    assert _no_write_retry_platform_failure_payload({"success": True}) is None
    assert _no_write_retry_platform_failure_payload({"success": False, "error": "no_write_tool_available"}) is None


def test_no_materialized_changes_preserves_retry_persist_failure(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    task = adapter.task_runtime.create_task_row(
        subject="Implement forecast engine",
        description="Write src/engine/forecast.py",
        metadata={"target_files": ["src/engine/forecast.py"], "scope_paths": ["src/engine/forecast.py"]},
    )
    task_id = str(task["id"])
    state = MaterializationState(
        current_files={"src/engine/forecast.py": "old"},
        new_files=[],
        modified_files=[],
        all_affected_files=[],
        tool_results=[{"tool": "read_file", "success": True}],
    )

    result = _phase_no_materialized_changes(
        adapter,
        baseline_files={"src/engine/forecast.py": "old"},
        board_claim_applied=False,
        can_accept_existing_scope=False,
        context={},
        direct_fallback_summary=None,
        empty_write_content_retry_summary=None,
        no_write_materialization_retry_summary={
            "success": False,
            "error": "TransactionKernel execution failed: final_physical_context_snapshot_persist_failed",
            "llm_calls": 0,
            "metadata": {"error_message": "final_physical_context_snapshot_persist_failed"},
        },
        existing_contract_evidence={"ok": True, "existing_paths": ["src/engine/forecast.py"]},
        primary_llm_summary={"success": False, "error": "no_write_tool_available"},
        requires_fresh_materialization=True,
        run_id="director-dc8940d840e6",
        target_task_id=task_id,
        task={"target_files": ["src/engine/forecast.py"]},
        task_claim_session_id="",
        workspace_name=tmp_path.name,
        write_tool_evidence=False,
        state=state,
    )

    assert result is not None
    assert result["success"] is False
    assert result["error"] == "director_final_request_context_persist_failed"
    assert result["error_code"] == "final_physical_context_snapshot_persist_failed"
    assert result["failure_class"] == FailureClassV1.EXECUTION_EVIDENCE_MISSING.value
    assert result.get("error_code") != "incomplete_materialization"


def test_primary_llm_persist_failed_is_platform_payload() -> None:
    from polaris.cells.roles.adapters.internal.director.execute_method._phases_failure import (
        _primary_llm_provider_failure_payload,
    )

    payload = _primary_llm_provider_failure_payload(
        {
            "success": False,
            "error": "TransactionKernel execution failed: final_physical_context_snapshot_persist_failed",
            "llm_calls": 0,
        }
    )
    assert payload is not None
    assert payload["error"] == "director_final_request_context_persist_failed"
    assert payload["error_code"] == "final_physical_context_snapshot_persist_failed"
    assert payload["failure_class"] == FailureClassV1.EXECUTION_EVIDENCE_MISSING.value
    assert payload["root_cause_hint"] == "final_physical_context_snapshot_persist_failed"


def test_no_materialized_changes_preserves_primary_persist_failure(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    task = adapter.task_runtime.create_task_row(
        subject="Implement weather models",
        description="Write src/models/mood.py",
        metadata={"target_files": ["src/models/mood.py"], "scope_paths": ["src/models/mood.py"]},
    )
    task_id = str(task["id"])
    state = MaterializationState(
        current_files={"src/models/mood.py": "old"},
        new_files=[],
        modified_files=[],
        all_affected_files=[],
        tool_results=[],
    )

    result = _phase_no_materialized_changes(
        adapter,
        baseline_files={"src/models/mood.py": "old"},
        board_claim_applied=False,
        can_accept_existing_scope=False,
        context={},
        direct_fallback_summary=None,
        empty_write_content_retry_summary=None,
        no_write_materialization_retry_summary=None,
        existing_contract_evidence={"ok": True, "existing_paths": ["src/models/mood.py"]},
        primary_llm_summary={
            "success": False,
            "error": "TransactionKernel execution failed: final_physical_context_snapshot_persist_failed",
            "llm_calls": 0,
        },
        requires_fresh_materialization=True,
        run_id="director-87348cf58cfb",
        target_task_id=task_id,
        task={"target_files": ["src/models/mood.py"]},
        task_claim_session_id="",
        workspace_name=tmp_path.name,
        write_tool_evidence=False,
        state=state,
    )

    assert result is not None
    assert result["success"] is False
    assert result["error"] == "director_final_request_context_persist_failed"
    assert result["error_code"] == "final_physical_context_snapshot_persist_failed"
    assert result.get("error_code") != "incomplete_materialization"


def test_primary_llm_circuit_open_is_provider_failure_payload() -> None:
    from polaris.cells.roles.adapters.internal.director.execute_method._phases_failure import (
        _primary_llm_provider_failure_payload,
    )

    payload = _primary_llm_provider_failure_payload(
        {"success": False, "error": "TransactionKernel execute failed: circuit_open:58s_remaining"}
    )
    assert payload is not None
    assert payload["error"] == "model_provider_failure"
    assert payload["error_code"] == "model_provider_failure"
    assert payload["failure_class"] == FailureClassV1.MODEL_PROVIDER_FAILURE.value
    assert payload["root_cause_hint"] == "provider_circuit_open"


def test_no_write_retry_circuit_open_is_platform_payload() -> None:
    payload = _no_write_retry_platform_failure_payload(
        {
            "success": False,
            "error": "circuit_open:58s_remaining",
            "metadata": {"error_message": "circuit_open:58s_remaining"},
        }
    )
    assert payload is not None
    assert payload["error"] == "model_provider_failure"
    assert payload["error_code"] == "model_provider_failure"
    assert payload["root_cause_hint"] == "provider_circuit_open"


def test_no_materialized_changes_preserves_circuit_open(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    task = adapter.task_runtime.create_task_row(
        subject="Implement weather models",
        description="Write src/models/weather.py",
        metadata={"target_files": ["src/models/weather.py"], "scope_paths": ["src/models/weather.py"]},
    )
    task_id = str(task["id"])
    state = MaterializationState(
        current_files={"src/models/weather.py": "old"},
        new_files=[],
        modified_files=[],
        all_affected_files=[],
        tool_results=[{"tool": "file_exists", "success": True}],
    )

    result = _phase_no_materialized_changes(
        adapter,
        baseline_files={"src/models/weather.py": "old"},
        board_claim_applied=False,
        can_accept_existing_scope=False,
        context={},
        direct_fallback_summary=None,
        empty_write_content_retry_summary=None,
        no_write_materialization_retry_summary=None,
        existing_contract_evidence={"ok": True, "existing_paths": ["src/models/weather.py"]},
        primary_llm_summary={
            "success": False,
            "error": "TransactionKernel execute failed: circuit_open:58s_remaining",
        },
        requires_fresh_materialization=True,
        run_id="director-8f7992c8b2d4",
        target_task_id=task_id,
        task={"target_files": ["src/models/weather.py"]},
        task_claim_session_id="",
        workspace_name=tmp_path.name,
        write_tool_evidence=False,
        state=state,
    )

    assert result is not None
    assert result["success"] is False
    assert result["error"] == "model_provider_failure"
    assert result["error_code"] == "model_provider_failure"
    assert result.get("error_code") != "incomplete_materialization"
