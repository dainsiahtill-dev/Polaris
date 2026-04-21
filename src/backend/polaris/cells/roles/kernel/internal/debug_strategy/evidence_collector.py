"""Evidence Collector - 证据收集器。

系统化收集调试证据，支持四阶段调试流程。
"""

from __future__ import annotations

import hashlib
import time
from typing import Any

from polaris.cells.roles.kernel.internal.debug_strategy.models import (
    ErrorContext,
    Evidence,
)


class EvidenceCollector:
    """证据收集器：系统化收集调试证据。

    支持：
    - 代码证据收集
    - 环境证据收集
    - 历史证据收集
    - 运行时证据收集
    """

    def __init__(self) -> None:
        """初始化证据收集器。"""
        self._evidence_store: list[Evidence] = []

    def collect_code_evidence(
        self,
        file_path: str,
        line_number: int,
        context_lines: int = 5,
    ) -> Evidence:
        """收集代码证据。

        Args:
            file_path: 文件路径
            line_number: 行号
            context_lines: 上下文行数

        Returns:
            代码证据
        """
        content = f"File: {file_path}\nLine: {line_number}\nContext: ±{context_lines} lines"

        evidence = Evidence(
            evidence_id=self._generate_evidence_id("code", file_path, line_number),
            source="code",
            content=content,
            timestamp=time.time(),
            confidence=1.0,
            metadata={
                "file_path": file_path,
                "line_number": line_number,
                "context_lines": context_lines,
            },
        )
        self._evidence_store.append(evidence)
        return evidence

    def collect_environment_evidence(
        self,
        environment: dict[str, str],
    ) -> Evidence:
        """收集环境证据。

        Args:
            environment: 环境变量字典

        Returns:
            环境证据
        """
        content = "Environment:\n" + "\n".join(f"{k}={v}" for k, v in sorted(environment.items()))

        evidence = Evidence(
            evidence_id=self._generate_evidence_id("env", str(hash(tuple(environment.keys())))),
            source="environment",
            content=content,
            timestamp=time.time(),
            confidence=0.9,
            metadata={"env_keys": list(environment.keys())},
        )
        self._evidence_store.append(evidence)
        return evidence

    def collect_stack_trace_evidence(
        self,
        stack_trace: str,
    ) -> Evidence:
        """收集堆栈跟踪证据。

        Args:
            stack_trace: 堆栈跟踪字符串

        Returns:
            堆栈跟踪证据
        """
        evidence = Evidence(
            evidence_id=self._generate_evidence_id("stack", stack_trace[:50]),
            source="stack_trace",
            content=stack_trace,
            timestamp=time.time(),
            confidence=1.0,
            metadata={"trace_length": len(stack_trace)},
        )
        self._evidence_store.append(evidence)
        return evidence

    def collect_change_history_evidence(
        self,
        changes: list[str],
    ) -> Evidence:
        """收集变更历史证据。

        Args:
            changes: 变更列表

        Returns:
            变更历史证据
        """
        content = "Recent Changes:\n" + "\n".join(f"- {c}" for c in changes)

        evidence = Evidence(
            evidence_id=self._generate_evidence_id("changes", str(hash(tuple(changes)))),
            source="change_history",
            content=content,
            timestamp=time.time(),
            confidence=0.85,
            metadata={"change_count": len(changes)},
        )
        self._evidence_store.append(evidence)
        return evidence

    def collect_error_pattern_evidence(
        self,
        error_type: str,
        error_message: str,
        similar_errors: list[dict[str, Any]],
    ) -> Evidence:
        """收集错误模式证据。

        Args:
            error_type: 错误类型
            error_message: 错误消息
            similar_errors: 相似错误列表

        Returns:
            错误模式证据
        """
        content = f"Error Type: {error_type}\nMessage: {error_message}\n"
        if similar_errors:
            content += f"\nSimilar Errors ({len(similar_errors)}):\n"
            for err in similar_errors[:5]:  # 只显示前5个
                content += f"- {err.get('type', 'unknown')}: {err.get('message', 'no message')[:50]}\n"

        evidence = Evidence(
            evidence_id=self._generate_evidence_id("pattern", error_type, error_message[:30]),
            source="error_pattern",
            content=content,
            timestamp=time.time(),
            confidence=0.8 if similar_errors else 0.6,
            metadata={
                "error_type": error_type,
                "similar_count": len(similar_errors),
            },
        )
        self._evidence_store.append(evidence)
        return evidence

    def collect_from_context(self, context: ErrorContext) -> list[Evidence]:
        """从错误上下文收集所有证据。

        Args:
            context: 错误上下文

        Returns:
            证据列表
        """
        evidences = []

        # 收集堆栈跟踪
        if context.stack_trace:
            evidences.append(self.collect_stack_trace_evidence(context.stack_trace))

        # 收集环境证据
        if context.environment:
            evidences.append(self.collect_environment_evidence(context.environment))

        # 收集变更历史
        if context.recent_changes:
            evidences.append(self.collect_change_history_evidence(context.recent_changes))

        # 收集代码证据
        if context.file_path and context.line_number:
            evidences.append(self.collect_code_evidence(context.file_path, context.line_number))

        return evidences

    def get_all_evidence(self) -> list[Evidence]:
        """获取所有收集的证据。

        Returns:
            证据列表
        """
        return self._evidence_store.copy()

    def get_evidence_by_source(self, source: str) -> list[Evidence]:
        """按来源获取证据。

        Args:
            source: 证据来源

        Returns:
            证据列表
        """
        return [e for e in self._evidence_store if e.source == source]

    def clear(self) -> None:
        """清空证据存储。"""
        self._evidence_store.clear()

    def _generate_evidence_id(self, source: str, *parts: str | int) -> str:
        """生成证据唯一ID。"""
        content = f"{source}:{':'.join(str(p) for p in parts)}:{time.time()}"
        hash_part = hashlib.md5(content.encode()).hexdigest()[:8]
        return f"evid_{source}_{hash_part}"


__all__ = ["EvidenceCollector"]
