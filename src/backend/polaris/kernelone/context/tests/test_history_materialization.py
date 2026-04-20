"""Tests for history materialization strategies."""

from __future__ import annotations

import pytest
from polaris.kernelone.context.history_materialization import (
    HistoryMaterializationStrategy,
    SessionContinuityStrategy,
    _estimate_tokens_for_messages,
)


def test_session_strategy_materialize_strips_reasoning_tokens() -> None:
    strategy = SessionContinuityStrategy(
        profile_overrides={"compaction": {"receipt_micro_compact": False}},
    )
    messages = [
        {"role": "assistant", "content": "<thinking>内部推理</thinking>最终答复"},
    ]
    receipts = [
        {"tool": "read_file", "content": "<thinking>trace</thinking>读取成功"},
    ]

    raw_total = _estimate_tokens_for_messages(messages) + _estimate_tokens_for_messages(receipts)
    materialized = strategy.materialize(messages, receipts)

    assert materialized.total_tokens < raw_total
    assert materialized.message_count == 1
    assert materialized.receipt_count == 1


def test_history_strategy_micro_compacts_after_reasoning_strip() -> None:
    strategy = HistoryMaterializationStrategy(
        profile_overrides={
            "compaction": {
                "receipt_micro_compact": True,
                "micro_compact_keep": 1,
                "compress_threshold_chars": 80,
            }
        }
    )
    messages = [{"role": "user", "content": "继续执行"}]
    receipts = [
        {"tool": "search", "content": "<thinking>a</thinking>" + ("x" * 180)},
        {"tool": "search", "content": "<thinking>b</thinking>" + ("x" * 180)},
        {"tool": "search", "content": "<thinking>c</thinking>" + ("x" * 180)},
        {"tool": "search", "content": "<thinking>d</thinking>" + ("x" * 180)},
    ]

    materialized = strategy.materialize(messages, receipts)

    assert materialized.micro_compacted is True
    assert materialized.receipt_count < len(receipts)
    assert materialized.total_tokens > 0
    assert materialized.artifact_stub_count >= 1
    assert materialized.materialized_receipts
    assert any(
        isinstance(item.get("_artifact_stub"), dict)
        for item in materialized.materialized_receipts
        if isinstance(item, dict)
    )


def test_history_strategy_emits_restorable_artifact_stub_for_large_receipt() -> None:
    strategy = HistoryMaterializationStrategy(
        profile_overrides={
            "compaction": {
                "receipt_micro_compact": True,
                "micro_compact_keep": 0,
                "compress_threshold_chars": 64,
            }
        }
    )
    materialized = strategy.materialize(
        messages=[{"role": "user", "content": "继续"}],
        receipts=[
            {
                "tool": "read_file",
                "content": "line 1\n" + ("x" * 200),
            }
        ],
    )

    assert materialized.artifact_stub_count == 1
    assert len(materialized.materialized_receipts) == 1
    receipt = materialized.materialized_receipts[0]
    assert receipt["_artifact_stub"]["type"] == "file_excerpt"
    assert "artifact_stub:" in str(receipt.get("content") or "")
    assert "restore_hint" in receipt["_artifact_stub"]


@pytest.mark.asyncio
async def test_session_strategy_builds_context_os_continuity_prompt_block() -> None:
    strategy = SessionContinuityStrategy()
    block = await strategy.build_continuity_prompt_block(
        {
            "session_id": "sess_ctx",
            "role": "director",
            "workspace": "C:/repo",
            "messages": [
                {
                    "sequence": 1,
                    "role": "user",
                    "content": "Fix polaris/kernelone/context/session_continuity.py and add tests.",
                },
                {
                    "sequence": 2,
                    "role": "assistant",
                    "content": "I will patch the continuity runtime and run tests.",
                },
            ],
            "history_limit": 1,
            "focus": "Preserve working memory and active code entities.",
            "incoming_context": {"context_os_domain": "code"},
        }
    )

    assert "State-First Context OS" in block
    assert "Current goal:" in block
    assert "session_continuity.py" in block
