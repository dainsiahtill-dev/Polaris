"""Unit tests for check_cell_internal_fence.py."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from docs.governance.ci.scripts.check_cell_internal_fence import (
    CellInternalFenceChecker,
    Violation,
    main,
)


@pytest.fixture
def minimal_workspace(tmp_path: Path) -> Path:
    """Create a minimal workspace with cells.yaml and a few Python files."""
    workspace = tmp_path / "repo"
    workspace.mkdir()

    catalog = workspace / "docs" / "graph" / "catalog"
    catalog.mkdir(parents=True)
    cells_yaml = catalog / "cells.yaml"
    cells_yaml.write_text(
        """
version: 1
cells:
  - id: alpha.core
    owned_paths:
      - polaris/cells/alpha/core/**
  - id: beta.helper
    owned_paths:
      - polaris/cells/beta/helper/**
""",
        encoding="utf-8",
    )

    polaris = workspace / "polaris"
    polaris.mkdir()

    # Alpha cell files
    alpha = polaris / "cells" / "alpha" / "core"
    alpha.mkdir(parents=True)
    (alpha / "public.py").write_text("x = 1\n", encoding="utf-8")
    (alpha / "internal.py").write_text("y = 2\n", encoding="utf-8")

    # Beta cell files
    beta = polaris / "cells" / "beta" / "helper"
    beta.mkdir(parents=True)
    (beta / "public.py").write_text("x = 1\n", encoding="utf-8")
    (beta / "internal.py").write_text("z = 3\n", encoding="utf-8")

    return workspace


def test_no_violations(minimal_workspace: Path) -> None:
    """No violations when cells only use their own internals."""
    checker = CellInternalFenceChecker(workspace=minimal_workspace, mode="audit-only")
    report = checker.run()
    assert report.violation_count == 0
    assert report.exit_code == 0


def test_cross_cell_violation(minimal_workspace: Path) -> None:
    """Cross-cell internal import is flagged."""
    beta_file = minimal_workspace / "polaris" / "cells" / "beta" / "helper" / "public.py"
    beta_file.write_text("from polaris.cells.alpha.core.internal import y\n", encoding="utf-8")

    checker = CellInternalFenceChecker(workspace=minimal_workspace, mode="audit-only")
    report = checker.run()
    assert report.violation_count == 1
    v = report.violations[0]
    assert v.source_cell == "beta.helper"
    assert v.target_cell == "alpha.core"
    assert v.reason == "cross_cell"


def test_unowned_source_violation(minimal_workspace: Path) -> None:
    """Unowned file importing an internal module is flagged."""
    unowned = minimal_workspace / "polaris" / "application" / "admin.py"
    unowned.parent.mkdir(parents=True)
    unowned.write_text("from polaris.cells.alpha.core.internal import y\n", encoding="utf-8")

    checker = CellInternalFenceChecker(workspace=minimal_workspace, mode="audit-only")
    report = checker.run()
    assert any(v.reason == "unowned_source" for v in report.violations)


def test_orphan_target_violation(minimal_workspace: Path) -> None:
    """Import of an internal module not owned by any cell is flagged."""
    alpha_public = minimal_workspace / "polaris" / "cells" / "alpha" / "core" / "public.py"
    # polaris.cells.gamma.util.internal.helper is not owned by any cell
    alpha_public.write_text(
        "from polaris.cells.gamma.util.internal.helper import x\n",
        encoding="utf-8",
    )

    checker = CellInternalFenceChecker(workspace=minimal_workspace, mode="audit-only")
    report = checker.run()
    assert any(v.reason == "orphan_target" for v in report.violations)


def test_fail_on_new_with_baseline(minimal_workspace: Path) -> None:
    """fail-on-new exits 0 when no new violations appear."""
    beta_file = minimal_workspace / "polaris" / "cells" / "beta" / "helper" / "public.py"
    beta_file.write_text("from polaris.cells.alpha.core.internal import y\n", encoding="utf-8")

    baseline_path = minimal_workspace / "baseline.json"
    baseline_path.write_text(json.dumps({"violation_fingerprints": []}), encoding="utf-8")

    checker = CellInternalFenceChecker(
        workspace=minimal_workspace,
        mode="fail-on-new",
        baseline_path=baseline_path,
    )
    report = checker.run()
    assert report.new_violation_count == 1
    assert report.exit_code == 1


def test_fail_on_new_no_new_violations(minimal_workspace: Path) -> None:
    """fail-on-new exits 0 when baseline covers all violations."""
    beta_file = minimal_workspace / "polaris" / "cells" / "beta" / "helper" / "public.py"
    beta_file.write_text("from polaris.cells.alpha.core.internal import y\n", encoding="utf-8")

    checker = CellInternalFenceChecker(workspace=minimal_workspace, mode="audit-only")
    report = checker.run()

    baseline_path = minimal_workspace / "baseline.json"
    checker.write_baseline(baseline_path, report)

    checker2 = CellInternalFenceChecker(
        workspace=minimal_workspace,
        mode="fail-on-new",
        baseline_path=baseline_path,
    )
    report2 = checker2.run()
    assert report2.new_violation_count == 0
    assert report2.exit_code == 0


def test_main_audit_only(minimal_workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """main() prints JSON and returns 0 in audit-only mode."""
    code = main(
        [
            "--workspace",
            str(minimal_workspace),
            "--mode",
            "audit-only",
        ]
    )
    assert code == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["mode"] == "audit-only"
    assert data["exit_code"] == 0


def test_main_fail_on_new(minimal_workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """main() returns non-zero in fail-on-new mode when violations exist."""
    beta_file = minimal_workspace / "polaris" / "cells" / "beta" / "helper" / "public.py"
    beta_file.write_text("from polaris.cells.alpha.core.internal import y\n", encoding="utf-8")

    code = main(
        [
            "--workspace",
            str(minimal_workspace),
            "--mode",
            "fail-on-new",
        ]
    )
    assert code == 1
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["mode"] == "fail-on-new"
    assert data["new_violation_count"] == 1


def test_violation_fingerprint_stable() -> None:
    """Fingerprints must be deterministic."""
    v1 = Violation(
        source_file="a.py",
        source_cell="c1",
        line=10,
        import_stmt="from x import y",
        target_module="x",
        target_cell="c2",
        reason="cross_cell",
    )
    v2 = Violation(
        source_file="a.py",
        source_cell="c1",
        line=10,
        import_stmt="from x import y",
        target_module="x",
        target_cell="c2",
        reason="cross_cell",
    )
    assert v1.fingerprint() == v2.fingerprint()
