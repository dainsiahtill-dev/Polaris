"""ProjectionEngine - generates LLM-ready prompt projections."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Iterable, Mapping

from polaris.kernelone.context.context_os.helpers import get_metadata_value

if TYPE_CHECKING:
    from polaris.kernelone.context.receipt_store import ReceiptStore

logger = logging.getLogger(__name__)


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

    def __init__(self) -> None:
        """Initialize ProjectionEngine with adaptive learning state."""
        self._weights = _AdaptiveWeights()
        self._outcomes: list[_ProjectionOutcome] = []
        self._max_outcomes: int = 100  # Rolling window for weight learning
        self._projection_count: int = 0

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

    def _normalize_turn(self, turn: Mapping[str, Any], receipt_store: ReceiptStore) -> dict[str, Any] | None:
        role = str(turn.get("role") or "").strip()
        content = str(turn.get("content") or "")
        if not role:
            return None

        result: dict[str, Any] = {"role": role, "content": content}

        receipt_refs = turn.get("receipt_refs")
        if isinstance(receipt_refs, (list, tuple)) and receipt_store is not None:
            snippets: list[str] = []
            for ref in receipt_refs:
                receipt_content = receipt_store.get(str(ref))
                if receipt_content is not None:
                    snippets.append(f"[Receipt {ref}]: {receipt_content[:500]}")
            if snippets:
                result["content"] = content + "\n\n" + "\n".join(snippets)
                result["receipt_refs"] = [str(ref) for ref in receipt_refs]

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
        if not events:
            return []

        # Compute adaptive priority key using current weights
        def event_priority_key(event: Any) -> tuple[int, float, float, float, int]:
            sequence = int(getattr(event, "sequence", 0))
            route = str(getattr(event, "route", "clear") or "clear").lower()
            metadata = getattr(event, "metadata", ())
            confidence = float(get_metadata_value(metadata, "routing_confidence", 0.5))
            dialog_act_bonus = self._dialog_act_priority(metadata) * 0.1
            role = str(getattr(event, "role", "user") or "user")
            role_prio = self._role_priority(role)

            # Route score (higher route priority = more signal)
            route_score = float(self._ROUTE_PRIORITY.get(route, 0)) / 3.0

            # Recency score (normalized to 0-1 based on position)
            max_seq = max((int(getattr(e, "sequence", 0)) for e in events), default=1)
            recency_score = sequence / max(1, max_seq) if max_seq > 0 else 0.0

            # Combined confidence with dialog act bonus
            combined_confidence = min(1.0, confidence + dialog_act_bonus)

            # Weighted composite score (lower = higher priority in sort)
            composite = (
                -sequence,  # Primary: earlier events first
                -(self._weights.route_weight * route_score),  # Route contribution
                -(self._weights.confidence_weight * combined_confidence),  # Confidence contribution
                -(self._weights.recency_weight * recency_score),  # Recency contribution
                -role_prio,  # Role priority as tiebreaker
            )
            return composite

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
        outcome = _ProjectionOutcome(
            projection_id=f"proj_{self._projection_count}",
            timestamp=datetime.now(timezone.utc),
            success=success,
            route_score=0.5,
            confidence_score=0.5,
            recency_score=0.5,
            tokens_used=tokens_used,
        )
        self._outcomes.append(outcome)

        # Rolling window: keep last _max_outcomes
        if len(self._outcomes) > self._max_outcomes:
            self._outcomes = self._outcomes[-self._max_outcomes :]

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
        sorted_events = self.sort_events(active_window)
        if not sorted_events:
            return []

        turns: list[dict[str, Any]] = []
        latest_sequence = int(getattr(sorted_events[-1], "sequence", 0))

        for index, event in enumerate(sorted_events):
            route = str(getattr(event, "route", "clear") or "clear").lower()
            metadata = getattr(event, "metadata", ())
            if route == "clear":
                is_forced = bool(get_metadata_value(metadata, "reopen_hold")) if metadata else False
                is_recent = int(getattr(event, "sequence", 0)) >= latest_sequence - 3
                if not is_forced and not is_recent:
                    continue

            role = str(getattr(event, "role", "user") or "user")
            content = str(getattr(event, "content", "") or "")
            event_id = str(getattr(event, "event_id", "") or f"idx_{index}")

            if route == "archive":
                artifact_id = str(getattr(event, "artifact_id", "") or event_id)
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
