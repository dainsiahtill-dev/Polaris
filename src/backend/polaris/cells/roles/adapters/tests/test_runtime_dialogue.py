"""Tests for runtime-first role adapter dialogue helper."""

from __future__ import annotations

from typing import Any

import pytest
from polaris.cells.roles.adapters.internal import runtime_dialogue
from polaris.cells.roles.adapters.internal.runtime_dialogue import invoke_role_runtime_first
from polaris.cells.roles.runtime.public.contracts import RoleExecutionResultV1


class TestRuntimeDialogueHelper:
    @pytest.mark.asyncio
    async def test_uses_role_runtime_context_os_path_first(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Any,
    ) -> None:
        captured: dict[str, Any] = {}

        class FakeRoleRuntimeService:
            async def execute_role_session(self, command: Any) -> RoleExecutionResultV1:
                captured["command"] = command
                return RoleExecutionResultV1(
                    ok=True,
                    status="ok",
                    role=command.role,
                    workspace=command.workspace,
                    task_id=command.task_id,
                    session_id=command.session_id,
                    run_id=command.run_id,
                    output="runtime output",
                    usage={"tokens": 7},
                    metadata={"context_os_snapshot_loaded": True},
                )

        monkeypatch.setattr(runtime_dialogue, "_create_role_runtime_service", lambda: FakeRoleRuntimeService())

        result = await invoke_role_runtime_first(
            workspace=str(tmp_path),
            role="pm",
            message="produce executable task contracts",
            context={
                "run_id": "run-1",
                "task_id": "task-1",
                "history": [("user", "previous turn")],
            },
            validate_output=False,
            max_retries=1,
        )

        assert result["success"] is True
        assert result["response"] == "runtime output"
        assert result["metadata"]["role_runtime_entrypoint"] == "roles.runtime.execute_role_session"
        assert result["metadata"]["context_os_expected"] is True
        assert result["metadata"]["runtime_fallback_used"] is False
        assert result["metadata"]["fallback_policy"] == "fail_closed"
        command = captured["command"]
        assert command.role == "pm"
        assert command.run_id == "run-1"
        assert command.task_id == "task-1"
        assert command.session_id == "pm-adapter-run-1"
        assert command.stream is False
        assert command.host_kind == "pm_adapter"
        assert command.history == (("user", "previous turn"),)
        assert command.metadata["role_runtime_required"] is True
        assert command.metadata["cognitive_runtime_required"] is True
        assert command.metadata["context_os_expected"] is True

    @pytest.mark.asyncio
    async def test_uses_pm_task_id_as_runtime_task_id(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Any,
    ) -> None:
        captured: dict[str, Any] = {}

        class FakeRoleRuntimeService:
            async def execute_role_session(self, command: Any) -> RoleExecutionResultV1:
                captured["command"] = command
                return RoleExecutionResultV1(
                    ok=True,
                    status="ok",
                    role=command.role,
                    workspace=command.workspace,
                    task_id=command.task_id,
                    session_id=command.session_id,
                    output="runtime output",
                )

        monkeypatch.setattr(runtime_dialogue, "_create_role_runtime_service", lambda: FakeRoleRuntimeService())

        await invoke_role_runtime_first(
            workspace=str(tmp_path),
            role="pm",
            message="probe runtime route",
            context={"pm_task_id": "pm-contract-1"},
        )

        command = captured["command"]
        assert command.task_id == "pm-contract-1"
        assert command.metadata["pm_task_id"] == "pm-contract-1"

    @pytest.mark.asyncio
    async def test_projects_trusted_metadata_timeout_into_role_context(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Any,
    ) -> None:
        captured: dict[str, Any] = {}

        class FakeRoleRuntimeService:
            async def execute_role_session(self, command: Any) -> RoleExecutionResultV1:
                captured["command"] = command
                return RoleExecutionResultV1(
                    ok=True,
                    status="ok",
                    role=command.role,
                    workspace=command.workspace,
                    task_id=command.task_id,
                    session_id=command.session_id,
                    output="runtime output",
                )

        monkeypatch.setattr(runtime_dialogue, "_create_role_runtime_service", lambda: FakeRoleRuntimeService())

        await invoke_role_runtime_first(
            workspace=str(tmp_path),
            role="qa",
            message="review verified delivery",
            context={
                "task_id": "qa-1",
                "metadata": {"request_timeout_seconds": 595},
            },
        )

        command = captured["command"]
        assert command.context["request_timeout_seconds"] == 595
        assert command.metadata["request_timeout_seconds"] == 595

    @pytest.mark.asyncio
    async def test_projects_first_class_turn_request_identity_into_runtime_metadata(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Any,
    ) -> None:
        captured: dict[str, Any] = {}

        class FakeRoleRuntimeService:
            async def execute_role_session(self, command: Any) -> RoleExecutionResultV1:
                captured["command"] = command
                return RoleExecutionResultV1(
                    ok=True,
                    status="ok",
                    role=command.role,
                    workspace=command.workspace,
                    task_id=command.task_id,
                    session_id=command.session_id,
                    output="runtime output",
                )

        monkeypatch.setattr(runtime_dialogue, "_create_role_runtime_service", lambda: FakeRoleRuntimeService())

        await invoke_role_runtime_first(
            workspace=str(tmp_path),
            role="pm",
            message="probe runtime route",
            context={
                "run_id": "factory-run-1",
                "task_id": "pm-route-probe",
                "turn_request_id": "pm-route-probe-0123456789abcdef",
            },
        )

        command = captured["command"]
        assert command.context["turn_request_id"] == "pm-route-probe-0123456789abcdef"
        assert command.metadata["turn_request_id"] == "pm-route-probe-0123456789abcdef"

    @pytest.mark.asyncio
    async def test_runtime_boundary_unavailable_fails_closed(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Any,
    ) -> None:
        def _missing_runtime() -> Any:
            raise ImportError("roles.runtime public boundary unavailable")

        monkeypatch.setattr(runtime_dialogue, "_create_role_runtime_service", _missing_runtime)

        with pytest.raises(ImportError, match=r"roles\.runtime public boundary unavailable"):
            await invoke_role_runtime_first(
                workspace=str(tmp_path),
                role="qa",
                message="review quality",
                context={"task_id": "qa-1"},
                validate_output=False,
                max_retries=1,
            )

    @pytest.mark.asyncio
    async def test_runtime_execution_failure_is_not_hidden_by_fallback(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Any,
    ) -> None:
        class FailingRoleRuntimeService:
            async def execute_role_session(self, _command: Any) -> RoleExecutionResultV1:
                raise RuntimeError("runtime provider failed")

        monkeypatch.setattr(runtime_dialogue, "_create_role_runtime_service", lambda: FailingRoleRuntimeService())

        with pytest.raises(RuntimeError, match="runtime provider failed"):
            await invoke_role_runtime_first(
                workspace=str(tmp_path),
                role="chief_engineer",
                message="analyze design",
                context={"task_id": "chief-1"},
            )
