"""Unit tests for the file-size budget governance gate (Phase 0 regrowth guard)."""

from __future__ import annotations

from pathlib import Path

from docs.governance.ci.scripts.check_file_size_budget import (
    FileSizeViolation,
    find_stale_baseline_entries,
    find_violations,
    iter_source_files,
    load_baseline,
    write_baseline,
)

_EXCLUDED_PARTS = frozenset({"tests", "generated", "__pycache__", "docs"})
_EXCLUDED_FILENAMES = frozenset({"test_helper.py"})


def _write(path: Path, lines: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(f"line {i}" for i in range(lines)) + "\n", encoding="utf-8")
    return path


def test_iter_source_files_excludes_tests_generated_and_docs(tmp_path: Path) -> None:
    """The gate must measure product source only — tests/generated/docs are out of scope."""
    _write(tmp_path / "polaris" / "cell" / "service.py", 10)
    _write(tmp_path / "polaris" / "tests" / "test_service.py", 10)
    _write(tmp_path / "polaris" / "generated" / "desc.py", 10)
    _write(tmp_path / "docs" / "governance" / "gate.py", 10)
    _write(tmp_path / "polaris" / "module" / "test_service.py", 10)

    found = {
        p.relative_to(tmp_path).as_posix()
        for p in iter_source_files(
            tmp_path,
            excluded_parts=_EXCLUDED_PARTS,
            excluded_filename_prefixes=("test_",),
        )
    }

    assert found == {"polaris/cell/service.py"}


def test_clean_when_every_file_is_under_budget(tmp_path: Path) -> None:
    _write(tmp_path / "polaris" / "small.py", 100)

    violations = find_violations(
        tmp_path,
        budget=2000,
        baseline={},
        excluded_parts=_EXCLUDED_PARTS,
        excluded_filename_prefixes=("test_",),
    )

    assert violations == []


def test_fails_on_new_oversized_file_absent_from_baseline(tmp_path: Path) -> None:
    """A new file over budget with no baseline entry is a violation."""
    _write(tmp_path / "polaris" / "big.py", 2001)

    violations = find_violations(
        tmp_path,
        budget=2000,
        baseline={},
        excluded_parts=_EXCLUDED_PARTS,
        excluded_filename_prefixes=("test_",),
    )

    assert len(violations) == 1
    assert violations[0].kind == "new_oversized"
    assert violations[0].lines == 2001
    assert violations[0].budget == 2000


def test_grandfathers_oversized_file_at_frozen_baseline_count(tmp_path: Path) -> None:
    """Existing offenders are grandfathered at their exact frozen count — no growth allowed."""
    _write(tmp_path / "polaris" / "god_class.py", 2500)

    violations = find_violations(
        tmp_path,
        budget=2000,
        baseline={"polaris/god_class.py": 2500},
        excluded_parts=_EXCLUDED_PARTS,
        excluded_filename_prefixes=("test_",),
    )

    assert violations == []


def test_fails_when_grandfathered_file_grows_by_even_one_line(tmp_path: Path) -> None:
    """The regrowth guard: any growth beyond the frozen baseline count fails."""
    _write(tmp_path / "polaris" / "god_class.py", 2501)

    violations = find_violations(
        tmp_path,
        budget=2000,
        baseline={"polaris/god_class.py": 2500},
        excluded_parts=_EXCLUDED_PARTS,
        excluded_filename_prefixes=("test_",),
    )

    assert len(violations) == 1
    assert violations[0].kind == "regrowth"
    assert violations[0].baseline_lines == 2500
    assert violations[0].lines == 2501


def test_shrinking_a_grandfathered_file_is_not_a_violation(tmp_path: Path) -> None:
    """Successful shrinks must not trip the gate — only growth does."""
    _write(tmp_path / "polaris" / "god_class.py", 1800)

    violations = find_violations(
        tmp_path,
        budget=2000,
        baseline={"polaris/god_class.py": 2500},
        excluded_parts=_EXCLUDED_PARTS,
        excluded_filename_prefixes=("test_",),
    )

    assert violations == []


def test_stale_baseline_entries_are_reported_for_cleanup(tmp_path: Path) -> None:
    """Baseline rows whose file vanished or dropped under budget are eligible for removal."""
    _write(tmp_path / "polaris" / "shrunk.py", 500)  # now under budget
    # "gone.py" is absent from the tree

    stale = find_stale_baseline_entries(
        tmp_path,
        budget=2000,
        baseline={"polaris/shrunk.py": 2500, "polaris/gone.py": 2500},
        excluded_parts=_EXCLUDED_PARTS,
        excluded_filename_prefixes=("test_",),
    )

    assert set(stale) == {"polaris/shrunk.py", "polaris/gone.py"}


def test_load_baseline_returns_empty_when_file_missing(tmp_path: Path) -> None:
    assert load_baseline(tmp_path / "absent.json") == {}


def test_write_baseline_snapshots_current_oversized_counts(tmp_path: Path) -> None:
    """`--update-baseline` records every file over budget at its current count."""
    _write(tmp_path / "polaris" / "big.py", 3000)
    _write(tmp_path / "polaris" / "small.py", 100)

    baseline_path = tmp_path / "baseline.json"
    snapshot = write_baseline(
        tmp_path,
        baseline_path,
        budget=2000,
        excluded_parts=_EXCLUDED_PARTS,
        excluded_filename_prefixes=("test_",),
    )

    assert snapshot == {"polaris/big.py": 3000}
    assert load_baseline(baseline_path) == {"polaris/big.py": 3000}


def test_violation_carries_actionable_path_for_reports(tmp_path: Path) -> None:
    """A violation must expose a repo-relative path so reports point at the right file."""
    _write(tmp_path / "polaris" / "cell" / "big.py", 4000)

    violations = find_violations(
        tmp_path,
        budget=2000,
        baseline={},
        excluded_parts=_EXCLUDED_PARTS,
        excluded_filename_prefixes=("test_",),
    )

    assert isinstance(violations[0], FileSizeViolation)
    assert violations[0].rel_path == "polaris/cell/big.py"
