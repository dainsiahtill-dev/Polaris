"""Tests for task_contract_builder."""

from __future__ import annotations

from polaris.cells.roles.kernel.internal.transaction.task_contract_builder import (
    _extract_instruction_from_continuation_prompt,
    extract_continuation_prompt_metadata,
    extract_latest_user_message,
)


class TestExtractInstructionFromContinuationPrompt:
    def test_extracts_instruction_block(self) -> None:
        prompt = (
            "<Goal>\nFix the bug in main.py\n</Goal>\n"
            "<Progress>\nRead file successfully\n</Progress>\n"
            "<WorkingMemory>\nSome notes\n</WorkingMemory>\n"
            "<Instruction>\nPlease read utils.py next\n</Instruction>"
        )
        result = _extract_instruction_from_continuation_prompt(prompt)
        assert result == "Please read utils.py next"

    def test_returns_none_for_plain_message(self) -> None:
        assert _extract_instruction_from_continuation_prompt("Hello world") is None

    def test_returns_none_when_missing_goal(self) -> None:
        prompt = "<Instruction>Do something</Instruction>"
        assert _extract_instruction_from_continuation_prompt(prompt) is None

    def test_returns_none_when_missing_instruction(self) -> None:
        prompt = "<Goal>Fix bug</Goal>"
        assert _extract_instruction_from_continuation_prompt(prompt) is None

    def test_returns_none_for_empty_instruction(self) -> None:
        prompt = "<Goal>Fix bug</Goal><Instruction>   </Instruction>"
        assert _extract_instruction_from_continuation_prompt(prompt) is None


class TestExtractLatestUserMessage:
    def test_extracts_plain_user_message(self) -> None:
        context = [{"role": "user", "content": "Hello"}]
        assert extract_latest_user_message(context) == "Hello"

    def test_extracts_instruction_from_continuation(self) -> None:
        prompt = "<Goal>\nFix the bug in main.py\n</Goal>\n<Instruction>\nRead utils.py\n</Instruction>"
        context = [{"role": "user", "content": prompt}]
        assert extract_latest_user_message(context) == "Read utils.py"

    def test_skips_non_user_messages(self) -> None:
        context = [
            {"role": "assistant", "content": "Hi"},
            {"role": "user", "content": "Hello"},
        ]
        assert extract_latest_user_message(context) == "Hello"

    def test_returns_empty_for_no_user_messages(self) -> None:
        context = [{"role": "assistant", "content": "Hi"}]
        assert extract_latest_user_message(context) == ""

    def test_ignores_non_mapping_items(self) -> None:
        context = ["not a dict", {"role": "user", "content": "Hello"}]
        assert extract_latest_user_message(context) == "Hello"

    def test_empty_content_returns_empty(self) -> None:
        context = [{"role": "user", "content": "   "}]
        assert extract_latest_user_message(context) == ""

    def test_continuation_prompt_with_session_patch_preserves_instruction(self) -> None:
        prompt = (
            "<Goal>\nRefactor the transaction flow\n</Goal>\n"
            "<Progress>\n当前阶段: exploring | 回合: 1 / 6\n</Progress>\n"
            "<WorkingMemory>\n</WorkingMemory>\n"
            "<Instruction>\n请继续读取 session_orchestrator.py\n</Instruction>\n"
            "<SESSION_PATCH>\n"
            '{"delivery_mode": "materialize_changes", "recent_reads": ["read_file"]}'
            "\n</SESSION_PATCH>"
        )
        assert (
            extract_latest_user_message([{"role": "user", "content": prompt}]) == "请继续读取 session_orchestrator.py"
        )


class TestExtractContinuationPromptMetadata:
    def test_extracts_delivery_mode_from_session_patch(self) -> None:
        prompt = (
            "<Goal>\nFix the bug\n</Goal>\n"
            "<Progress>\n当前阶段: exploring | 回合: 1 / 6\n</Progress>\n"
            "<WorkingMemory>\n</WorkingMemory>\n"
            "<Instruction>\n继续执行\n</Instruction>\n"
            "<SESSION_PATCH>\n"
            '{"delivery_mode": "materialize_changes", "task_progress": "exploring", "recent_reads": ["read_file"]}'
            "\n</SESSION_PATCH>"
        )
        metadata = extract_continuation_prompt_metadata(prompt)
        assert metadata["delivery_mode"] == "materialize_changes"
        assert metadata["task_progress"] == "exploring"
        assert metadata["recent_reads"] == ["read_file"]

    def test_returns_empty_for_invalid_or_missing_patch(self) -> None:
        assert extract_continuation_prompt_metadata("Hello world") == {}
        prompt = "<SESSION_PATCH>{not-json}</SESSION_PATCH>"
        assert extract_continuation_prompt_metadata(prompt) == {}
