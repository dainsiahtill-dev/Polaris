"""Real owner composition for ordinary same-path partial mutation batches.

No provider runs. Gateway, policy, TaskRuntime, fence, physical mutation and
receipt commit are production owners; only the decoded provider input is supplied.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from polaris.cells.events.fact_stream.public import (
    BootstrapFactStreamWorkspaceCommandV1,
    bootstrap_fact_stream_workspace,
    fact_stream_bootstrap_streams,
)
from polaris.cells.roles.adapters.public.directed_effect_policy_service import (
    create_director_effect_policy_snapshot_port,
)
from polaris.cells.roles.adapters.public.directed_effect_service import create_director_directed_effect_mutation_port
from polaris.cells.roles.kernel.internal.directed_effect_policy_guard import DirectedEffectPolicyGuard
from polaris.cells.roles.kernel.internal.tool_gateway import RoleToolGateway
from polaris.cells.roles.kernel.internal.transaction.ledger import TransactionConfig, TurnLedger
from polaris.cells.roles.kernel.internal.transaction.tool_batch_executor import ToolBatchExecutor
from polaris.cells.roles.kernel.internal.turn_state_machine import TurnState, TurnStateMachine
from polaris.cells.roles.kernel.public import DirectedEffectRuntimeDependenciesV1
from polaris.cells.roles.kernel.public.directed_effect_service import create_directed_effect_fence_ports
from polaris.cells.roles.kernel.public.turn_contracts import ToolBatch, ToolInvocation
from polaris.cells.roles.profile.public.service import RoleProfile, RoleToolPolicy
from polaris.cells.runtime.task_runtime.public import TaskRuntimeExecutionAttemptIdentityV1, TaskRuntimeService
from polaris.cells.runtime.task_runtime.public.service import create_task_runtime_execution_attempt_authority
from polaris.kernelone.llm.toolkit.tool_normalization import normalize_tool_arguments

ORIGINAL = "alpha\nbeta\ngamma\ndelta\n"


def _block(old: str, new: str) -> str:
    return f"<<<< SEARCH\n{old}\n====\n{new}\n>>>> REPLACE\n"


def _real_chain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, scope: list[str] | None = None) -> Any:
    monkeypatch.setenv("KERNELONE_ROOT", str(tmp_path / "system"))
    monkeypatch.setenv("KERNELONE_HOME", str(tmp_path / "system/.polaris"))
    monkeypatch.delenv("KERNELONE_RUNTIME_ROOT", raising=False)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "owned.txt").write_text(ORIGINAL, encoding="utf-8")
    (workspace / "AGENTS.md").write_text("# Generic component fixture\n", encoding="utf-8")
    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=str(workspace), streams=fact_stream_bootstrap_streams(), maintenance_reason="tool-contract-test"
        )
    )
    service = TaskRuntimeService(str(workspace))
    row_id = int(service.create_task_row(subject="generic edit contract")["id"])
    attempt = TaskRuntimeExecutionAttemptIdentityV1.from_record(
        service.claim_execution(
            row_id,
            worker_id="test",
            role_id="director",
            run_id="test-range",
            external_task_id="TEST-1",
            selection_source="test",
        )["execution_attempt"]
    )
    authority = create_task_runtime_execution_attempt_authority(attempt)
    paths = ["owned.txt"] if scope is None else list(scope)
    token = {
        "token_id": "fixture-token",
        "execution_envelope_hash": "a" * 64,
        "capability_audit_ok": True,
        "allowed_write_paths": paths,
        "allowed_commands": [],
    }
    profile = RoleProfile(
        role_id="director",
        display_name="Fixture",
        description="No provider",
        tool_policy=RoleToolPolicy(whitelist=["edit_blocks", "write_file", "read_file"], allow_code_write=True),
    )
    gateway = RoleToolGateway(
        profile,
        str(workspace),
        session_id=attempt.session_id,
        run_id=attempt.run_id,
        task_id=attempt.external_task_id,
        capability_scope=paths,
        capability_token=token,
    )
    policy = create_director_effect_policy_snapshot_port(str(workspace))
    fences = create_directed_effect_fence_ports()
    mutation = create_director_directed_effect_mutation_port(
        workspace=str(workspace), policy_snapshot_port=policy, fence_consume_port=fences.consume
    )
    dependencies = DirectedEffectRuntimeDependenciesV1(
        policy_snapshot_port=policy, fence_admin_port=fences.admin, mutation_port=mutation
    )

    class Runtime:
        def directed_effect_policy_guard(self, port: Any) -> DirectedEffectPolicyGuard:
            assert port is policy
            return DirectedEffectPolicyGuard(gateway, port)

        async def __call__(self, *args: Any, **kwargs: Any) -> Any:
            raise AssertionError("legacy physical fallback is forbidden")

    events: list[Any] = []

    def executor() -> ToolBatchExecutor:
        return ToolBatchExecutor(
            tool_runtime=Runtime(),
            config=TransactionConfig(
                role_id="director",
                workspace=str(workspace),
                run_id=attempt.run_id,
                task_id=attempt.external_task_id,
                mutation_guard_mode="strict",
            ),
            emit_event=events.append,
            guard_assert_single_tool_batch=lambda **_: None,
            finalization_handler=object(),
            handoff_handler=object(),
            directed_effect_runtime=dependencies,
            directed_effect_required=True,
            directed_effect_execution_attempt=attempt,
            directed_effect_execution_attempt_authority=authority,
            capability_token=token,
            execution_envelope_hash=token["execution_envelope_hash"],
        )

    return SimpleNamespace(workspace=workspace, executor=executor, attempt=attempt, events=events)


def _decision(chain: Any, calls: list[tuple[str, str, dict[str, Any]]], *, turn: str = "turn") -> dict[str, Any]:
    invocations = [
        ToolInvocation.model_validate(
            {
                "call_id": call_id,
                "raw_tool_name": tool,
                "tool_name": tool,
                "arguments": normalize_tool_arguments(tool, arguments),
                "native_arguments": arguments,
            }
        )
        for call_id, tool, arguments in calls
    ]
    return {
        "turn_id": turn,
        "metadata": {
            "workspace": str(chain.workspace),
            "run_id": chain.attempt.run_id,
            "task_id": chain.attempt.external_task_id,
        },
        "finalize_mode": "none",
        "tool_batch": ToolBatch(batch_id="batch-" + turn, invocations=invocations, serial_writes=invocations),
    }


async def _execute(chain: Any, decision: dict[str, Any], *, executor: Any = None) -> dict[str, Any]:
    turn = decision["turn_id"]
    state = TurnStateMachine(turn_id=turn)
    for phase in (
        TurnState.CONTEXT_BUILT,
        TurnState.DECISION_REQUESTED,
        TurnState.DECISION_RECEIVED,
        TurnState.DECISION_DECODED,
    ):
        state.transition_to(phase)
    return await (executor or chain.executor()).execute_tool_batch(
        decision,
        state,
        TurnLedger(turn_id=turn),
        [{"role": "user", "content": "Modify owned.txt only. Target file: owned.txt. Allowed scope: owned.txt."}],
        stream=False,
    )


def _operations(chain: Any) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for path in (chain.workspace / ".polaris/runtime/events").glob("deo_operation*.jsonl")
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def _commits(chain: Any) -> list[dict[str, Any]]:
    return [row for row in _operations(chain) if row["event_type"].endswith(".receipt_committed")]


@pytest.mark.asyncio
async def test_two_partial_edits_commit_in_order_with_fresh_preimage(tmp_path, monkeypatch):
    chain = _real_chain(tmp_path, monkeypatch)
    result = await _execute(
        chain,
        _decision(
            chain,
            [
                ("a", "edit_blocks", {"file": "owned.txt", "blocks": _block("beta", "BETA")}),
                ("b", "edit_blocks", {"file": "owned.txt", "blocks": _block("delta", "DELTA")}),
            ],
        ),
    )
    assert (chain.workspace / "owned.txt").read_text(encoding="utf-8") == "alpha\nBETA\ngamma\nDELTA\n"
    rows = result["batch_receipt"]["results"]
    assert [row["call_id"] for row in rows] == ["a", "b"]
    assert all(row["result"]["effect_receipt_commit"]["state"] == "RECEIPT_COMMITTED" for row in rows)
    assert rows[1]["result"]["before_sha256"] == rows[0]["result"]["after_sha256"]
    assert len(_commits(chain)) == 2
    assert any(row["payload"]["state"] == "CLOSED_BY_PARENT" for row in _operations(chain))


@pytest.mark.asyncio
async def test_cold_executor_cannot_replay_committed_partial_batch(tmp_path, monkeypatch):
    chain = _real_chain(tmp_path, monkeypatch)
    decision = _decision(
        chain,
        [
            ("a", "edit_blocks", {"file": "owned.txt", "blocks": _block("beta", "BETA")}),
            ("b", "edit_blocks", {"file": "owned.txt", "blocks": _block("delta", "DELTA")}),
        ],
    )
    await _execute(chain, decision)
    before = (chain.workspace / "owned.txt").read_bytes()
    with pytest.raises(RuntimeError):
        await _execute(chain, decision, executor=chain.executor())
    assert (chain.workspace / "owned.txt").read_bytes() == before
    assert len(_commits(chain)) == 2


@pytest.mark.asyncio
async def test_receipt_observation_loss_stops_next_cohort_and_cold_replay(tmp_path, monkeypatch):
    chain = _real_chain(tmp_path, monkeypatch)
    decision = _decision(
        chain,
        [
            ("a", "edit_blocks", {"file": "owned.txt", "blocks": _block("beta", "BETA")}),
            ("b", "edit_blocks", {"file": "owned.txt", "blocks": _block("delta", "DELTA")}),
        ],
    )
    current = chain.executor()

    def unavailable(*args, **kwargs):
        raise OSError("injected observation loss after real first commit")

    monkeypatch.setattr(current, "_committed_cohort_targets", unavailable)
    with pytest.raises(RuntimeError, match="observation loss"):
        await _execute(chain, decision, executor=current)
    assert len(_commits(chain)) == 1
    assert (chain.workspace / "owned.txt").read_text(encoding="utf-8") == "alpha\nBETA\ngamma\ndelta\n"
    with pytest.raises(RuntimeError):
        await _execute(chain, decision, executor=chain.executor())
    assert len(_commits(chain)) == 1


@pytest.mark.asyncio
async def test_explicit_empty_scope_never_defaults_to_owned_path(tmp_path, monkeypatch):
    chain = _real_chain(tmp_path, monkeypatch, scope=[])
    decision = _decision(chain, [("a", "edit_blocks", {"file": "owned.txt", "start": 2, "end": 2, "replace": ""})])
    try:
        result = await _execute(chain, decision)
    except RuntimeError:
        result = None
    assert (chain.workspace / "owned.txt").read_text(encoding="utf-8") == ORIGINAL
    assert _commits(chain) == []
    if result is not None:
        assert result["batch_receipt"]["failure_count"] == 1


@pytest.mark.asyncio
async def test_safe_whole_file_replacements_keep_final_write_optimization(tmp_path, monkeypatch):
    chain = _real_chain(tmp_path, monkeypatch)
    result = await _execute(
        chain,
        _decision(
            chain,
            [
                ("a", "write_file", {"file": "owned.txt", "content": "first\n"}),
                ("b", "write_file", {"file": "owned.txt", "content": "last\n"}),
            ],
        ),
    )
    assert (chain.workspace / "owned.txt").read_text(encoding="utf-8") == "last\n"
    assert len(_commits(chain)) == 1
    assert (
        next(row for row in result["batch_receipt"]["results"] if row["call_id"] == "a")["result"]["superseded"] is True
    )


@pytest.mark.asyncio
async def test_changed_prior_postimage_rejects_next_cohort_before_admission(tmp_path, monkeypatch):
    chain = _real_chain(tmp_path, monkeypatch)
    current = chain.executor()
    original = current._committed_cohort_targets

    def drift(*args, **kwargs):
        proof = original(*args, **kwargs)
        (chain.workspace / "owned.txt").write_text("foreign fixture change\n", encoding="utf-8")
        return proof

    monkeypatch.setattr(current, "_committed_cohort_targets", drift)
    decision = _decision(
        chain,
        [
            ("a", "edit_blocks", {"file": "owned.txt", "blocks": _block("beta", "BETA")}),
            ("b", "edit_blocks", {"file": "owned.txt", "blocks": _block("delta", "DELTA")}),
        ],
    )
    with pytest.raises(RuntimeError, match="dependent_preimage_mismatch"):
        await _execute(chain, decision, executor=current)
    assert len(_commits(chain)) == 1
    assert not any(row["payload"]["operation"]["tool_call_id"] == "b" for row in _operations(chain))


@pytest.mark.asyncio
async def test_failed_prewrite_never_admits_later_partial(tmp_path, monkeypatch):
    chain = _real_chain(tmp_path, monkeypatch)
    decision = _decision(
        chain,
        [
            ("a", "edit_blocks", {"file": "owned.txt", "blocks": "invalid block content"}),
            ("b", "edit_blocks", {"file": "owned.txt", "blocks": _block("delta", "DELTA")}),
        ],
    )
    with pytest.raises(RuntimeError, match="dependent_predecessor_not_committed"):
        await _execute(chain, decision)
    assert (chain.workspace / "owned.txt").read_text(encoding="utf-8") == ORIGINAL
    assert _commits(chain) == []
    assert not any(row["payload"]["operation"]["tool_call_id"] == "b" for row in _operations(chain))


@pytest.mark.asyncio
async def test_full_original_declaration_is_frozen_before_first_cohort(tmp_path, monkeypatch):
    chain = _real_chain(tmp_path, monkeypatch)
    decision = _decision(
        chain,
        [
            ("a", "edit_blocks", {"file": "owned.txt", "blocks": _block("beta", "BETA")}),
            ("b", "edit_blocks", {"file": "owned.txt", "blocks": _block("delta", "DELTA")}),
        ],
    )
    current = chain.executor()
    original = current._committed_cohort_targets

    def mutate_caller(*args, **kwargs):
        proof = original(*args, **kwargs)
        decision["tool_batch"].invocations[1].arguments["blocks"] = _block("delta", "WRONG")
        return proof

    monkeypatch.setattr(current, "_committed_cohort_targets", mutate_caller)
    await _execute(chain, decision, executor=current)
    assert (chain.workspace / "owned.txt").read_text(encoding="utf-8") == "alpha\nBETA\ngamma\nDELTA\n"
    assert len(_commits(chain)) == 2
