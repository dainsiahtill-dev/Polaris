"""ProjectionEngine - generates LLM-ready prompt projections."""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Iterable, Mapping

from polaris.kernelone.context.context_os.helpers import get_metadata_value
from polaris.kernelone.context.control_plane_noise import (
    is_signal_role,
    strip_control_plane_markers,
)

if TYPE_CHECKING:
    from polaris.kernelone.context.receipt_store import ReceiptStore

logger = logging.getLogger(__name__)

_RECEIPT_REF_SAFE_CHARS = re.compile(r"[^A-Za-z0-9_.-]+")


@dataclass
class _AdaptiveWeights:
    """Adaptive weights for projection scoring, adjusted based on historical outcomes."""

    route_weight: float = 0.30
    confidence_weight: float = 0.25
    recency_weight: float = 0.20
    dialog_act_weight: float = 0.15
    role_priority_weight: float = 0.10

    # Learning rate for weight adjustment
    _learning_rate: float = 0.05

    def adjust(self, route_score: float, confidence_score: float, recency_score: float) -> None:
        """Adjust weights based on outcome quality scores.

        Args:
            route_score: How well the route matched success (0-1)
            confidence_score: How well confidence predicted success (0-1)
            recency_score: How well recency predicted success (0-1)
        """
        # Normalize scores to delta space
        total = route_score + confidence_score + recency_score + 0.001
        route_delta = (route_score / total - self.route_weight) * self._learning_rate
        conf_delta = (confidence_score / total - self.confidence_weight) * self._learning_rate
        rec_delta = (recency_score / total - self.recency_weight) * self._learning_rate

        self.route_weight = max(0.05, min(0.6, self.route_weight + route_delta))
        self.confidence_weight = max(0.05, min(0.6, self.confidence_weight + conf_delta))
        self.recency_weight = max(0.05, min(0.5, self.recency_weight + rec_delta))

        # Normalize so weights sum to 1.0
        total_weight = (
            self.route_weight
            + self.confidence_weight
            + self.recency_weight
            + self.dialog_act_weight
            + self.role_priority_weight
        )
        self.route_weight /= total_weight
        self.confidence_weight /= total_weight
        self.recency_weight /= total_weight
        self.dialog_act_weight /= total_weight
        self.role_priority_weight /= total_weight


@dataclass
class _ProjectionOutcome:
    """Tracks projection outcome for adaptive weight learning."""

    projection_id: str
    timestamp: datetime
    success: bool
    route_score: float  # Did the selected route contribute to success?
    confidence_score: float  # Did confidence correlate with success?
    recency_score: float  # Did recency ordering contribute to success?
    tokens_used: int


# Role priority for task-relevant ordering
_ROLE_PRIORITY = {
    "system": 4,
    "assistant": 3,
    "user": 2,
    "tool": 1,
}


@dataclass
class _AdaptiveState:
    """跨 turn 持久的自适应学习状态（权重 + outcomes 滚动窗口）。"""

    weights: _AdaptiveWeights
    outcomes: list[_ProjectionOutcome]


# 模块级状态存储（与 RoleSignalFreshnessCache 同一模板）：解开 ProjectionEngine
# "每 turn 新建 → 学习状态清零"的架构阻断。按 learning_key（通常是角色）累积，
# 使权重与 outcomes 窗口跨 turn 存活。
_ADAPTIVE_STATE_STORE: dict[str, _AdaptiveState] = {}


def adaptive_ordering_enabled() -> bool:
    """自适应排序总开关。默认开（与当前线上行为一致）；置 0/false 退回纯时序排序。

    供真实 LLM A/B 评测（ON vs OFF 比任务效果）与紧急回滚使用。
    env: ``ENABLE_PROJECTION_ADAPTIVE_ORDERING``。
    """
    raw = os.environ.get("ENABLE_PROJECTION_ADAPTIVE_ORDERING")
    if raw is None:
        return True
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _get_adaptive_state(learning_key: str) -> _AdaptiveState:
    key = learning_key or "default"
    state = _ADAPTIVE_STATE_STORE.get(key)
    if state is None:
        state = _AdaptiveState(weights=_AdaptiveWeights(), outcomes=[])
        _ADAPTIVE_STATE_STORE[key] = state
    return state


def reset_projection_adaptive_state() -> None:
    """测试用：清空跨 turn 自适应状态。"""
    _ADAPTIVE_STATE_STORE.clear()


class ProjectionEngine:
    """Generate prompt-safe messages from ContextOS projections.

    Design constraints:
    - Read-only: never mutates TruthLog or WorkingState.
    - Receipt-aware: large outputs are referenced, not inlined.
    - Control-plane noise is excluded at both payload and turn level.
    - Adaptive: weights adjust based on projection outcome quality.
    """

    _CONTROL_PLANE_KEYS = frozenset(
        {
            "budget_status",
            "metrics",
            "policy_verdict",
            "system_warnings",
            "telemetry",
            "telemetry_events",
        }
    )
    _TURN_BLOCKED_KEYS = frozenset(
        {
            "budget_status",
            "metrics",
            "policy_verdict",
            "raw_output",
            "system_warnings",
            "telemetry",
            "telemetry_events",
            "thinking",
            "thinking_content",
        }
    )
    _HIGH_PRIORITY_DIALOG_ACTS = frozenset({"affirm", "clarify", "commit", "deny", "pause", "redirect", "cancel"})
    _ROUTE_PRIORITY = {
        "patch": 3,
        "summarize": 2,
        "archive": 1,
        "clear": 0,
    }

    def __init__(self, learning_key: str = "default") -> None:
        """Initialize ProjectionEngine with adaptive learning state.

        ``learning_key`` 选择跨 turn 持久的自适应状态桶（通常传角色），使权重与
        outcomes 窗口跨 turn 累积（解开"每 turn 新建即清零"的阻断）。默认 "default"
        保持所有现有构造点行为不变（共享同一桶）。
        """
        self._state = _get_adaptive_state(learning_key)
        # 引用共享状态：权重在 adjust 中原地变更、outcomes 原地增删 → 跨 turn 存活。
        self._weights = self._state.weights
        self._outcomes: list[_ProjectionOutcome] = self._state.outcomes
        self._max_outcomes: int = 100  # Rolling window for weight learning
        self._projection_count: int = 0
        # 记住最近一次实际投影所用的事件，供 record_outcome 计算真实质量分。
        # （修复历史缺陷：record_outcome 原先喂入常量 0.5，使 _compute_projection_quality
        #  形同虚设、自适应学习恒为空转。）
        self._last_projected_events: list[Any] = []

    @staticmethod
    def _as_mapping(value: Any) -> dict[str, Any]:
        if isinstance(value, Mapping):
            return dict(value)
        if isinstance(value, tuple):
            return dict(value)
        return {}

    def _strip_control_plane_noise(self, projection: dict[str, Any]) -> dict[str, Any]:
        stripped: dict[str, Any] = {}
        for key, value in projection.items():
            if key in self._CONTROL_PLANE_KEYS:
                logger.warning("Stripping control-plane noise from projection: %s", key)
                continue
            stripped[key] = value
        return stripped

    def _sanitize_metadata(self, metadata: Any) -> dict[str, Any]:
        raw = self._as_mapping(metadata)
        return {key: value for key, value in raw.items() if key not in self._TURN_BLOCKED_KEYS}

    @staticmethod
    def _sanitize_receipt_ref(ref: Any) -> str:
        ref_text = str(ref or "").strip()
        if not ref_text:
            return ""
        safe_ref = _RECEIPT_REF_SAFE_CHARS.sub("_", ref_text).strip("_")
        return safe_ref[:128]

    @staticmethod
    def _clean_turn_content(role: str, content: str) -> str:
        """对非信号角色(tool/system)内容剥离框架性控制面标记。

        兑现类 docstring "control-plane noise excluded at turn level" 的承诺:工具/系统
        内容里泄漏的 ``<tool_result>`` 包裹标签、``[system warning]`` 等整行会被清掉,而
        信号角色(user/assistant)的内容——模型与用户的真实话语——保持原样不动。
        """
        if is_signal_role(role):
            return content
        return strip_control_plane_markers(content)

    def _normalize_turn(self, turn: Mapping[str, Any], receipt_store: ReceiptStore) -> dict[str, Any] | None:
        role = str(turn.get("role") or "").strip()
        content = self._clean_turn_content(role, str(turn.get("content") or ""))
        if not role:
            return None

        result: dict[str, Any] = {"role": role, "content": content}

        receipt_refs = turn.get("receipt_refs")
        if isinstance(receipt_refs, (list, tuple)) and receipt_store is not None:
            safe_refs: list[str] = []
            for ref in receipt_refs:
                ref_text = self._sanitize_receipt_ref(ref)
                if ref_text:
                    safe_refs.append(ref_text)
            if safe_refs:
                refs_text = "\n".join(f"[receipt_ref:{ref}]" for ref in safe_refs)
                result["content"] = content + "\n\n" + refs_text
                result["receipt_refs"] = safe_refs

        metadata = self._sanitize_metadata(turn.get("metadata"))
        if metadata:
            result["metadata"] = metadata

        for passthrough_key in ("name", "tool_call_id"):
            value = turn.get(passthrough_key)
            if value not in (None, ""):
                result[passthrough_key] = value

        return result

    def _dialog_act_priority(self, metadata: Any) -> int:
        act = str(get_metadata_value(metadata, "dialog_act", "")).lower()
        if act in self._HIGH_PRIORITY_DIALOG_ACTS:
            return 2
        return 0

    def _role_priority(self, role: str) -> int:
        """Get role priority for ordering (higher = more important)."""
        return _ROLE_PRIORITY.get(role.lower(), 0)

    def sort_events(self, active_window: Iterable[Any]) -> list[Any]:
        events = list(active_window)
        # 记录本次投影的事件，供后续 record_outcome 计算真实质量分。
        self._last_projected_events = events
        if not events:
            return []

        # A/B 总开关：关闭自适应排序时退回纯时序（sequence 升序），用于 ON/OFF 对照评测与回滚。
        if not adaptive_ordering_enabled():
            return sorted(events, key=lambda e: int(getattr(e, "sequence", 0)))

        # Sequence ceiling is invariant across the window; compute it once here
        # instead of re-deriving it inside the per-element sort key (was O(n^2)).
        max_seq = max((int(getattr(e, "sequence", 0)) for e in events), default=1)

        # Compute adaptive priority key using current weights.  Sequence remains
        # the stable tie-breaker, but it is no longer the primary key; otherwise
        # learned route/confidence/dialog weights can never affect real prompts
        # when transcript sequence values are unique.
        def event_priority_key(event: Any) -> tuple[float, int]:
            sequence = int(getattr(event, "sequence", 0))
            route = str(getattr(event, "route", "clear") or "clear").lower()
            metadata = getattr(event, "metadata", ())
            confidence = float(get_metadata_value(metadata, "routing_confidence", 0.5))
            dialog_act_score = min(1.0, self._dialog_act_priority(metadata) / 2.0)
            role = str(getattr(event, "role", "user") or "user")
            role_prio = self._role_priority(role)
            role_score = role_prio / max(1, max(_ROLE_PRIORITY.values()))

            # Route score (higher route priority = more signal)
            route_score = float(self._ROUTE_PRIORITY.get(route, 0)) / 3.0

            # Recency score (normalized to 0-1 based on position)
            recency_score = sequence / max(1, max_seq) if max_seq > 0 else 0.0

            combined_confidence = min(1.0, confidence)

            # Patch/code-turns keep chronological stability when their semantic
            # scores tie. Non-patch context can use adaptive recency/role boost
            # to surface fresh supporting context without destabilizing equal
            # patch events.
            recency_component = (
                0.0 if route == "patch" else self._weights.recency_weight * recency_score * (1.0 - route_score)
            )
            role_component = 0.0 if route == "patch" else self._weights.role_priority_weight * role_score

            priority_score = (
                self._weights.route_weight * route_score
                + self._weights.confidence_weight * combined_confidence
                + recency_component
                + self._weights.dialog_act_weight * dialog_act_score
                + role_component
            )
            return (-priority_score, sequence)

        return sorted(events, key=event_priority_key)

    def _compute_projection_quality(
        self,
        events: list[Any],
        outcome: bool,
    ) -> tuple[float, float, float]:
        """Compute quality scores for weight adjustment from projected events.

        Returns:
            Tuple of (route_score, confidence_score, recency_score)
        """
        if not events:
            return (0.5, 0.5, 0.5)

        route_scores: list[float] = []
        confidence_scores: list[float] = []

        for event in events:
            route = str(getattr(event, "route", "clear") or "clear").lower()
            metadata = getattr(event, "metadata", ())
            confidence = float(get_metadata_value(metadata, "routing_confidence", 0.5))

            route_scores.append(float(self._ROUTE_PRIORITY.get(route, 0)) / 3.0)
            confidence_scores.append(confidence)

        avg_route = sum(route_scores) / len(route_scores) if route_scores else 0.5
        avg_conf = sum(confidence_scores) / len(confidence_scores) if confidence_scores else 0.5

        # Recency: favor more recent events for successful outcomes
        max_seq = max((int(getattr(e, "sequence", 0)) for e in events), default=1)
        latest_seq = max((int(getattr(e, "sequence", 0)) for e in events), default=0)
        recency_score = (latest_seq / max(1, max_seq)) if max_seq > 0 else 0.5

        # If outcome was successful, high route/confidence scores are good signals
        # If outcome was failure, low route/confidence scores indicate noise
        if outcome:
            route_signal = avg_route  # High route priority contributed
            conf_signal = avg_conf  # High confidence predicted well
        else:
            route_signal = 1.0 - avg_route  # High route might have been wrong
            conf_signal = 1.0 - avg_conf  # Over-confidence might have contributed

        return (route_signal, conf_signal, recency_score)

    def record_outcome(self, success: bool, tokens_used: int = 0) -> None:
        """Record projection outcome for adaptive weight learning.

        Args:
            success: Whether the projection led to successful task completion
            tokens_used: Token budget consumed for this projection
        """
        self._projection_count += 1
        # 从最近一次实际投影的事件计算真实质量分（修复：原先硬编码 0.5 导致学习空转）。
        route_score, confidence_score, recency_score = self._compute_projection_quality(
            self._last_projected_events, success
        )
        outcome = _ProjectionOutcome(
            projection_id=f"proj_{self._projection_count}",
            timestamp=datetime.now(timezone.utc),
            success=success,
            route_score=route_score,
            confidence_score=confidence_score,
            recency_score=recency_score,
            tokens_used=tokens_used,
        )
        self._outcomes.append(outcome)

        # Rolling window: keep last _max_outcomes（**原地**截断，保持与共享状态同引用）。
        if len(self._outcomes) > self._max_outcomes:
            del self._outcomes[: -self._max_outcomes]

        # Adjust weights if we have enough data (at least 5 outcomes)
        if len(self._outcomes) >= 5:
            recent = self._outcomes[-5:]
            avg_route = sum(o.route_score for o in recent) / 5
            avg_conf = sum(o.confidence_score for o in recent) / 5
            avg_rec = sum(o.recency_score for o in recent) / 5
            self._weights.adjust(avg_route, avg_conf, avg_rec)
            logger.debug(
                "ProjectionEngine weights adjusted: route=%.3f conf=%.3f recency=%.3f",
                self._weights.route_weight,
                self._weights.confidence_weight,
                self._weights.recency_weight,
            )

    def get_adaptive_weights(self) -> dict[str, float]:
        """Get current adaptive weights for debugging/inspection."""
        return {
            "route_weight": self._weights.route_weight,
            "confidence_weight": self._weights.confidence_weight,
            "recency_weight": self._weights.recency_weight,
            "dialog_act_weight": self._weights.dialog_act_weight,
            "role_priority_weight": self._weights.role_priority_weight,
        }

    def build_turns(self, active_window: Iterable[Any], receipt_store: ReceiptStore) -> list[dict[str, Any]]:
        events = list(active_window)
        sorted_events = self.sort_events(events)
        if not sorted_events:
            return []

        turns: list[dict[str, Any]] = []
        latest_sequence = max((int(getattr(event, "sequence", 0)) for event in events), default=0)

        for index, event in enumerate(sorted_events):
            route = str(getattr(event, "route", "clear") or "clear").lower()
            metadata = getattr(event, "metadata", ())
            if route == "clear":
                is_forced = bool(get_metadata_value(metadata, "reopen_hold")) if metadata else False
                is_recent = int(getattr(event, "sequence", 0)) >= latest_sequence - 3
                if not is_forced and not is_recent:
                    continue

            role = str(getattr(event, "role", "user") or "user")
            content = self._clean_turn_content(role, str(getattr(event, "content", "") or ""))
            event_id = self._sanitize_receipt_ref(getattr(event, "event_id", "")) or f"idx_{index}"

            if route == "archive":
                artifact_id = self._sanitize_receipt_ref(getattr(event, "artifact_id", "")) or event_id
                is_recent = int(getattr(event, "sequence", 0)) >= latest_sequence - 3
                content = content if is_recent else f"[Artifact stored: {artifact_id}]"

            if role == "tool":
                content, receipt_refs = receipt_store.offload_content(
                    f"tool_{event_id}",
                    content,
                    threshold=500,
                    placeholder=f"[Large output stored in receipt tool_{event_id}]",
                )
            else:
                content, receipt_refs = receipt_store.offload_content(
                    f"evt_{event_id}",
                    content,
                    threshold=2000,
                    placeholder=f"[Large content stored in receipt evt_{event_id}]",
                )

            turn: dict[str, Any] = {"role": role, "content": content}
            if receipt_refs:
                turn["receipt_refs"] = list(receipt_refs)

            filtered_metadata = self._sanitize_metadata(metadata)
            if filtered_metadata:
                turn["metadata"] = filtered_metadata

            turns.append(turn)

        return turns

    def render_run_card(self, run_card: Any | None) -> str:
        if run_card is None:
            return ""
        run_card_lines = ["【Run Card】"]
        if getattr(run_card, "current_goal", ""):
            run_card_lines.append(f"Goal: {run_card.current_goal}")
        if getattr(run_card, "open_loops", ()):
            run_card_lines.append(f"Open loops: {len(list(run_card.open_loops))}")
        if getattr(run_card, "latest_user_intent", ""):
            run_card_lines.append(f"Latest intent: {run_card.latest_user_intent[:100]}")
        if getattr(run_card, "pending_followup_action", ""):
            run_card_lines.append(f"Pending: {run_card.pending_followup_action}")
        if getattr(run_card, "last_turn_outcome", ""):
            run_card_lines.append(f"Last outcome: {run_card.last_turn_outcome}")
        return "\n".join(run_card_lines)

    def build_payload(
        self,
        *,
        active_window: Iterable[Any],
        receipt_store: ReceiptStore,
        head_anchor: str = "",
        tail_anchor: str = "",
        run_card: Any | None = None,
        supplemental_turns: Iterable[Mapping[str, Any]] = (),
        user_message: str = "",
        structured_findings: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        turns = self.build_turns(active_window, receipt_store)
        for turn in supplemental_turns:
            normalized_turn = self._normalize_turn(turn, receipt_store)
            if normalized_turn is not None:
                turns.append(normalized_turn)
        if user_message:
            turns.append({"role": "user", "content": user_message})

        payload: dict[str, Any] = {"turns": turns}
        if head_anchor:
            payload["system_hint"] = head_anchor
        if tail_anchor:
            payload["tail_hint"] = tail_anchor
        rendered_run_card = self.render_run_card(run_card)
        if rendered_run_card:
            payload["run_card"] = rendered_run_card
        # Phase 1.5: Include structured findings in payload for downstream consumption
        if structured_findings:
            payload["structured_findings"] = structured_findings
        return payload

    def project(
        self,
        projection: dict[str, Any],
        receipt_store: ReceiptStore,
    ) -> list[dict[str, Any]]:
        cleaned = self._strip_control_plane_noise(projection)
        _turns_count = len(cleaned.get("turns", []))
        logger.debug(
            "[DEBUG][ProjectionEngine] project start: turns=%d system_hint=%s tail_hint=%s run_card=%s",
            _turns_count,
            "yes" if cleaned.get("system_hint") else "no",
            "yes" if cleaned.get("tail_hint") else "no",
            "yes" if cleaned.get("run_card") else "no",
        )
        messages: list[dict[str, Any]] = []
        normalized_turns: list[dict[str, Any]] = []

        system_hint = cleaned.get("system_hint")
        if system_hint:
            messages.append({"role": "system", "content": str(system_hint)})

        # Phase 1.5: Inject structured findings as system context
        structured_findings = cleaned.get("structured_findings")
        if structured_findings and isinstance(structured_findings, dict):
            confirmed_facts = structured_findings.get("confirmed_facts", [])
            if confirmed_facts:
                facts_text = "\n".join(f"- {fact}" for fact in confirmed_facts if isinstance(fact, str))
                if facts_text:
                    messages.append(
                        {
                            "role": "system",
                            "content": f"## Confirmed Facts\n{facts_text}",
                        }
                    )

        for turn in cleaned.get("turns", []):
            if not isinstance(turn, Mapping):
                continue
            normalized_turn = self._normalize_turn(turn, receipt_store)
            if normalized_turn is not None:
                normalized_turns.append(normalized_turn)

        trailing_user_turn: dict[str, Any] | None = None
        if normalized_turns and str(normalized_turns[-1].get("role", "")).strip().lower() == "user":
            trailing_user_turn = normalized_turns.pop()

        messages.extend(normalized_turns)

        tail_hint = cleaned.get("tail_hint")
        if tail_hint:
            messages.append({"role": "system", "content": str(tail_hint)})

        run_card = cleaned.get("run_card")
        if run_card:
            messages.append({"role": "system", "content": str(run_card)})

        if trailing_user_turn is not None:
            # Keep the current user instruction as the final message so that
            # historical run-card hints never override current-turn intent.
            messages.append(trailing_user_turn)

        logger.debug(
            "[DEBUG][ProjectionEngine] project end: messages=%d system=%d user=%d assistant=%d tool=%d",
            len(messages),
            sum(1 for m in messages if m.get("role") == "system"),
            sum(1 for m in messages if m.get("role") == "user"),
            sum(1 for m in messages if m.get("role") == "assistant"),
            sum(1 for m in messages if m.get("role") == "tool"),
        )
        return messages
