from __future__ import annotations

import json

import pytest
from polaris.cells.roles.runtime.internal.engineering_command_cards import build_command_card
from polaris.cells.roles.runtime.internal.engineering_file_card_projection import fold_file_card_previews


def _command_message(exit_code: int = 0) -> dict[str, object]:
    card = build_command_card("make test", "x" * 1200, "", exit_code, "command:abc")
    return {"role": "tool", "content": json.dumps({"command_card": card})}


def test_command_card_folds_only_after_inactive_window(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KERNELONE_CONTEXTOS_INACTIVE_FOLD_ACTIVITIES", "3")
    folded = fold_file_card_previews(
        [
            {"role": "assistant", "content": "step-1"},
            _command_message(),
            {"role": "assistant", "content": "step-2"},
            _command_message(),
            {"role": "assistant", "content": "step-3"},
            _command_message(),
            {"role": "assistant", "content": "step-4"},
            _command_message(),
        ],
        semantic_pins={"command:abc": "semantic_card"},
        semantic_summaries={"command:abc": {"summary": "旧命令摘要"}},
    )
    tool_messages = [message for message in folded if message["role"] == "tool"]
    first = json.loads(tool_messages[0]["content"])["command_card"]
    last = json.loads(tool_messages[-1]["content"])["command_card"]
    assert first["fold_state"] == "FOLDED" and first["stdout_preview"] == ""
    assert last["fold_state"] == "ACTIVE" and last["stdout_preview"] == "x" * 1200


def test_failed_command_preview_is_pinned() -> None:
    folded = fold_file_card_previews(
        [_command_message(exit_code=1), _command_message(), _command_message(), _command_message()]
    )
    first = json.loads(folded[0]["content"])["command_card"]
    assert first["fold_state"] == "ACTIVE" and first["pin_reason"] == "unresolved_failure"
    assert first["stdout_preview"] == "x" * 1200


def test_old_failed_command_folds_without_current_dependency(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KERNELONE_CONTEXTOS_INACTIVE_FOLD_ACTIVITIES", "3")
    messages: list[dict[str, object]] = []
    for index in range(11):
        messages.append({"role": "assistant", "content": f"step-{index}"})
        messages.append(_command_message(exit_code=1 if index == 0 else 0))
    folded = fold_file_card_previews(
        messages,
        semantic_summaries={
            "command:abc": {"summary": "该命令较早执行，输出已超出当前关注窗口，可按证据引用取回。"}
        },
    )
    first = next(message for message in folded if message["role"] == "tool")
    assert json.loads(first["content"])["command_card"]["fold_state"] == "FOLDED"


def test_fold_refused_when_projection_would_grow(monkeypatch: pytest.MonkeyPatch) -> None:
    """A summary larger than the preview must not fold: folding has to buy room."""

    from polaris.cells.roles.runtime.internal.engineering_file_cards import build_file_card

    monkeypatch.setenv("KERNELONE_CONTEXTOS_INACTIVE_FOLD_ACTIVITIES", "1")
    card = build_file_card(
        "a.py",
        "x" * 1200,
        revision=1,
        preview="x" * 1200,
        truncated=False,
        start_line=1,
        end_line=1,
        snapshot_ref="filecard:a",
    )
    card["summary"] = "S" * 4000  # card_default summary bigger than the preview
    messages: list[dict[str, object]] = []
    for index in range(4):
        messages.append({"role": "assistant", "content": f"step-{index}"})
        messages.append({"role": "tool", "content": json.dumps({"file_card": dict(card)})})
    folded = fold_file_card_previews(messages)
    cards = [json.loads(message["content"])["file_card"] for message in folded if message["role"] == "tool"]
    assert cards[0]["fold_state"] == "ACTIVE"
    assert cards[0]["content_preview"] == "x" * 1200


def test_fold_uses_structural_summary_when_the_distiller_is_silent(monkeypatch: pytest.MonkeyPatch) -> None:
    """A silent distiller must not make folding inert and force turn dropping."""

    monkeypatch.setenv("KERNELONE_CONTEXTOS_INACTIVE_FOLD_ACTIVITIES", "1")
    messages: list[dict[str, object]] = []
    for index in range(4):
        messages.append({"role": "assistant", "content": f"step-{index}"})
        messages.append(_command_message())
    folded = fold_file_card_previews(messages)
    card = json.loads(next(message for message in folded if message["role"] == "tool")["content"])["command_card"]
    assert card["fold_state"] == "FOLDED" and card["stdout_preview"] == ""
    assert "make test" in card["summary"] and card["summary_kind"] == "structural"


def test_uninformative_summaries_fall_back_to_structural(monkeypatch: pytest.MonkeyPatch) -> None:
    """A status word or a copied excerpt must never replace the payload as-is.

    Folding still happens, but with a deterministic structural summary that keeps
    the recovery pointer, so compression never depends on the distiller alone.
    """

    monkeypatch.setenv("KERNELONE_CONTEXTOS_INACTIVE_FOLD_ACTIVITIES", "1")
    messages: list[dict[str, object]] = []
    for index in range(4):
        messages.append({"role": "assistant", "content": f"step-{index}"})
        messages.append(_command_message())
    for uninformative in ("completed", "completed successfully", "xxxxxxxxxxxxxxxxxxxx"):
        folded = fold_file_card_previews(
            messages,
            semantic_summaries={"command:abc": {"summary": uninformative}},
        )
        cards = [json.loads(message["content"])["command_card"] for message in folded if message["role"] == "tool"]
        folded_card = cards[0]
        assert folded_card["fold_state"] == "FOLDED", (uninformative, folded_card)
        assert folded_card["stdout_preview"] == ""
        assert folded_card["summary_kind"] == "structural", (uninformative, folded_card)
        assert "make test" in folded_card["summary"]
        # The current turn is never folded away.
        assert cards[-1]["fold_state"] == "ACTIVE"


def test_real_distiller_summary_wins_over_structural(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KERNELONE_CONTEXTOS_INACTIVE_FOLD_ACTIVITIES", "1")
    messages: list[dict[str, object]] = []
    for index in range(4):
        messages.append({"role": "assistant", "content": f"step-{index}"})
        messages.append(_command_message())
    folded = fold_file_card_previews(
        messages,
        semantic_summaries={
            "command:abc": {"summary": "该命令执行了测试套件，末尾输出了汇总行，原始输出可按引用取回。"}
        },
    )
    card = json.loads(next(message for message in folded if message["role"] == "tool")["content"])["command_card"]
    assert card["fold_state"] == "FOLDED"
    assert card["summary_kind"] == "model_inference"
    assert card["summary"].startswith("该命令执行了测试套件")


def test_failed_command_current_reasoning_is_protected() -> None:
    messages: list[dict[str, object]] = []
    for index in range(11):
        messages.append({"role": "assistant", "content": f"step-{index}"})
        messages.append(_command_message(exit_code=1 if index == 0 else 0))
    messages.append({"role": "assistant", "content": "继续分析 command:abc 的失败原因"})
    folded = fold_file_card_previews(messages)
    first = next(message for message in folded if message["role"] == "tool")
    card = json.loads(first["content"])["command_card"]
    assert card["fold_state"] == "ACTIVE" and card["pin_reason"] == "current_reasoning"
