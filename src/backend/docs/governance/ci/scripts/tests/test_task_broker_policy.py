"""Tests for task-market single-broker policy wiring."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from docs.governance.ci.scripts import fitness_rule_checker
from docs.governance.ci.scripts.check_task_market_single_broker import TaskMarketSingleBrokerChecker
from docs.governance.ci.scripts.task_broker_policy import evaluate_task_broker


def _write_task_market_cell(workspace: Path) -> None:
    """Write the minimal runtime.task_market Cell fixture."""
    task_market_dir = workspace / "polaris" / "cells" / "runtime" / "task_market"
    task_market_dir.mkdir(parents=True, exist_ok=True)
    (task_market_dir / "cell.yaml").write_text("id: runtime.task_market\n", encoding="utf-8")


def _write_source(workspace: Path, relative_path: str, content: str) -> None:
    """Write a source fixture into a temporary workspace."""
    path = workspace / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_catalog(workspace: Path, cells: list[dict[str, Any]]) -> None:
    """Write a cells.yaml catalog fixture."""
    catalog_path = workspace / "docs" / "graph" / "catalog" / "cells.yaml"
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    catalog_path.write_text(yaml.safe_dump({"cells": cells}, sort_keys=False), encoding="utf-8")


def test_task_broker_entrypoints_use_canonical_policy(tmp_path: Path) -> None:
    """The standalone and aggregate entrypoints must match the policy."""
    _write_task_market_cell(tmp_path)
    _write_source(
        tmp_path,
        "polaris/cells/runtime/execution_broker/internal/broker.py",
        "def route(execution_broker: object) -> object:\n    return execution_broker.claim()\n",
    )

    policy = evaluate_task_broker(tmp_path)
    standalone = TaskMarketSingleBrokerChecker(tmp_path).check()
    aggregate = fitness_rule_checker.FitnessRuleChecker(tmp_path).check_task_broker()

    assert policy.passed is False
    assert standalone.passed is False
    assert aggregate.passed is False
    assert aggregate.rule_id == policy.rule_id == standalone.rule_id
    assert aggregate.violations == list(policy.violations) == standalone.violations
    assert any("Execution broker task routing" in violation for violation in aggregate.violations)


def test_task_broker_policy_requires_task_market_cell(tmp_path: Path) -> None:
    """Missing runtime.task_market is a hard failure."""
    result = evaluate_task_broker(tmp_path)

    assert result.passed is False
    assert result.violations == ("runtime.task_market cell not found or incomplete",)


def test_task_broker_policy_reports_catalog_warnings(tmp_path: Path) -> None:
    """Catalog dependencies are warnings unless they prove a broker bypass."""
    _write_task_market_cell(tmp_path)
    _write_catalog(
        tmp_path,
        [
            {
                "id": "director.execution",
                "depends_on": [],
            }
        ],
    )

    result = evaluate_task_broker(tmp_path)

    assert result.passed is True
    assert result.violations == ()
    assert result.warnings == ("Cell 'director.execution' missing runtime.task_market in depends_on",)


def test_task_broker_policy_accepts_clean_workspace(tmp_path: Path) -> None:
    """A workspace with task_market and clean broker/catalog evidence passes."""
    _write_task_market_cell(tmp_path)
    _write_catalog(
        tmp_path,
        [
            {
                "id": "director.execution",
                "depends_on": ["runtime.task_market"],
            }
        ],
    )

    result = evaluate_task_broker(tmp_path)

    assert result.passed is True
    assert result.violations == ()
    assert "Graph relations correctly route through task_market" in result.evidence
