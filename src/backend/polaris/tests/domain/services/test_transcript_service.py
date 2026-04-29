# ruff: noqa: E402
"""Tests for polaris.domain.services.transcript_service module."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

BACKEND_DIR = str(Path(__file__).resolve().parents[4])
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from polaris.domain.services.transcript_service import (
    MessageRole,
    TranscriptMessage,
    TranscriptService,
    TranscriptSession,
    get_transcript_service,
    reset_transcript_service,
)


class TestMessageRole:
    def test_enum_values(self) -> None:
        assert MessageRole.SYSTEM == "system"
        assert MessageRole.USER == "user"
        assert MessageRole.ASSISTANT == "assistant"
        assert MessageRole.TOOL == "tool"


class TestTranscriptMessage:
    def test_creation(self) -> None:
        msg = TranscriptMessage(role=MessageRole.USER, content="hello")
        assert msg.role == MessageRole.USER
        assert msg.content == "hello"
        assert msg.timestamp is not None
        assert len(msg.message_id) == 8

    def test_to_dict(self) -> None:
        msg = TranscriptMessage(role=MessageRole.USER, content="hello", metadata={"key": "val"})
        d = msg.to_dict()
        assert d["role"] == "user"
        assert d["content"] == "hello"
        assert d["metadata"] == {"key": "val"}
        assert "message_id" in d

    def test_from_dict(self) -> None:
        now = datetime.now(timezone.utc)
        msg = TranscriptMessage.from_dict(
            {"message_id": "abc123", "role": "assistant", "content": "hi", "timestamp": now.isoformat(), "metadata": {}}
        )
        assert msg.role == MessageRole.ASSISTANT
        assert msg.content == "hi"
        assert msg.message_id == "abc123"


class TestTranscriptSession:
    def test_creation(self) -> None:
        session = TranscriptSession(session_id="sess-1", started_at=datetime.now(timezone.utc))
        assert session.session_id == "sess-1"
        assert session.ended_at is None
        assert session.messages == []

    def test_add_message(self) -> None:
        session = TranscriptSession(session_id="sess-1", started_at=datetime.now(timezone.utc))
        msg = session.add_message(MessageRole.USER, "hello")
        assert len(session.messages) == 1
        assert msg.content == "hello"

    def test_add_message_with_string_role(self) -> None:
        session = TranscriptSession(session_id="sess-1", started_at=datetime.now(timezone.utc))
        msg = session.add_message("tool", "result")
        assert msg.role == MessageRole.TOOL

    def test_end_session(self) -> None:
        session = TranscriptSession(session_id="sess-1", started_at=datetime.now(timezone.utc))
        session.end_session()
        assert session.ended_at is not None

    def test_to_dict(self) -> None:
        session = TranscriptSession(session_id="sess-1", started_at=datetime.now(timezone.utc))
        session.add_message(MessageRole.USER, "hello")
        d = session.to_dict()
        assert d["session_id"] == "sess-1"
        assert len(d["messages"]) == 1
        assert d["ended_at"] is None

    def test_from_dict(self) -> None:
        now = datetime.now(timezone.utc)
        session = TranscriptSession.from_dict(
            {
                "session_id": "s1",
                "started_at": now.isoformat(),
                "ended_at": None,
                "metadata": {},
                "messages": [
                    {"message_id": "m1", "role": "user", "content": "hi", "timestamp": now.isoformat(), "metadata": {}}
                ],
            }
        )
        assert session.session_id == "s1"
        assert len(session.messages) == 1


class TestTranscriptService:
    @pytest.fixture(autouse=True)
    def reset_singleton(self):
        reset_transcript_service()
        yield
        reset_transcript_service()

    def test_start_session(self, tmp_path) -> None:
        svc = TranscriptService(tmp_path)
        session = svc.start_session("my-session")
        assert session.session_id == "my-session"
        assert svc.get_current_session() is not None

    def test_start_session_ends_previous(self, tmp_path) -> None:
        svc = TranscriptService(tmp_path)
        svc.start_session("first")
        svc.start_session("second")
        sessions = svc.list_sessions()
        assert len(sessions) == 1
        assert sessions[0]["session_id"] == "first"

    def test_end_session(self, tmp_path) -> None:
        svc = TranscriptService(tmp_path)
        svc.start_session("s1")
        svc.end_session()
        assert svc.get_current_session() is None
        sessions = svc.list_sessions()
        assert len(sessions) == 1

    def test_record_message(self, tmp_path) -> None:
        svc = TranscriptService(tmp_path)
        svc.start_session("s1")
        msg = svc.record_message(MessageRole.USER, "hello")
        assert msg is not None
        assert msg.content == "hello"

    def test_record_message_auto_start(self, tmp_path) -> None:
        svc = TranscriptService(tmp_path)
        msg = svc.record_message(MessageRole.USER, "hello")
        assert msg is not None
        assert svc.get_current_session() is not None

    def test_record_tool_call(self, tmp_path) -> None:
        svc = TranscriptService(tmp_path)
        svc.start_session("s1")
        svc.record_tool_call("read_file", {"path": "foo.txt"}, "content")
        msgs = svc.get_messages()
        assert len(msgs) == 1
        assert msgs[0]["role"] == "tool"

    def test_get_messages(self, tmp_path) -> None:
        svc = TranscriptService(tmp_path)
        svc.start_session("s1")
        svc.record_message(MessageRole.USER, "a")
        svc.record_message(MessageRole.ASSISTANT, "b")
        msgs = svc.get_messages(limit=1)
        assert len(msgs) == 1

    def test_get_messages_no_session(self, tmp_path) -> None:
        svc = TranscriptService(tmp_path)
        assert svc.get_messages() == []

    def test_load_session(self, tmp_path) -> None:
        svc = TranscriptService(tmp_path)
        svc.start_session("s1")
        svc.record_message(MessageRole.USER, "hello")
        svc.end_session()
        loaded = svc.load_session("s1")
        assert loaded is not None
        assert loaded.session_id == "s1"
        assert len(loaded.messages) == 1

    def test_load_session_missing(self, tmp_path) -> None:
        svc = TranscriptService(tmp_path)
        assert svc.load_session("missing") is None

    def test_load_session_corrupted(self, tmp_path) -> None:
        svc = TranscriptService(tmp_path)
        (tmp_path / "bad.json").write_text("not json", encoding="utf-8")
        assert svc.load_session("bad") is None

    def test_list_sessions(self, tmp_path) -> None:
        svc = TranscriptService(tmp_path)
        svc.start_session("s1")
        svc.end_session()
        sessions = svc.list_sessions()
        assert len(sessions) == 1
        assert sessions[0]["session_id"] == "s1"
        assert sessions[0]["message_count"] == 0

    def test_search(self, tmp_path) -> None:
        svc = TranscriptService(tmp_path)
        svc.start_session("s1")
        svc.record_message(MessageRole.USER, "hello world")
        svc.end_session()
        results = list(svc.search("hello"))
        assert len(results) == 1
        assert results[0].content == "hello world"

    def test_search_with_role_filter(self, tmp_path) -> None:
        svc = TranscriptService(tmp_path)
        svc.start_session("s1")
        svc.record_message(MessageRole.USER, "hello")
        svc.record_message(MessageRole.ASSISTANT, "world")
        svc.end_session()
        results = list(svc.search("hello", role=MessageRole.ASSISTANT))
        assert len(results) == 0

    def test_export_json(self, tmp_path) -> None:
        svc = TranscriptService(tmp_path)
        svc.start_session("s1")
        svc.record_message(MessageRole.USER, "hello")
        svc.end_session()
        exported = svc.export_session("s1", format="json")
        assert "session_id" in exported
        assert "role" in exported

    def test_export_markdown(self, tmp_path) -> None:
        svc = TranscriptService(tmp_path)
        svc.start_session("s1")
        svc.record_message(MessageRole.USER, "hello")
        svc.end_session()
        exported = svc.export_session("s1", format="markdown")
        assert "# Transcript: s1" in exported
        assert "hello" in exported

    def test_export_txt(self, tmp_path) -> None:
        svc = TranscriptService(tmp_path)
        svc.start_session("s1")
        svc.record_message(MessageRole.USER, "hello")
        svc.end_session()
        exported = svc.export_session("s1", format="txt")
        assert "Transcript: s1" in exported
        assert "hello" in exported

    def test_export_unknown_format(self, tmp_path) -> None:
        svc = TranscriptService(tmp_path)
        svc.start_session("s1")
        svc.end_session()
        with pytest.raises(ValueError, match="Unknown format"):
            svc.export_session("s1", format="xml")

    def test_export_session_not_found(self, tmp_path) -> None:
        svc = TranscriptService(tmp_path)
        with pytest.raises(ValueError, match="Session not found"):
            svc.export_session("missing", format="json")

    def test_singleton(self, tmp_path) -> None:
        reset_transcript_service()
        svc1 = get_transcript_service(transcripts_dir=tmp_path)
        svc2 = get_transcript_service()
        assert svc1 is svc2
