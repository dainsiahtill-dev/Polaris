"""Pure policy for task-market single-broker governance checks."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

RULE_ID = "task_market_is_single_business_broker"
PEER_ROLE_CELL_TERMS = (
    "pm",
    "chief_engineer",
    "director",
    "qa",
    "roles.pm",
    "roles.chief_engineer",
    "roles.director",
    "roles.qa",
)
EXECUTION_BROKER_TASK_ROUTING_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"ExecutionBroker\.publish\("),
    re.compile(r"execution_broker\.claim\("),
    re.compile(r"execution_broker\.acquire\("),
    re.compile(r"from.*execution_broker.*import.*publish", re.DOTALL),
    re.compile(r"from.*execution_broker.*import.*claim", re.DOTALL),
    re.compile(r"from.*execution_broker.*import.*acquire", re.DOTALL),
    re.compile(r"ExecutionBroker\("),
)
TASK_MARKET_USAGE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"TaskMarket"),
    re.compile(r"task_market"),
    re.compile(r"WorkItem"),
    re.compile(r"work_item"),
    re.compile(r"publish_work_item"),
    re.compile(r"claim_work_item"),
)


@dataclass(frozen=True)
class TaskBrokerPolicyResult:
    """Evaluation result for task-market single-broker governance."""

    rule_id: str
    passed: bool
    evidence: tuple[str, ...] = ()
    violations: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


def _relative_path(workspace: Path, path: Path) -> str:
    """Return a stable repository-relative path."""
    try:
        return str(path.relative_to(workspace)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def _is_test_path(path: Path) -> bool:
    """Return true for test files and test directories."""
    return any(part in {"test", "tests"} for part in path.parts) or path.name.startswith("test_")


def _read_text(path: Path) -> str | None:
    """Read UTF-8 text, returning None when it cannot be inspected."""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _task_market_exists(workspace: Path) -> bool:
    """Return true when runtime.task_market Cell exists with cell.yaml."""
    task_market_dir = workspace / "polaris" / "cells" / "runtime" / "task_market"
    return task_market_dir.exists() and (task_market_dir / "cell.yaml").exists()


def _find_execution_broker_routing(workspace: Path) -> tuple[str, ...]:
    """Return forbidden business-task routing usages under execution_broker."""
    execution_broker_dir = workspace / "polaris" / "cells" / "runtime" / "execution_broker"
    if not execution_broker_dir.exists():
        return ()

    violations: list[str] = []
    for py_file in execution_broker_dir.rglob("*.py"):
        if _is_test_path(py_file):
            continue

        content = _read_text(py_file)
        if content is None or ("execution_broker" not in content and "ExecutionBroker" not in content):
            continue

        rel_path = _relative_path(workspace, py_file)
        for pattern in EXECUTION_BROKER_TASK_ROUTING_PATTERNS:
            for match in pattern.finditer(content):
                line_num = content[: match.start()].count("\n") + 1
                violations.append(f"Execution broker task routing at {rel_path}:{line_num}: {match.group()}")
    return tuple(violations)


def _find_peer_role_execution_broker_usage(workspace: Path) -> tuple[str, ...]:
    """Return peer-role files that use execution_broker routing without task_market."""
    cells_dir = workspace / "polaris" / "cells"
    peer_dirs = (
        cells_dir / "pm",
        cells_dir / "director",
        cells_dir / "chief_engineer",
        cells_dir / "qa",
    )
    warnings: list[str] = []

    for peer_dir in peer_dirs:
        if not peer_dir.exists():
            continue
        for py_file in peer_dir.rglob("*.py"):
            if _is_test_path(py_file):
                continue

            content = _read_text(py_file)
            if content is None:
                continue
            has_execution_broker_routing = any(
                pattern.search(content) for pattern in EXECUTION_BROKER_TASK_ROUTING_PATTERNS
            )
            if not has_execution_broker_routing:
                continue

            uses_task_market = any(pattern.search(content) for pattern in TASK_MARKET_USAGE_PATTERNS)
            if not uses_task_market:
                warnings.append(f"Peer role file does not use task_market: {_relative_path(workspace, py_file)}")

    return tuple(warnings)


def _load_catalog(workspace: Path) -> tuple[Mapping[str, Any] | None, str | None]:
    """Load cells.yaml, returning an error message instead of raising."""
    cells_yaml_path = workspace / "docs" / "graph" / "catalog" / "cells.yaml"
    if not cells_yaml_path.exists():
        return None, "cells.yaml not found"
    try:
        with cells_yaml_path.open(encoding="utf-8") as stream:
            data = yaml.safe_load(stream)
    except (OSError, yaml.YAMLError) as exc:
        return None, f"Error parsing cells.yaml: {exc}"
    if not isinstance(data, Mapping):
        return None, "cells.yaml must contain a mapping"
    return data, None


def _check_graph_relations(workspace: Path) -> tuple[tuple[str, ...], bool]:
    """Return graph-relation warnings and whether catalog evidence was checked."""
    catalog, error = _load_catalog(workspace)
    if error is not None or catalog is None:
        return (error or "cells.yaml unavailable",), False

    raw_cells = catalog.get("cells", [])
    if not isinstance(raw_cells, list):
        return ("cells.yaml field 'cells' must be a list",), False

    warnings: list[str] = []
    for raw_cell in raw_cells:
        if not isinstance(raw_cell, Mapping):
            continue

        cell_id = str(raw_cell.get("id", ""))
        if not any(term in cell_id for term in PEER_ROLE_CELL_TERMS):
            continue

        raw_depends_on = raw_cell.get("depends_on", [])
        depends_on = raw_depends_on if isinstance(raw_depends_on, list) else []
        if "runtime.task_market" not in depends_on and "task_market" not in depends_on:
            warnings.append(f"Cell '{cell_id}' missing runtime.task_market in depends_on")

    return tuple(warnings), True


def evaluate_task_broker(workspace: Path) -> TaskBrokerPolicyResult:
    """Evaluate whether runtime.task_market is the single business broker.

    The policy treats runtime.task_market presence and execution_broker business
    routing as hard checks. Peer-role files and catalog relations produce
    warnings because they reveal migration debt without proving a bypass by
    themselves.

    Complexity:
        O(f * p + c) time for scanned files, routing patterns, and catalog
        cells. O(v + w) space for emitted violations and warnings.
    """
    evidence: list[str] = []
    violations: list[str] = []
    warnings: list[str] = []

    if not _task_market_exists(workspace):
        return TaskBrokerPolicyResult(
            rule_id=RULE_ID,
            passed=False,
            violations=("runtime.task_market cell not found or incomplete",),
        )
    evidence.append("runtime.task_market cell exists")

    broker_violations = _find_execution_broker_routing(workspace)
    violations.extend(broker_violations)
    if not broker_violations:
        evidence.append("execution_broker does not have business task routing")

    warnings.extend(_find_peer_role_execution_broker_usage(workspace))
    graph_warnings, graph_checked = _check_graph_relations(workspace)
    warnings.extend(graph_warnings)
    if graph_checked and not graph_warnings:
        evidence.append("Graph relations correctly route through task_market")

    return TaskBrokerPolicyResult(
        rule_id=RULE_ID,
        passed=not violations,
        evidence=tuple(evidence),
        violations=tuple(violations),
        warnings=tuple(warnings),
    )
