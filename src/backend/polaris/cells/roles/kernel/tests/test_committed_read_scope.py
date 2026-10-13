"""Committed read scope is enforced before physical reads and searches."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.roles.kernel.internal.kernel.core import RoleExecutionKernel
from polaris.cells.roles.kernel.internal.kernel.tool_executor import KernelToolExecutor
from polaris.cells.roles.kernel.internal.tool_gateway import ToolAuthorizationError
from polaris.cells.roles.profile.public import RoleProfile, RoleTurnRequest


def _gateway(workspace: Path, token: dict[str, Any] | None) -> Any:
    profile = RoleProfile(role_id="director", display_name="Director", description="Committed read-scope test")
    profile = replace(
        profile,
        tool_policy=replace(
            profile.tool_policy,
            whitelist=["read_file", "repo_rg", "repo_tree", "repo_read_slice"],
            max_tool_calls_per_turn=20,
        ),
    )
    request = RoleTurnRequest(
        workspace=str(workspace),
        message="Read only the committed inputs.",
        context_override={"metadata": {"control_plane_job_token": token}} if token is not None else {},
    )
    kernel = RoleExecutionKernel(workspace=str(workspace))
    return KernelToolExecutor(kernel, str(workspace)).create_gateway(profile, request)


@pytest.mark.parametrize(
    "tool,arguments",
    [
        ("read_file", {"file": "private.py"}),
        ("cat", {"path": "private.py"}),
        ("repo_read_slice", {"path": "private.py", "start": 1, "end": 1}),
        ("repo_rg", {"pattern": "PRIVATE_INPUT", "path": "private.py"}),
        ("repo_rg", {"pattern": "PRIVATE_INPUT"}),
        ("repo_tree", {"path": "."}),
    ],
)
def test_committed_scope_rejects_unauthorized_reads_and_unbounded_searches(
    tmp_path: Path, tool: str, arguments: dict[str, Any]
) -> None:
    (tmp_path / "public.py").write_text("PUBLIC_INPUT\n", encoding="utf-8")
    (tmp_path / "private.py").write_text("PRIVATE_INPUT\n", encoding="utf-8")
    gateway = _gateway(tmp_path, {"token_id": "committed", "allowed_read_paths": ["public.py"]})
    with pytest.raises(ToolAuthorizationError, match="read scope"):
        gateway.execute_tool(tool, arguments)


def test_legitimate_dependency_read_and_alias_normalization_preserve_authority(tmp_path: Path) -> None:
    (tmp_path / "dependency.py").write_text("DEPENDENCY_INPUT\n", encoding="utf-8")
    token = {"token_id": "committed", "allowed_write_paths": [], "allowed_read_paths": ["dependency.py"]}
    original = deepcopy(token)
    gateway = _gateway(tmp_path, token)
    result = gateway.execute_tool("cat", {"path": "./dependency.py"})
    assert result["success"] is True
    assert "DEPENDENCY_INPUT" in str(result["result"])
    assert token == original


def test_authorized_search_uses_exact_dependency_path(tmp_path: Path) -> None:
    (tmp_path / "dependency.py").write_text("DEPENDENCY_INPUT\n", encoding="utf-8")
    (tmp_path / "private.py").write_text("PRIVATE_INPUT\n", encoding="utf-8")
    gateway = _gateway(tmp_path, {"token_id": "committed", "allowed_read_paths": ["dependency.py"]})
    result = gateway.execute_tool("grep", {"query": "INPUT", "file_path": "dependency.py"})
    assert result["success"] is True
    assert "DEPENDENCY_INPUT" in str(result["result"])
    assert "PRIVATE_INPUT" not in str(result["result"])


@pytest.mark.parametrize("token", [None, {"token_id": "legacy"}])
def test_planning_and_legacy_undefined_scope_keep_workspace_reads(tmp_path: Path, token: dict[str, Any] | None) -> None:
    (tmp_path / "public.py").write_text("PUBLIC_INPUT\n", encoding="utf-8")
    gateway = _gateway(tmp_path, token)
    assert gateway.execute_tool("read_file", {"file": "public.py"})["success"] is True


def test_explicit_empty_scope_denies_all_workspace_reads(tmp_path: Path) -> None:
    (tmp_path / "public.py").write_text("PUBLIC_INPUT\n", encoding="utf-8")
    gateway = _gateway(tmp_path, {"token_id": "committed", "allowed_read_paths": []})
    with pytest.raises(ToolAuthorizationError, match="read scope"):
        gateway.execute_tool("read_file", {"file": "public.py"})


def test_symlink_and_traversal_do_not_expand_committed_read_scope(tmp_path: Path) -> None:
    (tmp_path / "public.py").write_text("PUBLIC_INPUT\n", encoding="utf-8")
    (tmp_path / "private.py").write_text("PRIVATE_INPUT\n", encoding="utf-8")
    (tmp_path / "alias.py").symlink_to(tmp_path / "private.py")
    gateway = _gateway(tmp_path, {"token_id": "committed", "allowed_read_paths": ["alias.py", "public.py"]})
    with pytest.raises(ToolAuthorizationError):
        gateway.execute_tool("read_file", {"file": "alias.py"})
    with pytest.raises(ToolAuthorizationError):
        gateway.execute_tool("read_file", {"file": "../private.py"})
