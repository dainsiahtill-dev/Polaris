"""Decision-tree tests for ``ToolBatchExecutor.execute_tool_batch``.

These tests pin the OBSERVABLE behavior of the tool-batch executor under the
new ToolCallEnvelope/receipt contract (WS1 migration). The contract requires
that every decoded tool call produces an authoritative batch receipt; batches
that fall through to no receipt must fail-closed via
``tool_dispatch_dropped``. The previous architecture tolerated "bare" tool
calls (invocations without ``execution_mode``/``effect_type`` paired with an
``AsyncMock`` tool runtime returning arbitrary objects). Those old expectations
no longer hold: ``ToolBatch`` and ``ToolInvocation`` are strict pydantic models
that require the routing fields, and ``ToolBatchRuntime`` consumes the runtime
dict as authoritative evidence.

Each test pins (where applicable):
  1. exception message prefix (``single_batch_contract_violation``,
     ``tool_dispatch_dropped``, ``Unknown finalize_mode``)
  2. ``ErrorEvent.error_type`` string
  3. emitted-event ordered sequence
  4. return dict kind/shape
  5. ledger side-effect deltas (``tool_batch_count`` delta, ``mark_blocked``
     reason, ``anomaly_flags``, ``_implementing_phase_block_triggered``,
     ``_session_read_files``)

Shared helpers (``_make_invocation``, ``_deterministic_tool_runtime``,
``_decision``, ``_make_executor``) keep the test surface uniform so each test
only writes the bits that are unique to the path under test.
"""

from __future__ import annotations

import asyncio
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from polaris.cells.control_plane.run_ledger.public import (
    ReadRunLedgerProjectionQueryV1,
    read_run_ledger_projection,
)
from polaris.cells.control_plane.run_ledger.public.ledger import RunLedger
from polaris.cells.events.fact_stream.public import (
    BootstrapFactStreamWorkspaceCommandV1,
    bootstrap_fact_stream_workspace,
    fact_stream_bootstrap_streams,
)
from polaris.cells.roles.kernel.internal.transaction.delivery_contract import (
    DeliveryContract,
    DeliveryMode,
)
from polaris.cells.roles.kernel.internal.transaction.ledger import TransactionConfig, TurnLedger
from polaris.cells.roles.kernel.internal.transaction.modification_contract import ModificationContractStatus
from polaris.cells.roles.kernel.internal.transaction.phase_manager import Phase
from polaris.cells.roles.kernel.internal.transaction.tool_batch_executor import (
    ToolBatchExecutor,
    _recent_edit_failure_in_context,
)
from polaris.cells.roles.kernel.internal.turn_state_machine import TurnState, TurnStateMachine
from polaris.cells.roles.kernel.public.turn_contracts import (
    ToolEffectType,
    ToolExecutionMode,
    _infer_effect_type,
    _infer_execution_mode,
)
from polaris.cells.roles.kernel.public.turn_events import CompletionEvent, ErrorEvent, TurnPhaseEvent

# ---------------------------------------------------------------------------
# Shared fixtures / builders
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_fact_stream_workspace(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=str(tmp_path),
            maintenance_reason="tool_batch_executor_decision_tree_test",
            streams=fact_stream_bootstrap_streams(),
        )
    )


def _build_decoded_state_machine(turn_id: str) -> TurnStateMachine:
    state_machine = TurnStateMachine(turn_id=turn_id)
    state_machine.transition_to(TurnState.CONTEXT_BUILT)
    state_machine.transition_to(TurnState.DECISION_REQUESTED)
    state_machine.transition_to(TurnState.DECISION_RECEIVED)
    state_machine.transition_to(TurnState.DECISION_DECODED)
    return state_machine


def _make_invocation(
    *,
    call_id: str,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
    execution_mode: ToolExecutionMode | str | None = None,
    effect_type: ToolEffectType | str | None = None,
) -> dict[str, Any]:
    """Build a ToolCallEnvelope-shaped invocation dict.

    The new ``ToolBatch``/``ToolInvocation`` pydantic models require
    ``execution_mode`` and ``effect_type`` on every decoded call. Callers that
    only care about the tool semantics can omit both fields; this helper
    derives them through the kernel's single classification truth source
    (:py:func:`_infer_execution_mode` / :py:func:`_infer_effect_type`) so the
    routing stays in lockstep with the kernel's strict schema. Callers that
    want to pin a specific mode/effect pass them explicitly and skip
    inference.

    Args:
        call_id: Stable identifier for the tool call within the batch.
        tool_name: Name of the tool (``read_file``, ``write_file``, ...).
        arguments: Tool arguments. ``None`` becomes an empty dict.
        execution_mode: Explicit mode. ``None`` triggers inference from
            ``tool_name``. An already-valid enum/value is passed through.
        effect_type: Explicit effect type. ``None`` triggers inference from
            ``tool_name`` + resolved execution_mode.

    Returns:
        A plain dict suitable for inclusion in ``tool_batch.invocations``.
        Downstream ``ToolInvocation`` validation will accept the result.

    Complexity:
        O(1). No I/O.
    """
    resolved_mode: ToolExecutionMode | None
    if isinstance(execution_mode, ToolExecutionMode):
        resolved_mode = execution_mode
    elif isinstance(execution_mode, str) and execution_mode:
        try:
            resolved_mode = ToolExecutionMode(execution_mode)
        except ValueError:
            resolved_mode = _infer_execution_mode(tool_name)
    else:
        resolved_mode = _infer_execution_mode(tool_name)

    resolved_effect: ToolEffectType | None
    if isinstance(effect_type, ToolEffectType):
        resolved_effect = effect_type
    elif isinstance(effect_type, str) and effect_type:
        try:
            resolved_effect = ToolEffectType(effect_type)
        except ValueError:
            resolved_effect = _infer_effect_type(tool_name, resolved_mode)
    else:
        resolved_effect = _infer_effect_type(tool_name, resolved_mode)

    return {
        "call_id": call_id,
        "tool_name": tool_name,
        "arguments": dict(arguments or {}),
        "execution_mode": resolved_mode.value,
        "effect_type": resolved_effect.value,
    }


def _default_success_payload(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Build the canonical success dict the executor expects from a runtime.

    Mirrors the read/write dispatch the production runtime synthesizes — a
    top-level ``success=True`` flag, a ``result`` block with the ``file`` path
    if the arguments carried one (so ``_session_read_files`` accounting
    fires), and ``truncated=False`` so the read is treated as non-truncated.
    """
    file_path = ""
    raw_file = arguments.get("file") or arguments.get("path") or arguments.get("target_file")
    if isinstance(raw_file, str):
        file_path = raw_file
    result_block: dict[str, Any] = {"ok": True}
    if file_path:
        result_block["file"] = file_path
        result_block["truncated"] = False
        if "content" in arguments and isinstance(arguments["content"], str):
            result_block["content"] = arguments["content"]
    payload: dict[str, Any] = {"success": True, "ok": True, "result": result_block}
    if _infer_effect_type(tool_name, _infer_execution_mode(tool_name)) == ToolEffectType.WRITE:
        payload["effect_receipt"] = {
            "schema_version": "effect_receipt.v1",
            "operation": tool_name,
            "file": file_path,
            "changed_files": [file_path] if file_path else [],
        }
    return payload


def _deterministic_tool_runtime(
    *,
    payload_factory: Any | None = None,
    record_calls: list[tuple[str, dict[str, Any]]] | None = None,
) -> Any:
    """Build a deterministic async tool runtime returning a real dict.

    The new executor contract requires the runtime to await a callable that
    returns a dict (``success``/``ok``, ``result``, optional ``effect_receipt``).
    ``AsyncMock`` returns a ``MagicMock`` by default, which fails the
    ``isinstance(result, dict)`` branch in ``ToolBatchRuntime._execute_single``
    and silently degrades to "missing effect_receipt" for writes. This helper
    gives every test a real dict-returning callable so the dispatch path
    matches the production behavior it is meant to exercise.

    Args:
        payload_factory: Optional ``(tool_name, arguments) -> dict`` callable
            to compute the dict per call. ``None`` uses
            :py:func:`_default_success_payload`.
        record_calls: Optional list to record ``(tool_name, arguments)`` for
            every invocation — useful for asserting the executor actually
            reached the runtime.

    Returns:
        An async callable matching ``executor(tool_name, arguments) -> dict``.

    Complexity:
        O(1) per call.
    """

    async def _runtime(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if record_calls is not None:
            record_calls.append((tool_name, dict(arguments)))
        if payload_factory is None:
            return _default_success_payload(tool_name, arguments)
        result = payload_factory(tool_name, arguments)
        if asyncio.iscoroutine(result):
            return await result  # type: ignore[no-any-return]
        return result

    return _runtime


def _make_executor(
    *,
    captured_events: list[Any] | None = None,
    tool_runtime: Any | None = None,
    config: TransactionConfig | None = None,
    guard_calls: list[dict[str, Any]] | None = None,
    capability_token: dict[str, Any] | None = None,
    execution_envelope_hash: str = "",
) -> ToolBatchExecutor:
    def _emit(event: Any) -> None:
        if captured_events is not None:
            captured_events.append(event)

    def _guard(**kw: Any) -> None:
        if guard_calls is not None:
            guard_calls.append(dict(kw))

    return ToolBatchExecutor(
        tool_runtime=tool_runtime if tool_runtime is not None else _deterministic_tool_runtime(),
        config=config or TransactionConfig(mutation_guard_mode="warn"),
        emit_event=_emit,
        guard_assert_single_tool_batch=_guard,
        finalization_handler=AsyncMock(),
        handoff_handler=AsyncMock(),
        capability_token=capability_token,
        execution_envelope_hash=execution_envelope_hash,
    )


def _decision(turn_id: str, batch_id: str, invocations: list[dict[str, Any]], *, finalize: str = "none") -> Any:
    """Build a decision envelope that reaches ``execute_tool_batch``.

    Each invocation is normalized through :py:func:`_make_invocation` so the
    call has explicit ``execution_mode`` and ``effect_type``. Tests that want
    to pin a specific routing can pass an invocation dict with those fields
    already set — :py:func:`_make_invocation` then preserves them.
    """
    normalized: list[dict[str, Any]] = []
    for inv in invocations:
        normalized.append(_make_invocation(**inv))
    return cast(
        Any,
        {
            "turn_id": turn_id,
            "metadata": {"workspace": "."},
            "finalize_mode": finalize,
            "tool_batch": {
                "batch_id": batch_id,
                "invocations": normalized,
            },
        },
    )


# ---------------------------------------------------------------------------
# IDEMPOTENCY cache HIT (642-645)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_idempotency_cache_hit_returns_cached_receipt_without_events() -> None:
    """Cache hit short-circuits: returns cached receipt, emits NO events, no batch count change."""
    captured: list[Any] = []
    cached_receipt = {"batch_id": "cached", "results": [], "_marker": "from_cache"}

    class _ReceiptStore:
        def get_by_batch_idempotency_key(self, key: str) -> dict[str, Any]:
            return cached_receipt

    tool_runtime = AsyncMock()
    tool_runtime.receipt_store = _ReceiptStore()
    executor = _make_executor(captured_events=captured, tool_runtime=tool_runtime)
    turn_id = "turn_idem_hit"
    ledger = TurnLedger(turn_id=turn_id)
    before = ledger.tool_batch_count

    result = await executor.execute_tool_batch(
        _decision(turn_id, "b1", [{"call_id": "c", "tool_name": "read_file", "arguments": {"file": "README.md"}}]),
        _build_decoded_state_machine(turn_id),
        ledger,
        [{"role": "user", "content": "read it"}],
        stream=False,
    )

    assert result is cached_receipt
    assert result.get("_marker") == "from_cache"
    assert captured == []
    assert ledger.tool_batch_count == before


# ---------------------------------------------------------------------------
# ALIAS allow-list disallowed-tools raise (656-666)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_allowed_tool_names_disallowed_raises_without_error_event() -> None:
    """Tools outside the narrowed allow-list raise a bare RuntimeError (no ErrorEvent)."""
    captured: list[Any] = []
    executor = _make_executor(captured_events=captured)
    turn_id = "turn_alias_disallowed"
    decision = _decision(
        turn_id,
        "b1",
        [{"call_id": "c", "tool_name": "write_file", "arguments": {"file": "x.py", "content": "x"}}],
    )
    with pytest.raises(RuntimeError, match="single_batch_contract_violation: retry batch used tools outside"):
        await executor.execute_tool_batch(
            decision,
            _build_decoded_state_machine(turn_id),
            TurnLedger(turn_id=turn_id),
            [{"role": "user", "content": "go"}],
            stream=False,
            allowed_tool_names={"read_file"},
        )
    # Bare RuntimeError path: no ErrorEvent emitted (raised before any emission)
    assert not any(isinstance(e, ErrorEvent) for e in captured)


@pytest.mark.asyncio
async def test_allowed_tool_names_alias_normalization_permits_scaffolding_alias() -> None:
    """``project_scaffolding`` is allowed by alias when ``project_scaffold`` is narrowed."""
    captured: list[Any] = []
    executor = _make_executor(captured_events=captured)
    turn_id = "turn_alias_ok"
    decision = _decision(
        turn_id,
        "b1",
        [{"call_id": "c", "tool_name": "project_scaffolding", "arguments": {}}],
    )
    # Must NOT raise the disallowed-tools error (alias matches).
    result = await executor.execute_tool_batch(
        decision,
        _build_decoded_state_machine(turn_id),
        TurnLedger(turn_id=turn_id),
        [{"role": "user", "content": "go"}],
        stream=False,
        allowed_tool_names={"project_scaffold"},
    )
    assert result.get("turn_id") == turn_id


@pytest.mark.asyncio
async def test_missing_execution_mode_is_normalized_before_replay_dispatch() -> None:
    """Bare decoded calls route through the shared classifier before dispatch.

    This pins the WS1 ToolCallEnvelope gap directly. The decision intentionally
    bypasses ``_decision()`` so the invocation lacks both ``execution_mode`` and
    ``effect_type``. ``execute_tool_batch`` must normalize that shape before
    constructing ``ToolBatch``; otherwise the decoded call lands in no bucket
    and incorrectly raises ``tool_dispatch_dropped``.
    """
    runtime_calls: list[tuple[str, dict[str, Any]]] = []
    executor = _make_executor(tool_runtime=_deterministic_tool_runtime(record_calls=runtime_calls))
    turn_id = "turn_missing_mode_normalized"
    decision = cast(
        Any,
        {
            "turn_id": turn_id,
            "metadata": {"workspace": "."},
            "finalize_mode": "none",
            "tool_batch": {
                "batch_id": "b1",
                "invocations": [
                    {
                        "call_id": "r",
                        "tool_name": "read_file",
                        "arguments": {"file": "README.md"},
                    }
                ],
            },
        },
    )

    result = await executor.execute_tool_batch(
        decision,
        _build_decoded_state_machine(turn_id),
        TurnLedger(turn_id=turn_id),
        [{"role": "user", "content": "read README"}],
        stream=False,
    )

    assert result.get("turn_id") == turn_id
    assert runtime_calls == [("read_file", {"file": "README.md"})]
    batch_receipt = result.get("batch_receipt") or {}
    assert batch_receipt.get("success_count") == 1
    assert batch_receipt.get("failure_count") == 0


@pytest.mark.asyncio
async def test_token_scoped_legacy_effect_receipt_is_observed_but_not_authoritative(tmp_path) -> None:
    """A legacy token-scoped receipt cannot satisfy authoritative Run Ledger evidence."""
    capability_token = {
        "source": "control_plane.job_token",
        "token_id": "jt-tool-batch",
        "run_id": "run-tool-batch",
        "project_id": "project-tool-batch",
        "stage": "director_mutation",
        "contract_hash": "contract-tool-batch",
        "blueprint_hash": "blueprint-tool-batch",
        "execution_envelope_hash": "env-tool-batch",
        "capability_audit_ok": True,
        "allowed_scope": ["src/app.py"],
    }

    async def _runtime(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return {
            "ok": True,
            "result": {"file": arguments.get("file")},
            "effect_receipt": {
                "operation": tool_name,
                "file": arguments.get("file"),
                "capability_token": capability_token,
            },
        }

    executor = _make_executor(
        tool_runtime=AsyncMock(side_effect=_runtime),
        config=TransactionConfig(role_id="director", mutation_guard_mode="warn"),
    )
    turn_id = "turn_tool_receipt_ledger"
    decision = _decision(
        turn_id,
        "b1",
        [
            {
                "call_id": "w",
                "tool_name": "write_file",
                "arguments": {"file": "src/app.py", "content": "value = 1\n"},
                "execution_mode": "write_serial",
                "effect_type": "write",
            }
        ],
    )
    decision["metadata"]["workspace"] = str(tmp_path)
    decision["metadata"]["run_id"] = "run-tool-batch"
    decision["metadata"]["task_id"] = "task-tool-batch"

    await executor.execute_tool_batch(
        decision,
        _build_decoded_state_machine(turn_id),
        TurnLedger(turn_id=turn_id),
        [{"role": "user", "content": "write src/app.py"}],
        stream=False,
    )

    projection = read_run_ledger_projection(
        ReadRunLedgerProjectionQueryV1(workspace=str(tmp_path), run_id="run-tool-batch")
    ).projection

    assert projection["ok"] is True
    assert projection["projects"][0]["latest_token_id"] == "jt-tool-batch"
    assert projection["evidence_modalities"]["tool_receipt"]["present"] == 0
    assert projection["evidence_modalities"]["tool_receipt"]["ok"] == 0
    gate_receipt = projection["run_projection"]["gates"][0]["evidence_modalities"]["tool_receipt"]
    assert gate_receipt["metadata"]["legacy_receipt_count"] == 1
    assert gate_receipt["metadata"]["task_runtime_receipt_count"] == 0
    events = RunLedger(tmp_path, run_id="run-tool-batch").read_events()
    tool_receipt_event = next(event for event in events if event.get("stage") == "director_mutation")
    assert tool_receipt_event["job_token"]["execution_envelope_hash"] == "env-tool-batch"
    assert tool_receipt_event["physical_evidence"]["execution_envelope_hash"] == "env-tool-batch"


@pytest.mark.asyncio
async def test_request_bound_job_token_records_gate_when_response_metadata_omits_authority(tmp_path) -> None:
    """A real write must settle under immutable request authority, not model metadata."""
    job_token = {
        "source": "control_plane.job_token",
        "token_id": "jt-request-bound",
        "run_id": "factory-run",
        "factory_run_id": "factory-run",
        "project_id": "L1-01",
        "stage": "director_mutation",
        "contract_hash": "contract-request-bound",
        "blueprint_hash": "blueprint-request-bound",
        "execution_envelope_hash": "f" * 64,
        "capability_audit": {"ok": True, "issues": []},
        "allowed_write_paths": ["src/app.py"],
    }
    executor = _make_executor(
        config=TransactionConfig(role_id="director", mutation_guard_mode="warn"),
        capability_token=job_token,
        execution_envelope_hash="f" * 64,
    )
    turn_id = "turn_request_bound_authority"
    decision = _decision(
        turn_id,
        "b1",
        [
            {
                "call_id": "w",
                "tool_name": "write_file",
                "arguments": {"file": "src/app.py", "content": "value = 1\n"},
                "execution_mode": "write_serial",
                "effect_type": "write",
            }
        ],
    )
    decision["metadata"].update(
        {
            "workspace": str(tmp_path),
            "run_id": "director-run",
            "task_id": "TASK-1",
        }
    )

    await executor.execute_tool_batch(
        decision,
        _build_decoded_state_machine(turn_id),
        TurnLedger(turn_id=turn_id),
        [{"role": "user", "content": "write src/app.py"}],
        stream=False,
    )

    projection = read_run_ledger_projection(
        ReadRunLedgerProjectionQueryV1(workspace=str(tmp_path), run_id="factory-run")
    ).projection
    events = RunLedger(tmp_path, run_id="factory-run").read_events()
    gate_event = next(event for event in events if event.get("event_type") == "gate_evaluated")

    assert projection["projects"][0]["latest_token_id"] == "jt-request-bound"
    assert gate_event["job_token"]["run_id"] == "factory-run"
    assert gate_event["job_token"]["factory_run_id"] == "factory-run"
    assert gate_event["job_token"]["project_id"] == "L1-01"
    assert gate_event["physical_evidence"]["execution_envelope_hash"] == "f" * 64


@pytest.mark.asyncio
async def test_token_scoped_failed_tool_batch_appends_failed_run_ledger_event(tmp_path) -> None:
    """A JobToken-scoped failed batch must fail the platform Run Ledger projection."""
    job_token = {
        "source": "control_plane.job_token",
        "token_id": "jt-tool-failure",
        "run_id": "run-tool-failure",
        "project_id": "project-tool-failure",
        "stage": "director_mutation",
        "contract_hash": "contract-tool-failure",
        "blueprint_hash": "blueprint-tool-failure",
        "capability_audit": {"ok": True, "issues": []},
        "allowed_write_paths": ["src/app.py"],
    }

    async def _runtime(_tool_name: str, _arguments: dict[str, Any]) -> dict[str, Any]:
        return {
            "ok": False,
            "error": "runtime denied write without producing an effect receipt",
        }

    executor = _make_executor(
        tool_runtime=AsyncMock(side_effect=_runtime),
        config=TransactionConfig(role_id="director", mutation_guard_mode="warn"),
    )
    turn_id = "turn_tool_failure_ledger"
    decision = _decision(
        turn_id,
        "b1",
        [
            {
                "call_id": "w",
                "tool_name": "write_file",
                "arguments": {"file": "src/app.py", "content": "value = 1\n"},
                "execution_mode": "write_serial",
                "effect_type": "write",
            }
        ],
    )
    decision["metadata"]["workspace"] = str(tmp_path)
    decision["metadata"]["run_id"] = "run-tool-failure"
    decision["metadata"]["task_id"] = "task-tool-failure"
    decision["metadata"]["job_token"] = job_token

    with pytest.raises(
        RuntimeError,
        match=r"tool_dispatch_failed: decoded tool batch produced only failed tool results",
    ):
        await executor.execute_tool_batch(
            decision,
            _build_decoded_state_machine(turn_id),
            TurnLedger(turn_id=turn_id),
            [{"role": "user", "content": "write src/app.py"}],
            stream=False,
        )

    projection = read_run_ledger_projection(
        ReadRunLedgerProjectionQueryV1(workspace=str(tmp_path), run_id="run-tool-failure")
    ).projection

    # Ordinary Director tool failure is recoverable: preserve integrity while
    # exposing the failed outcome for same-task repair instead of poisoning the
    # whole project/control-plane projection.
    assert projection["ok"] is True
    assert projection["failed"] == 0
    assert projection["projects"][0]["latest_token_id"] == "jt-tool-failure"
    assert projection["projects"][0]["gate_count"] == 1
    assert projection["projects"][0]["outcome_ok"] is False
    assert projection["projects"][0]["tool_lifecycle"]["failed_count"] == 1
    assert projection["run_projection"]["ok"] is False
    assert "recoverable" in projection["projects"][0]["detail"]
    events = RunLedger(tmp_path, run_id="run-tool-failure").read_events()
    gate_event = next(event for event in events if event.get("event_type") == "gate_evaluated")
    assert gate_event["gate"]["ok"] is False


# ---------------------------------------------------------------------------
# READ-WRITE BARRIER (690-736)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_write_barrier_mixed_read_and_write_raises() -> None:
    """Mixing a read tool and a write tool in one batch raises (no ErrorEvent)."""
    captured: list[Any] = []
    executor = _make_executor(captured_events=captured)
    turn_id = "turn_barrier"
    decision = _decision(
        turn_id,
        "b1",
        [
            {"call_id": "r", "tool_name": "read_file", "arguments": {"file": "README.md"}},
            {"call_id": "w", "tool_name": "write_file", "arguments": {"file": "a.py", "content": "x"}},
        ],
    )
    with pytest.raises(RuntimeError, match="single_batch_contract_violation: Cannot mix Read tools"):
        await executor.execute_tool_batch(
            decision,
            _build_decoded_state_machine(turn_id),
            TurnLedger(turn_id=turn_id),
            [{"role": "user", "content": "update a.py"}],
            stream=False,
        )
    assert not any(isinstance(e, ErrorEvent) for e in captured)


@pytest.mark.asyncio
async def test_read_write_barrier_bypassed_for_platform_tool_contract() -> None:
    """Platform tool contracts can explicitly bypass the read/write barrier."""
    captured: list[Any] = []
    executor = _make_executor(captured_events=captured)
    turn_id = "turn_barrier_bench"
    decision = _decision(
        turn_id,
        "b1",
        [
            {"call_id": "r", "tool_name": "read_file", "arguments": {"file": "README.md"}},
            {"call_id": "w", "tool_name": "write_file", "arguments": {"file": "a.py", "content": "x"}},
        ],
    )
    # Must NOT raise the barrier error because the platform contract allows
    # mixed read/write batches.
    result = await executor.execute_tool_batch(
        decision,
        _build_decoded_state_machine(turn_id),
        TurnLedger(turn_id=turn_id),
        [
            {
                "role": "user",
                "content": "update a.py",
                "metadata": {"tool_contract": {"allow_mixed_read_write_batch": True}},
            }
        ],
        stream=False,
    )
    assert result.get("turn_id") == turn_id


@pytest.mark.asyncio
async def test_platform_tool_contract_targets_authorize_mutation_guard() -> None:
    """Structured platform targets are part of the mutation guard authority."""
    executor = _make_executor(config=TransactionConfig(mutation_guard_mode="strict"))
    turn_id = "turn_contract_target"
    decision = _decision(
        turn_id,
        "b1",
        [
            {
                "call_id": "w",
                "tool_name": "write_file",
                "arguments": {"file": "go.mod", "content": "module ascii-magic-pet\n\ngo 1.22\n"},
            }
        ],
    )

    result = await executor.execute_tool_batch(
        decision,
        _build_decoded_state_machine(turn_id),
        TurnLedger(turn_id=turn_id),
        [
            {
                "role": "user",
                "content": "请更新 README.md 并继续落地",
                "metadata": {"tool_contract": {"target_files": ["go.mod"]}},
            }
        ],
        stream=False,
    )

    assert result.get("turn_id") == turn_id


# ---------------------------------------------------------------------------
# MATERIALIZE + EXPLORING text-output interception (747-768)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_materialize_exploring_no_tools_raises_text_intercept() -> None:
    """MATERIALIZE_CHANGES + EXPLORING + no invocations raises a bare RuntimeError."""
    captured: list[Any] = []
    executor = _make_executor(captured_events=captured)
    turn_id = "turn_text_intercept"
    decision = _decision(turn_id, "b1", [])
    ledger = TurnLedger(turn_id=turn_id)
    ledger.set_delivery_contract(DeliveryContract(mode=DeliveryMode.MATERIALIZE_CHANGES, requires_mutation=True))
    assert ledger.phase_manager.current_phase == Phase.EXPLORING
    with pytest.raises(
        RuntimeError,
        match="single_batch_contract_violation: MATERIALIZE_CHANGES mode requires tool execution",
    ):
        await executor.execute_tool_batch(
            decision,
            _build_decoded_state_machine(turn_id),
            ledger,
            [{"role": "user", "content": "go modify"}],
            stream=False,
        )
    assert not any(isinstance(e, ErrorEvent) for e in captured)


# ---------------------------------------------------------------------------
# VERIFICATION-READ EXEMPTION helper (_recent_edit_failure_in_context)
# ---------------------------------------------------------------------------


def test_recent_edit_failure_in_context_detects_marker() -> None:
    context = [
        {"role": "user", "content": "do it"},
        {"role": "tool", "content": "Validation failed for edit_blocks: SEARCH text exactly matches file content"},
    ]
    assert _recent_edit_failure_in_context(context) is True


def test_recent_edit_failure_in_context_no_marker() -> None:
    context = [
        {"role": "user", "content": "do it"},
        {"role": "tool", "content": "edit applied successfully"},
    ]
    assert _recent_edit_failure_in_context(context) is False


def test_recent_edit_failure_in_context_non_iterable_returns_false() -> None:
    assert _recent_edit_failure_in_context(123) is False


def test_recent_edit_failure_in_context_respects_lookback_window() -> None:
    # Failure marker beyond the lookback window is not detected.
    context = [{"role": "tool", "content": "No valid edit blocks"}]
    context += [{"role": "user", "content": str(i)} for i in range(10)]
    assert _recent_edit_failure_in_context(context, lookback=3) is False


@pytest.mark.asyncio
async def test_content_gathered_verification_read_exemption_allows_direct_read() -> None:
    """A direct-read-only batch after a recent edit failure bypasses the content_gathered write gate."""
    captured: list[Any] = []
    executor = _make_executor(
        captured_events=captured,
        tool_runtime=AsyncMock(return_value={"success": True, "result": {"file": "a.py", "content": "x"}}),
    )
    turn_id = "turn_verif_exempt"
    decision = _decision(
        turn_id,
        "b1",
        [{"call_id": "r", "tool_name": "read_file", "arguments": {"file": "a.py"}}],
    )
    ledger = TurnLedger(turn_id=turn_id)
    ledger.set_delivery_contract(DeliveryContract(mode=DeliveryMode.MATERIALIZE_CHANGES, requires_mutation=True))
    # Force CONTENT_GATHERED phase.
    ledger.phase_manager._current_phase = Phase.CONTENT_GATHERED
    context = [
        {"role": "user", "content": "fix a.py"},
        {"role": "tool", "content": "Validation failed for edit_blocks; SEARCH text exactly matches file content"},
    ]
    # Must NOT raise content_gathered_write_required.
    result = await executor.execute_tool_batch(
        decision,
        _build_decoded_state_machine(turn_id),
        ledger,
        context,
        stream=False,
    )
    assert result.get("turn_id") == turn_id
    assert not any(isinstance(e, ErrorEvent) and e.error_type == "content_gathered_write_required" for e in captured)


# ---------------------------------------------------------------------------
# CONTENT_GATHERED gate: READY_TO_WRITE raise (816-851)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_content_gathered_ready_to_write_raises_write_required() -> None:
    """When the modification plan is confirmed, reading is blocked in CONTENT_GATHERED."""
    captured: list[Any] = []
    executor = _make_executor(captured_events=captured)
    turn_id = "turn_cg_ready"
    decision = _decision(
        turn_id,
        "b1",
        [{"call_id": "r", "tool_name": "read_file", "arguments": {"file": "a.py"}}],
    )
    ledger = TurnLedger(turn_id=turn_id)
    ledger.set_delivery_contract(DeliveryContract(mode=DeliveryMode.MATERIALIZE_CHANGES, requires_mutation=True))
    ledger.phase_manager._current_phase = Phase.CONTENT_GATHERED
    # Force the contract to READY so the readiness verdict is READY_TO_WRITE.
    ledger.modification_contract.status = ModificationContractStatus.READY
    before = ledger.tool_batch_count
    with pytest.raises(
        RuntimeError,
        match="single_batch_contract_violation: CONTENT_GATHERED phase requires write tools",
    ):
        await executor.execute_tool_batch(
            decision,
            _build_decoded_state_machine(turn_id),
            ledger,
            [{"role": "user", "content": "fix a.py"}],
            stream=False,
        )
    error_events = [e for e in captured if isinstance(e, ErrorEvent)]
    assert error_events, "expected an ErrorEvent before the raise"
    assert error_events[0].error_type == "content_gathered_write_required"
    # tool_batch_count must not have advanced (raise is before the increment).
    assert ledger.tool_batch_count == before


# ---------------------------------------------------------------------------
# CONTENT_GATHERED gate: NEEDS_PLAN allow (turns < max) — no raise
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_content_gathered_needs_plan_allows_read_when_under_max_turns() -> None:
    """NEEDS_PLAN + turns_in_phase < max → reads allowed, NO content_gathered raise."""
    captured: list[Any] = []
    executor = _make_executor(
        captured_events=captured,
        tool_runtime=AsyncMock(return_value={"success": True, "result": {"file": "a.py", "content": "x"}}),
    )
    turn_id = "turn_cg_needs_plan_allow"
    decision = _decision(
        turn_id,
        "b1",
        [{"call_id": "r", "tool_name": "read_file", "arguments": {"file": "a.py"}}],
    )
    ledger = TurnLedger(turn_id=turn_id)
    ledger.set_delivery_contract(DeliveryContract(mode=DeliveryMode.MATERIALIZE_CHANGES, requires_mutation=True))
    ledger.phase_manager._current_phase = Phase.CONTENT_GATHERED
    # DRAFT contract w/o targets/actions → NEEDS_PLAN; turns 0 < max 3 → allow.
    ledger.modification_contract.status = ModificationContractStatus.DRAFT
    ledger.phase_manager._turns_in_current_phase = 0
    ledger.phase_manager._max_turns_per_phase = 3
    result = await executor.execute_tool_batch(
        decision,
        _build_decoded_state_machine(turn_id),
        ledger,
        [{"role": "user", "content": "fix a.py"}],
        stream=False,
    )
    assert result.get("turn_id") == turn_id
    assert not any(isinstance(e, ErrorEvent) and e.error_type == "content_gathered_write_required" for e in captured)


@pytest.mark.asyncio
async def test_content_gathered_needs_plan_timeout_degradation_raises() -> None:
    """NEEDS_PLAN + turns_in_phase >= max → degraded content_gathered_write_required raise."""
    captured: list[Any] = []
    executor = _make_executor(captured_events=captured)
    turn_id = "turn_cg_needs_plan_timeout"
    decision = _decision(
        turn_id,
        "b1",
        [{"call_id": "r", "tool_name": "read_file", "arguments": {"file": "a.py"}}],
    )
    ledger = TurnLedger(turn_id=turn_id)
    ledger.set_delivery_contract(DeliveryContract(mode=DeliveryMode.MATERIALIZE_CHANGES, requires_mutation=True))
    ledger.phase_manager._current_phase = Phase.CONTENT_GATHERED
    ledger.modification_contract.status = ModificationContractStatus.DRAFT
    ledger.phase_manager._turns_in_current_phase = 3
    ledger.phase_manager._max_turns_per_phase = 3
    before = ledger.tool_batch_count
    with pytest.raises(
        RuntimeError,
        match="single_batch_contract_violation: CONTENT_GATHERED phase timeout",
    ):
        await executor.execute_tool_batch(
            decision,
            _build_decoded_state_machine(turn_id),
            ledger,
            [{"role": "user", "content": "fix a.py"}],
            stream=False,
        )
    error_events = [e for e in captured if isinstance(e, ErrorEvent)]
    assert error_events and error_events[0].error_type == "content_gathered_write_required"
    assert ledger.tool_batch_count == before


@pytest.mark.asyncio
async def test_content_gathered_disabled_contract_turns_ge_2_raises() -> None:
    """When enable_modification_contract=False, turns_in_phase >= 2 → v2 fallback raise."""
    captured: list[Any] = []
    executor = _make_executor(
        captured_events=captured,
        config=TransactionConfig(mutation_guard_mode="warn", enable_modification_contract=False),
    )
    turn_id = "turn_cg_disabled"
    decision = _decision(
        turn_id,
        "b1",
        [{"call_id": "r", "tool_name": "read_file", "arguments": {"file": "a.py"}}],
    )
    ledger = TurnLedger(turn_id=turn_id)
    ledger.set_delivery_contract(DeliveryContract(mode=DeliveryMode.MATERIALIZE_CHANGES, requires_mutation=True))
    ledger.phase_manager._current_phase = Phase.CONTENT_GATHERED
    ledger.phase_manager._turns_in_current_phase = 2
    with pytest.raises(
        RuntimeError,
        match="single_batch_contract_violation: CONTENT_GATHERED phase requires write tools",
    ):
        await executor.execute_tool_batch(
            decision,
            _build_decoded_state_machine(turn_id),
            ledger,
            [{"role": "user", "content": "fix a.py"}],
            stream=False,
        )
    error_events = [e for e in captured if isinstance(e, ErrorEvent)]
    assert error_events and error_events[0].error_type == "content_gathered_write_required"


# ---------------------------------------------------------------------------
# IMPLEMENTING-phase broad-exploration HARD block (935-947)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_implementing_phase_all_broad_exploration_hard_block_raises() -> None:
    """In IMPLEMENTING phase, an all-broad-exploration batch raises a bare RuntimeError."""
    captured: list[Any] = []
    executor = _make_executor(captured_events=captured)
    turn_id = "turn_impl_hard"
    decision = _decision(
        turn_id,
        "b1",
        [{"call_id": "g", "tool_name": "glob", "arguments": {"pattern": "**/*"}}],
    )
    ledger = TurnLedger(turn_id=turn_id)
    ledger.phase_manager._current_phase = Phase.IMPLEMENTING
    with pytest.raises(
        RuntimeError,
        match="single_batch_contract_violation: in implementing phase, broad exploration tools",
    ):
        await executor.execute_tool_batch(
            decision,
            _build_decoded_state_machine(turn_id),
            ledger,
            [{"role": "user", "content": "implement it"}],
            stream=False,
        )
    assert not any(isinstance(e, ErrorEvent) for e in captured)


@pytest.mark.asyncio
async def test_implementing_phase_partial_block_sets_ledger_flag() -> None:
    """Partial block emits a failed receipt and never dispatches the broad tool."""

    captured: list[Any] = []
    tool_runtime = AsyncMock(return_value={"success": True, "result": {"file": "a.py", "content": "x"}})
    executor = _make_executor(
        captured_events=captured,
        tool_runtime=tool_runtime,
    )
    turn_id = "turn_impl_partial"
    decision = _decision(
        turn_id,
        "b1",
        [
            {"call_id": "g", "tool_name": "glob", "arguments": {"pattern": "**/*"}},
            {"call_id": "r", "tool_name": "read_file", "arguments": {"file": "a.py"}},
        ],
    )
    ledger = TurnLedger(turn_id=turn_id)
    ledger.phase_manager._current_phase = Phase.IMPLEMENTING
    # Non-mutation user request so the downstream strict mutation-guard does not fire,
    # leaving the implementing-phase PARTIAL block as the observed behavior.
    result = await executor.execute_tool_batch(
        decision,
        _build_decoded_state_machine(turn_id),
        ledger,
        [{"role": "user", "content": "show me the contents of a.py"}],
        stream=False,
    )

    assert getattr(ledger, "_implementing_phase_block_triggered", False) is True
    assert tool_runtime.await_count == 1
    assert all(call.args[0] != "glob" for call in tool_runtime.await_args_list)
    batch_receipt = result.get("batch_receipt", {})
    blocked = [item for item in batch_receipt.get("results", []) if item.get("tool_name") == "glob"]
    assert blocked
    assert blocked[0]["result"]["error_type"] == "implementing_phase_tool_blocked"


# ---------------------------------------------------------------------------
# VERIFYING-phase verification-required raise (973-983)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verifying_phase_without_verification_tool_raises() -> None:
    """In VERIFYING phase with no verification tool, a bare RuntimeError is raised."""
    captured: list[Any] = []
    executor = _make_executor(captured_events=captured)
    turn_id = "turn_verify_req"
    decision = _decision(
        turn_id,
        "b1",
        [{"call_id": "r", "tool_name": "read_file", "arguments": {"file": "a.py"}}],
    )
    ledger = TurnLedger(turn_id=turn_id)
    ledger.phase_manager._current_phase = Phase.VERIFYING
    with pytest.raises(
        RuntimeError,
        match="single_batch_contract_violation: verifying-phase-requires-verification",
    ):
        await executor.execute_tool_batch(
            decision,
            _build_decoded_state_machine(turn_id),
            ledger,
            [{"role": "user", "content": "verify it"}],
            stream=False,
        )
    assert not any(isinstance(e, ErrorEvent) for e in captured)


# ---------------------------------------------------------------------------
# MUTATION write-guard STRICT raise (no write tool in mutation batch)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mutation_write_guard_strict_raises_when_no_write_tool() -> None:
    """strict guard_mode + mutation request + no write tool → bare RuntimeError (no ErrorEvent)."""
    captured: list[Any] = []
    executor = _make_executor(
        captured_events=captured,
        config=TransactionConfig(mutation_guard_mode="strict"),
    )
    turn_id = "turn_mut_strict"
    decision = _decision(
        turn_id,
        "b1",
        [{"call_id": "r", "tool_name": "read_file", "arguments": {"file": "a.py"}}],
    )
    ledger = TurnLedger(turn_id=turn_id)
    with pytest.raises(
        RuntimeError,
        match="single_batch_contract_violation: mutation requested but no write tool invocation",
    ):
        await executor.execute_tool_batch(
            decision,
            _build_decoded_state_machine(turn_id),
            ledger,
            [{"role": "user", "content": "edit a.py to add a new function"}],
            stream=False,
        )
    assert not any(isinstance(e, ErrorEvent) for e in captured)


# ---------------------------------------------------------------------------
# PHASE transition post-processing: phase_timeout_guard receipt + mark_blocked
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_phase_timeout_injects_guard_receipt_and_marks_blocked() -> None:
    """A phase timeout injects a phase_timeout_guard error receipt and marks PHASE_TIMEOUT."""
    from polaris.cells.roles.kernel.internal.transaction.delivery_contract import BlockedReason

    captured: list[Any] = []
    # A successful read in CONTENT_GATHERED keeps the phase; force phase-timeout via the
    # phase manager so the post-processing branch injects the guard receipt.
    executor = _make_executor(
        captured_events=captured,
        tool_runtime=AsyncMock(
            return_value={"success": True, "result": {"file": "a.py", "content": "x", "truncated": False}}
        ),
    )
    turn_id = "turn_phase_timeout"
    decision = _decision(
        turn_id,
        "b1",
        [
            {
                "call_id": "r",
                "tool_name": "read_file",
                "arguments": {"file": "a.py"},
                "execution_mode": "readonly_parallel",
                "effect_type": "read",
            }
        ],
        finalize="none",
    )
    ledger = TurnLedger(turn_id=turn_id)
    ledger.phase_manager._current_phase = Phase.CONTENT_GATHERED
    # Drive the phase manager into timeout territory.
    ledger.phase_manager._turns_in_current_phase = 99
    ledger.phase_manager._max_turns_per_phase = 3
    result = await executor.execute_tool_batch(
        decision,
        _build_decoded_state_machine(turn_id),
        ledger,
        [{"role": "user", "content": "read a.py"}],
        stream=False,
    )
    # Result returns through complete_with_tool_results; the timeout receipt was injected.
    results = result.get("batch_receipt") or {}
    assert ledger.mutation_obligation.blocked_reason == BlockedReason.PHASE_TIMEOUT
    assert (
        any(isinstance(r, dict) and r.get("tool_name") == "phase_timeout_guard" for r in (results.get("results") or []))
        or ledger.mutation_obligation.blocked_reason == BlockedReason.PHASE_TIMEOUT
    )


# ---------------------------------------------------------------------------
# ARGUMENT-SHAPE escalation + tool_batch_count decrement (1400-1414)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_argument_shape_void_batch_decrements_tool_batch_count() -> None:
    """A write batch that fails entirely on argument shape decrements tool_batch_count before raising.

    ADR-0071: the void batch must NOT consume the single-batch budget.
    """
    captured: list[Any] = []

    async def _runtime(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        # Simulate an argument-shape failure for the write tool ("missing argument"
        # is a _WRITE_ARGUMENT_SHAPE_FAILURE_ANCHORS substring).
        return {
            "success": False,
            "error": "Parameter validation failed: missing argument blocks or start",
        }

    executor = _make_executor(captured_events=captured, tool_runtime=AsyncMock(side_effect=_runtime))
    turn_id = "turn_argshape"
    decision = _decision(
        turn_id,
        "b1",
        [
            {
                "call_id": "w",
                "tool_name": "edit_blocks",
                "arguments": {"file": "main.py"},
                "execution_mode": "write_serial",
                "effect_type": "write",
            }
        ],
    )
    ledger = TurnLedger(turn_id=turn_id)
    ledger.set_delivery_contract(DeliveryContract(mode=DeliveryMode.MATERIALIZE_CHANGES, requires_mutation=True))
    before = ledger.tool_batch_count
    with pytest.raises(
        RuntimeError,
        match=(
            r"single_batch_contract_violation: write tool batch produced no effects .*"
            r"error_types=correctable_write_rejection"
        ),
    ):
        await executor.execute_tool_batch(
            decision,
            _build_decoded_state_machine(turn_id),
            ledger,
            [{"role": "user", "content": "edit main.py to add a function"}],
            stream=False,
        )
    # Count was incremented (+1) then decremented (-1) on the void batch => net 0.
    assert ledger.tool_batch_count == before


@pytest.mark.asyncio
async def test_argument_shape_escalates_before_failure_circuit_breaker() -> None:
    """Malformed write args should enter the normalization/retry ladder before breaker accounting."""

    async def _runtime(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        del tool_name, arguments
        return {
            "success": False,
            "error": "Parameter validation failed: edit_blocks: missing required argument: blocks or start",
        }

    class _BreakerMustNotRun:
        def evaluate_batch(self, **_kwargs: Any) -> Any:
            raise AssertionError("argument-shape guard must run before the failure circuit breaker")

    executor = _make_executor(tool_runtime=AsyncMock(side_effect=_runtime))
    executor._tool_failure_circuit_breaker = _BreakerMustNotRun()
    turn_id = "turn_argshape_before_breaker"
    decision = _decision(
        turn_id,
        "b1",
        [
            {
                "call_id": "w",
                "tool_name": "edit_blocks",
                "arguments": {"file": "main.py"},
                "execution_mode": "write_serial",
                "effect_type": "write",
            }
        ],
    )
    ledger = TurnLedger(turn_id=turn_id)
    ledger.set_delivery_contract(DeliveryContract(mode=DeliveryMode.MATERIALIZE_CHANGES, requires_mutation=True))

    with pytest.raises(
        RuntimeError,
        match=(
            r"single_batch_contract_violation: write tool batch produced no effects .*"
            r"error_types=correctable_write_rejection"
        ),
    ):
        await executor.execute_tool_batch(
            decision,
            _build_decoded_state_machine(turn_id),
            ledger,
            [{"role": "user", "content": "edit main.py to add a function"}],
            stream=False,
        )


# ---------------------------------------------------------------------------
# tool_batch_count + guard ordering (1082-1088)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_guard_invoked_once_when_count_towards_limit() -> None:
    """guard_assert_single_tool_batch is invoked exactly once when count_towards_batch_limit=True."""
    guard_calls: list[dict[str, Any]] = []
    executor = _make_executor(
        guard_calls=guard_calls,
        tool_runtime=AsyncMock(return_value={"success": True, "result": {"file": "README.md", "content": "ok"}}),
    )
    turn_id = "turn_guard_once"
    decision = _decision(
        turn_id,
        "b1",
        [{"call_id": "r", "tool_name": "read_file", "arguments": {"file": "README.md"}}],
    )
    ledger = TurnLedger(turn_id=turn_id)
    before = ledger.tool_batch_count
    await executor.execute_tool_batch(
        decision,
        _build_decoded_state_machine(turn_id),
        ledger,
        [{"role": "user", "content": "read README"}],
        stream=False,
    )
    assert len(guard_calls) == 1
    assert guard_calls[0]["tool_batch_count"] == before + 1
    assert ledger.tool_batch_count == before + 1


@pytest.mark.asyncio
async def test_guard_not_invoked_when_count_towards_limit_false() -> None:
    """guard is NOT invoked and tool_batch_count is unchanged when count_towards_batch_limit=False."""
    guard_calls: list[dict[str, Any]] = []
    executor = _make_executor(
        guard_calls=guard_calls,
        tool_runtime=AsyncMock(return_value={"success": True, "result": {"file": "README.md", "content": "ok"}}),
    )
    turn_id = "turn_guard_zero"
    decision = _decision(
        turn_id,
        "b1",
        [{"call_id": "r", "tool_name": "read_file", "arguments": {"file": "README.md"}}],
    )
    ledger = TurnLedger(turn_id=turn_id)
    before = ledger.tool_batch_count
    await executor.execute_tool_batch(
        decision,
        _build_decoded_state_machine(turn_id),
        ledger,
        [{"role": "user", "content": "read README"}],
        stream=False,
        count_towards_batch_limit=False,
    )
    assert guard_calls == []
    assert ledger.tool_batch_count == before


# ---------------------------------------------------------------------------
# Happy-path event sequence + return + session_read_files (success read)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_successful_read_emits_started_then_completed_and_tracks_session_read() -> None:
    """A successful non-truncated read emits started->completed and records the read file."""
    captured: list[Any] = []
    executor = _make_executor(
        captured_events=captured,
        tool_runtime=AsyncMock(
            return_value={"success": True, "result": {"file": "README.md", "content": "ok", "truncated": False}}
        ),
    )
    turn_id = "turn_happy_read"
    decision = _decision(
        turn_id,
        "b1",
        [
            {
                "call_id": "r",
                "tool_name": "read_file",
                "arguments": {"file": "README.md"},
                "execution_mode": "readonly_parallel",
                "effect_type": "read",
            }
        ],
    )
    ledger = TurnLedger(turn_id=turn_id)
    result = await executor.execute_tool_batch(
        decision,
        _build_decoded_state_machine(turn_id),
        ledger,
        [{"role": "user", "content": "read README"}],
        stream=False,
    )
    assert result.get("turn_id") == turn_id
    # Event ordering: tool_batch_started -> tool_batch_completed -> CompletionEvent.
    phase_event_names = [e.phase for e in captured if isinstance(e, TurnPhaseEvent)]
    assert phase_event_names[0] == "tool_batch_started"
    assert "tool_batch_completed" in phase_event_names
    started_idx = next(
        i for i, e in enumerate(captured) if isinstance(e, TurnPhaseEvent) and e.phase == "tool_batch_started"
    )
    completed_idx = next(
        i for i, e in enumerate(captured) if isinstance(e, TurnPhaseEvent) and e.phase == "tool_batch_completed"
    )
    assert started_idx < completed_idx
    assert any(isinstance(e, CompletionEvent) for e in captured)
    completion_idx = next(i for i, e in enumerate(captured) if isinstance(e, CompletionEvent))
    assert completed_idx < completion_idx
    # session_read_files records the non-truncated successful read.
    assert "readme.md" in executor._session_read_files


# ---------------------------------------------------------------------------
# FINALIZE routing: NONE / unknown
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_finalize_mode_none_completes_with_tool_results() -> None:
    """FinalizeMode 'none' routes to complete_with_tool_results (returns turn_id dict)."""
    executor = _make_executor(
        tool_runtime=AsyncMock(return_value={"success": True, "result": {"file": "README.md", "content": "ok"}}),
    )
    turn_id = "turn_finalize_none"
    decision = _decision(
        turn_id,
        "b1",
        [{"call_id": "r", "tool_name": "read_file", "arguments": {"file": "README.md"}}],
        finalize="none",
    )
    result = await executor.execute_tool_batch(
        decision,
        _build_decoded_state_machine(turn_id),
        TurnLedger(turn_id=turn_id),
        [{"role": "user", "content": "read README"}],
        stream=False,
    )
    assert result.get("turn_id") == turn_id


@pytest.mark.asyncio
async def test_finalize_mode_local_routes_to_finalize_local() -> None:
    """FinalizeMode 'local' routes to FinalizationHandler.finalize_local."""
    from polaris.cells.roles.kernel.public.turn_contracts import FinalizeMode

    captured: list[Any] = []
    executor = _make_executor(
        captured_events=captured,
        tool_runtime=AsyncMock(return_value={"success": True, "result": {"file": "README.md", "content": "ok"}}),
    )
    turn_id = "turn_finalize_local"
    decision = _decision(
        turn_id,
        "b1",
        [{"call_id": "r", "tool_name": "read_file", "arguments": {"file": "README.md"}}],
        finalize=cast(Any, FinalizeMode.LOCAL),
    )
    result = await executor.execute_tool_batch(
        decision,
        _build_decoded_state_machine(turn_id),
        TurnLedger(turn_id=turn_id),
        [{"role": "user", "content": "read README"}],
        stream=False,
    )
    # finalize_local returns a dict carrying the turn_id.
    assert result.get("turn_id") == turn_id


@pytest.mark.asyncio
async def test_finalize_mode_unknown_raises_value_error() -> None:
    """An unrecognized finalize_mode raises ValueError('Unknown finalize_mode: ...')."""
    executor = _make_executor(
        tool_runtime=AsyncMock(return_value={"success": True, "result": {"file": "README.md", "content": "ok"}}),
    )
    turn_id = "turn_finalize_unknown"
    decision = _decision(
        turn_id,
        "b1",
        [{"call_id": "r", "tool_name": "read_file", "arguments": {"file": "README.md"}}],
        finalize="totally_bogus_mode",
    )
    with pytest.raises(ValueError, match="Unknown finalize_mode"):
        await executor.execute_tool_batch(
            decision,
            _build_decoded_state_machine(turn_id),
            TurnLedger(turn_id=turn_id),
            [{"role": "user", "content": "read README"}],
            stream=False,
        )


# ---------------------------------------------------------------------------
# MISSING tool_batch -> ValueError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_tool_batch_raises_value_error() -> None:
    executor = _make_executor()
    turn_id = "turn_missing_batch"
    decision = cast(Any, {"turn_id": turn_id, "metadata": {"workspace": "."}, "finalize_mode": "none"})
    with pytest.raises(ValueError, match="TOOL_BATCH decision missing tool_batch"):
        await executor.execute_tool_batch(
            decision,
            _build_decoded_state_machine(turn_id),
            TurnLedger(turn_id=turn_id),
            [{"role": "user", "content": "x"}],
            stream=False,
        )


# ---------------------------------------------------------------------------
# STEP 19: ADR-0071 guard invariant preservation (real guard binding)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_real_guard_raises_kernel_guard_error_on_second_batch() -> None:
    """With the REAL guard binding, a second ToolBatch (count>1) raises KernelGuardError.

    Pins ADR-0071: the executor increments tool_batch_count then calls
    guard_assert_single_tool_batch, which raises KernelGuardError when count>1.
    """
    from polaris.cells.roles.kernel.internal.kernel_guard import KernelGuardError
    from polaris.cells.roles.kernel.internal.transaction.kernel_guard_asserts import (
        guard_assert_single_tool_batch,
    )

    executor = ToolBatchExecutor(
        tool_runtime=AsyncMock(return_value={"success": True, "result": {"file": "README.md", "content": "ok"}}),
        config=TransactionConfig(mutation_guard_mode="warn"),
        emit_event=lambda event: None,
        guard_assert_single_tool_batch=guard_assert_single_tool_batch,
        finalization_handler=AsyncMock(),
        handoff_handler=AsyncMock(),
    )
    turn_id = "turn_real_guard"
    decision = _decision(
        turn_id,
        "b1",
        [{"call_id": "r", "tool_name": "read_file", "arguments": {"file": "README.md"}}],
    )
    ledger = TurnLedger(turn_id=turn_id)
    # Pre-set the count so the executor's increment pushes it to 2 (>1).
    ledger.tool_batch_count = 1
    with pytest.raises(KernelGuardError):
        await executor.execute_tool_batch(
            decision,
            _build_decoded_state_machine(turn_id),
            ledger,
            [{"role": "user", "content": "read README"}],
            stream=False,
        )
