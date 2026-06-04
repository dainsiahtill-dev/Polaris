"""Worker executor for code generation tasks.

Migrated from ``polaris.cells.director.execution.internal.worker_executor``.

This module implements the task execution orchestration for Workers,
delegating to specialized services for code generation, file I/O, and evidence.

All text operations MUST explicitly use UTF-8 encoding.

Phase 4 note:
    CodeGenerationEngine and FileApplyService are Phase 4 director.runtime deps.
    These are deferred via lazy import to avoid breaking director.execution.internal
    consumers that still depend on the original location.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import re
import sys
import time
import typing
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from polaris.cells.audit.evidence.public.service import EvidenceService
from polaris.cells.director.tasking.internal.bootstrap_template_catalog import (
    get_intelligent_bootstrap_files,
)
from polaris.domain.entities import Task, TaskResult
from polaris.domain.services import get_token_service
from polaris.kernelone.quality.artifact_quality import scan_workspace_artifact_quality

logger = logging.getLogger(__name__)

_DEFAULT_DIRECTOR_LLM_CALL_TIMEOUT_SECONDS = 300
_DEFAULT_DIRECTOR_RUNTIME_LLM_CALL_TIMEOUT_SECONDS = 600
_DIRECTOR_LLM_CALL_TIMEOUT_METADATA_KEYS = (
    "llm_call_timeout_seconds",
    "director_llm_timeout_seconds",
    "request_timeout_seconds",
)
_DIRECTOR_LLM_CALL_TIMEOUT_ENV_KEYS = (
    "KERNELONE_DIRECTOR_LLM_CALL_TIMEOUT_SECONDS",
    "KERNELONE_DIRECTOR_LLM_TIMEOUT_SECONDS",
    "KERNELONE_WORKER_LLM_TIMEOUT",
)
_SCOPE_INFERENCE_MAX_FILES = 12
_SCOPE_INFERENCE_MAX_FILES_PER_SCOPE = 3
_SCOPE_INFERENCE_SOURCE_EXTENSIONS = (
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".py",
    ".sql",
    ".json",
    ".toml",
    ".yaml",
    ".yml",
)
_SCOPE_INFERENCE_IGNORED_DIRS = {
    ".git",
    ".hg",
    ".mypy_cache",
    ".polaris",
    ".pytest_cache",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "runtime",
}
_TASK_TOKEN_STOPWORDS = {
    "according",
    "acceptance",
    "criteria",
    "execute",
    "goal",
    "implementation",
    "task",
    "test",
    "tests",
}


def _parse_positive_timeout_seconds(raw: Any) -> int | None:
    """Parse a positive timeout value in seconds."""
    if raw is None:
        return None
    try:
        timeout = int(float(str(raw).strip()))
    except (TypeError, ValueError):
        return None
    if timeout <= 0:
        return None
    return timeout


# -----------------------------------------------------------------------
# Dependency deferral: CodeGenerationEngine + FileApplyService
# Imports stay lazy to avoid circular imports during Director bootstrap.
# -----------------------------------------------------------------------

if TYPE_CHECKING:
    from polaris.cells.director.tasking.internal.code_generation_engine import (
        CODE_WRITING_FORBIDDEN_WARNING,
        CodeGenerationPolicyViolationError,
    )
    from polaris.kernelone.events.message_bus import MessageBus

    # Type stubs for deferred imports (mypy sees these, runtime uses importlib)
    _CodeGenerationEngine: type | None = None
    _FileApplyService: type | None = None

else:
    # Deferred runtime import using importlib to avoid triggering parent
    # package __init__ files during the circular import chain.
    import importlib as _importlib

    _CGE = None
    _FAS = None

    # Canonical tasking runtime entries. Missing imports fail closed below.
    with contextlib.suppress(ImportError):
        _CGE = _importlib.import_module("polaris.cells.director.tasking.internal.code_generation_engine")

    with contextlib.suppress(ImportError):
        _FAS = _importlib.import_module("polaris.cells.director.tasking.internal.file_apply_service")

    if _CGE is None:
        _CGE = sys.modules.get("polaris.cells.director.tasking.internal.code_generation_engine")

    if _CGE is None:
        _CODE_WARN = "[Policy] Code writing is forbidden"

        class _CodeGenPlaceholderError(Exception):
            pass

        _CGE = type(
            "_CGEPlaceholder",
            (),
            {
                "CODE_WRITING_FORBIDDEN_WARNING": _CODE_WARN,
                "CodeGenerationPolicyViolationError": _CodeGenPlaceholderError,
                "CodeGenerationEngine": None,
            },
        )()

    CODE_WRITING_FORBIDDEN_WARNING = getattr(
        _CGE, "CODE_WRITING_FORBIDDEN_WARNING", "[Policy] Code writing is forbidden"
    )
    CodeGenerationPolicyViolationError = getattr(_CGE, "CodeGenerationPolicyViolationError", Exception)  # type: ignore[misc]

    _CodeGenerationEngine = getattr(_CGE, "CodeGenerationEngine", None)  # type: ignore[assignment]
    _FileApplyService = getattr(_FAS, "FileApplyService", None) if _FAS else None  # type: ignore[assignment]


@dataclass
class CodeGenerationResult:
    """Result of code generation."""

    success: bool
    files_created: list[dict] = field(default_factory=list)
    error: str | None = None
    output: str = ""
    duration_ms: int = 0


class WorkerExecutor:
    """Executor that performs code generation for tasks.

    Responsibilities:
    - Parse task description to understand requirements
    - Delegate to CodeGenerationEngine for LLM generation
    - Use FileApplyService for file I/O
    - Build evidence with EvidenceService
    - Return structured results

    This class acts as an orchestrator, delegating to specialized services:
    - CodeGenerationEngine: policy-guarded generation entry points
    - FileApplyService: File writing, collection, patch application
    - EvidenceService: TaskEvidence construction
    """

    def __init__(
        self,
        workspace: str,
        message_bus: MessageBus | None = None,
        worker_id: str = "",
    ) -> None:
        self.workspace = workspace
        self.token_service = get_token_service()
        self._bus = message_bus
        self._worker_id = worker_id

        # Initialize specialized services (Phase 4 deps deferred)
        if _FileApplyService is not None:
            self._file_service = _FileApplyService(workspace, message_bus, worker_id)  # type: ignore[operator, no-redef]
        else:
            self._file_service = None  # type: ignore[assignment, no-redef]
        self._evidence_service = EvidenceService()
        if _CodeGenerationEngine is not None:
            self._code_engine = _CodeGenerationEngine(workspace, self)  # type: ignore[operator, no-redef]
        else:
            self._code_engine = None  # type: ignore[assignment, no-redef]

    def _raise_code_writing_forbidden(self, action: str) -> typing.NoReturn:
        """Fail closed when legacy code-writing helpers are invoked."""
        message = f"{CODE_WRITING_FORBIDDEN_WARNING} blocked_action={action}"
        logger.error(message)
        raise CodeGenerationPolicyViolationError(message)

    async def execute(self, task: Task) -> TaskResult:
        """Execute a task and return result.

        Args:
            task: The task to execute

        Returns:
            TaskResult with success/failure status and evidence
        """
        start_time = time.time()

        try:
            # Extract tech stack from task metadata (set by PM)
            tech_stack = self._extract_tech_stack(task)

            # Parse task to understand what needs to be done
            task_type = self._classify_task(task)

            logger.info("[WorkerExecutor] Executing task: %s", task.subject)
            logger.info("[WorkerExecutor] Task type: %s", task_type)
            if tech_stack.get("language"):
                logger.info("[WorkerExecutor] Detected language: %s", tech_stack["language"])
            if tech_stack.get("framework"):
                logger.info("[WorkerExecutor] Detected framework: %s", tech_stack["framework"])

            # Execute based on task type
            if task_type == "code_generation":
                result = await self._execute_code_generation(task)
            elif task_type == "file_creation":
                result = await self._execute_file_creation(task)
            elif task_type == "bootstrap":
                result = await self._execute_bootstrap(task)
            else:
                result = await self._execute_generic(task)

            duration_ms = int((time.time() - start_time) * 1000)

            # Collect evidence using EvidenceService
            evidence_list = self._evidence_service.build_file_evidence(result.files_created)

            return TaskResult(
                success=result.success,
                output=result.output,
                error=result.error,
                duration_ms=duration_ms,
                evidence=tuple(evidence_list),
            )

        except (
            AttributeError,
            OSError,
            RuntimeError,
            TypeError,
            ValueError,
        ) as exc:
            duration_ms = int((time.time() - start_time) * 1000)
            logger.error("Worker task execution failed", exc_info=exc)
            return TaskResult(
                success=False,
                output="",
                error=str(exc),
                duration_ms=duration_ms,
                evidence=(),
            )

    def _classify_task(self, task: Task) -> str:
        """Classify task type based on subject and description."""
        subject = task.subject.lower()
        description = task.description.lower()

        # Bootstrap tasks
        if "bootstrap" in subject or "init" in subject:
            return "bootstrap"

        # File creation tasks
        if "create file" in subject or "create directory" in subject:
            return "file_creation"

        # Code generation tasks
        if any(
            kw in subject or kw in description
            for kw in [
                "implement",
                "create",
                "build",
                "generate",
                "function",
                "class",
                "module",
                "api",
                "endpoint",
            ]
        ):
            return "code_generation"

        return "generic"

    def _extract_tech_stack(self, task: Task) -> dict:
        """Extract technology stack from task metadata (set by PM)."""
        tech_stack: dict[str, str] = {}

        # First try to get from metadata (new PM sets this)
        if task.metadata:
            if "tech_stack" in task.metadata:
                return dict(task.metadata["tech_stack"])
            if "detected_language" in task.metadata:
                tech_stack["language"] = task.metadata["detected_language"]
            if "detected_framework" in task.metadata:
                tech_stack["framework"] = task.metadata["detected_framework"]
            if "project_type" in task.metadata:
                tech_stack["project_type"] = task.metadata["project_type"]
            if tech_stack:
                return tech_stack

        # Fallback: detect from task description
        description = (task.description or "").lower()
        subject = (task.subject or "").lower()
        text = f"{subject} {description}"

        language_patterns: dict[str, list[str]] = {
            "python": [
                r"\bpython\b",
                r"\bfastapi\b",
                r"\bflask\b",
                r"\bdjango\b",
                r"\bpytest\b",
                r"requirements\.txt",
                r"\.py\b",
            ],
            "typescript": [
                r"\btypescript\b",
                r"\bts-node\b",
                r"tsconfig\.json",
                r"\bts\b(?=\s+(project|service|app|api|module|code|conventions))",
                r"\.tsx?\b",
            ],
            "javascript": [
                r"\bjavascript\b",
                r"\bnode\.?js\b",
                r"\bnode\b",
                r"\bexpress\b",
                r"\bjs\b(?=\s+(project|service|app|api|module|code|conventions))",
                r"\.jsx?\b",
            ],
            "go": [
                r"\bgolang\b",
                r"\bgo\b(?=\s+(project|service|app|api|module|code|conventions|test|build))",
                r"go\.mod",
                r"\.go\b",
                r"\bgin\b",
                r"\bfiber\b",
            ],
            "rust": [
                r"\brust\b",
                r"\bcargo\b",
                r"cargo\.toml",
                r"\.rs\b",
            ],
            "java": [
                r"\bjava\b",
                r"\bspring\b",
                r"\bgradle\b",
                r"pom\.xml",
            ],
        }
        language_scores: dict[str, int] = {}
        for language, patterns in language_patterns.items():
            score = sum(1 for pattern in patterns if re.search(pattern, text))
            if score > 0:
                language_scores[language] = score
        if language_scores:
            tech_stack["language"] = max(language_scores, key=language_scores.get)  # type: ignore[arg-type]
        else:
            tech_stack["language"] = "unknown"

        framework_patterns: dict[str, str] = {
            "fastapi": r"\bfastapi\b",
            "flask": r"\bflask\b",
            "django": r"\bdjango\b",
            "react": r"\breact\b",
            "vue": r"\bvue\b",
            "express": r"\bexpress\b",
        }
        for framework, pattern in framework_patterns.items():
            if re.search(pattern, text):
                tech_stack["framework"] = framework
                break

        if re.search(r"\bapi\b|\brest\b|\bendpoint\b", text):
            tech_stack["project_type"] = "api"
        elif re.search(r"\bcli\b|\bcommand\b|\bterminal\b", text):
            tech_stack["project_type"] = "cli"
        elif re.search(r"\bweb\b|\bfrontend\b|\bui\b", text):
            tech_stack["project_type"] = "web"
        elif re.search(r"\bservice\b|\bmicroservice\b", text):
            tech_stack["project_type"] = "microservice"
        elif re.search(r"\blibrary\b|\bpackage\b|\bsdk\b", text):
            tech_stack["project_type"] = "library"
        else:
            tech_stack["project_type"] = "generic"

        return tech_stack

    # === Execution Methods ===

    async def _execute_code_generation(self, task: Task) -> CodeGenerationResult:
        """Execute code generation task with LLM."""
        if self._code_engine is None:
            return CodeGenerationResult(
                success=False,
                error="CodeGenerationEngine not available (Phase 4 pending)",
                output="WorkerExecutor: code engine not initialised",
            )

        files_created: list[dict] = []
        policy_messages: list[str] = []

        # Get model and timeout from environment
        model = os.environ.get("KERNELONE_WORKER_MODEL", "MiniMax-M2.5")
        per_call_timeout = self._code_engine.resolve_llm_timeout(self._resolve_llm_call_timeout_hint(task))

        # Build code generation rounds
        rounds = self._build_code_generation_rounds(task)
        total_rounds = len(rounds) if rounds else 1
        target_files = self._normalize_target_files(task)
        initial_target_signatures = self._snapshot_workspace_files(target_files)
        budget_seconds = self._code_engine.resolve_task_timeout_budget(
            task,
            rounds=total_rounds,  # type: ignore[arg-type]
        )
        deadline_ts = time.time() + budget_seconds
        spin_tracker: dict[str, dict[str, Any]] = {}

        logger.info(
            "[WorkerExecutor] CodeGen budget: %ss (rounds=%s)",
            budget_seconds,
            total_rounds,
        )

        if rounds:
            logger.info(
                "[WorkerExecutor] CodeGen ChiefEngineer rounds: %s",
                len(rounds),
            )
            for round_index, round_plan in enumerate(rounds, start=1):
                remaining = self._code_engine.remaining_timeout(deadline_ts)  # type: ignore[operator]
                if remaining <= 0:
                    logger.info(
                        "[WorkerExecutor] CodeGen budget exhausted before round %s/%s",
                        round_index,
                        len(rounds),
                    )
                    break
                round_paths = [
                    str(item.get("path") or "").strip()
                    for item in round_plan
                    if isinstance(item, dict) and str(item.get("path") or "").strip()
                ]
                if self._code_generation_round_marker_satisfied(task, round_index, round_paths):
                    files_created.extend(self._collect_existing_file_records(round_paths))
                    logger.info(
                        "[WorkerExecutor] CodeGen round %s/%s satisfied by persisted progress marker",
                        round_index,
                        len(rounds),
                    )
                    continue
                if files_created and self._round_files_changed_since(round_paths, initial_target_signatures):
                    files_created.extend(self._collect_existing_file_records(round_paths))
                    logger.info(
                        "[WorkerExecutor] CodeGen round %s/%s already satisfied by earlier runtime output",
                        round_index,
                        len(rounds),
                    )
                    continue
                prompt = self._build_code_generation_prompt(
                    task,
                    round_index=round_index,
                    round_total=len(rounds),
                    round_files=round_paths,
                )
                try:
                    round_files, round_warnings = await self._code_engine.invoke_generation_with_retries(  # type: ignore[operator]
                        task=task,
                        prompt=prompt,
                        model=model,
                        per_call_timeout=per_call_timeout,
                        deadline_ts=deadline_ts,
                        round_label=f"{round_index}/{len(rounds)}",
                        round_files=round_paths,
                        spin_tracker=spin_tracker,
                    )
                    files_created.extend(round_files)  # type: ignore[union-attr]
                    if round_warnings:
                        policy_messages.extend(round_warnings)  # type: ignore[union-attr]
                        logger.warning(
                            "Worker code generation blocked for task %s: %s",
                            task.id,
                            "; ".join(round_warnings[:3]),
                        )
                    if round_files:
                        self._write_code_generation_round_marker(task, round_index, round_paths)
                    if not round_files and round_warnings:
                        return CodeGenerationResult(
                            success=False,
                            files_created=files_created,
                            error=round_warnings[0],
                            output=(f"Code generation round {round_index}/{len(rounds)} produced no accepted files"),
                        )
                except (
                    CodeGenerationPolicyViolationError,
                    OSError,
                    RuntimeError,
                    TypeError,
                    ValueError,
                ) as exc:
                    policy_messages.append(str(exc))
                    logger.error(
                        "Worker code generation round failed for task %s round %s/%s",
                        task.id,
                        round_index,
                        len(rounds),
                        exc_info=True,
                    )
                    continue

        if not files_created and policy_messages:
            return CodeGenerationResult(
                success=False,
                files_created=[],
                error=policy_messages[0],
                output="Code generation blocked by security policy",
            )

        quality_error = self._generated_artifact_quality_error(files_created, phase="Code generation")
        if quality_error:
            return CodeGenerationResult(
                success=False,
                files_created=files_created,
                error=quality_error,
                output=quality_error,
            )

        return CodeGenerationResult(
            success=len(files_created) > 0,
            files_created=files_created,
            output=f"Generated {len(files_created)} files",
        )

    async def _execute_file_creation(self, task: Task) -> CodeGenerationResult:
        """Execute file creation through the audited code-generation path."""
        target_files = self._normalize_target_files(task)
        if not target_files:
            return CodeGenerationResult(
                success=False,
                error="File creation rejected: no concrete target files declared",
                output="File creation requires concrete target files",
            )
        return await self._execute_code_generation(task)

    async def _execute_bootstrap(self, task: Task) -> CodeGenerationResult:
        """Execute bootstrap task with template or LLM generation."""
        if self._file_service is None:
            return CodeGenerationResult(
                success=False,
                error="FileApplyService not available (Phase 4 pending)",
                output="WorkerExecutor: file service not initialised",
            )

        tech_stack = self._extract_tech_stack(task)
        language = tech_stack.get("language", "generic")
        framework = tech_stack.get("framework")

        logger.info("[WorkerExecutor] Bootstrap: %s project detected", language)
        if framework:
            logger.info("[WorkerExecutor] Framework: %s", framework)

        files_created: list[dict] = []

        # Try intelligent bootstrap templates first
        bootstrap_files = get_intelligent_bootstrap_files(
            language=language,
            framework=framework,
            task_subject=task.subject or "",
            task_description=task.description or "",
        )

        if bootstrap_files:
            files_created = self._file_service.write_files(bootstrap_files)  # type: ignore[operator]
            logger.info(
                "[WorkerExecutor] Bootstrap template generated %s files",
                len(files_created),
            )

        target_files = self._normalize_target_files(task)
        existing_paths = {str(item.get("path") or "").strip() for item in files_created if isinstance(item, dict)}
        missing_target_files = [path for path in target_files if path and path not in existing_paths]
        if missing_target_files:
            codegen_result = await self._execute_code_generation(task)
            files_created.extend(codegen_result.files_created)
            if not codegen_result.success:
                return CodeGenerationResult(
                    success=False,
                    files_created=files_created,
                    error=codegen_result.error
                    or "Bootstrap target contract requires runtime code generation for missing files",
                    output=codegen_result.output,
                )
            logger.info(
                "[WorkerExecutor] Bootstrap target contract generated %s files through runtime codegen",
                len(codegen_result.files_created),
            )

        return CodeGenerationResult(
            success=len(files_created) > 0,
            files_created=files_created,
            output=f"Bootstrapped {language} project with {len(files_created)} files",
        )

    async def _execute_generic(self, task: Task) -> CodeGenerationResult:
        """Execute generic task - delegate to code generation."""
        return await self._execute_code_generation(task)

    # === Helper Methods ===

    def _resolve_llm_call_timeout_hint(self, task: Task) -> int:
        """Resolve per-call LLM timeout without using the task total budget."""
        metadata = task.metadata if isinstance(task.metadata, dict) else {}
        for key in _DIRECTOR_LLM_CALL_TIMEOUT_METADATA_KEYS:
            timeout = _parse_positive_timeout_seconds(metadata.get(key))
            if timeout is not None:
                return timeout
        for key in _DIRECTOR_LLM_CALL_TIMEOUT_ENV_KEYS:
            timeout = _parse_positive_timeout_seconds(os.environ.get(key))
            if timeout is not None:
                return timeout
        runtime_codegen_enabled = getattr(self._code_engine, "runtime_codegen_enabled", None)
        if callable(runtime_codegen_enabled):
            try:
                if bool(runtime_codegen_enabled()):
                    return _DEFAULT_DIRECTOR_RUNTIME_LLM_CALL_TIMEOUT_SECONDS
            except (RuntimeError, TypeError, ValueError):
                logger.debug("Failed to resolve runtime Director codegen mode", exc_info=True)
        return _DEFAULT_DIRECTOR_LLM_CALL_TIMEOUT_SECONDS

    def _normalize_target_files(self, task: Task) -> list[str]:
        """Get normalized list of target files from task."""
        files: list[str] = []
        seen: set[str] = set()

        # Get from metadata
        metadata = task.metadata if isinstance(task.metadata, dict) else {}

        # From target_files
        target_files = metadata.get("target_files", [])
        if isinstance(target_files, list):
            for f in target_files:
                path = str(f or "").strip()
                if path and self._is_concrete_target_file_path(path) and path not in seen:
                    seen.add(path)
                    files.append(path)

        # From file_plan
        file_plan = metadata.get("file_plan", [])
        if isinstance(file_plan, list):
            for item in file_plan:
                if isinstance(item, dict):
                    path = str(item.get("path") or "").strip()
                    if path and self._is_concrete_target_file_path(path) and path not in seen:
                        seen.add(path)
                        files.append(path)

        # Fallback: extract from description
        if not files and task.description:
            for line in task.description.split("\n"):
                description_path = self._extract_description_target_path(line)
                if description_path and description_path not in seen:
                    seen.add(description_path)
                    files.append(description_path)

        if not files:
            for inferred_path in self._infer_target_files_from_scope_paths(task):
                if inferred_path not in seen:
                    seen.add(inferred_path)
                    files.append(inferred_path)

        return files

    def _infer_target_files_from_scope_paths(self, task: Task) -> list[str]:
        """Infer concrete target files from PM directory scopes.

        PM contracts sometimes describe module scopes without concrete files.
        Passing that ambiguity through to the LLM creates broad prompts that can
        consume the whole task budget. This inference keeps the Director prompt
        bounded by existing files in the declared scopes and one conservative
        new file candidate for empty scopes.
        """
        inferred: list[str] = []
        seen: set[str] = set()
        for scope in self._normalize_scope_paths(task):
            if len(inferred) >= _SCOPE_INFERENCE_MAX_FILES:
                break
            scope_files = self._existing_files_for_scope(scope, task)
            if not scope_files:
                synthesized = self._synthesize_scope_target_file(scope, task)
                scope_files = [synthesized] if synthesized else []
            for path in scope_files:
                if len(inferred) >= _SCOPE_INFERENCE_MAX_FILES:
                    break
                if path and path not in seen:
                    seen.add(path)
                    inferred.append(path)
        source_targets = [path for path in inferred if not self._is_test_like_target_file(path)]
        if source_targets:
            return source_targets
        return inferred

    def _existing_files_for_scope(self, scope: str, task: Task) -> list[str]:
        """Return existing workspace files under a declared scope."""
        normalized_scope = str(scope or "").strip().replace("\\", "/").rstrip("/")
        if not normalized_scope or os.path.isabs(normalized_scope) or ".." in normalized_scope.split("/"):
            return []
        if self._is_concrete_target_file_path(normalized_scope):
            full_file = self._resolve_workspace_file_path(normalized_scope)
            if full_file and os.path.isfile(full_file):
                return [normalized_scope]
            return []

        full_scope = self._resolve_workspace_file_path(normalized_scope)
        if not full_scope or not os.path.isdir(full_scope):
            return []

        candidates: list[str] = []
        for root, dirnames, filenames in os.walk(full_scope):
            dirnames[:] = [
                name
                for name in sorted(dirnames)
                if name not in _SCOPE_INFERENCE_IGNORED_DIRS and not name.startswith(".")
            ]
            for filename in sorted(filenames):
                full_path = os.path.join(root, filename)
                rel_path = os.path.relpath(full_path, self.workspace).replace("\\", "/")
                if self._is_scope_inference_candidate(rel_path):
                    candidates.append(rel_path)
            if len(candidates) >= _SCOPE_INFERENCE_MAX_FILES_PER_SCOPE * 4:
                break

        tokens = self._task_ascii_tokens(task)
        candidates.sort(key=lambda path: self._scope_candidate_sort_key(path, tokens))
        return candidates[:_SCOPE_INFERENCE_MAX_FILES_PER_SCOPE]

    def _is_scope_inference_candidate(self, path: str) -> bool:
        """Return whether an existing file is useful as an inferred target."""
        normalized = str(path or "").strip().replace("\\", "/")
        if not self._is_concrete_target_file_path(normalized):
            return False
        leaf = os.path.basename(normalized)
        if leaf.startswith(".") and leaf not in {".env", ".env.example", ".gitignore"}:
            return False
        lowered = normalized.lower()
        return lowered.endswith(_SCOPE_INFERENCE_SOURCE_EXTENSIONS)

    def _scope_candidate_sort_key(self, path: str, tokens: list[str]) -> tuple[int, int, str]:
        """Prefer paths whose names match task tokens, then shorter paths."""
        lowered = str(path or "").lower()
        token_hits = sum(1 for token in tokens if token and token in lowered)
        return (-token_hits, len(lowered), lowered)

    def _synthesize_scope_target_file(self, scope: str, task: Task) -> str | None:
        """Create a conservative target filename inside an empty declared scope."""
        normalized_scope = str(scope or "").strip().replace("\\", "/").rstrip("/")
        if not normalized_scope or os.path.isabs(normalized_scope) or ".." in normalized_scope.split("/"):
            return None
        if self._is_concrete_target_file_path(normalized_scope):
            return normalized_scope
        slug = self._task_slug(task)
        if self._looks_like_test_scope(normalized_scope):
            filename = self._test_filename(slug)
        else:
            filename = f"{slug}{self._preferred_source_extension()}"
        return f"{normalized_scope}/{filename}"

    def _task_slug(self, task: Task) -> str:
        """Build an ASCII slug from task text for synthesized file names."""
        tokens = self._task_ascii_tokens(task)
        if not tokens:
            return "implementation"
        return "-".join(tokens[:4])

    def _task_ascii_tokens(self, task: Task) -> list[str]:
        """Extract stable ASCII tokens from task fields."""
        parts = [
            str(getattr(task, "subject", "") or ""),
            str(getattr(task, "description", "") or ""),
        ]
        metadata = task.metadata if isinstance(task.metadata, dict) else {}
        for key in ("acceptance_criteria", "execution_checklist"):
            value = metadata.get(key)
            if isinstance(value, list):
                parts.extend(str(item or "") for item in value)
        text = " ".join(parts).replace("_", " ").replace("-", " ").lower()
        tokens: list[str] = []
        seen: set[str] = set()
        for token in re.findall(r"[a-z][a-z0-9]{1,}", text):
            if token in _TASK_TOKEN_STOPWORDS or token in seen:
                continue
            seen.add(token)
            tokens.append(token)
            if len(tokens) >= 12:
                break
        return tokens

    def _preferred_source_extension(self) -> str:
        """Infer the most suitable source extension for new scope targets."""
        if os.path.isfile(os.path.join(self.workspace, "tsconfig.json")):
            return ".ts"
        if os.path.isfile(os.path.join(self.workspace, "pyproject.toml")):
            return ".py"
        if os.path.isfile(os.path.join(self.workspace, "package.json")):
            return ".js"
        return ".ts"

    def _looks_like_test_scope(self, scope: str) -> bool:
        """Return whether a scope is intended for tests."""
        scope_text = str(scope or "").strip().lower().replace("\\", "/")
        normalized = f"/{scope_text}/"
        return (
            "/test/" in normalized
            or "/tests/" in normalized
            or normalized.endswith("/test/")
            or normalized.endswith("/tests/")
        )

    def _test_filename(self, slug: str) -> str:
        """Return a conventional test filename for the current workspace."""
        extension = self._preferred_source_extension()
        if extension == ".py":
            return f"test_{slug.replace('-', '_')}.py"
        if extension == ".js":
            return f"{slug}.test.js"
        return f"{slug}.test.ts"

    def _extract_description_target_path(self, line: str) -> str | None:
        """Extract an explicit target path from one description line.

        This fallback is intentionally conservative. PM acceptance text can
        contain API routes, filenames used as evidence labels, or prose with
        punctuation; those are not target-file contracts and must not pollute
        Director prompts.
        """
        cleaned = str(line or "").strip()
        if not cleaned:
            return None
        cleaned = re.sub(r"^[\d]+[.)]\s*", "", cleaned)
        cleaned = re.sub(r"^[-*•]\s*", "", cleaned)
        cleaned = cleaned.strip()
        labeled = re.match(
            r"(?i)^(?:file|path|target|target_file|target files?|目标文件|文件)\s*[:：]\s*(.+?)\s*$",
            cleaned,
        )
        if labeled:
            cleaned = labeled.group(1).strip()
        cleaned = cleaned.strip("`'\"")
        if any(ch.isspace() for ch in cleaned):
            return None
        if any(ch in cleaned for ch in "[]{}()，。；；：,;"):
            return None
        normalized = cleaned.replace("\\", "/")
        if self._is_probable_file_path(normalized) and self._is_concrete_target_file_path(normalized):
            return normalized
        return None

    def _is_probable_file_path(self, path: str) -> bool:
        """Check if string looks like a file path."""
        if not path:
            return False
        # Must have extension or look like a path
        has_ext = "." in path and len(path.rsplit(".", maxsplit=1)[-1]) <= 5
        has_slash = "/" in path or "\\" in path
        return has_ext or has_slash

    def _is_concrete_target_file_path(self, path: str) -> bool:
        """Return true when a PM target path names a file, not a directory scope."""
        token = str(path or "").strip().replace("\\", "/")
        if not token or token.endswith("/"):
            return False
        if os.path.isabs(token) or ".." in token.split("/"):
            return False
        leaf = os.path.basename(token)
        if not leaf:
            return False
        known_extensionless = {
            ".dockerignore",
            ".env",
            ".env.example",
            ".gitignore",
            "dockerfile",
            "go.mod",
            "go.sum",
            "license",
            "makefile",
            "readme",
        }
        if leaf.lower() in known_extensionless:
            return True
        return "." in leaf

    def _normalize_scope_paths(self, task: Task) -> list[str]:
        """Return directory/module scopes declared by PM without treating them as files."""
        metadata = task.metadata if isinstance(task.metadata, dict) else {}
        scopes: list[str] = []
        seen: set[str] = set()
        for key in ("scope_paths", "write_scope"):
            value = metadata.get(key)
            if not isinstance(value, list):
                continue
            for raw in value:
                path = str(raw or "").strip().replace("\\", "/").rstrip("/")
                if not path or path in seen:
                    continue
                seen.add(path)
                scopes.append(path)
        return scopes

    def _verification_feedback(self, task: Task) -> dict[str, Any]:
        """Return previous verification diagnostics carried by the workflow retry."""
        metadata = task.metadata if isinstance(task.metadata, dict) else {}
        direct = metadata.get("previous_verification_result")
        if isinstance(direct, dict) and direct:
            return direct
        phase_context = metadata.get("phase_context")
        if isinstance(phase_context, dict):
            phase_verification = phase_context.get("verification_result")
            if isinstance(phase_verification, dict) and phase_verification:
                return phase_verification
        task_context = metadata.get("task_context")
        if isinstance(task_context, dict):
            previous = task_context.get("previous_verification_result")
            if isinstance(previous, dict) and previous:
                return previous
            nested_phase = task_context.get("phase_context")
            if isinstance(nested_phase, dict):
                nested_verification = nested_phase.get("verification_result")
                if isinstance(nested_verification, dict) and nested_verification:
                    return nested_verification
        return {}

    @staticmethod
    def _parse_unresolved_import_entry(entry: Any) -> tuple[str, str] | None:
        token = str(entry or "").strip()
        if ":" not in token:
            return None
        source_file, import_ref = token.split(":", maxsplit=1)
        source_file = source_file.strip().replace("\\", "/")
        import_ref = import_ref.strip().strip("`'\"")
        if not source_file or not import_ref.startswith("."):
            return None
        return source_file, import_ref

    def _candidate_paths_for_unresolved_import(self, source_file: str, import_ref: str) -> list[str]:
        source_dir = os.path.dirname(source_file.replace("\\", "/"))
        resolved = os.path.normpath(os.path.join(source_dir, import_ref)).replace("\\", "/")
        if not resolved or resolved.startswith("../") or os.path.isabs(resolved):
            return []
        leaf = os.path.basename(resolved)
        extension = os.path.splitext(leaf)[1].lower()
        if extension in {".ts", ".tsx", ".js", ".jsx", ".json", ".mjs", ".cjs"}:
            raw_candidates = [resolved]
        elif source_file.endswith((".ts", ".tsx")):
            raw_candidates = [
                f"{resolved}.ts",
                f"{resolved}.tsx",
                f"{resolved}.js",
                f"{resolved}.jsx",
                f"{resolved}.json",
                f"{resolved}/index.ts",
                f"{resolved}/index.tsx",
                f"{resolved}/index.js",
            ]
        elif source_file.endswith((".js", ".jsx", ".mjs", ".cjs")):
            raw_candidates = [
                f"{resolved}.js",
                f"{resolved}.jsx",
                f"{resolved}.ts",
                f"{resolved}.tsx",
                f"{resolved}.json",
                f"{resolved}/index.js",
                f"{resolved}/index.ts",
            ]
        else:
            raw_candidates = [resolved]

        candidates: list[str] = []
        seen: set[str] = set()
        for path in raw_candidates:
            normalized = path.replace("\\", "/")
            if normalized in seen or not self._is_concrete_target_file_path(normalized):
                continue
            if self._resolve_workspace_file_path(normalized) is None:
                continue
            seen.add(normalized)
            candidates.append(normalized)
        return candidates

    @staticmethod
    def _path_under_scope(path: str, scope: str) -> bool:
        normalized_path = path.strip().replace("\\", "/").strip("/")
        normalized_scope = scope.strip().replace("\\", "/").strip("/")
        if not normalized_path or not normalized_scope:
            return False
        if "." in os.path.basename(normalized_scope):
            normalized_scope = os.path.dirname(normalized_scope).replace("\\", "/").strip("/")
        return normalized_path == normalized_scope or normalized_path.startswith(f"{normalized_scope}/")

    def _repair_path_allowed(self, task: Task, path: str) -> bool:
        normalized = str(path or "").strip().replace("\\", "/")
        if not normalized or self._resolve_workspace_file_path(normalized) is None:
            return False
        target_files = set(self._normalize_target_files(task))
        if normalized in target_files:
            return True
        return any(self._path_under_scope(normalized, scope) for scope in self._normalize_scope_paths(task))

    def _unresolved_import_repair_records(self, task: Task) -> list[dict[str, Any]]:
        feedback = self._verification_feedback(task)
        unresolved_raw = feedback.get("unresolved_imports")
        if not isinstance(unresolved_raw, list):
            return []
        records: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for raw_entry in unresolved_raw:
            parsed = self._parse_unresolved_import_entry(raw_entry)
            if parsed is None or parsed in seen:
                continue
            seen.add(parsed)
            source_file, import_ref = parsed
            if not self._repair_path_allowed(task, source_file):
                continue
            candidates = [
                candidate
                for candidate in self._candidate_paths_for_unresolved_import(source_file, import_ref)
                if self._repair_path_allowed(task, candidate)
            ]
            if not candidates:
                continue
            records.append(
                {
                    "source_file": source_file,
                    "import_ref": import_ref,
                    "candidate_files": candidates[:3],
                }
            )
        return records

    def _verification_repair_target_paths(self, task: Task) -> list[str]:
        records = self._unresolved_import_repair_records(task)
        paths: list[str] = []
        seen: set[str] = set()
        for record in records:
            record_paths = [
                str(record.get("source_file") or ""),
                *[str(path) for path in list(record.get("candidate_files") or [])[:1]],
            ]
            for path in record_paths:
                normalized = path.strip().replace("\\", "/")
                if normalized and normalized not in seen and self._repair_path_allowed(task, normalized):
                    seen.add(normalized)
                    paths.append(normalized)
        return paths

    def _verification_repair_prompt_section(self, task: Task) -> str:
        records = self._unresolved_import_repair_records(task)
        if not records:
            return "- No previous verification failure was provided."
        lines = [
            "- Previous verification failed with unresolved relative imports.",
            "- Resolve each issue by creating the listed candidate file or changing the source import to an existing local module.",
        ]
        for record in records[:8]:
            candidates = ", ".join(str(path) for path in list(record.get("candidate_files") or [])[:3])
            lines.append(
                f"- {record.get('source_file')} imports {record.get('import_ref')}; allowed repair candidate(s): {candidates}"
            )
        return "\n".join(lines)

    def _bootstrap_target_file_content(self, *, path: str, task: Task, language: str) -> typing.NoReturn:
        """Blocked: bootstrap target files must be produced by runtime codegen."""
        _ = (path, task, language)
        self._raise_code_writing_forbidden("_bootstrap_target_file_content")

    def _construction_file_plans(self, task: Task) -> list[dict]:
        """Get construction file plans from task metadata."""
        metadata = task.metadata if isinstance(task.metadata, dict) else {}
        plan = metadata.get("construction_plan", {})
        if isinstance(plan, dict):
            files = plan.get("files", [])
            if isinstance(files, list):
                return files
        return []

    def _extract_architecture_context(self, task: Task) -> dict[str, Any]:
        """Extract architecture context from task metadata."""
        metadata = task.metadata if isinstance(task.metadata, dict) else {}
        return metadata.get("architecture_context", {})

    def _get_module_for_task(self, task: Task) -> str:
        """Get current module name for task."""
        metadata = task.metadata if isinstance(task.metadata, dict) else {}
        return metadata.get("current_module", "unknown")

    def _build_code_generation_rounds(self, task: Task) -> list[list[dict]]:
        """Build code generation rounds from task metadata."""
        repair_paths = self._verification_repair_target_paths(task)
        if repair_paths:
            return [[{"path": path, "repair": True} for path in repair_paths]]

        metadata = task.metadata if isinstance(task.metadata, dict) else {}
        plan = metadata.get("construction_plan", {})

        # Check for rounds in construction plan
        if isinstance(plan, dict):
            rounds = plan.get("rounds", [])
            if rounds:
                normalized_rounds: list[list[dict]] = []
                for round_items in rounds:
                    if not isinstance(round_items, list):
                        continue
                    normalized_items = [
                        item
                        for item in round_items
                        if isinstance(item, dict) and self._is_concrete_target_file_path(str(item.get("path") or ""))
                    ]
                    if normalized_items:
                        normalized_rounds.append(normalized_items)
                return normalized_rounds or [[]]

        # Check for file_plans (legacy format)
        if isinstance(plan, dict):
            file_plans = plan.get("file_plans", [])
            if file_plans:
                normalized_plans = [
                    item
                    for item in file_plans
                    if isinstance(item, dict) and self._is_concrete_target_file_path(str(item.get("path") or ""))
                ]
                if not normalized_plans:
                    return [[]]
                chunk_size = self._effective_code_generation_round_chunk_size(normalized_plans)
                if chunk_size > 0:
                    return [normalized_plans[i : i + chunk_size] for i in range(0, len(normalized_plans), chunk_size)]
                return [normalized_plans]  # Single round with all files

        # Default: single round with all target files
        files = self._normalize_target_files(task)
        if files:
            file_plans = [{"path": f} for f in files]
            chunk_size = self._effective_code_generation_round_chunk_size(file_plans)
            if chunk_size > 0:
                return [file_plans[i : i + chunk_size] for i in range(0, len(file_plans), chunk_size)]
            return [file_plans]
        return [[]]

    @staticmethod
    def _resolve_code_generation_round_chunk_size() -> int:
        """Resolve file count per code-generation round."""
        raw = (
            os.environ.get("KERNELONE_DIRECTOR_TARGET_FILE_CHUNK")
            or os.environ.get("KERNELONE_CE_ROUND_FILE_CHUNK")
            or "2"
        )
        try:
            value = int(raw)
        except (TypeError, ValueError):
            value = 0
        if value <= 0:
            return 0
        return min(value, 8)

    @staticmethod
    def _code_generation_round_chunk_configured() -> bool:
        """Return whether a caller explicitly configured target-file chunking."""
        return bool(
            os.environ.get("KERNELONE_DIRECTOR_TARGET_FILE_CHUNK") or os.environ.get("KERNELONE_CE_ROUND_FILE_CHUNK")
        )

    def _effective_code_generation_round_chunk_size(self, file_plans: list[dict]) -> int:
        """Return the chunk size for this concrete file set."""
        _ = file_plans
        chunk_size = self._resolve_code_generation_round_chunk_size()
        if chunk_size <= 0:
            return 0
        return chunk_size

    @staticmethod
    def _is_test_like_target_file(path: str) -> bool:
        """Return whether a target path is likely to produce expensive test/spec output."""
        normalized = str(path or "").strip().replace("\\", "/").lower()
        if not normalized:
            return False
        return (
            "/test/" in f"/{normalized}"
            or "/tests/" in f"/{normalized}"
            or normalized.endswith(".test.ts")
            or normalized.endswith(".test.tsx")
            or normalized.endswith(".test.js")
            or normalized.endswith(".spec.ts")
            or normalized.endswith(".spec.tsx")
            or normalized.endswith(".spec.js")
        )

    def _generated_artifact_quality_error(self, files_created: list[dict], *, phase: str) -> str | None:
        """Return a fail-closed quality error for generated artifact receipts."""
        relative_paths = self._generated_artifact_paths(files_created)
        if not relative_paths:
            return f"{phase} quality gate failed: no changed files to evaluate"

        missing_paths: list[str] = []
        for path in relative_paths:
            full_path = self._resolve_workspace_file_path(path)
            if full_path is None or not os.path.isfile(full_path):
                missing_paths.append(path)
        if missing_paths:
            return f"{phase} quality gate failed: changed file receipts missing on disk: {', '.join(missing_paths[:6])}"

        errors = scan_workspace_artifact_quality(self.workspace, relative_paths=relative_paths)
        if errors:
            return f"{phase} quality gate failed: {'; '.join(errors[:6])}"
        return None

    @staticmethod
    def _generated_artifact_paths(files_created: list[dict]) -> list[str]:
        """Extract stable workspace-relative paths from generated file receipts."""
        paths: list[str] = []
        seen: set[str] = set()
        for item in files_created:
            if not isinstance(item, dict) or bool(item.get("deleted")):
                continue
            raw_path = item.get("path") or item.get("file")
            path = str(raw_path or "").strip().replace("\\", "/").lstrip("/")
            if not path or path in seen:
                continue
            seen.add(path)
            paths.append(path)
        return paths

    def _resolve_workspace_file_path(self, relative_path: str) -> str | None:
        """Resolve a workspace-relative file path without allowing traversal."""
        path = str(relative_path or "").strip()
        if not path or os.path.isabs(path):
            return None
        workspace_abs = os.path.abspath(self.workspace)
        full_path = os.path.abspath(os.path.join(workspace_abs, path))
        try:
            if os.path.commonpath([workspace_abs, full_path]) != workspace_abs:
                return None
        except ValueError:
            return None
        return full_path

    def _snapshot_workspace_files(self, paths: list[str]) -> dict[str, tuple[bool, int, int]]:
        """Capture initial target-file signatures for over-completion detection."""
        snapshot: dict[str, tuple[bool, int, int]] = {}
        for raw_path in paths:
            path = str(raw_path or "").strip()
            if not path or path in snapshot:
                continue
            full_path = self._resolve_workspace_file_path(path)
            if full_path is None:
                continue
            try:
                stat = os.stat(full_path)
            except OSError:
                snapshot[path] = (False, -1, -1)
            else:
                snapshot[path] = (True, int(stat.st_size), int(stat.st_mtime_ns))
        return snapshot

    def _round_files_changed_since(
        self,
        paths: list[str],
        initial_signatures: dict[str, tuple[bool, int, int]],
    ) -> bool:
        """Return true when every path in a round was created or changed."""
        normalized = [str(path or "").strip() for path in paths if str(path or "").strip()]
        if not normalized:
            return False
        for path in normalized:
            full_path = self._resolve_workspace_file_path(path)
            if full_path is None or not os.path.isfile(full_path):
                return False
            try:
                stat = os.stat(full_path)
            except OSError:
                return False
            current = (True, int(stat.st_size), int(stat.st_mtime_ns))
            if current == initial_signatures.get(path, (False, -1, -1)):
                return False
        return True

    def _collect_existing_file_records(self, paths: list[str]) -> list[dict[str, str]]:
        """Return lightweight records for existing workspace files."""
        records: list[dict[str, str]] = []
        seen: set[str] = set()
        for raw_path in paths:
            path = str(raw_path or "").strip()
            if not path or path in seen:
                continue
            full_path = self._resolve_workspace_file_path(path)
            if full_path is None or not os.path.isfile(full_path):
                continue
            seen.add(path)
            records.append({"path": path, "content": ""})
        return records

    def _code_generation_round_marker_path(self, task: Task, round_index: int) -> str:
        """Return the persisted progress marker path for one task round."""
        raw_task_id = str(getattr(task, "id", "") or getattr(task, "subject", "") or "task")
        safe_task_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", raw_task_id).strip("._") or "task"
        return os.path.join(
            self.workspace,
            ".polaris",
            "runtime",
            "director",
            "codegen_progress",
            safe_task_id,
            f"round-{round_index:03d}.json",
        )

    def _workspace_file_signature(self, relative_path: str) -> dict[str, int | str] | None:
        """Return a stable file signature for a workspace-relative file."""
        full_path = self._resolve_workspace_file_path(relative_path)
        if full_path is None or not os.path.isfile(full_path):
            return None
        try:
            stat = os.stat(full_path)
            with open(full_path, encoding="utf-8", errors="surrogateescape") as handle:
                content = handle.read()
        except OSError:
            return None
        digest = hashlib.sha256(content.encode("utf-8", errors="surrogateescape")).hexdigest()
        return {
            "size": int(stat.st_size),
            "mtime_ns": int(stat.st_mtime_ns),
            "sha256": digest,
        }

    def _write_code_generation_round_marker(
        self,
        task: Task,
        round_index: int,
        round_paths: list[str],
    ) -> None:
        """Persist successful round progress so process-level retries can resume."""
        normalized_paths = [str(path or "").strip() for path in round_paths if str(path or "").strip()]
        if not normalized_paths:
            return
        signatures: dict[str, dict[str, int | str]] = {}
        for path in normalized_paths:
            signature = self._workspace_file_signature(path)
            if signature is None:
                return
            signatures[path] = signature
        marker_path = self._code_generation_round_marker_path(task, round_index)
        payload = {
            "schema_version": 1,
            "task_id": str(getattr(task, "id", "") or ""),
            "round_index": round_index,
            "target_files": normalized_paths,
            "signatures": signatures,
            "timestamp_epoch": time.time(),
        }
        os.makedirs(os.path.dirname(marker_path), exist_ok=True)
        with open(marker_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")

    def _code_generation_round_marker_satisfied(
        self,
        task: Task,
        round_index: int,
        round_paths: list[str],
    ) -> bool:
        """Return whether a previous process already completed this round."""
        normalized_paths = [str(path or "").strip() for path in round_paths if str(path or "").strip()]
        if not normalized_paths:
            return False
        marker_path = self._code_generation_round_marker_path(task, round_index)
        try:
            with open(marker_path, encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return False
        if not isinstance(payload, dict) or int(payload.get("schema_version") or 0) != 1:
            return False
        target_files = payload.get("target_files")
        signatures = payload.get("signatures")
        if target_files != normalized_paths or not isinstance(signatures, dict):
            return False
        for path in normalized_paths:
            expected = signatures.get(path)
            current = self._workspace_file_signature(path)
            if not isinstance(expected, dict) or current != expected:
                return False
        return True

    def _write_files(self, files: list[dict], task_id: str = "") -> list[dict]:
        """Delegate to FileApplyService."""
        if self._file_service is None:
            return []
        return self._file_service.write_files(files, task_id)  # type: ignore[operator]

    def _collect_workspace_files(self, paths: list[str], task_id: str = "", operation: str = "modify") -> list[dict]:
        """Delegate to FileApplyService."""
        if self._file_service is None:
            return []
        return self._file_service.collect_workspace_files(paths, task_id, operation)  # type: ignore[operator]

    def _apply_response_operations(
        self,
        response: str,
        task_id: str = "",
        llm_metadata: dict[str, Any] | None = None,
        allowed_scope_paths: list[str] | tuple[str, ...] | None = None,
    ) -> tuple[list[dict], list[str]]:
        """Delegate to FileApplyService."""
        if self._file_service is None:
            return [], []
        return self._file_service.apply_response_operations(
            response,
            task_id,
            llm_metadata,  # type: ignore[arg-type]
            allowed_scope_paths=allowed_scope_paths,
        )

    def _compact_prompt_fragment(self, text: str, *, max_chars: int) -> str:
        """Compact long text for prompts."""
        payload = str(text or "").strip()
        if max_chars <= 0 or len(payload) <= max_chars:
            return payload
        return self.token_service.truncate_output(
            payload,
            max_size=max_chars,
            add_notice=True,
        )

    def _extract_functional_requirements(self, description: str) -> list[str]:
        """Extract functional requirements from description."""
        requirements: list[str] = []
        lines = str(description or "").split("\n")
        for line in lines:
            line = line.strip()
            if not line:
                continue
            # Remove common prefixes
            cleaned = re.sub(r"^[\d]+[.)]\s*", "", line)
            cleaned = re.sub(r"^[-*•]\s*", "", cleaned)
            cleaned = re.sub(
                r"^(must|should|need|require|implement|create|add)\s+",
                "",
                cleaned,
                flags=re.IGNORECASE,
            )
            if len(cleaned) > 10 and len(cleaned) < 500:
                requirements.append(cleaned)
        return requirements[:10]

    def _get_framework_guidance(self, language: str, framework: str | None) -> str:
        """Get framework-specific guidance."""
        if language != "python":
            return ""
        if framework == "fastapi":
            return """
## FastAPI Specific Requirements
- Use Pydantic models for request/response validation
- Include proper HTTP exception handling
- Add OpenAPI/Swagger documentation
- Use async/await for endpoint handlers
"""
        elif framework == "flask":
            return """
## Flask Specific Requirements
- Use Flask blueprints for route organization
- Include proper error handlers
- Add application factory pattern if appropriate
"""
        return ""

    def _build_code_generation_prompt(
        self,
        task: Task,
        *,
        round_index: int = 0,
        round_total: int = 0,
        round_files: list[Any] | None = None,
    ) -> str:
        """Build LLM prompt for code generation."""
        task_subject = self._compact_prompt_fragment(str(task.subject or ""), max_chars=280)
        task_description = self._compact_prompt_fragment(
            str(task.description or ""),
            max_chars=2600,
        )
        normalized_round_files: list[str] = []
        for raw_round_file in round_files or []:
            raw_path = raw_round_file.get("path") if isinstance(raw_round_file, dict) else raw_round_file
            path = str(raw_path or "").strip().replace("\\", "/")
            if path and self._is_concrete_target_file_path(path) and path not in normalized_round_files:
                normalized_round_files.append(path)
        target_files = normalized_round_files or self._normalize_target_files(task)
        target_text = "\n".join(f"- {path}" for path in target_files[:16]) if target_files else "- (model may decide)"
        repair_records = self._unresolved_import_repair_records(task)
        target_scope_rule = (
            "- This is a verification repair round. Edit/create only the target files listed above, "
            "including derived repair files. Do not write outside declared scopes."
            if repair_records and target_files
            else (
                "- Concrete target files are declared for this round. Edit/create only those paths; "
                "do not add unrelated files."
                if target_files
                else "- No concrete target files were declared. Choose the smallest concrete file set under the declared scopes."
            )
        )
        scope_paths = self._normalize_scope_paths(task)
        scope_text = "\n".join(f"- {path}" for path in scope_paths[:16]) if scope_paths else "- (not declared)"
        construction_hints: list[str] = []
        for plan in self._construction_file_plans(task):
            path = str(plan.get("path") or "").strip()
            if not path:
                continue
            if target_files and path not in target_files:
                continue
            steps = plan.get("implementation_steps")
            step_text = (
                "; ".join(str(item).strip() for item in steps[:2] if str(item or "").strip())
                if isinstance(steps, list)
                else ""
            )
            step_text = self._compact_prompt_fragment(step_text, max_chars=180)
            if step_text:
                construction_hints.append(f"- {path}: {step_text}")
            else:
                construction_hints.append(f"- {path}: follow ChiefEngineer file plan")
            if len(construction_hints) >= 12:
                break
        round_text = ""
        if round_index > 0 and round_total > 0:
            round_text = f"\nBuild Round: {round_index}/{round_total}\n"

        # Extract architecture context
        arch_context = self._extract_architecture_context(task)
        current_module = self._get_module_for_task(task)

        # Build architecture hints
        arch_hints: list[str] = []
        module_order = arch_context.get("module_order", [])
        if module_order:
            arch_hints.append(f"模块构建顺序（底层优先）: {' -> '.join(module_order[:6])}")
            if len(module_order) > 6:
                arch_hints.append(f"  ... 及其他 {len(module_order) - 6} 个模块")

        module_arch = arch_context.get("module_arch", {})
        if current_module in module_arch:
            arch = module_arch[current_module]
            layer = arch.get("layer", 0)
            deps = arch.get("dependencies", [])
            stability = arch.get("stability_score", 0)
            arch_hints.append(f"当前模块: '{current_module}' (层级 L{layer})")
            if deps:
                arch_hints.append(f"  依赖模块: {', '.join(deps[:6])}")
            if stability > 0.3:
                arch_hints.append(f"  注意: 此模块被多个其他模块依赖（稳定性 {stability:.0%}），请设计稳定接口")

        constraints = arch_context.get("constraints", [])
        violation_constraints = [c for c in constraints if c.startswith(("❌", "⚠️"))]
        for vc in violation_constraints[:2]:
            arch_hints.append(f"架构警告: {vc}")

        arch_section = "\n".join(f"- {h}" for h in arch_hints) if arch_hints else "- 无全局架构上下文"

        prompt = f"""You are a software developer implementing a task.

Task: {task_subject}
Description: {task_description}
{round_text}
Target files for this execution:
{target_text}

Declared directory/module scopes:
{scope_text}

=== Architecture Context (全局架构上下文) ===
{arch_section}

=== ChiefEngineer Blueprint Hints ===
{chr(10).join(construction_hints) if construction_hints else "- no explicit file hints"}

=== Verification Repair Context ===
{self._verification_repair_prompt_section(task)}

IMPORTANT ARCHITECTURE GUIDELINES:
1. 遵循模块层级: 底层模块（L0）提供基础设施，上层模块（L1+）依赖底层
2. 保持接口稳定: 如果被依赖的模块（高稳定性），优先设计清晰的接口
3. 避免循环依赖: 不要让你的模块依赖关系形成环路
4. 按模块构建顺序施工: 优先实现底层模块，再实现依赖它们的上层模块
5. 架构演进: 如果模块健康状况不佳，优先重构而不是添加新功能
6. 模块规划: 如果任务涉及计划中的新模块，请按ADR(架构决策记录)的指引实现
7. 接口优先: 对于稳定模块，先定义清晰的公共接口，再实现内部逻辑

Please generate the code to implement this task.

Output contract:
{target_scope_rule}
- Return only `PATCH_FILE:` blocks or fenced ` ```file: path` file sections.
- Do not output `Command:`, shell commands, terminal logs, status narration, SESSION_PATCH, or tool-call wrappers.
- If target files are not concrete, choose concrete workspace-relative file paths under the declared scopes and create those files.

For each file you create, provide:
1. Prefer PATCH format for incremental edits on existing files:
   PATCH_FILE: path/to/file.py
   <<<<<<< SEARCH
   <old snippet>
   =======
   <new snippet>
   >>>>>>> REPLACE
2. For new files, use full-file block:
   FILE: path/to/file.py
   <complete content>
   END FILE
3. For deletions, use:
   DELETE_FILE: path/to/obsolete.py

Format your response as:

```file: path/to/file.py
<file content here>
```

```file: another/file.py
<content>
```

Requirements:
- Write complete, working code
- Include all necessary imports
- Add appropriate comments and docstrings
- Follow best practices for the target language/framework
- Ensure the code is syntactically correct
- Prioritize minimal patch edits over full-file rewrites when file already exists
- Prefer editing/creating the listed target files before creating new files
- Keep this generation response focused on source-file changes. Skip package
  installation, build/test commands, and dev-server startup; Polaris runs
  verification after file application.
"""

        raw_max_chars = os.environ.get("KERNELONE_WORKER_PROMPT_MAX_CHARS", "9000")
        try:
            max_chars = int(raw_max_chars)
        except ValueError:
            max_chars = 9000
        max_chars = min(max(max_chars, 2000), 40000)
        return self._compact_prompt_fragment(prompt, max_chars=max_chars)

    def _extract_files_from_response(self, response: str) -> list[dict]:
        """Extract file paths and contents from LLM response."""
        files: list[dict] = []
        seen: set[str] = set()

        def _append_file(path: str, content: str) -> None:
            normalized_path = str(path or "").strip().strip("`")
            normalized_content = str(content or "").strip()
            if not normalized_path or not normalized_content:
                return
            if normalized_path in seen:
                return
            seen.add(normalized_path)
            files.append({"path": normalized_path, "content": normalized_content})

        # Pattern 1: explicit file fences
        for file_path, content in re.findall(r"```file:\s*(.+?)\n(.*?)```", response, re.DOTALL):
            _append_file(file_path, content)

        # Pattern 2: heading + code block
        for file_path, content in re.findall(
            r"(?:^|\n)(?:###|##|#)\s*`?([A-Za-z0-9_./\\-]+\.[A-Za-z0-9_]+)`?\s*\n```[^\n]*\n(.*?)```",
            response,
            re.DOTALL,
        ):
            _append_file(file_path, content)

        # Pattern 3: File: path followed by fenced code block
        for file_path, content in re.findall(
            r"(?:^|\n)(?:File|Path)\s*:\s*([A-Za-z0-9_./\\-]+\.[A-Za-z0-9_]+)\s*\n```[^\n]*\n(.*?)```",
            response,
            re.DOTALL,
        ):
            _append_file(file_path, content)

        return files

    def _fallback_code_files(self, task: Task, round_files: list[str] | None = None) -> list[dict]:
        """Legacy entry point retained only to fail closed."""
        _ = (task, round_files)
        self._raise_code_writing_forbidden("_fallback_code_files")

    def _deterministic_repair_files(self, task: Task, round_files: list[str] | None = None) -> list[dict]:
        """Legacy entry point retained only to fail closed."""
        _ = (task, round_files)
        self._raise_code_writing_forbidden("_deterministic_repair_files")

    def _invoke_generation_response(
        self,
        *,
        prompt: str,
        model: str,
        timeout: int,
        deadline_ts: float,
        round_label: str,
    ) -> typing.NoReturn:
        """Legacy direct-generation entry point retained only to fail closed."""
        _ = (prompt, model, timeout, deadline_ts, round_label)
        self._raise_code_writing_forbidden("_invoke_generation_response")

    # === Legacy Method Names ===
    # These names are retained only to fail closed and expose policy violations.

    def _fallback_code_content(self, path: str, language: str, task: Any) -> str:
        """Legacy entry point retained only to fail closed."""
        _ = (path, language, task)
        self._raise_code_writing_forbidden("_fallback_code_content")

    def _deterministic_repair_enabled(self) -> bool:
        """Deterministic repair is forbidden under the no-code-writing policy."""
        return False

    def _register_spin_guard(
        self,
        tracker: dict[str, dict[str, Any]],
        *,
        scope: str,
        prompt: str,
        output: str,
    ) -> None:
        """Register spin guard to detect repeated prompt+output.

        Delegates to code_generation_engine.
        """
        if self._code_engine is not None:
            self._code_engine.register_spin_guard(
                tracker,
                scope=scope,
                prompt=prompt,
                output=output,
            )

    def _invoke_ollama(
        self,
        *,
        prompt: str,
        model: str,
        timeout: int,
    ) -> typing.NoReturn:
        """Legacy Ollama entry point retained only to fail closed."""
        _ = (prompt, model, timeout)
        self._raise_code_writing_forbidden("_invoke_ollama")

    def _invoke_generation_with_retries(
        self,
        *,
        task: Any,
        prompt: str,
        model: str,
        per_call_timeout: int,
        deadline_ts: float,
        round_label: str,
        round_files: list[str] | None,
        spin_tracker: dict[str, dict[str, Any]],
    ) -> tuple[list[dict], list[str]]:
        """Invoke LLM generation with retries.

        Delegates to code_generation_engine.
        """
        if self._code_engine is None:
            return [], []
        import asyncio

        return asyncio.run(
            self._code_engine.invoke_generation_with_retries(
                task=task,
                prompt=prompt,
                model=model,
                per_call_timeout=per_call_timeout,
                deadline_ts=deadline_ts,
                round_label=round_label,
                round_files=round_files,
                spin_tracker=spin_tracker,
            )
        )
