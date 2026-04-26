"""Tests for bootstrap_template_catalog module.

All text operations MUST explicitly use UTF-8 encoding.
"""

from __future__ import annotations

from polaris.cells.director.execution.internal.bootstrap_template_catalog import (
    get_generic_bootstrap_files,
    get_intelligent_bootstrap_files,
    get_python_bootstrap_files,
    get_typescript_bootstrap_files,
)


class TestGetIntelligentBootstrapFiles:
    """Tests for get_intelligent_bootstrap_files function."""

    def test_python_fastapi_bootstrap(self):
        """Test Python FastAPI bootstrap template generation."""
        files = get_intelligent_bootstrap_files(
            language="python",
            framework="fastapi",
            task_subject="Create FastAPI Service",
            task_description="A FastAPI REST API service",
        )

        assert len(files) >= 4
        paths = [f["path"] for f in files]
        assert "fastapi_entrypoint.py" in paths
        assert "requirements.txt" in paths
        assert "tui_runtime.md" in paths

        # Check FastAPI content
        main_file = next(f for f in files if f["path"] == "fastapi_entrypoint.py")
        assert "FastAPI" in main_file["content"]
        assert "uvicorn" in main_file["content"]

    def test_python_flask_bootstrap(self):
        """Test Python Flask bootstrap template generation."""
        files = get_intelligent_bootstrap_files(
            language="python",
            framework="flask",
            task_subject="Create Flask App",
            task_description="A Flask web application",
        )

        paths = [f["path"] for f in files]
        assert "fastapi_entrypoint.py" in paths

        main_file = next(f for f in files if f["path"] == "fastapi_entrypoint.py")
        assert "Flask" in main_file["content"]

    def test_python_generic_bootstrap(self):
        """Test Python generic bootstrap template generation."""
        files = get_intelligent_bootstrap_files(
            language="python",
            framework=None,
            task_subject="Python CLI Tool",
            task_description="A command line tool",
        )

        paths = [f["path"] for f in files]
        assert "fastapi_entrypoint.py" in paths
        assert "requirements.txt" in paths

    def test_typescript_bootstrap(self):
        """Test TypeScript bootstrap template generation."""
        files = get_intelligent_bootstrap_files(
            language="typescript",
            framework=None,
            task_subject="TypeScript Project",
            task_description="A TypeScript application",
        )

        paths = [f["path"] for f in files]
        assert "package.json" in paths
        assert "src/index.ts" in paths
        assert "tsconfig.json" in paths

    def test_javascript_bootstrap(self):
        """Test JavaScript bootstrap template generation."""
        files = get_intelligent_bootstrap_files(
            language="javascript",
            framework=None,
            task_subject="JavaScript Project",
            task_description="A JavaScript application",
        )

        paths = [f["path"] for f in files]
        assert "package.json" in paths
        assert "src/index.js" in paths

    def test_go_bootstrap(self):
        """Test Go bootstrap template generation."""
        files = get_intelligent_bootstrap_files(
            language="go",
            framework=None,
            task_subject="Go Service",
            task_description="A Go backend service",
        )

        paths = [f["path"] for f in files]
        assert "main.go" in paths
        assert "go.mod" in paths

    def test_rust_bootstrap(self):
        """Test Rust bootstrap template generation."""
        files = get_intelligent_bootstrap_files(
            language="rust",
            framework=None,
            task_subject="Rust Application",
            task_description="A Rust async application",
        )

        paths = [f["path"] for f in files]
        assert "Cargo.toml" in paths
        assert "src/main.rs" in paths
        assert "src/lib.rs" in paths
        assert "src/service.rs" in paths
        assert "tests/integration_test.rs" in paths

    def test_generic_bootstrap(self):
        """Test generic bootstrap for unknown languages."""
        files = get_intelligent_bootstrap_files(
            language="unknown",
            framework=None,
            task_subject="Project",
            task_description="A project",
        )

        paths = [f["path"] for f in files]
        assert "tui_runtime.md" in paths


class TestLegacyBootstrapFunctions:
    """Tests for legacy bootstrap file functions."""

    def test_get_python_bootstrap_files(self):
        """Test legacy Python bootstrap function."""
        files = get_python_bootstrap_files()

        assert len(files) >= 3
        paths = [f["path"] for f in files]
        assert "fastapi_entrypoint.py" in paths
        assert "requirements.txt" in paths

    def test_get_typescript_bootstrap_files(self):
        """Test legacy TypeScript bootstrap function."""
        files = get_typescript_bootstrap_files()

        assert len(files) >= 4
        paths = [f["path"] for f in files]
        assert "package.json" in paths
        assert "src/index.ts" in paths
        assert "tsconfig.json" in paths

    def test_get_generic_bootstrap_files(self):
        """Test legacy generic bootstrap function."""
        files = get_generic_bootstrap_files()

        assert len(files) >= 1
        assert files[0]["path"] == "tui_runtime.md"


class TestBootstrapUTF8:
    """Tests for UTF-8 encoding in bootstrap templates."""

    def test_chinese_task_subject(self):
        """Test Chinese characters in task subject."""
        files = get_intelligent_bootstrap_files(
            language="python",
            framework="fastapi",
            task_subject="创建FastAPI服务",
            task_description="REST API服务",
        )

        readme = next(f for f in files if f["path"] == "tui_runtime.md")
        assert "创建FastAPI服务" in readme["content"]

    def test_utf8_encoding_in_content(self):
        """Verify all content is valid UTF-8."""
        files = get_intelligent_bootstrap_files(
            language="python",
            framework="fastapi",
            task_subject="Test Project",
            task_description="Test Description",
        )

        for file_info in files:
            content = file_info["content"]
            # Should not raise UnicodeEncodeError
            encoded = content.encode("utf-8")
            decoded = encoded.decode("utf-8")
            assert decoded == content
