"""Tests for evidence_service module.

All text operations MUST explicitly use UTF-8 encoding.
"""

from __future__ import annotations

from polaris.cells.audit.evidence.public.service import (
    EvidenceService,
    build_error_evidence,
    build_file_evidence,
    detect_language,
)


class TestDetectLanguage:
    """Tests for detect_language function."""

    def test_python_detection(self):
        """Test Python file detection."""
        assert detect_language("fastapi_entrypoint.py") == "python"
        assert detect_language("app.py") == "python"
        assert detect_language("module.py") == "python"

    def test_typescript_detection(self):
        """Test TypeScript file detection."""
        assert detect_language("app.ts") == "typescript"
        assert detect_language("component.tsx") == "typescript"
        assert detect_language("index.js") == "javascript"

    def test_javascript_detection(self):
        """Test JavaScript file detection."""
        assert detect_language("app.js") == "javascript"
        assert detect_language("script.jsx") == "javascript"

    def test_rust_detection(self):
        """Test Rust file detection."""
        assert detect_language("main.rs") == "rust"
        assert detect_language("lib.rs") == "rust"

    def test_go_detection(self):
        """Test Go file detection."""
        assert detect_language("main.go") == "go"
        assert detect_language("server.go") == "go"

    def test_java_detection(self):
        """Test Java file detection."""
        assert detect_language("Main.java") == "java"

    def test_yaml_detection(self):
        """Test YAML file detection."""
        assert detect_language("config.yaml") == "yaml"
        assert detect_language("docker-compose.yml") == "yaml"

    def test_json_detection(self):
        """Test JSON file detection."""
        assert detect_language("package.json") == "json"
        assert detect_language("config.json") == "json"

    def test_markdown_detection(self):
        """Test Markdown file detection."""
        assert detect_language("tui_runtime.md") == "markdown"
        assert detect_language("CHANGELOG.md") == "markdown"

    def test_unknown_detection(self):
        """Test unknown file detection."""
        assert detect_language("Makefile") == "unknown"
        assert detect_language("Dockerfile") == "unknown"
        assert detect_language("") == "unknown"


class TestBuildFileEvidence:
    """Tests for build_file_evidence function."""

    def test_build_file_evidence_from_files(self):
        """Test building file evidence from file list."""
        files_created = [
            {"path": "fastapi_entrypoint.py", "content": "print('hello')"},
            {"path": "readme.md", "content": "# Test Project"},
        ]

        evidence = build_file_evidence(files_created)

        assert len(evidence) == 2
        assert evidence[0].type == "file"
        assert evidence[0].path == "fastapi_entrypoint.py"
        assert evidence[0].content == "print('hello')"
        assert evidence[0].metadata["language"] == "python"
        assert evidence[0].metadata["size"] == 14  # len("print('hello')")

    def test_build_file_evidence_content_truncation(self):
        """Test that file content is truncated to 1KB."""
        long_content = "x" * 2000
        files_created = [{"path": "large.txt", "content": long_content}]

        evidence = build_file_evidence(files_created)

        assert len(evidence) == 1
        assert len(evidence[0].content) == 1000

    def test_build_file_evidence_empty_files(self):
        """Test building evidence with empty file list."""
        evidence = build_file_evidence([])
        assert len(evidence) == 0


class TestBuildErrorEvidence:
    """Tests for build_error_evidence function."""

    def test_build_error_evidence(self):
        """Test building error evidence."""
        evidence = build_error_evidence("Something went wrong", 1000)

        assert len(evidence) == 1
        assert evidence[0].type == "error"
        assert evidence[0].content == "Something went wrong"
        assert evidence[0].metadata["duration_ms"] == 1000


class TestEvidenceService:
    """Tests for EvidenceService class."""

    def test_evidence_service_build_file_evidence(self):
        """Test EvidenceService static method."""
        files = [{"path": "test.py", "content": "test"}]
        evidence = EvidenceService.build_file_evidence(files)

        assert len(evidence) == 1
        assert evidence[0].type == "file"

    def test_evidence_service_build_error_evidence(self):
        """Test EvidenceService error method."""
        evidence = EvidenceService.build_error_evidence("error", 500)

        assert len(evidence) == 1
        assert evidence[0].type == "error"

    def test_evidence_service_detect_language(self):
        """Test EvidenceService language detection."""
        lang = EvidenceService.detect_language("app.py")
        assert lang == "python"


class TestEvidenceServiceUTF8:
    """Tests for UTF-8 encoding in evidence."""

    def test_build_file_evidence_with_unicode(self):
        """Test evidence building with Unicode content."""
        files = [{"path": "unicode.py", "content": "print('你好世界')\n"}]

        evidence = build_file_evidence(files)

        assert len(evidence) == 1
        assert "你好世界" in evidence[0].content

    def test_build_error_evidence_with_unicode(self):
        """Test error evidence with Unicode."""
        evidence = build_error_evidence("错误信息: 文件不存在", 100)

        assert "错误信息" in evidence[0].content

