"""Call-plane folding: the model's own write_file/edit_file arguments.

Result cards were already folded; the call plane is where the document body
actually travels, so it is where an unfolded working set explodes. These tests
pin the policy: 24 activities, 1024 bytes, no pin, strictly smaller.
"""

from __future__ import annotations

import json

import pytest
from polaris.cells.roles.runtime.internal.engineering_tool_payload_projection import (
    fold_tool_payload_previews,
    folded_payload_projections,
)


def _call(call_id: str, name: str, arguments: dict[str, object], text: str = "") -> dict[str, object]:
    return {
        "role": "assistant",
        "content": text,
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)},
            }
        ],
    }


def _result(call_id: str, content: str) -> dict[str, object]:
    return {"role": "tool", "tool_call_id": call_id, "content": content}


def _pad(turns: int) -> list[dict[str, object]]:
    return [{"role": "assistant", "content": f"step-{index}"} for index in range(turns)]


def _write(call_id: str, path: str, size: int = 4000) -> dict[str, object]:
    return _call(call_id, "write_file", {"file": path, "content": "x" * size})


def _arguments(message: dict[str, object], index: int = 0) -> dict[str, object]:
    call = (message.get("tool_calls") or [])[index]
    return json.loads(call["function"]["arguments"])


def test_old_large_write_call_is_folded_and_stays_pairable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KERNELONE_CONTEXTOS_INACTIVE_FOLD_ACTIVITIES", "24")
    body = "x" * 4000
    messages = [
        _write("call_1", "src/a.ts"),
        _result("call_1", "Wrote 4000 UTF-8 bytes to src/a.ts."),
        *_pad(25),
    ]
    folded = fold_tool_payload_previews(messages)

    original_arguments = len(json.dumps({"file": "src/a.ts", "content": body}, ensure_ascii=False))
    projected = folded[0]["tool_calls"][0]
    arguments = json.loads(projected["function"]["arguments"])
    assert arguments["fold_state"] == "FOLDED"
    assert arguments["fold_reason"] == "outside_current_focus"
    assert arguments["file"] == "src/a.ts"
    assert arguments["summary_kind"] == "structural"
    assert "src/a.ts" in arguments["summary"] and "read_file" in arguments["recovery"]
    assert len(projected["function"]["arguments"]) < original_arguments
    # The native envelope and its result pairing survive intact.
    assert projected["id"] == "call_1"
    assert projected["function"]["name"] == "write_file"
    assert folded[1]["tool_call_id"] == "call_1"


def test_recent_write_call_is_never_folded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KERNELONE_CONTEXTOS_INACTIVE_FOLD_ACTIVITIES", "24")
    messages = [_write("call_1", "src/a.ts"), _result("call_1", "ok"), *_pad(3)]
    folded = fold_tool_payload_previews(messages)
    assert "fold_state" not in _arguments(folded[0])
    assert _arguments(folded[0])["content"] == "x" * 4000


def test_small_write_call_is_never_folded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KERNELONE_CONTEXTOS_INACTIVE_FOLD_ACTIVITIES", "1")
    messages = [_write("call_1", "src/a.ts", size=800), _result("call_1", "ok"), *_pad(3)]
    folded = fold_tool_payload_previews(messages)
    assert "fold_state" not in _arguments(folded[0])


def test_current_edit_target_is_pinned(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KERNELONE_CONTEXTOS_INACTIVE_FOLD_ACTIVITIES", "24")
    messages = [
        _write("call_1", "src/a.ts"),
        _result("call_1", "ok"),
        *_pad(25),
        _write("call_2", "src/a.ts"),
        _result("call_2", "ok"),
    ]
    folded = fold_tool_payload_previews(messages)
    assert _arguments(folded[0]).get("fold_state") != "FOLDED"


def test_superseded_write_call_reports_duplicate_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KERNELONE_CONTEXTOS_INACTIVE_FOLD_ACTIVITIES", "24")
    messages = [
        _write("call_1", "src/a.ts"),
        _result("call_1", "ok"),
        *_pad(20),
        _write("call_2", "src/a.ts"),
        _result("call_2", "ok"),
        *_pad(20),
    ]
    folded = fold_tool_payload_previews(messages)
    assert _arguments(folded[0])["fold_reason"] == "superseded_by_newer_payload"
    # The newer call is still inside the window and stays active.
    assert _arguments(folded[22]).get("fold_state") != "FOLDED"


def test_edit_call_is_folded_with_search_and_replace_sizes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KERNELONE_CONTEXTOS_INACTIVE_FOLD_ACTIVITIES", "24")
    messages = [
        _call("call_1", "edit_file", {"file": "src/b.ts", "search": "a" * 2000, "replace": "b" * 2000}),
        _result("call_1", "Replaced one exact occurrence in src/b.ts."),
        *_pad(25),
    ]
    folded = fold_tool_payload_previews(messages)
    arguments = _arguments(folded[0])
    assert arguments["fold_state"] == "FOLDED"
    assert arguments["file"] == "src/b.ts"
    assert "search 2000 字节" in arguments["summary"] and "replace 2000 字节" in arguments["summary"]


def test_long_shell_command_is_not_a_content_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KERNELONE_CONTEXTOS_INACTIVE_FOLD_ACTIVITIES", "1")
    script = "node -e " + "y" * 4000
    messages = [
        _call("call_1", "execute_command", {"command": script}),
        _result("call_1", "ok"),
        *_pad(3),
    ]
    folded = fold_tool_payload_previews(messages)
    assert _arguments(folded[0])["command"] == script


def test_distiller_summary_wins_over_the_structural_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KERNELONE_CONTEXTOS_INACTIVE_FOLD_ACTIVITIES", "24")
    messages = [_write("call_1", "src/a.ts"), _result("call_1", "ok"), *_pad(25)]
    folded = fold_tool_payload_previews(
        messages, semantic_summaries={"src/a.ts": {"summary": "该文件实现萤火虫的固定步长推进。"}}
    )
    arguments = _arguments(folded[0])
    assert arguments["summary"] == "该文件实现萤火虫的固定步长推进。"
    assert arguments["summary_kind"] == "model_inference"


def test_folded_call_is_reported_as_a_projection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KERNELONE_CONTEXTOS_INACTIVE_FOLD_ACTIVITIES", "24")
    messages = [_write("call_1", "src/a.ts"), _result("call_1", "ok"), *_pad(25)]
    folded = fold_tool_payload_previews(messages)
    projections = folded_payload_projections(folded)
    call = next(item for item in projections if item["plane"] == "call")
    assert call["node_kind"] == "write_file_call"
    assert call["projection_ref"] == "toolcall:call_1"
    assert call["fold_reason_code"] == "INACTIVE_PAYLOAD"


def test_working_set_budget_folds_oldest_first_until_the_set_fits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The budget tier is the pressure valve when the age window is not enough."""

    from polaris.kernelone.llm.engine.token_estimator import TokenEstimator

    monkeypatch.setenv("KERNELONE_CONTEXTOS_INACTIVE_FOLD_ACTIVITIES", "24")
    messages: list[dict[str, object]] = []
    for index in range(6):
        messages.append(_write(f"call_{index}", f"src/f{index}.ts"))
        messages.append(_result(f"call_{index}", "ok"))
    # Keep the writes out of the current-edit pin window so only budget applies.
    messages.extend(_pad(12))
    folded = fold_tool_payload_previews(messages, working_set_tokens=3000)
    states = ["fold_state" in _arguments(folded[index * 2]) for index in range(6)]
    # Oldest first, until the remaining set fits the budget.
    assert states == [True, True, True, True, False, False]
    assert [_arguments(folded[index * 2])["fold_reason"] for index in range(4)] == ["working_set_pressure"] * 4
    remaining = sum(TokenEstimator.estimate(json.dumps(message, ensure_ascii=False)) for message in folded)
    assert remaining <= 3000 + TokenEstimator.estimate("x" * 4000)


def test_working_set_budget_can_be_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KERNELONE_CONTEXTOS_INACTIVE_FOLD_ACTIVITIES", "24")
    messages: list[dict[str, object]] = []
    for index in range(6):
        messages.append(_write(f"call_{index}", f"src/f{index}.ts"))
        messages.append(_result(f"call_{index}", "ok"))
    folded = fold_tool_payload_previews(messages, working_set_tokens=0)
    assert all("fold_state" not in _arguments(folded[index * 2]) for index in range(6))


def test_age_tier_folds_even_with_an_unlimited_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KERNELONE_CONTEXTOS_INACTIVE_FOLD_ACTIVITIES", "24")
    messages = [_write("call_1", "src/a.ts"), _result("call_1", "ok"), *_pad(25)]
    folded = fold_tool_payload_previews(messages, working_set_tokens=0)
    assert _arguments(folded[0])["fold_reason"] == "outside_current_focus"


def test_folding_twice_is_stable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KERNELONE_CONTEXTOS_INACTIVE_FOLD_ACTIVITIES", "24")
    messages = [_write("call_1", "src/a.ts"), _result("call_1", "ok"), *_pad(25)]
    once = fold_tool_payload_previews(messages)
    twice = fold_tool_payload_previews(once)
    assert twice[0]["tool_calls"][0]["function"]["arguments"] == once[0]["tool_calls"][0]["function"]["arguments"]
