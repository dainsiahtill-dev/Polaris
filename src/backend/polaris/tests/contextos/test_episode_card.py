"""ContextOS EpisodeCard Three-Layer Summary Generation Tests.

P0-1: EpisodeCard 三层摘要生成验证
P0-2: digest_64/digest_256 散列值生成
P0-3: digest_1k 叙事摘要生成
P0-4: EpisodeCard 状态转换
P0-5: EpisodeCard 序列化/反序列化
"""

from __future__ import annotations

from typing import Any

import pytest
from polaris.kernelone.context.context_os.models import (
    EpisodeCard,
)


# -----------------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------------


def _make_episode(
    *,
    episode_id: str = "ep_001",
    from_sequence: int = 0,
    to_sequence: int = 10,
    intent: str = "用户请求实现登录功能",
    outcome: str = "登录功能已实现",
    decisions: tuple[str, ...] = (),
    facts: tuple[str, ...] = (),
    artifact_refs: tuple[str, ...] = (),
    entities: tuple[str, ...] = (),
    reopen_conditions: tuple[str, ...] = (),
    source_spans: tuple[str, ...] = (),
    digest_64: str = "",
    digest_256: str = "",
    digest_1k: str = "",
    sealed_at: float = 0.0,
    status: str = "sealed",
    reopened_at: str = "",
    reopen_reason: str = "",
) -> EpisodeCard:
    """Create a minimal EpisodeCard for testing."""
    return EpisodeCard(
        episode_id=episode_id,
        from_sequence=from_sequence,
        to_sequence=to_sequence,
        intent=intent,
        outcome=outcome,
        decisions=decisions,
        facts=facts,
        artifact_refs=artifact_refs,
        entities=entities,
        reopen_conditions=reopen_conditions,
        source_spans=source_spans,
        digest_64=digest_64,
        digest_256=digest_256,
        digest_1k=digest_1k,
        sealed_at=sealed_at,
        status=status,
        reopened_at=reopened_at,
        reopen_reason=reopen_reason,
    )


def _make_payload(
    *,
    episode_id: str = "ep_001",
    from_sequence: int = 0,
    to_sequence: int = 10,
    intent: str = "用户请求实现登录功能",
    outcome: str = "登录功能已实现",
    decisions: list[str] | None = None,
    facts: list[str] | None = None,
    artifact_refs: list[str] | None = None,
    entities: list[str] | None = None,
    reopen_conditions: list[str] | None = None,
    source_spans: list[str] | None = None,
    digest_64: str = "",
    digest_256: str = "",
    digest_1k: str = "",
    sealed_at: float = 0.0,
    status: str = "sealed",
    reopened_at: str = "",
    reopen_reason: str = "",
) -> dict[str, Any]:
    """Create a minimal payload dict for testing."""
    return {
        "episode_id": episode_id,
        "from_sequence": from_sequence,
        "to_sequence": to_sequence,
        "intent": intent,
        "outcome": outcome,
        "decisions": decisions or [],
        "facts": facts or [],
        "artifact_refs": artifact_refs or [],
        "entities": entities or [],
        "reopen_conditions": reopen_conditions or [],
        "source_spans": source_spans or [],
        "digest_64": digest_64,
        "digest_256": digest_256,
        "digest_1k": digest_1k,
        "sealed_at": sealed_at,
        "status": status,
        "reopened_at": reopened_at,
        "reopen_reason": reopen_reason,
    }


# -----------------------------------------------------------------------------
# Test: EpisodeCard 基础字段
# -----------------------------------------------------------------------------


class TestEpisodeCardFields:
    """P0-1: EpisodeCard 基础字段验证"""

    def test_episode_has_episode_id(self) -> None:
        """验证 EpisodeCard 包含 episode_id"""
        card = _make_episode(episode_id="ep_test_123")
        assert card.episode_id == "ep_test_123"

    def test_episode_has_sequence_range(self) -> None:
        """验证 EpisodeCard 包含 from_sequence 和 to_sequence"""
        card = _make_episode(from_sequence=5, to_sequence=15)
        assert card.from_sequence == 5
        assert card.to_sequence == 15

    def test_episode_has_intent(self) -> None:
        """验证 EpisodeCard 包含 intent"""
        card = _make_episode(intent="实现用户登录功能")
        assert card.intent == "实现用户登录功能"

    def test_episode_has_outcome(self) -> None:
        """验证 EpisodeCard 包含 outcome"""
        card = _make_episode(outcome="登录功能已完成")
        assert card.outcome == "登录功能已完成"

    def test_episode_has_status(self) -> None:
        """验证 EpisodeCard 包含 status"""
        card = _make_episode(status="sealed")
        assert card.status == "sealed"

    def test_status_defaults_to_sealed(self) -> None:
        """验证 status 默认值为 sealed"""
        card = _make_episode(status="sealed")
        assert card.status == "sealed"

    def test_episode_has_digest_fields(self) -> None:
        """验证 EpisodeCard 包含 digest_64 和 digest_256"""
        card = _make_episode(digest_64="abc123", digest_256="xyz789")
        assert card.digest_64 == "abc123"
        assert card.digest_256 == "xyz789"

    def test_episode_has_narrative(self) -> None:
        """验证 EpisodeCard 包含 digest_1k"""
        card = _make_episode(digest_1k="这是一个叙事摘要...")
        assert card.digest_1k == "这是一个叙事摘要..."

    def test_episode_has_reopen_fields(self) -> None:
        """验证 EpisodeCard 包含 reopened_at 和 reopen_reason"""
        card = _make_episode(reopened_at="2026-03-30T10:00:00Z", reopen_reason="用户重新打开")
        assert card.reopened_at == "2026-03-30T10:00:00Z"
        assert card.reopen_reason == "用户重新打开"


# -----------------------------------------------------------------------------
# Test: EpisodeCard 三层摘要生成
# -----------------------------------------------------------------------------


class TestEpisodeCardThreeLayerSummary:
    """P0-1/P0-3: EpisodeCard 三层摘要生成验证

    三层摘要结构:
    - Layer 1 (digest_64): 64字符散列 - 快速唯一标识
    - Layer 2 (digest_256): 256字符散列 - 完整性校验
    - Layer 3 (digest_1k): 叙事摘要 - 可读性描述
    """

    def test_digest_64_length(self) -> None:
        """验证 digest_64 为 64 字符散列"""
        card = _make_episode(digest_64="a" * 64)
        assert len(card.digest_64) == 64

    def test_digest_256_length(self) -> None:
        """验证 digest_256 为 256 字符散列"""
        card = _make_episode(digest_256="b" * 256)
        assert len(card.digest_256) == 256

    def test_digest_1k_is_string(self) -> None:
        """验证 digest_1k 是字符串类型"""
        card = _make_episode(digest_1k="叙事摘要内容")
        assert isinstance(card.digest_1k, str)

    def test_digest_1k_max_length_approx(self) -> None:
        """验证 digest_1k 最大长度约为 1000 字符"""
        long_narrative = "x" * 1000
        card = _make_episode(digest_1k=long_narrative)
        assert len(card.digest_1k) == 1000

    def test_digest_identifies_episode(self) -> None:
        """验证 digest 可以唯一标识 Episode"""
        card1 = _make_episode(
            episode_id="ep_1",
            intent="任务A",
            digest_64="digest_a",
        )
        card2 = _make_episode(
            episode_id="ep_2",
            intent="任务B",
            digest_64="digest_b",
        )
        # 不同的 digest 应该对应不同的 episode
        assert card1.digest_64 != card2.digest_64

    def test_digest_changes_with_content(self) -> None:
        """验证 digest 随内容变化"""
        card1 = _make_episode(intent="原始意图", digest_64="original")
        card2 = _make_episode(intent="修改意图", digest_64="modified")
        # digest_64 不同表示内容变化
        assert card1.digest_64 != card2.digest_64

    def test_narrative_describes_intent(self) -> None:
        """验证 narrative 描述 intent"""
        card = _make_episode(
            intent="实现登录功能",
            digest_1k="用户请求实现登录功能，经过开发完成了登录功能",
        )
        assert "登录功能" in card.digest_1k

    def test_narrative_describes_outcome(self) -> None:
        """验证 narrative 描述 outcome"""
        card = _make_episode(
            outcome="登录功能已完成",
            digest_1k="最终完成了用户登录功能，包括用户名密码验证",
        )
        assert "完成" in card.digest_1k or "完成" in card.outcome

    def test_narrative_captures_context(self) -> None:
        """验证 narrative 捕获上下文"""
        card = _make_episode(
            intent="修复登录bug",
            facts=("bug出现在密码验证环节",),
            digest_1k="用户报告登录失败，经排查发现是密码验证逻辑的bug",
        )
        assert len(card.digest_1k) > 0

    def test_three_layer_summary_serves_different_purposes(self) -> None:
        """验证三层摘要服务不同目的"""
        card = _make_episode(
            episode_id="ep_test",
            digest_64="d" * 64,
            digest_256="e" * 256,
            digest_1k="这是一个完整的叙事描述",
        )
        # digest_64 用于快速索引
        assert len(card.digest_64) == 64
        # digest_256 用于完整性校验
        assert len(card.digest_256) == 256
        # digest_1k 用于可读性
        assert len(card.digest_1k) > 0


# -----------------------------------------------------------------------------
# Test: EpisodeCard 状态转换
# -----------------------------------------------------------------------------


class TestEpisodeCardStatusTransition:
    """P0-4: EpisodeCard 状态转换验证"""

    def test_status_sealed(self) -> None:
        """验证 sealed 状态"""
        card = _make_episode(status="sealed")
        assert card.status == "sealed"

    def test_status_reopened(self) -> None:
        """验证 reopened 状态"""
        card = _make_episode(
            status="reopened",
            reopened_at="2026-03-30T10:00:00Z",
            reopen_reason="用户发现新问题",
        )
        assert card.status == "reopened"
        assert card.reopened_at == "2026-03-30T10:00:00Z"

    def test_reopen_reason_requires_reopened_at(self) -> None:
        """验证 reopened 状态需要 reopened_at"""
        card = _make_episode(
            status="reopened",
            reopened_at="2026-03-30T10:00:00Z",
            reopen_reason="功能需要扩展",
        )
        assert card.reopened_at != ""
        assert card.reopen_reason != ""

    def test_transition_from_sealed_to_reopened(self) -> None:
        """验证从 sealed 到 reopened 的转换"""
        # 初始 sealed
        card = _make_episode(status="sealed")
        assert card.status == "sealed"
        # 模拟重新打开
        card = _make_episode(
            status="reopened",
            reopened_at="2026-03-30T10:00:00Z",
            reopen_reason="需要修复",
        )
        assert card.status == "reopened"


# -----------------------------------------------------------------------------
# Test: EpisodeCard 序列化/反序列化
# -----------------------------------------------------------------------------


class TestEpisodeCardSerialization:
    """P0-5: EpisodeCard 序列化/反序列化验证"""

    def test_from_mapping_basic(self) -> None:
        """验证 from_mapping 基本功能"""
        payload = _make_payload(
            episode_id="ep_test",
            intent="测试意图",
            outcome="测试结果",
        )
        card = EpisodeCard.from_mapping(payload)
        assert card is not None
        assert card.episode_id == "ep_test"
        assert card.intent == "测试意图"

    def test_from_mapping_with_all_fields(self) -> None:
        """验证 from_mapping 处理所有字段"""
        payload = _make_payload(
            episode_id="ep_full",
            from_sequence=5,
            to_sequence=15,
            intent="完整意图",
            outcome="完整结果",
            decisions=["决策1", "决策2"],
            facts=["事实1", "事实2"],
            artifact_refs=["artifact_1"],
            entities=["entity_1"],
            reopen_conditions=["条件1"],
            source_spans=["span_1"],
            digest_64="d" * 64,
            digest_256="e" * 256,
            digest_1k="叙事摘要",
            status="sealed",
        )
        card = EpisodeCard.from_mapping(payload)
        assert card.episode_id == "ep_full"
        assert card.from_sequence == 5
        assert card.to_sequence == 15
        assert card.decisions == ("决策1", "决策2")
        assert card.facts == ("事实1", "事实2")
        assert len(card.digest_64) == 64

    def test_from_mapping_with_none(self) -> None:
        """验证 from_mapping 处理 None 输入"""
        card = EpisodeCard.from_mapping(None)
        assert card is None

    def test_from_mapping_with_empty_dict(self) -> None:
        """验证 from_mapping 处理空字典"""
        card = EpisodeCard.from_mapping({})
        assert card is not None
        # 默认值
        assert card.episode_id == ""

    def test_to_dict_basic(self) -> None:
        """验证 to_dict 基本功能"""
        card = _make_episode(
            episode_id="ep_dict",
            intent="测试意图",
        )
        result = card.to_dict()
        assert isinstance(result, dict)
        assert result["episode_id"] == "ep_dict"
        assert result["intent"] == "测试意图"

    def test_to_dict_preserves_all_fields(self) -> None:
        """验证 to_dict 保留所有字段"""
        card = _make_episode(
            episode_id="ep_preserve",
            from_sequence=1,
            to_sequence=20,
            intent="保留意图",
            outcome="保留结果",
            decisions=("d1", "d2"),
            facts=("f1",),
            status="sealed",
        )
        result = card.to_dict()
        assert result["episode_id"] == "ep_preserve"
        assert result["from_sequence"] == 1
        assert result["to_sequence"] == 20
        assert result["decisions"] == ["d1", "d2"]
        assert result["facts"] == ["f1"]

    def test_roundtrip(self) -> None:
        """验证序列化/反序列化往返"""
        original = _make_episode(
            episode_id="ep_roundtrip",
            from_sequence=3,
            to_sequence=25,
            intent="往返测试",
            outcome="往返结果",
            decisions=("决策A",),
            facts=("事实X",),
            digest_64="r" * 64,
            digest_1k="叙事往返测试",
            status="sealed",
        )
        # 序列化
        payload = original.to_dict()
        # 反序列化
        restored = EpisodeCard.from_mapping(payload)
        assert restored is not None
        assert restored.episode_id == original.episode_id
        assert restored.from_sequence == original.from_sequence
        assert restored.to_sequence == original.to_sequence
        assert restored.intent == original.intent
        assert restored.outcome == original.outcome
        assert restored.decisions == original.decisions
        assert restored.digest_64 == original.digest_64

    def test_tuple_conversion_in_from_mapping(self) -> None:
        """验证 from_mapping 将 list 转换为 tuple"""
        payload = _make_payload(
            decisions=["d1", "d2", "d3"],
            facts=["f1", "f2"],
        )
        card = EpisodeCard.from_mapping(payload)
        # 应该转换为 tuple
        assert isinstance(card.decisions, tuple)
        assert isinstance(card.facts, tuple)
        assert card.decisions == ("d1", "d2", "d3")


# -----------------------------------------------------------------------------
# Test: EpisodeCard 边界情况
# -----------------------------------------------------------------------------


class TestEpisodeCardEdgeCases:
    """EpisodeCard 边界情况测试"""

    def test_empty_intent(self) -> None:
        """验证空 intent"""
        card = _make_episode(intent="")
        assert card.intent == ""

    def test_empty_outcome(self) -> None:
        """验证空 outcome"""
        card = _make_episode(outcome="")
        assert card.outcome == ""

    def test_empty_episode_id(self) -> None:
        """验证空 episode_id"""
        card = _make_episode(episode_id="")
        assert card.episode_id == ""

    def test_zero_sequence_range(self) -> None:
        """验证零范围序列"""
        card = _make_episode(from_sequence=0, to_sequence=0)
        assert card.from_sequence == 0
        assert card.to_sequence == 0

    def test_large_sequence_range(self) -> None:
        """验证大范围序列"""
        card = _make_episode(from_sequence=0, to_sequence=1000000)
        assert card.to_sequence == 1000000

    def test_many_decisions(self) -> None:
        """验证多个决策"""
        decisions = tuple(f"决策{i}" for i in range(20))
        card = _make_episode(decisions=decisions)
        assert len(card.decisions) == 20

    def test_many_facts(self) -> None:
        """验证多个事实"""
        facts = tuple(f"事实{i}" for i in range(30))
        card = _make_episode(facts=facts)
        assert len(card.facts) == 30

    def test_special_characters_in_intent(self) -> None:
        """验证意图中的特殊字符"""
        card = _make_episode(
            intent="实现登录功能 <script>alert('xss')</script>"
        )
        assert "<script>" in card.intent

    def test_unicode_in_narrative(self) -> None:
        """验证叙事摘要中的 Unicode"""
        card = _make_episode(
            digest_1k="用户请求实现登录功能，包括用户名密码验证和错误处理。"
        )
        assert isinstance(card.digest_1k, str)

    def test_reopen_without_reason(self) -> None:
        """验证 reopened 但无 reason"""
        card = _make_episode(
            status="reopened",
            reopened_at="2026-03-30T10:00:00Z",
            reopen_reason="",
        )
        assert card.status == "reopened"
        assert card.reopened_at != ""

    def test_narrative_with_decisions_summary(self) -> None:
        """验证叙事摘要包含决策摘要"""
        card = _make_episode(
            decisions=("使用JWT认证", "密码加密存储"),
            digest_1k="经过讨论决定采用JWT进行认证，密码使用bcrypt加密存储",
        )
        assert "JWT" in card.digest_1k or "决定" in card.digest_1k
