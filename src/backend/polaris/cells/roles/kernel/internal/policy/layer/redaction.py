"""RedactionPolicy - 脱敏策略。

# -*- coding: utf-8 -*-
UTF-8 编码验证: 本文所有文本使用 UTF-8

Blueprint: §11 RedactionPolicy

用于日志输出、trace 导出、prompt / tool result 中的敏感字段遮罩。
"""

from __future__ import annotations

import os
import re
from typing import Any

# 默认脱敏模式
_DEFAULT_PATTERNS: list[tuple[str, str]] = [
    # 环境变量
    (r"(api_key|secret|password|token|auth)\s*[=:]\s*['\"]?([\w\-]{8})['\"]?", r"\1=***REDACTED***"),
    # AWS 密钥
    (r"(aws_access_key|aws_secret)\s*[=:]\s*['\"]?([\w+/=]{16,})['\"]?", r"\1=***REDACTED***"),
    # GitHub Token
    (r"(gh[pousr]_[a-zA-Z0-9_]{36,})", r"***REDACTED***"),
    # Bearer Token
    (r"bearer\s+([a-zA-Z0-9_\-\.]{16,})", r"bearer ***REDACTED***"),
]


class RedactionPolicy:
    """脱敏策略。

    Blueprint: §11 RedactionPolicy

    Phase 3: 基础字段脱敏（环境变量、路径、token）。
    Phase 4: 可配置的正则脱敏规则。

    用于：
        - 日志输出脱敏
        - trace 导出脱敏
        - prompt / tool result 中的敏感字段遮罩
    """

    def __init__(
        self,
        *,
        redact_in_logs: bool = True,
        redact_in_trace: bool = True,
        redact_in_tool_args: bool = True,
        custom_patterns: list[tuple[str, str]] | None = None,
    ) -> None:
        self.redact_in_logs = redact_in_logs
        self.redact_in_trace = redact_in_trace
        self.redact_in_tool_args = redact_in_tool_args

        patterns = (custom_patterns or []) + _DEFAULT_PATTERNS
        self._compiled: list[tuple[re.Pattern[str], str]] = [
            (re.compile(pattern, re.IGNORECASE), replacement) for pattern, replacement in patterns
        ]

    @classmethod
    def from_env(cls) -> RedactionPolicy:
        """从环境变量构造。"""
        redact_logs = os.environ.get("KERNELONE_REDACT_LOGS", "true").lower() not in ("false", "0", "no")
        redact_trace = os.environ.get("KERNELONE_REDACT_TRACE", "true").lower() not in ("false", "0", "no")
        return cls(
            redact_in_logs=redact_logs,
            redact_in_trace=redact_trace,
            redact_in_tool_args=redact_logs,
        )

    def redact(self, text: str) -> str:
        """对文本进行脱敏。"""
        for pattern, replacement in self._compiled:
            text = pattern.sub(replacement, text)
        return text

    def redact_dict(self, data: dict[str, Any]) -> dict[str, Any]:
        """对字典进行脱敏（递归）。"""
        result: dict[str, Any] = {}
        for key, value in data.items():
            if isinstance(value, dict):
                result[key] = self.redact_dict(value)
            elif isinstance(value, str):
                result[key] = self.redact(value)
            else:
                result[key] = value
        return result

    def redact_tool_result(self, result: dict[str, Any]) -> dict[str, Any]:
        """对工具结果进行脱敏。"""
        if not self.redact_in_trace:
            return result
        return self.redact_dict(dict(result))


__all__ = [
    "_DEFAULT_PATTERNS",
    "RedactionPolicy",
]
