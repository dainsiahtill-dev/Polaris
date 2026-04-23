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
import logging
import os
import re
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

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------
# Phase 4 dependency deferral: CodeGenerationEngine + FileApplyService
# These imports are deferred because they belong to director.runtime (Phase 4).
# When director.runtime is migrated, update these to direct imports.
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

    # Phase 4 target location (canonical after director.runtime migration)
    with contextlib.suppress(ImportError):
        _CGE = _importlib.import_module("polaris.cells.director.tasking.internal.code_generation_engine")

    with contextlib.suppress(ImportError):
        _FAS = _importlib.import_module("polaris.cells.director.tasking.internal.file_apply_service")

    # Fall back to execution/internal.code_generation_engine directly.
    # Direct module path avoids triggering execution/internal/__init__ which
    # would create a circular import during initial load.
    if _CGE is None:
        with contextlib.suppress(ImportError):
            _CGE = _importlib.import_module("polaris.cells.director.execution.internal.code_generation_engine")

    if _FAS is None:
        with contextlib.suppress(ImportError):
            _FAS = _importlib.import_module("polaris.cells.director.execution.internal.file_apply_service")

    # Placeholder only if Phase 4 is completely unavailable
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
                evidence=evidence_list,
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
                evidence=[],
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
        per_call_timeout = self._code_engine.resolve_llm_timeout(
            task.timeout_seconds  # type: ignore[arg-type]
        )

        # Build code generation rounds
        rounds = self._build_code_generation_rounds(task)
        total_rounds = len(rounds) if rounds else 1
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

        return CodeGenerationResult(
            success=len(files_created) > 0,
            files_created=files_created,
            output=f"Generated {len(files_created)} files",
        )

    async def _execute_file_creation(self, task: Task) -> CodeGenerationResult:
        """Execute file creation task."""
        if self._file_service is None:
            return CodeGenerationResult(
                success=False,
                error="FileApplyService not available (Phase 4 pending)",
                output="WorkerExecutor: file service not initialised",
            )
        # For file creation, extract target files from task and create them
        target_files = self._normalize_target_files(task)
        files_created: list[dict] = []

        for file_path in target_files[:10]:
            content = f"# Created by Polaris: {task.subject}\n# {task.description or ''}\n"
            files_created.append({"path": file_path, "content": content})

        written = self._file_service.write_files(files_created)  # type: ignore[operator]

        return CodeGenerationResult(
            success=len(written) > 0,
            files_created=written,
            output=f"Created {len(written)} files",
        )

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

        return CodeGenerationResult(
            success=len(files_created) > 0,
            files_created=files_created,
            output=f"Bootstrapped {language} project with {len(files_created)} files",
        )

    async def _execute_generic(self, task: Task) -> CodeGenerationResult:
        """Execute generic task - delegate to code generation."""
        return await self._execute_code_generation(task)

    # === Helper Methods ===

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
                if path and path not in seen:
                    seen.add(path)
                    files.append(path)

        # From file_plan
        file_plan = metadata.get("file_plan", [])
        if isinstance(file_plan, list):
            for item in file_plan:
                if isinstance(item, dict):
                    path = str(item.get("path") or "").strip()
                    if path and path not in seen:
                        seen.add(path)
                        files.append(path)

        # Fallback: extract from description
        if not files and task.description:
            for line in task.description.split("\n"):
                line = line.strip()
                if self._is_probable_file_path(line) and line not in seen:
                    seen.add(line)
                    files.append(line)

        return files

    def _is_probable_file_path(self, path: str) -> bool:
        """Check if string looks like a file path."""
        if not path:
            return False
        # Must have extension or look like a path
        has_ext = "." in path and len(path.rsplit(".", maxsplit=1)[-1]) <= 5
        has_slash = "/" in path or "\\" in path
        return has_ext or has_slash

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
        metadata = task.metadata if isinstance(task.metadata, dict) else {}
        plan = metadata.get("construction_plan", {})

        # Check for rounds in construction plan
        if isinstance(plan, dict):
            rounds = plan.get("rounds", [])
            if rounds:
                return rounds

        # Check for file_plans (legacy format)
        if isinstance(plan, dict):
            file_plans = plan.get("file_plans", [])
            if file_plans:
                # Check for chunking environment variable
                chunk_size_str = os.environ.get("KERNELONE_CE_ROUND_FILE_CHUNK", "")
                if chunk_size_str:
                    try:
                        chunk_size = int(chunk_size_str)
                        if chunk_size > 0:
                            # Split file_plans into chunks
                            chunks = []
                            for i in range(0, len(file_plans), chunk_size):
                                chunks.append(file_plans[i : i + chunk_size])
                            return chunks
                    except ValueError:
                        pass
                return [file_plans]  # Single round with all files

        # Default: single round with all target files
        files = self._normalize_target_files(task)
        if files:
            return [[{"path": f} for f in files]]
        return []

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
    ) -> tuple[list[dict], list[str]]:
        """Delegate to FileApplyService."""
        if self._file_service is None:
            return [], []
        return self._file_service.apply_response_operations(
            response,
            task_id,
            llm_metadata,  # type: ignore[arg-type]
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
        round_files: list[str] | None = None,
    ) -> str:
        """Build LLM prompt for code generation."""
        task_subject = self._compact_prompt_fragment(str(task.subject or ""), max_chars=280)
        task_description = self._compact_prompt_fragment(
            str(task.description or ""),
            max_chars=2600,
        )
        target_files = round_files or self._normalize_target_files(task)
        target_text = "\n".join(f"- {path}" for path in target_files[:16]) if target_files else "- (model may decide)"
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

=== Architecture Context (全局架构上下文) ===
{arch_section}

=== ChiefEngineer Blueprint Hints ===
{chr(10).join(construction_hints) if construction_hints else "- no explicit file hints"}

IMPORTANT ARCHITECTURE GUIDELINES:
1. 遵循模块层级: 底层模块（L0）提供基础设施，上层模块（L1+）依赖底层
2. 保持接口稳定: 如果被依赖的模块（高稳定性），优先设计清晰的接口
3. 避免循环依赖: 不要让你的模块依赖关系形成环路
4. 按模块构建顺序施工: 优先实现底层模块，再实现依赖它们的上层模块
5. 架构演进: 如果模块健康状况不佳，优先重构而不是添加新功能
6. 模块规划: 如果任务涉及计划中的新模块，请按ADR(架构决策记录)的指引实现
7. 接口优先: 对于稳定模块，先定义清晰的公共接口，再实现内部逻辑

Please generate the code to implement this task.

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
    ) -> tuple[str, list[str], list[str], dict[str, Any]]:
        """Invoke LLM for generation response."""
        if self._code_engine is None:
            return "", [], [], {}
        # This delegates to the code generation engine
        output, metadata = self._code_engine.invoke_ollama(
            prompt=prompt,
            model=model,
            timeout=timeout,
        )

        # Extract tool calls if any (simplified - full implementation in code_generation_engine)
        tool_changed_files: list[str] = []
        tool_warnings: list[str] = []

        return output, tool_changed_files, tool_warnings, metadata

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
    ) -> dict:
        """Invoke Ollama for LLM generation.

        Delegates to code_generation_engine.
        """
        if self._code_engine is None:
            return {"output": "", "metadata": {}}
        output, metadata = self._code_engine.invoke_ollama(
            prompt=prompt,
            model=model,
            timeout=timeout,
        )
        return {"output": output, **metadata}

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
