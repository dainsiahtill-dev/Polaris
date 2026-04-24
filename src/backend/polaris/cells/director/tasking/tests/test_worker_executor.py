"""Tests for worker_executor module."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

if TYPE_CHECKING:
    pass


class TestCodeGenerationResult:
    """Tests for CodeGenerationResult dataclass."""

    def test_creation_success(self) -> None:
        """Test CodeGenerationResult creation with success."""
        from polaris.cells.director.tasking.internal.worker_executor import (
            CodeGenerationResult,
        )

        result = CodeGenerationResult(
            success=True,
            files_created=[{"path": "a.py", "content": "# a"}],
        )
        assert result.success is True
        assert len(result.files_created) == 1
        assert result.error is None
        assert result.output == ""

    def test_creation_failure(self) -> None:
        """Test CodeGenerationResult creation with failure."""
        from polaris.cells.director.tasking.internal.worker_executor import (
            CodeGenerationResult,
        )

        result = CodeGenerationResult(
            success=False,
            error="Code generation blocked",
            output="Policy violation",
        )
        assert result.success is False
        assert result.error == "Code generation blocked"
        assert result.output == "Policy violation"

    def test_creation_with_duration(self) -> None:
        """Test CodeGenerationResult with duration."""
        from polaris.cells.director.tasking.internal.worker_executor import (
            CodeGenerationResult,
        )

        result = CodeGenerationResult(
            success=True,
            duration_ms=1500,
        )
        assert result.duration_ms == 1500


class TestWorkerExecutor:
    """Tests for WorkerExecutor class."""

    def test_executor_initialization(self) -> None:
        """Test WorkerExecutor initialization."""
        from polaris.cells.director.tasking.internal.worker_executor import WorkerExecutor

        executor = WorkerExecutor(workspace="/tmp/workspace")
        assert executor.workspace == "/tmp/workspace"
        assert executor._worker_id == ""

    def test_executor_with_worker_id(self) -> None:
        """Test WorkerExecutor with worker ID."""
        from polaris.cells.director.tasking.internal.worker_executor import WorkerExecutor

        executor = WorkerExecutor(workspace="/tmp", worker_id="worker-42")
        assert executor._worker_id == "worker-42"

    def test_executor_with_message_bus(self) -> None:
        """Test WorkerExecutor with message bus."""
        from polaris.cells.director.tasking.internal.worker_executor import WorkerExecutor

        mock_bus = MagicMock()
        executor = WorkerExecutor(workspace="/tmp", message_bus=mock_bus)
        assert executor._bus is mock_bus


class TestTaskClassification:
    """Tests for task classification."""

    def test_classify_bootstrap_task(self) -> None:
        """Test classification of bootstrap task."""
        from polaris.cells.director.tasking.internal.worker_executor import WorkerExecutor

        executor = WorkerExecutor(workspace="/tmp")
        task = MagicMock()
        task.subject = "Bootstrap new project"
        task.description = "Initialize a new project"

        result = executor._classify_task(task)
        assert result == "bootstrap"

    def test_classify_init_task(self) -> None:
        """Test classification of init task."""
        from polaris.cells.director.tasking.internal.worker_executor import WorkerExecutor

        executor = WorkerExecutor(workspace="/tmp")
        task = MagicMock()
        task.subject = "Init the repository"
        task.description = ""

        result = executor._classify_task(task)
        assert result == "bootstrap"

    def test_classify_file_creation(self) -> None:
        """Test classification of file creation task."""
        from polaris.cells.director.tasking.internal.worker_executor import WorkerExecutor

        executor = WorkerExecutor(workspace="/tmp")
        task = MagicMock()
        task.subject = "Create file main.py"
        task.description = ""

        result = executor._classify_task(task)
        assert result == "file_creation"

    def test_classify_code_generation(self) -> None:
        """Test classification of code generation task."""
        from polaris.cells.director.tasking.internal.worker_executor import WorkerExecutor

        executor = WorkerExecutor(workspace="/tmp")
        task = MagicMock()
        task.subject = "Implement user authentication"
        task.description = "Add login functionality"

        result = executor._classify_task(task)
        assert result == "code_generation"

    def test_classify_generic(self) -> None:
        """Test classification of generic task."""
        from polaris.cells.director.tasking.internal.worker_executor import WorkerExecutor

        executor = WorkerExecutor(workspace="/tmp")
        task = MagicMock()
        task.subject = "Do something"
        task.description = "Not sure what"

        result = executor._classify_task(task)
        assert result == "generic"


class TestTechStackExtraction:
    """Tests for technology stack extraction."""

    def test_extract_python_from_metadata(self) -> None:
        """Test extracting Python from metadata."""
        from polaris.cells.director.tasking.internal.worker_executor import WorkerExecutor

        executor = WorkerExecutor(workspace="/tmp")
        task = MagicMock()
        task.metadata = {"tech_stack": {"language": "python"}}

        result = executor._extract_tech_stack(task)
        assert result["language"] == "python"

    def test_extract_fastapi_framework(self) -> None:
        """Test extracting FastAPI framework from description."""
        from polaris.cells.director.tasking.internal.worker_executor import WorkerExecutor

        executor = WorkerExecutor(workspace="/tmp")
        task = MagicMock()
        task.subject = "Build API"
        task.description = "Create a FastAPI application"
        task.metadata = {}

        result = executor._extract_tech_stack(task)
        assert result["language"] == "python"
        assert result["framework"] == "fastapi"

    def test_extract_typescript(self) -> None:
        """Test extracting TypeScript from description."""
        from polaris.cells.director.tasking.internal.worker_executor import WorkerExecutor

        executor = WorkerExecutor(workspace="/tmp")
        task = MagicMock()
        task.subject = "Add component"
        task.description = "Create a TypeScript component"
        task.metadata = {}

        result = executor._extract_tech_stack(task)
        assert result["language"] == "typescript"

    def test_extract_go(self) -> None:
        """Test extracting Go from description."""
        from polaris.cells.director.tasking.internal.worker_executor import WorkerExecutor

        executor = WorkerExecutor(workspace="/tmp")
        task = MagicMock()
        task.subject = "Build service"
        task.description = "Create a golang microservice"
        task.metadata = {}

        result = executor._extract_tech_stack(task)
        assert result["language"] == "go"

    def test_extract_rust(self) -> None:
        """Test extracting Rust from description."""
        from polaris.cells.director.tasking.internal.worker_executor import WorkerExecutor

        executor = WorkerExecutor(workspace="/tmp")
        task = MagicMock()
        task.subject = "Implement parser"
        task.description = "Use Rust with cargo.toml"
        task.metadata = {}

        result = executor._extract_tech_stack(task)
        assert result["language"] == "rust"

    def test_extract_project_type_api(self) -> None:
        """Test extracting API project type."""
        from polaris.cells.director.tasking.internal.worker_executor import WorkerExecutor

        executor = WorkerExecutor(workspace="/tmp")
        task = MagicMock()
        task.subject = "Build REST"
        task.description = "Create a REST endpoint"
        task.metadata = {}

        result = executor._extract_tech_stack(task)
        assert result.get("project_type") == "api"

    def test_extract_unknown_language(self) -> None:
        """Test with unknown language."""
        from polaris.cells.director.tasking.internal.worker_executor import WorkerExecutor

        executor = WorkerExecutor(workspace="/tmp")
        task = MagicMock()
        task.subject = "Do something generic"
        task.description = "No specific language"
        task.metadata = {}

        result = executor._extract_tech_stack(task)
        assert result["language"] == "unknown"


class TestHelperMethods:
    """Tests for helper methods."""

    def test_is_probable_file_path_with_extension(self) -> None:
        """Test _is_probable_file_path with extension."""
        from polaris.cells.director.tasking.internal.worker_executor import WorkerExecutor

        executor = WorkerExecutor(workspace="/tmp")
        assert executor._is_probable_file_path("file.py") is True
        assert executor._is_probable_file_path("file.ts") is True
        assert executor._is_probable_file_path("file.txt") is True

    def test_is_probable_file_path_with_slash(self) -> None:
        """Test _is_probable_file_path with path separator."""
        from polaris.cells.director.tasking.internal.worker_executor import WorkerExecutor

        executor = WorkerExecutor(workspace="/tmp")
        assert executor._is_probable_file_path("src/file.py") is True
        assert executor._is_probable_file_path("path/to/file") is True

    def test_is_probable_file_path_false(self) -> None:
        """Test _is_probable_file_path returns False for non-paths."""
        from polaris.cells.director.tasking.internal.worker_executor import WorkerExecutor

        executor = WorkerExecutor(workspace="/tmp")
        assert executor._is_probable_file_path("") is False
        assert executor._is_probable_file_path("just text") is False

    def test_normalize_target_files_from_metadata(self) -> None:
        """Test _normalize_target_files from metadata."""
        from polaris.cells.director.tasking.internal.worker_executor import WorkerExecutor

        executor = WorkerExecutor(workspace="/tmp")
        task = MagicMock()
        task.metadata = {"target_files": ["a.py", "b.py"]}
        task.description = ""

        result = executor._normalize_target_files(task)
        assert "a.py" in result
        assert "b.py" in result

    def test_normalize_target_files_deduplication(self) -> None:
        """Test _normalize_target_files deduplicates."""
        from polaris.cells.director.tasking.internal.worker_executor import WorkerExecutor

        executor = WorkerExecutor(workspace="/tmp")
        task = MagicMock()
        task.metadata = {"target_files": ["a.py", "a.py", "b.py"]}
        task.description = ""

        result = executor._normalize_target_files(task)
        assert len(result) == 2

    def test_normalize_target_files_from_description(self) -> None:
        """Test _normalize_target_files from description."""
        from polaris.cells.director.tasking.internal.worker_executor import WorkerExecutor

        executor = WorkerExecutor(workspace="/tmp")
        task = MagicMock()
        task.metadata = {}
        # Description extraction splits by newline and checks for file paths
        # File paths must have extension or path separator
        task.description = "src/main.py\ntests/test_main.py"

        result = executor._normalize_target_files(task)
        assert "src/main.py" in result
        assert "tests/test_main.py" in result

    def test_compact_prompt_fragment_short_text(self) -> None:
        """Test _compact_prompt_fragment with short text."""
        from polaris.cells.director.tasking.internal.worker_executor import WorkerExecutor

        executor = WorkerExecutor(workspace="/tmp")
        result = executor._compact_prompt_fragment("short text", max_chars=100)
        assert result == "short text"

    def test_compact_prompt_fragment_truncates_long_text(self) -> None:
        """Test _compact_prompt_fragment truncates long text."""
        from polaris.cells.director.tasking.internal.worker_executor import WorkerExecutor

        executor = WorkerExecutor(workspace="/tmp")
        long_text = "a" * 200
        result = executor._compact_prompt_fragment(long_text, max_chars=50)
        # Result may be slightly longer due to truncation notice
        assert len(result) < 200
        assert result.startswith("a")


class TestExecutionMethods:
    """Tests for execution methods."""

    @pytest.mark.asyncio
    async def test_execute_file_creation(self) -> None:
        """Test _execute_file_creation method."""
        from polaris.cells.director.tasking.internal.worker_executor import WorkerExecutor

        executor = WorkerExecutor(workspace="/tmp")
        task = MagicMock()
        task.subject = "Create file test.py"
        task.description = "Create a test file"
        task.metadata = {"target_files": ["test.py"]}

        result = await executor._execute_file_creation(task)
        assert isinstance(result.success, bool)

    @pytest.mark.asyncio
    async def test_execute_bootstrap(self) -> None:
        """Test _execute_bootstrap method."""
        from polaris.cells.director.tasking.internal.worker_executor import WorkerExecutor

        executor = WorkerExecutor(workspace="/tmp")
        task = MagicMock()
        task.subject = "Bootstrap project"
        task.description = "Initialize"
        task.metadata = {"tech_stack": {"language": "python"}}

        result = await executor._execute_bootstrap(task)
        assert isinstance(result.success, bool)
        assert isinstance(result.files_created, list)

    @pytest.mark.asyncio
    async def test_execute_generic(self) -> None:
        """Test _execute_generic method."""
        from polaris.cells.director.tasking.internal.worker_executor import WorkerExecutor

        executor = WorkerExecutor(workspace="/tmp")
        task = MagicMock()
        task.subject = "Generic task"
        task.description = "Do something"
        task.metadata = {}

        # Generic falls back to code generation
        result = await executor._execute_generic(task)
        assert isinstance(result.success, bool)


class TestPolicyViolation:
    """Tests for policy violation handling."""

    def test_raise_code_writing_forbidden(self) -> None:
        """Test _raise_code_writing_forbidden raises exception."""
        from polaris.cells.director.tasking.internal.worker_executor import (
            CodeGenerationPolicyViolationError,
            WorkerExecutor,
        )

        executor = WorkerExecutor(workspace="/tmp")
        with pytest.raises(CodeGenerationPolicyViolationError):
            executor._raise_code_writing_forbidden("test_action")

    def test_fallback_code_files_raises(self) -> None:
        """Test _fallback_code_files raises policy error."""
        from polaris.cells.director.tasking.internal.worker_executor import (
            CodeGenerationPolicyViolationError,
            WorkerExecutor,
        )

        executor = WorkerExecutor(workspace="/tmp")
        task = MagicMock()

        with pytest.raises(CodeGenerationPolicyViolationError):
            executor._fallback_code_files(task)

    def test_deterministic_repair_enabled_returns_false(self) -> None:
        """Test _deterministic_repair_enabled returns False."""
        from polaris.cells.director.tasking.internal.worker_executor import WorkerExecutor

        executor = WorkerExecutor(workspace="/tmp")
        assert executor._deterministic_repair_enabled() is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
