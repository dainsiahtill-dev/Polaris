"""A repair cannot erase required delivery bytes and claim convergence."""

from pathlib import Path

import pytest
from polaris.cells.roles.adapters.internal.director.artifact_quality_diagnostics import (
    _filter_satisfied_declared_target_missing_errors,
)
from polaris.cells.roles.adapters.internal.director.execute_method._helpers import (
    _quality_repair_progress_evidence,
)
from polaris.cells.roles.adapters.internal.director.quality_gate import (
    _build_materialization_quality_repair_message,
    _declared_target_file_quality_findings,
    _missing_materialization_quality_repair_target_files,
)


@pytest.mark.parametrize("filename", ["index.html", "main.ts", "main.py"])
def test_empty_declared_target_stays_failed_and_is_recoverable(tmp_path: Path, filename: str) -> None:
    (tmp_path / filename).write_text("", encoding="utf-8")
    task = {"target_files": [filename], "scope_paths": [filename]}
    errors, issues = _declared_target_file_quality_findings(workspace_full=str(tmp_path), task=task)
    assert errors == [f"Artifact quality scan failed: declared target file empty '{filename}'"]
    assert issues[0]["code"] == "declared_target_empty"
    assert issues[0]["path"] == filename
    assert _missing_materialization_quality_repair_target_files(task, str(tmp_path), errors, issues) == [filename]
    assert _filter_satisfied_declared_target_missing_errors(errors, str(tmp_path)) == errors


def test_full_file_erasure_cannot_count_as_diagnostic_convergence(tmp_path: Path) -> None:
    before = "<html><script>const garden = 1;"
    (tmp_path / "index.html").write_text("", encoding="utf-8")
    errors, _ = _declared_target_file_quality_findings(
        workspace_full=str(tmp_path), task={"target_files": ["index.html"]}
    )
    progress = _quality_repair_progress_evidence(
        before_files={"index.html": before},
        after_files={"index.html": ""},
        before_errors=["index.html: truncated/incomplete HTML: missing </html> closing tag"],
        after_errors=errors,
        before_missing_count=0,
        after_missing_count=len(errors),
        successful_write_paths=["index.html"],
    )
    assert progress["status"] != "converged"
    assert progress["effective_progress"] is False
    assert progress["errors_after"] == 1


def test_real_replacement_clears_empty_evidence_without_changing_contract(tmp_path: Path) -> None:
    task = {"target_files": ["index.html"]}
    (tmp_path / "index.html").write_text("", encoding="utf-8")
    empty_errors, _ = _declared_target_file_quality_findings(workspace_full=str(tmp_path), task=task)
    (tmp_path / "index.html").write_text("<html><body>garden</body></html>", encoding="utf-8")
    errors, issues = _declared_target_file_quality_findings(workspace_full=str(tmp_path), task=task)
    assert errors == [] and issues == ()
    assert _filter_satisfied_declared_target_missing_errors(empty_errors, str(tmp_path)) == []
    assert _missing_materialization_quality_repair_target_files(task, str(tmp_path), errors, issues) == []
    assert task == {"target_files": ["index.html"]}


def test_undeclared_empty_marker_does_not_restrict_valid_owned_edit(tmp_path: Path) -> None:
    (tmp_path / "__init__.py").write_text("", encoding="utf-8")
    # Removing an unused import still leaves a real declared source artifact.
    (tmp_path / "main.py").write_text("def main():\n    return 1\n", encoding="utf-8")
    errors, issues = _declared_target_file_quality_findings(
        workspace_full=str(tmp_path), task={"target_files": ["main.py"]}
    )
    assert errors == [] and issues == ()
    assert (tmp_path / "__init__.py").read_bytes() == b""


def test_case_variant_empty_diagnostic_is_not_cleared_by_existence(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("", encoding="utf-8")
    errors = ["Artifact quality scan failed: declared target file EMPTY 'index.html'"]
    assert _filter_satisfied_declared_target_missing_errors(errors, str(tmp_path)) == errors


def test_case_matched_target_must_have_delivery_bytes(tmp_path: Path) -> None:
    path = tmp_path / "index.html"
    path.write_text("", encoding="utf-8")
    task = {"target_files": ["INDEX.HTML"]}
    errors, issues = _declared_target_file_quality_findings(workspace_full=str(tmp_path), task=task)
    assert len(errors) == 1 and issues[0]["code"] == "declared_target_empty"
    assert issues[0]["path"] == "INDEX.HTML"
    path.write_text("<html><body>garden</body></html>", encoding="utf-8")
    errors, issues = _declared_target_file_quality_findings(workspace_full=str(tmp_path), task=task)
    assert errors == [] and issues == ()


def test_case_matched_empty_target_is_restored_without_changing_declared_scope(tmp_path: Path) -> None:
    path = tmp_path / "index.html"
    path.write_text("", encoding="utf-8")
    task = {"target_files": ["INDEX.HTML"], "scope_paths": ["INDEX.HTML"]}
    errors, issues = _declared_target_file_quality_findings(workspace_full=str(tmp_path), task=task)
    assert _missing_materialization_quality_repair_target_files(task, str(tmp_path), errors, issues) == ["INDEX.HTML"]
    assert _filter_satisfied_declared_target_missing_errors(errors, str(tmp_path)) == errors
    path.write_text("<html><body>garden</body></html>", encoding="utf-8")
    assert _filter_satisfied_declared_target_missing_errors(errors, str(tmp_path)) == []
    assert task == {"target_files": ["INDEX.HTML"], "scope_paths": ["INDEX.HTML"]}


@pytest.mark.parametrize(
    "diagnostic",
    [
        "index.html: truncated/incomplete HTML: missing </html> closing tag",
        "main.js: unexpected end of input",
    ],
)
def test_incomplete_file_guidance_does_not_require_absent_read_append_tools(diagnostic: str) -> None:
    # The real existing-target repair surface is forced edit_file. Neither
    # read_file nor append_to_file can fulfill an unconditional instruction.
    message = _build_materialization_quality_repair_message(
        original_message="Repair the originally owned project entrypoint.",
        artifact_quality_errors=[diagnostic],
        changed_files=["index.html" if diagnostic.startswith("index") else "main.js"],
    )
    assert "append_to_file" not in message
    assert "read_file its tail" not in message
    assert "CUT OFF by the output limit" not in message
