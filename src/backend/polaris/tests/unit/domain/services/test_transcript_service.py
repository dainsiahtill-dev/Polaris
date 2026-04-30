"""Tests for polaris.domain.services.transcript_service."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from polaris.domain.services.transcript_service import (
    MessageRole,
    TranscriptMessage,
    TranscriptService,
    TranscriptSession,
    get_transcript_service,
    reset_transcript_service,
)


class TestMessageRole:
    def test_values(self) -> None:
        assert MessageRole.SYSTEM.value == "system"
        assert MessageRole.USER.value == "user"
        assert MessageRole.ASSISTANT.value == "assistant"
        assert MessageRole.TOOL.value == "tool"


class TestTranscriptMessage:
    def test_defaults(self) -> None:
        msg = TranscriptMessage(role=MessageRole.USER, content="hello")
        assert msg.role == MessageRole.USER
        assert msg.content == "hello"
        assert isinstance(msg.timestamp, datetime)
        assert msg.metadata == {}
        assert len(msg.message_id) == 8

    def test_to_dict(self) -> None:
        msg = TranscriptMessage(
            role=MessageRole.USER,
            content="hello",
            metadata={"key": "value"},
        )
        d = msg.to_dict()
        assert d["role"] == "user"
        assert d["content"] == "hello"
        assert d["metadata"] == {"key": "value"}
        assert "timestamp" in d

    def test_from_dict(self) -> None:
        d = {
            "message_id": "abc123",
            "role": "assistant",
            "content": "hi",
            "timestamp": "2024-01-01T00:00:00+00:00",
            "metadata": {"key": "value"},
        }
        msg = TranscriptMessage.from_dict(d)
        assert msg.message_id == "abc123"
        assert msg.role == MessageRole.ASSISTANT
        assert msg.content == "hi"
        assert msg.metadata == {"key": "value"}

    def test_from_dict_defaults(self) -> None:
        d = {
            "role": "user",
            "content": "hello",
            "timestamp": "2024-01-01T00:00:00+00:00",
        }
        msg = TranscriptMessage.from_dict(d)
        assert len(msg.message_id) == 8
        assert msg.metadata == {}


class TestTranscriptSession:
    def test_defaults(self) -> None:
        session = TranscriptSession(session_id="s1", started_at=datetime.now(timezone.utc))
        assert session.session_id == "s1"
        assert session.ended_at is None
        assert session.messages == []
        assert session.metadata == {}

    def test_add_message(self) -> None:
        session = TranscriptSession(session_id="s1", started_at=datetime.now(timezone.utc))
        msg = session.add_message(MessageRole.USER, "hello")
        assert len(session.messages) == 1
        assert msg.role == MessageRole.USER
        assert msg.content == "hello"

    def test_add_message_with_str_role(self) -> None:
        session = TranscriptSession(session_id="s1", started_at=datetime.now(timezone.utc))
        msg = session.add_message("user", "hello")
        assert msg.role == MessageRole.USER

    def test_add_message_with_metadata(self) -> None:
        session = TranscriptSession(session_id="s1", started_at=datetime.now(timezone.utc))
        msg = session.add_message(MessageRole.TOOL, "result", metadata={"tool": "test"})
        assert msg.metadata == {"tool": "test"}

    def test_end_session(self) -> None:
        session = TranscriptSession(session_id="s1", started_at=datetime.now(timezone.utc))
        session.end_session()
        assert session.ended_at is not None
        assert session.ended_at.tzinfo == timezone.utc

    def test_to_dict(self) -> None:
        session = TranscriptSession(session_id="s1", started_at=datetime.now(timezone.utc))
        session.add_message(MessageRole.USER, "hello")
        d = session.to_dict()
        assert d["session_id"] == "s1"
        assert d["ended_at"] is None
        assert len(d["messages"]) == 1

    def test_to_dict_with_ended_at(self) -> None:
        session = TranscriptSession(session_id="s1", started_at=datetime.now(timezone.utc))
        session.end_session()
        d = session.to_dict()
        assert d["ended_at"] is not None

    def test_from_dict(self) -> None:
        d = {
            "session_id": "s1",
            "started_at": "2024-01-01T00:00:00+00:00",
            "ended_at": None,
            "metadata": {},
            "messages": [
                {
                    "message_id": "m1",
                    "role": "user",
                    "content": "hello",
                    "timestamp": "2024-01-01T00:00:00+00:00",
                    "metadata": {},
                }
            ],
        }
        session = TranscriptSession.from_dict(d)
        assert session.session_id == "s1"
        assert len(session.messages) == 1
        assert session.messages[0].content == "hello"

    def test_from_dict_with_ended_at(self) -> None:
        d = {
            "session_id": "s1",
            "started_at": "2024-01-01T00:00:00+00:00",
            "ended_at": "2024-01-01T01:00:00+00:00",
            "metadata": {},
            "messages": [],
        }
        session = TranscriptSession.from_dict(d)
        assert session.ended_at is not None


class TestTranscriptService:
    def test_init_creates_directory(self, tmp_path: Path) -> None:
        dir_path = tmp_path / "transcripts"
        assert not dir_path.exists()
        TranscriptService(dir_path)
        assert dir_path.exists()

    def test_start_session(self, tmp_path: Path) -> None:
        svc = TranscriptService(tmp_path)
        session = svc.start_session("test-session")
        assert session.session_id == "test-session"
        assert svc.get_current_session() is session

    def test_start_session_auto_id(self, tmp_path: Path) -> None:
        svc = TranscriptService(tmp_path)
        session = svc.start_session()
        assert len(session.session_id) == 12

    def test_start_session_ends_current(self, tmp_path: Path) -> None:
        svc = TranscriptService(tmp_path)
        session1 = svc.start_session("s1")
        session1.add_message(MessageRole.USER, "hello")
        session2 = svc.start_session("s2")
        assert svc.get_current_session() is session2
        # s1 should be persisted
        assert (tmp_path / "s1.json").exists()

    def test_end_session(self, tmp_path: Path) -> None:
        svc = TranscriptService(tmp_path)
        svc.start_session("s1")
        svc.record_message(MessageRole.USER, "hello")
        svc.end_session()
        assert svc.get_current_session() is None
        assert (tmp_path / "s1.json").exists()

    def test_record_message(self, tmp_path: Path) -> None:
        svc = TranscriptService(tmp_path)
        svc.start_session("s1")
        msg = svc.record_message(MessageRole.USER, "hello")
        assert msg is not None
        assert msg.content == "hello"

    def test_record_message_auto_start(self, tmp_path: Path) -> None:
        svc = TranscriptService(tmp_path)
        msg = svc.record_message(MessageRole.USER, "hello")
        assert msg is not None
        assert svc.get_current_session() is not None

    def test_record_tool_call(self, tmp_path: Path) -> None:
        svc = TranscriptService(tmp_path)
        svc.start_session("s1")
        svc.record_tool_call("test_tool", {"arg": 1}, "result")
        messages = svc.get_messages()
        assert len(messages) == 1
        assert messages[0]["role"] == "tool"
        assert "test_tool" in messages[0]["content"]

    def test_get_messages(self, tmp_path: Path) -> None:
        svc = TranscriptService(tmp_path)
        svc.start_session("s1")
        svc.record_message(MessageRole.USER, "hello")
        svc.record_message(MessageRole.ASSISTANT, "hi")
        messages = svc.get_messages()
        assert len(messages) == 2

    def test_get_messages_limit(self, tmp_path: Path) -> None:
        svc = TranscriptService(tmp_path)
        svc.start_session("s1")
        for i in range(5):
            svc.record_message(MessageRole.USER, f"msg {i}")
        messages = svc.get_messages(limit=3)
        assert len(messages) == 3

    def test_get_messages_no_session(self, tmp_path: Path) -> None:
        svc = TranscriptService(tmp_path)
        assert svc.get_messages() == []

    def test_load_session(self, tmp_path: Path) -> None:
        svc = TranscriptService(tmp_path)
        svc.start_session("s1")
        svc.record_message(MessageRole.USER, "hello")
        svc.end_session()

        loaded = svc.load_session("s1")
        assert loaded is not None
        assert loaded.session_id == "s1"
        assert len(loaded.messages) == 1

    def test_load_session_missing(self, tmp_path: Path) -> None:
        svc = TranscriptService(tmp_path)
        assert svc.load_session("missing") is None

    def test_load_session_invalid_json(self, tmp_path: Path) -> None:
        (tmp_path / "bad.json").write_text("not json", encoding="utf-8")
        svc = TranscriptService(tmp_path)
        assert svc.load_session("bad") is None

    def test_list_sessions(self, tmp_path: Path) -> None:
        svc = TranscriptService(tmp_path)
        svc.start_session("s1")
        svc.record_message(MessageRole.USER, "hello")
        svc.end_session()

        sessions = svc.list_sessions()
        assert len(sessions) == 1
        assert sessions[0]["session_id"] == "s1"
        assert sessions[0]["message_count"] == 1

    def test_list_sessions_invalid_file(self, tmp_path: Path) -> None:
        (tmp_path / "bad.json").write_text("not json", encoding="utf-8")
        svc = TranscriptService(tmp_path)
        sessions = svc.list_sessions()
        assert len(sessions) == 0

    def test_search(self, tmp_path: Path) -> None:
        svc = TranscriptService(tmp_path)
        svc.start_session("s1")
        svc.record_message(MessageRole.USER, "hello world")
        svc.record_message(MessageRole.ASSISTANT, "goodbye")
        svc.end_session()

        results = list(svc.search("hello"))
        assert len(results) == 1
        assert results[0].content == "hello world"

    def test_search_with_role(self, tmp_path: Path) -> None:
        svc = TranscriptService(tmp_path)
        svc.start_session("s1")
        svc.record_message(MessageRole.USER, "hello")
        svc.record_message(MessageRole.ASSISTANT, "hello back")
        svc.end_session()

        results = list(svc.search("hello", role=MessageRole.USER))
        assert len(results) == 1
        assert results[0].role == MessageRole.USER

    def test_search_no_match(self, tmp_path: Path) -> None:
        svc = TranscriptService(tmp_path)
        svc.start_session("s1")
        svc.record_message(MessageRole.USER, "hello")
        svc.end_session()

        results = list(svc.search("xyz"))
        assert len(results) == 0

    def test_export_session_json(self, tmp_path: Path) -> None:
        svc = TranscriptService(tmp_path)
        svc.start_session("s1")
        svc.record_message(MessageRole.USER, "hello")
        svc.end_session()

        exported = svc.export_session("s1", format="json")
        data = json.loads(exported)
        assert data["session_id"] == "s1"

    def test_export_session_markdown(self, tmp_path: Path) -> None:
        svc = TranscriptService(tmp_path)
        svc.start_session("s1")
        svc.record_message(MessageRole.USER, "hello")
        svc.end_session()

        exported = svc.export_session("s1", format="markdown")
        assert "# Transcript: s1" in exported
        assert "hello" in exported

    def test_export_session_txt(self, tmp_path: Path) -> None:
        svc = TranscriptService(tmp_path)
        svc.start_session("s1")
        svc.record_message(MessageRole.USER, "hello")
        svc.end_session()

        exported = svc.export_session("s1", format="txt")
        assert "Transcript: s1" in exported
        assert "hello" in exported

    def test_export_session_not_found(self, tmp_path: Path) -> None:
        svc = TranscriptService(tmp_path)
        with pytest.raises(ValueError, match="Session not found"):
            svc.export_session("missing", format="json")

    def test_export_session_unknown_format(self, tmp_path: Path) -> None:
        svc = TranscriptService(tmp_path)
        svc.start_session("s1")
        svc.record_message(MessageRole.USER, "hello")
        svc.end_session()

        with pytest.raises(ValueError, match="Unknown format"):
            svc.export_session("s1", format="xml")


class TestGlobalFunctions:
    def test_get_and_reset(self, tmp_path: Path) -> None:
        reset_transcript_service()
        svc1 = get_transcript_service(transcripts_dir=tmp_path)
        svc2 = get_transcript_service()
        assert svc1 is svc2
        reset_transcript_service()
        svc3 = get_transcript_service(transcripts_dir=tmp_path)
        assert svc3 is not svc1

    def test_get_default_dir(self) -> None:
        reset_transcript_service()
        svc = get_transcript_service()
        assert svc.transcripts_dir == Path.cwd() / ".transcripts"
        reset_transcript_service()
