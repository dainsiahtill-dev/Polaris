from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from polaris.cells.llm.control_plane.public import service as llm_control_plane_public
from polaris.cells.llm.control_plane.public.contracts import (
    GetLlmConfigQueryV1,
    GetLlmRuntimeStatusQueryV1,
    InvokeLlmRoleCommandV1,
    LLMRequest,
    SaveLlmConfigCommandV1,
)
from polaris.cells.roles.profile.internal.schema import RoleTurnResult
from polaris.cells.roles.runtime.public import service as roles_runtime_public
from polaris.cells.roles.runtime.public.contracts import (
    ExecuteRoleSessionCommandV1,
    ExecuteRoleTaskCommandV1,
    GetRoleRuntimeStatusQueryV1,
)


class _FakeRoleKernel:
    def __init__(self, workspace: str, registry: object) -> None:
        self.workspace = workspace
        self.registry = registry
        self.calls: list[tuple[str, object]] = []

    async def run(self, role: str, request: object) -> RoleTurnResult:
        self.calls.append((role, request))
        message = str(getattr(request, "message", "") or "")
        if message == "boom":
            return RoleTurnResult(error="boom failed", is_complete=True)
        return RoleTurnResult(
            content=f"ok:{role}",
            thinking="trace",
            tool_calls=[{"name": "read_file"}],
            structured_output={"artifacts": ["runtime/tasks/TASK-001/result.json"]},
            execution_stats={"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
            is_complete=True,
        )

    async def run_stream(self, role: str, request: object):
        """Yield a synthetic completion event then return."""
        self.calls.append((role, request))
        yield {"type": "content_chunk", "content": f"ok:{role}"}
        yield {"type": "complete"}


@pytest.mark.asyncio
async def test_role_runtime_service_executes_task_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(roles_runtime_public, "RoleExecutionKernel", _FakeRoleKernel)
    monkeypatch.setattr(roles_runtime_public, "load_core_roles", lambda: None)
    monkeypatch.setattr(roles_runtime_public.registry, "list_roles", lambda: ["pm"])

    service = roles_runtime_public.RoleRuntimeService()
    command = ExecuteRoleTaskCommandV1(
        role="pm",
        task_id="TASK-001",
        workspace="workspace",
        objective="implement feature",
        context={"phase": "pm"},
    )
    result = await service.execute_role_task(command)

    assert result.ok is True
    assert result.status == "ok"
    assert result.output == "ok:pm"
    assert result.tool_calls == ("read_file",)
    assert result.artifacts == ("runtime/tasks/TASK-001/result.json",)
    assert result.usage["total_tokens"] == 3


@pytest.mark.asyncio
async def test_role_runtime_service_maps_kernel_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(roles_runtime_public, "RoleExecutionKernel", _FakeRoleKernel)
    monkeypatch.setattr(roles_runtime_public, "load_core_roles", lambda: None)
    monkeypatch.setattr(roles_runtime_public.registry, "list_roles", lambda: ["pm"])

    service = roles_runtime_public.RoleRuntimeService()
    result = await service.execute_role_task(
        ExecuteRoleTaskCommandV1(
            role="pm",
            task_id="TASK-002",
            workspace="workspace",
            objective="boom",
        )
    )

    assert result.ok is False
    assert result.status == "failed"
    assert result.error_code == "role_runtime_error"
    assert "boom failed" in str(result.error_message)


@pytest.mark.asyncio
async def test_role_runtime_service_compat_execute_role_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(roles_runtime_public, "RoleExecutionKernel", _FakeRoleKernel)
    monkeypatch.setattr(roles_runtime_public, "load_core_roles", lambda: None)
    monkeypatch.setattr(roles_runtime_public.registry, "list_roles", lambda: ["director"])

    service = roles_runtime_public.RoleRuntimeService()
    payload = await service.execute_role(
        "director",
        {
            "workspace": "workspace",
            "session_id": "session-1",
            "message": "do it",
            "stream": True,
        },
    )

    assert payload["ok"] is True
    assert payload["session_id"] == "session-1"
    assert payload["status"] == "ok"


@pytest.mark.asyncio
async def test_role_runtime_service_stream_chat_turn_passes_history_and_prompt_appendix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_request: dict[str, object] = {}

    class _HistoryKernel(_FakeRoleKernel):
        async def run_stream(self, role: str, request: object):
            captured_request["role"] = role
            captured_request["history"] = list(getattr(request, "history", []))
            captured_request["prompt_appendix"] = getattr(request, "prompt_appendix", None)
            yield {"type": "content_chunk", "content": f"ok:{role}"}
            yield {
                "type": "complete",
                "result": RoleTurnResult(content=f"ok:{role}", thinking="trace", is_complete=True),
            }

    monkeypatch.setattr(roles_runtime_public, "RoleExecutionKernel", _HistoryKernel)
    monkeypatch.setattr(roles_runtime_public, "load_core_roles", lambda: None)
    monkeypatch.setattr(roles_runtime_public.registry, "list_roles", lambda: ["qa"])

    service = roles_runtime_public.RoleRuntimeService()
    command = ExecuteRoleSessionCommandV1(
        role="qa",
        session_id="session-1",
        workspace="workspace",
        user_message="inspect workspace",
        history=(("user", "previous turn"), ("assistant", "previous answer")),
        metadata={"prompt_appendix": "appendix"},
        stream=True,
    )

    events = [event async for event in service.stream_chat_turn(command)]

    assert captured_request["role"] == "qa"
    assert captured_request["history"] == [("user", "previous turn"), ("assistant", "previous answer")]
    assert captured_request["prompt_appendix"] == "appendix"
    assert events[0]["type"] == "content_chunk"


@pytest.mark.asyncio
async def test_role_runtime_service_status_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(roles_runtime_public, "load_core_roles", lambda: None)
    monkeypatch.setattr(roles_runtime_public.registry, "list_roles", lambda: ["pm", "director"])

    service = roles_runtime_public.RoleRuntimeService()
    status = await service.get_runtime_status(
        GetRoleRuntimeStatusQueryV1(workspace="workspace", role="pm", include_tools=True)
    )

    assert status["ready"] is True
    assert status["role_exists"] is True
    assert status["role_count"] == 2
    assert status["tools"]["available"] is True


@dataclass
class _FakeConfigStore:
    data: dict[str, llm_control_plane_public.LLMConfig]

    def save(self, config: llm_control_plane_public.LLMConfig) -> None:
        self.data[config.role] = config

    def get(self, role: str) -> llm_control_plane_public.LLMConfig | None:
        return self.data.get(role)

    def get_all(self) -> list[llm_control_plane_public.LLMConfig]:
        return list(self.data.values())


@pytest.mark.asyncio
async def test_llm_control_plane_service_save_get_and_status(monkeypatch: pytest.MonkeyPatch) -> None:
    service = llm_control_plane_public.LlmControlPlaneService(default_role="pm", default_workspace="workspace")
    store = _FakeConfigStore(data={})
    monkeypatch.setattr(service, "_get_store", lambda _workspace: store)

    save_result = service.save_config(
        SaveLlmConfigCommandV1(
            workspace="workspace",
            role="pm",
            provider_id="provider-x",
            model="gpt-5.4",
            config={"type": "openai_compat"},
        )
    )
    get_result = service.get_config(GetLlmConfigQueryV1(workspace="workspace", role="pm"))
    status = service.get_runtime_status(GetLlmRuntimeStatusQueryV1(workspace="workspace", role="pm"))

    assert save_result.ready is True
    assert get_result.provider_id == "provider-x"
    assert status["role_configured"] is True
    assert status["configured_count"] == 1


class _FakeTuiClient:
    def __init__(self, role: str, workspace: str, system_prompt: str = "") -> None:
        self.role = role
        self.workspace = workspace
        self.system_prompt = system_prompt

    async def chat(self, messages: list[llm_control_plane_public.LLMMessage]) -> str:
        return f"chat:{messages[0].content}"

    async def chat_stream(
        self,
        messages: list[llm_control_plane_public.LLMMessage],
        on_token: Any = None,
    ) -> str:
        if on_token is not None:
            on_token("tok-1")
            on_token("tok-2")
        return f"stream:{messages[0].content}"

    def is_configured(self) -> bool:
        return True


@pytest.mark.asyncio
async def test_llm_control_plane_invoke_role_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    service = llm_control_plane_public.LlmControlPlaneService(default_role="pm", default_workspace="workspace")
    monkeypatch.setattr(llm_control_plane_public, "TUILLMClient", _FakeTuiClient)

    result = await service.invoke_role(
        InvokeLlmRoleCommandV1(
            workspace="workspace",
            role="pm",
            message="hello",
            context={"k": "v"},
            stream=True,
        )
    )

    assert result.ok is True
    assert result.metadata["stream"] is True
    assert result.metadata["chunk_count"] == 2


@pytest.mark.asyncio
async def test_llm_control_plane_generate_and_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    service = llm_control_plane_public.LlmControlPlaneService(default_role="pm", default_workspace="workspace")
    monkeypatch.setattr(llm_control_plane_public, "TUILLMClient", _FakeTuiClient)

    response = await service.generate(LLMRequest(prompt="q", system_prompt="sys", max_tokens=10))
    streamed = [chunk async for chunk in service.stream(LLMRequest(prompt="q", max_tokens=10))]

    assert response.content.startswith("chat:")
    assert streamed == ["tok-1", "tok-2"]
