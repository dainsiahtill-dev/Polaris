"""Tests for polaris.kernelone.audit.llm_bridge."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from polaris.kernelone.audit.contracts import KernelAuditEventType
from polaris.kernelone.audit.llm_bridge import LLMuditBridge


class TestLLMuditBridge:
    def test_runtime_lazy_load(self, tmp_path: Path) -> None:
        bridge = LLMuditBridge()
        assert bridge._runtime is None
        with patch("polaris.kernelone.audit.llm_bridge.KernelAuditRuntime") as mock_runtime_cls:
            mock_runtime = MagicMock()
            mock_runtime_cls.get_instance.return_value = mock_runtime
            _ = bridge.runtime
            assert bridge._runtime is mock_runtime

    def test_replay_session_missing_file(self, tmp_path: Path) -> None:
        bridge = LLMuditBridge(runtime=MagicMock())
        count = bridge.replay_session(tmp_path / "missing.json")
        assert count == 0

    def test_replay_session_invalid_json(self, tmp_path: Path) -> None:
        bridge = LLMuditBridge(runtime=MagicMock())
        session_file = tmp_path / "bad.json"
        session_file.write_text("not json", encoding="utf-8")
        count = bridge.replay_session(session_file)
        assert count == 0

    def test_replay_session_success(self, tmp_path: Path) -> None:
        mock_runtime = MagicMock()
        bridge = LLMuditBridge(runtime=mock_runtime)
        session_file = tmp_path / "session.json"
        session_data = {
            "session_id": "sess-1",
            "workspace": "/ws",
            "events": [
                {
                    "event_type": "operation_start",
                    "tool_name": "test_tool",
                    "success": True,
                    "duration_ms": 100,
                    "metadata": {"key": "val"},
                },
                {
                    "event_type": "operation_error",
                    "tool_name": "test_tool",
                    "success": False,
                    "error_message": "boom",
                },
            ],
        }
        session_file.write_text(json.dumps(session_data), encoding="utf-8")
        count = bridge.replay_session(session_file)
        assert count == 2
        assert mock_runtime.emit_event.call_count == 2

        # Check first event mapping
        first_call = mock_runtime.emit_event.call_args_list[0]
        kwargs = first_call.kwargs
        assert kwargs["event_type"] == KernelAuditEventType.TOOL_EXECUTION
        assert kwargs["role"] == "llm_audit_bridge"
        assert kwargs["workspace"] == "/ws"
        assert kwargs["data"]["duration_ms"] == 100.0

        # Check second event mapping
        second_call = mock_runtime.emit_event.call_args_list[1]
        kwargs = second_call.kwargs
        assert kwargs["event_type"] == KernelAuditEventType.TASK_FAILED
        assert kwargs["action"]["result"] == "failure"
        assert kwargs["action"]["error"] == "boom"

    def test_replay_session_with_timestamp(self, tmp_path: Path) -> None:
        mock_runtime = MagicMock()
        bridge = LLMuditBridge(runtime=mock_runtime)
        session_file = tmp_path / "session.json"
        ts = datetime.now(timezone.utc).isoformat()
        session_data = {
            "session_id": "sess-1",
            "events": [
                {"event_type": "parse_start", "timestamp": ts, "success": True},
            ],
        }
        session_file.write_text(json.dumps(session_data), encoding="utf-8")
        count = bridge.replay_session(session_file)
        assert count == 1

    def test_replay_session_bad_event_data(self, tmp_path: Path) -> None:
        mock_runtime = MagicMock()
        bridge = LLMuditBridge(runtime=mock_runtime)
        session_file = tmp_path / "session.json"
        session_data = {
            "session_id": "sess-1",
            "events": [
                {"event_type": "operation_start", "success": True},
            ],
        }
        session_file.write_text(json.dumps(session_data), encoding="utf-8")
        # Force emit_event to raise on first call
        mock_runtime.emit_event.side_effect = [ValueError("boom"), None]
        count = bridge.replay_session(session_file)
        assert count == 0  # The bad event fails, no retries

    def test_replay_directory(self, tmp_path: Path) -> None:
        mock_runtime = MagicMock()
        bridge = LLMuditBridge(runtime=mock_runtime)
        for i in range(3):
            session_file = tmp_path / f"session_{i}.json"
            session_file.write_text(
                json.dumps({"session_id": f"sess-{i}", "events": [{"event_type": "operation_start", "success": True}]}),
                encoding="utf-8",
            )
        results = bridge.replay_directory(tmp_path)
        assert len(results) == 3
        for count in results.values():
            assert count == 1

    def test_replay_directory_missing(self, tmp_path: Path) -> None:
        bridge = LLMuditBridge(runtime=MagicMock())
        results = bridge.replay_directory(tmp_path / "nonexistent")
        assert results == {}

    def test_replay_directory_pattern_filter(self, tmp_path: Path) -> None:
        mock_runtime = MagicMock()
        bridge = LLMuditBridge(runtime=mock_runtime)
        (tmp_path / "session.json").write_text(json.dumps({"session_id": "s1", "events": []}), encoding="utf-8")
        (tmp_path / "other.txt").write_text("not json", encoding="utf-8")
        results = bridge.replay_directory(tmp_path, pattern="*.json")
        assert len(results) == 1
        assert "session.json" in results

    def test_bridge_event_type_mapping(self, tmp_path: Path) -> None:
        mock_runtime = MagicMock()
        bridge = LLMuditBridge(runtime=mock_runtime)
        session_file = tmp_path / "session.json"
        events = [
            {"event_type": "operation_start", "success": True},
            {"event_type": "operation_complete", "success": True},
            {"event_type": "parse_error", "success": False},
            {"event_type": "validation_start", "success": True},
            {"event_type": "apply_complete", "success": True},
            {"event_type": "rollback", "success": False},
            {"event_type": "unknown_type", "success": True},
        ]
        session_file.write_text(
            json.dumps({"session_id": "s1", "events": events}),
            encoding="utf-8",
        )
        bridge.replay_session(session_file)
        types = [call.kwargs["event_type"] for call in mock_runtime.emit_event.call_args_list]
        assert types[0] == KernelAuditEventType.TOOL_EXECUTION
        assert types[1] == KernelAuditEventType.TOOL_EXECUTION
        assert types[2] == KernelAuditEventType.TASK_FAILED
        assert types[3] == KernelAuditEventType.VERIFICATION
        assert types[4] == KernelAuditEventType.FILE_CHANGE
        assert types[5] == KernelAuditEventType.TASK_FAILED
        assert types[6] == KernelAuditEventType.LLM_CALL  # default
