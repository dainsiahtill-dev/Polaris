from __future__ import annotations

import json

import pytest
from polaris.cells.roles.runtime.internal.engineering_file_cards import build_file_card
from polaris.cells.roles.runtime.internal.engineering_tool_payload_projection import fold_tool_payload_previews


@pytest.fixture(autouse=True)
def _result_plane_size_floor(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise the fold mechanism at test sizes, not the production floor."""

    monkeypatch.setenv("KERNELONE_CONTEXTOS_FOLD_RESULT_MIN_BYTES", "512")


def _message(path: str, preview: str) -> dict[str, object]:
    card = build_file_card(
        path,
        preview,
        revision=1,
        preview=preview,
        truncated=False,
        start_line=1,
        end_line=2,
        snapshot_ref=f"filecard:{'a' * 64}",
    )
    return {"role": "tool", "content": json.dumps({"file_card": card})}


def _read_call(call_id: str, path: str) -> dict[str, object]:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": "read_file", "arguments": json.dumps({"file": path})},
            }
        ],
    }


def _pad(turns: int) -> list[dict[str, object]]:
    return [{"role": "assistant", "content": f"step-{index}"} for index in range(turns)]


def test_result_plane_demands_a_larger_payload_than_the_call_plane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Folding a read result costs a re-read; folding a write body costs nothing."""

    monkeypatch.setenv("KERNELONE_CONTEXTOS_INACTIVE_FOLD_ACTIVITIES", "24")
    monkeypatch.setenv("KERNELONE_CONTEXTOS_FOLD_RESULT_MIN_BYTES", "4096")
    preview = "p" * 3000
    messages = [_message("src/a.ts", preview), *_pad(25)]
    folded = fold_tool_payload_previews(messages)
    assert json.loads(folded[0]["content"])["file_card"]["fold_state"] == "ACTIVE"

    large = fold_tool_payload_previews([_message("src/a.ts", "p" * 5000), *_pad(25)])
    assert json.loads(large[0]["content"])["file_card"]["fold_state"] == "FOLDED"


def test_read_focus_window_protects_a_path_the_model_keeps_returning_to(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A file read inside the focus window stays live however old its card is."""

    monkeypatch.setenv("KERNELONE_CONTEXTOS_INACTIVE_FOLD_ACTIVITIES", "24")
    hot = [_message("src/hot.ts", "h" * 3000), *_pad(20), _read_call("r1", "src/hot.ts"), *_pad(20)]
    cold = [_message("src/cold.ts", "c" * 3000), *_pad(20), _read_call("r2", "src/other.ts"), *_pad(20)]
    folded_hot = fold_tool_payload_previews(hot)
    folded_cold = fold_tool_payload_previews(cold)
    assert json.loads(folded_hot[0]["content"])["file_card"]["pin_reason"] == "current_focus"
    assert json.loads(folded_hot[0]["content"])["file_card"]["fold_state"] == "ACTIVE"
    assert json.loads(folded_cold[0]["content"])["file_card"]["fold_state"] == "FOLDED"


def test_folded_card_keeps_a_bounded_preview(monkeypatch: pytest.MonkeyPatch) -> None:
    """A folded card still answers a glance, so only a real edit pays a re-read."""

    monkeypatch.setenv("KERNELONE_CONTEXTOS_INACTIVE_FOLD_ACTIVITIES", "24")
    monkeypatch.setenv("KERNELONE_CONTEXTOS_FOLD_KEPT_PREVIEW_BYTES", "64")
    folded = fold_tool_payload_previews([_message("src/a.ts", "0123456789" * 400), *_pad(25)])
    card = json.loads(folded[0]["content"])["file_card"]
    assert card["fold_state"] == "FOLDED"
    assert card["content_preview"] == "0123456789" * 6 + "0123"
    assert card["content_bounds"]["truncated"] is True


def test_duplicate_file_cards_keep_only_latest_preview(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KERNELONE_CONTEXTOS_INACTIVE_FOLD_ACTIVITIES", "3")
    folded = fold_tool_payload_previews(
        [
            {"role": "assistant", "content": "step-1"},
            _message("a.py", "o" * 1200),
            {"role": "assistant", "content": "step-2"},
            _message("a.py", "n" * 1200),
            {"role": "assistant", "content": "step-3"},
            _message("b.py", "keep"),
            {"role": "assistant", "content": "step-4"},
            _message("c.py", "keep"),
        ],
        semantic_summaries={"file:a.py": {"summary": "旧文件摘要"}},
    )
    tool_messages = [message for message in folded if message["role"] == "tool"]
    first, second, third, _fourth = (json.loads(message["content"])["file_card"] for message in tool_messages)
    assert first["content_preview"] == "o" * 512 and first["fold_state"] == "FOLDED"
    assert first["content_bounds"]["truncated"] is True
    assert second["content_preview"] == "n" * 1200 and second["fold_state"] == "ACTIVE"
    assert third["content_preview"] == "keep" and third["fold_state"] == "ACTIVE"


def test_current_edit_pin_keeps_preview_and_reason() -> None:
    messages = [
        _message("a.py", "source"),
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "function": {
                        "name": "edit_file",
                        "arguments": json.dumps({"file": "a.py", "search": "old", "replace": "new"}),
                    }
                }
            ],
        },
        _message("a.py", "new"),
    ]
    folded = fold_tool_payload_previews(messages)
    first = json.loads(folded[0]["content"])["file_card"]
    assert first["pin_reason"] == "current_edit"
    assert first["content_preview"] == "source" and first["fold_state"] == "ACTIVE"


def test_distiller_interprets_only_the_observed_snapshot_slice(monkeypatch: pytest.MonkeyPatch) -> None:
    from polaris.cells.roles.runtime.internal.engineering_semantic_advice import summary_input_key

    monkeypatch.setenv("KERNELONE_CONTEXTOS_INACTIVE_FOLD_ACTIVITIES", "1")
    preview = "p" * 1500 + "\n" + "p" * 1499
    message = _message("src/a.ts", preview)
    source = json.loads(message["content"])["file_card"]
    text = "解析输入后构造两个稀疏索引，供后续查询使用。"
    for snapshot, content, ranges, expected in (
        (source["content_ref"], preview, [[1, 2]], True),
        ("filecard:obsolete", preview, [[1, 2]], False),
        (source["content_ref"], preview, [[6, 7]], False),
        (source["content_ref"], "q" * 3000, [[1, 2]], False),
    ):
        key = summary_input_key("file:src/a.ts", snapshot, content, ranges)
        messages = fold_tool_payload_previews([message, *_pad(4)], semantic_summaries={key: {"summary": text}})
        result = json.loads(messages[0]["content"])["file_card"]
        assert (result["summary_kind"] == "model_inference") == expected
        assert (result["summary"] == text) == expected


@pytest.mark.parametrize("coverage_format", ["current", "absent", "partial", "unknown_offset"])
def test_distiller_and_fold_use_only_locatable_visible_source(
    coverage_format: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from polaris.cells.roles.runtime.internal.engineering_semantic_advice import summary_input_key
    from polaris.cells.roles.runtime.internal.engineering_semantic_runtime import facts_for_advice

    monkeypatch.setenv("KERNELONE_CONTEXTOS_INACTIVE_FOLD_ACTIVITIES", "1")
    lines = ["A" * 1600 + "\n", "B" * 1600 + "\n", "C" * 1600 + "\n"]
    source = "package\n" * 30 + "".join(lines)
    visible = "".join(lines[:2])
    preview = visible + "\n[output truncated: 1601 bytes omitted]"
    card = build_file_card(
        "a.py",
        source,
        revision=1,
        preview=preview,
        truncated=True,
        start_line=31,
        end_line=33,
        snapshot_ref="filecard:old",
    )
    if coverage_format != "current":
        card.pop("coverage")
    if coverage_format == "partial":
        card["coverage"] = {"presentation_truncated": True}
    if coverage_format == "unknown_offset":
        card.pop("content_bounds")
    wire = json.dumps({"file_card": card})
    events = [{"kind": "tool_observed", "seq": 1, "payload": {"tool": "read_file", "ok": True, "content": wire}}]
    events.extend({"kind": "turn_started", "seq": i + 2, "payload": {}} for i in range(30))
    request, _, _ = facts_for_advice({}, events)
    candidates = request["candidates"]
    if coverage_format == "unknown_offset":
        assert candidates == []
        return
    assert [(item["content"], item["coverage"]["observed_line_ranges"]) for item in candidates] == [
        (visible, [[31, 32]])
    ]
    key = summary_input_key("file:a.py", "filecard:old", visible, [[31, 32]])
    message = {"role": "tool", "content": wire}
    messages = fold_tool_payload_previews(
        [message, *_pad(4)], semantic_summaries={key: {"summary": "只读取了中间两行的输入。"}}
    )
    assert json.loads(messages[0]["content"])["file_card"]["summary_kind"] == "model_inference"


@pytest.mark.parametrize("kind", ["file_card", "write_call"])
def test_fold_keeps_comment_provenance_visible_in_both_planes(kind, monkeypatch):
    from polaris.cells.roles.runtime.internal.engineering_semantic_advice import (
        summary_input_key,
        validate_node_summary,
        write_snapshot_id,
    )
    from polaris.cells.roles.runtime.tests.test_engineering_semantic_advice import valid_summary
    from polaris.cells.roles.runtime.tests.test_engineering_tool_call_projection import _arguments, _call, _result

    monkeypatch.setenv("KERNELONE_CONTEXTOS_INACTIVE_FOLD_ACTIVITIES", "1")
    path = "src/view.ts"
    source = "/**\n * Only reads the state.\n */\nfunction draw() {}\n// " + "padding " * 600
    count = len(source.splitlines())
    node = ("file:" if kind == "file_card" else "write:") + path
    snapshot = "filecard:fixture" if kind == "file_card" else write_snapshot_id(source)
    request = {
        "node_kind": kind,
        "node_id": node,
        "snapshot_id": snapshot,
        "content": source,
        "coverage": {"observed_line_ranges": [[1, count]]},
    }
    raw = {
        **valid_summary(),
        "node_id": node,
        "snapshot_id": snapshot,
        "evidence_refs": [snapshot],
        "coverage": {**request["coverage"], "summary_complete": False},
        "summary": "Drawing has no side effects on simulation data.",
        "uncertain": False,
    }
    cache = {
        summary_input_key(node, snapshot, source, [[1, count]]): validate_node_summary(raw, request, selected_line=2)
    }
    if kind == "file_card":
        card = build_file_card(
            path,
            source,
            revision=1,
            preview=source,
            truncated=False,
            start_line=1,
            end_line=count,
            snapshot_ref=snapshot,
        )
        messages = [{"role": "tool", "content": json.dumps({"file_card": card})}]
    else:
        messages = [_call("t", "write_file", {"file": path, "content": source}), _result("t", "ok")]
    folded = fold_tool_payload_previews([*messages, *_pad(30)], semantic_summaries=cache)
    exposed = json.loads(folded[0]["content"])["file_card"] if kind == "file_card" else _arguments(folded[0])
    assert exposed["fold_state"] == "FOLDED" and exposed["summary_kind"] == "model_inference"
    assert exposed["summary"].startswith("Documented intent")
    assert "Only reads the state." in exposed["summary"]
