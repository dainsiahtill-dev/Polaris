"""路径安全回归测试 — 验证 _resolve_existing_workspace_file 与 rewrite_existing_file_paths_in_invocations 的目录遍历防御。

覆盖:
- 正常路径解析
- 目录遍历攻击拦截
- 符号链接指向外部被拦截
- 重写后路径二次验证
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from polaris.cells.roles.kernel.internal.transaction.tool_batch_executor import (
    _resolve_existing_workspace_file,
    rewrite_existing_file_paths_in_invocations,
)

# ---------------------------------------------------------------------------
# _resolve_existing_workspace_file 测试
# ---------------------------------------------------------------------------


class TestResolveExistingWorkspaceFile:
    def test_normal_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "test.txt").write_text("hello")
            result = _resolve_existing_workspace_file(workspace=tmpdir, raw_path="test.txt")
            assert result == "test.txt"

    def test_windows_absolute_path_inside_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            nested_file = Path(tmpdir) / "nested" / "a.txt"
            nested_file.parent.mkdir(parents=True, exist_ok=True)
            nested_file.write_text("hello")
            result = _resolve_existing_workspace_file(workspace=tmpdir, raw_path=nested_file.as_posix())
            assert result == "nested/a.txt"

    def test_windows_absolute_path_with_leading_slash_inside_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            nested_file = Path(tmpdir) / "nested" / "b.txt"
            nested_file.parent.mkdir(parents=True, exist_ok=True)
            nested_file.write_text("hello")
            result = _resolve_existing_workspace_file(workspace=tmpdir, raw_path=f"/{nested_file.as_posix()}")
            assert result == "nested/b.txt"

    def test_path_traversal_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            outside_file = Path(tmpdir).parent / f"outside-{Path(tmpdir).name}.txt"
            outside_file.write_text("secret")
            result = _resolve_existing_workspace_file(workspace=tmpdir, raw_path=f"../{outside_file.name}")
            assert result is None

    def test_symlink_outside_workspace_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            outside = tempfile.mkdtemp()
            outside_file = Path(outside) / "secret.txt"
            outside_file.write_text("secret")
            symlink = Path(tmpdir) / "link.txt"
            symlink.symlink_to(outside_file)
            result = _resolve_existing_workspace_file(workspace=tmpdir, raw_path="link.txt")
            assert result is None

    def test_empty_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = _resolve_existing_workspace_file(workspace=tmpdir, raw_path="")
            assert result is None

    def test_file_uri(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "data.json").write_text("{}")
            result = _resolve_existing_workspace_file(workspace=tmpdir, raw_path="file://data.json")
            assert result == "data.json"

    def test_nonexistent_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = _resolve_existing_workspace_file(workspace=tmpdir, raw_path="not_exist.txt")
            assert result is None


# ---------------------------------------------------------------------------
# rewrite_existing_file_paths_in_invocations 测试
# ---------------------------------------------------------------------------


class TestRewriteExistingFilePathsInInvocations:
    def test_no_rewrite_for_non_file_tools(self) -> None:
        invocations: list[Any] = [{"tool_name": "echo", "arguments": {"message": "hi"}}]
        result = rewrite_existing_file_paths_in_invocations(turn_id="t1", workspace="/tmp", invocations=invocations)
        assert result == invocations

    def test_rewrite_windows_absolute_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            nested_file = Path(tmpdir) / "nested" / "a.txt"
            nested_file.parent.mkdir(parents=True, exist_ok=True)
            nested_file.write_text("a")
            invocations: list[Any] = [{"tool_name": "read_file", "arguments": {"path": nested_file.as_posix()}}]
            result = rewrite_existing_file_paths_in_invocations(turn_id="t1", workspace=tmpdir, invocations=invocations)
            assert result[0]["arguments"]["path"] == "nested/a.txt"

    def test_rewrite_malformed_windows_absolute_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            nested_file = Path(tmpdir) / "nested" / "b.txt"
            nested_file.parent.mkdir(parents=True, exist_ok=True)
            nested_file.write_text("b")
            invocations: list[Any] = [{"tool_name": "read_file", "arguments": {"path": f"/{nested_file.as_posix()}"}}]
            result = rewrite_existing_file_paths_in_invocations(turn_id="t1", workspace=tmpdir, invocations=invocations)
            assert result[0]["arguments"]["path"] == "nested/b.txt"

    def test_rewrite_normal_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "a.txt").write_text("a")
            invocations: list[Any] = [{"tool_name": "read_file", "arguments": {"path": "a.txt"}}]
            result = rewrite_existing_file_paths_in_invocations(turn_id="t1", workspace=tmpdir, invocations=invocations)
            assert result[0]["arguments"]["path"] == "a.txt"

    def test_rewrite_workspace_relative_path_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            nested_file = Path(tmpdir) / "nested" / "c.txt"
            nested_file.parent.mkdir(parents=True, exist_ok=True)
            nested_file.write_text("c")
            invocations: list[Any] = [{"tool_name": "read_file", "arguments": {"path": "nested/c.txt"}}]
            result = rewrite_existing_file_paths_in_invocations(turn_id="t1", workspace=tmpdir, invocations=invocations)
            assert result[0]["arguments"]["path"] == "nested/c.txt"

    def test_rewrite_traversal_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            outside_file = Path(tmpdir).parent / f"outside-{Path(tmpdir).name}.txt"
            outside_file.write_text("secret")
            invocations: list[Any] = [{"tool_name": "read_file", "arguments": {"path": f"../{outside_file.name}"}}]
            result = rewrite_existing_file_paths_in_invocations(turn_id="t1", workspace=tmpdir, invocations=invocations)
            # 路径验证失败，应保持原始路径
            assert result[0]["arguments"]["path"] == f"../{outside_file.name}"

    def test_rewrite_symlink_outside_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            outside = tempfile.mkdtemp()
            outside_file = Path(outside) / "secret.txt"
            outside_file.write_text("secret")
            symlink = Path(tmpdir) / "link.txt"
            symlink.symlink_to(outside_file)
            invocations: list[Any] = [{"tool_name": "read_file", "arguments": {"path": "link.txt"}}]
            result = rewrite_existing_file_paths_in_invocations(turn_id="t1", workspace=tmpdir, invocations=invocations)
            # 符号链接指向外部，应保持原始路径
            assert result[0]["arguments"]["path"] == "link.txt"

    def test_revalidation_after_rewrite(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "b.txt").write_text("b")
            invocations: list[Any] = [
                {
                    "tool_name": "edit_file",
                    "arguments": {"target": "b.txt", "content": "new"},
                }
            ]
            result = rewrite_existing_file_paths_in_invocations(turn_id="t1", workspace=tmpdir, invocations=invocations)
            # 正常文件应被重写（路径不变但验证通过）
            assert result[0]["arguments"]["target"] == "b.txt"

    def test_no_arguments(self) -> None:
        invocations: list[Any] = [{"tool_name": "read_file", "arguments": {}}]
        result = rewrite_existing_file_paths_in_invocations(turn_id="t1", workspace="/tmp", invocations=invocations)
        assert result == invocations

    def test_non_mapping_invocation(self) -> None:
        class FakeInvocation:
            tool_name = "read_file"
            arguments = {"path": "foo.txt"}
            call_id = "c1"
            effect_type = None
            execution_mode = None

        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "foo.txt").write_text("foo")
            invocations: list[Any] = [FakeInvocation()]
            result = rewrite_existing_file_paths_in_invocations(turn_id="t1", workspace=tmpdir, invocations=invocations)
            # 当 resolved_path == normalized_raw_path 时不会触发重写，保持原对象
            assert result[0] is invocations[0]
