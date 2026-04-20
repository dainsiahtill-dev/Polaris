"""Tests for Structured Findings in ContextHandoffPack (Phase 1 P0-6).

验证：
1. StructuredFindings 四要素字段
2. ContextHandoffPack 包含 structured_findings
3. from_mapping 正确解析
"""

from __future__ import annotations

import pytest
from polaris.domain.cognitive_runtime.models import ContextHandoffPack, StructuredFindings


class TestStructuredFindings:
    """StructuredFindings 模型验证。"""

    def test_four_fields(self) -> None:
        """四要素字段完整。"""
        findings = StructuredFindings(
            confirmed_facts=["fact1", "fact2"],
            rejected_hypotheses=["hyp1"],
            open_questions=["q1"],
            relevant_refs=["ref1"],
            source_turn_id="t1",
            extracted_at="2026-04-21T10:00:00Z",
        )
        assert findings.confirmed_facts == ["fact1", "fact2"]
        assert findings.rejected_hypotheses == ["hyp1"]
        assert findings.open_questions == ["q1"]
        assert findings.relevant_refs == ["ref1"]
        assert findings.source_turn_id == "t1"

    def test_empty_lists_allowed(self) -> None:
        """空列表允许。"""
        findings = StructuredFindings()
        assert findings.confirmed_facts == []
        assert findings.rejected_hypotheses == []
        assert findings.open_questions == []
        assert findings.relevant_refs == []


class TestContextHandoffPackWithFindings:
    """ContextHandoffPack 包含 structured_findings 验证。"""

    def test_handoff_pack_with_findings(self) -> None:
        """Handoff pack 包含 structured_findings。"""
        findings = StructuredFindings(
            confirmed_facts=["bug is in line 42"],
            open_questions=["how to reproduce"],
        )
        pack = ContextHandoffPack(
            handoff_id="h1",
            workspace=".",
            created_at="2026-04-21T10:00:00Z",
            session_id="s1",
            structured_findings=findings,
        )
        assert pack.structured_findings is not None
        assert pack.structured_findings.confirmed_facts == ["bug is in line 42"]

    def test_handoff_pack_without_findings(self) -> None:
        """Handoff pack 可以没有 structured_findings。"""
        pack = ContextHandoffPack(
            handoff_id="h1",
            workspace=".",
            created_at="2026-04-21T10:00:00Z",
            session_id="s1",
        )
        assert pack.structured_findings is None

    def test_from_mapping_with_findings(self) -> None:
        """from_mapping 正确解析 structured_findings。"""
        payload = {
            "handoff_id": "h1",
            "workspace": ".",
            "created_at": "2026-04-21T10:00:00Z",
            "session_id": "s1",
            "structured_findings": {
                "confirmed_facts": ["fact1"],
                "rejected_hypotheses": ["hyp1"],
                "open_questions": ["q1"],
                "relevant_refs": ["ref1"],
                "source_turn_id": "t1",
                "extracted_at": "2026-04-21T10:00:00Z",
            },
        }
        pack = ContextHandoffPack.from_mapping(payload)
        assert pack is not None
        assert pack.structured_findings is not None
        assert pack.structured_findings.confirmed_facts == ["fact1"]
        assert pack.structured_findings.rejected_hypotheses == ["hyp1"]

    def test_from_mapping_without_findings(self) -> None:
        """from_mapping 处理缺少 structured_findings 的情况。"""
        payload = {
            "handoff_id": "h1",
            "workspace": ".",
            "created_at": "2026-04-21T10:00:00Z",
            "session_id": "s1",
        }
        pack = ContextHandoffPack.from_mapping(payload)
        assert pack is not None
        assert pack.structured_findings is None
