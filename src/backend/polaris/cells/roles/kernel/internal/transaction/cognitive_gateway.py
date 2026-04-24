"""CognitiveGateway — 统一认知网关。

负责 SLM 健康监控、级联降级任务调度、以及认知层统一入口。

Architecture:
- 健康检查: 30-60s TTL 缓存，避免频繁探测 SLM
- 级联瀑布 (Waterfall):
  Level 1: IntentEmbeddingRouter (零延迟 cosine 相似度)
  Level 2: SLMCoprocessor (本地/局域网小模型)
  Level 3: Hard-coded Regex (100% 可用终极兜底)
- 任务模板: INTENT_CLASSIFY | LOG_DISTILL | JSON_HEAL | QUERY_EXPAND

所有 I/O 均为 async；sync 调用通过 asyncio.to_thread offload。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
import time
from dataclasses import replace
from typing import Any, Literal

from polaris.cells.roles.kernel.internal.transaction.delivery_contract import (
    DeliveryContract,
    DeliveryMode,
    EnrichmentContext,
    ExpectedAction,
)
from polaris.cells.roles.kernel.internal.transaction.intent_classifier import (
    _is_negated_mutation,
    classify_intent_regex,
    enrich_delivery_contract,
)
from polaris.cells.roles.kernel.internal.transaction.intent_embedding_router import (
    IntentEmbeddingRouter,
)
from polaris.cells.roles.kernel.internal.transaction.ledger import TransactionConfig
from polaris.cells.roles.kernel.internal.transaction.slm_coprocessor import SLMCoprocessor

logger = logging.getLogger(__name__)

COGNITIVE_TASK = Literal["INTENT_CLASSIFY", "LOG_DISTILL", "JSON_HEAL", "QUERY_EXPAND"]


class CognitiveGateway:
    """统一认知网关。

    Usage:
        gateway = await CognitiveGateway.default()
        intent = await gateway.classify_intent("修改代码")
        distilled = await gateway.distill_logs("长日志...")
    """

    _default_instance: CognitiveGateway | None = None
    _instance_lock: asyncio.Lock | None = None
    _thread_lock: threading.Lock = threading.Lock()

    @classmethod
    def _get_instance_lock(cls) -> asyncio.Lock:
        """线程安全地获取/创建 asyncio.Lock（双重检查锁定）。"""
        if cls._instance_lock is None:
            with cls._thread_lock:
                # 双重检查：防止多线程竞态创建多个 Lock
                if cls._instance_lock is None:
                    cls._instance_lock = asyncio.Lock()
        return cls._instance_lock

    # 默认健康检查 TTL（秒）
    _DEFAULT_HEALTH_TTL_SECONDS: float = 30.0

    def __init__(
        self,
        *,
        config: TransactionConfig | None = None,
        embedding_router: IntentEmbeddingRouter | None = None,
        slm_coprocessor: SLMCoprocessor | None = None,
        health_ttl_seconds: float | None = None,
    ) -> None:
        self.config = config or TransactionConfig()
        self._embedding_router = embedding_router
        self._slm_coprocessor = slm_coprocessor
        self._health_ttl_seconds = health_ttl_seconds or self._DEFAULT_HEALTH_TTL_SECONDS

        # 健康状态缓存
        self._slm_healthy: bool | None = None
        self._last_health_check_at: float = 0.0
        self._health_lock = asyncio.Lock()

        # 后台预热任务 (fire-and-forget)
        self._warmup_task: asyncio.Task[Any] | None = None

    @classmethod
    async def default(cls) -> CognitiveGateway:
        """获取全局单例（懒加载，带锁）。"""
        if cls._default_instance is None:
            async with cls._get_instance_lock():
                if cls._default_instance is None:
                    cls._default_instance = await cls._create_default()
        return cls._default_instance

    @classmethod
    async def _create_default(cls) -> CognitiveGateway:
        """构建默认实例，自动装配 EmbeddingRouter 和 SLMCoprocessor。"""
        config = TransactionConfig()
        embedding_router = IntentEmbeddingRouter.default()
        slm_coprocessor = await SLMCoprocessor.default()
        gateway = cls(
            config=config,
            embedding_router=embedding_router,
            slm_coprocessor=slm_coprocessor,
        )
        gateway._start_silent_warmup()
        return gateway

    def _start_silent_warmup(self) -> None:
        """Fire-and-forget 后台预热 SLM，不阻塞主流程。"""
        if not self.config.slm_enabled:
            return
        try:
            self._warmup_task = asyncio.create_task(self._silent_warmup())
        except RuntimeError:
            # 无运行事件循环时静默跳过
            logger.debug("No running event loop, skipping SLM warmup")

    async def _silent_warmup(self) -> None:
        """静默唤醒 SLM，利用首调用触发模型加载到 VRAM。

        局域网/本地 Ollama 首次加载大模型到显存可能需要 10-20 秒。
        后台预热让真实流量到达时模型已驻留，实现零延迟响应。
        """
        logger.info("Initiating background SLM warmup (model=%s)...", self.config.slm_model_name)
        logger.debug(
            "[SLM warmup] _silent_warmup 启动, slm_enabled=%s, coprocessor=%s",
            self.config.slm_enabled,
            self._slm_coprocessor is not None,
        )
        try:
            if self._slm_coprocessor is None:
                logger.debug("[SLM warmup] 跳过: SLM coprocessor 未初始化")
                return
            if not self.config.slm_enabled:
                logger.debug("[SLM warmup] 跳过: slm_enabled=False")
                return
            # 发送最轻量的请求，只为触发 VRAM 加载
            logger.debug("[SLM warmup] 发送 warmup 请求到 SLM (model=%s, max_tokens=1)...", self.config.slm_model_name)
            result = await self._slm_coprocessor._invoke_slm("warmup", max_tokens=1)
            if result and result.strip():
                logger.debug("[SLM warmup] SLM 返回结果 (len=%d): %s", len(result), result[:200])
                logger.info("SLM warmup complete. Model is now memory-resident.")
            else:
                logger.debug("[SLM warmup] SLM 返回空结果 (模型可能离线或配置错误)")
                logger.info("SLM warmup skipped: model returned empty response (may be offline).")
        except (ConnectionError, TimeoutError, RuntimeError) as exc:
            logger.debug("[SLM warmup] SLM warmup 失败 (model may be offline): %s", exc, exc_info=True)
            logger.info("SLM warmup failed: %s", exc)

    @classmethod
    def reset_default(cls) -> None:
        """重置单例 — 主要用于测试隔离。"""
        cls._default_instance = None

    @classmethod
    def has_default_instance(cls) -> bool:
        """检查全局单例是否已初始化（sync 安全）。"""
        return cls._default_instance is not None

    @classmethod
    def get_default_instance_sync(cls) -> CognitiveGateway | None:
        """同步获取全局单例（若已初始化），用于 sync 上下文注入.

        若单例尚未初始化，返回 None；调用方应自行 fallback 到 lazy init.
        """
        return cls._default_instance

    async def close(self) -> None:
        """清理资源，取消 warmup 任务."""
        if self._warmup_task is not None and not self._warmup_task.done():
            self._warmup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._warmup_task

    # ------------------------------------------------------------------
    # 健康检查
    # ------------------------------------------------------------------

    async def is_slm_healthy(self) -> bool:
        """返回 SLM 是否可用，带 TTL 缓存。"""
        async with self._health_lock:
            now = time.monotonic()
            if self._slm_healthy is not None and (now - self._last_health_check_at) < self._health_ttl_seconds:
                return self._slm_healthy

            healthy = await self._probe_slm_health()
            self._slm_healthy = healthy
            self._last_health_check_at = now
            logger.debug("SLM health probe result: %s (ttl=%ss)", healthy, self._health_ttl_seconds)
            return healthy

    async def _probe_slm_health(self) -> bool:
        """底层健康探测：尝试一次极轻量的 SLM 调用。"""
        logger.debug("[SLM health] 开始健康探测...")
        if self._slm_coprocessor is None:
            logger.debug("[SLM health] 不健康: coprocessor 未初始化")
            return False
        if not self.config.slm_enabled:
            logger.debug("[SLM health] 不健康: slm_enabled=False")
            return False
        # 先检查 provider 是否可达（避免 _invoke_slm 在 client=None 时静默返回空字符串）
        client = self._slm_coprocessor._get_slm_client()
        if client is None:
            logger.debug("[SLM health] 不健康: SLM client 未配置")
            return False
        try:
            # 使用一个极短 prompt 做探测，max_tokens=1 减少开销
            logger.debug("[SLM health] 发送探测请求 (prompt='hi', max_tokens=1)...")
            _result = await self._slm_coprocessor._invoke_slm("hi", max_tokens=1)
            healthy = isinstance(_result, str) and len(_result.strip()) > 0
            logger.debug(
                "[SLM health] 探测结果: healthy=%s, response_len=%d",
                healthy,
                len(_result) if isinstance(_result, str) else 0,
            )
            # 必须返回非空字符串才判定健康；空字符串意味着模型未加载成功
            return healthy
        except (ConnectionError, TimeoutError, RuntimeError):
            logger.debug("[SLM health] 探测异常", exc_info=True)
            return False

    def invalidate_health_cache(self) -> None:
        """手动失效健康缓存，用于外部感知到 SLM 状态变化时。"""
        self._slm_healthy = None
        self._last_health_check_at = 0.0

    # ------------------------------------------------------------------
    # 级联意图分类 (Waterfall)
    # ------------------------------------------------------------------

    async def classify_intent(self, message: str) -> str:
        """级联意图分类 — Embedding → SLM → Regex。

        返回标准意图标签（如 STRONG_MUTATION / DEBUG_AND_FIX 等）。
        保证永远有返回值（Regex 终极兜底）。
        """
        # Level 1: Embedding Router（若已 warmup 且启用）
        if self._embedding_router is not None and self.config.intent_embedding_enabled:
            try:
                emb_result = await self._embedding_router.classify(message)
                if emb_result is not None:
                    logger.debug("CognitiveGateway intent from embedding: %s", emb_result)
                    return emb_result
            except (ConnectionError, TimeoutError, RuntimeError, ValueError):
                logger.debug("Embedding router classify failed", exc_info=True)

        # Level 2: SLM Coprocessor（若健康且启用）
        if await self.is_slm_healthy():
            slm = self._slm_coprocessor
            if slm is not None:
                try:
                    slm_result = await slm.classify_intent(message)
                    if slm_result and slm_result != "UNKNOWN":
                        logger.debug("CognitiveGateway intent from SLM: %s", slm_result)
                        return slm_result
                except (ConnectionError, TimeoutError, RuntimeError, ValueError):
                    logger.debug("SLM classify_intent failed", exc_info=True)

        # Level 3: Hard-coded Regex（100% 可用兜底）
        regex_result = classify_intent_regex(message)
        logger.debug("CognitiveGateway intent from regex fallback: %s", regex_result)
        return regex_result

    # ------------------------------------------------------------------
    # 富路由协议 (Rich Routing Protocol)
    # ------------------------------------------------------------------

    _SLM_ROUTING_PROMPT: str = (
        "你是一个请求解析器。将用户请求解析为结构化执行工单。\n\n"
        "【关键规则】primary_mode 判定准则（严格遵守）：\n"
        "- 如果用户明确要求修改/优化/重构/修复/添加/删除/写入代码或文件，"
        "primary_mode 必须是 MATERIALIZE_CHANGES，即使改动很小\n"
        "- 如果用户要求'总结'、'分析'、'评估'、'给出建议'、'帮我看看'，"
        "无论是否提到'完善'或'改进'，primary_mode 必须是 ANALYZE_ONLY\n"
        "  （示例：'总结代码并给出完善建议'→ANALYZE_ONLY；'完善这段代码'→MATERIALIZE_CHANGES）\n"
        "- 只有当用户明确要求你'只分析不修改'、'规划方案但不实施'时，"
        "才使用 PROPOSE_PATCH\n"
        "- expected_actions 包含 'write_code' 或 'write_tests' 时，"
        "primary_mode 绝不可为 PROPOSE_PATCH 或 ANALYZE_ONLY\n\n"
        "要求：\n"
        "- 只输出 JSON，不要解释\n"
        "- explicit_targets 从用户消息中提取具体的文件路径、函数名、类名\n"
        '- expected_actions 可以包含多个（如 ["write_code", "summarize"]）\n'
        "- mutation_scale 按修改规模判断："
        "MINOR(1文件少量行)/MODERATE(跨文件)/MAJOR(架构级)\n\n"
        "JSON Schema:\n"
        "{\n"
        '  "reasoning": "简短分析用户的真实目的（限30字以内）",\n'
        '  "primary_mode": "ANALYZE_ONLY | PROPOSE_PATCH | MATERIALIZE_CHANGES",\n'
        '  "task_category": "feature_dev | bug_fix | refactor | optimization | '
        'exploration | explanation | code_review | documentation | devops | testing | security",\n'
        '  "expected_actions": ["read_files", "write_code", "write_tests", '
        '"run_commands", "summarize", "plan", "explain"],\n'
        '  "explicit_targets": ["文件名或函数名"],\n'
        '  "mutation_scale": "none | minor | moderate | major",\n'
        '  "requires_confirmation": false,\n'
        '  "is_negated": false,\n'
        '  "confidence": 0.95\n'
        "}\n\n"
        "用户请求：{user_message}\n"
    )

    @staticmethod
    def _parse_slm_bool(value: Any) -> bool:
        """安全解析 SLM 输出的布尔值（防御字符串 \"false\" 被 bool() 误判为 True）。"""
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.lower().strip() in ("true", "1", "yes", "on")
        return bool(value)

    def _apply_dual_resolution_guard(self, contract: DeliveryContract, raw_message: str) -> DeliveryContract:
        """双重决议容错：SLM-Regex 一致性校验（Intent Override）。

        当 SLM 保守地返回 PROPOSE_PATCH / ANALYZE_ONLY，但：
        1. expected_actions 已包含 WRITE_CODE / WRITE_TESTS（暴露真实意图），或
        2. Regex 守卫检测到明确的 mutation 意图（STRONG_MUTATION / WEAK_MUTATION / DEBUG_AND_FIX / DEVOPS）
        时，强制升级到 MATERIALIZE_CHANGES，避免任务中断。

        这是复杂智能体系统中经典的"双重决议容错"架构模式：
        永远不能 100% 信任概率模型对高危状态转换的判定。
        """
        if contract.mode == DeliveryMode.MATERIALIZE_CHANGES:
            return contract

        # Signal 1: SLM enrichment 已暴露写意图
        enrichment_signals_mutation = False
        if contract.enrichment is not None:
            actions = contract.enrichment.expected_actions
            if ExpectedAction.WRITE_CODE in actions or ExpectedAction.WRITE_TESTS in actions:
                enrichment_signals_mutation = True

        # Signal 2: Regex 引擎检测到 mutation 意图
        regex_intent = classify_intent_regex(raw_message)
        regex_signals_mutation = regex_intent in (
            "STRONG_MUTATION",
            "WEAK_MUTATION",
            "DEBUG_AND_FIX",
            "DEVOPS",
        )

        if enrichment_signals_mutation or regex_signals_mutation:
            override_reasons: list[str] = []
            if enrichment_signals_mutation:
                override_reasons.append("expected_actions contains write_code/write_tests")
            if regex_signals_mutation:
                override_reasons.append(f"regex detected {regex_intent}")

            logger.warning(
                "[Intent Override] SLM misclassified mutation as %s. "
                "Forcing upgrade to MATERIALIZE_CHANGES. Reasons: %s. "
                "Input: %s...",
                contract.mode.value,
                "; ".join(override_reasons),
                raw_message[:60],
            )

            # 升级契约到 MATERIALIZE_CHANGES
            new_contract = replace(
                contract,
                mode=DeliveryMode.MATERIALIZE_CHANGES,
                requires_mutation=True,
                allow_inline_code=False,
                allow_patch_proposal=False,
            )

            # 同步更新 enrichment（保留 SLM 提取的其他富字段，如 explicit_targets）
            if new_contract.enrichment is not None:
                new_enrichment = new_contract.enrichment.model_copy()
                new_enrichment.raw_intent_label = (
                    f"SLM_OVERRIDDEN({new_contract.enrichment.raw_intent_label or 'unknown'})"
                )
                if ExpectedAction.WRITE_CODE not in new_contract.enrichment.expected_actions:
                    new_enrichment.expected_actions = list(new_contract.enrichment.expected_actions)
                    new_enrichment.expected_actions.append(ExpectedAction.WRITE_CODE)
                new_contract = replace(new_contract, enrichment=new_enrichment)

            return new_contract

        return contract

    def _parse_routing_json(self, raw: str) -> DeliveryContract | None:
        """将 SLM 返回的 JSON 解析为 DeliveryContract + EnrichmentContext。"""
        import json
        import re

        text = str(raw or "").strip()
        if not text:
            return None

        # 剥离可能的 markdown code block
        code_block_match = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
        if code_block_match:
            text = code_block_match.group(1).strip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return None

        if not isinstance(data, dict):
            return None

        mode_str = str(data.get("primary_mode", "")).strip().upper().replace(" ", "_")
        mode_map = {
            "ANALYZE_ONLY": DeliveryMode.ANALYZE_ONLY,
            "PROPOSE_PATCH": DeliveryMode.PROPOSE_PATCH,
            "MATERIALIZE_CHANGES": DeliveryMode.MATERIALIZE_CHANGES,
        }
        mode = mode_map.get(mode_str, DeliveryMode.ANALYZE_ONLY)

        # 构建 EnrichmentContext（Pydantic model_validator 自动处理枚举清洗）
        try:
            # 防御 SLM 返回字符串而非列表
            _raw_actions = data.get("expected_actions")
            if isinstance(_raw_actions, str):
                _raw_actions = [_raw_actions]
            _raw_targets = data.get("explicit_targets")
            if isinstance(_raw_targets, str):
                _raw_targets = [_raw_targets]

            enrichment = EnrichmentContext(
                reasoning=str(data.get("reasoning", ""))[:50],
                task_category=data.get("task_category", "unknown"),
                expected_actions=_raw_actions if _raw_actions is not None else [],
                explicit_targets=_raw_targets if _raw_targets is not None else [],
                mutation_scale=data.get("mutation_scale", "none"),
                requires_confirmation=self._parse_slm_bool(data.get("requires_confirmation", False)),
                confidence=max(0.0, min(1.0, float(data.get("confidence", 0.0)))),
                is_negated=self._parse_slm_bool(data.get("is_negated", False)),
            )
        except (TypeError, ValueError, KeyError):
            logger.debug("EnrichmentContext parse failed from SLM JSON", exc_info=True)
            enrichment = None

        return DeliveryContract(
            mode=mode,
            requires_mutation=mode == DeliveryMode.MATERIALIZE_CHANGES,
            requires_verification=mode == DeliveryMode.MATERIALIZE_CHANGES,
            allow_inline_code=mode != DeliveryMode.MATERIALIZE_CHANGES,
            allow_patch_proposal=mode == DeliveryMode.PROPOSE_PATCH,
            enrichment=enrichment,
        )

    async def resolve_delivery_mode(self, user_message: str) -> DeliveryContract:
        """SLM 优先、regex 兜底的 delivery mode 解析。

        架构：
        1. 显式模式标记 → 直接返回（确定性规则，不走 SLM）
        2. 否定语境 → 直接返回 ANALYZE_ONLY（快速路径）
        3. classify_intent（Embedding → SLM → Regex）→ 意图标签映射到 DeliveryContract
        """
        latest_user = str(user_message or "")
        lowered = latest_user.lower()

        # Rule 1: 显式模式指令标记（确定性规则，不走 SLM）
        if "[mode:materialize]" in lowered or "[mode:materialize_changes]" in lowered:
            return DeliveryContract(
                mode=DeliveryMode.MATERIALIZE_CHANGES,
                requires_mutation=True,
                requires_verification=False,
                allow_inline_code=False,
                allow_patch_proposal=False,
            )
        if "[mode:propose]" in lowered or "[mode:propose_patch]" in lowered:
            return DeliveryContract(
                mode=DeliveryMode.PROPOSE_PATCH,
                requires_mutation=False,
                requires_verification=False,
                allow_inline_code=True,
                allow_patch_proposal=True,
            )
        if "[mode:analyze]" in lowered or "[mode:analyze_only]" in lowered:
            return DeliveryContract(
                mode=DeliveryMode.ANALYZE_ONLY,
                requires_mutation=False,
                requires_verification=False,
                allow_inline_code=True,
                allow_patch_proposal=False,
            )

        # Rule 2: 否定语境快速降级（本地路径，不消耗 SLM token）
        if _is_negated_mutation(latest_user):
            return DeliveryContract(
                mode=DeliveryMode.ANALYZE_ONLY,
                requires_mutation=False,
                requires_verification=False,
                allow_inline_code=True,
                allow_patch_proposal=False,
            )

        # Guard: 如果输入是编排器续写 prompt（包含 Goal/Progress/WorkingMemory XML 块），
        # 直接返回 ANALYZE_ONLY，不走 SLM 路由（防止续写 prompt 被 SLM 二次解析导致循环）。
        # 使用 case-insensitive 检查，兼容各种编码变体。
        _has_xml_zone = any(
            tag in latest_user or tag.lower() in latest_user.lower()
            for tag in ("<Goal>", "<Progress>", "<WorkingMemory>", "<Instruction>")
        )
        if _has_xml_zone:
            logger.debug("[SLM routing] 输入为续写 prompt（包含 XML zone），直接返回 ANALYZE_ONLY")
            return DeliveryContract(
                mode=DeliveryMode.ANALYZE_ONLY,
                requires_mutation=False,
                requires_verification=False,
                allow_inline_code=True,
                allow_patch_proposal=False,
            )

        # Rule 2.5: SLM 富路由解析（JSON 结构化输出）
        logger.debug("[SLM routing] Rule 2.5: 尝试 SLM 富路由解析...")
        is_healthy = await self.is_slm_healthy()
        logger.debug("[SLM routing] is_slm_healthy=%s", is_healthy)
        if is_healthy:
            slm = self._slm_coprocessor
            if slm is not None:
                try:
                    prompt = self._SLM_ROUTING_PROMPT.replace("{user_message}", latest_user)
                    logger.debug("[SLM routing] 发送结构化路由请求 (max_tokens=256)...")
                    raw_json = await slm._invoke_slm(prompt, max_tokens=256)
                    logger.debug(
                        "[SLM routing] SLM 返回 JSON (len=%d): %s",
                        len(raw_json) if raw_json else 0,
                        raw_json[:500] if raw_json else "<empty>",
                    )
                    if raw_json and raw_json.strip():
                        parsed = self._parse_routing_json(raw_json)
                        if parsed is not None:
                            logger.debug(
                                "[SLM routing] 解析成功: mode=%s, category=%s, confidence=%s",
                                parsed.mode,
                                parsed.enrichment.task_category if parsed.enrichment else "n/a",
                                parsed.enrichment.confidence if parsed.enrichment else "n/a",
                            )
                            # 双重决议容错：SLM 保守误判时 Regex 兜底强制升级
                            parsed = self._apply_dual_resolution_guard(parsed, latest_user)
                            return parsed
                        logger.debug("[SLM routing] JSON 解析返回 None，回退到 regex")
                    else:
                        logger.debug("[SLM routing] SLM 返回空结果，回退到 regex")
                except (ConnectionError, TimeoutError, RuntimeError, ValueError):
                    logger.debug("[SLM routing] SLM 路由请求失败", exc_info=True)
        else:
            logger.debug("[SLM routing] SLM 不健康，跳过富路由，回退到 regex")

        # Rule 3: 走 classify_intent 级联瀑布（Embedding → SLM → Regex）
        intent = await self.classify_intent(latest_user)

        if intent in ("STRONG_MUTATION", "DEBUG_AND_FIX", "DEVOPS"):
            return enrich_delivery_contract(
                intent,
                DeliveryContract(
                    mode=DeliveryMode.MATERIALIZE_CHANGES,
                    requires_mutation=True,
                    requires_verification=False,
                    allow_inline_code=False,
                    allow_patch_proposal=False,
                ),
            )
        if intent == "WEAK_MUTATION":
            return enrich_delivery_contract(
                intent,
                DeliveryContract(
                    mode=DeliveryMode.MATERIALIZE_CHANGES,
                    requires_mutation=True,
                    requires_verification=False,
                    allow_inline_code=False,
                    allow_patch_proposal=False,
                ),
            )
        if intent == "TESTING":
            return enrich_delivery_contract(
                intent,
                DeliveryContract(
                    mode=DeliveryMode.PROPOSE_PATCH,
                    requires_mutation=False,
                    requires_verification=True,
                    allow_inline_code=True,
                    allow_patch_proposal=True,
                ),
            )
        if intent == "PLANNING":
            return enrich_delivery_contract(
                intent,
                DeliveryContract(
                    mode=DeliveryMode.PROPOSE_PATCH,
                    requires_mutation=False,
                    requires_verification=False,
                    allow_inline_code=True,
                    allow_patch_proposal=True,
                ),
            )
        if intent == "ANALYSIS_ONLY":
            return enrich_delivery_contract(
                intent,
                DeliveryContract(
                    mode=DeliveryMode.ANALYZE_ONLY,
                    requires_mutation=False,
                    requires_verification=False,
                    allow_inline_code=True,
                    allow_patch_proposal=False,
                ),
            )
        # UNKNOWN → ANALYZE_ONLY（安全默认值）
        return enrich_delivery_contract(
            intent,
            DeliveryContract(
                mode=DeliveryMode.ANALYZE_ONLY,
                requires_mutation=False,
                requires_verification=False,
                allow_inline_code=True,
                allow_patch_proposal=False,
            ),
        )

    # ------------------------------------------------------------------
    # 任务模板入口
    # ------------------------------------------------------------------

    async def distill_logs(self, raw_logs: str, max_tokens: int = 500) -> str:
        """日志降维 — SLM 可用时走 SLM，否则暴力截断。"""
        if await self.is_slm_healthy():
            slm = self._slm_coprocessor
            if slm is not None:
                try:
                    return await slm.distill_long_logs(raw_logs, max_tokens=max_tokens)
                except (ConnectionError, TimeoutError, RuntimeError, ValueError):
                    logger.debug("SLM distill_long_logs failed", exc_info=True)
        # Fallback: 暴力截断尾部
        return raw_logs[-2000:] if len(raw_logs) > 2000 else raw_logs

    async def heal_json(self, broken_json: str) -> dict[str, Any] | None:
        """JSON 修复 — SLM 可用时走 SLM，否则返回 None。"""
        if await self.is_slm_healthy():
            slm = self._slm_coprocessor
            if slm is not None:
                try:
                    return await slm.heal_json(broken_json)
                except (ConnectionError, TimeoutError, RuntimeError, ValueError):
                    logger.debug("SLM heal_json failed", exc_info=True)
        return None

    async def expand_query(self, user_query: str) -> list[str]:
        """搜索查询扩展 — SLM 可用时走 SLM，否则返回原查询。"""
        if await self.is_slm_healthy():
            slm = self._slm_coprocessor
            if slm is not None:
                try:
                    return await slm.expand_search_query(user_query)
                except (ConnectionError, TimeoutError, RuntimeError, ValueError):
                    logger.debug("SLM expand_search_query failed", exc_info=True)
        return [user_query]

    async def compress_text(self, prompt: str, max_tokens: int = 500) -> str:
        """通用文本压缩 — 使用自定义 prompt 调用 SLM。

        供 ContextOS SLMSummarizer 等外部模块使用，支持语义压缩。
        """
        if await self.is_slm_healthy():
            slm = self._slm_coprocessor
            if slm is not None:
                try:
                    return await slm._invoke_slm(prompt, max_tokens=max_tokens)
                except (ConnectionError, TimeoutError, RuntimeError, ValueError):
                    logger.debug("SLM compress_text failed", exc_info=True)
        return ""

    # ------------------------------------------------------------------
    # 统一任务调度
    # ------------------------------------------------------------------

    async def execute_task(
        self,
        task: COGNITIVE_TASK,
        payload: str,
        **kwargs: Any,
    ) -> Any:
        """统一任务调度入口。

        task: INTENT_CLASSIFY | LOG_DISTILL | JSON_HEAL | QUERY_EXPAND
        payload: 任务输入文本
        """
        if task == "INTENT_CLASSIFY":
            return await self.classify_intent(payload)
        if task == "LOG_DISTILL":
            return await self.distill_logs(payload, max_tokens=kwargs.get("max_tokens", 500))
        if task == "JSON_HEAL":
            return await self.heal_json(payload)
        if task == "QUERY_EXPAND":
            return await self.expand_query(payload)
        raise ValueError(f"Unknown cognitive task: {task}")
