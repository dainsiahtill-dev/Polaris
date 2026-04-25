#!/usr/bin/env python3
"""Check no_direct_role_call: No cell calls another cell's internal role functions.

Rule: no_direct_role_call
Severity: blocker
Description: >
    PM, ChiefEngineer, Director, and QA mainline collaboration must not rely on
    direct role-to-role service or agent invocation. Business coordination must
    flow through runtime.task_market contracts and state transitions.

Evidence:
    - docs/AGENT_COLLABORATION_EDA_TASK_MARKET_BLUEPRINT_2026-04-14.md
    - docs/graph/subgraphs/execution_governance_pipeline.yaml
    - docs/graph/subgraphs/pm_pipeline.yaml

Compliance:
    - grep mainline orchestration paths for direct imports of peer role public.service modules
    - PM/ChiefEngineer/Director/QA public services must not directly call each other
    - All role-to-role communication must go through task_market contracts

Violations:
    - Direct imports from peer role public.service modules
    - Direct function calls between roles outside task_market contracts
    - Peer role adapter patterns that bypass task market

Exit codes:
    0 - All checks passed
    1 - Rule violation detected
    2 - Script error (e.g., missing dependencies)
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).parent.resolve()  # docs/governance/ci/scripts
GOVERNANCE_DIR = SCRIPT_DIR.parent  # docs/governance
CI_DIR = GOVERNANCE_DIR.parent  # docs
DOCS_DIR = CI_DIR.parent  # docs
BACKEND_ROOT = DOCS_DIR.parent  # src/backend

sys.path.insert(0, str(SCRIPT_DIR))
from fitness_rule_checker import FitnessCheckResult, FitnessRuleChecker  # noqa: E402


class NoDirectRoleCallChecker(FitnessRuleChecker):
    """Checker for no_direct_role_call rule."""

    # Roles that should not directly call each other
    RESTRICTED_ROLES = {
        "roles.pm",
        "roles.chief_engineer",
        "roles.director",
        "roles.qa",
        "pm",
        "chief_engineer",
        "director",
        "qa",
    }

    # Pattern to match direct role imports
    ROLE_IMPORT_PATTERN = re.compile(
        r"from\s+polaris\.cells\.(?:roles|" + r"(?:director|pm|chief_engineer|qa))"
        r"\.?(?:public|internal)?"
        r"\.?(?:service|agent|adapter)?"
        r"\.?\w*"
        r"\s+import"
    )

    # Pattern for direct function/class calls to role services
    ROLE_CALL_PATTERN = re.compile(
        r"(DirectorService|ChiefEngineerService|PmService|QaService|"
        + r"RoleService|RoleAgent|"
        + r"execute_role|invoke_role|run_role|call_role)"
    )

    # Allowed patterns: imports through task_market or approved adapters
    ALLOWED_PATTERNS = [
        re.compile(r"from polaris\.cells\.runtime\.task_market"),
        re.compile(r"from polaris\.cells\.roles\.runtime"),
        re.compile(r"\.task_market"),
        re.compile(r"TaskMarket"),
        re.compile(r"WorkItem"),
    ]

    def __init__(self, workspace: Path | None = None) -> None:
        super().__init__(workspace)
        self.cells_dir = self.workspace / "polaris" / "cells"

    def _is_allowed_import(self, content: str, import_line: str) -> bool:
        """Check if an import is allowed through task_market or runtime."""
        # Check if the import line itself contains task_market pattern
        if any(pattern.search(import_line) for pattern in self.ALLOWED_PATTERNS):
            return True
        # Check if the file uses task_market
        return any(pattern.search(content) for pattern in self.ALLOWED_PATTERNS)

    def _check_file_for_violations(self, py_file: Path) -> list[dict[str, Any]]:
        """Check a Python file for direct role call violations."""
        violations: list[dict[str, Any]] = []

        # Skip files in restricted roles themselves (they can have internal imports)
        file_path_str = str(py_file.relative_to(self.workspace))

        try:
            with open(py_file, encoding="utf-8") as f:
                content = f.read()

            # Parse the file
            tree = ast.parse(content, filename=str(py_file))

            # Check imports
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    if "polaris.cells.roles" in module or "polaris.cells.director" in module:
                        # Check if this is a peer role import
                        for alias in node.names:
                            name = alias.name
                            # Check for direct service/agent imports from peer roles
                            if name.endswith(("Service", "Agent", "Adapter")) and any(
                                role in module for role in ["pm", "director", "chief_engineer", "qa"]
                            ):
                                # Get line number
                                line_num = node.lineno
                                violations.append(
                                    {
                                        "file": file_path_str,
                                        "line": line_num,
                                        "type": "peer_role_import",
                                        "import": f"{module}.{name}",
                                    }
                                )

            # Check for suspicious function calls
            for match in self.ROLE_CALL_PATTERN.finditer(content):
                line_num = content[: match.start()].count("\n") + 1

                # Skip if it's in a test or allowed pattern
                if (
                    not self._is_allowed_import(content, match.group())
                    and "=" in content[match.start() : match.end() + 30]
                ):
                    violations.append(
                        {
                            "file": file_path_str,
                            "line": line_num,
                            "type": "suspicious_call",
                            "pattern": match.group(),
                        }
                    )

        except OSError:
            pass
        except SyntaxError as e:
            self.warnings.append(f"Could not parse {py_file}: {e}")

        return violations

    def _check_mainline_orchestration(self) -> list[dict[str, Any]]:
        """Check mainline orchestration paths for direct role calls."""
        violations = []

        # Check orchestration directories
        orchestrator_dirs = [
            self.cells_dir / "director" / "execution",
            self.cells_dir / "pm" / "workflow",
            self.cells_dir / "roles" / "runtime",
        ]

        for dir_path in orchestrator_dirs:
            if not dir_path.exists():
                continue

            for py_file in dir_path.rglob("*.py"):
                # Skip tests
                if "test" in py_file.parts:
                    continue

                file_violations = self._check_file_for_violations(py_file)
                violations.extend(file_violations)

        return violations

    def check(self) -> FitnessCheckResult:
        """Check that roles do not directly call peer role services.

        The rule enforces:
        1. No direct imports of peer role public.service modules
        2. No direct function calls between roles outside task_market
        3. All role-to-role communication flows through task_market
        """
        result = FitnessCheckResult(
            rule_id="no_direct_role_call",
            passed=True,
        )

        # Check mainline orchestration paths
        violations = self._check_mainline_orchestration()

        if violations:
            result.passed = False
            for v in violations:
                if v["type"] == "peer_role_import":
                    result.violations.append(f"Direct peer role import at {v['file']}:{v['line']}: {v['import']}")
                else:
                    result.violations.append(f"Suspicious role call at {v['file']}:{v['line']}: {v['pattern']}")
        else:
            result.evidence.append("No direct peer role calls found in mainline orchestration")

        return result


def main() -> int:
    """Main entry point for running the check."""
    checker = NoDirectRoleCallChecker()
    result = checker.check()
    print(result.format())

    # JSON output for CI integration
    import os

    if os.environ.get("CHECK_NO_DIRECT_ROLE_CALL_JSON_OUTPUT"):
        import json

        print(
            json.dumps(
                {
                    "rule_id": result.rule_id,
                    "passed": result.passed,
                    "evidence": result.evidence,
                    "violations": result.violations,
                    "warnings": result.warnings,
                    "timestamp": result.timestamp,
                    "duration_ms": result.duration_ms,
                },
                indent=2,
            )
        )

    return 0 if result.passed else 1


if __name__ == "__main__":
    sys.exit(main())
