"""Final-request audit must detect scope guidance lost or changed after projection."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from polaris.cells.roles.kernel.internal.llm_caller.context_audit import build_final_request_context_audit
from polaris.cells.roles.kernel.internal.llm_caller.response_types import PreparedLLMRequest
from polaris.kernelone.audit.task_write_guidance import project_task_write_guidance, render_task_write_guidance
from polaris.kernelone.tool_execution.forced_tool_surface import build_forced_tool_surface


def _audit(*, guidance: dict | None, render: bool = True, tool_paths: list[str] | None = None) -> dict:
    targets = ["tests/verify.test.ts", "README.md"]
    envelope = {"authorization": {"allowed_write_paths": targets, "target_files": targets}}
    context = {"director_execution_envelope": envelope, "target_files": targets}
    if guidance is not None:
        context["task_write_guidance"] = guidance
    tools = build_forced_tool_surface(["write_file"], pin_write_paths=tool_paths if tool_paths is not None else targets)
    messages = [
        {"role": "system", "content": render_task_write_guidance(guidance) if render and guidance else "Director"},
        {"role": "user", "content": "Create tests and documentation"},
    ]
    request = SimpleNamespace(role="director", context=context, metadata={}, model="test", max_tokens=4096)
    prepared = PreparedLLMRequest(
        messages=messages,
        input_text="",
        context_result=None,
        context_summary="",
        request_options={"tools": tools},
        ai_request=request,
        native_tool_schemas=tools,
        native_tool_mode="openai",
    )
    return build_final_request_context_audit(
        prepared=prepared, profile=SimpleNamespace(role_id="director", max_context_tokens=128000)
    )


def _guidance() -> dict:
    return project_task_write_guidance(
        {"authorization": {"allowed_write_paths": ["tests/verify.test.ts", "README.md"]}},
        ["tests/verify.test.ts", "README.md"],
    )


@pytest.mark.parametrize(
    "guidance,render,tool_paths,code",
    [
        (None, True, None, "task_write_guidance_missing"),
        (_guidance(), False, None, "task_write_guidance_not_projected"),
        ({**_guidance(), "write_targets": ["package.json"]}, True, None, "task_write_guidance_scope_mismatch"),
        (_guidance(), True, ["tests/verify.test.ts", "README.md", "package.json"], "task_write_tool_scope_mismatch"),
    ],
)
def test_final_request_rejects_inconsistent_write_guidance(guidance, render, tool_paths, code) -> None:
    audit = _audit(guidance=guidance, render=render, tool_paths=tool_paths)

    assert any(row["code"] == code and row["severity"] == "error" for row in audit["context_quality"]["findings"])


def test_final_request_accepts_consistent_write_guidance() -> None:
    audit = _audit(guidance=_guidance())

    assert not any(row["code"].startswith("task_write_") for row in audit["context_quality"]["findings"])


def test_final_request_accepts_narrower_call_scope_without_granting_more_targets() -> None:
    """Only the missing/failed file may be writable in this call; delivery gates still require both."""
    guidance = _guidance()
    audit = _audit(guidance=guidance, tool_paths=["tests/verify.test.ts"])

    assert guidance["write_targets"] == ["tests/verify.test.ts", "README.md"]
    assert not any(row["code"] == "task_write_tool_scope_mismatch" for row in audit["context_quality"]["findings"])
