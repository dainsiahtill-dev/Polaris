"""Native root aliases retain intent; KFS and explicit host grants retain authority."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from polaris.cells.roles.runtime.internal.engineering_tools import EngineeringTools
from polaris.cells.roles.runtime.public.contracts.engineering_run import PermissionScope, RunLimits
from polaris.infrastructure.storage.local_fs_adapter import LocalFileSystemAdapter
from polaris.kernelone.fs import KernelFileSystem
from polaris.kernelone.llm.toolkit.parsers.core import parse_tool_calls
from polaris.kernelone.llm.toolkit.tool_normalization import normalize_tool_arguments_from_snapshot
from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    filesystem = KernelFileSystem(str(tmp_path), LocalFileSystemAdapter())
    filesystem.workspace_mkdir("sub", parents=True, exist_ok=True)
    filesystem.workspace_write_text_atomic("sub/marker.txt", "synthetic UTF-8 反例\n", encoding="utf-8")
    return tmp_path


def native_call(name: str, arguments: dict[str, object]):
    calls = parse_tool_calls(
        tool_calls=[
            {
                "id": "synthetic-root",
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)},
            }
        ],
        allowed_tool_names=[name],
        allow_text_fallback=False,
    )
    assert len(calls) == 1 and calls[0].parse_error is None
    return calls[0]


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("./", "."),
        ("././", "."),
        (".", "."),
        ("./sub", "sub"),
        ("././sub", "sub"),
        ("", ""),
        ("/", "/"),
        ("..", ".."),
        ("./../escape", "../escape"),
        ("/workspace", "."),
        ("/workspace/sub", "sub"),
        ("sub/./marker.txt", "sub/./marker.txt"),
    ],
)
def test_captured_registry_preserves_root_intent(raw: str, expected: str) -> None:
    snapshot = ToolSpecRegistry.capture_effective_spec("repo_tree")
    call = native_call("repo_tree", {"path": raw})
    assert normalize_tool_arguments_from_snapshot(snapshot, call.arguments)["path"] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["./", "././", ".", "./sub"])
async def test_root_alias_reaches_real_engineering_consumer(workspace: Path, path: str) -> None:
    scope = PermissionScope((workspace,), (), frozenset({"repo_tree"}), False)
    tools = EngineeringTools(workspace, scope, RunLimits(None, 64000, 32000), asyncio.Event())
    call = native_call("repo_tree", {"path": path})
    observed = await tools.execute(call.id, call.name, call.arguments)
    assert observed.ok, observed.content
    assert "marker.txt" in observed.content


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,error",
    [
        ("", "nonempty string"),
        ("/", "UNSUPPORTED_PATH_PREFIX"),
        ("..", "UNSUPPORTED_PATH_PREFIX"),
        ("./../escape", "UNSUPPORTED_PATH_PREFIX"),
    ],
)
async def test_root_alias_does_not_erase_invalid_paths(workspace: Path, path: str, error: str) -> None:
    scope = PermissionScope((workspace,), (), frozenset({"repo_tree"}), False)
    tools = EngineeringTools(workspace, scope, RunLimits(None, 64000, 32000), asyncio.Event())
    call = native_call("repo_tree", {"path": path})
    observed = await tools.execute(call.id, call.name, call.arguments)
    assert not observed.ok and error in observed.content


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "enabled,error",
    [
        (frozenset({"repo_tree"}), "outside permission scope"),
        (frozenset(), "tool disabled"),
    ],
)
async def test_explicit_empty_grants_stay_empty(workspace: Path, enabled: frozenset[str], error: str) -> None:
    scope = PermissionScope((), (), enabled, False)
    tools = EngineeringTools(workspace, scope, RunLimits(None, 64000, 32000), asyncio.Event())
    observed = await tools.execute("synthetic-no-grant", "repo_tree", {"path": "./"})
    assert not observed.ok and error in observed.content
    assert tools.scope.read_roots == () and tools.scope.write_roots == ()


@pytest.mark.asyncio
async def test_write_root_alias_cannot_modify_directory(workspace: Path) -> None:
    scope = PermissionScope((workspace,), (workspace,), frozenset({"write_file"}), False)
    tools = EngineeringTools(workspace, scope, RunLimits(None, 64000, 32000), asyncio.Event())
    observed = await tools.execute("synthetic-root-write", "write_file", {"path": "./", "content": "never written"})
    assert not observed.ok and "cannot modify workspace root" in observed.content
    assert workspace.is_dir()
    assert tools.filesystem.workspace_read_text("sub/marker.txt", encoding="utf-8") == "synthetic UTF-8 反例\n"


@pytest.mark.asyncio
async def test_symlink_metadata_is_still_rejected(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scope = PermissionScope((workspace,), (), frozenset({"repo_tree"}), False)
    tools = EngineeringTools(workspace, scope, RunLimits(None, 64000, 32000), asyncio.Event())
    real_is_symlink = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda path: path == workspace / "link" or real_is_symlink(path))
    observed = await tools.execute("synthetic-symlink", "repo_tree", {"path": "./link"})
    assert not observed.ok and "Symlink detected" in observed.content
