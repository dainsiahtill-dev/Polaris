"""Tests for Evidence Collector - 证据收集器测试。"""

from __future__ import annotations

import pytest
from polaris.cells.roles.kernel.internal.debug_strategy.evidence_collector import (
    EvidenceCollector,
)
from polaris.cells.roles.kernel.internal.debug_strategy.models import ErrorContext


class TestEvidenceCollector:
    """证据收集器测试。"""

    @pytest.fixture
    def collector(self) -> EvidenceCollector:
        """创建证据收集器实例。"""
        return EvidenceCollector()

    def test_collect_code_evidence(self, collector: EvidenceCollector) -> None:
        """测试收集代码证据。"""
        evidence = collector.collect_code_evidence(
            file_path="test.py",
            line_number=42,
            context_lines=5,
        )

        assert evidence.source == "code"
        assert "test.py" in evidence.content
        assert "42" in evidence.content
        assert evidence.confidence == 1.0
        assert evidence.metadata["file_path"] == "test.py"
        assert evidence.metadata["line_number"] == 42

    def test_collect_environment_evidence(self, collector: EvidenceCollector) -> None:
        """测试收集环境证据。"""
        env = {"PATH": "/usr/bin", "HOME": "/home/user"}
        evidence = collector.collect_environment_evidence(env)

        assert evidence.source == "environment"
        assert "PATH" in evidence.content
        assert "/usr/bin" in evidence.content
        assert evidence.confidence == 0.9
        assert "PATH" in evidence.metadata["env_keys"]

    def test_collect_stack_trace_evidence(self, collector: EvidenceCollector) -> None:
        """测试收集堆栈跟踪证据。"""
        stack = "File 'test.py', line 10, in test\n    raise Exception('test')\nException: test"
        evidence = collector.collect_stack_trace_evidence(stack)

        assert evidence.source == "stack_trace"
        assert evidence.content == stack
        assert evidence.confidence == 1.0
        assert evidence.metadata["trace_length"] == len(stack)

    def test_collect_change_history_evidence(self, collector: EvidenceCollector) -> None:
        """测试收集变更历史证据。"""
        changes = ["Commit 1: Fix bug", "Commit 2: Add feature"]
        evidence = collector.collect_change_history_evidence(changes)

        assert evidence.source == "change_history"
        assert "Commit 1" in evidence.content
        assert evidence.confidence == 0.85
        assert evidence.metadata["change_count"] == 2

    def test_collect_error_pattern_evidence(self, collector: EvidenceCollector) -> None:
        """测试收集错误模式证据。"""
        similar = [
            {"type": "KeyError", "message": "Key 'x' not found"},
            {"type": "KeyError", "message": "Key 'y' not found"},
        ]
        evidence = collector.collect_error_pattern_evidence(
            error_type="KeyError",
            error_message="Key 'z' not found",
            similar_errors=similar,
        )

        assert evidence.source == "error_pattern"
        assert "KeyError" in evidence.content
        assert evidence.confidence == 0.8  # 有相似错误
        assert evidence.metadata["similar_count"] == 2

    def test_collect_from_context_complete(self, collector: EvidenceCollector) -> None:
        """测试从完整上下文收集。"""
        context = ErrorContext(
            error_type="test_error",
            error_message="Test",
            stack_trace="Traceback...",
            environment={"KEY": "VALUE"},
            recent_changes=["Change 1"],
            file_path="test.py",
            line_number=10,
        )

        evidences = collector.collect_from_context(context)

        assert len(evidences) == 4  # stack, env, changes, code

        sources = [e.source for e in evidences]
        assert "stack_trace" in sources
        assert "environment" in sources
        assert "change_history" in sources
        assert "code" in sources

    def test_collect_from_context_partial(self, collector: EvidenceCollector) -> None:
        """测试从部分上下文收集。"""
        context = ErrorContext(
            error_type="test",
            error_message="test",
            stack_trace="trace",
            # 缺少其他字段
        )

        evidences = collector.collect_from_context(context)

        assert len(evidences) == 1  # 只有stack_trace
        assert evidences[0].source == "stack_trace"

    def test_get_all_evidence(self, collector: EvidenceCollector) -> None:
        """测试获取所有证据。"""
        collector.collect_code_evidence("test.py", 1)
        collector.collect_environment_evidence({"KEY": "VALUE"})

        all_evidence = collector.get_all_evidence()

        assert len(all_evidence) == 2

    def test_get_evidence_by_source(self, collector: EvidenceCollector) -> None:
        """测试按来源获取证据。"""
        collector.collect_code_evidence("test.py", 1)
        collector.collect_code_evidence("test2.py", 2)
        collector.collect_environment_evidence({"KEY": "VALUE"})

        code_evidence = collector.get_evidence_by_source("code")
        env_evidence = collector.get_evidence_by_source("environment")

        assert len(code_evidence) == 2
        assert len(env_evidence) == 1

    def test_clear_evidence(self, collector: EvidenceCollector) -> None:
        """测试清空证据。"""
        collector.collect_code_evidence("test.py", 1)
        assert len(collector.get_all_evidence()) == 1

        collector.clear()
        assert len(collector.get_all_evidence()) == 0

    def test_evidence_id_unique(self, collector: EvidenceCollector) -> None:
        """测试证据ID唯一性。"""
        evidence1 = collector.collect_code_evidence("test.py", 1)
        evidence2 = collector.collect_code_evidence("test.py", 1)

        assert evidence1.evidence_id != evidence2.evidence_id

    def test_evidence_timestamp(self, collector: EvidenceCollector) -> None:
        """测试证据时间戳。"""
        import time

        before = time.time()
        evidence = collector.collect_code_evidence("test.py", 1)
        after = time.time()

        assert before <= evidence.timestamp <= after

    def test_error_pattern_no_similar(self, collector: EvidenceCollector) -> None:
        """测试没有相似错误的错误模式。"""
        evidence = collector.collect_error_pattern_evidence(
            error_type="UniqueError",
            error_message="Unique message",
            similar_errors=[],
        )

        assert evidence.confidence == 0.6  # 较低置信度
        assert evidence.metadata["similar_count"] == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
