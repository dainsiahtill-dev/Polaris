"""Tests for audit_quick helper logic.

CRITICAL: 所有文本文件 I/O 必须使用 UTF-8 编码。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

if importlib.util.find_spec("scripts.audit_quick") is None:
    pytest.skip("Legacy module not available: scripts.audit_quick", allow_module_level=True)

from polaris.cells.audit.diagnosis.public import ErrorChainSearcher

import scripts.audit_quick as audit_quick_module
from scripts.audit_quick import (
    _collect_factory_events,
    _collect_runtime_event_inventory,
    _diagnose_runtime,
    _export_data,
    _resolve_export_format,
)


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def test_collect_runtime_event_inventory_counts_role_events(tmp_path: Path) -> None:
    runtime_root = tmp_path / ".polaris" / "runtime"
    runtime_root.mkdir(parents=True, exist_ok=True)

    _write_jsonl(
        runtime_root / "roles" / "qa" / "logs" / "events_20260312.jsonl",
        [
            {
                "timestamp": "2026-03-12T04:04:50.576169",
                "role": "qa",
                "type": "turn_completed",
                "data": {"content_preview": "health check ok"},
            }
        ],
    )

    inventory = _collect_runtime_event_inventory(runtime_root)

    assert inventory["total_events"] == 1
    assert inventory["by_source"]["role"]["events"] == 1
    assert inventory["by_source"]["audit"]["events"] == 0
    assert inventory["by_source"]["runtime"]["events"] == 0


def test_diagnose_runtime_reports_raw_events_when_audit_empty(tmp_path: Path) -> None:
    runtime_root = tmp_path / ".polaris" / "runtime"
    runtime_root.mkdir(parents=True, exist_ok=True)
    (runtime_root / "audit").mkdir(parents=True, exist_ok=True)

    _write_jsonl(
        runtime_root / "roles" / "qa" / "logs" / "events_20260312.jsonl",
        [
            {
                "timestamp": "2026-03-12T04:04:50.576169",
                "role": "qa",
                "type": "turn_completed",
                "data": {"content_preview": "tool-ready"},
            }
        ],
    )

    diagnosis = _diagnose_runtime(runtime_root)

    assert diagnosis["total_events"] == 0
    assert diagnosis["all_events_total"] == 1
    assert any("events_20260312.jsonl" in path for path in diagnosis["all_event_files"])
    assert any("未落入 audit-YYYY-MM.jsonl" in rec for rec in diagnosis["recommendations"])


def test_collect_factory_events_missing_dir_is_not_failure(tmp_path: Path) -> None:
    runtime_root = tmp_path / ".polaris" / "runtime"
    runtime_root.mkdir(parents=True, exist_ok=True)

    result = _collect_factory_events(runtime_root)

    assert result["status"] == "not_found"
    assert result["reason"] == "factory_dir_missing"
    assert result["runs"] == []
    assert len(result["checked_factory_dirs"]) >= 1


def test_collect_factory_events_run_id_not_found(tmp_path: Path) -> None:
    workspace = tmp_path
    runtime_root = workspace / ".polaris" / "runtime"
    runtime_root.mkdir(parents=True, exist_ok=True)

    _write_jsonl(
        workspace / ".polaris" / "factory" / "factory_20260312_demo" / "events" / "events.jsonl",
        [{"timestamp": "2026-03-12T04:05:00.000000", "type": "started", "stage": "bootstrap"}],
    )

    result = _collect_factory_events(runtime_root, run_id="factory_not_exist")

    assert result["status"] == "not_found"
    assert result["reason"] == "run_id_not_found"
    assert "factory_20260312_demo" in result["available_run_ids"]


def test_collect_factory_events_returns_tail_with_total(tmp_path: Path) -> None:
    workspace = tmp_path
    runtime_root = workspace / ".polaris" / "runtime"
    runtime_root.mkdir(parents=True, exist_ok=True)

    _write_jsonl(
        workspace / ".polaris" / "factory" / "factory_20260312_ok" / "events" / "events.jsonl",
        [
            {"timestamp": "2026-03-12T04:05:00.000000", "type": "started", "stage": "bootstrap"},
            {"timestamp": "2026-03-12T04:05:01.000000", "type": "completed", "stage": "qa"},
        ],
    )

    result = _collect_factory_events(runtime_root, limit_per_run=1, max_runs=5)

    assert result["status"] == "ok"
    assert result["total_events"] == 2
    assert len(result["runs"]) == 1
    run = result["runs"][0]
    assert run["run_id"] == "factory_20260312_ok"
    assert run["total_events"] == 2
    assert len(run["events"]) == 1


def test_search_errors_diagnostics_classifies_role_events(tmp_path: Path) -> None:
    runtime_root = tmp_path / ".polaris" / "runtime"
    runtime_root.mkdir(parents=True, exist_ok=True)

    _write_jsonl(
        runtime_root / "roles" / "qa" / "logs" / "events_20260312.jsonl",
        [
            {
                "timestamp": "2026-03-12T04:04:50.576169",
                "role": "qa",
                "type": "turn_completed",
                "data": {"content_preview": "hello"},
            }
        ],
    )

    searcher = ErrorChainSearcher(runtime_root)
    chains = searcher.search(pattern="not_found_keyword", include_factory=True)

    assert chains == []
    stats = searcher.last_search_stats
    assert stats["total_events"] == 1
    assert stats["role_events"] == 1
    assert stats["factory_events"] == 0


def test_search_errors_since_filter_excludes_old_role_event(tmp_path: Path) -> None:
    runtime_root = tmp_path / ".polaris" / "runtime"
    runtime_root.mkdir(parents=True, exist_ok=True)

    old_timestamp = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat().replace("+00:00", "Z")
    _write_jsonl(
        runtime_root / "roles" / "qa" / "logs" / "events_20260312.jsonl",
        [
            {
                "timestamp": old_timestamp,
                "role": "qa",
                "type": "turn_completed",
                "data": {"content_preview": "timeout sentinel"},
            }
        ],
    )

    searcher = ErrorChainSearcher(runtime_root)
    recent_only = searcher.search(
        pattern="timeout",
        since=datetime.now(timezone.utc) - timedelta(hours=1),
        include_factory=True,
    )
    all_time = searcher.search(pattern="timeout", include_factory=True)

    assert recent_only == []
    assert len(all_time) == 1


def test_resolve_export_format_precedence_and_defaults() -> None:
    assert _resolve_export_format(
        export_format_arg="csv",
        output_path=Path("report.json"),
    ) == "csv"
    assert _resolve_export_format(
        export_format_arg=None,
        output_path=Path("report.csv"),
    ) == "csv"
    assert _resolve_export_format(
        export_format_arg=None,
        output_path=Path("report.json"),
    ) == "json"
    assert _resolve_export_format(
        export_format_arg=None,
        output_path=Path("report"),
    ) == "json"


def test_resolve_export_format_rejects_invalid_value() -> None:
    with pytest.raises(ValueError, match="Unsupported format"):
        _resolve_export_format(
            export_format_arg="yaml",
            output_path=Path("report.json"),
        )


def test_export_data_json_writes_utf8_json_payload(tmp_path: Path) -> None:
    runtime_root = tmp_path / ".polaris" / "runtime"
    runtime_root.mkdir(parents=True, exist_ok=True)
    output_path = tmp_path / "audit-export.json"

    result = _export_data(
        runtime_root=runtime_root,
        output_path=output_path,
        export_format="json",
    )

    assert result["format"] == "json"
    assert output_path.exists()
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    assert "export_metadata" in payload


def test_export_data_csv_writes_header_even_when_empty(tmp_path: Path) -> None:
    runtime_root = tmp_path / ".polaris" / "runtime"
    runtime_root.mkdir(parents=True, exist_ok=True)
    output_path = tmp_path / "audit-export.csv"

    result = _export_data(
        runtime_root=runtime_root,
        output_path=output_path,
        export_format="csv",
    )

    assert result["format"] == "csv"
    assert output_path.exists()
    text = output_path.read_text(encoding="utf-8")
    assert "event_id,timestamp,event_type,role" in text


def test_search_errors_passes_since_until_to_searcher(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / ".polaris" / "runtime"
    runtime_root.mkdir(parents=True, exist_ok=True)
    captured: dict[str, object] = {}

    class _FakeSearcher:
        def __init__(self, root: Path) -> None:
            captured["root"] = root
            self.last_search_stats = {
                "files_scanned": [],
                "factory_files": [],
                "total_events": 0,
                "action_events": 0,
                "observation_events": 0,
                "factory_events": 0,
                "role_events": 0,
                "runtime_events": 0,
            }

        def search(self, **kwargs: object) -> list[object]:
            captured.update(kwargs)
            return []

    monkeypatch.setattr(audit_quick_module, "ErrorChainSearcher", _FakeSearcher)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "audit_quick.py",
            "search-errors",
            "--pattern",
            "timeout",
            "--root",
            str(runtime_root),
            "--since",
            "1h",
            "--until",
            "now",
        ],
    )

    with pytest.raises(SystemExit) as excinfo:
        audit_quick_module.main()

    assert excinfo.value.code == 0
    assert isinstance(captured.get("since"), datetime)
    assert isinstance(captured.get("until"), datetime)


def test_verify_strict_non_empty_exits_with_code_1_when_no_events(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / ".polaris" / "runtime"
    runtime_root.mkdir(parents=True, exist_ok=True)

    def _fake_verify(**_: object) -> dict[str, object]:
        return {
            "chain_valid": True,
            "mode": "offline",
            "total_events": 0,
            "has_events": False,
            "empty_chain": True,
            "errors": [{"code": "insufficient_audit_data", "message": "strict_non_empty requires at least one audit event"}],
        }

    monkeypatch.setattr(audit_quick_module, "verify", _fake_verify)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "audit_quick.py",
            "verify",
            "--strict-non-empty",
            "--root",
            str(runtime_root),
        ],
    )

    with pytest.raises(SystemExit) as excinfo:
        audit_quick_module.main()

    assert excinfo.value.code == 1
