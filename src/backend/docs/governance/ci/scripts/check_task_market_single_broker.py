#!/usr/bin/env python3
"""Check task_market_single_broker: Task market follows single broker pattern.

Rule: task_market_is_single_business_broker
Severity: blocker
Description: >
    runtime.task_market is the sole business broker for PM/ChiefEngineer/Director/QA
    work-item publication, claim, ack, requeue, and dead-letter transitions.
    runtime.execution_broker may execute processes but must not own business task routing.

Evidence:
    - docs/AGENT_COLLABORATION_EDA_TASK_MARKET_BLUEPRINT_2026-04-14.md
    - docs/graph/catalog/cells.yaml
    - docs/graph/subgraphs/execution_governance_pipeline.yaml

Compliance:
    - Validate graph relations route PM/ChiefEngineer/Director/QA work-item
      transitions through runtime.task_market
    - runtime.execution_broker contracts must not be used for business task
      publication or claim semantics

Violations:
    - Direct task publication through execution_broker
    - Business task routing outside task_market
    - Task claim/acquire patterns bypassing task_market

Exit codes:
    0 - All checks passed
    1 - Rule violation detected
    2 - Script error (e.g., missing dependencies)
"""

from __future__ import annotations

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


class TaskMarketSingleBrokerChecker(FitnessRuleChecker):
    """Checker for task_market_single_broker rule."""

    # Patterns indicating direct execution_broker usage for task routing
    EXECUTION_BROKER_TASK_ROUTING_PATTERNS = [
        re.compile(r"ExecutionBroker\.publish\("),
        re.compile(r"execution_broker\.claim\("),
        re.compile(r"execution_broker\.acquire\("),
        re.compile(r"from.*execution_broker.*import.*publish", re.DOTALL),
        re.compile(r"from.*execution_broker.*import.*claim", re.DOTALL),
        re.compile(r"from.*execution_broker.*import.*acquire", re.DOTALL),
        re.compile(r"ExecutionBroker\("),
    ]

    # Patterns indicating proper task_market usage
    TASK_MARKET_PATTERNS = [
        re.compile(r"TaskMarket"),
        re.compile(r"task_market"),
        re.compile(r"WorkItem"),
        re.compile(r"work_item"),
        re.compile(r"publish_work_item"),
        re.compile(r"claim_work_item"),
    ]

    def __init__(self, workspace: Path | None = None) -> None:
        super().__init__(workspace)
        self.cells_dir = self.workspace / "polaris" / "cells"
        self.task_market_dir = self.cells_dir / "runtime" / "task_market"
        self.execution_broker_dir = self.cells_dir / "runtime" / "execution_broker"

    def _check_task_market_exists(self) -> bool:
        """Verify that runtime.task_market cell exists."""
        if not self.task_market_dir.exists():
            return False

        # Check for cell.yaml
        cell_yaml = self.task_market_dir / "cell.yaml"
        return cell_yaml.exists()

    def _check_execution_broker_forbidden_contracts(self) -> list[dict[str, Any]]:
        """Check execution_broker for forbidden business task routing contracts."""
        violations: list[dict[str, Any]] = []

        if not self.execution_broker_dir.exists():
            return violations

        for py_file in self.execution_broker_dir.rglob("*.py"):
            if "test" in py_file.parts:
                continue

            try:
                with open(py_file, encoding="utf-8") as f:
                    content = f.read()

                for pattern in self.EXECUTION_BROKER_TASK_ROUTING_PATTERNS:
                    for match in pattern.finditer(content):
                        line_num = content[: match.start()].count("\n") + 1
                        violations.append(
                            {
                                "file": str(py_file.relative_to(self.workspace)),
                                "line": line_num,
                                "pattern": match.group(),
                            }
                        )

            except OSError:
                continue

        return violations

    def _check_peers_use_task_market(self) -> tuple[bool, list[str]]:
        """Check if peer roles use task_market for business coordination.

        Returns (uses_task_market, files_not_using_task_market)
        """
        not_using_task_market = []

        # Check peer role directories
        peer_dirs = [
            self.cells_dir / "pm",
            self.cells_dir / "director",
            self.cells_dir / "chief_engineer",
            self.cells_dir / "qa",
        ]

        for dir_path in peer_dirs:
            if not dir_path.exists():
                continue

            for py_file in dir_path.rglob("*.py"):
                if "test" in py_file.parts:
                    continue

                try:
                    with open(py_file, encoding="utf-8") as f:
                        content = f.read()

                    # Check if this file does any task-related operations
                    has_task_operations = any(
                        pattern.search(content) for pattern in self.EXECUTION_BROKER_TASK_ROUTING_PATTERNS
                    )

                    if has_task_operations:
                        # Check if it also uses task_market (allowed)
                        uses_task_market = any(pattern.search(content) for pattern in self.TASK_MARKET_PATTERNS)

                        if not uses_task_market:
                            not_using_task_market.append(str(py_file.relative_to(self.workspace)))

                except OSError:
                    continue

        return len(not_using_task_market) == 0, not_using_task_market

    def _check_graph_relations(self) -> tuple[bool, list[str]]:
        """Check that cells.yaml graph relations route through task_market.

        Returns (correct_relations, issues)
        """
        import yaml

        cells_yaml_path = self.workspace / "docs" / "graph" / "catalog" / "cells.yaml"
        issues = []

        if not cells_yaml_path.exists():
            return False, ["cells.yaml not found"]

        try:
            with open(cells_yaml_path, encoding="utf-8") as f:
                catalog_data = yaml.safe_load(f)

            cells = catalog_data.get("cells", [])

            # Check that task_market is in depends_on for peer roles
            peer_role_ids = [
                "pm",
                "chief_engineer",
                "director",
                "qa",
                "roles.pm",
                "roles.chief_engineer",
                "roles.director",
                "roles.qa",
            ]

            for cell in cells:
                cell_id = cell.get("id", "")
                if any(peer in cell_id for peer in peer_role_ids):
                    depends_on = cell.get("depends_on", [])
                    if "runtime.task_market" not in depends_on and "task_market" not in depends_on:
                        issues.append(f"Cell '{cell_id}' missing runtime.task_market in depends_on")

            return len(issues) == 0, issues

        except (OSError, yaml.YAMLError) as e:
            return False, [f"Error parsing cells.yaml: {e}"]

    def check(self) -> FitnessCheckResult:
        """Check that task_market is the single business broker.

        The rule enforces:
        1. runtime.task_market cell exists and owns task routing contracts
        2. runtime.execution_broker does not have business task routing methods
        3. Peer roles (PM/Director/ChiefEngineer/QA) use task_market for coordination
        4. Graph relations properly route through task_market
        """
        result = FitnessCheckResult(
            rule_id="task_market_is_single_business_broker",
            passed=True,
        )

        # Step 1: Verify task_market exists
        if not self._check_task_market_exists():
            result.passed = False
            result.violations.append("runtime.task_market cell not found or incomplete")
            return result

        result.evidence.append("runtime.task_market cell exists")

        # Step 2: Check execution_broker for forbidden contracts
        broker_violations = self._check_execution_broker_forbidden_contracts()

        if broker_violations:
            result.passed = False
            for v in broker_violations:
                result.violations.append(f"Execution broker task routing at {v['file']}:{v['line']}: {v['pattern']}")
        else:
            result.evidence.append("execution_broker does not have business task routing")

        # Step 3: Check peer roles use task_market
        uses_task_market, not_using = self._check_peers_use_task_market()

        if not uses_task_market:
            for path in not_using:
                result.warnings.append(f"Peer role file does not use task_market: {path}")

        # Step 4: Check graph relations
        correct_relations, graph_issues = self._check_graph_relations()

        if not correct_relations:
            result.warnings.extend(graph_issues)
        else:
            result.evidence.append("Graph relations correctly route through task_market")

        return result


def main() -> int:
    """Main entry point for running the check."""
    checker = TaskMarketSingleBrokerChecker()
    result = checker.check()
    print(result.format())

    # JSON output for CI integration
    import os

    if os.environ.get("CHECK_TASK_MARKET_BROKER_JSON_OUTPUT"):
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
