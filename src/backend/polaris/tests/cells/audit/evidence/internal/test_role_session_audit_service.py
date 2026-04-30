"""Tests for RoleSessionAuditService — audit event recording and querying.

Coverage targets:
- All 5 public methods
- Mock KernelFileSystem to avoid real I/O
- Edge cases: missing session, bad JSON, pagination, filtering
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from polaris.cells.audit.evidence.internal.role_session_audit_service import (
    RoleSessionAuditService,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_fs() -> MagicMock:
    """Return a mocked KernelFileSystem."""
    return MagicMock()


@pytest.fixture
def service(mock_fs: MagicMock, mock_workspace: str) -> RoleSessionAuditService:
    """Return a RoleSessionAuditService with a mocked KernelFileSystem."""
    with (
        patch(
            "polaris.cells.audit.evidence.internal.role_session_audit_service.KernelAuditRuntime"
        ) as mock_runtime_cls,
        patch(
            "polaris.cells.audit.evidence.internal.role_session_audit_service.KernelFileSystem",
            return_value=mock_fs,
        ),
        patch("polaris.cells.audit.evidence.internal.role_session_audit_service.resolve_storage_roots") as mock_resolve,
    ):
        mock_resolve.return_value = MagicMock(runtime_root=mock_workspace)
        mock_runtime = MagicMock()
        mock_runtime_cls.get_instance.return_value = mock_runtime
        svc = RoleSessionAuditService(workspace=Path(mock_workspace))
        # Expose mock for assertions
        svc._fs = mock_fs  # type: ignore[method-assign]
        svc._runtime = mock_runtime  # type: ignore[method-assign]
        return svc


@pytest.fixture
def sample_events() -> list[dict[str, Any]]:
    """Return a list of sample audit event dicts."""
    return [
        {"id": "evt1", "type": "session_created", "details": {}, "timestamp": "2024-01-01T00:00:00+00:00"},
        {"id": "evt2", "type": "message_sent", "details": {"text": "hello"}, "timestamp": "2024-01-01T00:01:00+00:00"},
        {
            "id": "evt3",
            "type": "message_received",
            "details": {"text": "world"},
            "timestamp": "2024-01-01T00:02:00+00:00",
        },
        {"id": "evt4", "type": "message_sent", "details": {"text": "again"}, "timestamp": "2024-01-01T00:03:00+00:00"},
        {"id": "evt5", "type": "session_closed", "details": {}, "timestamp": "2024-01-01T00:04:00+00:00"},
    ]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_jsonl(events: list[dict[str, Any]]) -> str:
    """Serialize events as newline-delimited JSON."""
    return "\n".join(json.dumps(ev, ensure_ascii=False) for ev in events)


# ---------------------------------------------------------------------------
# 1. append_audit_event
# ---------------------------------------------------------------------------


def test_append_audit_event_returns_payload(service: RoleSessionAuditService, mock_fs: MagicMock) -> None:
    """append_audit_event must return a dict with id, type, details, timestamp."""
    result = service.append_audit_event("sess-1", "session_created")
    assert "id" in result
    assert result["type"] == "session_created"
    assert "timestamp" in result
    assert result["details"] == {}


def test_append_audit_event_writes_jsonl(service: RoleSessionAuditService, mock_fs: MagicMock) -> None:
    """append_audit_event must call fs.append_jsonl with the event payload."""
    service.append_audit_event("sess-1", "message_sent", {"text": "hi"})
    assert mock_fs.append_jsonl.call_count == 1
    logical_path, payload = mock_fs.append_jsonl.call_args[0]
    assert logical_path == "workspace/role_sessions/sess-1/audit/events.jsonl"
    assert payload["type"] == "message_sent"
    assert payload["details"] == {"text": "hi"}


def test_append_audit_event_emits_runtime_event(service: RoleSessionAuditService, mock_fs: MagicMock) -> None:
    """append_audit_event must emit a KernelAuditRuntime event."""
    service.append_audit_event("sess-1", "error_occurred", {"msg": "boom"})
    assert service._runtime.emit_event.call_count == 1  # type: ignore[attr-defined]
    kwargs = service._runtime.emit_event.call_args[1]  # type: ignore[attr-defined]
    assert kwargs["task_id"] == "role-session-sess-1"
    assert kwargs["action"]["name"] == "error_occurred"


def test_append_audit_event_rejects_empty_session_id(service: RoleSessionAuditService) -> None:
    """append_audit_event with an empty session_id must raise ValueError."""
    with pytest.raises(ValueError, match="session_id is required"):
        service.append_audit_event("", "session_created")


def test_append_audit_event_rejects_whitespace_session_id(service: RoleSessionAuditService) -> None:
    """append_audit_event with a whitespace-only session_id must raise ValueError."""
    with pytest.raises(ValueError, match="session_id is required"):
        service.append_audit_event("   ", "session_created")


def test_append_audit_event_strips_event_type(service: RoleSessionAuditService, mock_fs: MagicMock) -> None:
    """append_audit_event must strip whitespace from the event type string."""
    service.append_audit_event("sess-1", "  message_sent  ")
    _logical_path, payload = mock_fs.append_jsonl.call_args[0]
    assert payload["type"] == "message_sent"


# ---------------------------------------------------------------------------
# 2. get_events
# ---------------------------------------------------------------------------


def test_get_events_reads_existing_file(
    service: RoleSessionAuditService, mock_fs: MagicMock, sample_events: list[dict[str, Any]]
) -> None:
    """get_events must parse all lines from the JSONL file."""
    mock_fs.exists.return_value = True
    mock_fs.read_text.return_value = _build_jsonl(sample_events)
    events = service.get_events("sess-1")
    assert len(events) == 5
    assert events[0]["id"] == "evt1"


def test_get_events_returns_empty_when_file_missing(service: RoleSessionAuditService, mock_fs: MagicMock) -> None:
    """get_events must return [] when the audit file does not exist."""
    mock_fs.exists.return_value = False
    assert service.get_events("sess-1") == []


def test_get_events_filters_by_type(
    service: RoleSessionAuditService, mock_fs: MagicMock, sample_events: list[dict[str, Any]]
) -> None:
    """get_events with event_type must return only matching events."""
    mock_fs.exists.return_value = True
    mock_fs.read_text.return_value = _build_jsonl(sample_events)
    events = service.get_events("sess-1", event_type="message_sent")
    assert len(events) == 2
    assert all(ev["type"] == "message_sent" for ev in events)


def test_get_events_respects_limit(
    service: RoleSessionAuditService, mock_fs: MagicMock, sample_events: list[dict[str, Any]]
) -> None:
    """get_events limit must cap the number of returned events."""
    mock_fs.exists.return_value = True
    mock_fs.read_text.return_value = _build_jsonl(sample_events)
    events = service.get_events("sess-1", limit=2)
    assert len(events) == 2
    assert events[0]["id"] == "evt1"
    assert events[1]["id"] == "evt2"


def test_get_events_respects_offset(
    service: RoleSessionAuditService, mock_fs: MagicMock, sample_events: list[dict[str, Any]]
) -> None:
    """get_events offset must skip the first N events."""
    mock_fs.exists.return_value = True
    mock_fs.read_text.return_value = _build_jsonl(sample_events)
    events = service.get_events("sess-1", offset=2)
    assert len(events) == 3
    assert events[0]["id"] == "evt3"


def test_get_events_skips_malformed_json(service: RoleSessionAuditService, mock_fs: MagicMock) -> None:
    """get_events must silently skip lines that are not valid JSON."""
    mock_fs.exists.return_value = True
    mock_fs.read_text.return_value = '{"id":"a"}\nBAD_JSON\n{"id":"b"}'
    events = service.get_events("sess-1")
    assert len(events) == 2
    assert events[0]["id"] == "a"
    assert events[1]["id"] == "b"


def test_get_events_skips_non_dict_json(service: RoleSessionAuditService, mock_fs: MagicMock) -> None:
    """get_events must skip JSON values that are not objects."""
    mock_fs.exists.return_value = True
    mock_fs.read_text.return_value = '{"id":"a"}\n[1,2,3]\n{"id":"b"}'
    events = service.get_events("sess-1")
    assert len(events) == 2


def test_get_events_skips_blank_lines(service: RoleSessionAuditService, mock_fs: MagicMock) -> None:
    """get_events must ignore empty or whitespace-only lines."""
    mock_fs.exists.return_value = True
    mock_fs.read_text.return_value = '\n{"id":"a"}\n   \n{"id":"b"}\n'
    events = service.get_events("sess-1")
    assert len(events) == 2


def test_get_events_coerces_none_limit_to_default(service: RoleSessionAuditService, mock_fs: MagicMock) -> None:
    """get_events with None limit must fall back to the default (100)."""
    mock_fs.exists.return_value = True
    mock_fs.read_text.return_value = _build_jsonl([{"id": f"evt{i}"} for i in range(150)])
    events = service.get_events("sess-1", limit=None)  # type: ignore[arg-type]
    assert len(events) == 100


def test_get_events_coerces_none_offset_to_zero(service: RoleSessionAuditService, mock_fs: MagicMock) -> None:
    """get_events with None offset must fall back to 0."""
    mock_fs.exists.return_value = True
    mock_fs.read_text.return_value = _build_jsonl([{"id": "first"}, {"id": "second"}])
    events = service.get_events("sess-1", offset=None)  # type: ignore[arg-type]
    assert events[0]["id"] == "first"


# ---------------------------------------------------------------------------
# 3. get_event_count
# ---------------------------------------------------------------------------


def test_get_event_count_basic(
    service: RoleSessionAuditService, mock_fs: MagicMock, sample_events: list[dict[str, Any]]
) -> None:
    """get_event_count must return the total number of valid events."""
    mock_fs.exists.return_value = True
    mock_fs.resolve_path.return_value = Path("/tmp/fake_audit.jsonl")
    with patch("builtins.open", _mock_file_opener(_build_jsonl(sample_events))):
        count = service.get_event_count("sess-1")
    assert count == 5


def test_get_event_count_missing_file(service: RoleSessionAuditService, mock_fs: MagicMock) -> None:
    """get_event_count must return 0 when the file does not exist."""
    mock_fs.exists.return_value = False
    assert service.get_event_count("sess-1") == 0


def test_get_event_count_filtered(
    service: RoleSessionAuditService, mock_fs: MagicMock, sample_events: list[dict[str, Any]]
) -> None:
    """get_event_count with event_type must count only matching events."""
    mock_fs.exists.return_value = True
    mock_fs.resolve_path.return_value = Path("/tmp/fake_audit.jsonl")
    with patch("builtins.open", _mock_file_opener(_build_jsonl(sample_events))):
        count = service.get_event_count("sess-1", event_type="message_sent")
    assert count == 2


def test_get_event_count_skips_bad_json(service: RoleSessionAuditService, mock_fs: MagicMock) -> None:
    """get_event_count must skip malformed JSON lines."""
    mock_fs.exists.return_value = True
    mock_fs.resolve_path.return_value = Path("/tmp/fake_audit.jsonl")
    with patch("builtins.open", _mock_file_opener('{"id":"a"}\nBAD\n{"id":"b"}')):
        count = service.get_event_count("sess-1")
    assert count == 2


def test_get_event_count_oserror_returns_zero(service: RoleSessionAuditService, mock_fs: MagicMock) -> None:
    """get_event_count must return 0 on OSError (e.g. permission denied)."""
    mock_fs.exists.return_value = True
    mock_fs.resolve_path.return_value = Path("/tmp/fake_audit.jsonl")
    with patch("builtins.open", side_effect=OSError("permission denied")):
        assert service.get_event_count("sess-1") == 0


# ---------------------------------------------------------------------------
# 4. export_audit_log
# ---------------------------------------------------------------------------


def test_export_audit_log_writes_json(
    service: RoleSessionAuditService, mock_fs: MagicMock, sample_events: list[dict[str, Any]]
) -> None:
    """export_audit_log must write a JSON file with session metadata and events."""
    mock_fs.exists.return_value = True
    mock_fs.read_text.return_value = _build_jsonl(sample_events)
    mock_fs.write_json.return_value = MagicMock(absolute_path="/tmp/export.json")
    target = Path("/tmp/export.json")
    result = service.export_audit_log("sess-1", target)
    assert result == target
    assert mock_fs.write_json.call_count == 1
    _path, payload = mock_fs.write_json.call_args[0]
    assert payload["session_id"] == "sess-1"
    assert payload["event_count"] == 5
    assert len(payload["events"]) == 5
    assert "exported_at" in payload


def test_export_audit_log_empty_session(service: RoleSessionAuditService, mock_fs: MagicMock) -> None:
    """export_audit_log on an empty session must write event_count 0."""
    mock_fs.exists.return_value = False
    mock_fs.write_json.return_value = MagicMock(absolute_path="/tmp/export.json")
    target = Path("/tmp/export.json")
    result = service.export_audit_log("sess-empty", target)
    assert result == target
    _path, payload = mock_fs.write_json.call_args[0]
    assert payload["event_count"] == 0
    assert payload["events"] == []


# ---------------------------------------------------------------------------
# 5. Edge cases and validation
# ---------------------------------------------------------------------------


def test_get_events_rejects_empty_session_id(service: RoleSessionAuditService) -> None:
    """get_events with empty session_id must raise ValueError via _get_audit_logical_path."""
    with pytest.raises(ValueError, match="session_id is required"):
        service.get_events("")


def test_get_event_count_rejects_empty_session_id(service: RoleSessionAuditService) -> None:
    """get_event_count with empty session_id must raise ValueError."""
    with pytest.raises(ValueError, match="session_id is required"):
        service.get_event_count("")


def test_export_audit_log_rejects_empty_session_id(service: RoleSessionAuditService) -> None:
    """export_audit_log with empty session_id must raise ValueError."""
    with pytest.raises(ValueError, match="session_id is required"):
        service.export_audit_log("", Path("/tmp/out.json"))


def test_event_types_set_is_complete() -> None:
    """EVENT_TYPES must contain the canonical set of supported event names."""
    expected = {
        "session_created",
        "session_resumed",
        "message_sent",
        "message_received",
        "artifact_created",
        "artifact_deleted",
        "artifact_exported",
        "workflow_exported",
        "error_occurred",
        "session_closed",
    }
    assert expected == RoleSessionAuditService.EVENT_TYPES


# ---------------------------------------------------------------------------
# Helpers — mock file opener for get_event_count streaming
# ---------------------------------------------------------------------------


def _mock_file_opener(content: str):
    """Return a mock open() callable that yields the content as lines."""
    lines = content.splitlines(keepends=True)

    class _MockFile:
        def __enter__(self) -> _MockFile:
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

        def __iter__(self) -> Any:
            return iter(lines)

    def opener(_path, **_kwargs):
        return _MockFile()

    return opener
