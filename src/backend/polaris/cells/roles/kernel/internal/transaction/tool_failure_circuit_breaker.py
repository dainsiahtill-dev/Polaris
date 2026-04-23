"""Per-turn tool failure circuit breaker for tool batch execution."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

_FAILURE_STATUSES: frozenset[str] = frozenset({"error", "timeout", "aborted"})
_UNKNOWN_TOOL_NAME = "unknown_tool"
_UNKNOWN_EFFECT_SCOPE = "unknown"


@dataclass(frozen=True, slots=True)
class ToolFailureCircuitBreakerSnapshot:
    """Immutable evaluation result for a single tool batch."""

    turn_id: str
    batch_failures: int
    consecutive_failures: int
    total_failures: int
    consecutive_threshold: int
    total_threshold: int
    trigger_reason: str | None = None
    triggered_dimension: str | None = None

    @property
    def triggered(self) -> bool:
        """Whether current counters cross configured breaker thresholds."""
        return self.trigger_reason is not None


@dataclass(frozen=True, slots=True)
class FailureDimensionKey:
    """Failure dimension key for per-turn breaker counters."""

    tool_name: str
    effect_scope: str
    failure_class: str


@dataclass(slots=True)
class _TurnFailureState:
    consecutive_failures: int = 0
    total_failures: int = 0
    consecutive_by_dimension: dict[FailureDimensionKey, int] = field(default_factory=dict)
    total_by_dimension: dict[FailureDimensionKey, int] = field(default_factory=dict)


class ToolFailureCircuitBreaker:
    """Track per-turn tool failures and trigger fail-closed when limits are exceeded."""

    def __init__(
        self,
        *,
        consecutive_failure_threshold: int = 3,
        total_failure_threshold: int = 10,
        effect_threshold_overrides: Mapping[str, tuple[int, int]] | None = None,
    ) -> None:
        if consecutive_failure_threshold <= 0:
            raise ValueError("consecutive_failure_threshold must be > 0")
        if total_failure_threshold <= 0:
            raise ValueError("total_failure_threshold must be > 0")
        self._consecutive_failure_threshold = consecutive_failure_threshold
        self._total_failure_threshold = total_failure_threshold
        self._effect_threshold_overrides = self._normalize_effect_threshold_overrides(effect_threshold_overrides)
        self._state_by_turn: dict[str, _TurnFailureState] = {}

    def evaluate_batch(
        self,
        *,
        turn_id: str,
        receipts: Iterable[Mapping[str, Any]],
        invocations: Iterable[Mapping[str, Any]] | None = None,
    ) -> ToolFailureCircuitBreakerSnapshot:
        """Update per-turn counters using current batch receipts."""

        normalized_turn_id = str(turn_id or "")
        state = self._state_by_turn.setdefault(normalized_turn_id, _TurnFailureState())
        invocation_meta = self._build_invocation_meta(invocations or ())
        failures_by_dimension = self._collect_failures_by_dimension(receipts=receipts, invocation_meta=invocation_meta)
        batch_failures = sum(failures_by_dimension.values())

        if batch_failures > 0:
            state.consecutive_failures += 1
            state.total_failures += batch_failures
        else:
            state.consecutive_failures = 0
        self._update_dimension_counters(state, failures_by_dimension)

        trigger_reason, triggered_dimension = self._resolve_trigger(state, failures_by_dimension)

        return ToolFailureCircuitBreakerSnapshot(
            turn_id=normalized_turn_id,
            batch_failures=batch_failures,
            consecutive_failures=state.consecutive_failures,
            total_failures=state.total_failures,
            consecutive_threshold=self._consecutive_failure_threshold,
            total_threshold=self._total_failure_threshold,
            trigger_reason=trigger_reason,
            triggered_dimension=triggered_dimension,
        )

    def _collect_failures_by_dimension(
        self,
        *,
        receipts: Iterable[Mapping[str, Any]],
        invocation_meta: Mapping[str, tuple[str, str]],
    ) -> dict[FailureDimensionKey, int]:
        counts: dict[FailureDimensionKey, int] = {}
        for receipt in receipts:
            receipt_results = receipt.get("results")
            if isinstance(receipt_results, list) and receipt_results:
                for item in receipt_results:
                    if not isinstance(item, Mapping):
                        continue
                    key = self._extract_failure_dimension_key(
                        item,
                        invocation_meta=invocation_meta,
                        fallback_tool_name=str(receipt.get("tool_name", "")),
                    )
                    if key is None:
                        continue
                    counts[key] = counts.get(key, 0) + 1
                continue
            key = self._extract_failure_dimension_key(
                receipt,
                invocation_meta=invocation_meta,
                fallback_tool_name=str(receipt.get("tool_name", "")),
            )
            if key is None:
                continue
            counts[key] = counts.get(key, 0) + self._to_non_negative_int(receipt.get("failure_count", 1))
        return counts

    def _extract_failure_dimension_key(
        self,
        data: Mapping[str, Any],
        *,
        invocation_meta: Mapping[str, tuple[str, str]],
        fallback_tool_name: str,
    ) -> FailureDimensionKey | None:
        failure_class = str(data.get("status", "")).strip().lower()
        if failure_class not in _FAILURE_STATUSES:
            return None

        call_id = str(data.get("call_id", "")).strip()
        meta_tool_name = ""
        meta_effect_scope = ""
        if call_id:
            meta = invocation_meta.get(call_id)
            if meta is not None:
                meta_tool_name, meta_effect_scope = meta
        tool_name = str(data.get("tool_name", "")).strip() or meta_tool_name or fallback_tool_name or _UNKNOWN_TOOL_NAME
        effect_scope = meta_effect_scope or self._normalize_effect_scope(data.get("effect_type"))
        if not effect_scope:
            effect_scope = _UNKNOWN_EFFECT_SCOPE
        return FailureDimensionKey(
            tool_name=tool_name,
            effect_scope=effect_scope,
            failure_class=failure_class,
        )

    def _build_invocation_meta(self, invocations: Iterable[Mapping[str, Any]]) -> dict[str, tuple[str, str]]:
        meta: dict[str, tuple[str, str]] = {}
        for invocation in invocations:
            call_id = str(invocation.get("call_id", "")).strip()
            if not call_id:
                continue
            tool_name = str(invocation.get("tool_name", "")).strip() or _UNKNOWN_TOOL_NAME
            effect_scope = self._normalize_effect_scope(invocation.get("effect_type")) or _UNKNOWN_EFFECT_SCOPE
            meta[call_id] = (tool_name, effect_scope)
        return meta

    def _update_dimension_counters(
        self,
        state: _TurnFailureState,
        failures_by_dimension: Mapping[FailureDimensionKey, int],
    ) -> None:
        if not failures_by_dimension:
            for key in list(state.consecutive_by_dimension):
                state.consecutive_by_dimension[key] = 0
            return
        current_keys = set(failures_by_dimension.keys())
        for key in list(state.consecutive_by_dimension):
            if key not in current_keys:
                state.consecutive_by_dimension[key] = 0
        for key, failure_count in failures_by_dimension.items():
            if failure_count <= 0:
                continue
            state.consecutive_by_dimension[key] = state.consecutive_by_dimension.get(key, 0) + 1
            state.total_by_dimension[key] = state.total_by_dimension.get(key, 0) + failure_count

    def _resolve_trigger(
        self,
        state: _TurnFailureState,
        failures_by_dimension: Mapping[FailureDimensionKey, int],
    ) -> tuple[str | None, str | None]:
        for key in sorted(failures_by_dimension, key=self._dimension_sort_key):
            consecutive_threshold, total_threshold = self._resolve_dimension_thresholds(key.effect_scope)
            consecutive_count = state.consecutive_by_dimension.get(key, 0)
            total_count = state.total_by_dimension.get(key, 0)
            if consecutive_count >= consecutive_threshold:
                return "dimension_consecutive_threshold", self._dimension_to_str(key)
            if total_count >= total_threshold:
                return "dimension_total_threshold", self._dimension_to_str(key)
        if state.consecutive_failures >= self._consecutive_failure_threshold:
            return "global_consecutive_threshold", None
        if state.total_failures >= self._total_failure_threshold:
            return "global_total_threshold", None
        return None, None

    def _resolve_dimension_thresholds(self, effect_scope: str) -> tuple[int, int]:
        return self._effect_threshold_overrides.get(
            effect_scope,
            (self._consecutive_failure_threshold, self._total_failure_threshold),
        )

    @staticmethod
    def _normalize_effect_scope(effect_type: Any) -> str:
        normalized = str(effect_type or "").strip().lower()
        if normalized in {"read", "readonly"}:
            return "read"
        if normalized in {"write", "local_write", "external_write"}:
            return "write"
        if normalized in {"async", "async_receipt"}:
            return "async"
        return ""

    @staticmethod
    def _dimension_sort_key(key: FailureDimensionKey) -> tuple[str, str, str]:
        return (key.tool_name, key.effect_scope, key.failure_class)

    @staticmethod
    def _dimension_to_str(key: FailureDimensionKey) -> str:
        return f"{key.tool_name}|{key.effect_scope}|{key.failure_class}"

    @staticmethod
    def _normalize_effect_threshold_overrides(
        overrides: Mapping[str, tuple[int, int]] | None,
    ) -> dict[str, tuple[int, int]]:
        normalized: dict[str, tuple[int, int]] = {}
        if overrides is None:
            return normalized
        for raw_scope, raw_thresholds in overrides.items():
            scope = str(raw_scope or "").strip().lower()
            if not scope:
                continue
            if not isinstance(raw_thresholds, tuple) or len(raw_thresholds) != 2:
                raise ValueError(
                    "effect_threshold_overrides values must be tuple(consecutive_threshold, total_threshold)"
                )
            consecutive_threshold, total_threshold = raw_thresholds
            if consecutive_threshold <= 0 or total_threshold <= 0:
                raise ValueError("effect threshold overrides must be > 0")
            normalized[scope] = (int(consecutive_threshold), int(total_threshold))
        return normalized

    @staticmethod
    def _to_non_negative_int(value: Any) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return 0
        return parsed if parsed > 0 else 0
