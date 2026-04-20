"""将错误上下文注入 LLM 对话历史。"""

from __future__ import annotations

from typing import Any


class ErrorContextInjector:
    """错误上下文注入器。"""

    @staticmethod
    def inject_error_context(
        history: list[dict[str, Any]],
        tool_name: str,
        error_message: str,
        args: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """向对话历史注入错误上下文。"""
        error_entry = {
            "role": "system",
            "content": (
                f"[Tool Execution Failed]\n"
                f"Tool: {tool_name}\n"
                f"Error: {error_message}\n\n"
                f"Please decide next action based on the error above."
            ),
        }
        # 防御：确保 history 是列表
        if history is None:
            history = []
        return [*history, error_entry]

    @staticmethod
    def inject_recovery_hint(
        history: list[dict[str, Any]],
        hint: str,
    ) -> list[dict[str, Any]]:
        """注入恢复提示。"""
        hint_entry = {
            "role": "system",
            "content": f"[Recovery Hint]\n{hint}",
        }
        # 防御：确保 history 是列表
        if history is None:
            history = []
        return [*history, hint_entry]
