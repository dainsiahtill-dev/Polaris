"""Tests for the record_qa_blocked activity — writes integration_qa.result.json
when QA is skipped/blocked (Director fails or times out)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from polaris.cells.orchestration.workflow_runtime.internal.runtime_engine.activities.qa_activities import (
    record_qa_blocked,
)


def _make_activity_payload(
    *,
    run_id: str = "test-run-001",
    workspace: str = "/tmp/test-workspace",
    reason: str = "director_status_failed",
    blocked_stage: str = "director",
    failure_reason: str = "Director status: failed",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "workspace": workspace,
        "reason": reason,
        "blocked_stage": blocked_stage,
        "failure_reason": failure_reason,
        "metadata": metadata or {},
    }


def test_record_qa_blocked_writes_artifact(tmp_path: Path) -> None:
    """record_qa_blocked must write integration_qa.result.json with ran=False."""
    workspace = str(tmp_path)
    payload = _make_activity_payload(workspace=workspace)

    result = asyncio.run(record_qa_blocked(payload))

    assert result["success"] is True
    assert "QA blocked artifact written" in result["summary"]

    payload_data = result.get("payload", {})
    result_path_str = payload_data.get("result_path", "")
    assert result_path_str, f"Expected result_path in payload: {payload_data}"
    result_path = Path(result_path_str)
    assert result_path.exists(), f"Expected artifact at {result_path}"

    artifact = json.loads(result_path.read_text(encoding="utf-8"))
    assert artifact["ran"] is False
    assert artifact["passed"] is None
    assert artifact["reason"] == "director_status_failed"
    assert artifact["blocked_stage"] == "director"
    assert artifact["failure_reason"] == "Director status: failed"
    assert artifact["artifact_type"] == "qa_skipped_or_blocked"
    assert artifact["schema_version"] == 1


def test_record_qa_blocked_timeout_reason(tmp_path: Path) -> None:
    """record_qa_blocked must handle timeout reason correctly."""
    workspace = str(tmp_path)
    payload = _make_activity_payload(
        workspace=workspace,
        reason="director_status_timeout",
        failure_reason="Director status: timeout",
    )

    result = asyncio.run(record_qa_blocked(payload))

    assert result["success"] is True

    payload_data = result.get("payload", {})
    result_path = Path(payload_data.get("result_path", ""))
    assert result_path.exists()
    artifact = json.loads(result_path.read_text(encoding="utf-8"))
    assert artifact["ran"] is False
    assert artifact["reason"] == "director_status_timeout"
    assert artifact["blocked_stage"] == "director"


def test_record_qa_blocked_no_workspace_returns_success() -> None:
    """When workspace is empty, activity must return success (no-op)."""
    payload = _make_activity_payload(workspace="")

    result = asyncio.run(record_qa_blocked(payload))

    assert result["success"] is True
    assert "no workspace" in result["summary"]


def test_record_qa_blocked_with_project_id(tmp_path: Path) -> None:
    """record_qa_blocked must include project_id in the artifact."""
    workspace = str(tmp_path)
    payload = _make_activity_payload(
        workspace=workspace,
        metadata={"project_id": "L1-01"},
    )

    result = asyncio.run(record_qa_blocked(payload))

    assert result["success"] is True

    payload_data = result.get("payload", {})
    result_path = Path(payload_data.get("result_path", ""))
    assert result_path.exists()
    artifact = json.loads(result_path.read_text(encoding="utf-8"))
    assert artifact["ran"] is False
