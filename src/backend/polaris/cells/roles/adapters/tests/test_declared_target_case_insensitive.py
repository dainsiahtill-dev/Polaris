"""F26: declared-target quality scan must match case-insensitively.

The materialization-quality scan declared a target ``readme.md`` missing while
the Director had written ``README.md`` (case-sensitive ``Path.is_file`` on Linux).
The write-side already collapses case variants, so the scan disagreeing drove a
spurious repair loop that never cleared and failed an otherwise-runnable product
(observed live: L2-12, 2026-06-16). This mirrors F19/F20 existence-gate matching.
"""

from __future__ import annotations

from pathlib import Path

from polaris.cells.roles.adapters.internal.director.execute_method import (
    _case_insensitive_file_match,
    _declared_target_file_quality_errors,
)


def _write(p: Path, name: str, body: str = "x") -> None:
    (p / name).write_text(body, encoding="utf-8")


def test_case_variant_declared_target_not_reported_missing(tmp_path: Path) -> None:
    # Disk has README.md; task declares readme.md -> must NOT be "missing".
    _write(tmp_path, "README.md", "# hi")
    task = {"target_files": ["readme.md"]}
    errors = _declared_target_file_quality_errors(workspace_full=str(tmp_path), task=task, workspace_name="")
    assert errors == []


def test_exact_case_declared_target_not_reported_missing(tmp_path: Path) -> None:
    _write(tmp_path, "README.md", "# hi")
    task = {"target_files": ["README.md"]}
    errors = _declared_target_file_quality_errors(workspace_full=str(tmp_path), task=task, workspace_name="")
    assert errors == []


def test_genuinely_missing_target_still_reported(tmp_path: Path) -> None:
    # No case variant exists -> the scan must still fail closed.
    _write(tmp_path, "index.html", "<html></html>")
    task = {"target_files": ["main.py"]}
    errors = _declared_target_file_quality_errors(workspace_full=str(tmp_path), task=task, workspace_name="")
    assert any("main.py" in e for e in errors)


def test_case_insensitive_file_match_helper(tmp_path: Path) -> None:
    _write(tmp_path, "README.md", "# hi")
    assert _case_insensitive_file_match(tmp_path / "readme.md") is True
    assert _case_insensitive_file_match(tmp_path / "README.md") is True
    assert _case_insensitive_file_match(tmp_path / "missing.md") is False


def test_case_insensitive_match_requires_a_file_not_a_dir(tmp_path: Path) -> None:
    # A directory whose name case-matches must NOT satisfy a file target.
    (tmp_path / "Assets").mkdir()
    assert _case_insensitive_file_match(tmp_path / "assets") is False
