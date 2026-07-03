"""Tests for QA verdict engine classification details."""

from __future__ import annotations

from pathlib import Path

from polaris.cells.qa.audit_verdict.internal.verdict_engine import QAVerdictEngine


def test_typed_artifact_quality_missing_target_routes_to_director(tmp_path: Path) -> None:
    engine = QAVerdictEngine(str(tmp_path))

    envelope = engine.build_envelope(
        task_id="TASK-1",
        payload={"task_id": "TASK-1"},
        artifact_quality={
            "errors": ["legacy artifact quality text"],
            "issues": [
                {
                    "code": "declared_target_missing",
                    "message": "declared target file src/index.js is missing",
                    "path": "src/index.js",
                }
            ],
        },
    )

    assert envelope.verdict == "FAIL"
    assert envelope.next_stage == "pending_exec"
    assert envelope.classification.failure_class == "INCOMPLETE_MATERIALIZATION"
    assert envelope.classification.repairable_by_director is True
    assert envelope.classification.responsible_layer == "director"


def test_typed_artifact_quality_defect_routes_to_director_repair(tmp_path: Path) -> None:
    engine = QAVerdictEngine(str(tmp_path))

    envelope = engine.build_envelope(
        task_id="TASK-2",
        payload={"task_id": "TASK-2"},
        artifact_quality={
            "errors": ["legacy artifact quality text"],
            "issues": [
                {
                    "code": "npm_manifest_invalid",
                    "message": "npm package manifest script 'test' is invalid",
                    "path": "package.json",
                }
            ],
        },
    )

    assert envelope.verdict == "FAIL"
    assert envelope.next_stage == "pending_exec"
    assert envelope.classification.failure_class == "IMPLEMENTATION_DEFECT"
    assert envelope.classification.repairable_by_director is True
    assert "npm_manifest_invalid" in envelope.classification.reason

