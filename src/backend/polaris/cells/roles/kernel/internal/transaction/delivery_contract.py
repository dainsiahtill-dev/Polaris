"""Delivery Contract - 事务交付模式与 Mutation 义务追踪。

本模块定义 TransactionKernel 的核心交付契约，解决"模型贴代码逃逸"的结构性问题：
- DeliveryContract: 用户意图的交付模式（分析/提案/落盘）
- MutationObligationState: 追踪 mutation 任务的履约进度
- BlockedReason: 合法退出终态的标准化原因

核心不变量：
  Invariant A: MATERIALIZE_CHANGES 模式下，产生至少一个 authoritative write receipt 前，
               不得接受 FINAL_ANSWER 作为任务完成结果。
  Invariant B: MATERIALIZE_CHANGES 模式下，模型输出大段代码但没有 write receipt 时，
               必须判定为 INLINE_PATCH_ESCAPE。
  Invariant C: FINAL_ANSWER 阶段只能总结已发生的事实，不承担修改代码的职责。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class DeliveryMode(str, Enum):
    """交付模式 — 决定模型输出行为的语义边界。"""

    ANALYZE_ONLY = "analyze_only"
    """只允许读、分析、总结、建议。允许输出代码示例，但不承诺落盘。"""

    PROPOSE_PATCH = "propose_patch"
    """允许输出 patch 提案、diff 草案、示例代码，但不落盘。"""

    MATERIALIZE_CHANGES = "materialize_changes"
    """必须通过 write/edit tool 落盘到目标文件。禁止把新增代码当交付物直接贴出。"""


class BlockedReason(str, Enum):
    """BLOCKED 终态的标准化退出原因 —— 给模型合法退路，防止贴代码自救。"""

    TARGET_FILE_UNKNOWN = "target_file_unknown"
    INSUFFICIENT_CONTEXT = "insufficient_context"
    NO_WRITE_TOOL_AVAILABLE = "no_write_tool_available"
    EDIT_CONFLICT = "edit_conflict"
    VERIFICATION_FAILED = "verification_failed"
    SAFETY_CONSTRAINT = "safety_constraint"
    USER_CANCELLED = "user_cancelled"
    PHASE_TIMEOUT = "phase_timeout"


class TaskCategory(str, Enum):
    """任务业务定性 — 用于动态 Prompt 注入与护栏选择。"""

    UNKNOWN = "unknown"
    FEATURE_DEV = "feature_dev"
    BUG_FIX = "bug_fix"
    REFACTOR = "refactor"
    OPTIMIZATION = "optimization"
    EXPLORATION = "exploration"
    EXPLANATION = "explanation"
    CODE_REVIEW = "code_review"
    DOCUMENTATION = "documentation"
    DEVOPS = "devops"
    TESTING = "testing"
    SECURITY = "security"


class ExpectedAction(str, Enum):
    """用户期望发生的具体动作 — 支持多重意图叠加。"""

    READ_FILES = "read_files"
    WRITE_CODE = "write_code"
    WRITE_TESTS = "write_tests"
    RUN_COMMANDS = "run_commands"
    SUMMARIZE = "summarize"
    PLAN = "plan"
    EXPLAIN = "explain"


class MutationScale(str, Enum):
    """预期修改规模 — 用于成本路由与模型选择。"""

    NONE = "none"
    MINOR = "minor"
    MODERATE = "moderate"
    MAJOR = "major"


class EnrichmentContext(BaseModel):
    """SLM 解析出的富上下文 — CognitiveGateway 的核心增值产物。

    当 SLM 可用时由 resolve_delivery_mode() 填充；
    regex fallback 时退化为默认值，不影响下游。
    """

    # 1. CoT 前置推理（强制模型先思考再分类，显著提升准确率）
    reasoning: str = Field(default="", description="SLM 对用户意图的简要分析（≤30字）")

    # 2. 业务定性
    task_category: TaskCategory = Field(default=TaskCategory.UNKNOWN)

    # 3. 多重意图分解
    expected_actions: list[ExpectedAction] = Field(default_factory=list)

    # 4. 爆炸半径与目标预提取
    explicit_targets: list[str] = Field(default_factory=list)

    # 5. 成本路由信号
    mutation_scale: MutationScale = Field(default=MutationScale.NONE)

    # 6. 安全与防御
    requires_confirmation: bool = Field(default=False)

    # 7. 元信息
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    is_negated: bool = Field(default=False)

    # 8. 向后兼容：保留原始意图标签
    raw_intent_label: str = Field(default="")

    # 9. Evidence-Based Verification: 覆盖 constants.py 默认验证 patterns
    #    用于自定义测试框架或特殊验证命令（如指定特定的 "BUILD SUCCESS" 字样）。
    #    三层叠加：constants.py 默认值 → 本字段覆盖 → hardcoded fallback。
    verification_patterns: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _coerce_enums(cls, values: Any) -> Any:
        """自动清洗 SLM 输出的毛刺：大小写混用、多余空格、未知枚举值、非列表类型。"""
        if not isinstance(values, dict):
            return values

        def _coerce_enum(raw: Any, enum_cls: type, fallback: Any) -> Any:
            if raw is None or isinstance(raw, enum_cls):
                return raw
            try:
                return enum_cls(str(raw).lower().strip().replace(" ", "_"))
            except ValueError:
                return fallback

        # task_category
        tc = values.get("task_category")
        if tc is not None:
            values["task_category"] = _coerce_enum(tc, TaskCategory, TaskCategory.UNKNOWN)

        # mutation_scale
        ms = values.get("mutation_scale")
        if ms is not None:
            values["mutation_scale"] = _coerce_enum(ms, MutationScale, MutationScale.NONE)

        # expected_actions — 防御 SLM 返回字符串而非列表（如 "write_code"）
        actions = values.get("expected_actions")
        if actions is not None:
            if isinstance(actions, str):
                actions = [actions]
            cleaned: list[ExpectedAction] = []
            for a in actions if isinstance(actions, (list, tuple)) else []:
                if isinstance(a, ExpectedAction):
                    cleaned.append(a)
                    continue
                coerced = _coerce_enum(a, ExpectedAction, None)
                if coerced is not None:
                    cleaned.append(coerced)
            values["expected_actions"] = cleaned

        # explicit_targets — 防御 SLM 返回字符串而非列表
        targets = values.get("explicit_targets")
        if targets is not None:
            if isinstance(targets, str):
                targets = [targets]
            values["explicit_targets"] = [str(t).strip() for t in targets if isinstance(t, str) and t]

        return values


@dataclass(frozen=True)
class DeliveryContract:
    """单次 Turn 的交付契约。

    由意图分类器根据用户请求解析，贯穿整个事务生命周期。
    """

    mode: DeliveryMode = DeliveryMode.ANALYZE_ONLY
    requires_mutation: bool = False
    requires_verification: bool = False
    allow_inline_code: bool = True
    allow_patch_proposal: bool = False

    # === 新增：SLM 富解析上下文（regex fallback 时为 None）===
    enrichment: EnrichmentContext | None = None

    @property
    def must_materialize(self) -> bool:
        """是否必须通过工具落盘。"""
        return self.mode == DeliveryMode.MATERIALIZE_CHANGES

    @property
    def may_propose_patch(self) -> bool:
        """是否允许以文本形式输出 patch 提案。"""
        return self.mode == DeliveryMode.PROPOSE_PATCH


@dataclass
class MutationObligationState:
    """Mutation 义务追踪 —— 记录 MATERIALIZE_CHANGES 任务的履约进度。

    本状态对象绑定到 TurnLedger，作为事务级审计轨迹的一部分。
    """

    target_files_known: bool = False
    read_evidence_count: int = 0
    write_attempted: bool = False
    authoritative_write_count: int = 0
    verification_attempted: bool = False
    verification_passed: bool = False
    blocked_reason: BlockedReason | None = None
    blocked_detail: str = ""
    inline_patch_rejected_count: int = 0

    @property
    def mutation_satisfied(self) -> bool:
        """是否已产生至少一个 authoritative write receipt。"""
        return self.authoritative_write_count > 0

    @property
    def ready_for_report(self) -> bool:
        """是否可以进入 REPORT（收口）阶段。"""
        if self.blocked_reason is not None:
            return True
        return self.mutation_satisfied and not (self.verification_attempted and not self.verification_passed)

    def record_read_receipt(self) -> None:
        """记录一次成功的读取证据。"""
        self.read_evidence_count += 1

    def record_write_receipt(self) -> None:
        """记录一次成功的 authoritative 写入。"""
        self.authoritative_write_count += 1
        self.write_attempted = True

    def record_write_attempt(self) -> None:
        """记录一次写入尝试（无论成败）。"""
        self.write_attempted = True

    def record_inline_patch_rejected(self) -> None:
        """记录一次贴代码逃逸被拦截。"""
        self.inline_patch_rejected_count += 1

    def mark_blocked(self, reason: BlockedReason, detail: str = "") -> None:
        """标记事务为 BLOCKED，提供合法退出路径。"""
        self.blocked_reason = reason
        self.blocked_detail = detail

    def to_audit_dict(self) -> dict:
        """转换为审计日志格式。"""
        return {
            "target_files_known": self.target_files_known,
            "read_evidence_count": self.read_evidence_count,
            "write_attempted": self.write_attempted,
            "authoritative_write_count": self.authoritative_write_count,
            "verification_attempted": self.verification_attempted,
            "verification_passed": self.verification_passed,
            "blocked_reason": self.blocked_reason.value if self.blocked_reason else None,
            "blocked_detail": self.blocked_detail,
            "inline_patch_rejected_count": self.inline_patch_rejected_count,
            "mutation_satisfied": self.mutation_satisfied,
            "ready_for_report": self.ready_for_report,
        }
