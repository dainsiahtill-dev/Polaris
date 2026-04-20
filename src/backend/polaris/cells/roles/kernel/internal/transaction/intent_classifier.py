"""用户请求意图检测 — 区分 mutation / verification / analysis。

纯函数模块，无外部依赖，便于单元测试。
"""

from __future__ import annotations

import re
from dataclasses import replace

from polaris.cells.roles.kernel.internal.transaction.constants import (
    _EN_ANALYSIS_RE,
    _EN_DEBUG_FIX_RE,
    _EN_DEVOPS_RE,
    _EN_PLANNING_RE,
    _EN_STRONG_MUTATION_RE,
    _EN_TESTING_RE,
    _EN_WEAK_MUTATION_RE,
    ANALYSIS_ONLY_SIGNALS,
    DEBUG_AND_FIX_CN_MARKERS,
    DEVOPS_CONFIG_SIGNALS,
    PLANNING_SIGNALS,
    STRONG_MUTATION_CN_MARKERS,
    TESTING_SIGNALS,
    VERIFICATION_CN_MARKERS,
    VERIFICATION_EN_PATTERN,
    WEAK_MUTATION_CN_MARKERS,
)
from polaris.cells.roles.kernel.internal.transaction.delivery_contract import (
    DeliveryContract,
    DeliveryMode,
    EnrichmentContext,
    ExpectedAction,
    MutationScale,
    TaskCategory,
)


def _is_negated_mutation(message: str) -> bool:
    """检测消息是否是否定 mutation 语境（如'不要修改'、'无需更新'）。

    注意：这是基于 regex 的简化检测，无法处理复杂语义（如"不要改 A，但要改 B"）。
    复杂场景应委托 CognitiveGateway（SLM）处理。
    """
    negation_patterns = [
        r"不[要需用必].{0,5}(?:修改|更新|删除|替换|创建|写入|追加|改动|重写|插入|编写|开发)",
        r"别[去要].{0,5}(?:修改|更新|删除|替换|创建|写入|追加|改动|重写|插入|编写|开发)",
        r"无需.{0,5}(?:修改|更新|删除|替换|创建|写入|追加|改动|重写|插入|编写|开发)",
        r"不用.{0,5}(?:修改|更新|删除|替换|创建|写入|追加|改动|重写|插入|编写|开发)",
    ]
    if any(re.search(pattern, message) for pattern in negation_patterns):
        return True
    lowered = message.lower()
    return bool(
        re.search(
            r"\b(?:do not|don't|never|no need to)\b.{0,10}\b(?:modify|change|replace|create|write|append|update|delete|edit|patch|implement|remove|insert|generate|build|add)\b",
            lowered,
        )
    )


# Characters that, when immediately preceding a CN debug marker, indicate it is
# part of a compound/negated word rather than an independent intent signal.
# e.g. '不确定位置' -> '定位' is matched but preceded by '确', so it's a false positive.
_CN_DEBUG_NEGATION_PRECEDING = frozenset("不无未难确")


def _has_cn_debug_fix_signal(text: str) -> bool:
    """Context-aware Chinese DEBUG_AND_FIX marker matching.

    Prevents false positives where a debug marker appears as a sub-word in an
    unrelated phrase, e.g. '不确定位置' accidentally containing '定位'.

    A match is only valid if the character immediately before the marker is NOT
    one of the common negation/uncertainty prefixes.
    """
    for marker in DEBUG_AND_FIX_CN_MARKERS:
        idx = 0
        while True:
            pos = text.find(marker, idx)
            if pos < 0:
                break
            idx = pos + 1
            # If the preceding character is a negation/uncertainty prefix, skip.
            if pos > 0 and text[pos - 1] in _CN_DEBUG_NEGATION_PRECEDING:
                continue
            return True
    return False


def classify_intent_regex(message: str) -> str:
    """基于硬编码正则/关键词的意图分类，返回标准意图标签。

    优先级（从高到低）:
    STRONG_MUTATION > DEBUG_AND_FIX > DEVOPS > WEAK_MUTATION > TESTING > PLANNING > ANALYSIS_ONLY > UNKNOWN
    """
    latest_user = str(message or "")
    lowered = latest_user.lower()
    has_analysis_only = any(marker in lowered for marker in ANALYSIS_ONLY_SIGNALS)

    # 否定语境检测："不要修改"等应降级，不走 mutation 路径
    is_negated = _is_negated_mutation(latest_user)

    # 1. Strong mutation
    if not is_negated and any(marker in latest_user for marker in STRONG_MUTATION_CN_MARKERS):
        return "STRONG_MUTATION"
    if not is_negated and _EN_STRONG_MUTATION_RE.search(lowered):
        return "STRONG_MUTATION"

    # 2. Debug and fix（否定语境降级；分析语境覆盖）
    if not is_negated:
        # Use context-aware CN matching to avoid sub-word false positives
        # (e.g. '不确定位置' must NOT match '定位').
        if _has_cn_debug_fix_signal(latest_user):
            return "DEBUG_AND_FIX" if not has_analysis_only else "ANALYSIS_ONLY"
        if _EN_DEBUG_FIX_RE.search(lowered):
            return "DEBUG_AND_FIX" if not has_analysis_only else "ANALYSIS_ONLY"

    # 3. DevOps
    if any(marker in latest_user for marker in DEVOPS_CONFIG_SIGNALS):
        return "DEVOPS"
    if _EN_DEVOPS_RE.search(lowered):
        return "DEVOPS"

    # 4. Weak mutation (受分析语境影响)
    if any(marker in latest_user for marker in WEAK_MUTATION_CN_MARKERS):
        return "WEAK_MUTATION" if not has_analysis_only else "ANALYSIS_ONLY"
    if _EN_WEAK_MUTATION_RE.search(lowered):
        return "WEAK_MUTATION" if not has_analysis_only else "ANALYSIS_ONLY"

    # 5. Testing
    if any(marker in latest_user for marker in TESTING_SIGNALS):
        return "TESTING"
    if _EN_TESTING_RE.search(lowered):
        return "TESTING"

    # 6. Planning
    if any(marker in latest_user for marker in PLANNING_SIGNALS):
        return "PLANNING"
    if _EN_PLANNING_RE.search(lowered):
        return "PLANNING"

    # 7. Analysis only
    if any(marker in latest_user for marker in ANALYSIS_ONLY_SIGNALS):
        return "ANALYSIS_ONLY"
    if _EN_ANALYSIS_RE.search(lowered):
        return "ANALYSIS_ONLY"

    return "UNKNOWN"


def requires_mutation_intent(message: str) -> bool:
    """判定用户消息是否包含明确的文件/代码修改意图。

    逻辑分层：
    1. 若消息含分析专用信号（如"建议"、"分析"），则即使含弱突变词也返回 False。
    2. 强突变标记（如"修改"、"create"）直接返回 True。
    3. 弱突变标记（如"完善"）仅在非分析语境下返回 True。
    """
    intent = classify_intent_regex(message)
    return intent in {"STRONG_MUTATION", "DEBUG_AND_FIX", "DEVOPS", "WEAK_MUTATION"}


def requires_verification_intent(message: str) -> bool:
    """判定用户消息是否包含验证/测试意图。"""
    latest_user = str(message or "")
    lowered = latest_user.lower()
    if any(marker in latest_user for marker in VERIFICATION_CN_MARKERS):
        return True
    if bool(re.search(VERIFICATION_EN_PATTERN, lowered)):
        return True
    return classify_intent_regex(message) == "TESTING"


# ---------------------------------------------------------------------------
# Delivery Mode 解析 — 规则引擎
# ---------------------------------------------------------------------------


def resolve_delivery_mode(user_message: str) -> DeliveryContract:
    """基于规则引擎解析交付模式。

    规则优先级（从高到低）：
    1. 显式模式指令标记（如 [mode:materialize]）
    2. 强突变信号 → MATERIALIZE_CHANGES
    3. 调试修复信号 → MATERIALIZE_CHANGES
    4. DevOps 配置信号 → MATERIALIZE_CHANGES
    5. 弱突变信号（无分析语境）→ MATERIALIZE_CHANGES
    6. 测试验证信号 → PROPOSE_PATCH
    7. 规划/设计信号 → PROPOSE_PATCH
    8. 纯分析信号 → ANALYZE_ONLY
    9. 默认 → ANALYZE_ONLY
    """
    latest_user = str(user_message or "")
    lowered = latest_user.lower()

    # Rule 0: 否定语境降级 — 明确拒绝 mutation 的消息直接返回 ANALYZE_ONLY
    # （简化版，复杂语义应委托 CognitiveGateway / SLM 路由）
    if _is_negated_mutation(latest_user):
        return DeliveryContract(
            mode=DeliveryMode.ANALYZE_ONLY,
            requires_mutation=False,
            requires_verification=False,
            allow_inline_code=True,
            allow_patch_proposal=False,
        )

    # Rule 1: 显式模式指令标记
    explicit = _detect_explicit_mode_marker(lowered)
    if explicit is not None:
        return explicit

    has_analysis_only = any(marker in lowered for marker in ANALYSIS_ONLY_SIGNALS)

    # Rule 2-5: 突变意图 → MATERIALIZE_CHANGES
    if _has_strong_mutation_signal(latest_user, lowered):
        return DeliveryContract(
            mode=DeliveryMode.MATERIALIZE_CHANGES,
            requires_mutation=True,
            requires_verification=_has_verification_signal(latest_user, lowered),
            allow_inline_code=False,
            allow_patch_proposal=False,
        )

    if _has_debug_fix_signal(latest_user, lowered):
        return DeliveryContract(
            mode=DeliveryMode.MATERIALIZE_CHANGES,
            requires_mutation=True,
            requires_verification=True,
            allow_inline_code=False,
            allow_patch_proposal=False,
        )

    if _has_devops_signal(latest_user, lowered):
        return DeliveryContract(
            mode=DeliveryMode.MATERIALIZE_CHANGES,
            requires_mutation=True,
            requires_verification=True,
            allow_inline_code=False,
            allow_patch_proposal=False,
        )

    if _has_weak_mutation_signal(latest_user, lowered) and not has_analysis_only:
        return DeliveryContract(
            mode=DeliveryMode.MATERIALIZE_CHANGES,
            requires_mutation=True,
            requires_verification=False,
            allow_inline_code=False,
            allow_patch_proposal=False,
        )

    # Rule 6-7: 规划/测试 → PROPOSE_PATCH
    if _has_testing_signal(latest_user, lowered):
        return DeliveryContract(
            mode=DeliveryMode.PROPOSE_PATCH,
            requires_mutation=False,
            requires_verification=True,
            allow_inline_code=True,
            allow_patch_proposal=True,
        )

    if _has_planning_signal(latest_user, lowered):
        return DeliveryContract(
            mode=DeliveryMode.PROPOSE_PATCH,
            requires_mutation=False,
            requires_verification=False,
            allow_inline_code=True,
            allow_patch_proposal=True,
        )

    # Rule 8: 纯分析
    if has_analysis_only or _has_analysis_signal(latest_user, lowered):
        return DeliveryContract(
            mode=DeliveryMode.ANALYZE_ONLY,
            requires_mutation=False,
            requires_verification=False,
            allow_inline_code=True,
            allow_patch_proposal=False,
        )

    # Rule 9: 默认
    return DeliveryContract(
        mode=DeliveryMode.ANALYZE_ONLY,
        requires_mutation=False,
        requires_verification=False,
        allow_inline_code=True,
        allow_patch_proposal=False,
    )


def _detect_explicit_mode_marker(lowered: str) -> DeliveryContract | None:
    """检测显式模式指令标记。"""
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
    return None


def _has_strong_mutation_signal(raw: str, lowered: str) -> bool:
    if _is_negated_mutation(raw):
        return False
    return any(marker in raw for marker in STRONG_MUTATION_CN_MARKERS) or bool(_EN_STRONG_MUTATION_RE.search(lowered))


def _has_weak_mutation_signal(raw: str, lowered: str) -> bool:
    return any(marker in raw for marker in WEAK_MUTATION_CN_MARKERS) or bool(_EN_WEAK_MUTATION_RE.search(lowered))


def _has_debug_fix_signal(raw: str, lowered: str) -> bool:
    # Use context-aware CN matching to avoid sub-word false positives.
    return _has_cn_debug_fix_signal(raw) or bool(_EN_DEBUG_FIX_RE.search(lowered))


def _has_devops_signal(raw: str, lowered: str) -> bool:
    return any(marker in raw for marker in DEVOPS_CONFIG_SIGNALS) or bool(_EN_DEVOPS_RE.search(lowered))


def _has_testing_signal(raw: str, lowered: str) -> bool:
    return any(marker in raw for marker in TESTING_SIGNALS) or bool(_EN_TESTING_RE.search(lowered))


def _has_planning_signal(raw: str, lowered: str) -> bool:
    return any(marker in raw for marker in PLANNING_SIGNALS) or bool(_EN_PLANNING_RE.search(lowered))


def _has_analysis_signal(raw: str, lowered: str) -> bool:
    return any(marker in lowered for marker in ANALYSIS_ONLY_SIGNALS) or bool(_EN_ANALYSIS_RE.search(lowered))


def _has_verification_signal(raw: str, lowered: str) -> bool:
    return any(marker in raw for marker in VERIFICATION_CN_MARKERS) or bool(re.search(VERIFICATION_EN_PATTERN, lowered))


# ---------------------------------------------------------------------------
# Inline Patch Escape 检测器
# ---------------------------------------------------------------------------


def detect_inline_patch_escape(content: str, *, threshold: float | None = None) -> dict:
    """检测 LLM 输出是否包含 Inline Patch Escape（贴代码逃逸）。

    算法：
    1. 提取内容中所有代码块（```...```）
    2. 计算代码块字符数 / 总字符数 = token density ratio
    3. 若 ratio > threshold 且总字符数 > 200，判定为 INLINE_PATCH_ESCAPE

    返回：
    {
        "is_escape": bool,
        "ratio": float,
        "code_block_chars": int,
        "total_chars": int,
        "code_blocks_count": int,
    }
    """
    threshold = threshold if threshold is not None else 0.60
    text = str(content or "")
    total_chars = len(text)

    if total_chars == 0:
        return {
            "is_escape": False,
            "ratio": 0.0,
            "code_block_chars": 0,
            "total_chars": 0,
            "code_blocks_count": 0,
        }

    # 提取 ```...``` 代码块（更健壮的正则：支持可选语言标识符、可选换行、末尾无换行）
    code_block_pattern = re.compile(r"```(?:[\w]*\s*)\n?(.*?)```", re.DOTALL)
    matches = code_block_pattern.findall(text)
    code_block_chars = sum(len(m) for m in matches)
    code_blocks_count = len(matches)

    ratio = code_block_chars / total_chars if total_chars > 0 else 0.0

    # 启发式增强：检测 diff/patch 模式（---/+++ 或 @@ 行）
    has_diff_pattern = bool(re.search(r"^(---|\+\+|@@)\s", text, re.MULTILINE))
    if has_diff_pattern:
        ratio = max(ratio, 0.55)  # diff 模式提升 ratio 以更容易触发检测

    # 额外启发式：检测内联 patch/proposal 信号（--- a/... +++ b/...）
    if re.search(r"^---\s+\S+\s*\n^\+\+\+\s+\S+", text, re.MULTILINE):
        ratio = max(ratio, 0.50)

    is_escape = ratio > threshold and total_chars > 200 and code_blocks_count > 0

    return {
        "is_escape": is_escape,
        "ratio": round(ratio, 4),
        "code_block_chars": code_block_chars,
        "total_chars": total_chars,
        "code_blocks_count": code_blocks_count,
    }


# ---------------------------------------------------------------------------
# Regex fallback → EnrichmentContext 映射（Rich Routing Protocol Phase 1）
# ---------------------------------------------------------------------------

_INTENT_TO_ENRICHMENT: dict[str, EnrichmentContext] = {
    "STRONG_MUTATION": EnrichmentContext(
        task_category=TaskCategory.FEATURE_DEV,
        expected_actions=[ExpectedAction.WRITE_CODE],
        mutation_scale=MutationScale.MODERATE,
        raw_intent_label="STRONG_MUTATION",
    ),
    "DEBUG_AND_FIX": EnrichmentContext(
        task_category=TaskCategory.BUG_FIX,
        expected_actions=[ExpectedAction.READ_FILES, ExpectedAction.WRITE_CODE],
        mutation_scale=MutationScale.MINOR,
        raw_intent_label="DEBUG_AND_FIX",
    ),
    "DEVOPS": EnrichmentContext(
        task_category=TaskCategory.DEVOPS,
        expected_actions=[ExpectedAction.READ_FILES, ExpectedAction.WRITE_CODE, ExpectedAction.RUN_COMMANDS],
        mutation_scale=MutationScale.MINOR,
        raw_intent_label="DEVOPS",
    ),
    "WEAK_MUTATION": EnrichmentContext(
        task_category=TaskCategory.FEATURE_DEV,
        expected_actions=[ExpectedAction.WRITE_CODE],
        mutation_scale=MutationScale.MINOR,
        raw_intent_label="WEAK_MUTATION",
    ),
    "TESTING": EnrichmentContext(
        task_category=TaskCategory.TESTING,
        expected_actions=[ExpectedAction.WRITE_TESTS, ExpectedAction.RUN_COMMANDS],
        mutation_scale=MutationScale.MINOR,
        raw_intent_label="TESTING",
    ),
    "PLANNING": EnrichmentContext(
        task_category=TaskCategory.FEATURE_DEV,
        expected_actions=[ExpectedAction.PLAN, ExpectedAction.READ_FILES],
        mutation_scale=MutationScale.MODERATE,
        raw_intent_label="PLANNING",
    ),
    "ANALYSIS_ONLY": EnrichmentContext(
        task_category=TaskCategory.EXPLORATION,
        expected_actions=[ExpectedAction.READ_FILES, ExpectedAction.EXPLAIN],
        mutation_scale=MutationScale.NONE,
        raw_intent_label="ANALYSIS_ONLY",
    ),
    "UNKNOWN": EnrichmentContext(
        task_category=TaskCategory.UNKNOWN,
        expected_actions=[ExpectedAction.READ_FILES],
        mutation_scale=MutationScale.NONE,
        raw_intent_label="UNKNOWN",
    ),
}


def enrich_delivery_contract(intent: str, contract: DeliveryContract) -> DeliveryContract:
    """为 regex fallback 产生的 DeliveryContract 附加基础 EnrichmentContext（深拷贝，避免污染全局实例）。"""
    enrichment = _INTENT_TO_ENRICHMENT.get(intent)
    if enrichment is not None:
        return replace(contract, enrichment=enrichment.model_copy(deep=True))
    return contract
