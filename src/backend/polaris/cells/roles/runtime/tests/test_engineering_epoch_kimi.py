from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path

from polaris.cells.roles.runtime.internal.engineering_epoch_kimi import KimiEpochPool, kimi_epoch_pool_from_environment


def test_devourer_role_binding_drives_kimi_pool(tmp_path: Path, monkeypatch) -> None:
    config = tmp_path / "llm.json"
    config.write_text(
        json.dumps(
            {
                "providers": {"kimi": {"base_url": "https://kimi.example", "api_key": "secret"}},
                "roles": {"devourer": {"provider_id": "kimi", "model": "kimi-for-coding-highspeed"}},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("KERNELONE_LLM_CONFIG", str(config))
    pool = kimi_epoch_pool_from_environment()
    assert pool is not None and pool.model == "kimi-for-coding-highspeed"
    assert pool.base_url == "https://kimi.example"


def test_kimi_epoch_pool_validates_summary() -> None:
    pool = KimiEpochPool(base_url="https://kimi.example", api_key="secret", model="kimi-for-coding-highspeed")
    pool._post = lambda _request: json.dumps(
        {
            "node_id": "epoch-0001-0010",
            "snapshot_id": "epoch-0001-0010",
            "summary": "完成基础模块。",
            "summary_kind": "model_inference",
            "coverage": {"observed_line_ranges": [[1, 1]], "summary_complete": False},
            "evidence_refs": [],
            "uncertain": False,
            "reason": "基于关闭 epoch。",
        }
    )
    result = asyncio.run(
        pool.summarize_many(
            [
                {
                    "node_id": "epoch-0001-0010",
                    "snapshot_id": "epoch-0001-0010",
                    "coverage": {"observed_line_ranges": [[1, 1]]},
                    "allowed_evidence_refs": [],
                }
            ]
        )
    )
    assert result[0]["validation_result"] == "VALID"


def _epoch_request() -> dict[str, object]:
    return {
        "node_id": "epoch-0001-0010",
        "snapshot_id": "epoch-0001-0010",
        "coverage": {"observed_line_ranges": [[1, 1]]},
        "allowed_evidence_refs": [],
    }


def _epoch_payload(summary: str) -> str:
    return json.dumps(
        {
            "node_id": "epoch-0001-0010",
            "snapshot_id": "epoch-0001-0010",
            "summary": summary,
            "summary_kind": "model_inference",
            "coverage": {"observed_line_ranges": [[1, 1]], "summary_complete": False},
            "evidence_refs": [],
            "uncertain": False,
            "reason": "基于关闭 epoch。",
        }
    )


def test_kimi_epoch_pool_retries_once_after_a_rejected_summary() -> None:
    """One over-long summary must not permanently starve the epoch memory."""

    pool = KimiEpochPool(base_url="https://kimi.example", api_key="secret", model="kimi-for-coding-highspeed")
    calls = {"count": 0}
    responses = [_epoch_payload("S" * 2000), _epoch_payload("完成基础模块并保留未解决失败。")]

    def _post(messages: list[dict[str, str]]) -> str:
        response = responses[calls["count"]]
        calls["count"] += 1
        return response

    pool._post = _post  # type: ignore[method-assign]
    result = asyncio.run(pool.summarize_many([_epoch_request()]))
    assert calls["count"] == 2
    assert result[0]["validation_result"] == "VALID"


def test_kimi_epoch_pool_reports_rejection_after_the_retry() -> None:
    pool = KimiEpochPool(base_url="https://kimi.example", api_key="secret", model="kimi-for-coding-highspeed")

    def _post(messages: list[dict[str, str]]) -> str:
        return _epoch_payload("S" * 2000)

    pool._post = _post  # type: ignore[method-assign]
    result = asyncio.run(pool.summarize_many([_epoch_request()]))
    assert result[0]["validation_result"] == "REJECTED"
    assert "summary" in result[0]["error"]


def _compact_exchange(monkeypatch, replies):
    from polaris.cells.roles.runtime.internal.engineering_epoch_runtime import EpochRuntime, facts_for_epoch
    from polaris.cells.roles.runtime.tests.test_engineering_memory_cards import _closed_epoch

    sent = []

    def post(req, **kwargs):
        sent.append(json.loads(req.data))
        return io.BytesIO(json.dumps(replies[min(len(sent) - 1, len(replies) - 1)]).encode("utf-8"))

    monkeypatch.setattr("urllib.request.urlopen", post)

    async def draw():
        req, refs, fp = facts_for_epoch(
            {"instruction": "Investigate npm test. " + "long irrelevant goal " * 300}, _closed_epoch()
        )
        runtime = EpochRuntime(KimiEpochPool(base_url="https://unit.invalid", api_key="test", model="kimi"))
        runtime.schedule(req, refs, fingerprint=fp)
        await runtime._job
        return runtime.poll()

    return sent, asyncio.run(draw())


def _reply(obj):
    return {
        "content": [{"type": "text", "text": json.dumps(obj)}],
        "stop_reason": "end_turn",
        "usage": {"output_tokens": 16},
    }


def test_compact_epoch_selects_only_observed_refs_and_sends_bounded_cues_once(monkeypatch):
    ref = "event:18"
    result = {
        "recall_refs": [ref],
        "exploration": {
            "open_question": "Did setup fail?",
            "discriminating_check": "Inspect the recorded check output.",
            "evidence_refs": [ref],
        },
    }
    sent, card = _compact_exchange(monkeypatch, [_reply(result)])
    assert len(sent) == 1 and sent[0]["thinking"] == {"type": "disabled"}
    wire = json.loads(sent[0]["messages"][1]["content"])
    assert set(wire) == {"goal_cue", "observations"} and len(wire["goal_cue"]) <= 240
    assert sum(row["ref"] == ref for row in wire["observations"]) == 1
    assert card["recall_refs"] == [ref] and card["exploration"] == result["exploration"]
    assert card["advice_status"] == "probe_proposed" and card["provider_attempts"][0]["usage"]["output_tokens"] == 16


def test_compact_epoch_accepts_explicit_abstention_and_rejects_unoffered_recall(monkeypatch):
    sent, card = _compact_exchange(monkeypatch, [_reply({"abstain": True})])
    assert len(sent) == 1 and card["advice_status"] == "abstained" and not card["recall_refs"]
    assert "early.ts" in card["summary"]
    sent, card = _compact_exchange(monkeypatch, [_reply({"recall_refs": ["event:999"]})])
    assert card["advice_status"] == "rejected" and not card["recall_refs"] and "early.ts" in card["summary"]


def test_thinking_only_epoch_records_counts_without_reading_reasoning_or_retrying(monkeypatch):
    sent, card = _compact_exchange(
        monkeypatch,
        [
            {
                "content": [{"type": "thinking", "thinking": "SECRET DELIBERATION"}],
                "stop_reason": "max_tokens",
                "usage": {"output_tokens": 1400, "output_tokens_details": {"thinking_tokens": 1399}},
            }
        ],
    )
    assert len(sent) == 1 and card["advice_status"] == "rejected"
    assert card["provider_attempts"][0]["stop_reason"] == "max_tokens"
    assert card["provider_attempts"][0]["usage"]["output_tokens_details"]["thinking_tokens"] == 1399
    assert "SECRET DELIBERATION" not in json.dumps(card)


async def test_empty_observation_offer_skips_provider_and_records_that_no_draw_was_requested():
    from polaris.cells.roles.runtime.internal.engineering_epoch_runtime import EpochRuntime, facts_for_epoch

    events = [{"seq": n, "kind": "turn_started", "payload": {}} for n in range(1, 31)]
    pool = KimiEpochPool(base_url="https://unit.invalid", api_key="test", model="test")
    pool._post = lambda _messages: (_ for _ in ()).throw(AssertionError("no network request expected"))
    request, refs, fp = facts_for_epoch({}, events)
    runtime = EpochRuntime(pool)
    runtime.schedule(request, refs, fingerprint=fp)
    await runtime._job
    card = runtime.poll()
    assert card["validation_result"] == "NOT_REQUESTED" and card["advice_status"] == "no_observed_receipts"
    assert card["provider_attempts"] == [] and "model_advice" not in card
