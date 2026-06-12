"""PM CLI backend role-runtime integration tests."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from polaris.cells.roles.runtime.public.contracts import ExecuteRoleSessionCommandV1
from polaris.delivery.cli.pm import backend as pm_backend
from polaris.delivery.cli.pm.config import PmRoleState


def _pm_state(tmp_path: Path, *, timeout: int) -> PmRoleState:
    return PmRoleState(
        workspace_full=str(tmp_path),
        cache_root_full=str(tmp_path / ".polaris"),
        model="test-model",
        show_output=False,
        timeout=timeout,
        prompt_profile="default",
        output_path="",
        events_path="",
        log_path="",
        llm_events_path="",
    )


class _FakeRoleRuntimeService:
    def __init__(self, captured: dict[str, ExecuteRoleSessionCommandV1]) -> None:
        self._captured = captured

    async def execute_role_session(self, command: ExecuteRoleSessionCommandV1) -> SimpleNamespace:
        self._captured["command"] = command
        return SimpleNamespace(ok=True, output='{"tasks": []}', usage={}, metadata={})


def test_pm_backend_propagates_timeout_to_session_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, ExecuteRoleSessionCommandV1] = {}

    def _create_service() -> _FakeRoleRuntimeService:
        return _FakeRoleRuntimeService(captured)

    monkeypatch.setattr(pm_backend, "_create_role_runtime_service", _create_service)

    output = pm_backend._invoke_generic_role_runtime(
        _pm_state(tmp_path, timeout=300),
        "Create PM tasks",
        usage_ctx=None,
        backend_kind="generic",
    )

    assert output == '{"tasks": []}'
    command = captured["command"]
    assert command.timeout_seconds == 300
    assert command.metadata["timeout_seconds"] == 300
    assert command.context["llm_call_timeout_seconds"] == 300
    assert command.context["request_timeout_seconds"] == 300
    assert command.context["timeout_seconds"] == 300


def test_pm_backend_validate_output_flag_threads_to_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """L2-10 regression: AGENTS.md free-text drafts must disable the PM JSON
    contract validator (which force-parses any fenced block as JSON)."""
    captured: dict[str, ExecuteRoleSessionCommandV1] = {}

    def _create_service() -> _FakeRoleRuntimeService:
        return _FakeRoleRuntimeService(captured)

    monkeypatch.setattr(pm_backend, "_create_role_runtime_service", _create_service)

    pm_backend._invoke_generic_role_runtime(
        _pm_state(tmp_path, timeout=60),
        "draft an AGENTS.md",
        None,
        validate_output=False,
    )
    command = captured["command"]
    assert command.metadata["validate_output"] is False


def test_pm_backend_validates_output_by_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, ExecuteRoleSessionCommandV1] = {}

    def _create_service() -> _FakeRoleRuntimeService:
        return _FakeRoleRuntimeService(captured)

    monkeypatch.setattr(pm_backend, "_create_role_runtime_service", _create_service)

    pm_backend._invoke_generic_role_runtime(
        _pm_state(tmp_path, timeout=60),
        "plan tasks",
        None,
    )
    command = captured["command"]
    assert command.metadata["validate_output"] is True
