"""Real request preparation and transaction dispatch share retry tool authority."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.roles.kernel.internal.kernel.core import RoleExecutionKernel
from polaris.cells.roles.kernel.internal.kernel.transaction_factory import create_transaction_kernel
from polaris.cells.roles.kernel.internal.llm_caller.final_request_tool_surface import (
    assert_native_tool_call_in_final_request_surface,
)
from polaris.cells.roles.kernel.internal.llm_caller.request_preparer import LLMRequestPreparer
from polaris.cells.roles.kernel.internal.llm_caller.tool_helpers import build_native_tool_schemas
from polaris.cells.roles.kernel.internal.tool_gateway import ToolAuthorizationError
from polaris.cells.roles.kernel.internal.transaction.ledger import TurnLedger
from polaris.cells.roles.kernel.internal.turn_state_machine import TurnState, TurnStateMachine
from polaris.cells.roles.kernel.public.turn_contracts import (
    BatchId,
    FinalizeMode,
    ToolBatch,
    ToolCallId,
    ToolInvocation,
    TurnDecision,
    TurnDecisionKind,
    TurnId,
)
from polaris.cells.roles.profile.public.service import RoleProfile, RoleToolPolicy, RoleTurnRequest


class _PreparedNativeProvider:
    """Replace only remote transport; keep real final request assembly and guard."""

    def __init__(self, workspace: Path, calls: list[dict[str, Any]]) -> None:
        self.preparer = LLMRequestPreparer(workspace=str(workspace))
        self.calls = calls
        self.prepared: list[Any] = []
        self.final_prepared: list[Any] = []

    async def call_decision(self, *, profile: Any, system_prompt: str, context: Any, **_: Any) -> dict[str, Any]:
        prepared = await self.preparer._prepare_llm_request(
            profile=profile,
            system_prompt=system_prompt,
            context=context,
            temperature=0.2,
            max_tokens=4000,
            stream=False,
        )
        self.prepared.append(prepared)
        native_call = self.calls[0]
        assert_native_tool_call_in_final_request_surface(
            native_tool_call=native_call,
            active_request=prepared.ai_request,
            prepared=prepared,
        )
        return {
            "content": "",
            "tool_calls": [native_call],
            "native_tool_calls": [native_call],
            "model": "test",
            "usage": {},
        }

    async def call_finalization(self, *, profile: Any, system_prompt: str, context: Any, **_: Any) -> dict[str, Any]:
        prepared = await self.preparer._prepare_llm_request(
            profile=profile,
            system_prompt=system_prompt,
            context=context,
            temperature=0.2,
            max_tokens=4000,
            stream=False,
        )
        self.final_prepared.append(prepared)
        assert prepared.request_options.get("tool_choice", "none") == "none"
        assert not prepared.request_options.get("tools")
        return {
            "content": "Current-source observation recorded; implementation and verification remain pending.",
            "tool_calls": [],
            "model": "test",
            "usage": {},
        }


class _PrivateFileExecutor:
    """Physical private fixture port remains behind the real RoleToolGateway."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.executed: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, tool_name: str, args: dict[str, Any], *, context: Any = None) -> dict[str, Any]:
        self.executed.append((tool_name, dict(args)))
        file = str(args.get("file") or args.get("path") or "")
        path = self.workspace / file
        if tool_name in {"read_file", "repo_read_slice"}:
            return {"success": True, "file": file, "content": path.read_text(encoding="utf-8")}
        raise AssertionError(f"unexpected physical effect: {tool_name}")


def _native(tool: str, file: str, call_id: str) -> dict[str, Any]:
    return {"id": call_id, "type": "function", "function": {"name": tool, "arguments": json.dumps({"file": file})}}


def _kernel_fixture(tmp_path: Path, *, exact: bool = False) -> tuple[Any, Any, Any, Any, Any]:
    (tmp_path / "module.py").write_text("VALUE = 1\n" * 1600, encoding="utf-8")
    (tmp_path / "rules.test.py").write_text("assert True\n" * 1600, encoding="utf-8")
    profile = RoleProfile(
        role_id="director",
        display_name="Director",
        description="Private surface fixture",
        provider_id="provider-a",
        model="kimi-for-coding",
    )
    policy = RoleToolPolicy(
        whitelist=["edit_blocks", "write_file", "read_file", "repo_read_slice", "execute_command"],
        blacklist=[],
        allow_code_write=True,
        allow_command_execution=True,
        allow_file_delete=False,
        max_tool_calls_per_turn=30,
    )
    profile = replace(profile, tool_policy=policy)
    native = _PreparedNativeProvider(tmp_path, [_native("read_file", "rules.test.py", "native_read")])
    effects = _PrivateFileExecutor(tmp_path)
    definitions = build_native_tool_schemas(profile)
    narrow = [d for d in definitions if d["function"]["name"] != "read_file"]
    request = RoleTurnRequest(
        message="Repair module.py and rules.test.py, then verify the current source.",
        workspace=str(tmp_path),
        metadata={"turn_request_id": "tool-surface-private"},
        context_override={
            "target_files": ["module.py", "rules.test.py"],
            "allowed_write_paths": ["module.py", "rules.test.py"],
            "allowed_read_paths": ["module.py", "rules.test.py"],
            "_transaction_kernel_forced_tool_definitions": narrow,
            "_transaction_kernel_forced_tool_choice": "required",
            "_transaction_kernel_force_exact_tools": exact,
            "director_quality_repair": {"repair_target_files": ["module.py", "rules.test.py"]},
        },
    )
    kernel = RoleExecutionKernel.create_default(workspace=str(tmp_path), llm_invoker=native, tool_executor=effects)
    transaction = create_transaction_kernel(kernel, "director", profile, request)
    transaction.config.continuous_execution = True
    return transaction, native, effects, narrow, kernel


def _state(turn_id: str) -> TurnStateMachine:
    state = TurnStateMachine(turn_id=turn_id)
    for value in (
        TurnState.CONTEXT_BUILT,
        TurnState.DECISION_REQUESTED,
        TurnState.DECISION_RECEIVED,
        TurnState.DECISION_DECODED,
    ):
        state.transition_to(value)
    return state


@pytest.mark.asyncio
async def test_bootstrap_followup_executes_read_offered_by_real_final_request(tmp_path: Path) -> None:
    transaction, native, effects, narrow, _kernel = _kernel_fixture(tmp_path)
    turn_id = "surface-bootstrap"
    original = TurnDecision(
        turn_id=TurnId(turn_id),
        kind=TurnDecisionKind.TOOL_BATCH,
        visible_message="",
        tool_batch=ToolBatch(
            batch_id=BatchId("surface-bootstrap-original"),
            invocations=[
                ToolInvocation(
                    call_id=ToolCallId("original_read"),
                    tool_name="repo_read_slice",
                    arguments={"file": "module.py", "start_line": 1, "end_line": 20},
                ),
            ],
        ),
        finalize_mode=FinalizeMode.NONE,
        domain="code",
        metadata={},
    )
    result = await transaction._retry_orchestrator.retry_tool_batch_after_contract_violation(
        turn_id=turn_id,
        context=[
            {"role": "system", "content": "You are Director."},
            {"role": "user", "content": "Repair module.py and rules.test.py, then verify the current source."},
        ],
        tool_definitions=narrow,
        state_machine=_state(turn_id),
        ledger=TurnLedger(turn_id=turn_id),
        stream=False,
        original_decision=original,
    )
    assert result["kind"] == "tool_batch_with_receipt"
    assert any(tool == "read_file" and args.get("file") == "rules.test.py" for tool, args in effects.executed), (
        effects.executed,
        result,
    )
    assert any(
        item.get("tool_name") == "read_file" and item.get("status") == "success"
        for item in result["batch_receipt"]["results"]
    )
    assert len(native.prepared) == 1
    system = "\n".join(str(m["content"]) for m in native.prepared[0].messages if m["role"] == "system")
    assert "read_file" in system
    assert "INVALID retry outputs: read_file-only" not in system
    assert "Mandatory: include at least one write tool." not in system


@pytest.mark.asyncio
async def test_exact_surface_rejects_unoffered_read_before_any_physical_effect(tmp_path: Path) -> None:
    transaction, native, effects, narrow, _kernel = _kernel_fixture(tmp_path, exact=True)
    with pytest.raises(RuntimeError, match="provider_tool_surface_violation"):
        await transaction.llm_provider(
            {
                "messages": [
                    {"role": "system", "content": "You are Director."},
                    {"role": "user", "content": "Repair module.py."},
                ],
                "tools": narrow,
                "tool_choice": None,
            }
        )
    assert not effects.executed
    assert "read_file" not in {d["function"]["name"] for d in native.prepared[0].request_options["tools"]}


@pytest.mark.asyncio
async def test_named_choice_rejects_read_despite_companion_schema(tmp_path: Path) -> None:
    transaction, _native, effects, narrow, _kernel = _kernel_fixture(tmp_path)
    definitions, choice, allowed = transaction._retry_orchestrator._resolve_retry_tool_surface(
        narrow,
        {"type": "function", "function": {"name": "write_file"}},
    )
    assert allowed == {"write_file"}
    with pytest.raises(RuntimeError, match="requested=read_file; allowed=write_file"):
        await transaction.llm_provider(
            {
                "messages": [
                    {"role": "system", "content": "You are Director."},
                    {"role": "user", "content": "Repair module.py."},
                ],
                "tools": definitions,
                "tool_choice": choice,
            }
        )
    assert not effects.executed


@pytest.mark.asyncio
async def test_explicit_no_tools_survives_quality_repair_and_real_preparation(tmp_path: Path) -> None:
    transaction, native, effects, _narrow, _kernel = _kernel_fixture(tmp_path)
    definitions, choice, allowed = transaction._retry_orchestrator._resolve_retry_tool_surface([], "none")
    assert definitions == []
    assert choice == "none"
    assert allowed == set()
    await transaction.llm_provider(
        {
            "messages": [
                {"role": "system", "content": "You are Director."},
                {"role": "user", "content": "Record current observation."},
            ],
            "tools": definitions,
            "tool_choice": choice,
        }
    )
    assert not native.final_prepared[0].request_options.get("tools")
    assert not effects.executed


@pytest.mark.asyncio
async def test_schema_augmentation_does_not_grant_foreign_read_path(tmp_path: Path) -> None:
    transaction, _native, effects, _narrow, _kernel = _kernel_fixture(tmp_path)
    with pytest.raises(ToolAuthorizationError):
        await transaction.tool_runtime("read_file", {"file": "../outside.py"})
    assert not effects.executed


@pytest.mark.asyncio
async def test_registered_read_alias_is_scoped_before_physical_dispatch(tmp_path: Path) -> None:
    transaction, _native, effects, _narrow, _kernel = _kernel_fixture(tmp_path)
    ledger = TurnLedger(turn_id="surface-alias")
    original = ToolBatch(
        batch_id=BatchId("surface-alias-read"),
        invocations=[
            ToolInvocation(
                call_id=ToolCallId("alias-read"), raw_tool_name="cat", tool_name="cat", arguments={"path": "module.py"}
            ),
        ],
    )
    receipt = await transaction._retry_orchestrator.execute_read_bootstrap_batch(
        turn_id="surface-alias",
        workspace=str(tmp_path),
        tool_batch=original,
        ledger=ledger,
    )
    assert receipt is not None
    assert receipt["results"][0]["status"] == "success"
    assert effects.executed[0][0] == "read_file"
    assert (effects.executed[0][1].get("file") or effects.executed[0][1].get("path")) == "module.py"


@pytest.mark.parametrize("contract_key", ["tool_contract", "platform_tool_contract"])
def test_continuous_retry_preserves_typed_contract_and_explicit_single_batch(contract_key: str) -> None:
    from polaris.cells.roles.kernel.internal.transaction.retry_context_builders import (
        build_contract_retry_context,
        build_retry_write_after_bootstrap_context,
    )

    contract = {"single_batch": True, "required_tools": ["write_file"]}
    context: list[dict[str, Any]] = [
        {"role": "system", "content": "You are Director."},
        {"role": "user", "content": "Repair module.py", "metadata": {contract_key: contract}},
    ]
    definitions = [{"type": "function", "function": {"name": "write_file"}}]
    retry = build_contract_retry_context(context, definitions, continuous_execution=True)
    bootstrap = build_retry_write_after_bootstrap_context(
        original_context=context,
        bootstrap_receipt={"results": []},
        forced_write_tool_name="write_file",
        continuous_execution=True,
        callable_tool_names=["write_file"],
    )
    assert retry[-1]["metadata"][contract_key] == contract
    assert bootstrap[-1]["metadata"][contract_key] == contract
    assert "Mandatory write tool: write_file." in bootstrap[0]["content"]
    assert "An observation-only turn is valid" not in retry[0]["content"]
