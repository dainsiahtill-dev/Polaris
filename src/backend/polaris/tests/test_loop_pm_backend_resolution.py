from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from typing import Any

import pytest

BACKEND_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
for candidate in (BACKEND_ROOT,):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

from polaris.delivery.cli.pm import backend as pm_backend  # noqa: E402


def test_resolve_pm_backend_kind_honors_explicit_ollama(monkeypatch) -> None:
    monkeypatch.setattr(
        pm_backend,
        "_resolve_role_runtime_llm_config",
        lambda _state, _role: SimpleNamespace(provider_kind="generic"),
    )
    kind, _cfg = pm_backend.resolve_pm_backend_kind("ollama", SimpleNamespace())
    assert kind == "ollama"


def test_resolve_pm_backend_kind_honors_explicit_codex(monkeypatch) -> None:
    monkeypatch.setattr(
        pm_backend,
        "_resolve_role_runtime_llm_config",
        lambda _state, _role: SimpleNamespace(provider_kind="generic"),
    )
    kind, _cfg = pm_backend.resolve_pm_backend_kind("codex", SimpleNamespace())
    assert kind == "codex"


def test_resolve_pm_backend_kind_auto_uses_runtime_provider_for_ollama_role_mapping(monkeypatch) -> None:
    monkeypatch.setattr(
        pm_backend,
        "_resolve_role_runtime_llm_config",
        lambda _state, _role: SimpleNamespace(provider_kind="ollama"),
    )
    kind, _cfg = pm_backend.resolve_pm_backend_kind("auto", SimpleNamespace())
    assert kind == "generic"


def test_invoke_pm_backend_generic_prefers_role_runtime(monkeypatch) -> None:
    state = SimpleNamespace(
        workspace_full=".",
        show_output=False,
        timeout=0,
        events_full="",
        ollama_full="",
        model="unused",
    )
    args = SimpleNamespace(
        codex_full_auto=True,
        codex_dangerous=False,
        codex_profile="",
    )
    monkeypatch.setattr(
        pm_backend,
        "_invoke_generic_role_runtime",
        lambda **_: '{"tasks":[]}',
    )
    output = pm_backend.invoke_pm_backend(state, "prompt", "generic", args, usage_ctx=None)
    assert output == '{"tasks":[]}'


def test_generic_backend_uses_role_runtime_command(monkeypatch, tmp_path) -> None:
    captured: dict[str, Any] = {}

    class FakeRoleRuntimeService:
        async def execute_role_session(self, command):
            captured["command"] = command
            return SimpleNamespace(
                ok=True,
                output='{"tasks":[]}',
                usage={},
                metadata={"provider_type": "role_runtime", "model": "test-model"},
            )

    monkeypatch.setattr(pm_backend, "_create_role_runtime_service", lambda: FakeRoleRuntimeService())

    state = SimpleNamespace(
        workspace_full=str(tmp_path),
        timeout=12,
        events_full="",
    )

    output = pm_backend._invoke_generic_role_runtime(state, "prompt", usage_ctx=None)

    assert output == '{"tasks":[]}'
    command = captured["command"]
    assert command.role == "pm"
    assert command.workspace == str(tmp_path)
    assert command.user_message == "prompt"
    assert command.domain == "document"
    assert command.stream is False
    assert command.host_kind == "pm_cli_backend"
    assert command.metadata["role_runtime_required"] is True
    assert command.metadata["cognitive_runtime_required"] is True
    assert command.metadata["context_os_expected"] is True
    assert command.metadata["runtime_fallback_used"] is False
    assert command.metadata["fallback_policy"] == "fail_closed"
    assert "legacy_fallback_used" not in command.metadata


def test_explicit_ollama_backend_uses_role_runtime_provider_policy(monkeypatch, tmp_path) -> None:
    captured: dict[str, Any] = {}

    class FakeRoleRuntimeService:
        async def execute_role_session(self, command):
            captured["command"] = command
            return SimpleNamespace(
                ok=True,
                output='{"tasks":[]}',
                usage={},
                metadata={"provider_type": "ollama", "model": "qwen"},
            )

    monkeypatch.setattr(pm_backend, "_create_role_runtime_service", lambda: FakeRoleRuntimeService())
    state = SimpleNamespace(
        workspace_full=str(tmp_path),
        timeout=0,
        events_full="",
        ollama_full="",
    )
    args = SimpleNamespace(codex_full_auto=False, codex_dangerous=False, codex_profile="")

    output = pm_backend.invoke_pm_backend(state, "prompt", "ollama", args, usage_ctx=None)

    assert output == '{"tasks":[]}'
    command = captured["command"]
    assert command.metadata["requested_backend"] == "ollama"
    assert command.metadata["allowed_provider_types"] == ("ollama",)
    assert command.context["llm_provider_policy"]["allowed_provider_types"] == ("ollama",)


def test_explicit_codex_backend_uses_role_runtime_provider_policy(monkeypatch, tmp_path) -> None:
    captured: dict[str, Any] = {}

    class FakeRoleRuntimeService:
        async def execute_role_session(self, command):
            captured["command"] = command
            return SimpleNamespace(
                ok=True,
                output='{"tasks":[]}',
                usage={},
                metadata={"provider_type": "codex_cli", "model": "gpt-5.3-codex"},
            )

    monkeypatch.setattr(pm_backend, "_create_role_runtime_service", lambda: FakeRoleRuntimeService())
    state = SimpleNamespace(
        workspace_full=str(tmp_path),
        timeout=0,
        events_full="",
        ollama_full="",
    )
    args = SimpleNamespace(codex_full_auto=False, codex_dangerous=False, codex_profile="")

    output = pm_backend.invoke_pm_backend(state, "prompt", "codex", args, usage_ctx=None)

    assert output == '{"tasks":[]}'
    command = captured["command"]
    assert command.metadata["requested_backend"] == "codex"
    assert command.metadata["allowed_provider_types"] == ("codex", "codex_cli", "codex_sdk")
    assert command.context["llm_provider_policy"]["allowed_provider_types"] == (
        "codex",
        "codex_cli",
        "codex_sdk",
    )


def test_generic_role_runtime_failure_is_fail_closed(monkeypatch, tmp_path) -> None:
    class FailingRoleRuntimeService:
        async def execute_role_session(self, _command):
            return SimpleNamespace(
                ok=False,
                output="",
                usage={},
                metadata={},
                error_message="role model missing",
            )

    monkeypatch.setattr(pm_backend, "_create_role_runtime_service", lambda: FailingRoleRuntimeService())
    state = SimpleNamespace(
        workspace_full=str(tmp_path),
        timeout=0,
        events_full="",
    )

    with pytest.raises(RuntimeError, match="PM role runtime invocation failed"):
        pm_backend._invoke_generic_role_runtime(state, "prompt", usage_ctx=None)


def test_invoke_pm_backend_generic_raises_on_empty_runtime_output(monkeypatch) -> None:
    state = SimpleNamespace(
        workspace_full=".",
        show_output=False,
        timeout=0,
        events_full="",
        ollama_full="",
        model="unused",
    )
    args = SimpleNamespace(
        codex_full_auto=True,
        codex_dangerous=False,
        codex_profile="",
    )
    monkeypatch.setattr(
        pm_backend,
        "_invoke_generic_role_runtime",
        lambda **_: "",
    )

    with pytest.raises(RuntimeError, match="empty response"):
        pm_backend.invoke_pm_backend(state, "prompt", "generic", args, usage_ctx=None)
