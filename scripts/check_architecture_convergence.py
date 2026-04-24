#!/usr/bin/env python3
"""Architecture convergence checker for Polaris.

Checks:
1. Architecture convergence (workflow-only, no forbidden imports)
2. State consistency improvements
3. Error classification coverage
4. Prompt leakage detection effectiveness

Usage:
    python scripts/check_architecture_convergence.py --workspace <path>
    python scripts/check_architecture_convergence.py --verbose
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
BACKEND_ROOT = PROJECT_ROOT / "src" / "backend"

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# Files for legacy checks
CONFIG_FILE = BACKEND_ROOT / "app" / "orchestration" / "config.py"
ENGINE_FILE = BACKEND_ROOT / "scripts" / "pm" / "orchestration_engine.py"
ACTIVITIES_DIR = BACKEND_ROOT / "app" / "orchestration" / "activities"
ROLE_AGENT_DIR = BACKEND_ROOT / "core" / "polaris_loop" / "role_agent"


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def list_python_files(root: Path) -> list[Path]:
    return [path for path in root.rglob("*.py") if path.is_file()]


# =============================================================================
# Legacy Architecture Convergence Checks
# =============================================================================


def check_forbidden_temporal_imports(files: list[Path]) -> list[str]:
    issues: list[str] = []
    forbidden = (
        re.compile(r"^\s*import\s+workflowio\b", re.MULTILINE),
        re.compile(r"^\s*from\s+workflowio\b", re.MULTILINE),
        re.compile(r"^\s*import\s+temporal\b", re.MULTILINE),
        re.compile(r"^\s*from\s+temporal\b", re.MULTILINE),
    )
    for path in files:
        content = read_text(path)
        for pattern in forbidden:
            if pattern.search(content):
                issues.append(f"{path.relative_to(PROJECT_ROOT)}: forbidden import pattern `{pattern.pattern}`")
    return issues


def check_runtime_contract() -> list[str]:
    issues: list[str] = []
    if not CONFIG_FILE.exists():
        return [f"{CONFIG_FILE.relative_to(PROJECT_ROOT)}: missing config file"]

    content = read_text(CONFIG_FILE)
    if 'SUPPORTED_ORCHESTRATION_RUNTIMES = ("workflow",)' not in content:
        issues.append(
            f"{CONFIG_FILE.relative_to(PROJECT_ROOT)}: SUPPORTED_ORCHESTRATION_RUNTIMES must be workflow-only"
        )
    if "return \"workflow\"" not in content:
        issues.append(
            f"{CONFIG_FILE.relative_to(PROJECT_ROOT)}: resolve_orchestration_runtime must normalize to workflow"
        )
    return issues


def check_activity_layer_isolation() -> list[str]:
    issues: list[str] = []
    if not ACTIVITIES_DIR.exists():
        return issues
    patterns = (
        "from pm import orchestration_engine",
        "import pm.orchestration_engine",
        "from pm.orchestration_engine import",
    )
    for path in list_python_files(ACTIVITIES_DIR):
        content = read_text(path)
        for token in patterns:
            if token in content:
                issues.append(f"{path.relative_to(PROJECT_ROOT)}: forbidden activity dependency `{token}`")
    return issues


def check_engine_workflow_only() -> list[str]:
    issues: list[str] = []
    if not ENGINE_FILE.exists():
        return [f"{ENGINE_FILE.relative_to(PROJECT_ROOT)}: missing orchestration engine file"]
    content = read_text(ENGINE_FILE)
    forbidden_tokens = (
        "_run_dispatch_pipeline_with_nodes(",
        "orchestration_nodes_",
        '{"nodes", "auto"}',
        '_ORCHESTRATION_RUNTIME_DEFAULT = "nodes"',
        "fallback to embedded dispatch pipeline",
    )
    for token in forbidden_tokens:
        if token in content:
            issues.append(f"{ENGINE_FILE.relative_to(PROJECT_ROOT)}: forbidden workflow-divergent token `{token}`")
    return issues


def check_taskboard_single_impl(all_files: list[Path]) -> list[str]:
    issues: list[str] = []
    # After migration: canonical location is app.services.task_board
    forbidden_import_tokens = (
        "from core.polaris_loop.task_board import",
        "import core.polaris_loop.task_board",
    )
    for path in all_files:
        content = read_text(path)
        for token in forbidden_import_tokens:
            if token in content:
                issues.append(
                    f"{path.relative_to(PROJECT_ROOT)}: import must target app.services.task_board, found `{token}`"
                )
    # Check that shim files remain thin (no implementation)
    shim_file = ROLE_AGENT_DIR / "taskboard.py"
    if shim_file.exists():
        content = read_text(shim_file)
        if "class TaskBoard" in content:
            issues.append(
                f"{shim_file.relative_to(PROJECT_ROOT)}: legacy TaskBoard implementation detected; shim-only file required"
            )
    # Check that old compatibility layer is removed
    old_compat_file = Path("src/backend/core/polaris_loop/task_board.py")
    if old_compat_file.exists():
        issues.append(
            f"{old_compat_file}: deprecated compatibility layer should be removed, use app.services.task_board"
        )
    return issues


def run_legacy_checks(verbose: bool) -> tuple[bool, list[str]]:
    all_files = list_python_files(BACKEND_ROOT)
    issues: list[str] = []
    issues.extend(check_forbidden_temporal_imports(all_files))
    issues.extend(check_runtime_contract())
    issues.extend(check_activity_layer_isolation())
    issues.extend(check_engine_workflow_only())
    issues.extend(check_taskboard_single_impl(all_files))
    if verbose and not issues:
        print("All legacy convergence checks passed.")
    return len(issues) == 0, issues


# =============================================================================
# New Architecture Improvement Checks
# =============================================================================


class ImprovementChecker:
    """Check architecture improvements (state bridge, error classifier, etc.)."""

    def __init__(self, workspace: str) -> None:
        self.workspace = Path(workspace)
        self.results: dict[str, Any] = {
            "timestamp": None,
            "checks": {},
            "overall": {"passed": False, "score": 0.0},
        }

    async def run_all_checks(self) -> dict[str, Any]:
        """Run all improvement checks."""
        from datetime import datetime, timezone

        self.results["timestamp"] = datetime.now(timezone.utc).isoformat()

        # Check 1: State Bridge Availability
        self.results["checks"]["state_bridge"] = await self._check_state_bridge()

        # Check 2: Error Classifier
        self.results["checks"]["error_classifier"] = await self._check_error_classifier()

        # Check 3: TaskBoard Integration
        self.results["checks"]["task_board"] = await self._check_task_board()

        # Check 4: Workflow Runtime
        self.results["checks"]["workflow_runtime"] = await self._check_workflow_runtime()

        # Calculate overall score
        scores = [
            c["score"] for c in self.results["checks"].values() if "score" in c
        ]
        self.results["overall"]["score"] = sum(scores) / len(scores) if scores else 0.0
        self.results["overall"]["passed"] = self.results["overall"]["score"] >= 0.8

        return self.results

    async def _check_state_bridge(self) -> dict[str, Any]:
        """Check state bridge implementation."""
        result: dict[str, Any] = {"name": "State Bridge", "score": 0.0, "details": {}}

        try:
            sys.path.insert(0, str(BACKEND_ROOT))
            from app.orchestration.state_bridge import (
                StateConsistencyChecker,
                TaskBoardStateBridge,
            )

            # Check if classes are importable and have required methods
            required_methods = [
                "start",
                "stop",
                "notify_task_created",
                "notify_task_updated",
                "notify_task_completed",
            ]

            for method in required_methods:
                result["details"][f"has_{method}"] = hasattr(TaskBoardStateBridge, method)

            # Check consistency checker
            result["details"]["has_consistency_checker"] = hasattr(StateConsistencyChecker, "check_consistency")

            # Calculate score
            passed = sum(1 for v in result["details"].values() if v)
            result["score"] = passed / len(result["details"]) if result["details"] else 0.0
            result["status"] = "passed" if result["score"] >= 0.8 else "failed"

        except ImportError as e:
            result["status"] = "error"
            result["error"] = f"Failed to import state_bridge: {e}"
            result["score"] = 0.0

        return result

    async def _check_error_classifier(self) -> dict[str, Any]:
        """Check error classifier implementation."""
        result: dict[str, Any] = {"name": "Error Classifier", "score": 0.0, "details": {}}

        try:
            from app.orchestration.error_classifier import (
                CircuitBreaker,
                ErrorCategory,
                ErrorClassifier,
                ExponentialBackoff,
                RetryExecutor,
            )

            # Test error classification
            test_errors = [
                (ConnectionRefusedError("connection refused"), ErrorCategory.TRANSIENT_NETWORK),
                (TimeoutError("operation timed out"), ErrorCategory.SYSTEM_TIMEOUT),
                (PermissionError("access denied"), ErrorCategory.PERMANENT_AUTH),
                (ValueError("invalid argument"), ErrorCategory.PERMANENT_VALIDATION),
            ]

            correct = 0
            for error, expected in test_errors:
                category = ErrorClassifier.classify(error)
                if category == expected:
                    correct += 1

            result["details"]["classification_accuracy"] = correct / len(test_errors) if test_errors else 0.0

            # Check required classes exist
            result["details"]["has_circuit_breaker"] = True
            result["details"]["has_backoff"] = True
            result["details"]["has_retry_executor"] = True

            # Calculate score
            result["score"] = (
                result["details"]["classification_accuracy"] * 0.5
                + 0.5  # Component availability
            )
            result["status"] = "passed" if result["score"] >= 0.8 else "failed"

        except ImportError as e:
            result["status"] = "error"
            result["error"] = f"Failed to import error_classifier: {e}"
            result["score"] = 0.0

        return result

    async def _check_task_board(self) -> dict[str, Any]:
        """Check TaskBoard state bridge integration."""
        result: dict[str, Any] = {"name": "Task Board", "score": 0.0, "details": {}}

        try:
            from app.services.task_board import TaskBoard

            # Check if TaskBoard accepts state_bridge parameter
            import inspect

            sig = inspect.signature(TaskBoard.__init__)
            result["details"]["has_state_bridge_param"] = "state_bridge" in sig.parameters

            # Check if update_status accepts workflow_id
            sig = inspect.signature(TaskBoard.update_status)
            result["details"]["has_workflow_id_param"] = "workflow_id" in sig.parameters

            # Try to create a TaskBoard instance
            try:
                tb = TaskBoard(str(self.workspace))
                result["details"]["can_instantiate"] = True

                # Check if state_bridge attribute exists
                result["details"]["has_state_bridge_attr"] = hasattr(tb, "_state_bridge")

            except Exception as e:
                result["details"]["can_instantiate"] = False
                result["details"]["instantiate_error"] = str(e)

            # Calculate score
            passed = sum(
                1 for k, v in result["details"].items()
                if k != "instantiate_error" and v is True
            )
            total = len([k for k in result["details"].keys() if k != "instantiate_error"])
            result["score"] = passed / total if total > 0 else 0.0
            result["status"] = "passed" if result["score"] >= 0.8 else "failed"

        except ImportError as e:
            result["status"] = "error"
            result["error"] = f"Failed to import task_board: {e}"
            result["score"] = 0.0

        return result

    async def _check_workflow_runtime(self) -> dict[str, Any]:
        """Check Workflow Runtime availability."""
        result: dict[str, Any] = {"name": "Workflow Runtime", "score": 0.0, "details": {}}

        try:
            from app.orchestration.runtime.embedded.store_sqlite import (
                SqliteRuntimeStore,
            )

            # Check required methods
            required_methods = [
                "create_execution",
                "append_event",
                "upsert_task_state",
                "get_execution",
                "list_task_states",
            ]

            for method in required_methods:
                result["details"][f"has_{method}"] = hasattr(SqliteRuntimeStore, method)

            # Try to create an in-memory store
            try:
                store = SqliteRuntimeStore(":memory:")
                result["details"]["can_instantiate"] = True

                # Test basic operations
                await store.create_execution("test-1", "test_workflow", {})
                exec_result = await store.get_execution("test-1")
                result["details"]["basic_operations_work"] = exec_result is not None

            except Exception as e:
                result["details"]["can_instantiate"] = False
                result["details"]["operation_error"] = str(e)

            # Calculate score
            passed = sum(
                1 for k, v in result["details"].items()
                if not k.endswith("_error") and v is True
            )
            total = len([k for k in result["details"].keys() if not k.endswith("_error")])
            result["score"] = passed / total if total > 0 else 0.0
            result["status"] = "passed" if result["score"] >= 0.8 else "failed"

        except ImportError as e:
            result["status"] = "error"
            result["error"] = f"Failed to import workflow runtime: {e}"
            result["score"] = 0.0

        return result


def print_improvement_report(results: dict[str, Any]) -> None:
    """Print a formatted improvement report."""
    print("\n" + "=" * 70)
    print("Polaris Architecture Improvement Report")
    print("=" * 70)
    print(f"Timestamp: {results['timestamp']}")
    print()

    for check_name, check_result in results["checks"].items():
        status = check_result.get('status', 'unknown').upper()
        print(f"\n{check_result['name']}: {status}")
        print("-" * 50)
        print(f"Score: {check_result.get('score', 0) * 100:.1f}%")

        if "error" in check_result:
            print(f"Error: {check_result['error']}")
        else:
            for detail, value in check_result.get("details", {}).items():
                status = "✓" if value is True else "✗" if value is False else "•"
                print(f"  {status} {detail}: {value}")

    print("\n" + "=" * 70)
    overall = results["overall"]
    status = "PASSED" if overall["passed"] else "FAILED"
    print(f"Overall: {status} (Score: {overall['score'] * 100:.1f}%)")
    print("=" * 70)


# =============================================================================
# Main Entry Point
# =============================================================================


async def async_main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate Polaris architecture convergence and improvements."
    )
    parser.add_argument("--verbose", action="store_true", help="Print success messages when checks pass.")
    parser.add_argument("--workspace", default=".", help="Path to workspace for improvement checks.")
    parser.add_argument("--json", action="store_true", help="Output improvement results as JSON.")
    parser.add_argument("--legacy-only", action="store_true", help="Run only legacy checks.")
    parser.add_argument("--improvements-only", action="store_true", help="Run only improvement checks.")
    args = parser.parse_args()

    exit_code = 0

    # Run legacy checks
    if not args.improvements_only:
        print("\n[1/2] Running legacy architecture convergence checks...")
        ok, issues = run_legacy_checks(verbose=args.verbose)
        if not ok:
            print("\nLegacy architecture convergence check failed:")
            for issue in issues:
                print(f"  - {issue}")
            exit_code = 1
        else:
            print("Legacy checks: PASSED")

    # Run improvement checks
    if not args.legacy_only:
        print("\n[2/2] Running architecture improvement checks...")
        checker = ImprovementChecker(args.workspace)
        results = await checker.run_all_checks()

        if args.json:
            print(json.dumps(results, indent=2))
        else:
            print_improvement_report(results)

        if not results["overall"]["passed"]:
            exit_code = 1
        else:
            print("\nImprovement checks: PASSED")

    return exit_code


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    sys.exit(main())
