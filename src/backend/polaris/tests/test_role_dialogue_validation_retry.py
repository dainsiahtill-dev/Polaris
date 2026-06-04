from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from polaris.cells.llm.dialogue.internal import role_dialogue
from polaris.cells.roles.runtime.public.contracts import RoleExecutionResultV1


def test_resolve_role_tool_rounds_supports_role_overrides(monkeypatch) -> None:
    monkeypatch.delenv("KERNELONE_ROLE_TOOL_ROUNDS", raising=False)
    monkeypatch.delenv("KERNELONE_ROLE_TOOL_ROUNDS_PM", raising=False)
    monkeypatch.delenv("KERNELONE_ROLE_TOOL_ROUNDS_ARCHITECT", raising=False)

    assert role_dialogue._resolve_role_tool_rounds("pm") == 2
    assert role_dialogue._resolve_role_tool_rounds("architect") == 3
    assert role_dialogue._resolve_role_tool_rounds("director") == 4

    monkeypatch.setenv("KERNELONE_ROLE_TOOL_ROUNDS", "5")
    assert role_dialogue._resolve_role_tool_rounds("pm") == 5

    monkeypatch.setenv("KERNELONE_ROLE_TOOL_ROUNDS_PM", "3")
    assert role_dialogue._resolve_role_tool_rounds("pm") == 3


@pytest.mark.asyncio
async def test_generate_role_response_delegates_retry_policy_to_role_runtime(monkeypatch) -> None:
    """Legacy generate_role_response must delegate retry policy to RoleRuntime command metadata."""
    import polaris.cells.roles.runtime.public.service as roles_module

    captured: dict[str, Any] = {}

    class FakeRoleRuntimeService:
        async def execute_role_session(self, command) -> RoleExecutionResultV1:
            captured["command"] = command
            return RoleExecutionResultV1(
                ok=True,
                status="ok",
                role=command.role,
                workspace=command.workspace,
                task_id=command.task_id,
                session_id=command.session_id,
                run_id=command.run_id,
                output="PATCH_FILE: src/fastapi_entrypoint.py\n<<<<<<< SEARCH\n\n=======\nprint('ok')\n>>>>>>> REPLACE\nEND PATCH_FILE",
                metadata={
                    "provider_id": "runtime-provider",
                    "model": "runtime-model",
                    "profile_version": "test",
                    "prompt_fingerprint": "fp-test",
                    "tool_policy_id": "policy",
                    "context_os_snapshot_loaded": True,
                },
                usage={"tokens": 9},
            )

    monkeypatch.setattr(roles_module, "RoleRuntimeService", lambda: FakeRoleRuntimeService())
    monkeypatch.setattr(
        role_dialogue,
        "validate_and_parse_role_output",
        lambda role, output: {
            "success": bool(str(output or "").strip()),
            "data": None,
            "errors": [] if str(output or "").strip() else ["empty"],
            "quality_score": 100.0 if str(output or "").strip() else 0.0,
            "suggestions": [],
        },
    )

    response = await role_dialogue.generate_role_response(
        workspace=".",
        settings=SimpleNamespace(),
        role="director",
        message="请执行任务",
        validate_output=True,
        max_retries=1,
    )

    command = captured["command"]
    assert command.role == "director"
    assert command.metadata["role_runtime_required"] is True
    assert command.metadata["cognitive_runtime_required"] is True
    assert command.metadata["context_os_expected"] is True
    assert command.metadata["validate_output"] is True
    assert int(command.metadata["max_retries"]) == 1
    assert "PATCH_FILE" in str(response.get("response") or "")
    assert response["metadata"]["context_os_snapshot_loaded"] is True
    assert response["metadata"]["fallback_policy"] == "fail_closed"


@pytest.mark.asyncio
async def test_generate_role_response_advances_tool_rounds_until_completion(monkeypatch) -> None:
    pytest.skip("Legacy internal tool rounds moved under RoleRuntime/TransactionKernel")
    import polaris.cells.roles.runtime.public.service as roles_module

    call_count = {"value": 0}
    requests = []

    first_result = SimpleNamespace(
        content="",
        thinking="<thinking>[SEARCH_CODE]query:test[/SEARCH_CODE]</thinking>",
        metadata={},
        execution_stats={},
        profile_version="test",
        prompt_fingerprint=None,
        tool_policy_id="policy",
        error="",
        tool_results=[
            {
                "tool": "search_code",
                "success": True,
                "result": {"matches": 3},
            }
        ],
        tool_calls=[{"tool": "search_code", "args": {"query": "test"}}],
        is_complete=False,
    )
    second_result = SimpleNamespace(
        content='{"tasks":[{"id":"TASK-1"}]}',
        thinking=None,
        metadata={},
        execution_stats={},
        profile_version="test",
        prompt_fingerprint=None,
        tool_policy_id="policy",
        error="",
        tool_results=[],
        tool_calls=[],
        is_complete=True,
    )

    class FakeKernel:
        def __init__(self, workspace: str, registry) -> None:
            self.workspace = workspace
            self.registry = registry

        async def run(self, role: str, request) -> SimpleNamespace:
            del role
            call_count["value"] += 1
            requests.append(request)
            return first_result if call_count["value"] == 1 else second_result

    monkeypatch.setattr(roles_module, "RoleExecutionKernel", FakeKernel)
    monkeypatch.setattr(
        roles_module,
        "registry",
        SimpleNamespace(has_role=lambda _: True),
    )
    monkeypatch.setattr(
        role_dialogue,
        "validate_and_parse_role_output",
        lambda role, output: {
            "success": bool(str(output or "").strip()),
            "data": {"tasks": [{"id": "TASK-1"}]},
            "errors": [],
            "quality_score": 100.0,
            "suggestions": [],
        },
    )

    response = await role_dialogue.generate_role_response(
        workspace=".",
        settings=SimpleNamespace(),
        role="pm",
        message="请规划任务",
        validate_output=True,
        max_retries=1,
    )

    assert call_count["value"] == 2
    assert len(requests) == 2
    assert "工具执行结果" in str(requests[1].message)
    assert str(response.get("response") or "") == '{"tasks":[{"id":"TASK-1"}]}'
    tool_calls = response.get("tool_calls")
    assert isinstance(tool_calls, list)
    assert len(tool_calls) == 1
    assert tool_calls[0].get("tool") == "search_code"
    assert int(response.get("tool_rounds_executed") or 0) == 1


@pytest.mark.asyncio
async def test_generate_role_response_adds_loop_breaker_for_repeated_readonly_tool_calls(monkeypatch) -> None:
    pytest.skip("Legacy internal tool rounds moved under RoleRuntime/TransactionKernel")
    import polaris.cells.roles.runtime.public.service as roles_module

    requests = []
    call_count = {"value": 0}

    pending_round_1 = SimpleNamespace(
        content="",
        thinking=None,
        metadata={},
        execution_stats={},
        profile_version="test",
        prompt_fingerprint=None,
        tool_policy_id="policy",
        error="",
        tool_results=[{"tool": "list_directory", "success": True, "result": {"entries": []}}],
        tool_calls=[{"tool": "list_directory", "args": {"path": ".", "recursive": False}}],
        is_complete=False,
    )
    pending_round_2 = SimpleNamespace(
        content="",
        thinking=None,
        metadata={},
        execution_stats={},
        profile_version="test",
        prompt_fingerprint=None,
        tool_policy_id="policy",
        error="",
        tool_results=[{"tool": "list_directory", "success": True, "result": {"entries": []}}],
        tool_calls=[{"tool": "list_directory", "args": {"path": ".", "recursive": False}}],
        is_complete=False,
    )
    completed_round = SimpleNamespace(
        content='{"tasks":[{"id":"TASK-1"}]}',
        thinking=None,
        metadata={},
        execution_stats={},
        profile_version="test",
        prompt_fingerprint=None,
        tool_policy_id="policy",
        error="",
        tool_results=[],
        tool_calls=[],
        is_complete=True,
    )

    class FakeKernel:
        def __init__(self, workspace: str, registry) -> None:
            self.workspace = workspace
            self.registry = registry

        async def run(self, role: str, request) -> SimpleNamespace:
            del role
            call_count["value"] += 1
            requests.append(request)
            if call_count["value"] == 1:
                return pending_round_1
            if call_count["value"] == 2:
                return pending_round_2
            return completed_round

    monkeypatch.setattr(roles_module, "RoleExecutionKernel", FakeKernel)
    monkeypatch.setattr(
        roles_module,
        "registry",
        SimpleNamespace(has_role=lambda _: True),
    )
    monkeypatch.setattr(
        role_dialogue,
        "validate_and_parse_role_output",
        lambda role, output: {
            "success": bool(str(output or "").strip()),
            "data": {"tasks": [{"id": "TASK-1"}]},
            "errors": [],
            "quality_score": 100.0,
            "suggestions": [],
        },
    )
    monkeypatch.delenv("KERNELONE_ROLE_TOOL_ROUNDS", raising=False)
    monkeypatch.delenv("KERNELONE_ROLE_TOOL_ROUNDS_PM", raising=False)

    response = await role_dialogue.generate_role_response(
        workspace=".",
        settings=SimpleNamespace(),
        role="pm",
        message="请规划任务",
        validate_output=True,
        max_retries=1,
    )

    assert call_count["value"] == 3
    assert len(requests) == 3
    assert "检测到连续重复的只读工具调用" in str(requests[2].message)
    assert str(response.get("response") or "") == '{"tasks":[{"id":"TASK-1"}]}'


@pytest.mark.asyncio
async def test_generate_role_response_guides_write_file_when_read_file_missing(monkeypatch) -> None:
    pytest.skip("Legacy internal tool rounds moved under RoleRuntime/TransactionKernel")
    import polaris.cells.roles.runtime.public.service as roles_module

    call_count = {"value": 0}
    requests = []

    pending_result = SimpleNamespace(
        content="",
        thinking=None,
        metadata={},
        execution_stats={},
        profile_version="test",
        prompt_fingerprint=None,
        tool_policy_id="policy",
        error="",
        tool_results=[
            {
                "tool": "read_file",
                "success": False,
                "error": "File not found: src/expense_tracker/fastapi_entrypoint.py",
                "args": {"file": "src/expense_tracker/fastapi_entrypoint.py"},
            }
        ],
        tool_calls=[{"tool": "read_file", "args": {"file": "src/expense_tracker/fastapi_entrypoint.py"}}],
        is_complete=False,
    )
    completed_result = SimpleNamespace(
        content='{"execution_status":"success"}',
        thinking=None,
        metadata={},
        execution_stats={},
        profile_version="test",
        prompt_fingerprint=None,
        tool_policy_id="policy",
        error="",
        tool_results=[],
        tool_calls=[],
        is_complete=True,
    )

    class FakeKernel:
        def __init__(self, workspace: str, registry) -> None:
            self.workspace = workspace
            self.registry = registry

        async def run(self, role: str, request) -> SimpleNamespace:
            del role
            call_count["value"] += 1
            requests.append(request)
            return pending_result if call_count["value"] == 1 else completed_result

    monkeypatch.setattr(roles_module, "RoleExecutionKernel", FakeKernel)
    monkeypatch.setattr(
        roles_module,
        "registry",
        SimpleNamespace(has_role=lambda _: True),
    )
    monkeypatch.setattr(
        role_dialogue,
        "validate_and_parse_role_output",
        lambda role, output: {
            "success": bool(str(output or "").strip()),
            "data": {},
            "errors": [],
            "quality_score": 100.0,
            "suggestions": [],
        },
    )

    response = await role_dialogue.generate_role_response(
        workspace=".",
        settings=SimpleNamespace(),
        role="director",
        message="请完成执行",
        validate_output=True,
        max_retries=1,
    )

    assert call_count["value"] == 2
    assert "改用 write_file" in str(requests[1].message)
    assert str(response.get("response") or "") == '{"execution_status":"success"}'


@pytest.mark.asyncio
async def test_generate_role_response_marks_error_when_tool_rounds_exhausted(monkeypatch) -> None:
    pytest.skip("Legacy internal tool rounds moved under RoleRuntime/TransactionKernel")
    import polaris.cells.roles.runtime.public.service as roles_module

    call_count = {"value": 0}

    pending_result = SimpleNamespace(
        content="",
        thinking="<thinking>[SEARCH_CODE]query:loop[/SEARCH_CODE]</thinking>",
        metadata={},
        execution_stats={},
        profile_version="test",
        prompt_fingerprint=None,
        tool_policy_id="policy",
        error="",
        tool_results=[{"tool": "search_code", "success": True, "result": {"matches": 1}}],
        tool_calls=[{"tool": "search_code", "args": {"query": "loop"}}],
        is_complete=False,
    )

    class FakeKernel:
        def __init__(self, workspace: str, registry) -> None:
            self.workspace = workspace
            self.registry = registry

        async def run(self, role: str, request) -> SimpleNamespace:
            del role, request
            call_count["value"] += 1
            return pending_result

    monkeypatch.setattr(roles_module, "RoleExecutionKernel", FakeKernel)
    monkeypatch.setattr(
        roles_module,
        "registry",
        SimpleNamespace(has_role=lambda _: True),
    )
    monkeypatch.setattr(
        role_dialogue,
        "validate_and_parse_role_output",
        lambda role, output: {
            "success": False,
            "data": None,
            "errors": ["empty"],
            "quality_score": 0.0,
            "suggestions": [],
        },
    )
    monkeypatch.setenv("KERNELONE_ROLE_TOOL_ROUNDS", "1")

    response = await role_dialogue.generate_role_response(
        workspace=".",
        settings=SimpleNamespace(),
        role="director",
        message="请执行任务",
        validate_output=False,
        max_retries=1,
    )

    assert call_count["value"] == 2
    assert "role_tool_rounds_exhausted:1" in str(response.get("error") or "")
