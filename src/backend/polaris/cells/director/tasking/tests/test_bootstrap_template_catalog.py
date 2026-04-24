"""Tests for bootstrap_template_catalog module."""

from __future__ import annotations

import pytest


class TestGetPythonBootstrap:
    """Tests for Python bootstrap functions."""

    def test_get_python_bootstrap_files(self) -> None:
        """Test get_python_bootstrap_files returns expected structure."""
        from polaris.cells.director.tasking.internal.bootstrap_template_catalog import (
            get_python_bootstrap_files,
        )

        files = get_python_bootstrap_files()
        assert isinstance(files, list)
        assert len(files) > 0

        paths = [f["path"] for f in files]
        assert "fastapi_entrypoint.py" in paths
        assert "requirements.txt" in paths
        assert "tui_runtime.md" in paths

    def test_get_python_bootstrap_has_content(self) -> None:
        """Test Python bootstrap files have content."""
        from polaris.cells.director.tasking.internal.bootstrap_template_catalog import (
            get_python_bootstrap_files,
        )

        files = get_python_bootstrap_files()
        for f in files:
            assert "path" in f
            assert "content" in f
            assert len(f["content"]) > 0


class TestGetTypeScriptBootstrap:
    """Tests for TypeScript bootstrap functions."""

    def test_get_typescript_bootstrap_files(self) -> None:
        """Test get_typescript_bootstrap_files returns expected structure."""
        from polaris.cells.director.tasking.internal.bootstrap_template_catalog import (
            get_typescript_bootstrap_files,
        )

        files = get_typescript_bootstrap_files()
        assert isinstance(files, list)
        assert len(files) > 0

        paths = [f["path"] for f in files]
        assert "package.json" in paths
        assert any("index.ts" in p for p in paths)
        assert "tsconfig.json" in paths
        assert "tui_runtime.md" in paths

    def test_typescript_bootstrap_has_valid_package_json(self) -> None:
        """Test TypeScript bootstrap has valid package.json."""
        from polaris.cells.director.tasking.internal.bootstrap_template_catalog import (
            get_typescript_bootstrap_files,
        )

        files = get_typescript_bootstrap_files()
        package = next((f for f in files if f["path"] == "package.json"), None)
        assert package is not None

        import json

        data = json.loads(package["content"])
        assert "name" in data
        assert "version" in data


class TestGetGenericBootstrap:
    """Tests for generic bootstrap functions."""

    def test_get_generic_bootstrap_files(self) -> None:
        """Test get_generic_bootstrap_files returns expected structure."""
        from polaris.cells.director.tasking.internal.bootstrap_template_catalog import (
            get_generic_bootstrap_files,
        )

        files = get_generic_bootstrap_files()
        assert isinstance(files, list)
        assert len(files) > 0

        paths = [f["path"] for f in files]
        assert "tui_runtime.md" in paths

    def test_generic_bootstrap_has_tui_runtime(self) -> None:
        """Test generic bootstrap includes tui_runtime.md."""
        from polaris.cells.director.tasking.internal.bootstrap_template_catalog import (
            get_generic_bootstrap_files,
        )

        files = get_generic_bootstrap_files()
        tui = next((f for f in files if f["path"] == "tui_runtime.md"), None)
        assert tui is not None
        assert len(tui["content"]) > 0


class TestGetIntelligentBootstrapFiles:
    """Tests for intelligent bootstrap selection."""

    def test_intelligent_python(self) -> None:
        """Test intelligent bootstrap for Python."""
        from polaris.cells.director.tasking.internal.bootstrap_template_catalog import (
            get_intelligent_bootstrap_files,
        )

        files = get_intelligent_bootstrap_files(
            language="python",
            framework=None,
            task_subject="Test Task",
            task_description="Test Description",
        )

        assert len(files) > 0
        paths = [f["path"] for f in files]
        assert "tui_runtime.md" in paths

    def test_intelligent_fastapi(self) -> None:
        """Test intelligent bootstrap for FastAPI."""
        from polaris.cells.director.tasking.internal.bootstrap_template_catalog import (
            get_intelligent_bootstrap_files,
        )

        files = get_intelligent_bootstrap_files(
            language="python",
            framework="fastapi",
            task_subject="API Task",
            task_description="FastAPI Project",
        )

        assert len(files) > 0
        content = "".join(f["content"] for f in files)
        assert "FastAPI" in content or "fastapi" in content.lower()

    def test_intelligent_flask(self) -> None:
        """Test intelligent bootstrap for Flask."""
        from polaris.cells.director.tasking.internal.bootstrap_template_catalog import (
            get_intelligent_bootstrap_files,
        )

        files = get_intelligent_bootstrap_files(
            language="python",
            framework="flask",
            task_subject="Flask Task",
            task_description="Flask Project",
        )

        content = "".join(f["content"] for f in files)
        assert "flask" in content.lower()

    def test_intelligent_typescript(self) -> None:
        """Test intelligent bootstrap for TypeScript."""
        from polaris.cells.director.tasking.internal.bootstrap_template_catalog import (
            get_intelligent_bootstrap_files,
        )

        files = get_intelligent_bootstrap_files(
            language="typescript",
            framework=None,
            task_subject="TS Task",
            task_description="TypeScript Project",
        )

        assert len(files) > 0
        paths = [f["path"] for f in files]
        assert "package.json" in paths

    def test_intelligent_javascript(self) -> None:
        """Test intelligent bootstrap for JavaScript."""
        from polaris.cells.director.tasking.internal.bootstrap_template_catalog import (
            get_intelligent_bootstrap_files,
        )

        files = get_intelligent_bootstrap_files(
            language="javascript",
            framework=None,
            task_subject="JS Task",
            task_description="JavaScript Project",
        )

        assert len(files) > 0
        paths = [f["path"] for f in files]
        assert "package.json" in paths

    def test_intelligent_go(self) -> None:
        """Test intelligent bootstrap for Go."""
        from polaris.cells.director.tasking.internal.bootstrap_template_catalog import (
            get_intelligent_bootstrap_files,
        )

        files = get_intelligent_bootstrap_files(
            language="go",
            framework=None,
            task_subject="Go Task",
            task_description="Go Project",
        )

        assert len(files) > 0
        paths = [f["path"] for f in files]
        assert "main.go" in paths
        assert "go.mod" in paths

    def test_intelligent_rust(self) -> None:
        """Test intelligent bootstrap for Rust."""
        from polaris.cells.director.tasking.internal.bootstrap_template_catalog import (
            get_intelligent_bootstrap_files,
        )

        files = get_intelligent_bootstrap_files(
            language="rust",
            framework=None,
            task_subject="Rust Task",
            task_description="Rust Project",
        )

        assert len(files) > 0
        paths = [f["path"] for f in files]
        assert "Cargo.toml" in paths
        assert "src/main.rs" in paths

    def test_intelligent_unknown_language_falls_back_to_generic(self) -> None:
        """Test unknown language falls back to generic bootstrap."""
        from polaris.cells.director.tasking.internal.bootstrap_template_catalog import (
            get_intelligent_bootstrap_files,
        )

        files = get_intelligent_bootstrap_files(
            language="cobol",
            framework=None,
            task_subject="COBOL Task",
            task_description="Legacy Project",
        )

        assert len(files) > 0
        paths = [f["path"] for f in files]
        assert "tui_runtime.md" in paths

    def test_intelligent_empty_subject_uses_default(self) -> None:
        """Test empty subject uses default value."""
        from polaris.cells.director.tasking.internal.bootstrap_template_catalog import (
            get_intelligent_bootstrap_files,
        )

        files = get_intelligent_bootstrap_files(
            language="python",
            framework=None,
            task_subject="",
            task_description="",
        )

        # Should still return valid files
        assert len(files) > 0
        tui = next((f for f in files if f["path"] == "tui_runtime.md"), None)
        assert tui is not None
        assert "Generated Project" in tui["content"]


class TestBootstrapContent:
    """Tests for bootstrap content quality."""

    def test_python_fastapi_has_imports(self) -> None:
        """Test FastAPI bootstrap has proper imports."""
        from polaris.cells.director.tasking.internal.bootstrap_template_catalog import (
            get_intelligent_bootstrap_files,
        )

        files = get_intelligent_bootstrap_files(
            language="python",
            framework="fastapi",
            task_subject="Test",
            task_description="Test",
        )

        main = next(
            (f for f in files if "fastapi_entrypoint" in f["path"]),
            None,
        )
        assert main is not None
        assert "from fastapi import" in main["content"]

    def test_rust_has_tokio_dependency(self) -> None:
        """Test Rust bootstrap has tokio dependency."""
        from polaris.cells.director.tasking.internal.bootstrap_template_catalog import (
            get_intelligent_bootstrap_files,
        )

        files = get_intelligent_bootstrap_files(
            language="rust",
            framework=None,
            task_subject="Test",
            task_description="Test",
        )

        cargo = next((f for f in files if f["path"] == "Cargo.toml"), None)
        assert cargo is not None
        assert "tokio" in cargo["content"].lower()

    def test_go_has_flag_package(self) -> None:
        """Test Go bootstrap uses flag package."""
        from polaris.cells.director.tasking.internal.bootstrap_template_catalog import (
            get_intelligent_bootstrap_files,
        )

        files = get_intelligent_bootstrap_files(
            language="go",
            framework=None,
            task_subject="Test",
            task_description="Test",
        )

        main = next((f for f in files if f["path"] == "main.go"), None)
        assert main is not None
        assert 'flag"' in main["content"] or "flag." in main["content"]

    def test_typescript_has_interface(self) -> None:
        """Test TypeScript bootstrap has interface."""
        from polaris.cells.director.tasking.internal.bootstrap_template_catalog import (
            get_intelligent_bootstrap_files,
        )

        files = get_intelligent_bootstrap_files(
            language="typescript",
            framework=None,
            task_subject="Test",
            task_description="Test",
        )

        main = next((f for f in files if "index.ts" in f["path"]), None)
        assert main is not None
        assert "interface" in main["content"].lower()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
