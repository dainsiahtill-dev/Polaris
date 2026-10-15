"""Bind local dispatch to the same invocation's real prepared tool surface.

Remote transport is replaced; request preparation, decode, both batch guards,
and the private RoleToolGateway read remain real. No Bench target is used.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.roles.kernel.internal.kernel.core import RoleExecutionKernel
from polaris.cells.roles.kernel.internal.kernel.transaction_factory import create_transaction_kernel
from polaris.cells.roles.kernel.internal.llm_caller.tool_helpers import build_native_tool_schemas
from polaris.cells.roles.kernel.internal.transaction.ledger import TurnLedger
from polaris.cells.roles.kernel.internal.transaction.task_contract_builder import (
    extract_allowed_tool_names_from_definitions,
)
from polaris.cells.roles.kernel.internal.turn_state_machine import TurnStateMachine
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
from polaris.cells.roles.kernel.tests.test_retry_tool_surface_convergence import (
    _native,
    _PreparedNativeProvider,
    _PrivateFileExecutor,
    _state,
)
from polaris.cells.roles.profile.public.service import RoleProfile, RoleToolPolicy, RoleTurnRequest

TOOLS = [
    "repo_read_head",
    "repo_read_slice",
    "repo_read_tail",
    "repo_read_around",
    "repo_tree",
    "glob",
    "file_exists",
    "repo_rg",
    "edit_blocks",
    "repo_apply_diff",
    "write_file",
    "search_replace",
    "edit_file",
    "append_to_file",
    "execute_command",
    "scout_probe",
    "read_file",
]


def _fixture(tmp_path: Path, *, exact_narrow: bool = False, mutation: bool = False) -> tuple[Any, ...]:
    (tmp_path / "src").mkdir()
    (tmp_path / "src/engine.ts").write_text("export const VALUE = 1;\n", encoding="utf-8")
    profile = RoleProfile(
        role_id="director",
        display_name="Director",
        description="Private invocation fixture",
        provider_id="provider-a",
        model="kimi-for-coding",
    )
    profile = replace(
        profile,
        tool_policy=RoleToolPolicy(
            whitelist=TOOLS,
            blacklist=[],
            allow_code_write=True,
            allow_command_execution=True,
            allow_file_delete=False,
            max_tool_calls_per_turn=30,
        ),
    )
    full = build_native_tool_schemas(profile)
    narrow = [d for d in full if d["function"]["name"] in {"edit_blocks", "write_file"}]
    assert len(full) == 17 and len(narrow) == 2
    native = _PreparedNativeProvider(tmp_path, [_native("read_file", "src/engine.ts", "native-invocation-read")])
    effects = _PrivateFileExecutor(tmp_path)
    message = (
        "Repair src/engine.ts and verify current source."
        if mutation
        else "Read src/engine.ts and report its current contents."
    )
    request = RoleTurnRequest(
        message=message,
        workspace=str(tmp_path),
        metadata={"turn_request_id": "invocation-private"},
        context_override={
            "target_files": ["src/engine.ts"],
            "allowed_read_paths": ["src/engine.ts"],
            "allowed_write_paths": ["src/engine.ts"],
            "_transaction_kernel_forced_tool_definitions": narrow if exact_narrow else full,
            "_transaction_kernel_forced_tool_choice": "auto",
            "_transaction_kernel_force_exact_tools": exact_narrow,
        },
    )
    kernel = RoleExecutionKernel.create_default(workspace=str(tmp_path), llm_invoker=native, tool_executor=effects)
    transaction = create_transaction_kernel(kernel, "director", profile, request)
    transaction.config.continuous_execution = True
    context = [{"role": "system", "content": "You are Director."}, {"role": "user", "content": message}]
    return transaction, native, effects, full, narrow, context, kernel


async def _run(
    transaction: Any, *, stream: bool, turn: str, context: list[dict], definitions: list[dict], choice: Any = None
) -> tuple[Any, TurnLedger]:
    ledger = TurnLedger(turn_id=turn)
    state = TurnStateMachine(turn_id=turn)
    if not stream:
        result = await transaction._execute_turn(turn, context, definitions, state, ledger, tool_choice_override=choice)
        return result, ledger

    # Real sync preparation supplies the materialized response to the actual
    # stream handler; only the remote streaming transport is a component seam.
    async def prepared_stream(messages: list[dict], tools: list[dict], call_ledger: Any, **kwargs: Any):
        response = await transaction._call_llm_for_decision(
            messages, tools, call_ledger, tool_choice_override=kwargs.get("tool_choice_override")
        )
        yield {"type": "_internal_materialize", "response": response}

    events = [
        event
        async for event in transaction._stream_orchestrator.execute_turn_stream(
            turn, context, definitions, state, ledger, call_llm_for_decision_stream=prepared_stream
        )
    ]
    return events, ledger


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_initial_handler_uses_effective_prepared_surface_once(tmp_path: Path, stream: bool) -> None:
    transaction, native, effects, _full, narrow, context, _kernel = _fixture(tmp_path)
    result, ledger = await _run(
        transaction, stream=stream, turn="invocation-primary", context=context, definitions=narrow
    )
    actual = extract_allowed_tool_names_from_definitions(native.prepared[0].request_options["tools"])
    assert len(actual) == 17 and "read_file" in actual
    assert effects.executed == [("read_file", {"file": "src/engine.ts"})]
    assert len(native.prepared) == 1
    assert len(ledger.invocation_batches) == 1
    assert result


@pytest.mark.asyncio
async def test_original_bootstrap_resolves_effective_surface_before_retry_context(tmp_path: Path) -> None:
    transaction, _native_provider, effects, _full, narrow, context, _kernel = _fixture(tmp_path, mutation=True)
    turn = "invocation-bootstrap"
    original = TurnDecision(
        turn_id=TurnId(turn),
        kind=TurnDecisionKind.TOOL_BATCH,
        visible_message="",
        tool_batch=ToolBatch(
            batch_id=BatchId(turn + "-batch"),
            invocations=[
                ToolInvocation(
                    call_id=ToolCallId("original-invocation-read"),
                    tool_name="read_file",
                    arguments={"file": "src/engine.ts"},
                )
            ],
        ),
        finalize_mode=FinalizeMode.NONE,
        domain="code",
        metadata={"workspace": str(tmp_path)},
    )
    # Establish the real original request surface before invoking its existing
    # contract-retry entry, exactly as in the sealed causal experiment.
    await transaction.llm_provider({"messages": context, "tools": narrow, "tool_choice": None})
    result = await transaction._retry_orchestrator.retry_tool_batch_after_contract_violation(
        turn_id=turn,
        context=context,
        tool_definitions=narrow,
        state_machine=_state(turn),
        ledger=TurnLedger(turn_id=turn),
        stream=False,
        original_decision=original,
    )
    assert result["batch_receipt"]["success_count"] >= 1
    assert effects.executed == [("read_file", {"file": "src/engine.ts"}), ("read_file", {"file": "src/engine.ts"})]
    assert len({r["call_id"] for r in result["batch_receipt"]["results"]}) == len(result["batch_receipt"]["results"])


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_larger_local_schema_does_not_authorize_absent_effective_read(tmp_path: Path, stream: bool) -> None:
    transaction, native, effects, full, _narrow, context, _kernel = _fixture(tmp_path, exact_narrow=True)
    with pytest.raises(RuntimeError, match="provider_tool_surface_violation"):
        await _run(transaction, stream=stream, turn="invocation-denied", context=context, definitions=full)
    assert len(native.prepared[0].request_options["tools"]) == 2
    assert not effects.executed


@pytest.mark.asyncio
async def test_next_call_named_choice_does_not_inherit_previous_auto_surface(tmp_path: Path) -> None:
    transaction, native, effects, full, narrow, context, _kernel = _fixture(tmp_path)
    await _run(transaction, stream=False, turn="invocation-auto", context=context, definitions=narrow)
    assert len(effects.executed) == 1
    with pytest.raises(RuntimeError, match="provider_tool_surface_violation"):
        await _run(
            transaction,
            stream=False,
            turn="invocation-named",
            context=context,
            definitions=full,
            choice={"type": "function", "function": {"name": "write_file"}},
        )
    assert len(effects.executed) == 1
    assert len(native.prepared) == 2


@pytest.mark.asyncio
async def test_surface_is_captured_before_provider_override_unwinds(tmp_path: Path) -> None:
    transaction, native, effects, _full, narrow, context, _kernel = _fixture(tmp_path)
    resolve = transaction._retry_orchestrator.resolve_tool_surface

    def invocation_surface(definitions: list[dict], choice: Any):
        # Model a transient invocation override ending when its provider returns.
        # The actual preparer still sees the immutable request's full surface.
        if native.prepared:
            return narrow, "auto"
        return resolve(definitions, choice)

    transaction._retry_orchestrator.resolve_tool_surface = invocation_surface
    await _run(transaction, stream=False, turn="invocation-frozen", context=context, definitions=narrow)
    assert len(native.prepared[0].request_options["tools"]) == 17
    assert effects.executed == [("read_file", {"file": "src/engine.ts"})]
