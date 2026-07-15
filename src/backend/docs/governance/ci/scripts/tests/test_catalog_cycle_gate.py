"""Unit tests for the cross-cell cycle-detection rule (GATE-01).

These tests cover the ``no_new_cross_cell_cycle`` rule added to the catalog
governance gate. The rule is a preventive enhancement: it freezes the cycles
currently declared in ``cells.yaml`` as an allowlist and fails only on cycles
that are NEW relative to that allowlist.

Test layout (AAA, mocked at the filesystem boundary via temp workspaces):
  * SCC detection on synthetic graphs (2-cycle, multi-node SCC, acyclic).
  * Allowlist round-trip: writing then reading freezes the same fingerprints.
  * (a) all currently-known cycles in the real catalog pass (no new issue).
  * (b) a synthetic injected new cycle fails and is counted as a new issue.
"""

from __future__ import annotations

import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any

import yaml
from docs.governance.ci.scripts.run_catalog_governance_gate import (
    _RULE_NO_NEW_CROSS_CELL_CYCLE,
    CatalogCell,
    GovernanceIssue,
    _build_depends_on_graph,
    _check_no_new_cross_cell_cycle,
    _cycle_fingerprint,
    _load_cycle_allowlist,
    _load_cycle_allowlist_baselines,
    _strongly_connected_components,
    _write_cycle_allowlist,
)

_REPO_ROOT = Path(__file__).resolve().parents[5]
_REAL_CATALOG = _REPO_ROOT / "docs" / "graph" / "catalog" / "cells.yaml"


def _cell(cell_id: str, depends_on: tuple[str, ...] = ()) -> CatalogCell:
    """Build a minimal CatalogCell carrying only the fields the rule reads."""
    return CatalogCell(
        cell_id=cell_id,
        owned_paths=(),
        depends_on=depends_on,
        state_owners=(),
        effects_allowed=(),
    )


def _real_catalog_cells() -> list[CatalogCell]:
    """Load the live catalog into CatalogCell records for the realistic cases."""
    payload: Any = yaml.safe_load(_REAL_CATALOG.read_text(encoding="utf-8"))
    cells: list[CatalogCell] = []
    for item in payload["cells"]:
        deps = tuple(str(d).strip() for d in (item.get("depends_on") or []) if str(d).strip())
        cells.append(_cell(str(item["id"]).strip(), deps))
    return cells


# --------------------------------------------------------------------------- #
# SCC detection primitives
# --------------------------------------------------------------------------- #


def test_scc_detects_two_cycle() -> None:
    """A mutual A<->B dependency is reported as a single two-member component."""
    # Arrange
    cells = (_cell("a", ("b",)), _cell("b", ("a",)))

    # Act
    components = _strongly_connected_components(_build_depends_on_graph(cells))

    # Assert
    assert components == [("a", "b")]


def test_scc_detects_multi_node_cycle() -> None:
    """A 3-node ring A->B->C->A collapses to one component of all three nodes."""
    # Arrange
    cells = (_cell("a", ("b",)), _cell("b", ("c",)), _cell("c", ("a",)))

    # Act
    components = _strongly_connected_components(_build_depends_on_graph(cells))

    # Assert
    assert components == [("a", "b", "c")]


def test_scc_ignores_acyclic_graph() -> None:
    """A pure DAG produces no components of size > 1."""
    # Arrange
    cells = (_cell("a", ("b", "c")), _cell("b", ("c",)), _cell("c", ()))

    # Act
    components = _strongly_connected_components(_build_depends_on_graph(cells))

    # Assert
    assert components == []


def test_graph_drops_unknown_and_self_edges() -> None:
    """Edges to unknown cells and self-edges are excluded from the graph."""
    # Arrange
    cells = (_cell("a", ("a", "ghost", "b")), _cell("b", ()))

    # Act
    graph = _build_depends_on_graph(cells)

    # Assert
    assert graph == {"a": ("b",), "b": ()}


def test_cycle_fingerprint_is_order_independent() -> None:
    """The fingerprint depends on the member set, not the tuple ordering."""
    # Arrange
    forward = ("a", "b", "c")
    shuffled = ("c", "a", "b")

    # Act / Assert
    assert _cycle_fingerprint(forward) == _cycle_fingerprint(shuffled)
    assert _cycle_fingerprint(("a", "b")) != _cycle_fingerprint(("a", "b", "c"))


# --------------------------------------------------------------------------- #
# Allowlist round-trip
# --------------------------------------------------------------------------- #


def test_write_then_load_allowlist_round_trip() -> None:
    """Writing the allowlist freezes both the SCC members and internal edges."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Arrange
        repo_root = Path(tmpdir)
        cells = (_cell("a", ("b",)), _cell("b", ("a",)), _cell("c", ()))

        # Act
        _write_cycle_allowlist(repo_root, cells)
        loaded = _load_cycle_allowlist(repo_root)
        baselines = _load_cycle_allowlist_baselines(repo_root)

        # Assert
        assert loaded == {_cycle_fingerprint(("a", "b"))}
        assert len(baselines) == 1
        assert baselines[0].members == frozenset(("a", "b"))
        assert baselines[0].internal_edges == frozenset(("a -> b", "b -> a"))


def test_load_allowlist_missing_file_is_empty() -> None:
    """A missing allowlist file is fail-closed: an empty fingerprint set."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Arrange / Act
        loaded = _load_cycle_allowlist(Path(tmpdir))

        # Assert
        assert loaded == set()


# --------------------------------------------------------------------------- #
# (a) currently-known cycles pass / (b) a new cycle fails -- synthetic catalog
# --------------------------------------------------------------------------- #


def test_known_cycle_produces_no_issue() -> None:
    """(a) A cycle already in the allowlist raises no governance issue."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Arrange
        repo_root = Path(tmpdir)
        cells = (_cell("a", ("b",)), _cell("b", ("a",)))
        _write_cycle_allowlist(repo_root, cells)
        issues: list[GovernanceIssue] = []

        # Act
        _check_no_new_cross_cell_cycle(repo_root=repo_root, cells=cells, issues=issues)

        # Assert
        assert issues == []


def test_new_cycle_produces_issue() -> None:
    """(b) A cycle absent from the allowlist is reported as a new HIGH issue."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Arrange: freeze an allowlist with NO cycles, then introduce one.
        repo_root = Path(tmpdir)
        _write_cycle_allowlist(repo_root, (_cell("a", ()), _cell("b", ())))
        cells_with_cycle = (_cell("a", ("b",)), _cell("b", ("a",)))
        issues: list[GovernanceIssue] = []

        # Act
        _check_no_new_cross_cell_cycle(repo_root=repo_root, cells=cells_with_cycle, issues=issues)

        # Assert
        assert len(issues) == 1
        issue = issues[0]
        assert issue.rule_id == _RULE_NO_NEW_CROSS_CELL_CYCLE
        assert issue.severity == "high"
        assert "a" in issue.message and "b" in issue.message


def test_cycle_growing_new_member_is_flagged() -> None:
    """A previously-allowlisted cycle that absorbs a new cell is flagged as new."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Arrange: allowlist the 2-cycle a<->b only.
        repo_root = Path(tmpdir)
        _write_cycle_allowlist(repo_root, (_cell("a", ("b",)), _cell("b", ("a",))))
        # Now c joins the cycle: a->b->c->a.
        grown = (_cell("a", ("b",)), _cell("b", ("c",)), _cell("c", ("a",)))
        issues: list[GovernanceIssue] = []

        # Act
        _check_no_new_cross_cell_cycle(repo_root=repo_root, cells=grown, issues=issues)

        # Assert
        assert len(issues) == 1
        assert "c" in issues[0].message


def test_new_shortcut_edge_inside_allowlisted_scc_is_flagged() -> None:
    """An extra edge between existing SCC members fails even without member growth."""
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_root = Path(tmpdir)
        baseline = (
            _cell("a", ("b",)),
            _cell("b", ("c",)),
            _cell("c", ("a",)),
        )
        _write_cycle_allowlist(repo_root, baseline)
        with_shortcut = (
            _cell("a", ("b", "c")),
            _cell("b", ("c",)),
            _cell("c", ("a",)),
        )
        issues: list[GovernanceIssue] = []

        _check_no_new_cross_cell_cycle(repo_root=repo_root, cells=with_shortcut, issues=issues)

        assert len(issues) == 1
        assert issues[0].severity == "high"
        assert "a -> c" in issues[0].message


def test_allowlisted_cycle_shrinking_to_subset_is_convergence() -> None:
    """Removing edges may split one frozen SCC without creating a new cycle."""

    with tempfile.TemporaryDirectory() as tmpdir:
        repo_root = Path(tmpdir)
        original = (
            _cell("a", ("b",)),
            _cell("b", ("a", "c")),
            _cell("c", ("a",)),
        )
        _write_cycle_allowlist(repo_root, original)
        shrunk = (
            _cell("a", ("b",)),
            _cell("b", ("a",)),
            _cell("c", ()),
        )
        issues: list[GovernanceIssue] = []

        _check_no_new_cross_cell_cycle(repo_root=repo_root, cells=shrunk, issues=issues)

        assert issues == []


# --------------------------------------------------------------------------- #
# (a) currently-known cycles pass / (b) a new cycle fails -- REAL catalog
# --------------------------------------------------------------------------- #


def test_real_catalog_cycles_all_allowlisted() -> None:
    """(a) Every cycle declared in the live cells.yaml is in the shipped allowlist.

    This guards against the allowlist drifting out of date relative to the catalog:
    the rule must produce zero issues against the real, unmodified catalog.
    """
    # Arrange
    cells = _real_catalog_cells()
    issues: list[GovernanceIssue] = []

    # Act
    _check_no_new_cross_cell_cycle(repo_root=_REPO_ROOT, cells=cells, issues=issues)

    # Assert
    assert issues == []


def test_real_catalog_scc_rejects_new_internal_shortcut_edge() -> None:
    """A non-baseline edge between live baseline members is a HIGH governance failure."""
    baselines = _load_cycle_allowlist_baselines(_REPO_ROOT)
    assert baselines, "the live catalog must provide an SCC baseline"
    baseline = baselines[0]
    source = ""
    target = ""
    for candidate_source in sorted(baseline.members):
        for candidate_target in sorted(baseline.members):
            edge = f"{candidate_source} -> {candidate_target}"
            if candidate_source != candidate_target and edge not in baseline.internal_edges:
                source = candidate_source
                target = candidate_target
                break
        if source:
            break
    assert source and target, "the live SCC must have a non-baseline shortcut candidate"

    cells = _real_catalog_cells()
    for index, cell in enumerate(cells):
        if cell.cell_id == source:
            cells[index] = replace(cell, depends_on=(*cell.depends_on, target))
            break
    else:
        raise AssertionError(f"live catalog is missing baseline member {source}")
    issues: list[GovernanceIssue] = []

    _check_no_new_cross_cell_cycle(repo_root=_REPO_ROOT, cells=cells, issues=issues)

    assert len(issues) == 1
    assert issues[0].severity == "high"
    assert f"{source} -> {target}" in issues[0].message


def test_real_catalog_plus_injected_edge_fails() -> None:
    """(b) Injecting a brand-new disjoint cycle into the live catalog fails the rule.

    Two synthetic cells that depend on each other form an SCC absent from the
    allowlist, so exactly one new issue is reported while the real cycles stay clean.
    """
    # Arrange
    cells = _real_catalog_cells()
    cells.append(_cell("synthetic.alpha", ("synthetic.beta",)))
    cells.append(_cell("synthetic.beta", ("synthetic.alpha",)))
    issues: list[GovernanceIssue] = []

    # Act
    _check_no_new_cross_cell_cycle(repo_root=_REPO_ROOT, cells=cells, issues=issues)

    # Assert
    assert len(issues) == 1
    assert "synthetic.alpha" in issues[0].message
    assert "synthetic.beta" in issues[0].message
