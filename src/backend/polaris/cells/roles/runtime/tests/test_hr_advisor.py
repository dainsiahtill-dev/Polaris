"""Optional HR preserves actual RoleAgent hosting and deterministic config tools."""

from __future__ import annotations

import importlib
import time
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.llm.control_plane.tests.config_scenario import private_llm_config_scenario
from polaris.cells.roles.runtime.internal import agent_runtime_base as base

TOOLS = [
    "set_llm_config",
    "get_llm_config",
    "list_all_configs",
    "update_llm_config",
    "deactivate_config",
    "activate_config",
    "delete_config",
]


@pytest.fixture(autouse=True)
def _private_config(tmp_path: Path):
    with private_llm_config_scenario(tmp_path):
        yield


def _advisor(tmp_path: Path) -> Any:
    owner = importlib.import_module("polaris.cells.roles.runtime.public.hr_advisor")
    assert issubclass(owner.HRAgent, base.RoleAgent)
    return owner.HRAgent(str(tmp_path), config_dir=str(tmp_path / "private-home/config/llm"))


def _message(action: str, **values: Any) -> base.AgentMessage:
    return base.AgentMessage(
        id="input-" + action,
        type=base.MessageType.TASK,
        sender="fixture-sender",
        receiver="HR",
        payload={"action": action, "role": "pm", **values},
        timestamp="2026-01-02T03:04:05Z",
        correlation_id="older-correlation",
    )


def test_seven_hr_tools_call_real_low_public_crud_and_keep_raw_getters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Explicitly unavailable shared backend runtime isolates HR-specific tools;
    # it does not qualify real host tool execution/permission or provider calls.
    monkeypatch.setattr(base.RoleAgent, "_get_backend_tool_runtime", lambda self: None)
    actor = _advisor(tmp_path)
    public = importlib.import_module("polaris.cells.llm.control_plane.public.service")
    assert isinstance(actor._config_service, public.LLMConfigService)
    assert actor._resolve_provider_kind("fixture", "cli", {"command": "codex synthetic"}) == "codex"
    assert list(actor.toolbox.list_tools()) == TOOLS
    tool = actor.toolbox.call
    saved = tool(
        "set_llm_config",
        role="pm",
        provider_id="fixture",
        provider_type="cli",
        model="model",
        profile="profile",
        provider_cfg={"command": "codex synthetic", "api_key": "synthetic-key"},
    )
    assert saved["config"]["provider_cfg"]["api_key"] == "***"
    assert tool("get_llm_config", role="pm")["config"]["provider_kind"] == "codex"
    assert tool("list_all_configs")["count"] == 1
    updated = tool("update_llm_config", role="pm", model="updated")
    assert updated["config"]["model"] == "updated"
    assert tool("deactivate_config", role="pm")["config"]["is_active"] is False
    assert tool("activate_config", role="pm")["config"]["is_active"] is True
    assert actor.get_config("pm").provider_cfg["api_key"] == "synthetic-key"
    assert actor.get_all_configs()[0].model == "updated"
    assert tool("delete_config", role="pm")["deleted"] is True
    assert actor.get_config("pm") is None


def test_three_task_actions_preserve_result_payload_and_original_message_correlation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    actor = _advisor(tmp_path)
    incoming_messages = [
        _message(
            "set_config",
            provider_id="fixture",
            provider_type="cli",
            model="model",
            profile="profile",
            provider_cfg={"command": "codex synthetic"},
        ),
        _message("get_config"),
        _message("list_configs"),
    ]
    for incoming in incoming_messages:
        response = actor.handle_message(incoming)
        assert response.type is base.MessageType.RESULT
        assert response.sender == "HR" and response.receiver == incoming.sender
        assert response.correlation_id == incoming.id
        assert response.payload["role"] == "hr"
        assert response.payload["action"] == incoming.payload["action"]
        assert response.payload["result"]["ok"] is True
    assert response.payload["result"]["count"] == 1
    ignored = _message("get_config")
    ignored.type = base.MessageType.EVENT
    assert actor.handle_message(ignored) is None


def test_handle_message_catches_exceptions(tmp_path: Path) -> None:
    with private_llm_config_scenario(tmp_path, fail_config_reads=True):
        actor = _advisor(tmp_path)
        response = actor.handle_message(_message("get_config"))
        assert response.type is base.MessageType.RESULT
        result = response.payload["result"]
        assert result["ok"] is False and result["error"] == "internal_error"
        assert "store failure" in result["detail"]


def test_handle_message_unsupported_action(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    actor = _advisor(tmp_path)
    response = actor.handle_message(_message("unknown_action"))
    assert response.type is base.MessageType.RESULT
    assert response.payload["result"] == {"ok": False, "error": "unsupported_action", "action": "unknown_action"}


def test_run_cycle_advances_real_queue_and_ignores_non_task_without_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    actor = _advisor(tmp_path)
    bus = base.InMemoryAgentBusPort()
    actor._message_queue = base.AgentBusProxy("HR", bus=bus)
    sender = base.AgentBusProxy("fixture-sender", bus=bus)
    assert actor.run_cycle() is False
    incoming = _message("get_config")
    assert sender.send(incoming) and actor.run_cycle() is True
    response = sender.receive()
    assert response.correlation_id == incoming.id and response.payload["result"]["has_config"] is False
    ignored = _message("get_config")
    ignored.type = base.MessageType.EVENT
    assert sender.send(ignored) and actor.run_cycle() is True
    assert sender.receive() is None and actor.run_cycle() is False


def test_real_role_initialize_thread_queue_stop_cold_state_and_lazy_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(base.RoleAgent, "_get_backend_tool_runtime", lambda self: None)
    contexts: list[dict[str, Any]] = []

    class ContextMarker:
        def __init__(self, **values: Any) -> None:
            contexts.append(values)

    # Context factory substitution establishes laziness/arguments only, not
    # ContextOS readiness, provider execution or host-side tool containment.
    monkeypatch.setattr(base, "RoleContextCompressor", ContextMarker)
    actor = _advisor(tmp_path)
    for name in ("initialize", "start", "stop", "get_status", "memory", "message_queue", "context_compressor"):
        assert getattr(type(actor), name) is getattr(base.RoleAgent, name)
    assert all(
        getattr(actor, name) is None for name in ("_memory", "_toolbox", "_message_queue", "_context_compressor")
    )
    statuses: list[str] = []
    actor.register_callback("on_status_change", lambda status: statuses.append(status.value))
    actor.initialize()
    assert actor.get_status()["status"] == "idle"
    assert list(actor.toolbox.list_tools()) == TOOLS
    assert actor._context_compressor is None
    bus = base.InMemoryAgentBusPort()
    actor._message_queue = base.AgentBusProxy("HR", bus=bus)
    sender = base.AgentBusProxy("fixture-sender", bus=bus)
    incoming = _message(
        "set_config", provider_id="fixture", provider_type="ollama", model="saved", profile="p", provider_cfg={}
    )
    assert sender.send(incoming)
    try:
        actor.start()
        response = sender.receive(block=True, timeout=3.0)
        assert response is not None and response.correlation_id == incoming.id
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
    assert contexts == [{"workspace": str(tmp_path), "role_name": "HR"}]
    reopened = _advisor(tmp_path)
    reopened.initialize()
    assert reopened.get_state().total_tasks_processed == 1
    assert reopened.get_config("pm").model == "saved"
