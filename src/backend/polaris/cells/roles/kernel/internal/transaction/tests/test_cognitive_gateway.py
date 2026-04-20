"""Tests for CognitiveGateway — 统一认知网关。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import pytest
from polaris.cells.roles.kernel.internal.transaction.cognitive_gateway import CognitiveGateway
from polaris.cells.roles.kernel.internal.transaction.delivery_contract import (
    DeliveryContract,
    DeliveryMode,
    EnrichmentContext,
    ExpectedAction,
    MutationScale,
    TaskCategory,
)
from polaris.cells.roles.kernel.internal.transaction.ledger import TransactionConfig


@dataclass
class FakeInvokeResult:
    ok: bool
    output: str
    error: str | None = None


class FakeSLMClient:
    """Deterministic fake SLM client for unit tests."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self._next_output: str = ""

    def set_next_output(self, text: str) -> None:
        self._next_output = text

    def invoke(self, prompt: str, model: str, config: dict[str, Any]) -> FakeInvokeResult:
        self.calls.append((prompt, model, config))
        return FakeInvokeResult(ok=True, output=self._next_output)


class FakeProviderManager:
    """Fake provider manager that returns a preset SLM client."""

    def __init__(self, client: FakeSLMClient | None = None) -> None:
        self._client = client
        self.get_calls: list[str] = []

    def get_provider_instance(self, provider_type: str) -> FakeSLMClient | None:
        self.get_calls.append(provider_type)
        return self._client


class FakeEmbeddingPort:
    """Fake embedding port that returns deterministic one-hot vectors."""

    def __init__(self) -> None:
        self._dim: int = 8
        self._cache: dict[str, list[float]] = {}

    def get_embedding(self, text: str) -> list[float]:
        if text not in self._cache:
            import hashlib

            h = hashlib.md5(text.encode(), usedforsecurity=False).hexdigest()
            idx = int(h, 16) % self._dim
            vec = [0.0] * self._dim
            vec[idx] = 1.0
            self._cache[text] = vec
        return self._cache[text]


@pytest.fixture(autouse=True)
def _reset_singleton() -> None:
    CognitiveGateway.reset_default()


class TestCognitiveGatewayHealth:
    async def test_slm_healthy_when_client_available(self) -> None:
        client = FakeSLMClient()
        client.set_next_output("ok")
        manager = FakeProviderManager(client)
        slm = _build_slm(manager)
        gateway = CognitiveGateway(slm_coprocessor=slm)

        assert await gateway.is_slm_healthy() is True
        assert await gateway.is_slm_healthy() is True  # cached

    async def test_slm_unhealthy_when_no_client(self) -> None:
        slm = _build_slm(FakeProviderManager(None))
        gateway = CognitiveGateway(slm_coprocessor=slm)

        assert await gateway.is_slm_healthy() is False

    async def test_slm_unhealthy_when_disabled(self) -> None:
        config = TransactionConfig(slm_enabled=False)
        slm = _build_slm(FakeProviderManager(None), config=config)
        gateway = CognitiveGateway(config=config, slm_coprocessor=slm)

        assert await gateway.is_slm_healthy() is False

    async def test_health_cache_invalidation(self) -> None:
        client = FakeSLMClient()
        client.set_next_output("ok")
        manager = FakeProviderManager(client)
        slm = _build_slm(manager)
        gateway = CognitiveGateway(slm_coprocessor=slm, health_ttl_seconds=0.0)

        assert await gateway.is_slm_healthy() is True
        # 模拟 SLM 失效
        manager._client = None
        gateway.invalidate_health_cache()
        assert await gateway.is_slm_healthy() is False

    async def test_slm_unhealthy_on_empty_string_response(self) -> None:
        """SLM 返回空字符串时必须判定为不健康（虚假阳性修复）."""
        client = FakeSLMClient()
        client.set_next_output("")
        manager = FakeProviderManager(client)
        slm = _build_slm(manager)
        gateway = CognitiveGateway(slm_coprocessor=slm, health_ttl_seconds=0.0)

        assert await gateway.is_slm_healthy() is False

    async def test_close_cancels_warmup_task(self) -> None:
        """close() 应取消正在运行的 warmup 任务."""
        client = FakeSLMClient()
        client.set_next_output("ok")
        manager = FakeProviderManager(client)
        slm = _build_slm(manager)
        gateway = CognitiveGateway(slm_coprocessor=slm)

        # 手动创建一个模拟 warmup 任务
        async def _dummy_warmup() -> None:
            await asyncio.sleep(10)

        gateway._warmup_task = asyncio.create_task(_dummy_warmup())
        await gateway.close()
        assert gateway._warmup_task is None or gateway._warmup_task.done()


class TestCognitiveGatewayClassifyIntent:
    async def test_classify_intent_returns_strong_mutation_regex(self) -> None:
        gateway = _gateway_with_no_slm()
        result = await gateway.classify_intent("请修改这个文件")
        assert result == "STRONG_MUTATION"

    async def test_classify_intent_returns_debug_and_fix_regex(self) -> None:
        gateway = _gateway_with_no_slm()
        result = await gateway.classify_intent("修复这个bug")
        assert result == "DEBUG_AND_FIX"

    async def test_classify_intent_returns_analysis_only_regex(self) -> None:
        gateway = _gateway_with_no_slm()
        result = await gateway.classify_intent("帮我分析一下代码")
        assert result == "ANALYSIS_ONLY"

    async def test_classify_intent_returns_unknown_for_empty(self) -> None:
        gateway = _gateway_with_no_slm()
        result = await gateway.classify_intent("hello world")
        assert result == "UNKNOWN"

    async def test_classify_intent_uses_slm_when_healthy(self) -> None:
        client = FakeSLMClient()
        client.set_next_output("PLANNING")
        manager = FakeProviderManager(client)
        slm = _build_slm(manager)
        gateway = CognitiveGateway(slm_coprocessor=slm)

        # SLM healthy, embedding not ready → should use SLM
        result = await gateway.classify_intent("帮我规划一下")
        assert result == "PLANNING"

    async def test_classify_intent_falls_back_to_regex_when_slm_returns_unknown(self) -> None:
        client = FakeSLMClient()
        client.set_next_output("UNKNOWN")
        manager = FakeProviderManager(client)
        slm = _build_slm(manager)
        gateway = CognitiveGateway(slm_coprocessor=slm)

        result = await gateway.classify_intent("请修改这个文件")
        # SLM returns UNKNOWN → regex fallback should detect STRONG_MUTATION
        assert result == "STRONG_MUTATION"

    async def test_classify_intent_falls_back_to_regex_when_slm_unhealthy(self) -> None:
        gateway = _gateway_with_no_slm()
        result = await gateway.classify_intent("请修改这个文件")
        assert result == "STRONG_MUTATION"


class TestCognitiveGatewayTasks:
    async def test_distill_logs_with_slm(self) -> None:
        client = FakeSLMClient()
        client.set_next_output("核心错误：缺少依赖")
        manager = FakeProviderManager(client)
        slm = _build_slm(manager)
        gateway = CognitiveGateway(slm_coprocessor=slm)

        result = await gateway.distill_logs("very long error log...")
        assert "缺少依赖" in result

    async def test_distill_logs_fallback_when_slm_unhealthy(self) -> None:
        gateway = _gateway_with_no_slm()
        long_log = "x" * 3000
        result = await gateway.distill_logs(long_log)
        assert len(result) == 2000

    async def test_heal_json_with_slm(self) -> None:
        client = FakeSLMClient()
        client.set_next_output('{"tool": "read_file", "path": "main.py"}')
        manager = FakeProviderManager(client)
        slm = _build_slm(manager)
        gateway = CognitiveGateway(slm_coprocessor=slm)

        result = await gateway.heal_json("{tool: read_file}")
        assert result == {"tool": "read_file", "path": "main.py"}

    async def test_heal_json_fallback_returns_none(self) -> None:
        gateway = _gateway_with_no_slm()
        result = await gateway.heal_json("broken json")
        assert result is None

    async def test_expand_query_with_slm(self) -> None:
        client = FakeSLMClient()
        client.set_next_output("- auth\n- login\n- jwt")
        manager = FakeProviderManager(client)
        slm = _build_slm(manager)
        gateway = CognitiveGateway(slm_coprocessor=slm)

        result = await gateway.expand_query("用户登录")
        assert result == ["auth", "login", "jwt"]

    async def test_expand_query_fallback_returns_original(self) -> None:
        gateway = _gateway_with_no_slm()
        result = await gateway.expand_query("user login")
        assert result == ["user login"]


class TestCognitiveGatewayExecuteTask:
    async def test_execute_task_classify(self) -> None:
        gateway = _gateway_with_no_slm()
        result = await gateway.execute_task("INTENT_CLASSIFY", "修改代码")
        assert result == "STRONG_MUTATION"

    async def test_execute_task_distill(self) -> None:
        gateway = _gateway_with_no_slm()
        result = await gateway.execute_task("LOG_DISTILL", "long log", max_tokens=100)
        assert isinstance(result, str)

    async def test_execute_task_heal(self) -> None:
        gateway = _gateway_with_no_slm()
        result = await gateway.execute_task("JSON_HEAL", "broken")
        assert result is None

    async def test_execute_task_expand(self) -> None:
        gateway = _gateway_with_no_slm()
        result = await gateway.execute_task("QUERY_EXPAND", "login")
        assert result == ["login"]

    async def test_execute_task_unknown_raises(self) -> None:
        gateway = _gateway_with_no_slm()
        with pytest.raises(ValueError, match="Unknown cognitive task"):
            await gateway.execute_task("UNKNOWN_TASK", "payload")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_slm(manager: FakeProviderManager, config: TransactionConfig | None = None) -> Any:
    from polaris.cells.roles.kernel.internal.transaction.slm_coprocessor import SLMCoprocessor

    cfg = config or TransactionConfig(slm_enabled=True)
    return SLMCoprocessor(config=cfg, provider_manager=manager)


def _gateway_with_no_slm() -> CognitiveGateway:
    config = TransactionConfig(slm_enabled=False, intent_embedding_enabled=False)
    return CognitiveGateway(config=config)


class TestParseRoutingJson:
    def test_parse_valid_json(self) -> None:
        gateway = _gateway_with_no_slm()
        raw = (
            '{"reasoning": "用户要求修改代码", "primary_mode": "MATERIALIZE_CHANGES", '
            '"task_category": "bug_fix", "expected_actions": ["read_files", "write_code"], '
            '"explicit_targets": ["main.py"], "mutation_scale": "minor", '
            '"requires_confirmation": false, "is_negated": false, "confidence": 0.92}'
        )
        result = gateway._parse_routing_json(raw)
        assert result is not None
        assert result.mode == DeliveryMode.MATERIALIZE_CHANGES
        assert result.enrichment is not None
        assert result.enrichment.task_category == TaskCategory.BUG_FIX
        assert result.enrichment.expected_actions == [ExpectedAction.READ_FILES, ExpectedAction.WRITE_CODE]
        assert result.enrichment.explicit_targets == ["main.py"]
        assert result.enrichment.mutation_scale == MutationScale.MINOR
        assert result.enrichment.confidence == pytest.approx(0.92)
        assert result.enrichment.is_negated is False
        assert result.enrichment.requires_confirmation is False
        assert result.enrichment.reasoning == "用户要求修改代码"

    def test_parse_json_with_markdown_codeblock(self) -> None:
        gateway = _gateway_with_no_slm()
        raw = (
            "```json\n"
            '{"reasoning": "分析代码", "primary_mode": "ANALYZE_ONLY", '
            '"task_category": "exploration", "expected_actions": ["read_files"], '
            '"mutation_scale": "none", "confidence": 0.88}\n'
            "```"
        )
        result = gateway._parse_routing_json(raw)
        assert result is not None
        assert result.mode == DeliveryMode.ANALYZE_ONLY
        assert result.enrichment is not None
        assert result.enrichment.task_category == TaskCategory.EXPLORATION

    def test_parse_invalid_json_returns_none(self) -> None:
        gateway = _gateway_with_no_slm()
        assert gateway._parse_routing_json("not json") is None
        assert gateway._parse_routing_json("") is None
        assert gateway._parse_routing_json("{invalid") is None

    def test_parse_unknown_mode_falls_back_to_analyze_only(self) -> None:
        gateway = _gateway_with_no_slm()
        raw = '{"primary_mode": "UNKNOWN_MODE", "task_category": "devops"}'
        result = gateway._parse_routing_json(raw)
        assert result is not None
        assert result.mode == DeliveryMode.ANALYZE_ONLY
        assert result.enrichment is not None
        assert result.enrichment.task_category == TaskCategory.DEVOPS

    def test_parse_missing_optional_fields_uses_defaults(self) -> None:
        gateway = _gateway_with_no_slm()
        raw = '{"primary_mode": "PROPOSE_PATCH"}'
        result = gateway._parse_routing_json(raw)
        assert result is not None
        assert result.mode == DeliveryMode.PROPOSE_PATCH
        assert result.enrichment is not None
        assert result.enrichment.task_category == TaskCategory.UNKNOWN
        assert result.enrichment.expected_actions == []
        assert result.enrichment.explicit_targets == []
        assert result.enrichment.mutation_scale == MutationScale.NONE
        assert result.enrichment.confidence == pytest.approx(0.0)


class TestResolveDeliveryModeSLMJSON:
    async def test_slm_json_path_used_when_healthy(self) -> None:
        client = FakeSLMClient()
        client.set_next_output(
            '{"reasoning": "重构代码", "primary_mode": "MATERIALIZE_CHANGES", '
            '"task_category": "refactor", "expected_actions": ["write_code"], '
            '"mutation_scale": "major", "confidence": 0.95}'
        )
        manager = FakeProviderManager(client)
        slm = _build_slm(manager)
        gateway = CognitiveGateway(slm_coprocessor=slm)

        result = await gateway.resolve_delivery_mode("重构整个模块")
        assert result.mode == DeliveryMode.MATERIALIZE_CHANGES
        assert result.enrichment is not None
        assert result.enrichment.task_category == TaskCategory.REFACTOR
        assert result.enrichment.mutation_scale == MutationScale.MAJOR

    async def test_slm_json_parse_failure_falls_back_to_regex(self) -> None:
        client = FakeSLMClient()
        client.set_next_output("not valid json {{[")
        manager = FakeProviderManager(client)
        slm = _build_slm(manager)
        gateway = CognitiveGateway(slm_coprocessor=slm)

        result = await gateway.resolve_delivery_mode("修改这个文件")
        assert result.mode == DeliveryMode.MATERIALIZE_CHANGES
        # regex fallback 会附加 enrichment
        assert result.enrichment is not None
        assert result.enrichment.raw_intent_label == "STRONG_MUTATION"

    async def test_slm_unhealthy_falls_back_to_regex_with_enrichment(self) -> None:
        gateway = _gateway_with_no_slm()
        result = await gateway.resolve_delivery_mode("帮我分析一下代码")
        assert result.mode == DeliveryMode.ANALYZE_ONLY
        assert result.enrichment is not None
        assert result.enrichment.task_category == TaskCategory.EXPLORATION
        assert result.enrichment.expected_actions == [ExpectedAction.READ_FILES, ExpectedAction.EXPLAIN]


class TestEnrichmentContextDefaults:
    def test_pydantic_defaults(self) -> None:
        ctx = EnrichmentContext()
        assert ctx.task_category == TaskCategory.UNKNOWN
        assert ctx.expected_actions == []
        assert ctx.explicit_targets == []
        assert ctx.mutation_scale == MutationScale.NONE
        assert ctx.requires_confirmation is False
        assert ctx.confidence == pytest.approx(0.0)
        assert ctx.is_negated is False
        assert ctx.raw_intent_label == ""
        assert ctx.reasoning == ""

    def test_pydantic_coercion(self) -> None:
        """Pydantic 应自动处理 SLM 输出的毛刺（大小写混用、多余空格）。"""
        ctx = EnrichmentContext(
            task_category="BUG_FIX",  # type: ignore[arg-type]
            mutation_scale="Minor",  # type: ignore[arg-type]
            expected_actions=["WRITE_CODE", " summarize "],  # type: ignore[list-item]
        )
        assert ctx.task_category == TaskCategory.BUG_FIX
        assert ctx.mutation_scale == MutationScale.MINOR
        assert ctx.expected_actions == [ExpectedAction.WRITE_CODE, ExpectedAction.SUMMARIZE]

    def test_pydantic_string_actions_becomes_single_item_list(self) -> None:
        """SLM 返回字符串而非列表时应自动包装为单元素列表。"""
        ctx = EnrichmentContext(expected_actions="write_code")  # type: ignore[arg-type]
        assert ctx.expected_actions == [ExpectedAction.WRITE_CODE]

    def test_pydantic_string_targets_becomes_single_item_list(self) -> None:
        """SLM 返回字符串而非列表时应自动包装为单元素列表。"""
        ctx = EnrichmentContext(explicit_targets="main.py")  # type: ignore[arg-type]
        assert ctx.explicit_targets == ["main.py"]

    def test_pydantic_invalid_enum_falls_back_to_default(self) -> None:
        """无效枚举值应回退到默认值，不应抛异常。"""
        ctx = EnrichmentContext(
            task_category="invalid_category",  # type: ignore[arg-type]
            mutation_scale="huge",  # type: ignore[arg-type]
            expected_actions=["invalid_action"],  # type: ignore[list-item]
        )
        assert ctx.task_category == TaskCategory.UNKNOWN
        assert ctx.mutation_scale == MutationScale.NONE
        assert ctx.expected_actions == []

    def test_parse_bool_string_false_is_false(self) -> None:
        """防御 bool('false') == True 的 Python 陷阱。"""
        gateway = _gateway_with_no_slm()
        assert gateway._parse_slm_bool("false") is False
        assert gateway._parse_slm_bool("False") is False
        assert gateway._parse_slm_bool("FALSE") is False
        assert gateway._parse_slm_bool("true") is True
        assert gateway._parse_slm_bool("True") is True
        assert gateway._parse_slm_bool("1") is True
        assert gateway._parse_slm_bool("0") is False
        assert gateway._parse_slm_bool(True) is True
        assert gateway._parse_slm_bool(False) is False

    def test_parse_routing_json_bool_string_false(self) -> None:
        """SLM 返回字符串 'false' 时 requires_confirmation 应为 False。"""
        gateway = _gateway_with_no_slm()
        raw = '{"primary_mode": "ANALYZE_ONLY", "requires_confirmation": "false", "is_negated": "False"}'
        result = gateway._parse_routing_json(raw)
        assert result is not None
        assert result.enrichment is not None
        assert result.enrichment.requires_confirmation is False
        assert result.enrichment.is_negated is False

    def test_parse_routing_json_confidence_clamping(self) -> None:
        """confidence 超出 [0,1] 范围时应被截断，不应导致解析失败。"""
        gateway = _gateway_with_no_slm()
        raw = '{"primary_mode": "ANALYZE_ONLY", "confidence": 1.5}'
        result = gateway._parse_routing_json(raw)
        assert result is not None
        assert result.enrichment is not None
        assert result.enrichment.confidence == pytest.approx(1.0)

        raw2 = '{"primary_mode": "ANALYZE_ONLY", "confidence": -0.3}'
        result2 = gateway._parse_routing_json(raw2)
        assert result2 is not None
        assert result2.enrichment is not None
        assert result2.enrichment.confidence == pytest.approx(0.0)

    def test_parse_routing_json_string_actions_and_targets(self) -> None:
        """SLM 返回字符串而非列表时，_parse_routing_json 应正确处理。"""
        gateway = _gateway_with_no_slm()
        raw = (
            '{"primary_mode": "MATERIALIZE_CHANGES", "expected_actions": "write_code", '
            '"explicit_targets": "src/main.py"}'
        )
        result = gateway._parse_routing_json(raw)
        assert result is not None
        assert result.enrichment is not None
        assert result.enrichment.expected_actions == [ExpectedAction.WRITE_CODE]
        assert result.enrichment.explicit_targets == ["src/main.py"]


class TestEnrichmentContextIsolation:
    def test_regex_fallback_instances_are_isolated(self) -> None:
        """regex fallback 的 EnrichmentContext 必须是独立副本，修改一个不应影响下一个。"""
        from polaris.cells.roles.kernel.internal.transaction.intent_classifier import (
            enrich_delivery_contract,
        )

        contract1 = DeliveryContract(mode=DeliveryMode.ANALYZE_ONLY)
        contract2 = DeliveryContract(mode=DeliveryMode.ANALYZE_ONLY)

        contract1 = enrich_delivery_contract("STRONG_MUTATION", contract1)
        contract2 = enrich_delivery_contract("STRONG_MUTATION", contract2)

        assert contract1.enrichment is not None
        assert contract2.enrichment is not None
        # 必须是不同对象
        assert contract1.enrichment is not contract2.enrichment

        # 修改 contract1 不应影响 contract2
        contract1.enrichment.expected_actions.append(ExpectedAction.SUMMARIZE)
        assert ExpectedAction.SUMMARIZE not in contract2.enrichment.expected_actions


class TestDualResolutionGuard:
    """Tests for _apply_dual_resolution_guard — SLM-Regex 双重决议容错."""

    def test_materialize_changes_unchanged(self) -> None:
        """已经是 MATERIALIZE_CHANGES 时，guard 不应修改任何字段。"""
        gateway = _gateway_with_no_slm()
        contract = DeliveryContract(
            mode=DeliveryMode.MATERIALIZE_CHANGES,
            requires_mutation=True,
            allow_inline_code=False,
            allow_patch_proposal=False,
        )
        result = gateway._apply_dual_resolution_guard(contract, "修改代码")
        assert result.mode == DeliveryMode.MATERIALIZE_CHANGES
        assert result.requires_mutation is True

    def test_propose_patch_with_write_code_action_upgraded(self) -> None:
        """SLM 返回 PROPOSE_PATCH 但 expected_actions 含 WRITE_CODE → 强制升级。"""
        gateway = _gateway_with_no_slm()
        contract = DeliveryContract(
            mode=DeliveryMode.PROPOSE_PATCH,
            requires_mutation=False,
            allow_inline_code=True,
            allow_patch_proposal=True,
            enrichment=EnrichmentContext(
                expected_actions=[ExpectedAction.READ_FILES, ExpectedAction.WRITE_CODE],
                explicit_targets=["runtime/server.py"],
            ),
        )
        result = gateway._apply_dual_resolution_guard(contract, "上传时添加 UUID 前缀")
        assert result.mode == DeliveryMode.MATERIALIZE_CHANGES
        assert result.requires_mutation is True
        assert result.allow_inline_code is False
        assert result.allow_patch_proposal is False
        assert result.enrichment is not None
        assert ExpectedAction.WRITE_CODE in result.enrichment.expected_actions
        assert "runtime/server.py" in result.enrichment.explicit_targets
        assert "SLM_OVERRIDDEN" in result.enrichment.raw_intent_label

    def test_propose_patch_with_write_tests_action_upgraded(self) -> None:
        """SLM 返回 PROPOSE_PATCH 但 expected_actions 含 WRITE_TESTS → 强制升级。"""
        gateway = _gateway_with_no_slm()
        contract = DeliveryContract(
            mode=DeliveryMode.PROPOSE_PATCH,
            enrichment=EnrichmentContext(
                expected_actions=[ExpectedAction.WRITE_TESTS],
            ),
        )
        result = gateway._apply_dual_resolution_guard(contract, "为上传模块写单元测试")
        assert result.mode == DeliveryMode.MATERIALIZE_CHANGES
        assert result.requires_mutation is True

    def test_analyze_only_with_regex_strong_mutation_upgraded(self) -> None:
        """SLM 返回 ANALYZE_ONLY 但 regex 检测到 STRONG_MUTATION → 强制升级。"""
        gateway = _gateway_with_no_slm()
        contract = DeliveryContract(
            mode=DeliveryMode.ANALYZE_ONLY,
            enrichment=EnrichmentContext(
                expected_actions=[ExpectedAction.READ_FILES],
            ),
        )
        result = gateway._apply_dual_resolution_guard(contract, "请修改这个文件")
        assert result.mode == DeliveryMode.MATERIALIZE_CHANGES
        assert result.requires_mutation is True

    def test_analyze_only_with_regex_weak_mutation_upgraded(self) -> None:
        """SLM 返回 ANALYZE_ONLY 但 regex 检测到 WEAK_MUTATION → 强制升级。

        这是用户日志中的真实场景复现：
        "开始进一步完善：上传时添加时间戳或 UUID 前缀"
        """
        gateway = _gateway_with_no_slm()
        contract = DeliveryContract(
            mode=DeliveryMode.ANALYZE_ONLY,
            enrichment=EnrichmentContext(
                expected_actions=[ExpectedAction.READ_FILES, ExpectedAction.SUMMARIZE],
            ),
        )
        result = gateway._apply_dual_resolution_guard(contract, "开始进一步完善：上传时添加时间戳或 UUID 前缀")
        assert result.mode == DeliveryMode.MATERIALIZE_CHANGES
        assert result.requires_mutation is True
        assert result.enrichment is not None
        assert ExpectedAction.WRITE_CODE in result.enrichment.expected_actions

    def test_analyze_only_no_mutation_signals_unchanged(self) -> None:
        """ANALYZE_ONLY + 无任何 mutation 信号 → 保持原样。"""
        gateway = _gateway_with_no_slm()
        contract = DeliveryContract(
            mode=DeliveryMode.ANALYZE_ONLY,
            enrichment=EnrichmentContext(
                expected_actions=[ExpectedAction.READ_FILES, ExpectedAction.EXPLAIN],
            ),
        )
        result = gateway._apply_dual_resolution_guard(contract, "帮我分析一下这段代码")
        assert result.mode == DeliveryMode.ANALYZE_ONLY
        assert result.requires_mutation is False

    def test_propose_patch_no_mutation_signals_unchanged(self) -> None:
        """PROPOSE_PATCH + 无 mutation 信号 → 保持原样。"""
        gateway = _gateway_with_no_slm()
        contract = DeliveryContract(
            mode=DeliveryMode.PROPOSE_PATCH,
            enrichment=EnrichmentContext(
                expected_actions=[ExpectedAction.PLAN, ExpectedAction.READ_FILES],
            ),
        )
        result = gateway._apply_dual_resolution_guard(contract, "帮我规划一下架构")
        assert result.mode == DeliveryMode.PROPOSE_PATCH
        assert result.requires_mutation is False

    def test_override_preserves_all_enrichment_fields(self) -> None:
        """强制升级时，SLM 提取的所有其他富字段必须原样保留。"""
        gateway = _gateway_with_no_slm()
        contract = DeliveryContract(
            mode=DeliveryMode.PROPOSE_PATCH,
            enrichment=EnrichmentContext(
                task_category=TaskCategory.REFACTOR,
                expected_actions=[ExpectedAction.WRITE_CODE],
                explicit_targets=["runtime/server.py", "upload_handler"],
                mutation_scale=MutationScale.MINOR,
                confidence=0.95,
                reasoning="修改上传逻辑以添加唯一前缀",
            ),
        )
        result = gateway._apply_dual_resolution_guard(contract, "上传时添加 UUID 前缀")
        assert result.enrichment is not None
        assert result.enrichment.task_category == TaskCategory.REFACTOR
        assert result.enrichment.explicit_targets == ["runtime/server.py", "upload_handler"]
        assert result.enrichment.mutation_scale == MutationScale.MINOR
        assert result.enrichment.confidence == pytest.approx(0.95)
        assert result.enrichment.reasoning == "修改上传逻辑以添加唯一前缀"
