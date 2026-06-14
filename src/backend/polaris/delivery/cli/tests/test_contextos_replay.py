"""Tests for the ContextOS replay CLI helpers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from polaris.delivery.cli.tools.contextos_replay import (
    load_messages_from_file,
    load_messages_from_json,
    replay_contextos_messages,
)


def test_load_messages_from_json_accepts_list_and_envelope() -> None:
    direct = load_messages_from_json('[{"role": "user", "content": "hello"}]')
    envelope = load_messages_from_json('{"messages": [{"role": "assistant", "content": "hi"}]}')

    assert direct == [{"role": "user", "content": "hello"}]
    assert envelope == [{"role": "assistant", "content": "hi"}]


def test_load_messages_from_json_rejects_non_object_entries() -> None:
    with pytest.raises(ValueError, match="message at index 1"):
        load_messages_from_json('[{"role": "user", "content": "hello"}, "bad"]')


def test_load_messages_from_file_uses_utf8(tmp_path: Path) -> None:
    payload = {"messages": [{"role": "user", "content": "你好"}]}
    path = tmp_path / "messages.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    assert load_messages_from_file(str(path)) == [{"role": "user", "content": "你好"}]


@pytest.mark.asyncio
async def test_replay_contextos_messages_returns_projection_report(tmp_path: Path) -> None:
    result = await replay_contextos_messages(
        [{"role": "user", "content": "hello", "sequence": "0"}],
        workspace=str(tmp_path),
    )

    assert result["ok"] is True
    assert result["projection"]["snapshot_event_count"] == 1
    assert result["projection_report"]["projection_id"]
    assert result["projection_report"]["candidate_count"] >= 1
