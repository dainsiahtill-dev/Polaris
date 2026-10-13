"""A receipt-purpose projection must preserve its committed execution policy."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from polaris.bootstrap import project_completion_diagnostics_owner as owner
from polaris.cells.control_plane.run_ledger.public import (
    AppendRunLedgerEventCommandV1,
    ReadRunLedgerProjectionQueryV1,
    append_run_ledger_event,
    read_run_ledger_projection,
)
from polaris.cells.control_plane.run_ledger.tests.test_public_servicea import (
    _authoritative_directed_effect_receipt,
    _authoritative_directed_effect_receipt_commit,
)
from polaris.cells.events.fact_stream.public import (
    BootstrapFactStreamWorkspaceCommandV1,
    bootstrap_fact_stream_workspace,
    fact_stream_bootstrap_streams,
)
from polaris.cells.factory.verification_guard.public import ProjectCompletionOwnerObservationV1Error
from polaris.cells.roles.kernel.internal.transaction.tool_batch_executor import _helpers as producer
from polaris.cells.runtime.execution_broker.internal import project_verification_authority as authority_module
from polaris.cells.runtime.execution_broker.public.project_verification import (
    ResolveProjectVerificationAuthorityQueryV1,
    authorize_project_verification_command,
)
from polaris.tests.unit.bootstrap.test_project_completion_diagnostics_owner import _contract


def _token(task: str, *, command_enabled: bool = True, issue: str = "") -> dict[str, Any]:
    return {
        "schema_version": 1,
        "source": "control_plane.job_token",
        "token_id": "job-" + task,
        "run_id": "run-1",
        "factory_run_id": "run-1",
        "project_id": "project-1",
        "stage": "pending_exec",
        "contract_hash": "c" * 64,
        "blueprint_hash": "d" * 64,
        "allowed_write_paths": ["src/main.py"],
        "gate_policy": {
            "enabled_evidence_modalities": ["tool_receipt", "code", "qa", *(["command"] if command_enabled else [])],
            "required_evidence_modalities": ["tool_receipt", "code", "command", "qa"],
            "policy_hash": "e" * 64,
        },
        "capability_audit": {"ok": not issue, "issues": [issue] if issue else []},
    }


def _commit_receipt(workspace: Path, task: str, token: dict[str, Any]) -> None:
    producer._append_tool_batch_receipts_to_run_ledger(
        workspace=str(workspace),
        run_id="director-" + task,
        role_id="director",
        task_id=task,
        turn_id="turn-" + task,
        capability_token=token,
        invocations=[
            {
                "tool_name": "write_file",
                "call_id": "call-" + task,
                "arguments": {"file": "src/main.py", "content": "pass\n"},
            }
        ],
        receipts=[
            {
                "batch_id": "batch-" + task,
                "turn_id": "turn-" + task,
                "success_count": 1,
                "failure_count": 0,
                "pending_async_count": 0,
                "has_pending_async": False,
                "results": [
                    {
                        "call_id": "call-" + task,
                        "tool_name": "write_file",
                        "status": "success",
                        "result": {
                            "ok": True,
                            "effect_receipt": _authoritative_directed_effect_receipt(run_id="director-" + task),
                            "effect_receipt_commit": _authoritative_directed_effect_receipt_commit(
                                run_id="director-" + task
                            ),
                        },
                    }
                ],
            }
        ],
    )


def _setup(workspace: Path) -> None:
    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=str(workspace),
            maintenance_reason="committed_policy_regression",
            streams=fact_stream_bootstrap_streams(),
        )
    )


def _projection(workspace: Path) -> dict[str, Any]:
    return read_run_ledger_projection(
        ReadRunLedgerProjectionQueryV1(
            workspace=str(workspace),
            run_id="run-1",
            factory_run_id="run-1",
            project_id="project-1",
            include_migration_ledgers=False,
        )
    ).projection


def _bind_authority(monkeypatch: pytest.MonkeyPatch) -> None:
    contract = _contract()
    monkeypatch.setattr(owner, "query_project_completion_contract", lambda _query: contract)
    monkeypatch.setattr(
        authority_module, "_EXECUTION_AUTHORITY_PORT", owner.PROJECT_COMPLETION_OWNER_OBSERVATION_ADAPTER
    )


def _query(workspace: Path, obligation: str = "verify.test") -> ResolveProjectVerificationAuthorityQueryV1:
    contract = _contract()
    return ResolveProjectVerificationAuthorityQueryV1(
        workspace=str(workspace),
        project_id=contract.project_id,
        run_id=contract.run_id,
        completion_contract_hash=contract.contract_hash,
        obligation_id=obligation,
    )


def test_real_receipt_producer_preserves_original_policy_and_project_requirements(tmp_path: Path) -> None:
    _setup(tmp_path)
    token = _token("task-1")
    before = deepcopy(token)
    _commit_receipt(tmp_path, "task-1", token)
    projection = _projection(tmp_path)["run_projection"]
    assert token == before
    assert projection["execution_capability_by_task"]["task-1"]["gate_policy"] == before["gate_policy"]
    assert set(projection["evidence_policy"]["required_modalities"]) >= {"code", "command", "qa"}
    assert set(projection["evidence_policy"]["missing_required_modalities"]) >= {"code", "command", "qa"}
    local = next(g for g in projection["gates"] if g["name"] == "tool_receipt")
    assert local["ok"] is True
    assert local["missing_required_evidence_modalities"] == []


def test_real_committed_projection_authorizes_exact_owner_before_any_command_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _setup(tmp_path)
    _commit_receipt(tmp_path, "task-2", _token("task-2"))
    _commit_receipt(tmp_path, "task-3", _token("task-3", command_enabled=False))
    _bind_authority(monkeypatch)
    projection = _projection(tmp_path)["run_projection"]
    assert projection["capability"]["latest_token_id"] == "job-task-3"
    assert projection["physical_evidence"]["command_count"] == 0
    authority = owner.PROJECT_COMPLETION_OWNER_OBSERVATION_ADAPTER.resolve_project_verification_authority(
        _query(tmp_path)
    )
    assert authority.owner_task_id == "task-2"
    assert authority.job_token_id == "job-task-2"
    assert authority.argv == ("pytest", "-q")
    assert authority.cwd == "."
    command = authorize_project_verification_command(_query(tmp_path))
    assert command.job_token_id == "job-task-2"


@pytest.mark.parametrize("failure", ["command_disabled", "policy_missing", "expired", "revoked", "foreign_owner"])
def test_disabled_or_uncommitted_owner_cannot_borrow_global_permission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    _setup(tmp_path)
    token = _token(
        "task-2",
        command_enabled=failure != "command_disabled",
        issue=failure if failure in {"expired", "revoked"} else "",
    )
    if failure == "policy_missing":
        token.pop("gate_policy")
    if failure != "foreign_owner":
        _commit_receipt(tmp_path, "task-2", token)
    _commit_receipt(tmp_path, "task-3", _token("task-3"))
    _bind_authority(monkeypatch)
    with pytest.raises(ProjectCompletionOwnerObservationV1Error):
        authorize_project_verification_command(_query(tmp_path))


@pytest.mark.parametrize("exit_code", [0, 1])
def test_official_evidence_settles_project_missingness_and_preserves_real_failure(
    tmp_path: Path, exit_code: int
) -> None:
    _setup(tmp_path)
    token = _token("task-1")
    _commit_receipt(tmp_path, "task-1", token)
    for name, physical in [
        (
            "workspace_validation",
            {
                "command_count": 1,
                "commands": [{"argv": ["pytest", "-q"], "cwd": ".", "exit_code": exit_code, "ok": exit_code == 0}],
                "modalities": {"code": {"present": True, "ok": True}},
            },
        ),
        ("qa_verdict", {"modalities": {"qa": {"present": True, "ok": True}}}),
    ]:
        append_run_ledger_event(
            AppendRunLedgerEventCommandV1(
                workspace=str(tmp_path),
                run_id="run-1",
                event={
                    "event_type": "gate_evaluated",
                    "stage": name,
                    "gate": {"name": name, "ok": name == "qa_verdict" or exit_code == 0},
                    "job_token": deepcopy(token),
                    "physical_evidence": physical,
                },
            )
        )
    projection = _projection(tmp_path)["run_projection"]
    policy = projection["evidence_policy"]
    assert set(policy["required_modalities"]) >= {"tool_receipt", "code", "command", "qa"}
    assert policy["missing_required_modalities"] == []
    if exit_code:
        assert policy["failed_required_modalities"] == ["command"]
        assert policy["outcome_ok"] is False
    else:
        assert policy["failed_required_modalities"] == []
        assert policy["ok"] is True


def test_bare_official_pass_text_cannot_clear_actual_required_evidence(tmp_path: Path) -> None:
    _setup(tmp_path)
    token = _token("task-1")
    _commit_receipt(tmp_path, "task-1", token)
    append_run_ledger_event(
        AppendRunLedgerEventCommandV1(
            workspace=str(tmp_path),
            run_id="run-1",
            event={
                "event_type": "gate_evaluated",
                "stage": "qa_review",
                "gate": {"name": "qa_verdict", "ok": True, "summary": "PASS"},
                "job_token": deepcopy(token),
                "physical_evidence": {},
            },
        )
    )
    policy = _projection(tmp_path)["run_projection"]["evidence_policy"]
    assert {"code", "command", "qa"} <= set(policy["missing_required_modalities"])
    assert policy["ok"] is False
