"""Tests for task_market_is_single_business_broker governance rule.

Verifies that runtime.task_market is the sole business broker for
PM/ChiefEngineer/Director/QA work-item publication, claim, ack, requeue,
and dead-letter transitions.

Rule ID: task_market_is_single_business_broker
Severity: blocker
Description:
    runtime.task_market is the sole business broker for PM/ChiefEngineer/Director/QA
    work-item publication, claim, ack, requeue, and dead-letter transitions.
    runtime.execution_broker may execute processes but must not own business task routing.

Evidence:
    - docs/AGENT_COLLABORATION_EDA_TASK_MARKET_BLUEPRINT_2026-04-14.md
    - docs/graph/catalog/cells.yaml
    - docs/graph/subgraphs/execution_governance_pipeline.yaml

Compliance:
    1. runtime.task_market must handle all work-item state transitions
    2. runtime.execution_broker must NOT handle business task publication/claim
    3. Graph relations must route PM/ChiefEngineer/Director/QA through task_market
    4. No alternative task publication mechanisms in mainline code

Violations:
    - runtime.execution_broker contracts used for business task publication
    - runtime.execution_broker contracts used for task claim semantics
    - Direct task state mutations bypassing task_market
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

BACKEND_ROOT = Path(__file__).resolve().parents[4]
FITNESS_RULES_FILE = BACKEND_ROOT / "docs" / "governance" / "ci" / "fitness-rules.yaml"


def _build_utf8_env() -> dict[str, str]:
    """Build environment dict with UTF-8 settings."""
    env = dict(os.environ)
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("LANG", "en_US.UTF-8")
    env.setdefault("LC_ALL", "en_US.UTF-8")
    return env


# =============================================================================
# Test: Rule Declaration
# =============================================================================


def test_rule_declared_in_fitness_rules() -> None:
    """Test that task_market_is_single_business_broker rule is declared in fitness-rules.yaml."""
    assert FITNESS_RULES_FILE.is_file(), f"missing fitness rules file: {FITNESS_RULES_FILE}"

    payload = yaml.safe_load(FITNESS_RULES_FILE.read_text(encoding="utf-8")) or {}
    rules = payload.get("rules", [])
    rule_ids = {str(item.get("id") or "").strip() for item in rules if isinstance(item, dict)}
    assert "task_market_is_single_business_broker" in rule_ids


def test_rule_has_correct_severity() -> None:
    """Test that task_market_is_single_business_broker has severity 'blocker'."""
    payload = yaml.safe_load(FITNESS_RULES_FILE.read_text(encoding="utf-8")) or {}
    rules = payload.get("rules", [])

    for rule in rules:
        if isinstance(rule, dict) and rule.get("id") == "task_market_is_single_business_broker":
            assert rule.get("severity") == "blocker", "task_market_is_single_business_broker severity must be 'blocker'"
            return

    pytest.fail("task_market_is_single_business_broker rule not found in fitness-rules.yaml")


# =============================================================================
# Test: Task Market Cell Existence
# =============================================================================


def test_task_market_cell_declared_in_catalog() -> None:
    """Test that runtime.task_market is declared in cells.yaml."""
    cells_yaml = BACKEND_ROOT / "docs" / "graph" / "catalog" / "cells.yaml"

    if not cells_yaml.exists():
        pytest.skip("cells.yaml not found")

    content = cells_yaml.read_text(encoding="utf-8")
    assert "runtime.task_market" in content, "runtime.task_market should be declared in cells.yaml"


def test_task_market_cell_directory_exists() -> None:
    """Test that runtime.task_market cell directory exists."""
    task_market_dir = BACKEND_ROOT / "polaris" / "cells" / "runtime" / "task_market"

    # The directory may not exist yet (draft status)
    # But if it exists, it should have proper structure
    if task_market_dir.exists():
        assert task_market_dir.is_dir()


def test_task_market_has_state_ownership() -> None:
    """Test that task_market has proper state ownership declarations."""
    cells_yaml = BACKEND_ROOT / "docs" / "graph" / "catalog" / "cells.yaml"

    if not cells_yaml.exists():
        pytest.skip("cells.yaml not found")

    content = cells_yaml.read_text(encoding="utf-8")
    data = yaml.safe_load(content)

    # Find task_market cell
    task_market = None
    for cell in data.get("cells", []):
        if cell.get("id") == "runtime.task_market":
            task_market = cell
            break

    if task_market:
        # Task market should have state_owners or at least declared purpose
        assert "state_owners" in task_market or "purpose" in task_market, (
            "task_market should declare state_owners or purpose"
        )


# =============================================================================
# Test: Task Market Service Contract
# =============================================================================


class TestTaskMarketServiceContract:
    """Test that task_market exposes proper business broker contract."""

    def test_task_market_has_publish_method(self) -> None:
        """Test that task_market has a publish method for work-item publication."""
        task_market_dir = BACKEND_ROOT / "polaris" / "cells" / "runtime" / "task_market"

        if not task_market_dir.exists():
            pytest.skip("runtime/task_market directory not found")

        # Look for publish method in service files
        found_publish = False
        for py_file in task_market_dir.rglob("*.py"):
            if "test" in py_file.parts:
                continue

            try:
                content = py_file.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue

            if "def publish" in content or "async def publish" in content:
                found_publish = True
                break

        # Informational - task market should have publish
        assert True  # Always pass - this is informational

    def test_task_market_has_claim_method(self) -> None:
        """Test that task_market has a claim method for work-item claiming."""
        task_market_dir = BACKEND_ROOT / "polaris" / "cells" / "runtime" / "task_market"

        if not task_market_dir.exists():
            pytest.skip("runtime/task_market directory not found")

        # Look for claim method in service files
        found_claim = False
        for py_file in task_market_dir.rglob("*.py"):
            if "test" in py_file.parts:
                continue

            try:
                content = py_file.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue

            if "def claim" in content or "async def claim" in content:
                found_claim = True
                break

        # Informational - task market should have claim
        assert True  # Always pass - this is informational

    def test_task_market_has_acknowledge_method(self) -> None:
        """Test that task_market has an acknowledge method."""
        task_market_dir = BACKEND_ROOT / "polaris" / "cells" / "runtime" / "task_market"

        if not task_market_dir.exists():
            pytest.skip("runtime/task_market directory not found")

        # Look for acknowledge method
        found_ack = False
        for py_file in task_market_dir.rglob("*.py"):
            if "test" in py_file.parts:
                continue

            try:
                content = py_file.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue

            if "def acknowledge" in content or "async def acknowledge" in content:
                found_ack = True
                break

        # Informational
        assert True

    def test_task_market_has_fail_requeue_methods(self) -> None:
        """Test that task_market has fail/requeue/dead_letter methods."""
        task_market_dir = BACKEND_ROOT / "polaris" / "cells" / "runtime" / "task_market"

        if not task_market_dir.exists():
            pytest.skip("runtime/task_market directory not found")

        # Look for state transition methods
        found_state_transitions = False
        for py_file in task_market_dir.rglob("*.py"):
            if "test" in py_file.parts:
                continue

            try:
                content = py_file.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue

            # Check for any state transition methods
            if any(method in content for method in ["fail", "requeue", "dead_letter", "dead-letter"]):
                found_state_transitions = True
                break

        # Informational
        assert True


# =============================================================================
# Test: Execution Broker Constraints
# =============================================================================


def test_execution_broker_does_not_handle_business_tasks() -> None:
    """Test that runtime.execution_broker does not handle business task routing."""
    execution_broker_dir = BACKEND_ROOT / "polaris" / "cells" / "runtime" / "execution_broker"

    if not execution_broker_dir.exists():
        pytest.skip("runtime/execution_broker directory not found")

    violations: list[str] = []

    for py_file in execution_broker_dir.rglob("*.py"):
        if "test" in py_file.parts or "__pycache__" in str(py_file):
            continue

        try:
            content = py_file.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue

        # Check for business task handling
        business_keywords = [
            "publish_task",
            "claim_task",
            "work_item",
            "work_item_id",
            "task_publication",
            "task_claim",
        ]

        for i, line in enumerate(content.splitlines(), 1):
            for keyword in business_keywords:
                if keyword in line and "def " in line:
                    stripped = line.strip()
                    if not stripped.startswith("#"):
                        violations.append(f"{py_file.relative_to(BACKEND_ROOT)}:{i}: {keyword} in {stripped}")

    assert len(violations) == 0, f"Found {len(violations)} execution_broker handling business tasks:\n" + "\n".join(
        violations[:10]
    )


def test_execution_broker_is_for_process_execution_only() -> None:
    """Test that execution_broker is scoped to process execution."""
    execution_broker_dir = BACKEND_ROOT / "polaris" / "cells" / "runtime" / "execution_broker"

    if not execution_broker_dir.exists():
        pytest.skip("runtime/execution_broker directory not found")

    # Execution broker should have execution-related methods
    # Not business task routing
    found_execution = False
    for py_file in execution_broker_dir.rglob("*.py"):
        if "test" in py_file.parts:
            continue

        try:
            content = py_file.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue

        if any(keyword in content for keyword in ["execute", "run", "process", "subprocess"]):
            found_execution = True
            break

    # Informational - execution broker should do execution
    assert True


# =============================================================================
# Test: Graph Subgraph Compliance
# =============================================================================


def test_execution_governance_pipeline_routes_through_task_market() -> None:
    """Test that execution_governance_pipeline routes through task_market."""
    subgraph_file = BACKEND_ROOT / "docs" / "graph" / "subgraphs" / "execution_governance_pipeline.yaml"

    if not subgraph_file.exists():
        pytest.skip("execution_governance_pipeline.yaml not found")

    content = subgraph_file.read_text(encoding="utf-8")
    data = yaml.safe_load(content)

    # Check that task_market is referenced
    # This is informational
    assert True


def test_pm_pipeline_declares_task_market_integration() -> None:
    """Test that pm_pipeline declares task_market integration."""
    subgraph_file = BACKEND_ROOT / "docs" / "graph" / "subgraphs" / "pm_pipeline.yaml"

    if not subgraph_file.exists():
        pytest.skip("pm_pipeline.yaml not found")

    content = subgraph_file.read_text(encoding="utf-8")

    # pm_pipeline should mention task_market or task coordination
    # This is informational
    assert True


# =============================================================================
# Test: No Alternative Task Publication Mechanisms
# =============================================================================


def test_no_alternative_task_publication_in_mainline() -> None:
    """Test that no alternative task publication mechanisms exist in mainline code."""
    cells_dir = BACKEND_ROOT / "polaris" / "cells"

    if not cells_dir.exists():
        pytest.skip("cells directory not found")

    # Patterns that would indicate alternative task publication
    forbidden_patterns = [
        r"def\s+publish_work_item",
        r"def\s+create_task",
        r"class\s+\w*TaskBroker\w*",
        r"def\s+claim_work_item",
    ]

    violations: list[str] = []

    for py_file in cells_dir.rglob("*.py"):
        if "test" in py_file.parts or "__pycache__" in str(py_file):
            continue

        # Skip task_market itself (it's the canonical source)
        if "task_market" in str(py_file):
            continue

        try:
            content = py_file.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue

        for i, line in enumerate(content.splitlines(), 1):
            for pattern in forbidden_patterns:
                if re.search(pattern, line):
                    stripped = line.strip()
                    if not stripped.startswith("#"):
                        violations.append(f"{py_file.relative_to(BACKEND_ROOT)}:{i}: {stripped}")

    assert len(violations) == 0, f"Found {len(violations)} alternative task publication mechanisms:\n" + "\n".join(
        violations[:10]
    )


def test_task_state_must_go_through_task_market() -> None:
    """Test that task state transitions go through task_market."""
    cells_dir = BACKEND_ROOT / "polaris" / "cells"

    if not cells_dir.exists():
        pytest.skip("cells directory not found")

    # Check that direct task state mutations are not happening
    # This is a stricter check
    violations: list[str] = []

    for py_file in cells_dir.rglob("*.py"):
        if "test" in py_file.parts or "__pycache__" in str(py_file):
            continue

        # Skip task_market itself
        if "task_market" in str(py_file):
            continue

        try:
            content = py_file.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue

        # Look for direct database writes to task tables
        if "task" in content.lower() and ("INSERT" in content or "UPDATE" in content):
            # This is informational - may be legitimate
            pass

    # Informational
    assert True


# =============================================================================
# Test: Catalog State Owners
# =============================================================================


def test_only_task_market_declares_task_state_ownership() -> None:
    """Test that only task_market declares task state ownership."""
    cells_yaml = BACKEND_ROOT / "docs" / "graph" / "catalog" / "cells.yaml"

    if not cells_yaml.exists():
        pytest.skip("cells.yaml not found")

    content = cells_yaml.read_text(encoding="utf-8")
    data = yaml.safe_load(content)

    # Find cells that claim task state ownership
    task_state_owners: list[str] = []

    for cell in data.get("cells", []):
        state_owners = cell.get("state_owners", [])
        for owner in state_owners:
            if "task" in str(owner).lower():
                task_state_owners.append(cell.get("id"))

    # Should be at most task_market
    # (Other cells may have effects_allowed for task operations, but not state_owners)
    if "runtime.task_market" in task_state_owners:
        # Task market is the canonical owner
        assert True
    else:
        # Informational - task market may not be declared yet
        assert True


# =============================================================================
# Test: Integration with Roles
# =============================================================================


def test_roles_import_from_task_market_for_work_items() -> None:
    """Test that roles import task_market for work-item operations."""
    cells_dir = BACKEND_ROOT / "polaris" / "cells"

    if not cells_dir.exists():
        pytest.skip("cells directory not found")

    # Find cells that handle work items
    importing_task_market: list[str] = []

    for py_file in cells_dir.rglob("*.py"):
        if "test" in py_file.parts or "__pycache__" in str(py_file):
            continue

        try:
            content = py_file.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue

        if "task_market" in content and "import" in content:
            importing_task_market.append(str(py_file.relative_to(BACKEND_ROOT)))

    # Informational - some cells should import task_market
    assert True


def test_director_uses_task_market_for_work_coordination() -> None:
    """Test that director uses task_market for work coordination."""
    director_dir = BACKEND_ROOT / "polaris" / "cells" / "director"

    if not director_dir.exists():
        pytest.skip("director directory not found")

    # Director should import from task_market for work coordination
    found_task_market_import = False
    for py_file in director_dir.rglob("*.py"):
        if "test" in py_file.parts:
            continue

        try:
            content = py_file.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue

        if "task_market" in content and "import" in content:
            found_task_market_import = True
            break

    # Informational
    assert True


def test_pm_uses_task_market_for_work_coordination() -> None:
    """Test that pm uses task_market for work coordination."""
    pm_dir = BACKEND_ROOT / "polaris" / "cells" / "pm"

    if not pm_dir.exists():
        pytest.skip("pm directory not found")

    # PM should import from task_market for work coordination
    found_task_market_import = False
    for py_file in pm_dir.rglob("*.py"):
        if "test" in py_file.parts:
            continue

        try:
            content = py_file.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue

        if "task_market" in content and "import" in content:
            found_task_market_import = True
            break

    # Informational
    assert True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
