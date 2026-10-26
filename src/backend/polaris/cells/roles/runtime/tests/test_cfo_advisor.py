"""CFO role hosting retains tools, real queue progress and lifecycle."""

from __future__ import annotations

import importlib
import time
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.roles.runtime.internal import agent_runtime_base as base

TOOLS = [
    "set_global_budget",
    "allocate_budget",
    "check_budget",
    "record_usage",
    "get_budget_status",
    "get_usage_stats",
    "set_budget_limit",
]


def _advisor(tmp_path: Path) -> Any:
    owner = importlib.import_module("polaris.cells.roles.runtime.public.cfo_advisor")
    assert issubclass(owner.CFOAgent, base.RoleAgent)
    return owner.CFOAgent(str(tmp_path))


def _message(action: str, **values: Any) -> base.AgentMessage:
    return base.AgentMessage(
        id="input-" + action,
        type=base.MessageType.TASK,
        sender="fixture-sender",
        receiver="CFO",
        payload={"action": action, "task_id": "task-a", **values},
        timestamp="2026-01-02T03:04:05Z",
        correlation_id="older-correlation",
    )


def test_seven_cfo_tools_call_real_public_budget_operations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Explicitly unavailable shared backend runtime isolates the CFO tool set;
    # this fixture does not qualify host tool execution or its permissions.
    monkeypatch.setattr(base.RoleAgent, "_get_backend_tool_runtime", lambda self: None)
    actor = _advisor(tmp_path)
    public = importlib.import_module("polaris.cells.finops.budget_guard.public.service")
    assert isinstance(actor._budget_service, public.BudgetService)
    assert list(actor.toolbox.list_tools()) == TOOLS
    tool = actor.toolbox.call
    assert tool("set_global_budget", limit=100)["budget"]["limit"] == 100
    allocation = tool("allocate_budget", task_id="task-a", budget_type="general", limit=20)
    assert tool("check_budget", task_id="task-a", estimated_cost=21)["within_budget"] is False
    assert (
        tool("record_usage", task_id="task-a", agent_id="director", resource_type="tokens", amount=6)["usage"]["amount"]
        == 6
    )
    assert tool("get_budget_status", task_id="task-a")["budgets"][0]["used"] == 6
    assert tool("get_usage_stats", task_id="task-a")["totals"] == {"tokens": 6}
    assert tool("set_budget_limit", budget_id=allocation["budget"]["budget_id"], new_limit=8)["budget"]["limit"] == 8
    assert public.BudgetService(str(tmp_path)).check_budget("task-a", 3)["within_budget"] is False


def test_four_task_actions_preserve_result_payload_and_original_message_correlation(tmp_path: Path) -> None:
    actor = _advisor(tmp_path)
    messages = [
        _message("allocate_budget", budget_type="general", limit=20),
        _message("check_budget", estimated_cost=5),
        _message("record_usage", agent_id="director", resource_type="tokens", amount=6),
        _message("get_budget_status"),
    ]
    for incoming in messages:
        response = actor.handle_message(incoming)
        assert response.type is base.MessageType.RESULT
        assert response.sender == "CFO"
        assert response.receiver == incoming.sender
        assert response.correlation_id == incoming.id
        assert response.payload["role"] == "cfo"
        assert response.payload["action"] == incoming.payload["action"]
        assert response.payload["result"]["ok"] is True
    assert response.payload["result"]["budgets"][0]["used"] == 6
    unsupported = actor.handle_message(_message("unsupported"))
    assert unsupported.payload["result"] == {"ok": False, "error": "unsupported_action", "action": "unsupported"}
    ignored = _message("get_budget_status")
    ignored.type = base.MessageType.EVENT
    assert actor.handle_message(ignored) is None
    with pytest.raises(ValueError):
        actor.handle_message(_message("allocate_budget", limit="invalid"))


def test_run_cycle_advances_real_queue_and_ignores_non_task_without_response(tmp_path: Path) -> None:
    actor = _advisor(tmp_path)
    bus = base.InMemoryAgentBusPort()
    actor._message_queue = base.AgentBusProxy("CFO", bus=bus)
    sender = base.AgentBusProxy("fixture-sender", bus=bus)
    assert actor.run_cycle() is False
    incoming = _message("allocate_budget", limit=20)
    assert sender.send(incoming)
    assert actor.run_cycle() is True
    response = sender.receive()
    assert response.type is base.MessageType.RESULT
    assert response.correlation_id == incoming.id
    ignored = _message("get_budget_status")
    ignored.type = base.MessageType.EVENT
    assert sender.send(ignored)
    assert actor.run_cycle() is True
    assert sender.receive() is None
    assert actor.run_cycle() is False


def test_real_role_initialize_thread_queue_stop_cold_state_and_lazy_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(base.RoleAgent, "_get_backend_tool_runtime", lambda self: None)
    contexts: list[dict[str, Any]] = []

    class ContextMarker:
        def __init__(self, **values: Any) -> None:
            contexts.append(values)

    # Context factory substitution verifies laziness/argument handoff only;
    # ContextOS/model readiness is outside this deterministic advisor fixture.
    monkeypatch.setattr(base, "RoleContextCompressor", ContextMarker)
    actor = _advisor(tmp_path)
    owner = type(actor)
    for name in ("initialize", "start", "stop", "get_status", "memory", "message_queue", "context_compressor"):
        assert getattr(owner, name) is getattr(base.RoleAgent, name)
    assert all(
        getattr(actor, name) is None for name in ("_memory", "_toolbox", "_message_queue", "_context_compressor")
    )
    statuses: list[str] = []
    actor.register_callback("on_status_change", lambda status: statuses.append(status.value))
    actor.initialize()
    assert actor.get_status()["status"] == "idle"
    assert actor._context_compressor is None
    assert list(actor.toolbox.list_tools()) == TOOLS
    bus = base.InMemoryAgentBusPort()
    actor._message_queue = base.AgentBusProxy("CFO", bus=bus)
    sender = base.AgentBusProxy("fixture-sender", bus=bus)
    incoming = _message("allocate_budget", budget_type="general", limit=40)
    assert sender.send(incoming)
    try:
        actor.start()
        response = sender.receive(block=True, timeout=3.0)
        assert response is not None
        assert response.correlation_id == incoming.id
        deadline = time.monotonic() + 3.0
        while actor.get_state().total_tasks_processed < 1 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert actor.get_state().total_tasks_processed == 1
    finally:
        actor.stop(timeout=3.0)
    assert actor._thread is not None and not actor._thread.is_alive()
    assert actor.get_status()["status"] == "stopped"
    assert statuses == ["starting", "idle", "idle", "running", "stopping", "stopped", "stopped"]
    assert actor._context_compressor is None and contexts == []
    first = actor.context_compressor
    assert actor.context_compressor is first
    assert contexts == [{"workspace": str(tmp_path), "role_name": "CFO"}]
    reopened = _advisor(tmp_path)
    reopened.initialize()
    assert reopened.get_state().total_tasks_processed == 1
    assert reopened._tool_get_budget_status("task-a")["budgets"][0]["limit"] == 40


def test_existing_public_budget_capability_uses_real_lower_service_and_kfs(tmp_path: Path) -> None:
    from polaris.cells.finops.budget_guard.public.service import BudgetService
    from polaris.cells.roles.runtime.public import contracts
    from polaris.cells.roles.runtime.public.service import execute_role_capability_invocation

    spec = contracts.get_builtin_role_runtime_spec("architect")
    runtime = spec.instantiate(
        identity=contracts.RoleIdentity(
            role_id="architect",
            run_id="fixture-run",
            task_id="fixture-task",
            session_id="fixture-session",
            workspace=str(tmp_path),
            host_kind="headless",
        ),
        profile_binding=contracts.RoleProfileBinding(
            role_id="architect",
            profile_ref="roles.profile:architect",
            tool_policy_ref="roles.profile:architect:tool_policy",
            prompt_policy_ref="roles.profile:architect:prompt_policy",
            data_policy_ref="roles.profile:architect:data_policy",
            profile_fingerprint="fixture-profile",
        ),
        ledger_binding=contracts.RoleLedgerBinding(turn_ledger_ref="roles.kernel:turn-ledger:fixture-run"),
        policy_fingerprint="fixture-policy",
        capability_id="allocate_context_token_budget",
    )
    invocation = contracts.RoleCapabilityInvocation(
        invocation_id="fixture-budget-invocation",
        capability_id="allocate_context_token_budget",
        role_id="architect",
        command_contract="ReserveBudgetCommandV1",
        payload_ref=runtime.turn_context.typed_input_ref,
        fingerprint_ref=runtime.capability_fingerprint.fingerprint,
    )
    # No injected budget service: this takes the real fallback public endpoint.
    result = execute_role_capability_invocation(
        contracts.ExecuteRoleCapabilityInvocationCommandV1(
            runtime_object=runtime,
            invocation=invocation,
            payload={"scope_id": "actual-public-budget", "token_budget": 41},
        )
    )
    assert result.ok is True and result.allowed is True
    assert result.owner_cell == "finops.budget_guard"
    assert result.metadata["remaining_tokens"] == 41
    assert BudgetService(str(tmp_path)).get_budget_status("actual-public-budget")["budgets"][0]["limit"] == 41
    assert not (tmp_path / ".polaris/runtime/memory/CFO").exists()
