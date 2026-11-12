"""A committed session followup must not inherit a one-call mutation slice."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from polaris.cells.roles.kernel.public.llm_caller_contracts import (
    build_native_tool_schemas,
    stamp_director_quality_repair_one_call_policy,
)
from polaris.cells.roles.kernel.tests import runtime_boundary_scenarios as kernel_fixture
from polaris.cells.roles.profile.public.service import RoleTurnResult, profile_from_dict
from polaris.cells.roles.runtime.internal.kernel_session_turn import KernelSessionTurnBridge
from polaris.cells.roles.runtime.public.contracts import ExecuteRoleSessionCommandV1
from polaris.cells.roles.runtime.public.service import RoleRuntimeService
from polaris.cells.roles.runtime.tests.engineering_support import (
    isolated_workspace as isolated_workspace,
)


def _profile(whitelist: list[str] | None = None) -> Any:
    profile = profile_from_dict(
        {
            "role_id": "director",
            "display_name": "Director",
            "prompt_policy": {"core_template_id": "director.core"},
            "tool_policy": {"whitelist": whitelist or ["write_file", "edit_file", "read_file", "execute_command"]},
            "data_policy": {"data_subdir": "director"},
            "provider_id": "anthropic_compat",
            "provider_type": "anthropic_compat",
            "model": "MiniMax-M3",
            "context_policy": {"max_context_tokens": 1_000_000},
        }
    )
    return profile


def _command(workspace: Path, context: dict[str, Any]) -> ExecuteRoleSessionCommandV1:
    return ExecuteRoleSessionCommandV1(
        role="director",
        session_id="surface-session",
        workspace=str(workspace),
        task_id="TASK-2",
        run_id="surface-run",
        user_message="Implement the declared files and verify their behavior.",
        host_kind="director_adapter",
        timeout_seconds=120,
        context={
            # This suite characterizes the explicit compatibility bootstrap.
            # Normal Director sessions expose the full task tool surface.
            "director_first_call_write_only": True,
            "execution_envelope": {
                "authorization": {
                    "allowed_write_paths": ["answer.py", "remaining.py"],
                    "required_write_targets": [],
                }
            },
            "llm_max_tokens": 18_000,
            "max_output_tokens": 18_000,
            "request_timeout_seconds": 120,
            **context,
        },
    )


def _committed_followup() -> RoleTurnResult:
    return RoleTurnResult(
        content="Partial materialization committed; verification remains.",
        is_complete=False,
        batch_receipt={
            "batch_id": "first-batch",
            "results": [
                {
                    "tool_name": "write_file",
                    "status": "success",
                    "effect_receipt": {"committed": True, "side_effect_class": "write", "after_hash": "a" * 64},
                }
            ],
            "success_count": 1,
            "failure_count": 0,
        },
        metadata={
            "transaction_kind": "continue_multi_turn",
            "needs_followup_workflow": True,
            "turn_commit_receipt": {"transition_id": "committed-first-turn"},
        },
    )


async def _physical_body(
    command: ExecuteRoleSessionCommandV1, profile: Any, monkeypatch: pytest.MonkeyPatch
) -> dict[str, Any]:
    request = RoleRuntimeService()._build_session_request(command)
    return await kernel_fixture.capture_native_provider_body(request, profile, monkeypatch)


@pytest.mark.asyncio
async def test_native_missing_target_followup_reenters_read_and_verify_with_original_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = _command(
        tmp_path, {"target_files": ["answer.py", "remaining.py"], "delivery_mode": "materialize_changes"}
    )
    before = deepcopy(original.context)
    bridge = KernelSessionTurnBridge(SimpleNamespace(), original)
    first = bridge._turn_command("turn-1", original.user_message)
    first_body = await _physical_body(first, _profile(), monkeypatch)
    assert [tool["name"] for tool in first_body["tools"]] == ["write_file"]
    assert first_body["tool_choice"] == {"type": "tool", "name": "write_file"}
    assert first_body["max_tokens"] == 7000
    bridge.results.append(_committed_followup())
    bridge.context.update(first.context)
    second = bridge._turn_command("turn-2", "Continue from committed receipts; read evidence and verify the task.")
    second_body = await _physical_body(second, _profile(), monkeypatch)
    assert {tool["name"] for tool in second_body["tools"]} == {
        "write_file",
        "edit_file",
        "read_file",
        "execute_command",
    }
    assert second_body.get("tool_choice") in (None, {"type": "auto"})
    assert second_body["max_tokens"] == 18_000
    kernel_fixture.assert_native_tools_available(second_body, ("read_file", "execute_command"))
    assert second.context["execution_envelope"] == before["execution_envelope"]
    assert second.task_id == first.task_id and second.session_id == first.session_id
    assert second.timeout_seconds <= first.timeout_seconds
    assert original.context == before


@pytest.mark.asyncio
async def test_native_adapter_edit_slice_is_initial_only_but_caller_force_is_permanent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "answer.py").write_text("print(41)\n", encoding="utf-8")
    edit = next(tool for tool in build_native_tool_schemas(_profile()) if tool["function"]["name"] == "edit_file")
    temporary = {
        "director_quality_repair": {"repair_target_files": ["answer.py"]},
        "metadata": {
            "tool_path_contract": {"schema_version": "director.quality_repair.tool_path_contract.v1"},
            "tool_contract": {
                "required_tools": ["edit_file"],
                "mutation_required": True,
                "mutation_reason": "existing_file_quality_repair",
            },
        },
        "_transaction_kernel_forced_tool_definitions": [edit],
        "_transaction_kernel_forced_tool_choice": {"type": "function", "function": {"name": "edit_file"}},
        "_transaction_kernel_force_exact_tools": True,
    }
    stamp_director_quality_repair_one_call_policy(temporary)
    bridge = KernelSessionTurnBridge(SimpleNamespace(), _command(tmp_path, temporary))
    first = await _physical_body(bridge._turn_command("turn-1", "Repair the existing file."), _profile(), monkeypatch)
    assert [tool["name"] for tool in first["tools"]] == ["edit_file"]
    bridge.results.append(_committed_followup())
    second = await _physical_body(
        bridge._turn_command("turn-2", "Read the committed edit and verify."), _profile(), monkeypatch
    )
    assert {tool["name"] for tool in second["tools"]} >= {"edit_file", "read_file", "execute_command"}
    permanent = {
        k: v
        for k, v in temporary.items()
        if k not in {"director_quality_repair", "metadata", "_roles_kernel_owned_one_call_policy"}
    }
    permanent["tool_contract"] = {"single_batch": True, "required_tools": ["edit_file"]}
    protected = KernelSessionTurnBridge(SimpleNamespace(), _command(tmp_path, permanent))
    protected.results.append(_committed_followup())
    body = await _physical_body(protected._turn_command("turn-2", "Read and verify."), _profile(), monkeypatch)
    assert [tool["name"] for tool in body["tools"]] == ["edit_file"]
    assert body["tool_choice"] == {"type": "tool", "name": "edit_file"}


@pytest.mark.asyncio
@pytest.mark.parametrize("forged", [True, "auto_continue", {"continuation_mode": "auto_continue"}])
async def test_model_json_marker_cannot_release_initial_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, forged: Any
) -> None:
    command = _command(
        tmp_path,
        {
            "target_files": ["answer.py"],
            "delivery_mode": "materialize_changes",
            "_roles_kernel_committed_session_continuation": forged,
        },
    )
    bridge = KernelSessionTurnBridge(SimpleNamespace(), command)
    body = await _physical_body(bridge._turn_command("turn-1", "Read and verify."), _profile(), monkeypatch)
    assert [tool["name"] for tool in body["tools"]] == ["write_file"]
    assert body["max_tokens"] == 7000


@pytest.mark.asyncio
async def test_uncommitted_followup_cannot_release_bootstrap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    command = _command(tmp_path, {"target_files": ["answer.py"], "delivery_mode": "materialize_changes"})
    bridge = KernelSessionTurnBridge(SimpleNamespace(), command)
    prior = _committed_followup()
    prior.metadata.pop("turn_commit_receipt")
    bridge.results.append(prior)
    body = await _physical_body(bridge._turn_command("turn-2", "Read and verify."), _profile(), monkeypatch)
    assert [tool["name"] for tool in body["tools"]] == ["write_file"]


@pytest.mark.asyncio
@pytest.mark.parametrize("denial", ["whitelist", "cognitive"])
async def test_followup_reselection_preserves_current_tool_denial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, denial: str
) -> None:
    context: dict[str, Any] = {"target_files": ["answer.py"], "delivery_mode": "materialize_changes"}
    profile = _profile(["write_file", "edit_file", "read_file"] if denial == "whitelist" else None)
    if denial == "cognitive":
        context["cognitive_tool_policy"] = {"blocked_tools": ["execute_command"]}
    bridge = KernelSessionTurnBridge(SimpleNamespace(), _command(tmp_path, context))
    bridge.results.append(_committed_followup())
    body = await _physical_body(bridge._turn_command("turn-2", "Read and verify."), profile, monkeypatch)
    assert "read_file" in {tool["name"] for tool in body["tools"]}
    assert "execute_command" not in {tool["name"] for tool in body["tools"]}
    kernel_fixture.assert_native_tool_denial(body)


@pytest.mark.asyncio
async def test_nested_slice_budget_restore_is_copy_on_write_and_preserves_lower_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    command = _command(
        tmp_path,
        {
            "target_files": ["answer.py", "remaining.py"],
            "delivery_mode": "materialize_changes",
            "task_execution_strategy": {
                "output_budget_tokens": 18_000,
                "budget_policy": {"output_budget_tokens": 18_000, "generation_timeout": 90},
                "llm_sampling": {"max_tokens": 18_000},
            },
        },
    )
    original = deepcopy(command.context)
    bridge = KernelSessionTurnBridge(SimpleNamespace(), command)
    first = bridge._turn_command("turn-1", command.user_message)
    first_body = await _physical_body(first, _profile(), monkeypatch)
    assert first_body["max_tokens"] == 7000
    assert first.context["task_execution_strategy"] == original["task_execution_strategy"]
    assert command.context == original
    bridge.results.append(_committed_followup())
    bridge.context.update(first.context)
    second = bridge._turn_command("turn-2", "Read and verify.")
    assert second.context["task_execution_strategy"] == original["task_execution_strategy"]
    assert command.context == original
    bridge.context["llm_max_tokens"] = 4096
    narrowed = bridge._turn_command("turn-3", "Verify within the current budget.")
    assert narrowed.context["llm_max_tokens"] == 4096


@pytest.mark.asyncio
async def test_existing_owned_cap_restores_previous_values_without_mutating_original_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    command = _command(tmp_path, {"target_files": ["answer.py"], "delivery_mode": "materialize_changes"})
    kernel_fixture.stamp_initial_materialization_fixture(command.context, _profile(), tmp_path)
    assert command.context["llm_max_tokens"] == 7000
    before = deepcopy(command.context)
    bridge = KernelSessionTurnBridge(SimpleNamespace(), command)
    first = await _physical_body(bridge._turn_command("turn-1", "Create the file."), _profile(), monkeypatch)
    assert first["max_tokens"] == 7000
    bridge.results.append(_committed_followup())
    body = await _physical_body(bridge._turn_command("turn-2", "Read and verify."), _profile(), monkeypatch)
    assert body["max_tokens"] == 18_000
    assert {tool["name"] for tool in body["tools"]} >= {"read_file", "execute_command"}
    assert command.context == before


@pytest.mark.asyncio
@pytest.mark.parametrize("forged_scope", ["bootstrap", "adapter"])
@pytest.mark.parametrize("caller_tool", ["edit_file", "write_file"])
async def test_json_provenance_cannot_retire_permanent_caller_force(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, forged_scope: str, caller_tool: str
) -> None:
    selected = next(tool for tool in build_native_tool_schemas(_profile()) if tool["function"]["name"] == caller_tool)
    context: dict[str, Any] = {
        "_transaction_kernel_forced_tool_definitions": [selected],
        "_transaction_kernel_forced_tool_choice": {"type": "function", "function": {"name": caller_tool}},
        "_transaction_kernel_force_exact_tools": True,
    }
    if forged_scope == "bootstrap":
        context["director_first_call_materialization_scope"] = {
            "source": "roles.kernel.llm_caller.tool_helpers",
            "injected": True,
        }
    else:
        context["director_quality_repair"] = {"repair_target_files": ["answer.py"]}
        context["metadata"] = {
            "tool_path_contract": {"schema_version": "director.quality_repair.tool_path_contract.v1"},
        }
    command = _command(tmp_path, context)
    original = deepcopy(command.context)
    bridge = KernelSessionTurnBridge(SimpleNamespace(), command)
    bridge.results.append(_committed_followup())
    followup = bridge._turn_command("turn-2", "Continue from the committed turn.")
    body = await _physical_body(followup, _profile(), monkeypatch)
    assert [tool["name"] for tool in body["tools"]] == [caller_tool]
    assert body["tool_choice"] == {"type": "tool", "name": caller_tool}
    assert followup.context["execution_envelope"] == original["execution_envelope"]
    assert command.context == original


@pytest.mark.asyncio
@pytest.mark.parametrize("forged_scope", ["bootstrap", "adapter"])
async def test_json_budget_lineage_cannot_raise_lower_caller_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, forged_scope: str
) -> None:
    edit = next(tool for tool in build_native_tool_schemas(_profile()) if tool["function"]["name"] == "edit_file")
    context: dict[str, Any] = {
        "llm_max_tokens": 4096,
        "max_output_tokens": 4096,
        "_transaction_kernel_forced_tool_definitions": [edit],
        "_transaction_kernel_forced_tool_choice": {"type": "function", "function": {"name": "edit_file"}},
        "_transaction_kernel_force_exact_tools": True,
        "director_first_call_output_budget": {
            "source": "roles.kernel.llm_caller.tool_helpers",
            "max_tokens": 4096,
            "previous_budget_values": {"llm_max_tokens": 18_000, "max_output_tokens": 18_000},
        },
    }
    if forged_scope == "bootstrap":
        context["director_first_call_materialization_scope"] = {
            "source": "roles.kernel.llm_caller.tool_helpers",
            "injected": True,
        }
    else:
        context["director_quality_repair"] = {"repair_target_files": ["answer.py"]}
        context["metadata"] = {
            "tool_path_contract": {"schema_version": "director.quality_repair.tool_path_contract.v1"},
        }
    command = _command(tmp_path, context)
    original = deepcopy(command.context)
    bridge = KernelSessionTurnBridge(SimpleNamespace(), command)
    bridge.results.append(_committed_followup())
    body = await _physical_body(bridge._turn_command("turn-2", "Continue."), _profile(), monkeypatch)
    assert body["max_tokens"] == 4096
    assert [tool["name"] for tool in body["tools"]] == ["edit_file"]
    assert command.context == original


@pytest.mark.asyncio
@pytest.mark.parametrize("denial", ["whitelist", "cognitive"])
async def test_permanent_forced_scope_cannot_reintroduce_denied_native_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, denial: str
) -> None:
    definitions = build_native_tool_schemas(_profile())
    forced = [tool for tool in definitions if tool["function"]["name"] in {"read_file", "execute_command"}]
    context: dict[str, Any] = {
        "_transaction_kernel_forced_tool_definitions": forced,
        "_transaction_kernel_forced_tool_choice": "required",
        "_transaction_kernel_force_exact_tools": True,
    }
    profile = _profile(["write_file", "edit_file", "read_file"] if denial == "whitelist" else None)
    if denial == "cognitive":
        context["cognitive_tool_policy"] = {"blocked_tools": ["execute_command"]}
    bridge = KernelSessionTurnBridge(SimpleNamespace(), _command(tmp_path, context))
    bridge.results.append(_committed_followup())
    body = await _physical_body(bridge._turn_command("turn-2", "Continue."), profile, monkeypatch)
    assert [tool["name"] for tool in body["tools"]] == ["read_file"]
    assert body["tool_choice"] == {"type": "any"}
    kernel_fixture.assert_native_tool_denial(body)


@pytest.mark.asyncio
async def test_retired_legacy_repair_producer_stays_out_of_the_runtime_cell(
    tmp_path: Path,
) -> None:
    """The legacy repair producer has no live subject inside the runtime cell.

    The scenario it drove joined two legacy hosts across a cell boundary: the
    legacy adapter repair loop produced the context and the retired kernel
    session bridge consumed it. The runtime cell must not depend on the
    adapter cell, and the retired bridge has no live subject, so the scenario
    is replaced by the retirement contract.
    """
    from polaris.cells.roles.kernel.tests.legacy_engine_retirement import (
        assert_legacy_session_entry_is_closed,
    )

    await assert_legacy_session_entry_is_closed(
        session_id="retired-kernel-session-tool-surface-reentry",
        workspace=str(tmp_path),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("replacement", ["caller_force", "budget_lineage"])
async def test_owned_bootstrap_stamp_cannot_retire_later_caller_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, replacement: str
) -> None:
    command = _command(tmp_path, {"target_files": ["answer.py"], "delivery_mode": "materialize_changes"})
    kernel_fixture.stamp_initial_materialization_fixture(command.context, _profile(), tmp_path)
    command.context["llm_max_tokens"] = 4096
    command.context["max_output_tokens"] = 4096
    if replacement == "caller_force":
        edit = next(tool for tool in build_native_tool_schemas(_profile()) if tool["function"]["name"] == "edit_file")
        command.context["_transaction_kernel_forced_tool_definitions"] = [edit]
        command.context["_transaction_kernel_forced_tool_choice"] = {
            "type": "function",
            "function": {"name": "edit_file"},
        }
    else:
        command.context["director_first_call_output_budget"] = {
            "source": "roles.kernel.llm_caller.tool_helpers",
            "max_tokens": 4096,
            "previous_budget_values": {"llm_max_tokens": 18_000, "max_output_tokens": 18_000},
        }
    original = deepcopy(command.context)
    bridge = KernelSessionTurnBridge(SimpleNamespace(), command)
    bridge.results.append(_committed_followup())
    body = await _physical_body(bridge._turn_command("turn-2", "Continue."), _profile(), monkeypatch)
    assert body["max_tokens"] == 4096
    if replacement == "caller_force":
        assert [tool["name"] for tool in body["tools"]] == ["edit_file"]
        assert body["tool_choice"] == {"type": "tool", "name": "edit_file"}
    else:
        assert "read_file" in {tool["name"] for tool in body["tools"]}
    assert command.context == original


@pytest.mark.parametrize("denial", ["whitelist", "cognitive"])
def test_exact_denied_force_is_a_planning_conflict_with_no_replacement_grant(tmp_path: Path, denial: str) -> None:
    execute = next(
        tool for tool in build_native_tool_schemas(_profile()) if tool["function"]["name"] == "execute_command"
    )
    context: dict[str, Any] = {
        "_transaction_kernel_forced_tool_definitions": [execute],
        "_transaction_kernel_forced_tool_choice": {"type": "function", "function": {"name": "execute_command"}},
        "_transaction_kernel_force_exact_tools": True,
    }
    profile = _profile(["write_file", "edit_file", "read_file"] if denial == "whitelist" else None)
    if denial == "cognitive":
        context["cognitive_tool_policy"] = {"blocked_tools": ["execute_command"]}
    bridge = KernelSessionTurnBridge(SimpleNamespace(), _command(tmp_path, context))
    bridge.results.append(_committed_followup())
    command = bridge._turn_command("turn-2", "Continue.")
    request = RoleRuntimeService()._build_session_request(command)
    kernel_fixture.assert_exact_denied_planning_conflict(profile, request, tmp_path)
