"""Traceability consistency governance gate.

This script loads the latest traceability matrix for a workspace and
validates core invariants including ancestor checks, blueprint approval,
and version consistency between docs and blueprints.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class GateResult:
    """Result of a governance gate execution."""

    gate: str
    passed: bool
    errors: list[str]


def _find_latest_matrix(workspace: str) -> Path | None:
    """Locate the most recently modified traceability matrix file."""
    trace_dir = Path(workspace) / "runtime" / "traceability"
    if not trace_dir.exists():
        return None
    matrices = sorted(trace_dir.glob("*.matrix.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return matrices[0] if matrices else None


def load_latest_traceability_matrix(workspace: str) -> dict[str, Any] | None:
    """Load the latest traceability matrix dictionary from disk."""
    latest = _find_latest_matrix(workspace)
    if latest is None:
        return None
    with open(latest, encoding="utf-8") as f:
        data: dict[str, Any] = json.load(f)
        return data


def _ancestors(matrix: dict[str, Any], node_id: str) -> set[str]:
    """Return ancestor node IDs for a given node using BFS over links."""
    ancestor_ids: set[str] = set()
    frontier: set[str] = {node_id}
    links = matrix.get("links", [])
    while frontier:
        next_frontier: set[str] = set()
        for link in links:
            if isinstance(link, dict) and link.get("target") in frontier:
                source = str(link.get("source") or "").strip()
                if source and source not in ancestor_ids:
                    next_frontier.add(source)
        ancestor_ids |= next_frontier
        frontier = next_frontier
    return ancestor_ids


def _node_kind(matrix: dict[str, Any], node_id: str) -> str:
    """Return the kind of a node by its ID."""
    for node in matrix.get("nodes", []):
        if isinstance(node, dict) and node.get("node_id") == node_id:
            return str(node.get("kind") or "").strip()
    return ""


def _load_blueprint(workspace: str, blueprint_id: str) -> dict[str, Any] | None:
    """Load a blueprint JSON directly from the workspace runtime path."""
    p = Path(workspace) / "runtime" / "blueprints" / f"{blueprint_id}.json"
    if not p.exists():
        return None
    try:
        with open(p, encoding="utf-8") as f:
            data: dict[str, Any] = json.load(f)
            return data
    except (json.JSONDecodeError, OSError, TypeError):
        return None


def _check_gate14(workspace: str, matrix: dict[str, Any], errors: list[str]) -> None:
    """Check that every commit references an approved blueprint."""
    for node in matrix.get("nodes", []):
        if not isinstance(node, dict):
            continue
        if str(node.get("kind") or "").strip() != "commit":
            continue
        metadata = node.get("metadata") or {}
        if not isinstance(metadata, dict):
            metadata = {}
        blueprint_id = str(metadata.get("blueprint_id") or "").strip()
        external_id = str(node.get("external_id") or "").strip()
        if not blueprint_id:
            errors.append(f"Commit {external_id} is missing blueprint_id in metadata")
            continue
        blueprint = _load_blueprint(workspace, blueprint_id)
        if blueprint is None:
            errors.append(
                f"Commit {external_id} references unapproved or missing blueprint {blueprint_id}"
            )
            continue
        status = str(blueprint.get("status") or "").strip().lower()
        if status != "approved":
            errors.append(
                f"Commit {external_id} references unapproved or missing blueprint {blueprint_id}"
            )


def _check_gate15(matrix: dict[str, Any], errors: list[str]) -> None:
    """Check blueprint version does not lag behind doc version."""
    for node in matrix.get("nodes", []):
        if not isinstance(node, dict):
            continue
        if str(node.get("kind") or "").strip() != "blueprint":
            continue
        metadata = node.get("metadata") or {}
        if not isinstance(metadata, dict):
            metadata = {}
        if metadata.get("no_impact") is True:
            continue
        doc_version = metadata.get("doc_version")
        blueprint_version = metadata.get("blueprint_version")
        external_id = str(node.get("external_id") or "").strip()
        if doc_version is None or blueprint_version is None:
            continue
        try:
            doc_ver = int(doc_version)
            bp_ver = int(blueprint_version)
        except (ValueError, TypeError):
            continue
        if bp_ver < doc_ver:
            errors.append(
                f"Blueprint {external_id} version {bp_ver} lags behind doc version {doc_ver}"
            )


def run_traceability_gate(workspace: str) -> GateResult:
    """Run the traceability consistency gate.

    Args:
        workspace: Workspace root path.

    Returns:
        GateResult indicating pass/fail and any error messages.
    """
    matrix = load_latest_traceability_matrix(workspace)
    if matrix is None:
        return GateResult(
            gate="traceability_consistency",
            passed=True,
            errors=[],
        )

    errors: list[str] = []
    nodes = [n for n in matrix.get("nodes", []) if isinstance(n, dict)]

    # Check 1: every task node has a doc ancestor
    for node in nodes:
        if str(node.get("kind") or "").strip() == "task":
            ancestors = _ancestors(matrix, str(node.get("node_id") or "").strip())
            if not any(_node_kind(matrix, aid) == "doc" for aid in ancestors):
                errors.append(f"Task {node.get('external_id')} has no doc ancestor")

    # Check 2: every blueprint node has a task ancestor
    for node in nodes:
        if str(node.get("kind") or "").strip() == "blueprint":
            ancestors = _ancestors(matrix, str(node.get("node_id") or "").strip())
            if not any(_node_kind(matrix, aid) == "task" for aid in ancestors):
                errors.append(f"Blueprint {node.get('external_id')} has no task ancestor")

    # Check 3: every commit node has a blueprint ancestor
    for node in nodes:
        if str(node.get("kind") or "").strip() == "commit":
            ancestors = _ancestors(matrix, str(node.get("node_id") or "").strip())
            if not any(_node_kind(matrix, aid) == "blueprint" for aid in ancestors):
                errors.append(f"Commit {node.get('external_id')} has no blueprint ancestor")

    _check_gate14(workspace, matrix, errors)
    _check_gate15(matrix, errors)

    return GateResult(
        gate="traceability_consistency",
        passed=len(errors) == 0,
        errors=errors,
    )


def main() -> None:
    """CLI entry point for the traceability gate."""
    import argparse

    parser = argparse.ArgumentParser(description="Traceability consistency gate")
    parser.add_argument("--workspace", required=True, help="Workspace root path")
    args = parser.parse_args()
    result = run_traceability_gate(args.workspace)
    print(json.dumps({"gate": result.gate, "passed": result.passed, "errors": result.errors}, indent=2))
    raise SystemExit(0 if result.passed else 1)


if __name__ == "__main__":
    main()
