"""Action prompts must not promote project inventory into current write duties."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from polaris.cells.roles.adapters.internal.director.adapter import _prepare_role_dialogue_context
from polaris.cells.roles.adapters.internal.director.execute_method import _project_completion_targets_into_task
from polaris.cells.roles.kernel.internal.context_gateway.context_override_processor import ContextOverrideProcessor
from polaris.cells.roles.kernel.internal.transaction.task_contract_builder import (
    build_single_batch_task_contract_hint,
)
from polaris.kernelone.tool_execution.forced_tool_surface import build_forced_tool_surface

from .test_director_adapter_build_evidence import _make_adapter


def _context() -> dict:
    return {
        "target_files": ["package.json", "tests/verify.test.ts", "README.md", "index.html"],
        "director_execution_envelope": {
            "authorization": {"allowed_write_paths": ["tests/verify.test.ts", "README.md"]},
        },
    }


def test_message_does_not_demand_inventory_writes_outside_effective_scope(tmp_path: Path) -> None:
    context = _context()
    task = {"subject": "Create tests and documentation", "metadata": deepcopy(context)}
    original_task = deepcopy(task)
    message = _make_adapter(tmp_path)._build_director_message(task, context=context)

    assert "目标文件: tests/verify.test.ts, README.md" in message
    assert "目标文件: package.json" not in message
    assert "reference_only_targets" in message
    assert "package.json" in message and "index.html" in message
    assert task == original_task


def test_current_boundary_uses_effective_scope_not_inventory() -> None:
    source = _context()
    original = deepcopy(source)
    context, _ = _prepare_role_dialogue_context(source, timeout_seconds=300, stage_label="first_call")

    assert context["current_task_write_boundary"]["current_target_files"] == [
        "tests/verify.test.ts",
        "README.md",
    ]
    assert context["task_write_guidance"]["write_targets"] == ["tests/verify.test.ts", "README.md"]
    assert source == original


def test_single_batch_hint_does_not_promote_paths_mentioned_as_references() -> None:
    context = [
        {
            "role": "user",
            "content": "Create tests/verify.test.ts and README.md; package.json and index.html are reference context.",
            "metadata": {
                "task_write_guidance": {
                    "schema_version": "task.write_guidance.v1",
                    "write_targets": ["tests/verify.test.ts", "README.md"],
                    "reference_only_targets": ["package.json", "index.html"],
                },
            },
        }
    ]
    tools = build_forced_tool_surface(["write_file"], pin_write_paths=["tests/verify.test.ts", "README.md"])

    hint, _ = build_single_batch_task_contract_hint(context, tools)

    coverage_line = next(line for line in hint.splitlines() if line.startswith("Mutation target files"))
    assert "tests/verify.test.ts, README.md" in coverage_line
    assert "package.json" not in coverage_line and "index.html" not in coverage_line


def test_completion_projection_filters_inventory_before_envelope_is_built() -> None:
    inventory = ["package.json", "tests/verify.test.ts", "README.md", "index.html"]
    owned = ["tests/verify.test.ts", "README.md"]
    task = {"target_files": inventory, "metadata": {"target_files": inventory}}
    original = deepcopy(task)
    context = {
        "job_token": {"token_id": "job-tests", "allowed_write_paths": owned, "allowed_paths": inventory},
        "metadata": {
            "task_completion_projection": {
                "schema_version": "polaris.task_completion_projection.v1",
                "task_id": "TASK-TESTS",
                "owned_artifacts": [
                    {"path": path, "owner_task_id": "TASK-TESTS", "obligation_id": path} for path in owned
                ],
            },
        },
    }

    projected = _project_completion_targets_into_task(task, context, target_task_id="TASK-TESTS")

    assert projected["target_files"] == owned
    assert projected["metadata"]["target_files"] == owned
    assert projected["metadata"]["project_declared_target_files"] == inventory
    assert task == original


def test_single_batch_hint_uses_real_tool_scope_without_message_metadata() -> None:
    context = [
        {"role": "user", "content": "Create tests/verify.test.ts and README.md; reference package.json and index.html."}
    ]
    tools = build_forced_tool_surface(["write_file"], pin_write_paths=["tests/verify.test.ts", "README.md"])

    hint, _ = build_single_batch_task_contract_hint(context, tools)

    coverage_line = next(line for line in hint.splitlines() if line.startswith("Mutation target files"))
    assert "package.json" not in coverage_line and "index.html" not in coverage_line


def test_safe_guidance_survives_context_os_but_raw_control_helper_does_not() -> None:
    context, _ = _prepare_role_dialogue_context(_context(), timeout_seconds=300, stage_label="first_call")
    original = deepcopy(context)

    message = ContextOverrideProcessor(detect_prompt_injection=True).process_context_override(context)

    assert message is not None
    assert "task_write_guidance: " + str(context["task_write_guidance"]) in message["content"]
    assert "current_task_write_boundary:" not in message["content"]
    assert context == original
