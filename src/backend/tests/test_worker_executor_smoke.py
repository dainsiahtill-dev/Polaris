"""Smoke tests for WorkerExecutor.

All text operations MUST explicitly use UTF-8 encoding.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from polaris.cells.director.execution.internal.worker_executor import WorkerExecutor
from polaris.domain.entities import Task


class TestWorkerExecutorSmoke:
    """Smoke tests for WorkerExecutor class initialization and orchestration."""

    def test_worker_executor_initialization(self):
        """Test that WorkerExecutor can be initialized.

        Phase 3: FileApplyService and CodeGenerationEngine are Phase 4 deps
        and will be None until director.runtime is migrated. EvidenceService
        (Phase 3) is always available.
        """
        executor = WorkerExecutor(workspace="/tmp/test")

        assert executor.workspace == "/tmp/test"
        # Phase 3 available: EvidenceService
        assert executor._evidence_service is not None
        # Phase 4 deps (deferred import - may be None during Phase 3)
        # assert executor._file_service is not None   # Phase 4: FileApplyService
        # assert executor._code_engine is not None    # Phase 4: CodeGenerationEngine

    def test_worker_executor_initialization_with_message_bus(self):
        """Test that WorkerExecutor can be initialized with message bus."""
        from polaris.kernelone.events.message_bus import MessageBus

        bus = MessageBus()
        executor = WorkerExecutor(workspace="/tmp/test", message_bus=bus, worker_id="worker-1")

        assert executor._bus is not None
        assert executor._worker_id == "worker-1"


class TestTaskClassification:
    """Smoke tests for task classification."""

    def test_classify_bootstrap_task(self):
        """Test classification of bootstrap tasks."""
        executor = WorkerExecutor(workspace=".")

        task = Task(
            id="test-1",
            subject="Bootstrap Python project",
            description="Initialize a new Python project",
        )

        task_type = executor._classify_task(task)
        assert task_type == "bootstrap"

    def test_classify_file_creation_task(self):
        """Test classification of file creation tasks."""
        executor = WorkerExecutor(workspace=".")

        task = Task(
            id="test-2",
            subject="Create file config.yaml",
            description="Create a config file",
        )

        task_type = executor._classify_task(task)
        assert task_type == "file_creation"

    def test_classify_code_generation_task(self):
        """Test classification of code generation tasks."""
        executor = WorkerExecutor(workspace=".")

        task = Task(
            id="test-3",
            subject="Implement API endpoint",
            description="Create a REST API endpoint",
        )

        task_type = executor._classify_task(task)
        assert task_type == "code_generation"

    def test_classify_generic_task(self):
        """Test classification of generic tasks."""
        executor = WorkerExecutor(workspace=".")

        task = Task(
            id="test-4",
            subject="Do something",
            description="Generic task description",
        )

        task_type = executor._classify_task(task)
        assert task_type == "generic"


class TestTechStackExtraction:
    """Smoke tests for tech stack extraction."""

    def test_extract_python_fastapi_from_metadata(self):
        """Test extraction from metadata takes precedence."""
        executor = WorkerExecutor(workspace=".")

        task = Task(
            id="test-5",
            subject="Build API",
            description="Create a web service",
            metadata={
                "detected_language": "python",
                "detected_framework": "fastapi",
                "project_type": "api",
            },
        )

        tech_stack = executor._extract_tech_stack(task)

        assert tech_stack["language"] == "python"
        assert tech_stack["framework"] == "fastapi"
        assert tech_stack["project_type"] == "api"

    def test_extract_rust_from_description(self):
        """Test Rust detection from description."""
        executor = WorkerExecutor(workspace=".")

        task = Task(
            id="test-6",
            subject="Build Rust API",
            description="Create a Rust web service with actix-web",
        )

        tech_stack = executor._extract_tech_stack(task)

        assert tech_stack["language"] == "rust"

    def test_extract_go_from_subject(self):
        """Test Go detection from subject."""
        executor = WorkerExecutor(workspace=".")

        task = Task(
            id="test-7",
            subject="Go microservice",
            description="Build a microservice using golang with gin framework",
        )

        tech_stack = executor._extract_tech_stack(task)

        assert tech_stack["language"] == "go"


class TestBootstrapExecution:
    """Smoke tests for bootstrap execution."""

    @pytest.mark.asyncio
    async def test_execute_bootstrap_creates_files(self, tmp_path: Path):
        """Test that bootstrap execution creates files.

        Requires FileApplyService (Phase 4: director.runtime).
        Marked xfail during Phase 3 migration.
        """
        import warnings

        warnings.filterwarnings("ignore", category=DeprecationWarning)

        from polaris.cells.director.tasking.internal.worker_executor import _FileApplyService

        if _FileApplyService is None:
            pytest.xfail("FileApplyService not available (Phase 4 pending)")

        executor = WorkerExecutor(workspace=str(tmp_path))

        task = Task(
            id="test-bootstrap",
            subject="Bootstrap Python project",
            description="Create a new Python project",
            metadata={
                "detected_language": "python",
                "detected_framework": "fastapi",
            },
        )

        result = await executor._execute_bootstrap(task)

        assert result.success is True
        assert len(result.files_created) > 0

        # Verify files were actually created
        for file_info in result.files_created:
            file_path = tmp_path / file_info["path"]
            assert file_path.exists()


class TestCodeGenerationExecution:
    """Smoke tests for code generation execution."""

    @pytest.mark.asyncio
    async def test_execute_code_generation_with_no_files(self, tmp_path: Path):
        """Test code generation with no target files returns empty result."""
        executor = WorkerExecutor(workspace=str(tmp_path))

        task = Task(
            id="test-codegen",
            subject="Implement feature",
            description="Build something",
        )

        result = await executor._execute_code_generation(task)

        # Should handle gracefully without crashing
        assert result.output is not None


class TestFileCreationExecution:
    """Smoke tests for file creation execution."""

    @pytest.mark.asyncio
    async def test_execute_file_creation(self, tmp_path: Path):
        """Test file creation execution."""
        executor = WorkerExecutor(workspace=str(tmp_path))

        task = Task(
            id="test-files",
            subject="Create config files",
            description="Create necessary config files",
            metadata={
                "target_files": ["config.yaml", ".env.example"],
            },
        )

        result = await executor._execute_file_creation(task)

        # Should create files
        for file_info in result.files_created:
            file_path = tmp_path / file_info["path"]
            assert file_path.exists()


class TestWorkerExecutorUTF8:
    """Smoke tests for UTF-8 encoding in WorkerExecutor."""

    @pytest.mark.asyncio
    async def test_execute_with_chinese_task(self, tmp_path: Path):
        """Test execution with Chinese characters in task.

        Requires FileApplyService (Phase 4: director.runtime).
        Marked xfail during Phase 3 migration.
        """
        import warnings

        warnings.filterwarnings("ignore", category=DeprecationWarning)

        from polaris.cells.director.tasking.internal.worker_executor import _FileApplyService

        if _FileApplyService is None:
            pytest.xfail("FileApplyService not available (Phase 4 pending)")

        executor = WorkerExecutor(workspace=str(tmp_path))

        task = Task(
            id="test-chinese",
            subject="创建Python项目",
            description="使用FastAPI框架创建REST API服务",
            metadata={
                "detected_language": "python",
                "detected_framework": "fastapi",
            },
        )

        result = await executor._execute_bootstrap(task)

        assert result.success is True
        assert len(result.files_created) > 0
