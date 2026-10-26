"""Kernel private-unit scenarios relocated from Runtime with original assertions."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from polaris.cells.roles.kernel.internal.llm_caller.request_preparer import LLMRequestPreparer
from polaris.cells.roles.kernel.public.service import ContextRequest


@pytest.mark.parametrize("forged", [True, "auto_continue", {"mode": "auto_continue"}])
def test_json_marker_is_not_native_history_authority(forged: Any) -> None:
    from polaris.cells.roles.kernel.internal.llm_caller.tool_helpers import is_committed_role_session_continuation

    assert not is_committed_role_session_continuation({"_roles_kernel_committed_session_continuation": forged})


def test_prebuilt_native_blocks_and_sidecar_fields_are_not_stringified() -> None:
    messages = [
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "call-1", "name": "read_file", "input": {"path": "src/module.py"}}],
        },
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call-1", "content": "observed bytes"}]},
        {"role": "user", "content": "CURRENT"},
    ]
    actual = LLMRequestPreparer._extract_prebuilt_projection_messages(
        ContextRequest(message="CURRENT", context_override={"_transaction_kernel_prebuilt_messages": messages})
    )
    assert actual == messages
    assert actual is not messages


@pytest.mark.asyncio
@pytest.mark.parametrize("denied", [False, True])
async def test_runtime_transaction_fixture_executes_real_read_or_denial(tmp_path: Path, denied: bool) -> None:
    """Synthetic provider choice reaches actual transaction policy and result production."""
    from types import SimpleNamespace

    from polaris.cells.roles.kernel.tests.runtime_boundary_scenarios import execute_readonly_transaction_fixture
    from polaris.kernelone.fs import KernelFileSystem, get_default_adapter

    kfs = KernelFileSystem(str(tmp_path), get_default_adapter())
    kfs.workspace_write_text("policy.md", "Do not run rm -rf. Retain the project files.\n", encoding="utf-8")
    calls: list[str] = []

    async def provider(_request: Any) -> dict[str, Any]:
        return {
            "content": "",
            "model": "synthetic-component-input",
            "usage": {},
            "tool_calls": [
                {
                    "id": "synthetic-read-or-denial",
                    "type": "function",
                    "function": {
                        "name": "execute_command" if denied else "read_file",
                        "arguments": '{"command":"rm -rf policy.md"}' if denied else '{"file":"policy.md"}',
                    },
                }
            ],
        }

    async def tools(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        calls.append(name)
        assert name == "read_file" and arguments["file"] == "policy.md"
        return {
            "success": True,
            "result": {"file": "policy.md", "content": kfs.workspace_read_text("policy.md", encoding="utf-8")},
        }

    request = SimpleNamespace(
        metadata={"transaction_invocation_id": "synthetic-component-read"}, message="Read policy.md only."
    )
    if denied:
        with pytest.raises(RuntimeError, match="execute_command"):
            await execute_readonly_transaction_fixture("director", request, tmp_path, provider, tools)
        assert calls == []
    else:
        result = await execute_readonly_transaction_fixture("director", request, tmp_path, provider, tools)
        assert calls == ["read_file"]
        row = result["batch_receipt"]["results"][0]
        assert row["tool_name"] == "read_file" and row["status"] == "success"
        assert row["result"]["content"] == kfs.workspace_read_text("policy.md", encoding="utf-8")
    assert kfs.workspace_read_text("policy.md", encoding="utf-8").startswith("Do not run rm -rf")


def test_runtime_context_fixture_uses_actual_injected_adapter() -> None:
    from types import SimpleNamespace

    from polaris.cells.roles.kernel.public.service import ContextGatewayConfig
    from polaris.cells.roles.kernel.tests.runtime_boundary_scenarios import resolve_injected_context_fixture

    expected = ContextGatewayConfig()
    profile, request = SimpleNamespace(), SimpleNamespace()
    calls: list[tuple[str, Any, Any]] = []

    def factory(role: str, current_profile: Any, current_request: Any) -> ContextGatewayConfig:
        calls.append((role, current_profile, current_request))
        return expected

    assert resolve_injected_context_fixture(factory, "chief_engineer", profile, request) is expected
    assert calls == [("chief_engineer", profile, request)]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["missing_commit", "nonrepairable"])
async def test_runtime_projection_fault_is_observed_by_actual_projector(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    """Explicit synthetic event fault, without a positive replacement commit receipt."""
    from polaris.cells.roles.kernel.internal.kernel import stream_event_projection as projection
    from polaris.cells.roles.kernel.internal.kernel.tests.test_stream_event_projection import (
        _make_stream_completion_projector,
        _Publisher,
    )
    from polaris.cells.roles.kernel.public.turn_events import CompletionEvent
    from polaris.cells.roles.kernel.tests.runtime_boundary_scenarios import install_final_answer_projection_fault

    observations: dict[str, object] = {}
    monkeypatch.setattr(projection, "append_role_turn_task_boundary_verdict", lambda **_: None)
    install_final_answer_projection_fault(monkeypatch, observations, mode)
    projector = _make_stream_completion_projector(tmp_path, _Publisher())
    projected = await projector.project(
        CompletionEvent(turn_id="synthetic-empty-final", status="success", turn_kind="final_answer")
    )
    assert projected is not None
    assert observations == {mode: 1}
    result = projected.event["result"]
    if mode == "missing_commit":
        assert "turn_commit_receipt" not in result.metadata
    else:
        assert result.error == "Private nonrepairable execution failure"


@pytest.mark.asyncio
async def test_runtime_completion_fixture_preserves_actual_failed_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Synthetic completion input cannot override the actual missing-artifact verdict."""
    from polaris.cells.control_plane.run_ledger.public.task_boundary import evaluate_task_boundary_verdict
    from polaris.cells.roles.kernel.tests.runtime_boundary_scenarios import project_boundary_completion_fixture
    from polaris.cells.roles.profile.public.service import RoleTurnRequest

    request = RoleTurnRequest(
        workspace=str(tmp_path),
        message="Implement missing.py",
        task_id="TASK-1",
        run_id="projector-run",
        validate_output=False,
        context_override={},
        metadata={},
    )
    verdicts: list[dict[str, Any]] = []

    def append_boundary(**_kwargs: Any) -> dict[str, Any]:
        verdict = evaluate_task_boundary_verdict(
            workspace=tmp_path, task_id="TASK-1", run_id="projector-run", target_files=["missing.py"]
        ).to_dict()
        verdicts.append(verdict)
        return verdict

    event = await project_boundary_completion_fixture(
        tmp_path,
        "director",
        request,
        0,
        {"batch_id": "synthetic-empty-batch", "results": [], "success_count": 0, "failure_count": 0},
        [],
        append_boundary,
        monkeypatch,
    )
    assert len(verdicts) == 1
    assert verdicts[0]["status"] == "incomplete_materialization"
    assert event["result"].is_complete is False


def test_kernel_scenarios_keep_runtime_private_request_assembly_with_runtime_owner() -> None:
    """Catalog imports do not catch public-class private calls; guard the observed pattern."""
    import ast
    import inspect

    from polaris.cells.roles.kernel.tests import runtime_boundary_scenarios

    def crosses_runtime_request_owner(source: str) -> bool:
        tree = ast.parse(source)
        return any(
            (isinstance(node, ast.ImportFrom) and (node.module or "").startswith("polaris.cells.roles.runtime"))
            or (isinstance(node, ast.Attribute) and node.attr == "_build_session_request")
            for node in ast.walk(tree)
        )

    # Explicit rejected source specimen; never imported or executed.
    old_crossing = (
        "from polaris.cells.roles.runtime.public.service import RoleRuntimeService\n"
        "RoleRuntimeService()._build_session_request(command)\n"
    )
    assert crosses_runtime_request_owner(old_crossing)
    assert not crosses_runtime_request_owner(inspect.getsource(runtime_boundary_scenarios))
