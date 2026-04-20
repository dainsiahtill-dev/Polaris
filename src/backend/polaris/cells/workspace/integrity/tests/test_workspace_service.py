"""Unit tests for `workspace.integrity` cell - WorkspaceService and helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from polaris.cells.workspace.integrity.internal.fs_utils import (
    normalize_rel_path,
    workspace_has_docs,
    workspace_status_path,
)
from polaris.cells.workspace.integrity.internal.workspace_service import (
    _dedupe_keep_order,
    _format_list,
    _infer_profile_from_hint,
    _resolve_effective_qa_commands,
    _split_items,
    build_docs_templates,
    clear_workspace_status,
    default_qa_commands,
    detect_project_profile,
    is_safe_docs_path,
    read_workspace_status,
    select_docs_target_root,
    write_workspace_status,
)

if TYPE_CHECKING:
    from pathlib import Path

# =============================================================================
# detect_project_profile tests
# =============================================================================


class TestDetectProjectProfile:
    def test_empty_workspace(self) -> None:
        result = detect_project_profile("")
        assert result["python"] is False
        assert result["node"] is False
        assert result["go"] is False
        assert result["rust"] is False
        assert result["package_manager"] is None

    def test_python_profile(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").touch()
        result = detect_project_profile(str(tmp_path))
        assert result["python"] is True

    def test_node_profile(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").touch()
        result = detect_project_profile(str(tmp_path))
        assert result["node"] is True

    def test_go_profile(self, tmp_path: Path) -> None:
        (tmp_path / "go.mod").touch()
        result = detect_project_profile(str(tmp_path))
        assert result["go"] is True

    def test_rust_profile(self, tmp_path: Path) -> None:
        (tmp_path / "Cargo.toml").touch()
        result = detect_project_profile(str(tmp_path))
        assert result["rust"] is True

    def test_package_manager_pnpm(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").touch()
        (tmp_path / "pnpm-lock.yaml").touch()
        result = detect_project_profile(str(tmp_path))
        assert result["package_manager"] == "pnpm"

    def test_package_manager_yarn(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").touch()
        (tmp_path / "yarn.lock").touch()
        result = detect_project_profile(str(tmp_path))
        assert result["package_manager"] == "yarn"

    def test_package_manager_npm_default(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").touch()
        result = detect_project_profile(str(tmp_path))
        assert result["package_manager"] == "npm"


# =============================================================================
# default_qa_commands tests
# =============================================================================


class TestDefaultQACommands:
    def test_python_commands(self) -> None:
        profile = {"python": True}
        commands = default_qa_commands(profile)
        assert "ruff check ." in commands
        assert "mypy" in commands
        assert "pytest" in commands

    def test_node_commands(self) -> None:
        profile = {"python": False, "node": True, "package_manager": "npm"}
        commands = default_qa_commands(profile)
        assert "npm test" in commands

    def test_go_commands(self) -> None:
        profile = {"python": False, "node": False, "go": True}
        commands = default_qa_commands(profile)
        assert "go test ./..." in commands

    def test_rust_commands(self) -> None:
        profile = {"python": False, "node": False, "go": False, "rust": True}
        commands = default_qa_commands(profile)
        assert "cargo test" in commands

    def test_hint_inference(self) -> None:
        profile = {"python": False}
        hint = "This project uses pytest and ruff for testing"
        commands = default_qa_commands(profile, hint_text=hint)
        assert "pytest" in commands
        assert "ruff check ." in commands

    def test_fallback_when_no_profile(self) -> None:
        profile = {"python": False, "node": False, "go": False, "rust": False}
        commands = default_qa_commands(profile)
        assert "python -m pytest -q" in commands


# =============================================================================
# _infer_profile_from_hint tests
# =============================================================================


class TestInferProfileFromHint:
    def test_python_hint(self) -> None:
        result = _infer_profile_from_hint("This uses pytest and ruff")
        assert result["python"] is True

    def test_node_hint(self) -> None:
        result = _infer_profile_from_hint("Built with node and npm")
        assert result["node"] is True

    def test_go_hint(self) -> None:
        result = _infer_profile_from_hint("golang project with go.mod")
        assert result["go"] is True

    def test_rust_hint(self) -> None:
        result = _infer_profile_from_hint("Rust + Cargo.toml project")
        assert result["rust"] is True

    def test_empty_hint(self) -> None:
        result = _infer_profile_from_hint("")
        assert result["python"] is False
        assert result["node"] is False
        assert result["go"] is False
        assert result["rust"] is False


# =============================================================================
# _split_items tests
# =============================================================================


class TestSplitItems:
    def test_comma_separated(self) -> None:
        result = _split_items("item1,item2,item3")
        assert result == ["item1", "item2", "item3"]

    def test_newline_separated(self) -> None:
        result = _split_items("item1\nitem2\nitem3")
        assert result == ["item1", "item2", "item3"]

    def test_dash_prefix_stripped(self) -> None:
        result = _split_items("- item1\n- item2")
        assert result == ["item1", "item2"]

    def test_empty_returns_empty(self) -> None:
        assert _split_items("") == []
        assert _split_items(",,,") == []

    def test_whitespace_trimmed(self) -> None:
        result = _split_items("  item1  ,  item2  ")
        assert result == ["item1", "item2"]


# =============================================================================
# _format_list tests
# =============================================================================


class TestFormatList:
    def test_non_empty_list(self) -> None:
        result = _format_list(["item1", "item2"])
        assert "- item1" in result
        assert "- item2" in result

    def test_empty_list_uses_placeholder(self) -> None:
        result = _format_list([])
        assert result == "- TBD"

    def test_custom_placeholder(self) -> None:
        result = _format_list([], placeholder="EMPTY")
        assert result == "- EMPTY"


# =============================================================================
# _dedupe_keep_order tests
# =============================================================================


class TestDedupeKeepOrder:
    def test_removes_duplicates_case_insensitive(self) -> None:
        result = _dedupe_keep_order(["Item1", "item1", "ITEM1"])
        assert result == ["Item1"]  # First occurrence kept

    def test_preserves_order(self) -> None:
        result = _dedupe_keep_order(["b", "a", "c", "b", "a"])
        assert result == ["b", "a", "c"]

    def test_empty_input(self) -> None:
        assert _dedupe_keep_order([]) == []
        assert _dedupe_keep_order(["", "  "]) == []


# =============================================================================
# _resolve_effective_qa_commands tests
# =============================================================================


class TestResolveEffectiveQACommands:
    def test_uses_explicit_commands_when_provided(self) -> None:
        profile = {"python": False}
        explicit = ["ruff check .", "mypy"]
        result = _resolve_effective_qa_commands(profile, "", explicit)
        assert result == ["ruff check .", "mypy"]

    def test_falls_back_to_default_when_empty(self) -> None:
        profile = {"python": True}
        result = _resolve_effective_qa_commands(profile, "hint text", [])
        assert "ruff check ." in result

    def test_filters_placeholder(self) -> None:
        profile: dict[str, Any] = {}
        result = _resolve_effective_qa_commands(profile, "", ["Add project-specific QA commands."])
        assert "Add project-specific QA commands." not in result


# =============================================================================
# is_safe_docs_path tests
# =============================================================================


class TestIsSafeDocsPath:
    def test_valid_docs_path(self) -> None:
        assert is_safe_docs_path("docs/product/requirements.md", "docs") is True

    def test_valid_docs_dir(self) -> None:
        assert is_safe_docs_path("docs", "docs") is True

    def test_valid_workspace_docs_path(self) -> None:
        assert is_safe_docs_path("workspace/docs/product/plan.md", "workspace/docs") is True

    def test_rejects_parent_traversal(self) -> None:
        assert is_safe_docs_path("../etc/passwd", "docs") is False
        assert is_safe_docs_path("docs/../../etc/passwd", "docs") is False

    def test_rejects_non_docs_path(self) -> None:
        assert is_safe_docs_path("src/main.py", "docs") is False
        assert is_safe_docs_path("config.yaml", "docs") is False

    def test_empty_path_rejected(self) -> None:
        assert is_safe_docs_path("", "docs") is False
        assert is_safe_docs_path(".", "docs") is False


# =============================================================================
# select_docs_target_root tests
# =============================================================================


class TestSelectDocsTargetRoot:
    def test_uses_legacy_docs_when_exists(self, tmp_path: Path) -> None:
        (tmp_path / "docs").mkdir()
        result = select_docs_target_root(str(tmp_path))
        assert result.startswith("workspace/docs/_drafts/init-")

    def test_uses_workspace_docs_when_no_legacy(self, tmp_path: Path) -> None:
        result = select_docs_target_root(str(tmp_path))
        assert result == "workspace/docs"


# =============================================================================
# build_docs_templates tests
# =============================================================================


class TestBuildDocsTemplates:
    def test_builds_product_templates(self, tmp_path: Path) -> None:
        docs = build_docs_templates(
            workspace=str(tmp_path),
            mode="product",
            fields={"goal": "Test goal", "in_scope": "item1\nitem2"},
            qa_commands=["pytest"],
        )
        assert "docs/product/requirements.md" in docs
        assert "docs/product/plan.md" in docs
        assert "Test goal" in docs["docs/product/requirements.md"]
        assert "item1" in docs["docs/product/requirements.md"]

    def test_builds_legacy_templates(self, tmp_path: Path) -> None:
        docs = build_docs_templates(
            workspace=str(tmp_path),
            mode="product",
            fields={"goal": "Legacy test", "backlog": "task1"},
            qa_commands=[],
        )
        assert "docs/00_overview.md" in docs
        assert "docs/10_requirements.md" in docs
        assert "docs/40_quality.md" in docs
        assert "Legacy test" in docs["docs/00_overview.md"]

    def test_includes_polaris_metadata(self, tmp_path: Path) -> None:
        docs = build_docs_templates(
            workspace=str(tmp_path),
            mode="product",
            fields={},
            qa_commands=["pytest"],
        )
        assert "docs/.polaris.json" in docs
        import json

        meta: dict[str, Any] = json.loads(docs["docs/.polaris.json"])
        assert meta["schema_version"] == 2
        assert meta["docs_mode"] == "product"
        assert "pytest" in meta["qa_commands"]

    def test_goal_from_readme_in_import_mode(self, tmp_path: Path) -> None:
        # read_readme_title looks for tui_runtime.md (not README.md)
        (tmp_path / "tui_runtime.md").write_text("# My TUI Runtime Title", encoding="utf-8")
        docs = build_docs_templates(
            workspace=str(tmp_path),
            mode="import_readme",
            fields={},
            qa_commands=[],
        )
        # Should use tui_runtime.md title when goal is empty
        content: str = docs["docs/product/requirements.md"]
        assert "My TUI Runtime Title" in content


# =============================================================================
# read_workspace_status / write_workspace_status / clear_workspace_status
# =============================================================================


class TestWorkspaceStatusLifecycle:
    def test_write_and_read_roundtrip(self, tmp_path: Path) -> None:
        workspace = str(tmp_path)
        write_workspace_status(
            workspace,
            status="READY",
            reason="test reason",
            actions=["action1"],
        )
        status = read_workspace_status(workspace)
        assert status is not None
        assert status["status"] == "READY"
        assert status["reason"] == "test reason"
        assert status["actions"] == ["action1"]

    def test_read_returns_none_when_no_file(self, tmp_path: Path) -> None:
        status = read_workspace_status(str(tmp_path))
        assert status is None

    def test_write_empty_workspace_is_noop(self) -> None:
        # Should not raise
        write_workspace_status("", status="READY", reason="no-op")

    def test_clear_workspace_status(self, tmp_path: Path) -> None:
        workspace = str(tmp_path)
        write_workspace_status(workspace, status="READY", reason="clear test")
        clear_workspace_status(workspace)
        status = read_workspace_status(workspace)
        assert status is None


# =============================================================================
# workspace_status_path tests
# =============================================================================


class TestWorkspaceStatusPath:
    def test_empty_workspace_returns_empty_string(self) -> None:
        assert workspace_status_path("") == ""

    def test_returns_non_empty_for_valid_workspace(self, tmp_path: Path) -> None:
        result = workspace_status_path(str(tmp_path))
        assert isinstance(result, str)
        assert len(result) > 0
        assert "workspace_status.json" in result


# =============================================================================
# workspace_has_docs tests
# =============================================================================


class TestWorkspaceHasDocs:
    def test_false_for_empty_workspace(self) -> None:
        assert workspace_has_docs("") is False

    def test_true_when_docs_dir_exists(self, tmp_path: Path) -> None:
        (tmp_path / "docs").mkdir()
        assert workspace_has_docs(str(tmp_path)) is True

    def test_false_when_no_docs(self, tmp_path: Path) -> None:
        assert workspace_has_docs(str(tmp_path)) is False


# =============================================================================
# normalize_rel_path tests
# =============================================================================


class TestNormalizeRelPath:
    def test_forward_slash_normalized(self) -> None:
        assert (
            normalize_rel_path("docs/sub/file.md") == "docs\\sub\\file.md"
            or normalize_rel_path("docs/sub/file.md") == "docs/sub/file.md"
        )

    def test_leading_slash_stripped(self) -> None:
        result = normalize_rel_path("/docs/file.md")
        # normpath strips leading slash on Windows, or keeps it on Unix
        assert "docs" in result

    def test_dot_normalized(self) -> None:
        assert (
            normalize_rel_path("./docs/../docs/file.md") == "docs\\file.md"
            or normalize_rel_path("./docs/../docs/file.md") == "docs/file.md"
        )

    def test_empty_returns_dot(self) -> None:
        # normpath("") returns "." on Windows
        assert normalize_rel_path("") == "."
