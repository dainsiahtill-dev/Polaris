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


def test_tool_lifecycle_dropped_projection_blocks_as_platform_failure(tmp_path: Path) -> None:
    engine = QAVerdictEngine(str(tmp_path))

    envelope = engine.build_envelope(
        task_id="TASK-3",
        payload={"task_id": "TASK-3"},
        ledger_projection={
            "tool_lifecycle": {
                "ok": False,
                "dropped_count": 1,
                "failed_count": 1,
                "events": [
                    {
                        "dropped": True,
                        "failed": True,
                        "status": "dropped",
                        "failure_class": "TOOL_DISPATCH_DROPPED",
                        "reason": "provider emitted a write_file call but dispatch was not committed",
                    }
                ],
            }
        },
    )

    assert envelope.verdict == "BLOCKED"
    assert envelope.next_stage == "waiting_human"
    assert envelope.classification.failure_class == "TOOL_DISPATCH_DROPPED"
    assert envelope.classification.responsible_layer == "execution_control_plane"
    assert envelope.classification.repairable_by_director is False
    assert "dispatch was not committed" in envelope.classification.reason


def test_tool_lifecycle_failed_projection_blocks_with_projected_failure_class(tmp_path: Path) -> None:
    engine = QAVerdictEngine(str(tmp_path))

    envelope = engine.build_envelope(
        task_id="TASK-4",
        payload={"task_id": "TASK-4"},
        ledger_projection={
            "tool_lifecycle": {
                "ok": False,
                "dropped_count": 0,
                "failed_count": 1,
                "events": [
                    {
                        "failed": True,
                        "status": "failed",
                        "failure_class": "MISSING_EFFECT_RECEIPT",
                        "reason": "write_file returned without authoritative effect receipt",
                    }
                ],
            }
        },
    )

    assert envelope.verdict == "BLOCKED"
    assert envelope.next_stage == "waiting_human"
    assert envelope.classification.failure_class == "MISSING_EFFECT_RECEIPT"
    assert envelope.classification.responsible_layer == "execution_control_plane"
    assert envelope.classification.repairable_by_director is False
    assert "effect receipt" in envelope.classification.reason


def test_task_boundary_text_fallback_failure_routes_to_platform(tmp_path: Path) -> None:
    engine = QAVerdictEngine(str(tmp_path))

    envelope = engine.build_envelope(
        task_id="TASK-5",
        payload={"task_id": "TASK-5"},
        ledger_projection={
            "task_boundary": {
                "latest": {
                    "ok": False,
                    "failure_class": "REQUIRED_TOOL_TEXT_FALLBACK_NOT_DISPATCHED",
                    "responsible_layer": "execution_control_plane",
                    "reason": "compatibility parser produced no dispatch",
                }
            }
        },
    )

    assert envelope.verdict == "BLOCKED"
    assert envelope.next_stage == "waiting_human"
    assert envelope.classification.failure_class == "REQUIRED_TOOL_TEXT_FALLBACK_NOT_DISPATCHED"
    assert envelope.classification.responsible_layer == "execution_control_plane"
    assert envelope.classification.repairable_by_director is False


def test_task_boundary_failed_projection_beats_unrelated_latest_success(tmp_path: Path) -> None:
    engine = QAVerdictEngine(str(tmp_path))

    envelope = engine.build_envelope(
        task_id="TASK-2",
        payload={"task_id": "TASK-2"},
        ledger_projection={
            "task_boundary": {
                "ok": False,
                "latest": {
                    "task_id": "TASK-3",
                    "ok": True,
                    "failure_class": "PASSED",
                },
                "failed": [
                    {
                        "task_id": "TASK-2",
                        "ok": False,
                        "failure_class": "INCOMPLETE_MATERIALIZATION",
                        "responsible_layer": "director",
                        "reason": "TASK-2 remains incomplete",
                    }
                ],
            }
        },
    )

    assert envelope.verdict == "FAIL"
    assert envelope.next_stage == "pending_exec"
    assert envelope.classification.failure_class == "INCOMPLETE_MATERIALIZATION"


def test_failed_required_evidence_is_compiler_or_test_failure(tmp_path: Path) -> None:
    engine = QAVerdictEngine(str(tmp_path))

    envelope = engine.build_envelope(
        task_id="TASK-6",
        payload={"task_id": "TASK-6"},
        ledger_projection={
            "evidence_policy": {
                "required_modalities": ["command"],
                "missing_required_modalities": [],
                "failed_required_modalities": ["command"],
            }
        },
    )

    assert envelope.verdict == "FAIL"
    assert envelope.next_stage == "pending_exec"
    assert envelope.classification.failure_class == "COMPILER_OR_TEST_FAILURE"
    assert envelope.classification.responsible_layer == "director"
