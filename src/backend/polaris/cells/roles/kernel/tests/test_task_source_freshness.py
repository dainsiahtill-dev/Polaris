"""Read input freshness must remain separate from write obligations."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.roles.kernel.internal.kernel.task_boundary import (
    director_task_boundary_source_targets,
    director_task_boundary_target_files,
)


@pytest.mark.parametrize(
    "read_context",
    [
        {"context_files": ["answer.py", "verify.py"]},
        {"allowed_read_paths": ["answer.py", "verify.py"]},
        {"metadata": {"control_plane_job_token": {"allowed_read_paths": ["answer.py", "verify.py"]}}},
        {"task": {"metadata": {"context_files": ["answer.py", "verify.py"]}}},
        {"director_execution_envelope": {"authorization": {"allowed_read_paths": ["answer.py", "verify.py"]}}},
    ],
)
def test_read_only_inputs_bind_source_state_without_becoming_write_targets(read_context: dict[str, Any]) -> None:
    context = {"target_files": [], "required_write_targets": [], **read_context}
    assert director_task_boundary_source_targets(context) == ["answer.py", "verify.py"]
    assert director_task_boundary_target_files(context) == []


def test_source_state_hashes_physical_workspace_files_and_changes(tmp_path: Path) -> None:
    from polaris.cells.roles.kernel.internal.transaction.continuation_evidence import capture_task_source_state

    (tmp_path / "verify.py").write_text("print('verified')\n", encoding="utf-8")
    expected = hashlib.sha256(b"print('verified')\n").hexdigest()
    assert capture_task_source_state(str(tmp_path), ["verify.py", "absent.py"]) == {
        "absent.py": "missing",
        "verify.py": expected,
    }
    (tmp_path / "verify.py").write_text("raise AssertionError('changed')\n", encoding="utf-8")
    assert capture_task_source_state(str(tmp_path), ["verify.py"])["verify.py"] != expected


def test_directory_permission_scopes_do_not_replace_exact_read_input_hashes(tmp_path: Path) -> None:
    from polaris.cells.roles.kernel.internal.transaction.continuation_evidence import capture_task_source_state

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "verify.py").write_text("print('verified')\n", encoding="utf-8")
    assert capture_task_source_state(str(tmp_path), ["src", "src/verify.py"]) == {
        "src/verify.py": hashlib.sha256(b"print('verified')\n").hexdigest(),
    }
    assert capture_task_source_state(str(tmp_path), ["src", "."]) == {}


@pytest.mark.parametrize("field", ["target_files", "context_files"])
def test_declared_file_input_directory_is_rejected(tmp_path: Path, field: str) -> None:
    (tmp_path / "src").mkdir()
    with pytest.raises(ValueError, match="continuation_declared_source_file_is_directory"):
        director_task_boundary_source_targets({field: ["src"]}, workspace=str(tmp_path))


def test_read_inputs_do_not_change_legacy_missing_write_obligations() -> None:
    context = {"target_files": ["answer.py"], "context_files": ["verify.py"]}
    assert director_task_boundary_source_targets(context) == ["answer.py", "verify.py"]
    assert director_task_boundary_target_files(context) == ["answer.py"]


def test_read_inputs_do_not_discover_unrelated_history_or_expand_scope_patterns() -> None:
    context = {
        "context_files": ["answer.py", "./answer.py", "../outside.py", "**/*.py"],
        "history": {"context_files": ["old.py"]},
        "allowed_paths": ["unrelated.py"],
    }
    assert director_task_boundary_source_targets(context) == ["answer.py"]
    assert director_task_boundary_target_files(context) == []
