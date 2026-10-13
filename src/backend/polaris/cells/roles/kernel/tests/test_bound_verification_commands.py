"""Pinned recipe hashes authorize exact commands and cwd before dispatch."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.chief_engineer.blueprint.public.contracts import VerificationCommandAuthorityV1
from polaris.cells.roles.kernel.internal.kernel.core import RoleExecutionKernel
from polaris.cells.roles.kernel.internal.kernel.tool_executor import KernelToolExecutor
from polaris.cells.roles.kernel.internal.tool_gateway import ToolAuthorizationError
from polaris.cells.roles.profile.public import RoleProfile, RoleTurnRequest


def _gateway(tmp_path: Path, *, alteration: str = "") -> Any:
    authority = VerificationCommandAuthorityV1(task_id="TASK-VERIFY", modality="test", argv=("python", "verify.py"))
    row = authority.to_dict()
    token: dict[str, Any] = {"token_id": "job", "gate_policy": {"command_authority_hashes": [authority.authority_hash]}}
    if alteration in {"command", "cwd", "task_id", "authority_hash"}:
        row[alteration] = {
            "command": "python unauthorized.py",
            "cwd": "other",
            "task_id": "OTHER",
            "authority_hash": "f" * 64,
        }[alteration]
    elif alteration == "argv":
        row["argv"] = ["python", "unauthorized.py"]
    if alteration == "empty":
        token["gate_policy"]["command_authority_hashes"] = []
    rows = [] if alteration == "missing_rows" else [row]
    request = RoleTurnRequest(
        workspace=str(tmp_path),
        message="Verify the authorized task",
        task_id="TASK-VERIFY",
        context_override={
            "verification_authority": "chief_engineer",
            "allowed_commands": ["python verify.py", "python unauthorized.py"],
            "project_completion_contract": {"verification_command_authority": rows},
            "metadata": {"control_plane_job_token": deepcopy(token)},
        },
    )
    profile = RoleProfile(role_id="director", display_name="Director", description="Bound command test")
    profile = replace(
        profile, tool_policy=replace(profile.tool_policy, whitelist=["execute_command"], allow_command_execution=True)
    )
    return KernelToolExecutor(RoleExecutionKernel(workspace=str(tmp_path)), str(tmp_path)).create_gateway(
        profile, request
    )


@pytest.mark.parametrize("alteration", ["command", "cwd", "task_id", "authority_hash", "argv", "missing_rows"])
def test_modified_recipe_or_missing_rows_cannot_reconstitute_bound_authority(tmp_path: Path, alteration: str) -> None:
    with pytest.raises(ValueError, match="verification_command_authority_rows_missing_or_mismatched"):
        _gateway(tmp_path, alteration=alteration)


def test_valid_hash_allows_exact_recipe_and_blocks_extra_metadata_command(tmp_path: Path) -> None:
    gateway = _gateway(tmp_path)
    assert gateway.check_tool_permission("execute_command", {"command": "python verify.py"})[0] is True
    with pytest.raises(ToolAuthorizationError, match="token-bound"):
        gateway.execute_tool("execute_command", {"command": "python unauthorized.py"})


def test_valid_command_cannot_change_authorized_cwd(tmp_path: Path) -> None:
    (tmp_path / "other").mkdir()
    gateway = _gateway(tmp_path)
    with pytest.raises(ToolAuthorizationError, match="token-bound"):
        gateway.execute_tool("execute_command", {"command": "python verify.py", "working_dir": "other"})


def test_explicit_empty_command_hash_binding_denies_execution(tmp_path: Path) -> None:
    gateway = _gateway(tmp_path, alteration="empty")
    with pytest.raises(ToolAuthorizationError, match="token-bound"):
        gateway.execute_tool("execute_command", {"command": "python verify.py"})


def test_ce_public_contract_keeps_original_command_authority_receipt_hash() -> None:
    authority = VerificationCommandAuthorityV1(task_id="TASK-A", modality="test", argv=("python", "-m", "pytest", "-q"))
    assert authority.authority_hash == "c85709988047d0b899b2bbca89ba6be90a969ad77e252ef35dc1c6df0f7ae4f0"
