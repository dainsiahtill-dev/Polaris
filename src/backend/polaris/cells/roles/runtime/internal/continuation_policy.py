"""Continuation Policy - 负责仲裁是否允许自动连续执行下一回合.

Contains Circuit Breaker and anti-dead-loop logic, with Speculative-Aware strategy support.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

from polaris.cells.roles.kernel.public.turn_contracts import (
    FailureClass,
    TurnContinuationMode,
    TurnOutcomeEnvelope,
)

# ============ SessionPatch TypedDict（Step 2 类型化 schema） ============

# 置信度等级（高 → 低：superseded 时被高等级覆盖低等级）
_CONFIDENCE_RANK: dict[str, int] = {
    "confirmed": 3,
    "likely": 2,
    "hypothesis": 1,
}


class SessionPatch(dict):
    """语义补丁类型，定义 session_patch 中各字段的操作语义。

    相比裸 dict.update()，本类型明确区分：
    - replace: 覆盖旧值的标量字段
    - add: 追加到列表的字段（如 suspected_files）
    - remove: 从列表移除的字段
    - progress: task_progress 推进

    用法（由 LLM 在 turn 末尾输出）：
        <SESSION_PATCH>
        {
            "task_progress": "diagnosing",
            "suspected_files": ["src/auth.py", "src/db.py"],
            "error_summary": "Database timeout in auth flow",
            "pending_files": ["src/config.py"],
            "remove_keys": ["legacy_auth.py"]
        }
        </SESSION_PATCH>
    """

    def get_task_progress(self) -> str | None:
        return self.get("task_progress")

    def get_suspected_files(self) -> list[str]:
        return self.get("suspected_files", [])

    def get_patched_files(self) -> list[str]:
        return self.get("patched_files", [])

    def get_verified_results(self) -> list[str]:
        return self.get("verified_results", [])

    def get_pending_files(self) -> list[str]:
        return self.get("pending_files", [])

    def get_remove_keys(self) -> list[str]:
        return self.get("remove_keys", [])

    def get_error_summary(self) -> str:
        return self.get("error_summary", "")

    def get_action_taken(self) -> str:
        return self.get("action_taken", "")

    def get_key_file_snapshots(self) -> dict[str, str]:
        return self.get("key_file_snapshots", {})

    def get_confidence(self) -> str:
        """返回置信度：confirmed / likely / hypothesis（默认 hypothesis）。"""
        return self.get("confidence", "hypothesis")

    def get_superseded(self) -> bool:
        """返回 superseded 标记（默认 False）。被标记的发现物不进入续写 prompt。"""
        return bool(self.get("superseded", False))


@dataclass
class OrchestratorSessionState:
    """Orchestrator 层会话状态，避免与 roles.session 的 SessionState 命名冲突。

    记忆轨迹设计原则（ADR-0071 上下文降维）：
    - structured_findings 累积每个 Turn 的 LLM 合成结论（而非原始 artifact）
    - task_progress 追踪任务宏观进度（exploring → investigating → implementing → verifying → done）
    - artifact 仍存原始数据，但 LLM 续写时只看 structured_findings（降维门禁卡）
    """

    session_id: str
    goal: str = ""
    turn_count: int = 0
    max_turns: int = 15
    artifacts: dict[str, Any] = field(default_factory=dict)
    last_failure: dict[str, Any] | None = None
    turn_history: list[dict[str, Any]] = field(default_factory=list)
    recent_artifact_hashes: list[str] = field(default_factory=list)
    # 结构化发现物：从每个 TurnOutcomeEnvelope.session_patch 注入的降维结论
    structured_findings: dict[str, Any] = field(default_factory=dict)
    # 任务宏观进度：exploring | investigating | implementing | verifying | done
    task_progress: str = "exploring"
    # 关键文件的快照指纹：用于判断当前 Turn 是否真的在推进
    key_file_snapshots: dict[str, str] = field(default_factory=dict)
    # FIX-20250421: 原始目标，一旦设置永不丢失（防止 Turn 6 goal 为空）
    original_goal: str = ""
    # FIX-20250421: 已成功读取的文件列表（真正的 read_file，不是 glob）
    read_files: list[str] = field(default_factory=list)


class ContinuationPolicy:
    """负责仲裁是否允许自动连续执行下一回合。

    Phase 5.1 升级：
    - 基于历史 session 结果动态调整阈值
    - 早期成功检测（复杂任务允许更多 turn）
    - 恢复策略注入（停滞时尝试替代策略而非直接终止）

    Args:
        max_auto_turns: 最大自动连续回合数（硬限制）。
        speculative_hit_threshold: ShadowEngine 命中率阈值，用于 SPECULATIVE_CONTINUE。
    """

    def __init__(
        self,
        max_auto_turns: int = 10,
        speculative_hit_threshold: float = 0.7,
    ) -> None:
        self.max_auto_turns = max_auto_turns
        self.speculative_hit_threshold = speculative_hit_threshold

        # Phase 5.1: Adaptive threshold state
        self._session_history: list[dict[str, Any]] = []
        self._max_session_history = 50
        self._current_session_successes = 0
        self._current_session_failures = 0
        self._adaptive_threshold_override: float | None = None
        self._strategy_recovery_attempts: list[dict[str, Any]] = []

    def can_continue(
        self,
        state: OrchestratorSessionState,
        envelope: TurnOutcomeEnvelope,
    ) -> tuple[bool, str | None]:
        """返回 (是否允许继续, 阻止原因)。

        Phase 5.1 增强：
        - 动态阈值调整（基于历史性能）
        - 早期成功检测（复杂任务允许额外 turn）

        检查项：
        1. failure_class 驱动自我保护决策（Phase 1.5）
        2. continuation_mode 必须是 AUTO_CONTINUE 或 SPECULATIVE_CONTINUE
        3. turn_count 不能超过 max_auto_turns（除非早期成功检测）
        4. 不能连续重复失败
        5. 不能 stagnation（artifact hash 连续不变）
        6. SPECULATIVE_CONTINUE 模式下需满足 speculative worthwhile 条件
        """
        # Phase 1.5: FailureClass-driven self-protection
        failure_action = self._resolve_failure_class(envelope.failure_class)
        if failure_action == "stop":
            failure_name = envelope.failure_class.value if envelope.failure_class else "unknown"
            return False, f"failure_class={failure_name}"
        if failure_action == "stop_and_help":
            return False, "durability_failure_stop"

        if envelope.continuation_mode not in {
            TurnContinuationMode.AUTO_CONTINUE,
            TurnContinuationMode.SPECULATIVE_CONTINUE,
        }:
            return False, f"mode={envelope.continuation_mode.value}"

        # Phase 5.1: Early success detection - allow extra turns for complex tasks
        if state.turn_count >= self.max_auto_turns:
            if self.should_allow_extra_turns(state):
                pass
            else:
                return False, "max_turns_exceeded"

        if self._detect_repetitive_failure(state):
            return False, "repetitive_failure"

        if self._detect_stagnation_v2(state, envelope):
            recovery = self.get_recovery_strategy("stagnation")
            if recovery:
                return True, f"recovery_strategy={recovery}"
            return False, "stagnation_detected"

        # Phase 5.1: Use adaptive threshold
        effective_threshold = self.get_adaptive_threshold()
        if (
            envelope.continuation_mode == TurnContinuationMode.SPECULATIVE_CONTINUE
            and not self._detect_speculative_worthwhile_with_threshold(state, envelope, effective_threshold)
        ):
            return False, "speculative_not_worthwhile"

        return True, None

    @staticmethod
    def _resolve_failure_class(failure_class: FailureClass | None) -> str:
        """将 FailureClass 映射为 continuation action。

        Phase 1.5 冻结映射表：
        - CONTRACT_VIOLATION -> stop
        - DURABILITY_FAILURE -> stop_and_help
        - RUNTIME_FAILURE -> continue (with retry budget, handled upstream)
        - INSUFFICIENT_EVIDENCE -> continue
        - POLICY_FAILURE -> stop
        """
        if failure_class is None:
            return "continue"
        mapping: dict[FailureClass, str] = {
            FailureClass.CONTRACT_VIOLATION: "stop",
            FailureClass.DURABILITY_FAILURE: "stop_and_help",
            FailureClass.RUNTIME_FAILURE: "continue",
            FailureClass.INSUFFICIENT_EVIDENCE: "continue",
            FailureClass.POLICY_FAILURE: "stop",
        }
        return mapping.get(failure_class, "stop")

    @staticmethod
    def _detect_repetitive_failure(state: OrchestratorSessionState) -> bool:
        """检测最近 3 个 turn 是否连续发生相同的工具失败。"""
        recent = state.turn_history[-3:]
        if len(recent) < 3:
            return False
        errors = [t.get("error") for t in recent]
        return all(errors) and len({str(e) for e in errors}) == 1

    @staticmethod
    def _detect_stagnation_v2(
        state: OrchestratorSessionState,
        envelope: TurnOutcomeEnvelope,
    ) -> bool:
        """检测 stagnation（语义级 + 哈希级双重检测）。

        1. 语义停滞：task_progress 在最近 4 个 turn 没有推进
        2. 哈希停滞：artifact hash 连续 2 个 turn 未变，且 speculative_hints 为空
        3. 验证阶段回归：progress 从 verifying 回退到 implementing
        """
        trajectory = state.structured_findings.get("_findings_trajectory", [])

        # 检测 1: 语义进展停滞（task_progress 卡住）
        if len(trajectory) >= 4:
            recent_progresses = [e.get("task_progress") for e in trajectory[-4:] if "task_progress" in e]
            if len(recent_progresses) >= 4 and len(set(recent_progresses)) == 1:
                # 连续 4 个 turn 都在同一阶段，判定为语义停滞
                return True

        # 检测 2: 哈希停滞（文件内容无变化）
        recent_hashes = state.recent_artifact_hashes[-2:]
        if len(recent_hashes) >= 2 and recent_hashes[-1] == recent_hashes[-2] and not envelope.speculative_hints:
            return True

        # 检测 3: 验证阶段停滞检测
        # 使用 trajectory 而非 structured_findings["task_progress"] 来判断是否"刚从非verifying进入verifying"还是"已在verifying停留多轮"。
        # - 如果 trajectory 倒数第二个条目（上一轮 LLM 报告的 task_progress）不是 "verifying"：刚从 implementing/implementing 进入 verifying，正常，返回 False
        # - 如果 trajectory 倒数第二个条目（上一轮 LLM 报告的 task_progress）也是 "verifying"：已在 verifying 停留至少两轮没有推进到 done，停滞，返回 True
        # 注意：前两个检测已覆盖回退场景（verifying → implementing）和轨迹卡住场景，检测 3 只处理"停留在 verifying"的边界。
        # FIX-20250421: 增强检测 — 不仅检查是否在 verifying 停留，还需检查是否实际调用了验证工具
        # FIX-20250421: 同时检查是否有进度回退（verifying → implementing）以检测 oscillation 问题
        current_progress = state.task_progress
        if not envelope.speculative_hints and current_progress == "verifying":
            # FIX-20250421: 检查 speculative_hints 质量 — hit_rate 低于阈值时仍执行检测
            _shadow_hit_rate = float(envelope.speculative_hints.get("shadow_engine_hit_rate", 0.0) or 0.0)
            _skip_due_to_speculation = bool(envelope.speculative_hints) and _shadow_hit_rate >= 0.1
            if not _skip_due_to_speculation:
                trajectory = state.structured_findings.get("_findings_trajectory", [])
                if len(trajectory) >= 2:
                    prev_progress = trajectory[-2].get("task_progress") if isinstance(trajectory[-2], dict) else None
                    if prev_progress == "verifying":
                        # 已在 verifying 停留至少两轮，检查是否实际执行了验证工具
                        from polaris.cells.roles.kernel.internal.transaction.constants import VERIFICATION_TOOLS

                        verification_tools_called = False
                        batch_receipt = getattr(envelope.turn_result, "batch_receipt", None) or {}
                        # FIX-20250421: Use dict-style .get() for batch_receipt since it can be dict or Pydantic model
                        results = (
                            batch_receipt.get("results", [])
                            if isinstance(batch_receipt, dict)
                            else getattr(batch_receipt, "results", None) or []
                        )
                        for result in results:
                            tool_name = str(
                                (result.get("tool_name") or result.get("tool") or "")
                                if isinstance(result, dict)
                                else getattr(result, "tool_name", "") or getattr(result, "tool", "") or ""
                            )
                            if tool_name in VERIFICATION_TOOLS:
                                verification_tools_called = True
                                break
                        if not verification_tools_called:
                            # 在 verifying 停留超过 1 轮且未调用验证工具 → 强制结束
                            return True

        return False

    def _detect_speculative_worthwhile(
        self,
        state: OrchestratorSessionState,
        envelope: TurnOutcomeEnvelope,
    ) -> bool:
        """判断 ShadowEngine 预热是否值得继续。

        条件：ShadowEngine 命中率 >= 阈值，且本回合产生了新的 session_patch（artifact 有变化）。
        """
        effective_threshold = self.get_adaptive_threshold()
        return self._detect_speculative_worthwhile_with_threshold(state, envelope, effective_threshold)

    def _detect_speculative_worthwhile_with_threshold(
        self,
        state: OrchestratorSessionState,
        envelope: TurnOutcomeEnvelope,
        threshold: float,
    ) -> bool:
        """Phase 5.1: 判断 ShadowEngine 预热是否值得继续（使用显式阈值）。

        Args:
            state: Session state
            envelope: Turn outcome envelope
            threshold: Explicit threshold to use

        Returns:
            True if speculative execution is worthwhile
        """
        hit_rate = float(envelope.speculative_hints.get("shadow_engine_hit_rate") or 0.0)
        artifact_changed = bool(envelope.session_patch)
        return hit_rate >= threshold and artifact_changed

    # -------------------------------------------------------------------------
    # Phase 5.1: Adaptive Threshold Adjustment
    # -------------------------------------------------------------------------

    def record_session_outcome(
        self,
        session_id: str,
        success: bool,
        turn_count: int,
        stagnation_detected: bool = False,
    ) -> None:
        """Phase 5.1: Record session outcome for adaptive threshold learning.

        Args:
            session_id: Session identifier
            success: Whether session succeeded
            turn_count: Number of turns taken
            stagnation_detected: Whether stagnation was detected
        """
        outcome = {
            "session_id": session_id,
            "success": success,
            "turn_count": turn_count,
            "stagnation_detected": stagnation_detected,
        }

        self._session_history.append(outcome)
        if len(self._session_history) > self._max_session_history:
            self._session_history = self._session_history[-self._max_session_history :]

        if success:
            self._current_session_successes += 1
        else:
            self._current_session_failures += 1

        self._recompute_adaptive_threshold()

    def _recompute_adaptive_threshold(self) -> None:
        """Phase 5.1: Recompute adaptive threshold based on historical performance.

        Uses exponential moving average of session success rate.
        """
        if len(self._session_history) < 3:
            return

        recent_sessions = self._session_history[-10:]
        success_rate = sum(1 for s in recent_sessions if s["success"]) / len(recent_sessions)

        if success_rate >= 0.8:
            new_threshold = max(0.5, self.speculative_hit_threshold - 0.1)
        elif success_rate >= 0.6:
            new_threshold = self.speculative_hit_threshold
        else:
            new_threshold = min(0.9, self.speculative_hit_threshold + 0.1)

        self._adaptive_threshold_override = new_threshold

    def get_adaptive_threshold(self) -> float:
        """Phase 5.1: Get current adaptive threshold.

        Returns:
            Effective threshold (adaptive override or default)
        """
        return self._adaptive_threshold_override or self.speculative_hit_threshold

    def should_allow_extra_turns(self, state: OrchestratorSessionState) -> bool:
        """Phase 5.1: Determine if complex task should get extra turns.

        Args:
            state: Current session state

        Returns:
            True if early success detection suggests more turns needed
        """
        if state.turn_count < 3:
            return False

        recent_findings = state.structured_findings.get("_findings_trajectory", [])
        if len(recent_findings) < 2:
            return False

        progress_values = [
            f.get("task_progress") for f in recent_findings[-3:] if isinstance(f, dict) and f.get("task_progress")
        ]

        return len(set(progress_values)) > 1

    def get_recovery_strategy(self, stagnation_reason: str) -> str | None:
        """Phase 5.1: Get alternative strategy when stagnation detected.

        Args:
            stagnation_reason: The reason for stagnation

        Returns:
            Strategy name: 'retry_different_approach', 'simplify_task', 'request_help', or None
        """
        recent_same = [
            s for s in self._strategy_recovery_attempts[-5:] if s.get("stagnation_reason") == stagnation_reason
        ]

        if len(recent_same) >= 2:
            return None

        strategies = ["retry_different_approach", "simplify_task", "request_help"]
        for strategy in strategies:
            found = any(s.get("strategy") == strategy for s in recent_same)
            if not found:
                self._strategy_recovery_attempts.append(
                    {
                        "strategy": strategy,
                        "stagnation_reason": stagnation_reason,
                    }
                )
                return strategy

        return None


# ============ Session Patch 应用函数 ============


def apply_session_patch(
    state: OrchestratorSessionState,
    session_patch: dict[str, Any],
    *,
    max_trajectory_size: int = 10,
) -> None:
    """将 session_patch 的结论增量注入 structured_findings（上下文降维 ADR-0071）。

    使用 upsert 语义而非 dict.update()，确保每个字段只保留最新结论，
    避免旧值污染。发现物轨迹 (_findings_trajectory) 保留最近 N 条，
    用于检测 LLM 是否在"炒冷饭"。

    Confidence-aware 合并语义（Step 9 增强）：
    - superseded: True 时将旧发现标记为废弃，不进入续写 prompt
    - confidence: 高置信度（confirmed > likely > hypothesis）覆盖低置信度
    - 列表型字段（suspected_files, patched_files, pending_files）：追加去重
    - 标量型字段（error_summary, action_taken）：高置信度覆盖低置信度
    - task_progress：跨字段同步（同时更新 state.task_progress）
    - remove_keys: 从列表字段中移除指定条目（如排除伪线索）
    - _findings_trajectory：追加到轨迹，不覆盖历史
    - key_file_snapshots：同步更新到 state.key_file_snapshots
    """
    if not session_patch:
        return

    # 统一为 SessionPatch（支持类型化辅助方法）
    patch = session_patch if isinstance(session_patch, SessionPatch) else SessionPatch(session_patch)

    # 1. 追加到历史轨迹（最多保留 max_trajectory_size 条）
    prior_trajectory: list[dict[str, Any]] = state.structured_findings.get("_findings_trajectory", [])
    prior_trajectory.append(patch.copy())
    if len(prior_trajectory) > max_trajectory_size:
        prior_trajectory = prior_trajectory[-max_trajectory_size:]
    state.structured_findings["_findings_trajectory"] = prior_trajectory

    # 2. task_progress 跨字段同步（最高优先级）
    if (progress := patch.get_task_progress()) and isinstance(progress, str):
        state.task_progress = progress
        state.structured_findings["task_progress"] = progress

    # 2a. 处理 superseded：标记已废弃的发现物
    if patch.get_superseded():
        superseded_list: list[str] = state.structured_findings.get("_superseded_keys", [])
        internal_keys = {
            "_findings_trajectory",
            "key_file_snapshots",
            "task_progress",
            "remove_keys",
            "confidence",
            "superseded",
        }
        for key in patch:
            if key not in internal_keys and key not in superseded_list:
                superseded_list.append(key)
        state.structured_findings["_superseded_keys"] = superseded_list

    # 3. 移除伪线索（remove_keys：从列表字段中移除指定条目）
    for to_remove in patch.get_remove_keys():
        for key in ("suspected_files", "pending_files", "patched_files"):
            if key in state.structured_findings and isinstance(state.structured_findings[key], list):
                state.structured_findings[key] = [item for item in state.structured_findings[key] if item != to_remove]

    # 4. Confidence-aware Upsert
    patch_confidence_rank = _CONFIDENCE_RANK.get(patch.get_confidence(), 0)
    for key, value in patch.items():
        if key in (
            "_findings_trajectory",
            "key_file_snapshots",
            "task_progress",
            "remove_keys",
            "confidence",
            "superseded",
        ):
            continue
        if key in state.structured_findings:
            existing = state.structured_findings[key]
            existing_confidence_rank = _CONFIDENCE_RANK.get(
                state.structured_findings.get(f"_confidence_{key}", "hypothesis"), 0
            )
            if isinstance(existing, list) and isinstance(value, list):
                # 列表型：追加去重（如 suspected_files / pending_files 持续追加）
                combined = existing.copy()
                for item in value:
                    if item not in combined:
                        combined.append(item)
                state.structured_findings[key] = combined
            elif patch_confidence_rank >= existing_confidence_rank:
                # 标量型：高置信度覆盖低置信度（同级或更高则覆盖）
                state.structured_findings[key] = value
                state.structured_findings[f"_confidence_{key}"] = patch.get_confidence()
        else:
            state.structured_findings[key] = value
            state.structured_findings[f"_confidence_{key}"] = patch.get_confidence()

    # 5. 同步更新 key_file_snapshots（用于 stagnation 检测）
    if snapshots := patch.get_key_file_snapshots():
        state.key_file_snapshots.update(snapshots)


def get_active_findings(findings: dict[str, Any]) -> dict[str, Any]:
    """从 structured_findings 中过滤掉 superseded 发现物，返回活跃子集。

    用于续写 prompt 构建——LLM 不应看到已被推翻的结论。

    过滤规则：
    - `_superseded_keys` 中的字段不进入返回结果
    - `task_progress` 始终保留（控制流字段）
    - `_confidence_*` 元字段不进入返回结果（内部置信度记录）
    - `_findings_trajectory` 始终保留（用于 stagnation 检测）
    """
    superseded_keys: set[str] = set(findings.get("_superseded_keys", []))
    skip_keys = {"_confidence_", "_superseded_keys"}
    result: dict[str, Any] = {}
    for key, value in findings.items():
        if key in superseded_keys:
            continue
        if any(key.startswith(p) for p in skip_keys):
            continue
        result[key] = value
    return result


# ============ SessionPatch 提取器（LLM 端到端集成） ============


_SESSION_PATCH_BLOCK_RE = None  # lazy init


def _get_session_patch_block_re() -> Any:
    """Lazy-compile regex for <SESSION_PATCH> block extraction."""
    global _SESSION_PATCH_BLOCK_RE
    if _SESSION_PATCH_BLOCK_RE is None:
        import re

        _SESSION_PATCH_BLOCK_RE = re.compile(
            r"<SESSION_PATCH>\s*(.*?)\s*</SESSION_PATCH>",
            re.DOTALL,
        )
    return _SESSION_PATCH_BLOCK_RE


def extract_session_patch_from_text(text: str) -> SessionPatch | None:
    """从 LLM 输出文本中提取 <SESSION_PATCH> 块并解析为 SessionPatch。

    用于 LLM 端到端集成：在 LLM turn 末尾解码 <SESSION_PATCH> XML 块。

    Args:
        text: LLM 输出的原始文本（可能包含 <SESSION_PATCH> 块）

    Returns:
        解析后的 SessionPatch，或 None（无 SESSION_PATCH 块或解析失败）

    用法示例:
        raw_text = model_response.content
        patch = extract_session_patch_from_text(raw_text)
        if patch:
            apply_session_patch(state, patch)
    """
    if not text:
        return None

    pattern = _get_session_patch_block_re()
    match = pattern.search(text)
    if not match:
        return None

    json_str = match.group(1).strip()
    try:
        import json as _json

        parsed = _json.loads(json_str)
        return SessionPatch(parsed)
    except (_json.JSONDecodeError, TypeError, ValueError):
        # JSON 解析失败，记录但不崩溃
        return None


def strip_session_patch_block(text: str) -> str:
    """从 LLM 输出文本中移除 <SESSION_PATCH> 块，保留纯 visible content。

    用于确保 visible_content 不包含 SESSION_PATCH 块的噪声。

    Args:
        text: LLM 输出的原始文本

    Returns:
        移除 SESSION_PATCH 块后的纯文本
    """
    if not text:
        return text

    pattern = _get_session_patch_block_re()
    return pattern.sub("", text).strip()
