"""Tests for worker_executor module."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
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

    def test_normalize_target_files_filters_directory_scopes(self) -> None:
        """Directory-like target hints must not be handed to file apply as files."""
        from polaris.cells.director.tasking.internal.worker_executor import WorkerExecutor

        executor = WorkerExecutor(workspace="/tmp")
        task = MagicMock()
        task.metadata = {"target_files": ["src/renderer", "src/shared/generationSpec.ts", "package.json"]}
        task.description = ""

        result = executor._normalize_target_files(task)

        task_scope = MagicMock()
        task_scope.metadata = {"scope_paths": ["src/renderer"]}
        assert result == ["src/shared/generationSpec.ts", "package.json"]
        assert executor._normalize_scope_paths(task_scope) == ["src/renderer"]

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

    def test_normalize_target_files_from_labeled_description_line(self) -> None:
        """Target fallback accepts an explicit labeled path line."""
        from polaris.cells.director.tasking.internal.worker_executor import WorkerExecutor

        executor = WorkerExecutor(workspace="/tmp")
        task = MagicMock()
        task.metadata = {}
        task.description = "目标文件: src/api/v1/tasks.ts\npath: tests/tasks.test.ts"

        result = executor._normalize_target_files(task)

        assert result == ["src/api/v1/tasks.ts", "tests/tasks.test.ts"]

    def test_normalize_target_files_ignores_acceptance_prose(self) -> None:
        """Acceptance prose must not be mistaken for Director target files."""
        from polaris.cells.director.tasking.internal.worker_executor import WorkerExecutor

        executor = WorkerExecutor(workspace="/tmp")
        task = MagicMock()
        task.metadata = {"target_files": []}
        task.description = (
            "Acceptance: ['核心 API（POST /v1/tasks、PATCH /v1/tasks/{id}/status）"
            "在缺失 tenant_id 时返回400，并写入 security_audit_events.ndjson。']"
        )

        assert executor._normalize_target_files(task) == []

    def test_normalize_target_files_infers_from_scope_paths(self, tmp_path) -> None:
        """Scope-only PM tasks are narrowed to concrete workspace files."""
        from polaris.cells.director.tasking.internal.worker_executor import WorkerExecutor

        (tmp_path / "tsconfig.json").write_text("{}", encoding="utf-8")
        middleware = tmp_path / "src" / "middleware"
        middleware.mkdir(parents=True)
        (middleware / "auth.ts").write_text("export const auth = true;\n", encoding="utf-8")
        tests = tmp_path / "tests" / "integration"
        tests.mkdir(parents=True)
        (tests / "api.test.ts").write_text("test('api', () => {});\n", encoding="utf-8")

        executor = WorkerExecutor(workspace=str(tmp_path))
        task = MagicMock()
        task.subject = "RBAC中间件与tenant_id强制注入校验"
        task.description = "实现 tenant_id route RBAC permission checks"
        task.metadata = {
            "target_files": [],
            "scope_paths": [
                "src/middleware",
                "src/auth",
                "src/api",
                "tests/integration",
                "tests/integration/permissions",
            ],
        }

        result = executor._normalize_target_files(task)

        assert result == [
            "src/middleware/auth.ts",
            "src/auth/rbac-tenant-id-route.ts",
            "src/api/rbac-tenant-id-route.ts",
        ]

    def test_normalize_target_files_keeps_test_scopes_when_no_source_scope(self, tmp_path) -> None:
        """Test-only scope tasks can still infer test targets."""
        from polaris.cells.director.tasking.internal.worker_executor import WorkerExecutor

        (tmp_path / "tsconfig.json").write_text("{}", encoding="utf-8")
        executor = WorkerExecutor(workspace=str(tmp_path))
        task = MagicMock()
        task.subject = "Tenant isolation"
        task.description = ""
        task.metadata = {"target_files": [], "scope_paths": ["tests/integration"]}

        assert executor._normalize_target_files(task) == ["tests/integration/tenant-isolation.test.ts"]

    def test_code_generation_rounds_chunk_target_files_by_default(self, monkeypatch) -> None:
        """Default codegen splits larger target-file sets into manageable rounds."""
        from polaris.cells.director.tasking.internal.worker_executor import WorkerExecutor

        monkeypatch.delenv("KERNELONE_DIRECTOR_TARGET_FILE_CHUNK", raising=False)
        monkeypatch.delenv("KERNELONE_CE_ROUND_FILE_CHUNK", raising=False)
        executor = WorkerExecutor(workspace="/tmp")
        task = MagicMock()
        task.metadata = {"target_files": ["a.ts", "b.ts", "c.ts", "d.ts", "e.ts", "f.ts", "g.ts"]}
        task.description = ""

        rounds = executor._build_code_generation_rounds(task)

        assert [[item["path"] for item in round_plan] for round_plan in rounds] == [
            ["a.ts", "b.ts"],
            ["c.ts", "d.ts"],
            ["e.ts", "f.ts"],
            ["g.ts"],
        ]

    def test_code_generation_rounds_chunk_target_files_when_configured(self, monkeypatch) -> None:
        """Large target-file sets can still be split through an explicit env var."""
        from polaris.cells.director.tasking.internal.worker_executor import WorkerExecutor

        monkeypatch.setenv("KERNELONE_DIRECTOR_TARGET_FILE_CHUNK", "3")
        executor = WorkerExecutor(workspace="/tmp")
        task = MagicMock()
        task.metadata = {"target_files": ["a.ts", "b.ts", "c.ts", "d.ts", "e.ts", "f.ts", "g.ts"]}
        task.description = ""

        rounds = executor._build_code_generation_rounds(task)

        assert [[item["path"] for item in round_plan] for round_plan in rounds] == [
            ["a.ts", "b.ts", "c.ts"],
            ["d.ts", "e.ts", "f.ts"],
            ["g.ts"],
        ]

    def test_code_generation_rounds_allow_chunk_opt_out(self, monkeypatch) -> None:
        """A zero chunk env keeps the legacy single-round behavior available."""
        from polaris.cells.director.tasking.internal.worker_executor import WorkerExecutor

        monkeypatch.setenv("KERNELONE_DIRECTOR_TARGET_FILE_CHUNK", "0")
        executor = WorkerExecutor(workspace="/tmp")
        task = MagicMock()
        task.metadata = {"target_files": ["a.ts", "b.ts", "c.ts", "d.ts"]}
        task.description = ""

        rounds = executor._build_code_generation_rounds(task)

        assert len(rounds) == 1
        assert [item["path"] for item in rounds[0]] == ["a.ts", "b.ts", "c.ts", "d.ts"]

    def test_code_generation_rounds_chunk_test_targets_by_default(self, monkeypatch) -> None:
        """Test/spec targets keep the default chunking so full chains can converge."""
        from polaris.cells.director.tasking.internal.worker_executor import WorkerExecutor

        monkeypatch.delenv("KERNELONE_DIRECTOR_TARGET_FILE_CHUNK", raising=False)
        monkeypatch.delenv("KERNELONE_CE_ROUND_FILE_CHUNK", raising=False)
        executor = WorkerExecutor(workspace="/tmp")
        task = MagicMock()
        task.metadata = {
            "target_files": [
                "src/middleware/tenantContext.ts",
                "src/api/v1/tasks.ts",
                "src/modules/task/repository.ts",
                "tests/e2e/tenant_isolation.spec.ts",
            ]
        }
        task.description = ""

        rounds = executor._build_code_generation_rounds(task)

        assert [[item["path"] for item in round_plan] for round_plan in rounds] == [
            ["src/middleware/tenantContext.ts", "src/api/v1/tasks.ts"],
            ["src/modules/task/repository.ts", "tests/e2e/tenant_isolation.spec.ts"],
        ]

    def test_code_generation_rounds_keep_explicit_chunk_for_test_targets(self, monkeypatch) -> None:
        """Explicit chunk configuration still controls test/spec targets."""
        from polaris.cells.director.tasking.internal.worker_executor import WorkerExecutor

        monkeypatch.setenv("KERNELONE_DIRECTOR_TARGET_FILE_CHUNK", "2")
        executor = WorkerExecutor(workspace="/tmp")
        task = MagicMock()
        task.metadata = {
            "target_files": [
                "src/a.ts",
                "tests/e2e/a.spec.ts",
                "src/b.ts",
            ]
        }
        task.description = ""

        rounds = executor._build_code_generation_rounds(task)

        assert [[item["path"] for item in round_plan] for round_plan in rounds] == [
            ["src/a.ts", "tests/e2e/a.spec.ts"],
            ["src/b.ts"],
        ]

    def test_code_generation_rounds_infer_concrete_files_for_scope_only_task(self, tmp_path) -> None:
        """Scope-only module tasks get concrete targets instead of a model-decided prompt."""
        from polaris.cells.director.tasking.internal.worker_executor import WorkerExecutor

        (tmp_path / "tsconfig.json").write_text("{}", encoding="utf-8")
        executor = WorkerExecutor(workspace=str(tmp_path))
        task = MagicMock()
        task.subject = "Implement renderer state"
        task.metadata = {"target_files": ["src/renderer"], "scope_paths": ["src/renderer"]}
        task.description = ""

        rounds = executor._build_code_generation_rounds(task)
        prompt = executor._build_code_generation_prompt(task, round_files=rounds[0])

        assert rounds == [[{"path": "src/renderer/implement-renderer-state.ts"}]]
        assert "- (model may decide)" not in prompt
        assert "Concrete target files are declared" in prompt
        assert "src/renderer" in prompt
        assert "Do not output `Command:`" in prompt

    def test_verification_repair_round_includes_unresolved_import_candidates(self, tmp_path) -> None:
        """Retry rounds can repair missing modules inferred from unresolved relative imports."""
        from polaris.cells.director.tasking.internal.worker_executor import WorkerExecutor

        executor = WorkerExecutor(workspace=str(tmp_path))
        task = MagicMock()
        task.subject = "Implement task repository"
        task.description = ""
        task.metadata = {
            "target_files": ["tests/integration/task.test.ts"],
            "scope_paths": ["src", "tests"],
            "previous_verification_result": {
                "unresolved_imports": [
                    "tests/integration/task.test.ts: ../../src/app",
                    "tests/integration/task.test.ts: ../../src/repositories/task.repository",
                ],
            },
        }

        rounds = executor._build_code_generation_rounds(task)
        round_paths = [item["path"] for item in rounds[0]]
        prompt = executor._build_code_generation_prompt(task, round_files=round_paths)

        assert round_paths == [
            "tests/integration/task.test.ts",
            "src/app.ts",
            "src/repositories/task.repository.ts",
        ]
        assert "Verification Repair Context" in prompt
        assert "tests/integration/task.test.ts imports ../../src/app" in prompt
        assert "src/app.ts" in prompt
        assert "This is a verification repair round" in prompt

    def test_round_files_changed_since_detects_prior_runtime_output(self, tmp_path) -> None:
        """Later rounds can be skipped when an earlier runtime call already wrote them."""
        from polaris.cells.director.tasking.internal.worker_executor import WorkerExecutor

        executor = WorkerExecutor(workspace=str(tmp_path))
        paths = ["src/a.ts", "src/b.ts"]
        initial = executor._snapshot_workspace_files(paths)

        for relative_path in paths:
            target = tmp_path / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("export const value = 1;\n", encoding="utf-8")

        assert executor._round_files_changed_since(paths, initial) is True
        assert executor._collect_existing_file_records(paths) == [
            {"path": "src/a.ts", "content": ""},
            {"path": "src/b.ts", "content": ""},
        ]

    def test_round_files_changed_since_does_not_count_unchanged_existing_files(self, tmp_path) -> None:
        """Existing files only satisfy a round if they changed after the task started."""
        from polaris.cells.director.tasking.internal.worker_executor import WorkerExecutor

        target = tmp_path / "src" / "existing.ts"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("export const value = 1;\n", encoding="utf-8")

        executor = WorkerExecutor(workspace=str(tmp_path))
        paths = ["src/existing.ts"]
        initial = executor._snapshot_workspace_files(paths)

        assert executor._round_files_changed_since(paths, initial) is False

    def test_codegen_round_marker_satisfied(self, tmp_path) -> None:
        """Persisted round markers allow process-level retries to resume."""
        from polaris.cells.director.tasking.internal.worker_executor import WorkerExecutor

        target = tmp_path / "src" / "a.ts"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("export const value = 1;\n", encoding="utf-8")

        executor = WorkerExecutor(workspace=str(tmp_path))
        task = MagicMock()
        task.id = "T01-002"
        task.subject = "Task state"

        executor._write_code_generation_round_marker(task, 1, ["src/a.ts"])

        assert executor._code_generation_round_marker_satisfied(task, 1, ["src/a.ts"]) is True

        target.write_text("export const value = 2;\n", encoding="utf-8")

        assert executor._code_generation_round_marker_satisfied(task, 1, ["src/a.ts"]) is False

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
    async def test_code_generation_uses_per_call_timeout_not_task_budget(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path,
    ) -> None:
        """A long task budget must not become a long single LLM call."""
        from polaris.cells.director.tasking.internal.worker_executor import (
            _DEFAULT_DIRECTOR_LLM_CALL_TIMEOUT_SECONDS,
            WorkerExecutor,
        )

        monkeypatch.delenv("KERNELONE_DIRECTOR_LLM_CALL_TIMEOUT_SECONDS", raising=False)
        monkeypatch.delenv("KERNELONE_DIRECTOR_LLM_TIMEOUT_SECONDS", raising=False)
        monkeypatch.delenv("KERNELONE_WORKER_LLM_TIMEOUT", raising=False)

        class FakeCodeEngine:
            default_timeout: int | None = None
            invoked_timeout: int | None = None

            def resolve_llm_timeout(self, default_timeout: int) -> int:
                self.default_timeout = default_timeout
                return default_timeout

            def resolve_task_timeout_budget(self, task: Any, *, rounds: int) -> int:
                _ = (task, rounds)
                return 60

            def remaining_timeout(self, deadline_ts: float) -> int:
                _ = deadline_ts
                return 30

            async def invoke_generation_with_retries(self, **kwargs: Any) -> tuple[list[dict[str, str]], list[str]]:
                self.invoked_timeout = int(kwargs["per_call_timeout"])
                target = tmp_path / "src" / "app.ts"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("export const appName = 'polaris-test-app';\n", encoding="utf-8")
                return ([{"path": "src/app.ts", "content": ""}], [])

        executor = WorkerExecutor(workspace=str(tmp_path))
        fake_engine = FakeCodeEngine()
        executor._code_engine = fake_engine
        task = MagicMock()
        task.id = "T-timeout"
        task.subject = "Implement app"
        task.description = "Build the application"
        task.timeout_seconds = 3600
        task.metadata = {"target_files": ["src/app.ts"]}

        result = await executor._execute_code_generation(task)

        assert result.success is True
        assert fake_engine.default_timeout == _DEFAULT_DIRECTOR_LLM_CALL_TIMEOUT_SECONDS
        assert fake_engine.invoked_timeout == _DEFAULT_DIRECTOR_LLM_CALL_TIMEOUT_SECONDS

    @pytest.mark.asyncio
    async def test_code_generation_rejects_deterministic_scaffold_marker(self, tmp_path) -> None:
        """Generated files must pass the shared artifact quality gate before success."""
        from polaris.cells.director.tasking.internal.worker_executor import WorkerExecutor

        class FakeCodeEngine:
            def resolve_llm_timeout(self, default_timeout: int) -> int:
                return default_timeout

            def resolve_task_timeout_budget(self, task: Any, *, rounds: int) -> int:
                _ = (task, rounds)
                return 60

            def remaining_timeout(self, deadline_ts: float) -> int:
                _ = deadline_ts
                return 30

            async def invoke_generation_with_retries(self, **kwargs: Any) -> tuple[list[dict[str, str]], list[str]]:
                _ = kwargs
                target = tmp_path / "src" / "placeholder.ts"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("// Created by Polaris: placeholder\n", encoding="utf-8")
                return ([{"path": "src/placeholder.ts", "content": ""}], [])

        executor = WorkerExecutor(workspace=str(tmp_path))
        executor._code_engine = FakeCodeEngine()
        task = MagicMock()
        task.id = "T-quality"
        task.subject = "Implement meaningful feature"
        task.description = ""
        task.timeout_seconds = 60
        task.metadata = {"target_files": ["src/placeholder.ts"]}

        result = await executor._execute_code_generation(task)

        assert result.success is False
        assert result.error is not None
        assert "deterministic scaffold marker" in result.error

    @pytest.mark.asyncio
    async def test_code_generation_stops_after_empty_timeout_round(self) -> None:
        """A timed-out round should fail fast instead of wasting budget on later rounds."""
        from polaris.cells.director.tasking.internal.worker_executor import WorkerExecutor

        class FakeCodeEngine:
            calls = 0

            def resolve_llm_timeout(self, default_timeout: int) -> int:
                return default_timeout

            def resolve_task_timeout_budget(self, task: Any, *, rounds: int) -> int:
                _ = (task, rounds)
                return 900

            def remaining_timeout(self, deadline_ts: float) -> int:
                _ = deadline_ts
                return 300

            async def invoke_generation_with_retries(self, **kwargs: Any) -> tuple[list[dict[str, str]], list[str]]:
                _ = kwargs
                self.calls += 1
                return ([], ["director_runtime_codegen_timeout:300s"])

        executor = WorkerExecutor(workspace="/tmp")
        fake_engine = FakeCodeEngine()
        executor._code_engine = fake_engine
        task = MagicMock()
        task.id = "T-timeout"
        task.subject = "Implement two files"
        task.description = ""
        task.timeout_seconds = 900
        task.metadata = {"target_files": ["src/a.ts", "src/b.ts"]}

        result = await executor._execute_code_generation(task)

        assert result.success is False
        assert fake_engine.calls == 1
        assert result.error == "director_runtime_codegen_timeout:300s"

    @pytest.mark.asyncio
    async def test_code_generation_skips_persisted_round_marker(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path,
    ) -> None:
        """Retries should skip completed rounds and generate only missing rounds."""
        from polaris.cells.director.tasking.internal.worker_executor import WorkerExecutor

        monkeypatch.setenv("KERNELONE_DIRECTOR_TARGET_FILE_CHUNK", "1")
        first_target = tmp_path / "src" / "a.ts"
        first_target.parent.mkdir(parents=True, exist_ok=True)
        first_target.write_text("export const a = 1;\n", encoding="utf-8")

        executor = WorkerExecutor(workspace=str(tmp_path))
        task = MagicMock()
        task.id = "T-resume"
        task.subject = "Implement resumable rounds"
        task.description = ""
        task.timeout_seconds = 900
        task.metadata = {"target_files": ["src/a.ts", "src/b.ts"]}
        executor._write_code_generation_round_marker(task, 1, ["src/a.ts"])

        class FakeCodeEngine:
            calls: list[list[str]] = []

            def resolve_llm_timeout(self, default_timeout: int) -> int:
                return default_timeout

            def resolve_task_timeout_budget(self, task: Any, *, rounds: int) -> int:
                _ = (task, rounds)
                return 900

            def remaining_timeout(self, deadline_ts: float) -> int:
                _ = deadline_ts
                return 300

            async def invoke_generation_with_retries(self, **kwargs: Any) -> tuple[list[dict[str, str]], list[str]]:
                round_files = list(kwargs["round_files"])
                self.calls.append(round_files)
                for relative_path in round_files:
                    target = tmp_path / relative_path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(f"export const generated = '{relative_path}';\n", encoding="utf-8")
                return ([{"path": path, "content": ""} for path in round_files], [])

        fake_engine = FakeCodeEngine()
        executor._code_engine = fake_engine

        result = await executor._execute_code_generation(task)

        assert result.success is True
        assert fake_engine.calls == [["src/b.ts"]]
        assert [item["path"] for item in result.files_created] == ["src/a.ts", "src/b.ts"]

    def test_resolve_llm_call_timeout_hint_from_metadata(self) -> None:
        """Contracts may explicitly override per-call LLM timeout."""
        from polaris.cells.director.tasking.internal.worker_executor import WorkerExecutor

        executor = WorkerExecutor(workspace="/tmp")
        task = MagicMock()
        task.metadata = {"llm_call_timeout_seconds": "240"}

        assert executor._resolve_llm_call_timeout_hint(task) == 240

    def test_resolve_llm_call_timeout_hint_from_director_env(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Director runtime env may override the platform default."""
        from polaris.cells.director.tasking.internal.worker_executor import WorkerExecutor

        monkeypatch.setenv("KERNELONE_DIRECTOR_LLM_CALL_TIMEOUT_SECONDS", "720")
        executor = WorkerExecutor(workspace="/tmp")
        task = MagicMock()
        task.metadata = {}

        assert executor._resolve_llm_call_timeout_hint(task) == 720

    def test_resolve_llm_call_timeout_hint_uses_runtime_codegen_default(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Runtime codegen gets a larger default for slower provider-backed calls."""
        from polaris.cells.director.tasking.internal.worker_executor import (
            _DEFAULT_DIRECTOR_RUNTIME_LLM_CALL_TIMEOUT_SECONDS,
            WorkerExecutor,
        )

        monkeypatch.delenv("KERNELONE_DIRECTOR_LLM_CALL_TIMEOUT_SECONDS", raising=False)
        monkeypatch.delenv("KERNELONE_DIRECTOR_LLM_TIMEOUT_SECONDS", raising=False)
        monkeypatch.delenv("KERNELONE_WORKER_LLM_TIMEOUT", raising=False)
        monkeypatch.setenv("KERNELONE_DIRECTOR_RUNTIME_CODEGEN", "1")
        executor = WorkerExecutor(workspace="/tmp")
        task = MagicMock()
        task.metadata = {}

        assert executor._resolve_llm_call_timeout_hint(task) == _DEFAULT_DIRECTOR_RUNTIME_LLM_CALL_TIMEOUT_SECONDS

    @pytest.mark.asyncio
    async def test_execute_file_creation_requires_codegen_without_placeholder(self, tmp_path) -> None:
        """File creation must not synthesize deterministic placeholder content."""
        from polaris.cells.director.tasking.internal.worker_executor import WorkerExecutor

        executor = WorkerExecutor(workspace=str(tmp_path))
        executor._code_engine = None
        task = MagicMock()
        task.subject = "Create file test.py"
        task.description = "Create a test file"
        task.metadata = {"target_files": ["test.py"]}

        result = await executor._execute_file_creation(task)

        assert result.success is False
        assert result.error == "CodeGenerationEngine not available (Phase 4 pending)"
        assert not (tmp_path / "test.py").exists()

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
    async def test_bootstrap_missing_targets_require_codegen_without_placeholder(self, tmp_path) -> None:
        """Bootstrap must not fill missing PM target files with deterministic stubs."""
        from polaris.cells.director.tasking.internal.worker_executor import WorkerExecutor

        class FakeCodeEngine:
            def resolve_llm_timeout(self, default_timeout: int) -> int:
                return default_timeout

            def resolve_task_timeout_budget(self, task: Any, *, rounds: int) -> int:
                _ = (task, rounds)
                return 60

            def remaining_timeout(self, deadline_ts: float) -> int:
                _ = deadline_ts
                return 30

            async def invoke_generation_with_retries(self, **kwargs: Any) -> tuple[list[dict[str, str]], list[str]]:
                _ = kwargs
                target = tmp_path / "src" / "domain" / "cards.ts"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(
                    "export interface CardState { id: string; ownerId: string; zone: 'deck' | 'hand'; }\n",
                    encoding="utf-8",
                )
                return ([{"path": "src/domain/cards.ts", "content": ""}], [])

        executor = WorkerExecutor(workspace=str(tmp_path))
        executor._code_engine = FakeCodeEngine()
        task = MagicMock()
        task.id = "T-bootstrap-target"
        task.subject = "Bootstrap TypeScript card domain"
        task.description = "Create a multiplayer card domain model"
        task.timeout_seconds = 60
        task.metadata = {
            "tech_stack": {"language": "typescript"},
            "target_files": ["src/domain/cards.ts"],
        }

        result = await executor._execute_bootstrap(task)

        assert result.success is True
        content = (tmp_path / "src" / "domain" / "cards.ts").read_text(encoding="utf-8")
        assert "Generated file for" not in content
        assert "Created by Polaris" not in content

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
