"""ModificationContract — 修改契约与认知就绪评估。

FIX-20250422-v3: 将机械式阶段超时替换为意图驱动的就绪检测。

核心思想：
- LLM 在 CONTENT_GATHERED 阶段必须先声明修改计划（modification_plan），
  系统才会强制要求写工具。
- 如果 LLM 始终不输出计划，系统降级到现有的 phase timeout 行为。
- ModificationContract 是 CONTENT_GATHERED 的子状态，不改变 Phase 枚举。

数据流：
  SESSION_PATCH.modification_plan → ModificationContract.update_from_session_patch()
  → evaluate_modification_readiness() → tool_batch_executor pre-execution guard
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class ModificationContractStatus(str, Enum):
    """修改契约状态。"""

    EMPTY = "empty"  # 未声明计划
    DRAFT = "draft"  # 部分计划（有 target_files 但缺少完整 action）
    READY = "ready"  # 完整计划（target_files + modifications）


class ReadinessVerdict(str, Enum):
    """认知就绪裁决。"""

    READY_TO_WRITE = "ready_to_write"  # 契约就绪，可以强制写
    NEEDS_PLAN = "needs_plan"  # 契约不足，允许读但注入规划指令


@dataclass
class ModificationIntent:
    """单个修改意图。"""

    target_file: str  # e.g., "session_orchestrator.py"
    action: str  # e.g., "add error handling to connect()"
    confidence: str = "hypothesis"  # hypothesis | likely | confirmed


@dataclass
class ModificationContract:
    """修改契约 — CONTENT_GATHERED 阶段的认知子状态。

    通过 SESSION_PATCH 中的 modification_plan 字段提取 LLM 的修改计划，
    作为 tool_batch_executor 就绪判断的依据。
    """

    status: ModificationContractStatus = ModificationContractStatus.EMPTY
    target_files: list[str] = field(default_factory=list)
    modifications: list[ModificationIntent] = field(default_factory=list)
    rationale: str = ""
    declared_at_turn: int = 0
    last_updated_at_turn: int = 0

    def update_from_session_patch(self, patch: dict[str, Any], current_turn: int) -> None:
        """从 SESSION_PATCH 提取 modification_plan 并更新契约状态。

        SESSION_PATCH 格式::

            {
                "modification_plan": [
                    {"target_file": "path/to/file.py", "action": "具体修改描述"},
                    ...
                ]
            }

        状态转换规则：
        - 有 target_file 但无 action → DRAFT
        - 有 target_file + action → READY
        - 空或无效 → 保持当前状态
        """
        plan_items = patch.get("modification_plan")
        if not plan_items or not isinstance(plan_items, list):
            return

        new_targets: list[str] = []
        new_modifications: list[ModificationIntent] = []

        for item in plan_items:
            if not isinstance(item, dict):
                continue
            target = str(item.get("target_file", "")).strip()
            if not target:
                continue
            if target not in new_targets:
                new_targets.append(target)
            action = str(item.get("action", "")).strip()
            confidence = str(item.get("confidence", "hypothesis")).strip()
            if confidence not in {"hypothesis", "likely", "confirmed"}:
                confidence = "hypothesis"
            new_modifications.append(
                ModificationIntent(
                    target_file=target,
                    action=action,
                    confidence=confidence,
                )
            )

        if not new_targets:
            return

        # 合并而非替换：追加新 target 和 modification
        for t in new_targets:
            if t not in self.target_files:
                self.target_files.append(t)

        existing_keys = {(m.target_file, m.action) for m in self.modifications}
        for m in new_modifications:
            if (m.target_file, m.action) not in existing_keys:
                self.modifications.append(m)
                existing_keys.add((m.target_file, m.action))

        # 状态提升
        has_targets = bool(self.target_files)
        has_actions = any(m.action for m in self.modifications)

        if has_targets and has_actions:
            new_status = ModificationContractStatus.READY
        elif has_targets:
            new_status = ModificationContractStatus.DRAFT
        else:
            new_status = self.status

        # 只能升级不能降级（READY 不回退到 DRAFT）
        _rank = {
            ModificationContractStatus.EMPTY: 0,
            ModificationContractStatus.DRAFT: 1,
            ModificationContractStatus.READY: 2,
        }
        if _rank[new_status] > _rank[self.status]:
            self.status = new_status

        if self.declared_at_turn == 0:
            self.declared_at_turn = current_turn
        self.last_updated_at_turn = current_turn

        logger.info(
            "modification_contract_updated: status=%s targets=%d modifications=%d turn=%d",
            self.status.value,
            len(self.target_files),
            len(self.modifications),
            current_turn,
        )

    def to_dict(self) -> dict[str, Any]:
        """序列化为 checkpoint 可存储的 dict。"""
        return {
            "status": self.status.value,
            "target_files": list(self.target_files),
            "modifications": [
                {
                    "target_file": m.target_file,
                    "action": m.action,
                    "confidence": m.confidence,
                }
                for m in self.modifications
            ],
            "rationale": self.rationale,
            "declared_at_turn": self.declared_at_turn,
            "last_updated_at_turn": self.last_updated_at_turn,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ModificationContract:
        """从 checkpoint dict 反序列化。缺失字段安全回退到 EMPTY。"""
        if not data or not isinstance(data, dict):
            return cls()

        try:
            status = ModificationContractStatus(data.get("status", "empty"))
        except ValueError:
            status = ModificationContractStatus.EMPTY

        modifications: list[ModificationIntent] = []
        raw_mods = data.get("modifications", [])
        if isinstance(raw_mods, list):
            for item in raw_mods:
                if isinstance(item, dict):
                    modifications.append(
                        ModificationIntent(
                            target_file=str(item.get("target_file", "")),
                            action=str(item.get("action", "")),
                            confidence=str(item.get("confidence", "hypothesis")),
                        )
                    )

        target_files = data.get("target_files", [])
        if not isinstance(target_files, list):
            target_files = []

        return cls(
            status=status,
            target_files=[str(t) for t in target_files],
            modifications=modifications,
            rationale=str(data.get("rationale", "")),
            declared_at_turn=int(data.get("declared_at_turn", 0)),
            last_updated_at_turn=int(data.get("last_updated_at_turn", 0)),
        )

    def format_for_prompt(self) -> str:
        """生成可注入 continuation prompt 的契约摘要。"""
        if self.status == ModificationContractStatus.EMPTY:
            return "Modification Plan: [EMPTY] — 尚未声明修改计划"
        lines = [f"Modification Plan: [{self.status.value.upper()}]"]
        for m in self.modifications:
            action_display = m.action if m.action else "(pending)"
            lines.append(f"  - {m.target_file}: {action_display}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# ReadinessEvaluator — 纯函数，零依赖
# ---------------------------------------------------------------------------


# SUPER_MODE 标记，用于检测 CLI SUPER 模式注入的指令
_SUPER_MODE_MARKERS: tuple[str, ...] = (
    "[SUPER_MODE_HANDOFF]",
    "[/SUPER_MODE_HANDOFF]",
    "[SUPER_MODE_DIRECTOR_CONTINUE]",
    "[/SUPER_MODE_DIRECTOR_CONTINUE]",
)


def _conversation_has_super_mode_markers(context: list[dict[str, Any]] | None) -> bool:
    """检测对话上下文中是否包含 SUPER_MODE 标记。

    CLI SUPER 模式通过 [SUPER_MODE_HANDOFF] 和 [SUPER_MODE_DIRECTOR_CONTINUE]
    标记向 Director 传递"立即执行，无需计划"的指令。当这些标记存在时，
    ModificationContract 的 plan 要求应当被绕过。
    """
    if not context:
        return False
    for message in context:
        if not isinstance(message, dict):
            continue
        content = str(message.get("content", ""))
        for marker in _SUPER_MODE_MARKERS:
            if marker in content:
                return True
    return False


def evaluate_modification_readiness(
    contract: ModificationContract,
    phase_value: str,
    delivery_mode_value: str,
    turns_in_phase: int,
    max_turns_per_phase: int,
    conversation_context: list[dict[str, Any]] | None = None,
) -> ReadinessVerdict:
    """判断 LLM 是否已准备好执行写操作。

    使用字符串值（而非枚举）避免跨模块循环导入。

    规则：
    1. SUPER_MODE 模式 → READY_TO_WRITE（CLI SUPER 模式已提供完整计划）
    2. 非 MATERIALIZE_CHANGES → READY_TO_WRITE（不设门禁）
    3. 非 CONTENT_GATHERED → READY_TO_WRITE（门禁仅在此阶段生效）
    4. contract.status == READY → READY_TO_WRITE
    5. contract.status == DRAFT 且有 target_files + 非空 action → 自动提升为 READY
    6. 否则 → NEEDS_PLAN
    """
    # Rule 1: SUPER_MODE 绕过 — CLI SUPER 模式通过 PM 已生成完整计划，
    # Director 的唯一职责是执行，不应被 plan 门禁阻塞。
    if _conversation_has_super_mode_markers(conversation_context):
        logger.debug("modification_readiness: SUPER_MODE bypass — READY_TO_WRITE")
        return ReadinessVerdict.READY_TO_WRITE

    # Rule 2: 非 MATERIALIZE_CHANGES 模式不设门禁
    if delivery_mode_value != "materialize_changes":
        return ReadinessVerdict.READY_TO_WRITE

    # Rule 3: 非 CONTENT_GATHERED 阶段不设门禁
    if phase_value != "content_gathered":
        return ReadinessVerdict.READY_TO_WRITE

    # Rule 4: 契约已就绪
    if contract.status == ModificationContractStatus.READY:
        return ReadinessVerdict.READY_TO_WRITE

    # Rule 5: DRAFT 自动提升检查
    if contract.status == ModificationContractStatus.DRAFT:
        has_targets = bool(contract.target_files)
        has_actions = any(m.action for m in contract.modifications)
        if has_targets and has_actions:
            contract.status = ModificationContractStatus.READY
            logger.info("modification_contract_auto_promoted: DRAFT -> READY")
            return ReadinessVerdict.READY_TO_WRITE

    # Rule 6: 契约不足
    return ReadinessVerdict.NEEDS_PLAN
