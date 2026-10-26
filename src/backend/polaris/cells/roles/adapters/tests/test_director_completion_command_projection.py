"""CE task-local command rows survive the real Director runtime boundary."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.chief_engineer.blueprint.public.contracts import VerificationCommandAuthorityV1
from polaris.cells.roles.adapters.internal.director.adapter import DirectorAdapter
from polaris.cells.roles.kernel.tests.adapters_kernel_boundary_scenarios import observe_command_projection_permission


def _context() -> dict[str, Any]:
    authority = VerificationCommandAuthorityV1(task_id="TASK-1", modality="test", argv=("python", "verify.py"))
    row = authority.to_dict()
    return {
        "task_id": "1",
        "pm_task_id": "TASK-1",
        "run_id": "factory-command-projection",
        "metadata": {
            "external_task_id": "TASK-1",
            "verification_authority": "chief_engineer",
            "verification_commands": [],
            "control_plane_job_token": {
                "token_id": "job-projection",
                "run_id": "factory-command-projection",
                "factory_run_id": "factory-command-projection",
                "gate_policy": {
                    "project_completion_contract_hash": "c" * 64,
                    "command_authority_hashes": [authority.authority_hash],
                },
            },
            "task_completion_projection": {
                "schema_version": "polaris.task_completion_projection.v1",
                "task_id": "TASK-1",
                "run_id": "factory-command-projection",
                "project_contract_hash": "c" * 64,
                "verification_command_authority": [row],
                "verification_execution_authority": [
                    {"command": row["command"], "command_authority_hash": row["authority_hash"]}
                ],
            },
        },
    }


def _promote(context: dict[str, Any], workspace: Path) -> None:
    task = {"id": "1", "metadata": deepcopy(context["metadata"])}
    DirectorAdapter._promote_task_contract_to_runtime_context(task=task, context=context, workspace=str(workspace))
    DirectorAdapter._ensure_director_verification_commands(message="Implement requested behavior", context=context)


def _gateway(context: dict[str, Any], workspace: Path) -> tuple[bool, Any]:
    return observe_command_projection_permission(context, workspace)


@pytest.mark.parametrize("child_run", [False, True])
def test_claimed_numeric_task_projects_exact_ce_rows_into_real_gateway(tmp_path: Path, child_run: bool) -> None:
    context = _context()
    if child_run:
        context["metadata"]["factory_run_id"] = context["run_id"]
        context["run_id"] = "director-child-workflow"
    original = deepcopy(context["metadata"]["control_plane_job_token"])
    rows = deepcopy(context["metadata"]["task_completion_projection"]["verification_command_authority"])
    _promote(context, tmp_path)
    gateway = _gateway(context, tmp_path)
    assert gateway[0] is True
    assert context["verification_command_authority"] == rows
    assert context["metadata"]["verification_command_authority"] == rows
    assert context["verification_commands"] == ["python verify.py"]
    assert context["metadata"]["control_plane_job_token"] == original
    assert context["run_id"] == ("director-child-workflow" if child_run else "factory-command-projection")


@pytest.mark.parametrize("field", ["argv", "cwd", "modality", "task_id", "authority_hash", "command", "missing"])
def test_bad_completion_row_remains_rejected_by_real_guard(tmp_path: Path, field: str) -> None:
    context = _context()
    rows = context["metadata"]["task_completion_projection"]["verification_command_authority"]
    if field == "missing":
        rows.clear()
    else:
        rows[0][field] = ["python", "other.py"] if field == "argv" else "invalid"
    _promote(context, tmp_path)
    with pytest.raises(ValueError, match="verification_command_authority_rows_missing_or_mismatched"):
        _gateway(context, tmp_path)


@pytest.mark.parametrize("container", ["context", "metadata", "project_completion_contract"])
def test_existing_mismatched_authority_is_never_hidden_by_valid_projection(tmp_path: Path, container: str) -> None:
    context = _context()
    bad = deepcopy(context["metadata"]["task_completion_projection"]["verification_command_authority"])
    bad[0]["cwd"] = "other"
    destination = context if container == "context" else context["metadata"]
    if container == "project_completion_contract":
        destination = context.setdefault("project_completion_contract", {})
    destination["verification_command_authority"] = bad
    _promote(context, tmp_path)
    with pytest.raises(ValueError, match="verification_command_authority_rows_missing_or_mismatched"):
        _gateway(context, tmp_path)


@pytest.mark.parametrize("field", ["run_id", "task_id", "project_contract_hash", "factory_run_id"])
def test_foreign_completion_projection_cannot_populate_authority(tmp_path: Path, field: str) -> None:
    context = _context()
    if field == "factory_run_id":
        context["metadata"][field] = "foreign"
    else:
        context["metadata"]["task_completion_projection"][field] = "foreign"
    _promote(context, tmp_path)
    with pytest.raises(ValueError, match="verification_command_authority_rows_missing_or_mismatched"):
        _gateway(context, tmp_path)


@pytest.mark.asyncio
async def test_actual_adapter_projection_reaches_persistent_http_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reuse sealed HTTP/effect fixture through the actual adapter call boundary."""
    from polaris.cells.roles.kernel.tests.adapters_kernel_boundary_scenarios import (
        run_persistent_director_http_projection,
    )
    from polaris.cells.roles.runtime.public.service import RoleRuntimeService

    original_execute = RoleRuntimeService.execute_role_session
    original_init = RoleRuntimeService.__init__
    inner = False
    captured: dict[str, Any] = {}

    async def through_adapter(runtime: Any, command: Any) -> Any:
        nonlocal inner
        if inner:
            captured["command"] = command
            result = await original_execute(runtime, command)
            captured["result"] = result
            return result
        context = dict(command.context)
        metadata = deepcopy(context["metadata"])
        token = metadata["control_plane_job_token"]
        authority = VerificationCommandAuthorityV1(
            task_id=command.task_id, modality="test", argv=("python", "verify.py"), cwd="."
        )
        token["gate_policy"]["project_completion_contract_hash"] = "c" * 64
        token["gate_policy"]["command_authority_hashes"] = [authority.authority_hash]
        token.pop("allowed_commands", None)
        context.pop("verification_commands", None)
        context["task_id"] = str(context["task_runtime_execution_attempt_authority"].snapshot().identity.task_id)
        context["pm_task_id"] = command.task_id
        context["run_id"] = command.run_id
        context["session_id"] = command.session_id
        metadata["verification_authority"] = "chief_engineer"
        metadata["verification_commands"] = []
        metadata["task_completion_projection"] = {
            "schema_version": "polaris.task_completion_projection.v1",
            "task_id": command.task_id,
            "run_id": command.run_id,
            "project_contract_hash": "c" * 64,
            "verification_command_authority": [authority.to_dict()],
            "verification_execution_authority": [
                {"command": "python verify.py", "command_authority_hash": authority.authority_hash}
            ],
        }
        context["metadata"] = metadata
        adapter = DirectorAdapter(str(tmp_path))
        adapter._promote_task_contract_to_runtime_context(
            task={"metadata": deepcopy(metadata)}, context=context, workspace=str(tmp_path)
        )

        def reuse_kernel(instance: Any, *args: Any, **kwargs: Any) -> None:
            original_init(instance, *args, **kwargs)
            instance._kernels[str(tmp_path)] = runtime._kernels[str(tmp_path)]

        monkeypatch.setattr(RoleRuntimeService, "__init__", reuse_kernel)
        inner = True
        result = await adapter._invoke_role_runtime_session(command.user_message, context=context, max_retries=0)
        captured["adapter_result"] = result
        assert captured["command"].task_id == command.task_id
        assert captured["command"].context["verification_command_authority"] == [authority.to_dict()]
        assert captured["command"].context["verification_commands"] == ["python verify.py"]
        return captured["result"]

    monkeypatch.setattr(RoleRuntimeService, "execute_role_session", through_adapter)
    await run_persistent_director_http_projection(tmp_path, monkeypatch)
    assert captured["adapter_result"]["success"] is True
