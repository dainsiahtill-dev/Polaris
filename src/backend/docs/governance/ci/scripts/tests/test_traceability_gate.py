"""Unit tests for the traceability governance gate script."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
from docs.governance.ci.scripts.run_traceability_gate import (
    GateResult,
    main,
    run_traceability_gate,
)


def _write_matrix(workspace: Path, matrix: dict[str, Any]) -> Path:
    """Write a traceability matrix JSON file to the workspace runtime dir."""
    trace_dir = workspace / "runtime" / "traceability"
    trace_dir.mkdir(parents=True, exist_ok=True)
    matrix_path = trace_dir / "test.matrix.json"
    matrix_path.write_text(json.dumps(matrix, indent=2), encoding="utf-8")
    return matrix_path


def test_no_matrix_passes() -> None:
    """Gate passes when workspace has no runtime/traceability directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        result = run_traceability_gate(tmpdir)
        assert result == GateResult(
            gate="traceability_consistency",
            passed=True,
            errors=[],
        )


def test_valid_matrix_passes() -> None:
    """Gate passes for a valid doc -> task -> blueprint -> commit chain."""
    with tempfile.TemporaryDirectory() as tmpdir:
        matrix = {
            "nodes": [
                {"node_id": "doc-1", "kind": "doc", "external_id": "DOC-1"},
                {"node_id": "task-1", "kind": "task", "external_id": "TASK-1"},
                {"node_id": "bp-1", "kind": "blueprint", "external_id": "BP-1"},
                {
                    "node_id": "commit-1",
                    "kind": "commit",
                    "external_id": "COMMIT-1",
                    "metadata": {"blueprint_id": "bp-1"},
                },
            ],
            "links": [
                {"source": "doc-1", "target": "task-1", "rel": "traces_to"},
                {"source": "task-1", "target": "bp-1", "rel": "traces_to"},
                {"source": "bp-1", "target": "commit-1", "rel": "traces_to"},
            ],
        }
        _write_matrix(Path(tmpdir), matrix)
        _write_blueprint(Path(tmpdir), "bp-1", {"status": "approved"})
        result = run_traceability_gate(tmpdir)
        assert result.passed is True
        assert result.errors == []


def test_task_without_doc_fails() -> None:
    """Gate fails when a task node lacks a doc ancestor."""
    with tempfile.TemporaryDirectory() as tmpdir:
        matrix = {
            "nodes": [
                {"node_id": "task-1", "kind": "task", "external_id": "TASK-1"},
            ],
            "links": [],
        }
        _write_matrix(Path(tmpdir), matrix)
        result = run_traceability_gate(tmpdir)
        assert result.passed is False
        assert any("TASK-1" in err and "no doc ancestor" in err for err in result.errors)


def test_blueprint_without_task_fails() -> None:
    """Gate fails when a blueprint node lacks a task ancestor."""
    with tempfile.TemporaryDirectory() as tmpdir:
        matrix = {
            "nodes": [
                {"node_id": "doc-1", "kind": "doc", "external_id": "DOC-1"},
                {"node_id": "task-1", "kind": "task", "external_id": "TASK-1"},
                {"node_id": "bp-1", "kind": "blueprint", "external_id": "BP-1"},
            ],
            "links": [
                {"source": "doc-1", "target": "task-1", "rel": "traces_to"},
            ],
        }
        _write_matrix(Path(tmpdir), matrix)
        result = run_traceability_gate(tmpdir)
        assert result.passed is False
        assert any("BP-1" in err and "no task ancestor" in err for err in result.errors)


def test_commit_without_blueprint_fails() -> None:
    """Gate fails when a commit node lacks a blueprint ancestor."""
    with tempfile.TemporaryDirectory() as tmpdir:
        matrix = {
            "nodes": [
                {"node_id": "doc-1", "kind": "doc", "external_id": "DOC-1"},
                {"node_id": "task-1", "kind": "task", "external_id": "TASK-1"},
                {"node_id": "bp-1", "kind": "blueprint", "external_id": "BP-1"},
                {"node_id": "commit-1", "kind": "commit", "external_id": "COMMIT-1"},
            ],
            "links": [
                {"source": "doc-1", "target": "task-1", "rel": "traces_to"},
                {"source": "task-1", "target": "bp-1", "rel": "traces_to"},
            ],
        }
        _write_matrix(Path(tmpdir), matrix)
        result = run_traceability_gate(tmpdir)
        assert result.passed is False
        assert any(
            "COMMIT-1" in err and "no blueprint ancestor" in err for err in result.errors
        )


def test_cli_exit_code_zero_on_pass(capsys: pytest.CaptureFixture[str]) -> None:
    """CLI exits with 0 when the gate passes."""
    with (
        tempfile.TemporaryDirectory() as tmpdir,
        mock.patch.object(sys, "argv", ["run_traceability_gate.py", "--workspace", tmpdir]),
        pytest.raises(SystemExit) as exc_info,
    ):
        main()
    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert '"passed": true' in captured.out


def test_cli_exit_code_one_on_fail(capsys: pytest.CaptureFixture[str]) -> None:
    """CLI exits with 1 when the gate fails."""
    with tempfile.TemporaryDirectory() as tmpdir:
        matrix = {
            "nodes": [
                {"node_id": "task-1", "kind": "task", "external_id": "TASK-1"},
            ],
            "links": [],
        }
        _write_matrix(Path(tmpdir), matrix)
        with (
            mock.patch.object(
                sys, "argv", ["run_traceability_gate.py", "--workspace", tmpdir]
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert '"passed": false' in captured.out
        assert "TASK-1" in captured.out


def _write_blueprint(workspace: Path, blueprint_id: str, data: dict[str, Any]) -> Path:
    """Write a mock blueprint JSON file to the workspace runtime dir."""
    bp_dir = workspace / "runtime" / "blueprints"
    bp_dir.mkdir(parents=True, exist_ok=True)
    bp_path = bp_dir / f"{blueprint_id}.json"
    bp_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return bp_path


def test_gate14_commit_with_unapproved_blueprint_fails() -> None:
    """Gate 14 fails when a commit references a blueprint that is not approved."""
    with tempfile.TemporaryDirectory() as tmpdir:
        matrix = {
            "nodes": [
                {"node_id": "doc-1", "kind": "doc", "external_id": "DOC-1"},
                {"node_id": "task-1", "kind": "task", "external_id": "TASK-1"},
                {"node_id": "bp-1", "kind": "blueprint", "external_id": "BP-1"},
                {
                    "node_id": "commit-1",
                    "kind": "commit",
                    "external_id": "COMMIT-1",
                    "metadata": {"blueprint_id": "bp-1"},
                },
            ],
            "links": [
                {"source": "doc-1", "target": "task-1", "rel": "traces_to"},
                {"source": "task-1", "target": "bp-1", "rel": "traces_to"},
                {"source": "bp-1", "target": "commit-1", "rel": "traces_to"},
            ],
        }
        _write_matrix(Path(tmpdir), matrix)
        _write_blueprint(Path(tmpdir), "bp-1", {"status": "draft"})
        result = run_traceability_gate(tmpdir)
        assert result.passed is False
        assert any(
            "COMMIT-1" in err and "unapproved or missing blueprint bp-1" in err
            for err in result.errors
        )


def test_gate14_commit_with_approved_blueprint_passes() -> None:
    """Gate 14 passes when a commit references an approved blueprint."""
    with tempfile.TemporaryDirectory() as tmpdir:
        matrix = {
            "nodes": [
                {"node_id": "doc-1", "kind": "doc", "external_id": "DOC-1"},
                {"node_id": "task-1", "kind": "task", "external_id": "TASK-1"},
                {"node_id": "bp-1", "kind": "blueprint", "external_id": "BP-1"},
                {
                    "node_id": "commit-1",
                    "kind": "commit",
                    "external_id": "COMMIT-1",
                    "metadata": {"blueprint_id": "bp-1"},
                },
            ],
            "links": [
                {"source": "doc-1", "target": "task-1", "rel": "traces_to"},
                {"source": "task-1", "target": "bp-1", "rel": "traces_to"},
                {"source": "bp-1", "target": "commit-1", "rel": "traces_to"},
            ],
        }
        _write_matrix(Path(tmpdir), matrix)
        _write_blueprint(Path(tmpdir), "bp-1", {"status": "approved"})
        result = run_traceability_gate(tmpdir)
        assert result.passed is True
        assert result.errors == []


def test_gate15_version_lag_fails() -> None:
    """Gate 15 fails when blueprint version lags behind doc version."""
    with tempfile.TemporaryDirectory() as tmpdir:
        matrix = {
            "nodes": [
                {"node_id": "doc-1", "kind": "doc", "external_id": "DOC-1"},
                {"node_id": "task-1", "kind": "task", "external_id": "TASK-1"},
                {
                    "node_id": "bp-1",
                    "kind": "blueprint",
                    "external_id": "BP-1",
                    "metadata": {"doc_version": 3, "blueprint_version": 2},
                },
            ],
            "links": [
                {"source": "doc-1", "target": "task-1", "rel": "traces_to"},
                {"source": "task-1", "target": "bp-1", "rel": "traces_to"},
            ],
        }
        _write_matrix(Path(tmpdir), matrix)
        result = run_traceability_gate(tmpdir)
        assert result.passed is False
        assert any(
            "BP-1 version 2 lags behind doc version 3" in err for err in result.errors
        )


def test_gate15_no_impact_skips() -> None:
    """Gate 15 is skipped when the blueprint is marked no_impact."""
    with tempfile.TemporaryDirectory() as tmpdir:
        matrix = {
            "nodes": [
                {"node_id": "doc-1", "kind": "doc", "external_id": "DOC-1"},
                {"node_id": "task-1", "kind": "task", "external_id": "TASK-1"},
                {
                    "node_id": "bp-1",
                    "kind": "blueprint",
                    "external_id": "BP-1",
                    "metadata": {
                        "doc_version": 3,
                        "blueprint_version": 2,
                        "no_impact": True,
                    },
                },
            ],
            "links": [
                {"source": "doc-1", "target": "task-1", "rel": "traces_to"},
                {"source": "task-1", "target": "bp-1", "rel": "traces_to"},
            ],
        }
        _write_matrix(Path(tmpdir), matrix)
        result = run_traceability_gate(tmpdir)
        assert result.passed is True
        assert result.errors == []


def test_gate14_adr_store_blueprint_passes() -> None:
    """Gate 14 passes for a blueprint created via ADRStore."""
    with tempfile.TemporaryDirectory() as tmpdir:
        from polaris.cells.chief_engineer.blueprint.internal.adr_store import ADRStore

        store = ADRStore(workspace=tmpdir)
        store.create_blueprint("bp-adr-1", {"title": "Plan"})

        matrix = {
            "nodes": [
                {"node_id": "doc-1", "kind": "doc", "external_id": "DOC-1"},
                {"node_id": "task-1", "kind": "task", "external_id": "TASK-1"},
                {"node_id": "bp-adr-1", "kind": "blueprint", "external_id": "bp-adr-1"},
                {
                    "node_id": "commit-1",
                    "kind": "commit",
                    "external_id": "COMMIT-1",
                    "metadata": {"blueprint_id": "bp-adr-1"},
                },
            ],
            "links": [
                {"source": "doc-1", "target": "task-1", "rel": "traces_to"},
                {"source": "task-1", "target": "bp-adr-1", "rel": "traces_to"},
                {"source": "bp-adr-1", "target": "commit-1", "rel": "traces_to"},
            ],
        }
        _write_matrix(Path(tmpdir), matrix)
        result = run_traceability_gate(tmpdir)
        assert result.passed is True
        assert result.errors == []
