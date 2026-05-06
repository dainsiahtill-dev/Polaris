"""Phase executor for Director v2 state machine.

Executes the 4-phase workflow with:
- Policy validation gates
- Snapshot/rollback support
- Self-check verification
- Stall detection
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import logging
import os
import shutil
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from polaris.kernelone.process.command_executor import CommandExecutionService

from ..entities.capability import CapabilityChecker, Role, validate_director_action
from .task_phase import PhaseContext, PhaseResult, TaskPhase

if TYPE_CHECKING:
    from collections.abc import Callable

    from ..entities.policy import Policy

logger = logging.getLogger(__name__)


@dataclass
class SnapshotInfo:
    """Information about a snapshot."""

    path: str
    created_at: datetime
    files: list[str]


class PhaseExecutor:
    """Executes phases with governance and safety mechanisms."""

    def __init__(
        self,
        workspace: str,
        policy: Policy,
        role: Role = Role.DIRECTOR,
        snapshot_enabled: bool = True,
    ) -> None:
        self.workspace = workspace
        self.policy = policy
        self.role = role
        self.snapshot_enabled = snapshot_enabled
        self.snapshots: dict[str, SnapshotInfo] = {}
        self._capability_checker = CapabilityChecker(
            role_config=self._get_role_config(),
            policy=self.policy.to_dict(),
        )

    def _get_role_config(self):
        """Get role configuration."""
        from ..entities.capability import get_role_config

        return get_role_config(self.role, self.policy.to_dict())

    def execute_phase(
        self,
        phase: TaskPhase,
        context: PhaseContext,
        planning_fn: Callable[[PhaseContext], PhaseResult] | None = None,
        execution_fn: Callable[[PhaseContext], PhaseResult] | None = None,
    ) -> PhaseResult:
        """Execute a specific phase.

        Args:
            phase: Phase to execute
            context: Phase context
            planning_fn: Custom planning logic
            execution_fn: Custom execution logic

        Returns:
            Phase execution result
        """
        phase_handlers = {
            TaskPhase.PLANNING: self._execute_planning,
            TaskPhase.VALIDATION: self._execute_validation,
            TaskPhase.EXECUTION: lambda ctx: self._execute_execution(ctx, execution_fn),
            TaskPhase.VERIFICATION: self._execute_verification,
        }

        handler = phase_handlers.get(phase)
        if handler is None:
            return PhaseResult(
                success=False,
                phase=phase,
                message=f"No handler for phase {phase}",
                error_code="NO_HANDLER",
            )

        try:
            if phase == TaskPhase.PLANNING and planning_fn:
                return planning_fn(context)
            return handler(context)
        except (RuntimeError, ValueError) as e:
            logger.exception("execute_phase failed: phase=%s", phase)
            return PhaseResult(
                success=False,
                phase=phase,
                message=f"Phase execution failed: {e!s}",
                error_code="EXECUTION_ERROR",
                can_retry=True,
            )

    def _execute_planning(self, context: PhaseContext) -> PhaseResult:
        """Execute planning phase.

        Merged from original:
        - hp_start_run: Define goals and acceptance criteria
        - hp_create_blueprint: Create implementation plan
        """
        # Capability check
        result = validate_director_action("read", [context.workspace], self.policy.to_dict())
        if not result.allowed:
            return PhaseResult(
                success=False,
                phase=TaskPhase.PLANNING,
                message=f"Planning blocked: {result.reason}",
                error_code="CAPABILITY_DENIED",
            )

        # Load and validate plan
        from polaris.kernelone._runtime_config import get_workspace_metadata_dir_name

        plan_path = os.path.join(context.workspace, get_workspace_metadata_dir_name(), "plan.md")
        if os.path.exists(plan_path):
            with open(plan_path, encoding="utf-8") as f:
                context.plan = f.read()

        if not context.plan:
            return PhaseResult(
                success=False,
                phase=TaskPhase.PLANNING,
                message="No plan found",
                error_code="MISSING_PLAN",
            )

        return PhaseResult(
            success=True,
            phase=TaskPhase.PLANNING,
            message="Planning completed",
            next_phase=TaskPhase.VALIDATION,
        )

    def _execute_validation(self, context: PhaseContext) -> PhaseResult:
        """Execute validation phase (policy compliance check).

        This is the critical governance gate.
        """
        # Check budget limits
        if context.build_round >= self.policy.build_loop.budget:
            return PhaseResult(
                success=False,
                phase=TaskPhase.VALIDATION,
                message=f"Build round budget exceeded: {context.build_round}/{self.policy.build_loop.budget}",
                error_code="BUDGET_EXCEEDED",
                should_rollback=True,
            )

        # Check line count budget
        # (Would need to track actual lines read)

        # Create snapshot before execution (if enabled)
        if self.snapshot_enabled and not context.snapshot_path:
            snapshot = self._create_snapshot(context)
            if snapshot:
                context.snapshot_path = snapshot.path

        return PhaseResult(
            success=True,
            phase=TaskPhase.VALIDATION,
            message="Policy validation passed",
            context_updates={"snapshot_path": context.snapshot_path},
            next_phase=TaskPhase.EXECUTION,
        )

    def _execute_execution(
        self,
        context: PhaseContext,
        execution_fn: Callable[[PhaseContext], PhaseResult] | None = None,
    ) -> PhaseResult:
        """Execute implementation phase."""
        # Capability check for write
        write_scope = context.metadata.get("write_scope", [context.workspace])
        result = validate_director_action("write", write_scope, self.policy.to_dict())

        if not result.allowed:
            return PhaseResult(
                success=False,
                phase=TaskPhase.EXECUTION,
                message=f"Execution blocked: {result.reason}",
                error_code="CAPABILITY_DENIED",
            )

        # Use custom execution function if provided
        if execution_fn:
            return execution_fn(context)

        # Default: mark as completed (actual work done by worker)
        return PhaseResult(
            success=True,
            phase=TaskPhase.EXECUTION,
            message="Execution authorized",
            next_phase=TaskPhase.VERIFICATION,
        )

    def _execute_verification(self, context: PhaseContext) -> PhaseResult:
        """Execute verification phase (self-check).

        Director's responsibility: Ensure code compiles/runs
        - Type checking
        - Syntax validation
        - Basic linting

        NOT: Feature completeness (that's QA's job)
        """
        errors = []
        missing_targets = []
        unresolved_imports = []

        # Check for missing target files
        for target in context.metadata.get("target_files", []):
            target_path = os.path.join(context.workspace, target)
            if not os.path.exists(target_path):
                missing_targets.append(target)

        # Detect unresolved imports (simple heuristic)
        unresolved_imports = self._detect_unresolved_imports(context)

        # Run syntax/type checks based on project type
        check_results = self._run_project_checks(context)
        errors.extend(check_results.get("errors", []))

        # Determine if we should retry - explicit bool conversion for mypy
        has_issues: bool = bool(missing_targets or unresolved_imports or errors)
        can_retry: bool = (
            has_issues and context.build_round < context.max_build_rounds and self.policy.repair.auto_repair
        )

        if has_issues:
            context.build_round += 1
            return PhaseResult(
                success=False,
                phase=TaskPhase.VERIFICATION,
                message=f"Verification failed: {len(errors)} errors, "
                f"{len(missing_targets)} missing, "
                f"{len(unresolved_imports)} unresolved imports",
                error_code="VERIFICATION_FAILED",
                can_retry=can_retry,
                context_updates={
                    "build_round": context.build_round,
                    "missing_targets": missing_targets,
                    "unresolved_imports": unresolved_imports,
                },
                next_phase=TaskPhase.EXECUTION if can_retry else None,
            )

        return PhaseResult(
            success=True,
            phase=TaskPhase.VERIFICATION,
            message="Self-check passed",
            next_phase=TaskPhase.COMPLETED,
        )

    def _create_snapshot(self, context: PhaseContext) -> SnapshotInfo | None:
        """Create snapshot of current state before execution."""
        from polaris.kernelone._runtime_config import get_workspace_metadata_dir_name

        snapshot_id = f"{context.task_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        snapshot_dir = os.path.join(self.workspace, get_workspace_metadata_dir_name(), "snapshots", snapshot_id)

        try:
            os.makedirs(snapshot_dir, exist_ok=True)

            # Copy target files
            files_to_snapshot = context.metadata.get("target_files", [])
            if not files_to_snapshot:
                # Snapshot entire workspace (respecting .gitignore)
                files_to_snapshot = self._list_workspace_files()

            snapshotted = []
            for file_path in files_to_snapshot:
                src = os.path.join(self.workspace, file_path)
                if os.path.exists(src):
                    dst = os.path.join(snapshot_dir, file_path)
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.copy2(src, dst)
                    snapshotted.append(file_path)

            info = SnapshotInfo(
                path=snapshot_dir,
                created_at=datetime.now(),
                files=snapshotted,
            )
            self.snapshots[context.task_id] = info
            return info

        except (RuntimeError, ValueError):
            logger.exception("snapshot failed: task_id=%s", context.task_id)
            return None

    def rollback(self, context: PhaseContext) -> bool:
        """Rollback to snapshot."""
        if not context.snapshot_path or not os.path.exists(context.snapshot_path):
            return False

        try:
            for file_path in self.snapshots.get(context.task_id, SnapshotInfo("", datetime.now(), [])).files:
                src = os.path.join(context.snapshot_path, file_path)
                dst = os.path.join(self.workspace, file_path)
                if os.path.exists(src):
                    shutil.copy2(src, dst)
            return True
        except (RuntimeError, ValueError):
            logger.exception("rollback failed: task_id=%s", context.task_id)
            return False

    def _detect_unresolved_imports(self, context: PhaseContext) -> list[str]:
        """Detect unresolved imports in changed files."""
        unresolved = []

        for file_path in context.changed_files:
            full_path = os.path.join(self.workspace, file_path)
            if not os.path.exists(full_path):
                continue

            try:
                with open(full_path, encoding="utf-8") as f:
                    content = f.read()

                # Simple heuristic for different languages
                if file_path.endswith(".py"):
                    unresolved.extend(self._check_python_imports(content, file_path))
                elif file_path.endswith((".js", ".ts", ".tsx")):
                    unresolved.extend(self._check_js_imports(content, file_path))

            except (RuntimeError, ValueError):
                logger.exception("_detect_unresolved_imports: skipped file=%s", file_path)
                continue

        return unresolved

    def _check_python_imports(self, content: str, file_path: str) -> list[str]:
        """Check for unresolved Python imports."""
        import re

        unresolved = []
        # Match 'import X' or 'from X import Y'
        import_pattern = r"^(?:from|import)\s+([\w.]+)"

        for line in content.split("\n"):
            match = re.match(import_pattern, line.strip())
            if match:
                module = match.group(1)
                if not self._module_exists(module):
                    unresolved.append(f"{file_path}: {module}")

        return unresolved

    def _check_js_imports(self, content: str, file_path: str) -> list[str]:
        """Check for unresolved JS/TS imports."""
        import re

        unresolved = []
        # Match 'import X from "./path"' or 'import("./path")'
        import_pattern = r'import\s+.*?\s+from\s+["\']([\./][^"\']+)["\']'

        for match in re.finditer(import_pattern, content):
            import_path = match.group(1)
            # Resolve relative path
            if import_path.startswith("."):
                base_dir = os.path.dirname(file_path)
                resolved = os.path.normpath(os.path.join(base_dir, import_path))

                # Check with extensions
                extensions = ["", ".js", ".ts", ".tsx", ".json", "/index.js", "/index.ts"]
                exists = any(os.path.exists(os.path.join(self.workspace, resolved + ext)) for ext in extensions)

                if not exists:
                    unresolved.append(f"{file_path}: {import_path}")

        return unresolved

    def _module_exists(self, module: str) -> bool:
        """Check if a Python module exists in stdlib, site-packages, or workspace."""

        token = str(module or "").strip()
        if not token:
            return False

        standard_libs = {
            "os",
            "sys",
            "json",
            "re",
            "time",
            "datetime",
            "pathlib",
            "typing",
            "collections",
            "itertools",
            "functools",
            "math",
            "random",
            "hashlib",
        }
        if token in standard_libs or token.startswith("."):
            return True

        module_path = token.replace(".", os.sep)
        local_candidates = (
            os.path.join(self.workspace, f"{module_path}.py"),
            os.path.join(self.workspace, module_path, "__init__.py"),
        )
        if any(os.path.exists(path) for path in local_candidates):
            return True

        try:
            return importlib.util.find_spec(token) is not None
        except (RuntimeError, ValueError):
            return False

    def _run_project_checks(self, context: PhaseContext) -> dict[str, Any]:
        """Run project-specific checks (type/syntax)."""
        errors = []

        # Detect project type and run appropriate checks
        if os.path.exists(os.path.join(self.workspace, "package.json")):
            errors.extend(self._run_node_checks(context))
        elif os.path.exists(os.path.join(self.workspace, "requirements.txt")) or os.path.exists(
            os.path.join(self.workspace, "pyproject.toml")
        ):
            errors.extend(self._run_python_checks(context))

        return {"errors": errors}

    def _read_package_json(self) -> dict[str, Any]:
        package_path = os.path.join(self.workspace, "package.json")
        try:
            with open(package_path, encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, RuntimeError, ValueError):
            return {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _package_script(package_payload: dict[str, Any], script_name: str) -> str:
        scripts = package_payload.get("scripts")
        if not isinstance(scripts, dict):
            return ""
        return str(scripts.get(script_name) or "").strip()

    @staticmethod
    def _package_declares_dependency(package_payload: dict[str, Any], dependency_names: set[str]) -> bool:
        for section_name in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
            section = package_payload.get(section_name)
            if not isinstance(section, dict):
                continue
            if any(name in section for name in dependency_names):
                return True
        return False

    def _local_node_bin_exists(self, executable_name: str) -> bool:
        bin_dir = os.path.join(self.workspace, "node_modules", ".bin")
        candidates = (
            os.path.join(bin_dir, executable_name),
            os.path.join(bin_dir, f"{executable_name}.cmd"),
            os.path.join(bin_dir, f"{executable_name}.ps1"),
        )
        return any(os.path.isfile(path) for path in candidates)

    @staticmethod
    def _command_failure_excerpt(result: dict[str, Any]) -> str:
        parts: list[str] = []
        for key in ("error", "stdout", "stderr"):
            text = str(result.get(key) or "").strip()
            if text:
                parts.append(text)
        if not parts:
            parts.append(f"returncode={result.get('returncode', -1)}")
        return "\n".join(parts)[:1000]

    def _run_node_checks(self, context: PhaseContext) -> list[str]:
        """Run Node.js/TypeScript checks via CommandExecutionService (KernelOne contract)."""
        errors = []
        cmd_svc = CommandExecutionService(self.workspace)
        package_payload = self._read_package_json()

        if self._package_script(package_payload, "build"):
            try:
                req = cmd_svc.parse_command(
                    "npm run build",
                    cwd=self.workspace,
                    timeout_seconds=60,
                )
                result = cmd_svc.run(req)
                if not result["ok"]:
                    errors.append(f"Node build failed: {self._command_failure_excerpt(result)}")
            except ValueError:
                pass  # npm/build not in allowlist / validation failure
        elif os.path.exists(os.path.join(self.workspace, "tsconfig.json")) and (
            self._local_node_bin_exists("tsc")
            or self._package_declares_dependency(package_payload, {"typescript", "tsc"})
        ):
            try:
                req = cmd_svc.parse_command(
                    "npx tsc --noEmit",
                    cwd=self.workspace,
                    timeout_seconds=60,
                )
                result = cmd_svc.run(req)
                if not result["ok"]:
                    errors.append(f"TypeScript check failed: {self._command_failure_excerpt(result)}")
            except ValueError:
                pass  # npx or tsc not in allowlist / validation failure

        # ESLint check
        if (
            os.path.exists(os.path.join(self.workspace, ".eslintrc.js"))
            or os.path.exists(os.path.join(self.workspace, ".eslintrc.json"))
        ) and (self._local_node_bin_exists("eslint") or self._package_declares_dependency(package_payload, {"eslint"})):
            try:
                req = cmd_svc.parse_command(
                    "npx eslint --quiet .",
                    cwd=self.workspace,
                    timeout_seconds=60,
                )
                result = cmd_svc.run(req)
                if not result["ok"]:
                    errors.append(f"ESLint check failed: {self._command_failure_excerpt(result)}")
            except ValueError:
                pass  # npx or eslint not in allowlist / validation failure

        return errors

    def _run_python_checks(self, context: PhaseContext) -> list[str]:
        """Run Python checks via CommandExecutionService (KernelOne contract)."""
        errors: list[str] = []
        cmd_svc = CommandExecutionService(self.workspace)

        # MyPy check
        try:
            req = cmd_svc.parse_command(
                "python -m mypy . --ignore-missing-imports",
                cwd=self.workspace,
                timeout_seconds=60,
            )
            result = cmd_svc.run(req)
            if not result["ok"]:
                errors.append(f"MyPy check failed: {self._command_failure_excerpt(result)}")
        except ValueError:
            pass  # mypy not in allowlist / validation failure

        # Syntax check for changed files
        for file_path in context.changed_files:
            if file_path.endswith(".py"):
                full_path = os.path.join(self.workspace, file_path)
                try:
                    req = cmd_svc.parse_command(
                        f"python -m py_compile {full_path}",
                        timeout_seconds=10,
                    )
                    result = cmd_svc.run(req)
                    if not result["ok"]:
                        errors.append(f"Syntax error in {file_path}: {result.get('stderr', '')}")
                except ValueError:
                    pass  # py_compile validation failure

        return errors

    def _list_workspace_files(self) -> list[str]:
        """List all relevant files in workspace."""
        files = []
        for root, _, filenames in os.walk(self.workspace):
            # Skip common non-source directories
            if any(skip in root for skip in [".git", "node_modules", "__pycache__", ".polaris", ".polaris"]):
                continue
            for filename in filenames:
                rel_path = os.path.relpath(os.path.join(root, filename), self.workspace)
                files.append(rel_path)
        return files
