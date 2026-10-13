"""Real registered-handler regressions for committed read scope."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
from polaris.kernelone.llm.toolkit.executor import AgentAccelToolExecutor


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    for i in range(8):
        path = tmp_path / "src" / f"allowed{i}.py"
        path.parent.mkdir(exist_ok=True)
        path.write_text(f"allowed {i}\n", encoding="utf-8")
    (tmp_path / "src" / "secret.py").write_text("foreign-secret-content", encoding="utf-8")
    (tmp_path / "private").mkdir()
    (tmp_path / "private" / "credential.txt").write_text("private", encoding="utf-8")
    (tmp_path / "src" / ".hidden").write_text("hidden", encoding="utf-8")
    (tmp_path / "src" / "nested").mkdir()
    (tmp_path / "src" / "nested" / "deep.py").write_text("deep", encoding="utf-8")
    return tmp_path


def execute(workspace: Path, token: dict[str, Any], tool: str, args: dict[str, Any]) -> dict[str, Any]:
    with AgentAccelToolExecutor(str(workspace), capability_token=token) as executor:
        return executor.execute(tool, args)


def file_scope() -> dict[str, Any]:
    return {"allowed_read_paths": [f"src/allowed{i}.py" for i in range(8)]}


@pytest.mark.parametrize("args", [{}, {"path": "."}, {"root": "."}, {"dir": "."}])
def test_root_filters_actual_registered_result(workspace: Path, args: dict[str, Any]) -> None:
    token = file_scope()
    original = copy.deepcopy((token, args))
    result = execute(workspace, token, "repo_tree", {**args, "depth": 3})
    assert result["ok"] is True
    payload = result["result"]
    assert {entry["path"] for entry in payload["entries"]} == {"src", *(f"src/allowed{i}.py" for i in range(8))}
    assert payload["scope_filtered"] is True
    assert "secret" not in str(payload) and "private" not in str(payload) and ".hidden" not in str(payload)
    assert (token, args) == original


@pytest.mark.parametrize("scope", [None, [], "src", [""], [1], ["src/allowed0.py", ""]])
def test_explicit_invalid_scope_fails_closed(workspace: Path, scope: Any) -> None:
    assert execute(workspace, {"allowed_read_paths": scope}, "repo_tree", {})["ok"] is False


@pytest.mark.parametrize(
    "tool,args",
    [
        ("read_file", {"file": "src/secret.py"}),
        ("file_exists", {"path": "private"}),
        ("repo_tree", {"path": "private"}),
        ("repo_rg", {"path": ".", "pattern": "foreign-secret"}),
    ],
)
def test_foreign_read_and_root_denied(workspace: Path, tool: str, args: dict[str, Any]) -> None:
    result = execute(workspace, file_scope(), tool, args)
    assert result["ok"] is False
    assert "foreign-secret-content" not in str(result)


def test_depth_and_entry_limit_independent(workspace: Path) -> None:
    token = {"allowed_read_paths": ["src"]}
    shallow = execute(workspace, token, "repo_tree", {"depth": 1})["result"]
    assert {entry["path"] for entry in shallow["entries"]} == {"src"}
    deeper = execute(workspace, token, "repo_tree", {"depth": 2})["result"]
    assert len(deeper["entries"]) > 3
    assert "src/nested/deep.py" not in {entry["path"] for entry in deeper["entries"]}
    limited = execute(workspace, token, "repo_tree", {"depth": 3, "max_entries": 2})["result"]
    assert len(limited["entries"]) == 2


def test_scoped_subdirectory_and_missing_authorized_output(workspace: Path) -> None:
    result = execute(workspace, {"allowed_read_paths": ["future/output.py"]}, "repo_tree", {})
    assert result["ok"] is True
    assert result["result"]["entries"] == []
    result = execute(workspace, file_scope(), "repo_tree", {"path": "src"})
    assert result["ok"] is True
    assert {entry["path"] for entry in result["result"]["entries"]} == {f"allowed{i}.py" for i in range(8)}


def test_directory_scope_excludes_symlink_and_hidden(workspace: Path) -> None:
    (workspace / "src" / "escape").symlink_to(workspace / "private", target_is_directory=True)
    token = {"allowed_read_paths": ["src"]}
    result = execute(workspace, token, "repo_tree", {})
    assert result["ok"] is True
    assert "escape" not in str(result["result"]) and ".hidden" not in str(result["result"])
    assert execute(workspace, token, "repo_tree", {"path": "src/escape"})["ok"] is False
    assert execute(workspace, {"allowed_read_paths": ["src/escape"]}, "repo_tree", {})["ok"] is False


def test_undefined_scope_legacy_read_and_real_depth(workspace: Path) -> None:
    assert execute(workspace, {}, "read_file", {"file": "src/secret.py"})["ok"] is True
    result = execute(workspace, {}, "repo_tree", {"depth": 2})
    assert result["ok"] is True
    paths = {entry["path"] for entry in result["result"]["entries"]}
    assert "src/secret.py" in paths and len(paths) > 3
    assert "src/nested/deep.py" not in paths


def test_absolute_roots_and_authorized_read(workspace: Path) -> None:
    token = file_scope()
    result = execute(workspace, token, "repo_tree", {"root": str(workspace)})
    assert result["ok"] is True
    assert "secret.py" not in str(result["result"])
    assert execute(workspace, token, "read_file", {"path": "src/allowed0.py"})["ok"] is True
    assert execute(workspace, token, "repo_tree", {"path": str(workspace.parent)})["ok"] is False


def test_missing_authorized_file_and_hidden_scope(workspace: Path) -> None:
    token = {"allowed_read_paths": ["future/output.py"]}
    result = execute(workspace, token, "file_exists", {"path": "future/output.py"})
    assert result["ok"] is True
    assert result["result"]["exists"] is False
    hidden = execute(workspace, {"allowed_read_paths": ["src/.hidden"]}, "repo_tree", {})
    assert hidden["ok"] is True
    assert ".hidden" not in str(hidden["result"])


def test_outside_workspace_symlink_never_materialized(workspace: Path) -> None:
    outside = workspace.parent / "outside-secret.txt"
    outside.write_text("external secret", encoding="utf-8")
    (workspace / "src" / "outside-link").symlink_to(outside)
    token = {"allowed_read_paths": ["src"]}
    result = execute(workspace, token, "repo_tree", {})
    assert result["ok"] is True
    assert "outside" not in str(result["result"])
    assert execute(workspace, token, "read_file", {"file": "src/outside-link"})["ok"] is False
