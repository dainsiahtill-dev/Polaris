"""Tests for file_apply_service module."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

if TYPE_CHECKING:
    pass


class TestFileApplyService:
    """Tests for FileApplyService class."""

    def test_service_initialization(self) -> None:
        """Test FileApplyService initialization."""
        from polaris.cells.director.tasking.internal.file_apply_service import FileApplyService

        service = FileApplyService(
            workspace="/tmp/workspace",
            worker_id="worker-1",
        )
        assert service.workspace == "/tmp/workspace"
        assert service._worker_id == "worker-1"
        assert service._bus is None

    def test_service_with_message_bus(self) -> None:
        """Test FileApplyService with message bus."""
        from polaris.cells.director.tasking.internal.file_apply_service import FileApplyService

        mock_bus = MagicMock()
        service = FileApplyService(
            workspace="/tmp/workspace",
            message_bus=mock_bus,
        )
        assert service._bus is mock_bus

    def test_write_files_empty_list(self) -> None:
        """Test write_files with empty list."""
        from polaris.cells.director.tasking.internal.file_apply_service import FileApplyService

        service = FileApplyService(workspace="/tmp")
        result = service.write_files([])
        assert result == []

    def test_write_files_skips_empty_entries(self) -> None:
        """Test write_files skips entries without path or content."""
        from polaris.cells.director.tasking.internal.file_apply_service import FileApplyService

        service = FileApplyService(workspace="/tmp")
        result = service.write_files(
            [
                {"path": "", "content": "test"},  # Empty path
                {"path": "test.py", "content": ""},  # Empty content
            ]
        )
        assert result == []

    def test_write_files_blocks_agents_forbidden_path(self, tmp_path: Path) -> None:
        """write_files must not bypass AGENTS.md policy."""
        from polaris.cells.director.tasking.internal.file_apply_service import FileApplyService

        (tmp_path / "AGENTS.md").write_text("禁止修改 src/generated/schema.ts\n", encoding="utf-8")
        service = FileApplyService(workspace=str(tmp_path))

        result = service.write_files([{"path": "src/generated/schema.ts", "content": "export const schema = {};\n"}])

        assert result == []
        assert service._last_write_errors
        assert "AGENTS.md forbids writing src/generated/schema.ts" in service._last_write_errors[0]
        assert not (tmp_path / "src" / "generated" / "schema.ts").exists()

    def test_apply_response_operations_reports_fenced_policy_denial(self, tmp_path: Path) -> None:
        """Fenced direct writes should return policy errors instead of silent no_changes."""
        from polaris.cells.director.tasking.internal.file_apply_service import FileApplyService

        (tmp_path / "AGENTS.md").write_text("禁止修改 src/generated/schema.ts\n", encoding="utf-8")
        service = FileApplyService(workspace=str(tmp_path))

        files, errors = service.apply_response_operations(
            "```file: src/generated/schema.ts\nexport const schema = {};\n```",
            task_id="task-policy-fenced",
        )

        assert files == []
        assert any("AGENTS.md forbids writing src/generated/schema.ts" in error for error in errors)
        assert not (tmp_path / "src" / "generated" / "schema.ts").exists()

    def test_apply_response_operations_blocks_fenced_file_outside_allowed_scope(self, tmp_path: Path) -> None:
        """Director proposal apply must not write outside the round scope."""
        from polaris.cells.director.tasking.internal.file_apply_service import FileApplyService

        service = FileApplyService(workspace=str(tmp_path))

        files, errors = service.apply_response_operations(
            "```file: src/out_of_scope.py\nprint('blocked')\n```",
            task_id="task-scope-fenced",
            allowed_scope_paths=["src/app.py"],
        )

        assert files == []
        assert any("Changed files exceed act.files scope" in error for error in errors)
        assert not (tmp_path / "src" / "out_of_scope.py").exists()

    def test_collect_workspace_files_empty_list(self) -> None:
        """Test collect_workspace_files with empty list."""
        from polaris.cells.director.tasking.internal.file_apply_service import FileApplyService

        service = FileApplyService(workspace="/tmp")
        result = service.collect_workspace_files([])
        assert result == []

    def test_collect_workspace_files_nonexistent(self) -> None:
        """Test collect_workspace_files with non-existent files."""
        from polaris.cells.director.tasking.internal.file_apply_service import FileApplyService

        service = FileApplyService(workspace="/tmp")
        result = service.collect_workspace_files(["nonexistent.py"])
        assert len(result) == 1
        assert result[0]["path"] == "nonexistent.py"
        assert result[0]["content"] == ""
        assert result[0].get("deleted") is True

    def test_collect_workspace_files_existing(self, tmp_path: Path) -> None:
        """Test collect_workspace_files with existing files."""
        from polaris.cells.director.tasking.internal.file_apply_service import FileApplyService

        # Create test file
        test_file = tmp_path / "existing.py"
        test_file.write_text("# existing file\n", encoding="utf-8")

        service = FileApplyService(workspace=str(tmp_path))
        result = service.collect_workspace_files(["existing.py"])

        assert len(result) == 1
        assert result[0]["path"] == "existing.py"
        assert result[0]["content"] == "# existing file\n"
        assert result[0].get("deleted") is not True

    def test_collect_workspace_files_deduplication(self, tmp_path: Path) -> None:
        """Test collect_workspace_files deduplicates paths."""
        from polaris.cells.director.tasking.internal.file_apply_service import FileApplyService

        test_file = tmp_path / "file.py"
        test_file.write_text("content", encoding="utf-8")

        service = FileApplyService(workspace=str(tmp_path))
        result = service.collect_workspace_files(["file.py", "file.py", "file.py"])

        # Should only return one result
        assert len(result) == 1

    def test_collect_workspace_files_unicode_content(self, tmp_path: Path) -> None:
        """Test collect_workspace_files handles unicode content."""
        from polaris.cells.director.tasking.internal.file_apply_service import FileApplyService

        test_file = tmp_path / "unicode.py"
        test_file.write_text("# Unicode: \u4e2d\u6587\n# Emoji: \U0001f600\n", encoding="utf-8")

        service = FileApplyService(workspace=str(tmp_path))
        result = service.collect_workspace_files(["unicode.py"])

        assert result[0]["content"].startswith("# Unicode")

    def test_calculate_diff_stats(self) -> None:
        """Test calculate_diff_stats method."""
        from polaris.cells.director.tasking.internal.file_apply_service import FileApplyService

        service = FileApplyService(workspace="/tmp")
        stats = service.calculate_diff_stats(
            old_content="line1\nline2\n",
            new_content="line1\nnew line\nline2\n",
        )

        assert "old_size" in stats
        assert "new_size" in stats
        assert "patch_size" in stats
        assert "patch" in stats
        assert stats["old_size"] == len("line1\nline2\n")

    def test_calculate_diff_stats_identical(self) -> None:
        """Test calculate_diff_stats with identical content."""
        from polaris.cells.director.tasking.internal.file_apply_service import FileApplyService

        content = "same content\n"
        service = FileApplyService(workspace="/tmp")
        stats = service.calculate_diff_stats(
            old_content=content,
            new_content=content,
        )

        assert stats["old_size"] == stats["new_size"]

    def test_calculate_diff_stats_empty_to_content(self) -> None:
        """Test calculate_diff_stats from empty to content."""
        from polaris.cells.director.tasking.internal.file_apply_service import FileApplyService

        service = FileApplyService(workspace="/tmp")
        stats = service.calculate_diff_stats(
            old_content="",
            new_content="new content\n",
        )

        assert stats["old_size"] == 0
        assert stats["new_size"] == len("new content\n")

    def test_apply_response_operations_no_response(self) -> None:
        """Test apply_response_operations with empty response."""
        from polaris.cells.director.tasking.internal.file_apply_service import FileApplyService

        service = FileApplyService(workspace="/tmp")
        applied, errors = service.apply_response_operations("")
        assert applied == []
        assert "no_changes" in errors

    def test_apply_response_operations_accepts_fenced_file_block(self, tmp_path: Path) -> None:
        """Regression: Director proposal bridge emits ```file: path fences."""
        from polaris.cells.director.tasking.internal.file_apply_service import FileApplyService

        service = FileApplyService(workspace=str(tmp_path))
        applied, errors = service.apply_response_operations(
            "```file: src/health.ts\nexport function health(): string {\n  return 'ok';\n}\n```"
        )

        assert errors == []
        assert applied == [
            {
                "path": "src/health.ts",
                "content": "export function health(): string {\n  return 'ok';\n}",
            }
        ]
        assert (tmp_path / "src" / "health.ts").read_text(encoding="utf-8").strip() == (
            "export function health(): string {\n  return 'ok';\n}"
        )

    def test_apply_response_operations_rejects_invalid_fenced_json(self, tmp_path: Path) -> None:
        """Invalid structured files must not be written as successful Director output."""
        from polaris.cells.director.tasking.internal.file_apply_service import FileApplyService

        service = FileApplyService(workspace=str(tmp_path))
        applied, errors = service.apply_response_operations(
            "```file: package.json\n"
            "{\n"
            '  "scripts": {\n'
            '    "check": "node check.js"\n'
            '    "e2e:smoke": "node smoke.js"\n'
            "  }\n"
            "}\n"
            "```"
        )

        assert applied == []
        assert any("invalid JSON" in error for error in errors)
        assert not (tmp_path / "package.json").exists()

    def test_apply_response_operations_rejects_markdown_advisory_as_source_file(self, tmp_path: Path) -> None:
        """Plain review prose must not be accepted as source code output."""
        from polaris.cells.director.tasking.internal.file_apply_service import FileApplyService

        service = FileApplyService(workspace=str(tmp_path))
        applied, errors = service.apply_response_operations(
            "FILE: src/web.ts\n"
            "**Additional guidance**\n\n"
            "# Blueprint analysis\n"
            "1. Keep the browser entry small.\n"
            "2. Use the existing contract.\n"
            "END FILE",
            allowed_scope_paths=["src/web.ts"],
        )

        assert applied == []
        assert any("markdown/advisory text" in error for error in errors)
        assert not (tmp_path / "src" / "web.ts").exists()

    def test_apply_response_operations_ignores_markdown_file_inventory_fence(self, tmp_path: Path) -> None:
        """A target-file inventory code fence is not a whole-file write."""
        from polaris.cells.director.tasking.internal.file_apply_service import FileApplyService

        service = FileApplyService(workspace=str(tmp_path))
        applied, errors = service.apply_response_operations(
            "# Blueprint report\n\n"
            "Target files:\n"
            "```text\n"
            "src/index.ts # main entry\n"
            "src/web.ts # browser entry\n"
            "```\n\n"
            "**Additional guidance**\n"
            "1. Keep the implementation small.\n",
            allowed_scope_paths=["src/index.ts", "src/web.ts"],
        )

        assert applied == []
        assert errors == ["no_changes"]
        assert not (tmp_path / "src" / "web.ts").exists()

    def test_write_files_normalizes_trailing_fence_json_config(self, tmp_path: Path) -> None:
        """Direct write_files must share weak-model JSON normalization with write_file."""
        from polaris.cells.director.tasking.internal.file_apply_service import FileApplyService

        service = FileApplyService(workspace=str(tmp_path))
        result = service.write_files(
            [
                {
                    "path": "package.json",
                    "content": '{"name":"demo","scripts":{"build":"tsc"}}\n```',
                }
            ],
            allowed_scope_paths=["package.json"],
        )

        assert result == [
            {
                "path": "package.json",
                "content": '{\n  "name": "demo",\n  "scripts": {\n    "build": "tsc"\n  }\n}\n',
            }
        ]
        payload = json.loads((tmp_path / "package.json").read_text(encoding="utf-8"))
        assert payload["scripts"]["build"] == "tsc"

    def test_write_files_rejects_incomplete_empty_package_json_fragment(self, tmp_path: Path) -> None:
        """Weak-model garbage must not be silently normalized into an empty manifest."""
        from polaris.cells.director.tasking.internal.file_apply_service import FileApplyService

        service = FileApplyService(workspace=str(tmp_path))
        result = service.write_files(
            [{"path": "package.json", "content": "{  "}],
            allowed_scope_paths=["package.json"],
        )

        assert result == []
        assert any("invalid JSON" in error for error in service._last_write_errors)
        assert not (tmp_path / "package.json").exists()

    def test_apply_response_operations_rolls_back_invalid_json_patch(self, tmp_path: Path) -> None:
        """A bad JSON patch must be rejected and rolled back before task success."""
        from polaris.cells.director.tasking.internal.file_apply_service import FileApplyService

        package_path = tmp_path / "package.json"
        original = '{\n  "scripts": {\n    "e2e:smoke": "node smoke.js"\n  }\n}\n'
        package_path.write_text(original, encoding="utf-8")

        service = FileApplyService(workspace=str(tmp_path))
        applied, errors = service.apply_response_operations(
            "PATCH_FILE: package.json\n"
            "<<<<<<< SEARCH\n"
            '  "scripts": {\n'
            "=======\n"
            '  "scripts": {\n'
            '    "check": "node check.js"\n'
            ">>>>>>> REPLACE\n"
        )

        assert applied == []
        assert any(
            "package.json: invalid JSON" in error or "package.json structured diff failed" in error for error in errors
        )
        assert package_path.read_text(encoding="utf-8") == original

    def test_apply_response_operations_rolls_back_patch_outside_allowed_scope(self, tmp_path: Path) -> None:
        """Protocol writes that exceed Director scope must be rolled back."""
        from polaris.cells.director.tasking.internal.file_apply_service import FileApplyService

        path = tmp_path / "src" / "out_of_scope.py"
        service = FileApplyService(workspace=str(tmp_path))
        applied, errors = service.apply_response_operations(
            "FILE: src/out_of_scope.py\nprint('blocked')\nEND FILE",
            allowed_scope_paths=["src/app.py"],
        )

        assert applied == []
        assert any("Changed files exceed act.files scope" in error for error in errors)
        assert not path.exists()

    def test_apply_response_operations_accepts_nested_markdown_fences(self, tmp_path: Path) -> None:
        """Regression: nested README fences must not become bogus file paths."""
        from polaris.cells.director.tasking.internal.file_apply_service import FileApplyService

        service = FileApplyService(workspace=str(tmp_path))
        applied, errors = service.apply_response_operations(
            "```file: README.md\n"
            "# Demo\n\n"
            "```bash\n"
            "python -c \"import tomllib; tomllib.load(open('pyproject.toml', 'rb'))\"\n"
            "```\n\n"
            "Done.\n"
            "```\n"
            "```file: pyproject.toml\n"
            "[project]\n"
            'name = "demo"\n'
            'version = "0.1.0"\n'
            "```"
        )

        assert errors == []
        assert [item["path"] for item in applied] == ["README.md", "pyproject.toml"]
        assert "python -c" in (tmp_path / "README.md").read_text(encoding="utf-8")
        assert (tmp_path / "pyproject.toml").read_text(encoding="utf-8").startswith("[project]\n")

    def test_apply_response_operations_strips_codex_usage_footer_between_fenced_files(self, tmp_path: Path) -> None:
        """Regression: Codex CLI usage comments after fences are not file content."""
        from polaris.cells.director.tasking.internal.file_apply_service import FileApplyService

        service = FileApplyService(workspace=str(tmp_path))
        applied, errors = service.apply_response_operations(
            "```file: src/index.ts\n"
            "export const ok = true;\n"
            "```\n\n"
            "<!-- Usage: 13772 input, 1371 output, 12160 cached -->\n"
            "```file: test/index.test.ts\n"
            "import { ok } from '../src/index';\n"
            "```\n\n"
            "<!-- Usage: 35897 input, 3965 output, 9600 cached -->"
        )

        assert errors == []
        assert [item["path"] for item in applied] == ["src/index.ts", "test/index.test.ts"]
        assert (tmp_path / "src" / "index.ts").read_text(encoding="utf-8") == "export const ok = true;"
        assert (tmp_path / "test" / "index.test.ts").read_text(encoding="utf-8") == (
            "import { ok } from '../src/index';"
        )

    def test_apply_response_operations_recovers_fenced_files_after_failed_patch(self, tmp_path: Path) -> None:
        """Regression: one bad leading PATCH_FILE must not discard valid file blocks."""
        from polaris.cells.director.tasking.internal.file_apply_service import FileApplyService

        (tmp_path / "package.json").write_text('{"name":"demo"}\n', encoding="utf-8")

        service = FileApplyService(workspace=str(tmp_path))
        applied, errors = service.apply_response_operations(
            "PATCH_FILE: package.json\n"
            "<<<<<<< SEARCH\n"
            '  "scripts": {\n'
            "=======\n"
            '  "scripts": {\n'
            '    "db:migrate": "node ./db/migrate.mjs",\n'
            ">>>>>>> REPLACE\n\n"
            "```file: db/migrate.mjs\n"
            'export const migrationName = "initial";\n'
            "```\n\n"
            "```file: src/server.mjs\n"
            'export function startServer() { return "ok"; }\n'
            "```\n"
        )

        assert [item["path"] for item in applied] == ["db/migrate.mjs", "src/server.mjs"]
        assert any("package.json" in error for error in errors)
        assert (tmp_path / "db" / "migrate.mjs").read_text(encoding="utf-8") == (
            'export const migrationName = "initial";'
        )
        assert (tmp_path / "src" / "server.mjs").read_text(encoding="utf-8") == (
            'export function startServer() { return "ok"; }'
        )

    def test_apply_response_operations_recovers_fenced_files_before_integrity_block(
        self,
        tmp_path: Path,
    ) -> None:
        """Fenced file blocks are valid even when a leading PATCH_FILE is malformed."""
        from polaris.cells.director.tasking.internal.file_apply_service import FileApplyService

        (tmp_path / "package.json").write_text('{"name":"demo"}\n', encoding="utf-8")

        service = FileApplyService(workspace=str(tmp_path))
        applied, errors = service.apply_response_operations(
            "PATCH_FILE: package.json\n"
            "<<<<<<< SEARCH\n"
            '"scripts": {\n'
            "=======\n"
            '"scripts": {\n'
            '  "e2e:smoke": "node smoke.mjs",\n'
            ">>>>>>> REPLACE\n\n"
            "```file: scripts/e2e/run-smoke.ps1\n"
            "Write-Output 'PASS_SUMMARY'\n"
            "```\n\n"
            "```file: .polaris/pipeline/e2e-baseline.json\n"
            '{"expectedExitCode":0}\n'
            "```\n",
            llm_metadata={"provider": "unit", "model": "unit"},
        )

        assert [item["path"] for item in applied] == [
            "scripts/e2e/run-smoke.ps1",
            ".polaris/pipeline/e2e-baseline.json",
        ]
        assert any("package.json" in error for error in errors)
        assert (tmp_path / "scripts" / "e2e" / "run-smoke.ps1").read_text(encoding="utf-8") == (
            "Write-Output 'PASS_SUMMARY'"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
