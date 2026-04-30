"""Tests for unified audit service helpers.

CRITICAL: 所有文本文件 I/O 必须使用 UTF-8 编码。
"""

from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timezone

import pytest

if importlib.util.find_spec("polaris.cells.audit.diagnosis.public") is None:
    pytest.skip("Module not available: polaris.cells.audit.diagnosis.public", allow_module_level=True)

from polaris.cells.audit.diagnosis.public import run_audit_command, to_legacy_result


def _write_audit_event(runtime_root, event: dict) -> None:
    audit_dir = runtime_root / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    log_path = audit_dir / "audit-2024-03.jsonl"
    with open(log_path, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")


def _sample_event(*, task_id: str, run_id: str, trace_id: str) -> dict:
    return {
        "event_id": f"event-{task_id}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_type": "task_start",
        "version": "1.0",
        "source": {"role": "pm", "workspace": "."},
        "task": {"task_id": task_id, "iteration": 1, "run_id": run_id},
        "resource": {"type": "file", "path": "", "operation": "read"},
        "action": {"name": "start", "result": "success"},
        "data": {},
        "context": {"trace_id": trace_id},
        "prev_hash": "0" * 64,
        "signature": "invalid-signature",
    }


@pytest.fixture
def runtime_root(tmp_path):
    runtime = tmp_path / ".polaris" / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    return runtime


def test_run_audit_command_triage_task_id_offline(runtime_root):
    _write_audit_event(
        runtime_root,
        _sample_event(task_id="task-1", run_id="proj-20240310-abc12345", trace_id="trace-1"),
    )

    result = run_audit_command(
        "triage",
        params={"task_id": "task-1"},
        mode="offline",
        runtime_root=runtime_root,
        workspace=".",
    )

    assert result["status"] == "success"
    assert result["mode"] == "offline"
    data = result["data"]
    assert data["task_id"] == "task-1"
    assert data["run_id"] == "proj-20240310-abc12345"


def test_run_audit_command_hops_fallback_events_path(runtime_root):
    run_id = "proj-20240310-abc12345"
    events_dir = runtime_root / "events"
    events_dir.mkdir(parents=True, exist_ok=True)
    events_path = events_dir / "runtime.events.jsonl"

    records = [
        {
            "seq": 1,
            "kind": "action",
            "actor": "Tooling",
            "name": "repo_rg",
            "refs": {"run_id": run_id, "phase": "tool_exec", "task_id": "task-1"},
        },
        {
            "seq": 2,
            "kind": "observation",
            "actor": "Tooling",
            "name": "repo_rg",
            "ok": False,
            "refs": {"run_id": run_id, "phase": "tool_exec", "task_id": "task-1"},
            "output": {"ok": False, "tool": "repo_rg", "error": "timeout"},
        },
    ]

    with open(events_path, "w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    result = run_audit_command(
        "hops",
        params={"run_id": run_id},
        mode="offline",
        runtime_root=runtime_root,
    )

    assert result["status"] == "success"
    assert result["data"]["run_id"] == run_id
    assert result["data"]["has_failure"] is True


def test_run_audit_command_verify_chain_reports_status(runtime_root):
    _write_audit_event(
        runtime_root,
        _sample_event(task_id="task-2", run_id="proj-20240310-abc12345", trace_id="trace-2"),
    )

    result = run_audit_command(
        "verify-chain",
        mode="offline",
        runtime_root=runtime_root,
    )

    assert result["status"] == "success"
    assert result["command"] == "verify-chain"
    assert "chain_valid" in result["data"]
    assert result["data"]["has_events"] is True
    assert result["data"]["empty_chain"] is False


def test_run_audit_command_verify_chain_strict_non_empty_fails_on_empty_chain(runtime_root):
    (runtime_root / "audit").mkdir(parents=True, exist_ok=True)

    result = run_audit_command(
        "verify-chain",
        params={"strict_non_empty": True},
        mode="offline",
        runtime_root=runtime_root,
    )

    assert result["status"] == "error"
    data = result.get("data") or {}
    assert data.get("total_events") == 0
    assert data.get("has_events") is False
    assert data.get("empty_chain") is True
    errors = result.get("errors") or []
    assert errors
    assert errors[0].get("code") == "insufficient_audit_data"


def test_run_audit_command_verify_chain_strict_non_empty_passes_with_events(runtime_root):
    _write_audit_event(
        runtime_root,
        _sample_event(task_id="task-5", run_id="proj-20240310-abc12345", trace_id="trace-5"),
    )

    result = run_audit_command(
        "verify-chain",
        params={"strict_non_empty": True},
        mode="offline",
        runtime_root=runtime_root,
    )

    assert result["status"] == "success"
    assert result["data"]["has_events"] is True
    assert result["data"]["empty_chain"] is False


def test_run_audit_command_diagnose_offline(runtime_root):
    event = _sample_event(
        task_id="task-3",
        run_id="proj-20240310-abc12345",
        trace_id="trace-3",
    )
    event["event_type"] = "task_failed"
    event["action"] = {"name": "execute", "result": "failure", "error": "Permission denied"}
    _write_audit_event(runtime_root, event)

    result = run_audit_command(
        "diagnose",
        params={"run_id": "proj-20240310-abc12345", "depth": 3},
        mode="offline",
        runtime_root=runtime_root,
        workspace=str(runtime_root.parent),
    )

    assert result["status"] == "success"
    data = result["data"]
    assert data["failure_detected"] is True
    root = data.get("root_cause") or {}
    assert root.get("category") in {"permission_denied", "tool_execution_failure", "unknown"}


def test_run_audit_command_scan_offline(runtime_root, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    src_file = workspace / "src" / "service.py"
    src_file.parent.mkdir(parents=True, exist_ok=True)
    src_file.write_text(
        "API_KEY = 'abcdef1234567890'\n# TODO: remove test key\n",
        encoding="utf-8",
    )

    result = run_audit_command(
        "scan",
        params={"scope": "full", "max_files": 100},
        mode="offline",
        runtime_root=runtime_root,
        workspace=str(workspace),
    )

    assert result["status"] == "success"
    summary = result["data"].get("summary") or {}
    assert int(summary.get("files_scanned") or 0) >= 1
    assert int(summary.get("findings_total") or 0) >= 1


def test_run_audit_command_scan_region_requires_focus(runtime_root, tmp_path):
    workspace = tmp_path / "workspace-region-required"
    workspace.mkdir(parents=True, exist_ok=True)

    result = run_audit_command(
        "scan",
        params={"scope": "region"},
        mode="offline",
        runtime_root=runtime_root,
        workspace=str(workspace),
    )

    assert result["status"] == "error"
    errors = result.get("errors") or []
    assert errors
    assert "focus is required" in str(errors[0].get("message", "")).lower()


def test_run_audit_command_scan_region_path_traversal(runtime_root, tmp_path):
    workspace = tmp_path / "workspace-region-traversal"
    workspace.mkdir(parents=True, exist_ok=True)

    result = run_audit_command(
        "scan",
        params={"scope": "region", "focus": "../../etc/passwd"},
        mode="offline",
        runtime_root=runtime_root,
        workspace=str(workspace),
    )

    assert result["status"] == "error"
    errors = result.get("errors") or []
    assert errors
    assert "outside workspace" in str(errors[0].get("message", "")).lower()


def test_run_audit_command_scan_region_file_not_found(runtime_root, tmp_path):
    workspace = tmp_path / "workspace-region-not-found"
    workspace.mkdir(parents=True, exist_ok=True)

    result = run_audit_command(
        "scan",
        params={"scope": "region", "focus": "missing.py"},
        mode="offline",
        runtime_root=runtime_root,
        workspace=str(workspace),
    )

    assert result["status"] == "not_found"


def test_run_audit_command_scan_changed_no_changes_not_fallback_to_full(runtime_root, tmp_path):
    workspace = tmp_path / "workspace-changed-only"
    workspace.mkdir(parents=True, exist_ok=True)
    src_file = workspace / "src" / "module.py"
    src_file.parent.mkdir(parents=True, exist_ok=True)
    src_file.write_text("def ok():\n    return 1\n", encoding="utf-8")

    result = run_audit_command(
        "scan",
        params={"scope": "changed"},
        mode="offline",
        runtime_root=runtime_root,
        workspace=str(workspace),
    )

    assert result["status"] == "success"
    summary = result["data"].get("summary") or {}
    assert int(summary.get("files_scanned") or 0) == 0


def test_run_audit_command_check_region_offline(runtime_root, tmp_path):
    workspace = tmp_path / "workspace-region"
    workspace.mkdir(parents=True, exist_ok=True)
    src_file = workspace / "src" / "auth.py"
    src_file.parent.mkdir(parents=True, exist_ok=True)
    src_file.write_text(
        "def login():\n"
        "    token = 'topsecret12345'\n"
        "    return token\n",
        encoding="utf-8",
    )

    result = run_audit_command(
        "check-region",
        params={"file_path": "src/auth.py", "lines": "1-3"},
        mode="offline",
        runtime_root=runtime_root,
        workspace=str(workspace),
    )

    assert result["status"] == "success"
    data = result["data"]
    assert str(data.get("file") or "").endswith("src/auth.py")
    assert "summary" in data


def test_run_audit_command_trace_offline(runtime_root):
    _write_audit_event(
        runtime_root,
        _sample_event(task_id="task-4", run_id="proj-20240310-abc12345", trace_id="trace-4"),
    )

    result = run_audit_command(
        "trace",
        params={"trace_id": "trace-4", "limit": 10},
        mode="offline",
        runtime_root=runtime_root,
        workspace=str(runtime_root.parent),
    )

    assert result["status"] == "success"
    data = result["data"]
    assert data["trace_id"] == "trace-4"
    assert int(data.get("event_count") or 0) >= 1


def test_to_legacy_result_flattens_errors():
    envelope = {
        "schema_version": "2.1",
        "command": "triage",
        "status": "error",
        "mode": "offline",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "data": {},
        "errors": [{"code": "invalid_input", "message": "run_id is required"}],
    }
    flattened = to_legacy_result(envelope)
    assert flattened["status"] == "error"
    assert flattened["error"] == "run_id is required"
