"""Intent + delivery-contract resolution helpers for the turn kernel.

# -*- coding: utf-8 -*-
UTF-8 编码验证: 本文所有文本使用 UTF-8

Canonical delivery/intent helper implementations owned by the transaction
package. Controller call sites consume this module for delivery-mode parsing,
explicit materialization markers, and continuation intent inheritance.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping

from polaris.cells.roles.kernel.internal.transaction.delivery_contract import (
    DeliveryContract,
    DeliveryMode,
)
from polaris.cells.roles.kernel.internal.transaction.intent_classifier import (
    resolve_delivery_mode,
)
from polaris.cells.roles.kernel.internal.transaction.ledger import TurnLedger
from polaris.cells.roles.kernel.public.turn_contracts import (
    RawLLMResponse,
    TurnDecision,
)

_EXPLICIT_MATERIALIZE_MODE_MARKERS = (
    "[mode:materialize]",
    "[mode:materialize_changes]",
)


def has_explicit_materialize_delivery_marker(user_message: str) -> bool:
    lowered = str(user_message or "").lower()
    return any(marker in lowered for marker in _EXPLICIT_MATERIALIZE_MODE_MARKERS)


def enforce_explicit_materialize_delivery_marker(
    user_message: str,
    contract: DeliveryContract,
) -> DeliveryContract:
    """Make an explicit materialize marker authoritative over classifier output."""

    if not has_explicit_materialize_delivery_marker(user_message):
        return contract
    if contract.mode == DeliveryMode.MATERIALIZE_CHANGES:
        return contract
    return DeliveryContract(
        mode=DeliveryMode.MATERIALIZE_CHANGES,
        requires_mutation=True,
        requires_verification=contract.requires_verification,
        allow_inline_code=False,
        allow_patch_proposal=False,
        enrichment=contract.enrichment,
    )


def detect_target_files_known(context: list[dict]) -> bool:
    """检测上下文中是否包含明确的文件路径信息。"""
    for message in context:
        if not isinstance(message, Mapping):
            continue
        content = str(message.get("content") or "")
        # 简单启发式：包含常见代码文件扩展名或路径分隔符
        code_extensions = (
            ".py",
            ".ts",
            ".tsx",
            ".js",
            ".jsx",
            ".java",
            ".go",
            ".rs",
            ".cpp",
            ".c",
            ".h",
            ".md",
            ".json",
            ".yaml",
            ".yml",
            ".toml",
        )
        if any(ext in content for ext in code_extensions):
            return True
        # 检测路径模式
        if "/" in content or "\\" in content:
            # 排除URL
            lines = content.splitlines()
            for line in lines:
                stripped = line.strip()
                if stripped.startswith("http://") or stripped.startswith("https://"):
                    continue
                if "/" in stripped or "\\" in stripped:
                    parts = stripped.replace("\\", "/").split("/")
                    for part in parts:
                        if part and "." in part and not part.startswith("."):
                            return True
    return False


def is_refusal_response(response: RawLLMResponse) -> bool:
    """检测 LLM 响应是否为拒绝执行（refusal）."""
    from polaris.cells.roles.kernel.internal.transaction.stream_orchestrator import (
        is_refusal_response,
    )

    return is_refusal_response(response)


def inherit_materialize_from_history(context: list[dict], latest_user_request: str) -> DeliveryContract | None:
    """多轮对话意图继承：最新消息丢失 mutation 意图时，从历史消息中恢复。

    场景：用户先说"实现 XX 功能"，之后说"继续""开始吧""OK"等短指令。
    此时 latest_user_request 不含 mutation 标记，但任务本质仍需 MATERIALIZE。

    继承条件（全部满足）：
    1. 最新消息是短指令（<=20 字符或匹配 continuation markers）
    2. 最近 3 轮历史用户消息中存在 MATERIALIZE_CHANGES 意图
    3. 无显式 [mode:analyze] 等降级指令
    """
    # 条件 3：最新消息本身若是否定突变（如"不要修改"）或显式 analyze/propose
    # 降级标记，则绝不继承历史 MATERIALIZE 意图——否则会覆盖用户当下明确的
    # 降级请求并重新打开写入（fail-open）。复用 intent_classifier 的判定，
    # 避免另造正则。
    from polaris.cells.roles.kernel.internal.transaction.intent_classifier import (
        _detect_explicit_mode_marker,
        _is_negated_mutation,
    )

    if _is_negated_mutation(latest_user_request):
        return None
    explicit_marker = _detect_explicit_mode_marker(latest_user_request.lower())
    if explicit_marker is not None and explicit_marker.mode in (
        DeliveryMode.ANALYZE_ONLY,
        DeliveryMode.PROPOSE_PATCH,
    ):
        return None

    continuation_shortcuts: tuple[str, ...] = (
        "继续",
        "开始",
        "ok",
        "好",
        "行",
        "可以",
        "执行",
        "落实",
        "动手",
        "搞",
        "冲",
        "推进",
        "next",
        "go",
        "yes",
        "yeah",
        " proceed",
        "do it",
        "let's go",
        "开始吧",
        "那就开始",
    )
    lowered_latest = latest_user_request.lower().strip()
    is_shortcut = len(latest_user_request) <= 20 or any(marker in lowered_latest for marker in continuation_shortcuts)
    if not is_shortcut:
        return None

    # 检查最近 3 轮历史用户消息
    user_messages: list[str] = []
    for msg in reversed(context):
        if not isinstance(msg, Mapping):
            continue
        role = str(msg.get("role") or "").strip().lower()
        if role != "user":
            continue
        content = str(msg.get("content") or "").strip()
        if content and content != latest_user_request:
            user_messages.append(content)
            if len(user_messages) >= 3:
                break

    for historical_msg in user_messages:
        historical_contract = resolve_delivery_mode(historical_msg)
        if historical_contract.mode == DeliveryMode.MATERIALIZE_CHANGES:
            # 继承历史意图，但保留最新消息中可能的 verification 要求
            return DeliveryContract(
                mode=DeliveryMode.MATERIALIZE_CHANGES,
                requires_mutation=True,
                requires_verification=historical_contract.requires_verification,
                allow_inline_code=False,
                allow_patch_proposal=False,
            )
    return None


def apply_delivery_mode_filter(decision: TurnDecision, ledger: TurnLedger) -> TurnDecision:
    """根据 delivery_contract 过滤决策中的 write tools。

    PROPOSE_PATCH / ANALYZE_ONLY 模式下禁止 write tools。
    若检测到 write tools，过滤后降级为 FINAL_ANSWER。

    实现统一委托给 ``contract_guards.apply_delivery_mode_filter``，使 run 模式
    与 stream 模式共用同一只读/提案边界语义。
    """
    from polaris.cells.roles.kernel.internal.transaction.contract_guards import (
        apply_delivery_mode_filter,
    )

    return apply_delivery_mode_filter(decision, ledger)


def classify_user_intent(message: str) -> str:
    """对用户消息进行意图分类，返回最匹配的意图类别。

    委托给 intent_classifier.classify_intent_regex 以消除代码重复。
    """
    from polaris.cells.roles.kernel.internal.transaction.intent_classifier import (
        classify_intent_regex,
    )

    return classify_intent_regex(message)


async def requires_mutation_intent_hybrid(message: str) -> bool:
    """Async hybrid version of requires_mutation_intent.

    统一委托 CognitiveGateway（Embedding -> SLM -> Regex 级联瀑布），
    不再保留本地 hybrid 路径，确保全系统意图分类单一真相来源。
    """
    from polaris.cells.roles.kernel.internal.transaction.cognitive_gateway import (
        CognitiveGateway,
    )
    from polaris.cells.roles.kernel.internal.transaction.intent_classifier import (
        _is_negated_mutation,
    )

    if _is_negated_mutation(message):
        return False

    gateway = CognitiveGateway.get_default_instance_sync()
    if gateway is not None:
        intent = await gateway.classify_intent(message)
    else:
        # Gateway 尚未初始化：同步回退到纯 regex（零依赖、零延迟）
        from polaris.cells.roles.kernel.internal.transaction.intent_classifier import (
            classify_intent_regex,
        )

        intent = classify_intent_regex(message)
    return intent in {"STRONG_MUTATION", "DEBUG_AND_FIX", "DEVOPS", "WEAK_MUTATION"}


def requires_mutation_intent(message: str) -> bool:
    """判定用户请求是否要求代码/文件突变（需要写工具）。"""
    from polaris.cells.roles.kernel.internal.transaction.intent_classifier import (
        _is_negated_mutation,
    )

    if _is_negated_mutation(message):
        return False
    intent = classify_user_intent(message)
    return intent in {"STRONG_MUTATION", "DEBUG_AND_FIX", "DEVOPS", "WEAK_MUTATION"}


async def resolve_delivery_mode_hybrid(user_message: str) -> DeliveryContract:
    """SLM 优先、regex 兜底的 delivery mode 解析。

    先尝试 CognitiveGateway（统一级联入口），若不可用则回退到
    本地 regex 规则引擎。保证永远有返回值。
    """
    try:
        from polaris.cells.roles.kernel.internal.transaction.cognitive_gateway import (
            CognitiveGateway,
        )

        gateway = CognitiveGateway.get_default_instance_sync()
        if gateway is not None:
            return await gateway.resolve_delivery_mode(user_message)
    except (ImportError, AttributeError, RuntimeError, asyncio.TimeoutError, OSError):
        pass
    # Gateway 未初始化或失败：回退到 regex
    return resolve_delivery_mode(user_message)


def requires_verification_intent(message: str) -> bool:
    """判定用户请求是否要求验证/测试（需要 test/verify 类工具）。"""
    latest_user = str(message or "")
    lowered = latest_user.lower()
    if any(marker in latest_user for marker in ("验证", "校验", "测试")):
        return True
    if re.search(r"\b(verify|validation|validate|test|pytest|check)\b", lowered):
        return True
    return classify_user_intent(message) == "TESTING"
