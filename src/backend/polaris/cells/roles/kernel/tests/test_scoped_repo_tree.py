"""Gateway must permit only filtered ancestor enumeration."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from polaris.cells.roles.kernel.internal.tool_gateway import RoleToolGateway, ToolAuthorizationError
from polaris.cells.roles.profile.public.service import RoleProfile, RoleToolPolicy


def gateway(workspace: Path, token: dict[str, Any]) -> RoleToolGateway:
    profile = RoleProfile(
        role_id="chief_engineer",
        display_name="Chief Engineer",
        description="scope test",
        tool_policy=RoleToolPolicy(whitelist=["repo_tree", "read_file", "file_exists"]),
    )
    return RoleToolGateway(profile, str(workspace), capability_token=token)


@pytest.mark.parametrize("args", [{}, {"path": "."}, {"root": "."}])
def test_gateway_dispatches_real_filtered_ancestor_view(tmp_path: Path, args: dict[str, Any]) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "owned.py").write_text("owned", encoding="utf-8")
    (tmp_path / "src" / "secret.py").write_text("secret", encoding="utf-8")
    gate = gateway(tmp_path, {"allowed_read_paths": ["src/owned.py"]})
    result = gate.execute_tool("repo_tree", args)
    assert result["success"] is True
    assert "owned.py" in str(result["result"])
    assert "secret.py" not in str(result["result"])
    for tool, foreign_args in [("read_file", {"file": "src/secret.py"}), ("file_exists", {"path": "src"})]:
        with pytest.raises(ToolAuthorizationError):
            gate.execute_tool(tool, foreign_args)


@pytest.mark.parametrize("scope", [None, [], "src", [""], [1]])
def test_gateway_explicit_invalid_scope_denied(tmp_path: Path, scope: Any) -> None:
    gate = gateway(tmp_path, {"allowed_read_paths": scope})
    with pytest.raises(ToolAuthorizationError):
        gate.execute_tool("repo_tree", {})
