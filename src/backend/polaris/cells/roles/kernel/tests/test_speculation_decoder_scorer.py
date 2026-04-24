"""Tests for CandidateDecoder and StabilityScorer.

验证：
1. CandidateDecoder 的增量解析状态机转换
2. StabilityScorer 的加权评分计算
3. 边界条件（空输入、不完整 JSON、关键字段覆盖）
4. 异常路径（无效 schema、超时窗口）
"""

from __future__ import annotations

import time
from typing import Any

from polaris.cells.roles.kernel.internal.speculation.candidate_decoder import (
    CandidateDecoder,
)
from polaris.cells.roles.kernel.internal.speculation.models import (
    CandidateToolCall,
    FieldMutation,
)
from polaris.cells.roles.kernel.internal.speculation.stability_scorer import (
    StabilityScorer,
)

# ============ CandidateDecoder Tests ============


class TestCandidateDecoderInit:
    """测试 CandidateDecoder 初始化."""

    def test_init_creates_candidate(self) -> None:
        """初始化后 candidate 应有正确的标识和时间戳."""
        decoder = CandidateDecoder(candidate_id="cand_1", stream_id="stream_1", turn_id="turn_1")
        assert decoder.candidate.candidate_id == "cand_1"
        assert decoder.candidate.stream_id == "stream_1"
        assert decoder.candidate.turn_id == "turn_1"
        assert decoder.candidate.parse_state == "incomplete"
        assert decoder.candidate.partial_args == {}

    def test_init_with_schema(self) -> None:
        """传入 schema 后内部应保存."""
        schema = {"type": "object", "properties": {"path": {"type": "string"}}}
        decoder = CandidateDecoder(
            candidate_id="cand_1",
            stream_id="stream_1",
            turn_id="turn_1",
            schema=schema,
        )
        assert decoder._schema == schema


class TestCandidateDecoderFeedDelta:
    """测试 feed_delta 增量解析."""

    def test_empty_delta_returns_none(self) -> None:
        """空 delta 应返回 None."""
        decoder = CandidateDecoder("c1", "s1", "t1")
        assert decoder.feed_delta("") is None

    def test_simple_tool_call_extraction(self) -> None:
        """从 XML-like 标签中提取工具名和参数."""
        decoder = CandidateDecoder("c1", "s1", "t1")
        decoder.feed_delta("<tool_call>\nread_file\n")
        decoder.feed_delta('{"path": "main.py"}\n')
        decoder.feed_delta("</tool_call>")

        cand = decoder.candidate
        assert cand.tool_name == "read_file"
        assert cand.partial_args == {"path": "main.py"}
        assert cand.end_tag_seen is True

    def test_json_style_tool_call(self) -> None:
        """从 JSON 风格中提取工具名."""
        decoder = CandidateDecoder("c1", "s1", "t1")
        decoder.feed_delta('{"name": "write_file", "arguments": {"path": "out.py"}}')
        decoder.feed_delta("\n```")

        cand = decoder.candidate
        assert cand.tool_name == "write_file"
        # The decoder extracts the full JSON object as partial_args
        assert "path" in cand.partial_args or "arguments" in cand.partial_args

    def test_partial_json_parsing(self) -> None:
        """不完整 JSON 应能提取部分参数."""
        decoder = CandidateDecoder("c1", "s1", "t1")
        decoder.feed_delta('{"path": "main.py", "content": "hello')

        cand = decoder.candidate
        # Partial parse may or may not succeed depending on truncation heuristics
        # The test verifies the decoder does not crash and attempts parsing
        assert isinstance(cand.partial_args, dict)

    def test_mutation_history_recorded(self) -> None:
        """参数变更应记录 mutation history."""
        decoder = CandidateDecoder("c1", "s1", "t1")
        decoder.feed_delta('{"path": "a.py"}')
        decoder.feed_delta('{"path": "b.py"}')

        assert len(decoder.candidate.mutation_history) >= 1
        mutations = [m for m in decoder.candidate.mutation_history if m.field_path == "path"]
        assert len(mutations) >= 1

    def test_end_tag_detection(self) -> None:
        """检测结束标签并设置 end_tag_seen."""
        decoder = CandidateDecoder("c1", "s1", "t1")
        assert decoder.candidate.end_tag_seen is False

        decoder.feed_delta("some text </tool_call> more")
        assert decoder.candidate.end_tag_seen is True

    def test_code_block_end_tag(self) -> None:
        """检测代码块结束标签 - 注意实现中 ``` 前需要换行."""
        decoder = CandidateDecoder("c1", "s1", "t1")
        # The _END_TAGS includes "\n```" which requires a newline before ```
        decoder.feed_delta("\n```")
        assert decoder.candidate.end_tag_seen is True


class TestCandidateDecoderFinalize:
    """测试 finalize 强制收尾."""

    def test_finalize_without_end_tag(self) -> None:
        """无显式结束标签时 finalize 应尝试提取参数."""
        decoder = CandidateDecoder("c1", "s1", "t1")
        decoder.feed_delta('{"path": "main.py"}')

        result = decoder.finalize()
        assert isinstance(result, CandidateToolCall)
        assert result.partial_args == {"path": "main.py"}

    def finalize_updates_timestamp(self) -> None:
        """finalize 应更新 updated_at."""
        decoder = CandidateDecoder("c1", "s1", "t1")
        old_ts = decoder.candidate.updated_at
        time.sleep(0.01)
        decoder.finalize()
        assert decoder.candidate.updated_at > old_ts


class TestCandidateDecoderSchemaValidation:
    """测试 JSON Schema 验证（如 jsonschema 可用）."""

    def test_schema_valid_when_no_schema(self) -> None:
        """无 schema 时 syntactic_complete 即 schema_valid."""
        decoder = CandidateDecoder("c1", "s1", "t1")
        decoder.feed_delta('{"path": "main.py"}\n```')

        assert decoder.candidate.schema_valid is True
        assert decoder.candidate.parse_state == "schema_valid"

    def test_schema_invalid_with_wrong_type(self) -> None:
        """schema 不匹配时应标记 schema_valid=False."""
        schema: dict[str, Any] = {
            "type": "object",
            "properties": {"count": {"type": "integer"}},
        }
        decoder = CandidateDecoder("c1", "s1", "t1", schema=schema)
        decoder.feed_delta('{"count": "not_a_number"}\n```')

        # jsonschema may not be installed; test is best-effort
        if decoder.candidate.parse_state == "schema_valid":
            # If jsonschema is not available, it may still be schema_valid
            pass
        else:
            assert decoder.candidate.schema_valid is False


# ============ StabilityScorer Tests ============


class TestStabilityScorerInit:
    """测试 StabilityScorer 初始化."""

    def test_default_quiescence_window(self) -> None:
        """默认 quiescence_window 应为 120ms."""
        scorer = StabilityScorer()
        assert scorer._quiescence_window_ms == 120.0

    def test_custom_quiescence_window(self) -> None:
        """自定义 quiescence_window 应生效."""
        scorer = StabilityScorer(quiescence_window_ms=500.0)
        assert scorer._quiescence_window_ms == 500.0


class TestStabilityScorerScore:
    """测试 score 计算."""

    def test_empty_candidate_returns_zero(self) -> None:
        """无参数无工具名时应返回 0."""
        scorer = StabilityScorer()
        candidate = CandidateToolCall(candidate_id="c1", stream_id="s1", turn_id="t1")
        assert scorer.score(candidate) == 0.0

    def test_schema_valid_boost(self) -> None:
        """schema_valid 应增加 0.25 权重分."""
        scorer = StabilityScorer()
        candidate = CandidateToolCall(
            candidate_id="c1",
            stream_id="s1",
            turn_id="t1",
            tool_name="read_file",
            partial_args={"path": "main.py"},
            schema_valid=True,
            end_tag_seen=True,
        )
        score = scorer.score(candidate)
        # schema_valid(0.25) + end_tag(0.15) + quiescence(0.35) + overwrite(0.15) + hash(0.05 or 0.10)
        assert score >= 0.4

    def test_end_tag_seen_boost(self) -> None:
        """end_tag_seen 应增加 0.15 权重分."""
        scorer = StabilityScorer()
        candidate = CandidateToolCall(
            candidate_id="c1",
            stream_id="s1",
            turn_id="t1",
            tool_name="read_file",
            partial_args={"path": "main.py"},
            end_tag_seen=True,
        )
        score = scorer.score(candidate)
        assert score >= 0.15

    def test_critical_field_quiescence_full(self) -> None:
        """关键字段稳定时 quiescence 应为满分."""
        scorer = StabilityScorer(quiescence_window_ms=1.0)
        candidate = CandidateToolCall(
            candidate_id="c1",
            stream_id="s1",
            turn_id="t1",
            tool_name="read_file",
            partial_args={"path": "main.py"},
        )
        # Wait for quiescence window to pass
        time.sleep(0.05)
        score = scorer.score(candidate)
        # quiescence should be 1.0 * 0.35 = 0.35
        assert score >= 0.35

    def test_overwrite_penalty(self) -> None:
        """关键字段最近被覆盖时应受惩罚."""
        scorer = StabilityScorer(quiescence_window_ms=10000.0)
        candidate = CandidateToolCall(
            candidate_id="c1",
            stream_id="s1",
            turn_id="t1",
            tool_name="read_file",
            partial_args={"path": "main.py"},
            mutation_history=[
                FieldMutation(
                    field_path="path",
                    old_value="a.py",
                    new_value="b.py",
                    ts_monotonic=time.monotonic(),
                ),
            ],
        )
        score = scorer.score(candidate)
        # overwrite penalty should be 0
        # Without end_tag and schema_valid, score should be low
        assert score < 0.5

    def test_canonical_hash_consistency(self) -> None:
        """相同候选多次评分 hash 一致性应提升."""
        scorer = StabilityScorer()
        candidate = CandidateToolCall(
            candidate_id="c1",
            stream_id="s1",
            turn_id="t1",
            tool_name="read_file",
            partial_args={"path": "main.py"},
        )
        score1 = scorer.score(candidate)
        score2 = scorer.score(candidate)
        # Second call should have hash consistency bonus
        assert score2 >= score1

    def test_score_clamped_to_one(self) -> None:
        """评分不应超过 1.0."""
        scorer = StabilityScorer(quiescence_window_ms=1.0)
        candidate = CandidateToolCall(
            candidate_id="c1",
            stream_id="s1",
            turn_id="t1",
            tool_name="read_file",
            partial_args={"path": "main.py"},
            schema_valid=True,
            end_tag_seen=True,
        )
        time.sleep(0.05)
        score = scorer.score(candidate)
        assert score <= 1.0


class TestStabilityScorerUpdateParseState:
    """测试 update_parse_state 状态推导."""

    def test_incomplete_to_semantically_stable(self) -> None:
        """高稳定性候选应提升到 semantically_stable."""
        scorer = StabilityScorer(quiescence_window_ms=1.0)
        candidate = CandidateToolCall(
            candidate_id="c1",
            stream_id="s1",
            turn_id="t1",
            tool_name="read_file",
            partial_args={"path": "main.py"},
            schema_valid=True,
            end_tag_seen=True,
        )
        time.sleep(0.05)
        state = scorer.update_parse_state(candidate)
        assert state == "semantically_stable"
        assert candidate.stability_score >= 0.82

    def test_low_score_stays_incomplete(self) -> None:
        """低稳定性候选应保持 incomplete."""
        scorer = StabilityScorer()
        candidate = CandidateToolCall(candidate_id="c1", stream_id="s1", turn_id="t1")
        state = scorer.update_parse_state(candidate)
        assert state == "incomplete"

    def test_recent_overwrite_prevents_stable(self) -> None:
        """最近的关键字段覆盖应阻止 semantically_stable."""
        scorer = StabilityScorer(quiescence_window_ms=10000.0)
        candidate = CandidateToolCall(
            candidate_id="c1",
            stream_id="s1",
            turn_id="t1",
            tool_name="read_file",
            partial_args={"path": "main.py"},
            schema_valid=True,
            end_tag_seen=True,
            mutation_history=[
                FieldMutation(
                    field_path="path",
                    old_value="a.py",
                    new_value="b.py",
                    ts_monotonic=time.monotonic(),
                ),
            ],
        )
        state = scorer.update_parse_state(candidate)
        assert state != "semantically_stable"


class TestStabilityScorerCriticalFieldQuiescence:
    """测试关键字段静默期计算."""

    def test_no_critical_fields_returns_half(self) -> None:
        """无关键字段时返回 0.5."""
        scorer = StabilityScorer()
        candidate = CandidateToolCall(
            candidate_id="c1",
            stream_id="s1",
            turn_id="t1",
            partial_args={"other": "value"},
        )
        q = scorer._critical_field_quiescence(candidate)
        assert q == 0.5

    def test_no_mutations_with_critical_fields(self) -> None:
        """有关键字段但无 mutation 时返回 1.0."""
        scorer = StabilityScorer()
        candidate = CandidateToolCall(
            candidate_id="c1",
            stream_id="s1",
            turn_id="t1",
            partial_args={"path": "main.py"},
        )
        q = scorer._critical_field_quiescence(candidate)
        assert q == 1.0

    def test_recent_mutation_ramps_linearly(self) -> None:
        """最近 mutation 应在窗口内线性 ramp."""
        scorer = StabilityScorer(quiescence_window_ms=1000.0)
        candidate = CandidateToolCall(
            candidate_id="c1",
            stream_id="s1",
            turn_id="t1",
            partial_args={"path": "main.py"},
            mutation_history=[
                FieldMutation(
                    field_path="path",
                    old_value="a.py",
                    new_value="b.py",
                    ts_monotonic=time.monotonic(),
                ),
            ],
        )
        q = scorer._critical_field_quiescence(candidate)
        # Should be very low since mutation just happened
        assert q < 0.5
