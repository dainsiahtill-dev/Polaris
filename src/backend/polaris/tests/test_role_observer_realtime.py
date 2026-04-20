from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from polaris.cells.roles.profile.public.service import load_core_roles
from polaris.cells.roles.runtime.public.service import (
    RoleExecutionKernel,
    RoleExecutionMode,
    RoleProfileRegistry,
    RoleTurnRequest,
)


@pytest.fixture
def registry() -> RoleProfileRegistry:
    return load_core_roles()


@pytest.fixture
def kernel(tmp_path: Path, registry: RoleProfileRegistry) -> RoleExecutionKernel:
    return RoleExecutionKernel(workspace=str(tmp_path / "workspace"), registry=registry)


@pytest.mark.skip(reason="RoleExecutionKernel no longer has _get_or_create_data_store / _store_execution_data methods")
@pytest.mark.asyncio
async def test_store_execution_data_emits_preview_events_to_runtime_channel(
    kernel: RoleExecutionKernel,
    registry: RoleProfileRegistry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = registry.get_profile("architect")
    request = RoleTurnRequest(
        mode=RoleExecutionMode.CHAT,
        message="请给出架构说明",
        task_id="task-42",
    )

    captured_events: list[dict[str, object]] = []

    class _FakeStore:
        def __init__(self) -> None:
            self.records: list[tuple[str, dict[str, object]]] = []

        def append_event(self, event_name: str, payload: dict[str, object]) -> None:
            self.records.append((event_name, payload))

    fake_store = _FakeStore()

    async def _fake_get_store(_profile: object) -> _FakeStore:
        return fake_store

    def _fake_emit(**kwargs: object) -> None:
        captured_events.append(dict(kwargs))

    monkeypatch.setattr(kernel, "_get_or_create_data_store", _fake_get_store)
    monkeypatch.setattr(kernel, "_emit_runtime_llm_event", _fake_emit)

    await kernel._store_execution_data(  # noqa: SLF001
        profile=profile,  # type: ignore[arg-type]
        request=request,
        run_id="run-observer-1",
        attempt=2,
        content="最终架构建议：统一到单一 realtime contract，并提供清晰预览。",
        tool_results=[{"success": True, "result": {"path": "src/app.py"}}],
        tool_calls=[SimpleNamespace(tool="write_file", args={"file": "src/app.py", "content": "print('ok')\n"})],  # type: ignore[list-item]
        thinking="先分析边界，再决定收敛策略。",
    )

    assert fake_store.records
    event_names = {item[0] for item in fake_store.records}
    assert "turn_completed" in event_names

    realtime_event_types = {str(item.get("event_type") or "") for item in captured_events}
    assert realtime_event_types == {"thinking_preview", "content_preview"}

    content_event = next(item for item in captured_events if item.get("event_type") == "content_preview")
    content_metadata = content_event["metadata"]
    assert isinstance(content_metadata, dict)
    assert "最终架构建议" in str(content_metadata.get("content_preview") or "")
    tool_details = content_metadata.get("tool_details")
    assert isinstance(tool_details, list)
    assert tool_details[0]["tool"] == "write_file"


@pytest.mark.skip(reason="RoleExecutionKernel no longer has _emit_runtime_llm_event / _finalize_result methods")
@pytest.mark.asyncio
async def test_finalize_result_emits_structured_tool_events(
    kernel: RoleExecutionKernel,
    registry: RoleProfileRegistry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = registry.get_profile("director")
    request = RoleTurnRequest(
        mode=RoleExecutionMode.CHAT,
        message="执行文件修改",
        task_id="task-88",
        max_retries=0,
    )

    captured_events: list[dict[str, object]] = []

    def _fake_emit(**kwargs: object) -> None:
        captured_events.append(dict(kwargs))

    async def _fake_execute_tools(_profile: object, _tool_calls: object) -> list[dict[str, object]]:
        return [
            {
                "success": True,
                "authorized": True,
                "result": {"path": "src/app.py", "bytes_written": 12},
            }
        ]

    async def _fake_store_execution_data(**_: object) -> None:
        return None

    monkeypatch.setattr(kernel, "_emit_runtime_llm_event", _fake_emit)
    monkeypatch.setattr(kernel, "_execute_tools", _fake_execute_tools)
    monkeypatch.setattr(kernel, "_store_execution_data", _fake_store_execution_data)
    monkeypatch.setattr(
        kernel._output_parser,
        "parse_tool_calls",
        lambda *_args, **_kwargs: [
            SimpleNamespace(
                tool="write_file",
                args={"file": "src/app.py", "content": "print('ok')\n"},
            )
        ],
    )
    monkeypatch.setattr(kernel._output_parser, "parse_structured_output", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(kernel._quality_checker, "_check_quality", lambda *_args, **_kwargs: (100.0, []))

    result = await kernel._finalize_result(  # noqa: SLF001
        profile=profile,  # type: ignore[arg-type]
        request=request,
        run_id="run-observer-2",
        attempt=1,
        content="[WRITE_FILE]\nfile: src/app.py\ncontent: print('ok')\n[/WRITE_FILE]",
        thinking=None,
        fingerprint=SimpleNamespace(hash="fp", version="v1"),  # type: ignore[arg-type]
        llm_response=SimpleNamespace(token_estimate=10),  # type: ignore[arg-type]
    )

    assert result.error is None
    event_types = [str(item.get("event_type") or "") for item in captured_events]
    assert event_types.count("tool_execute") == 1
    assert event_types.count("tool_result") == 1

    tool_call_event = next(item for item in captured_events if item.get("event_type") == "tool_execute")
    tool_call_metadata = tool_call_event["metadata"]
    assert isinstance(tool_call_metadata, dict)
    assert tool_call_metadata["tool_name"] == "write_file"
    assert tool_call_metadata["args"]["file"] == "src/app.py"

    tool_result_event = next(item for item in captured_events if item.get("event_type") == "tool_result")
    tool_result_metadata = tool_result_event["metadata"]
    assert isinstance(tool_result_metadata, dict)
    assert tool_result_metadata["success"] is True
    assert tool_result_metadata["result"]["path"] == "src/app.py"
    assert tool_result_metadata["result_payload"]["authorized"] is True

