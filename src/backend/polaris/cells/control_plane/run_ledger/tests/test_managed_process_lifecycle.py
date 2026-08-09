"""GR3B-B3 focused proofs for typed managed-process Run Ledger projection."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.control_plane.run_ledger.public import (
    MANAGED_PROCESS_LIFECYCLE_EVENT_TYPE,
    AppendRunLedgerEventCommandV1,
    AppendToolCallLifecycleEventCommandV1,
    ControlPlaneRunLedgerV1Error,
    ProjectManagedProcessLifecycleCommandV1,
    append_run_ledger_event,
    append_tool_call_lifecycle_event,
    derive_managed_process_evidence_presence,
    project_managed_process_lifecycle,
)
from polaris.cells.control_plane.run_ledger.public.managed_process_lifecycle import (
    MANAGED_PROCESS_RECEIPT_LOGICAL_PATH_V1,
    ManagedProcessReceiptOwnerRecordV1,
)
from polaris.cells.control_plane.run_ledger.public.managed_process_lifecycle_bootstrap import (
    bind_managed_process_receipt_owner_port,
    clear_managed_process_receipt_owner_port,
)
from polaris.cells.events.fact_stream.public.contracts import BootstrapFactStreamWorkspaceCommandV1
from polaris.cells.events.fact_stream.public.workspace_bootstrap import bootstrap_fact_stream_workspace


class _ReceiptOwner:
    def __init__(self) -> None:
        self.records: dict[tuple[str, str], ManagedProcessReceiptOwnerRecordV1] = {}

    def read_managed_process_receipt(
        self,
        *,
        workspace: str,
        receipt_hash: str,
    ) -> ManagedProcessReceiptOwnerRecordV1 | None:
        return self.records.get((workspace, receipt_hash))


_OWNER = _ReceiptOwner()


@pytest.fixture(autouse=True)
def _bootstrap_streams(tmp_path: Path) -> Any:
    _OWNER.records.clear()
    bind_managed_process_receipt_owner_port(_OWNER)
    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=str(tmp_path),
            streams=("execution.control_plane", "task_runtime.execution"),
            maintenance_reason="gr3b_b3_managed_process_lifecycle_tests",
        )
    )
    yield
    clear_managed_process_receipt_owner_port(_OWNER)


def _persist_receipt(workspace: Path, receipt: dict[str, Any]) -> str:
    encoded = json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    receipt_hash = hashlib.sha256(encoded).hexdigest()
    receipt_ref = f"{MANAGED_PROCESS_RECEIPT_LOGICAL_PATH_V1}#{receipt_hash}"
    _OWNER.records[(str(workspace.resolve()), receipt_hash)] = ManagedProcessReceiptOwnerRecordV1(
        receipt_ref=receipt_ref,
        receipt_hash=receipt_hash,
        receipt=receipt,
    )
    return receipt_hash


def test_project_managed_process_lifecycle_happy_path_nonzero_exit_is_present_failed(
    tmp_path: Path,
) -> None:
    receipt_hash = _persist_receipt(
        tmp_path,
        {"exit_code": 1, "command": ["false"], "timeout": False},
    )

    result = project_managed_process_lifecycle(
        ProjectManagedProcessLifecycleCommandV1(
            workspace=str(tmp_path),
            run_id="run-b3-1",
            receipt_hash=receipt_hash,
            task_id="TASK-1",
            attempt_id="attempt-1",
        )
    )

    event = result.receipt.get("event") if isinstance(result.receipt, dict) else None
    assert isinstance(event, dict)
    assert event["event_type"] == MANAGED_PROCESS_LIFECYCLE_EVENT_TYPE
    assert event["receipt_hash"] == receipt_hash
    assert event["evidence_presence"] == "present_failed"
    assert event["missing_evidence"] is False
    assert event["exit_code"] == 1
    lifecycle = event["managed_process_lifecycle"]
    assert lifecycle["missing_evidence"] is False
    assert lifecycle["evidence_presence"] == "present_failed"
    assert lifecycle["receipt_body_owned_by"] == "audit.evidence"
    assert result.receipt.get("fact_receipt", {}).get("event_id")


def test_project_zero_exit_is_present_succeeded(tmp_path: Path) -> None:
    receipt_hash = _persist_receipt(tmp_path, {"exit_code": 0, "command": ["true"]})
    result = project_managed_process_lifecycle(
        ProjectManagedProcessLifecycleCommandV1(
            workspace=str(tmp_path),
            run_id="run-b3-ok",
            receipt_hash=receipt_hash,
        )
    )
    event = result.receipt["event"]
    assert event["evidence_presence"] == "present_succeeded"
    assert event["missing_evidence"] is False


def test_generic_append_of_managed_process_lifecycle_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ControlPlaneRunLedgerV1Error, match="requires_typed_projection"):
        append_run_ledger_event(
            AppendRunLedgerEventCommandV1(
                workspace=str(tmp_path),
                run_id="run-b3-generic",
                event={
                    "event_type": MANAGED_PROCESS_LIFECYCLE_EVENT_TYPE,
                    "receipt_hash": "a" * 64,
                    "ok": True,
                },
            )
        )


def test_tool_lifecycle_substitute_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ControlPlaneRunLedgerV1Error, match="tool_lifecycle_substitute"):
        append_tool_call_lifecycle_event(
            AppendToolCallLifecycleEventCommandV1(
                workspace=str(tmp_path),
                run_id="run-b3-tool",
                task_id="t1",
                turn_id="turn-1",
                role="director",
                lifecycle_receipt={
                    "event_type": MANAGED_PROCESS_LIFECYCLE_EVENT_TYPE,
                    "receipt_hash": "b" * 64,
                    "status": "completed",
                },
            )
        )


def test_tool_lifecycle_stage_managed_process_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ControlPlaneRunLedgerV1Error, match="tool_lifecycle_substitute"):
        append_tool_call_lifecycle_event(
            AppendToolCallLifecycleEventCommandV1(
                workspace=str(tmp_path),
                run_id="run-b3-stage",
                task_id="t1",
                turn_id="turn-1",
                role="director",
                stage="managed_process",
                lifecycle_receipt={"status": "completed", "tools": []},
            )
        )


def test_missing_receipt_identity_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(ControlPlaneRunLedgerV1Error, match="receipt_not_found"):
        project_managed_process_lifecycle(
            ProjectManagedProcessLifecycleCommandV1(
                workspace=str(tmp_path),
                run_id="run-b3-missing",
                receipt_hash="c" * 64,
            )
        )


def test_invalid_workspace_fails_closed(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"
    with pytest.raises(ValueError, match="existing directory"):
        ProjectManagedProcessLifecycleCommandV1(
            workspace=str(missing),
            run_id="run-b3-ws",
            receipt_hash="d" * 64,
        )


def test_evidence_presence_never_missing_for_nonzero_exit() -> None:
    assert derive_managed_process_evidence_presence({"exit_code": 2}) == "present_failed"
    assert derive_managed_process_evidence_presence({"exit_code": 0}) == "present_succeeded"
    assert derive_managed_process_evidence_presence({"timeout": True}) == "present_failed"
    assert derive_managed_process_evidence_presence({"pid": 9}) == "present_unknown_outcome"


def test_receipt_body_ok_false_with_nonzero_exit_is_accepted(tmp_path: Path) -> None:
    """Durable process receipts may carry ok/failed fields; presence from exit_code."""

    receipt_hash = _persist_receipt(
        tmp_path,
        {"exit_code": 1, "ok": False, "failed": True, "success": False},
    )
    result = project_managed_process_lifecycle(
        ProjectManagedProcessLifecycleCommandV1(
            workspace=str(tmp_path),
            run_id="run-b3-receipt-ok",
            receipt_hash=receipt_hash,
        )
    )
    event = result.receipt["event"]
    assert event["evidence_presence"] == "present_failed"
    assert event["missing_evidence"] is False
    assert event["exit_code"] == 1


def test_public_append_has_no_authorize_keyword_and_rejects_fabricated_event(
    tmp_path: Path,
) -> None:
    import inspect

    sig = inspect.signature(append_run_ledger_event)
    # Only the command parameter — no public authorize flag of any name.
    assert list(sig.parameters) == ["command"]
    assert "_authorize_managed_process_lifecycle" not in sig.parameters

    with pytest.raises(ControlPlaneRunLedgerV1Error, match="requires_typed_projection"):
        append_run_ledger_event(
            AppendRunLedgerEventCommandV1(
                workspace=str(tmp_path),
                run_id="run-b3-forge",
                event={
                    "event_type": MANAGED_PROCESS_LIFECYCLE_EVENT_TYPE,
                    "receipt_hash": "f" * 64,
                    "missing_evidence": True,
                    "evidence_presence": "missing",
                    "ok": True,
                },
            )
        )


def test_no_process_spawn_or_taskruntime_side_effects(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """B3 must not launch processes or open TaskRuntime claims."""

    def _boom(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("process or taskruntime side effect must not run in B3")

    monkeypatch.setattr("subprocess.Popen", _boom)
    monkeypatch.setattr("subprocess.run", _boom)
    # If TaskRuntime public claim is imported later, still block common names.
    import sys

    for mod_name in list(sys.modules):
        if "task_runtime" in mod_name and hasattr(sys.modules[mod_name], "claim_directed_effect"):
            monkeypatch.setattr(sys.modules[mod_name], "claim_directed_effect", _boom, raising=False)

    receipt_hash = _persist_receipt(tmp_path, {"exit_code": 0})
    result = project_managed_process_lifecycle(
        ProjectManagedProcessLifecycleCommandV1(
            workspace=str(tmp_path),
            run_id="run-b3-no-spawn",
            receipt_hash=receipt_hash,
        )
    )
    assert result.receipt["event"]["event_type"] == MANAGED_PROCESS_LIFECYCLE_EVENT_TYPE
