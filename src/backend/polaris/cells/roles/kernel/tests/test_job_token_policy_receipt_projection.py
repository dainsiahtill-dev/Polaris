"""Request-owned policy must survive the canonical ToolBatch ledger boundary."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.control_plane.run_ledger.public import (
    JobToken,
    ReadRunLedgerProjectionQueryV1,
    read_run_ledger_projection,
)
from polaris.cells.control_plane.run_ledger.public.ledger import RunLedger
from polaris.cells.events.fact_stream.public import (
    BootstrapFactStreamWorkspaceCommandV1,
    bootstrap_fact_stream_workspace,
    fact_stream_bootstrap_streams,
)
from polaris.cells.roles.kernel.internal.kernel.core import RoleExecutionKernel
from polaris.cells.roles.kernel.internal.kernel.tool_executor import (
    derive_role_turn_capability_scope,
    derive_role_turn_capability_token,
)
from polaris.cells.roles.kernel.internal.kernel.transaction_factory import create_transaction_kernel
from polaris.cells.roles.kernel.internal.transaction.tool_batch_executor._helpers import (
    _append_tool_batch_receipts_to_run_ledger,
)
from polaris.cells.roles.kernel.public.turn_contracts import (
    BatchId,
    ToolBatch,
    ToolCallId,
    ToolEffectType,
    ToolExecutionMode,
    ToolInvocation,
    TurnId,
)
from polaris.cells.roles.profile.public import RoleProfile, RoleTurnRequest


def _request(workspace: Path, policy: dict[str, Any] | None) -> tuple[RoleTurnRequest, dict[str, Any]]:
    token = JobToken(
        schema_version=1,
        token_id="job-task-owned-policy",
        run_id="factory-policy-projection",
        factory_run_id="factory-policy-projection",
        project_id="generic-project",
        stage="pending_exec",
        target_files=["src/module.py"],
        allowed_paths=["src/module.py", "docs/context.md"],
        allowed_write_paths=["src/module.py"],
        allowed_read_paths=["src/module.py", "docs/context.md"],
        gate_policy=deepcopy(policy or {}),
        capability_audit={"ok": True, "issues": []},
        contract_hash="a" * 64,
        blueprint_hash="b" * 64,
    ).to_dict()
    token["allowed_commands"] = ["npm test"]
    if policy is None:
        token.pop("gate_policy")
    request = RoleTurnRequest(
        workspace=str(workspace),
        message="Observe the authorized source before editing it.",
        task_id="TASK-1",
        run_id="director-policy-projection",
        metadata={"turn_request_id": "policy-projection-turn"},
        context_override={
            "execution_envelope_hash": "e" * 64,
            "director_execution_envelope": {
                "schema_version": "polaris.execution_envelope.v1",
                "envelope_hash": "e" * 64,
                "authorization": {"capability_token_ref": token["token_id"], "allowed_write_paths": ["src/module.py"]},
            },
            "metadata": {"control_plane_job_token": token},
        },
    )
    return request, token


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "policy",
    [
        {
            "enabled_evidence_modalities": ["tool_receipt", "command"],
            "required_evidence_modalities": ["command"],
            "policy_hash": "c" * 64,
        },
        {"enabled_evidence_modalities": ["tool_receipt"], "required_evidence_modalities": ["tool_receipt"]},
        None,
    ],
    ids=["command-enabled", "command-disabled", "legacy-missing"],
)
async def test_request_policy_reaches_actual_batch_ledger_without_adding_permissions(
    tmp_path: Path, policy: dict[str, Any] | None
) -> None:
    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=str(tmp_path),
            maintenance_reason="job_token_policy_projection_test",
            streams=fact_stream_bootstrap_streams(),
        )
    )
    request, original_token = _request(tmp_path, policy)
    original_snapshot = deepcopy(original_token)
    scope = derive_role_turn_capability_scope(request)
    derived = derive_role_turn_capability_token(request, scope)
    kernel = RoleExecutionKernel.create_default(workspace=str(tmp_path))
    profile = RoleProfile(role_id="director", display_name="Director", description="Canonical policy projection test")
    profile = replace(profile, tool_policy=replace(profile.tool_policy, whitelist=["read_file"]))
    transaction = create_transaction_kernel(kernel, "director", profile, request)
    executor = transaction._tool_batch_executor
    invocation = ToolInvocation(
        call_id=ToolCallId("read-missing-authorized-file"),
        tool_name="read_file",
        arguments={"path": "src/module.py"},
        effect_type=ToolEffectType.READ,
        execution_mode=ToolExecutionMode.READONLY_PARALLEL,
    )
    batch = ToolBatch(batch_id=BatchId("policy-batch"), invocations=[invocation], parallel_readonly=[invocation])
    runtime = executor._build_tool_batch_runtime(workspace=str(tmp_path), turn_id="policy-turn")
    receipts = [
        receipt.model_dump(mode="python") for receipt in await runtime.execute_batch(batch, TurnId("policy-turn"))
    ]
    # This is a real failed filesystem read, not a manufactured successful effect.
    assert receipts[0]["failure_count"] == 1
    _append_tool_batch_receipts_to_run_ledger(
        workspace=str(tmp_path),
        run_id="director-policy-projection",
        role_id="director",
        task_id="TASK-1",
        turn_id="policy-turn",
        invocations=[invocation],
        receipts=receipts,
        capability_token=executor._capability_token,
        execution_envelope_hash=executor._execution_envelope_hash,
    )
    events = RunLedger(tmp_path, run_id="factory-policy-projection").read_events()
    gate = next(event for event in events if event["event_type"] == "gate_evaluated")
    projection = read_run_ledger_projection(
        ReadRunLedgerProjectionQueryV1(workspace=str(tmp_path), run_id="factory-policy-projection")
    )
    capability = projection.projection["run_projection"]["execution_capability_by_task"]["TASK-1"]
    expected_policy = policy or {}
    assert capability["gate_policy"] == expected_policy
    assert gate["job_token"]["gate_policy"] == expected_policy
    assert gate["gate"]["gate_policy"]["enabled_evidence_modalities"] == ["tool_receipt"]
    assert gate["gate"]["ok"] is False
    assert capability["latest_token_id"] == original_token["token_id"]
    assert capability["latest_contract_hash"] == original_token["contract_hash"]
    assert capability["latest_blueprint_hash"] == original_token["blueprint_hash"]
    assert executor._capability_token["allowed_scope"] == ["src/module.py"]
    assert derived["allowed_commands"] == ["npm test"]
    assert original_token == original_snapshot
    assert ("command" in capability["gate_policy"].get("enabled_evidence_modalities", [])) is (
        policy is not None and "command" in policy["enabled_evidence_modalities"]
    )


def test_derived_policy_is_detached_from_original_nested_authority(tmp_path: Path) -> None:
    policy = {"enabled_evidence_modalities": ["command"], "evidence_policy_compiler": {"policy_hash": "d" * 64}}
    request, original_token = _request(tmp_path, policy)
    derived = derive_role_turn_capability_token(request, derive_role_turn_capability_scope(request))
    assert derived["gate_policy"] == original_token["gate_policy"]
    derived["gate_policy"]["enabled_evidence_modalities"].append("browser")
    derived["gate_policy"]["evidence_policy_compiler"]["policy_hash"] = "changed"
    assert original_token["gate_policy"] == policy
