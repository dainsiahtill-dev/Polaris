"""Unified tool-call loop controller for role kernel turns.

# -*- coding: utf-8 -*-
UTF-8 编码验证: 本文所有文本使用 UTF-8

## 职责边界（P0-012）

ToolLoopController 是 **Transcript 状态管理者**，被 TurnEngine 和
TurnTransactionController 共享使用：

| 方法 | TurnEngine 使用 | TurnTransactionController 使用 |
|------|----------------|------------------------------|
| build_context_request() | 每次 LLM 调用前 | 不使用（有自己的 context 构建） |
| append_tool_result() | 增量工具执行后 | 不使用（有 ToolBatchRuntime） |
| register_cycle() | 每轮工具执行后 | 不使用（有 StateMachine） |

**核心职责**：
- Transcript-first 状态管理（_history）
- 增量工具结果追加
- 安全策略检查（max_calls, stall_cycles）
- Success loop 检测

**不负责**：
- 循环控制（TurnEngine 负责）
- 状态机转换（TurnTransactionController 负责）
- LLM 调用决策（TurnEngine 负责）

This module centralizes the dialogue transcript and loop-safety policy used by
streaming role execution. The controller itself does not decide *what* the LLM
should do next. It only encodes the mainstream agent-loop contract:

1. The assistant returns either tool calls or a final answer.
2. If tool calls are returned, the host executes them and appends tool receipts.
3. The next LLM turn is driven by the updated transcript, not by an ad-hoc
   natural-language follow-up prompt.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from polaris.cells.roles.kernel.internal.circuit_breaker import (
    CircuitBreakerLevel,
    ProgressiveCircuitBreaker,
    infer_task_scene,
)
from polaris.cells.roles.kernel.internal.context_event import (
    _DEFAULT_CONTEXT_WINDOW_TOKENS,
    _MAX_READ_FILE_CONTENT_CHARS,
    _MAX_RESULT_DEPTH,
    _MAX_RESULT_ERROR_CHARS,
    _MAX_RESULT_LIST_ITEMS,
    _MAX_RESULT_OBJECT_KEYS,
    _MAX_RESULT_STRING_CHARS,
    _READ_FILE_PROMOTION_HEADROOM_RATIO,
    ContextEvent,
    ToolLoopSafetyPolicy,
)
from polaris.cells.roles.kernel.internal.context_gateway import ContextRequest
from polaris.cells.roles.kernel.internal.metrics import get_dead_loop_metrics
from polaris.kernelone.llm.engine.model_catalog import ModelCatalog
from polaris.kernelone.llm.runtime_config import get_role_model
from polaris.kernelone.llm.toolkit.parsers import format_tool_result


class ToolLoopCircuitBreakerError(Exception):
    """Hard fault raised when tool loop is detected and must be stopped.

    This exception is raised when:
    1. Same tool with same args is repeated SUCCESS_LOOP_HARD_THRESHOLD times
    2. Cross-tool loop (ABAB pattern) is detected
    3. State stagnation: too many read-only ops without workspace modification

    The error message includes guidance for the LLM to recover.
    """

    def __init__(self, message: str, recovery_hint: str = "", breaker_type: str = "unknown") -> None:
        self.recovery_hint = recovery_hint or (
            "探查阶段已超时。请根据已有信息立即给出结论或执行写入操作。如果信息不足，请明确说明缺口，而不是继续探查。"
        )
        self.breaker_type = breaker_type
        super().__init__(f"{message}\n\n[恢复指导] {self.recovery_hint}")


if TYPE_CHECKING:
    from polaris.cells.roles.kernel.internal.output_parser import ToolCallResult
    from polaris.cells.roles.profile.public.service import RoleProfile, RoleTurnRequest


@dataclass(slots=True)
class ToolLoopController:
    """Owns transcript state for one assistant turn with tool execution.

    P0 SSOT Enforcement: This controller now requires context_os_snapshot as the
    sole source of history. The legacy request.history fallback has been eliminated.
    This ensures ContextOS is the Single Source of Truth for the entire system.

    Architecture:
        - _history: List[ContextEvent] - immutable events, current turn scratchpad
        - ToolLoopController only SEEDS from snapshot, it does NOT own snapshot state
        - All new events are appended to _history during the current turn
        - At turn end, events are committed back to ContextOS via event sourcing
    """

    request: RoleTurnRequest
    profile: RoleProfile
    safety_policy: ToolLoopSafetyPolicy
    _history: list[ContextEvent] = field(default_factory=list)
    _pending_user_message: str = ""
    _last_consumed_message: str = ""  # Tracks message consumed on last build_context_request call
    _started_at: float = field(default_factory=time.monotonic)
    _total_tool_calls: int = 0
    _last_cycle_signature: str = ""
    _stall_cycles: int = 0
    _effective_context_window_tokens_cache: int | None = None
    # Success loop detection: track recent successful tool calls to detect repetitive behavior
    _recent_successful_calls: list[tuple[str, str]] = field(
        default_factory=list
    )  # [(tool_name, args_hash), ...] - bounded window of recent calls
    _recent_successful_counts: dict[tuple[str, str], int] = field(
        default_factory=dict
    )  # BUG-M02 Fix: counter for repeat detection
    # State stagnation detection: track read-only vs modifying operations
    _read_only_streak: int = field(default=0)  # Consecutive read-only operations
    _workspace_modified: bool = field(default=False)  # Whether any write occurred this turn
    # Cross-tool loop detection: recent tool names for ABAB pattern detection
    _recent_tool_names: list[str] = field(default_factory=list)  # Last N tool names

    # Sentinel object to distinguish "no snapshot" from "snapshot was empty"
    _NO_SNAPSHOT = object()

    # Threshold for triggering success loop warning (legacy, kept for compatibility)
    SUCCESS_LOOP_WARNING_THRESHOLD = 2
    # Hard threshold for circuit breaker - raises exception to force stop (legacy)
    SUCCESS_LOOP_HARD_THRESHOLD = 3
    # Maximum size of _recent_successful_calls to prevent unbounded growth
    MAX_RECENT_CALLS = 10
    # State stagnation: max read-only operations before forcing conclusion (legacy)
    MAX_READ_ONLY_STAGNATION = 5
    # Cross-tool loop detection window size (legacy)
    CROSS_TOOL_LOOP_WINDOW = 6

    # Progressive circuit breaker for semantic-aware loop detection
    _circuit_breaker: ProgressiveCircuitBreaker = field(init=False)

    def clear_history(self) -> None:
        """Clear the current turn's history scratchpad.

        Called at the start of a new turn to reset state. Events are committed
        to ContextOS via event sourcing before this is called.
        """
        self._history.clear()
        self._recent_successful_calls.clear()
        self._recent_successful_counts.clear()
        self._stall_cycles = 0
        self._read_only_streak = 0
        self._workspace_modified = False
        self._recent_tool_names.clear()
        self._last_cycle_signature = ""
        self._total_tool_calls = 0
        # Reset progressive circuit breaker for new turn
        self._circuit_breaker.reset()

    def __post_init__(self) -> None:
        self._pending_user_message = str(self.request.message or "")

        # P0 SSOT Enforcement: Seed _history ONLY from context_os_snapshot
        # For new sessions (empty transcript_log), start with empty history.
        # This enables fresh ContextOS bootstrapping for new sessions.
        snapshot_history = self._extract_snapshot_history()
        if snapshot_history is self._NO_SNAPSHOT:
            # Check if context_os_snapshot exists and is a dict (valid snapshot)
            context_override = getattr(self.request, "context_override", None)
            if isinstance(context_override, dict) and isinstance(context_override.get("context_os_snapshot"), dict):
                # Snapshot exists but empty - this is OK for new sessions
                snapshot_history = []
            else:
                raise ValueError(
                    "ToolLoopController requires context_os_snapshot for SSOT compliance. "
                    "request.history fallback is DEPRECATED and no longer supported. "
                    "Ensure the caller initializes ContextOS.project() before creating "
                    "ToolLoopController. "
                    f"request.context_override type: {type(getattr(self.request, 'context_override', None))}"
                )

        # Seeded from snapshot: tool results are NOT in snapshot.transcript_log,
        # so we need to add them separately from request.tool_results
        assert isinstance(snapshot_history, list)
        self._history = list(snapshot_history)  # Copy to prevent mutation of original snapshot
        self._seed_tool_results(self.request.tool_results)

        # Initialize progressive circuit breaker with scene detection
        scene = infer_task_scene(self._pending_user_message)
        self._circuit_breaker = ProgressiveCircuitBreaker(scene=scene)

    def _extract_snapshot_history(self) -> list[ContextEvent] | object:
        """Extract history from context_os_snapshot.transcript_log.

        P0 SSOT: Preserves FULL event metadata including:
        - event_id: Unique event identifier
        - sequence: Turn sequence number
        - route: Event routing decision (clear/patch/archive/summarize)
        - dialog_act: Dialog act classification (affirm/deny/pause/etc.)
        - All other metadata fields

        Returns:
            List[ContextEvent] with full metadata, or sentinel _NO_SNAPSHOT
            if no valid snapshot is available.

        Note:
            Previously returned List[tuple[str, str]] which lost all metadata.
            The legacy tuple interface is preserved via ContextEvent.to_tuple().
        """
        context_override = getattr(self.request, "context_override", None)
        if not isinstance(context_override, dict):
            return self._NO_SNAPSHOT
        snapshot = context_override.get("context_os_snapshot")
        if not isinstance(snapshot, dict):
            return self._NO_SNAPSHOT
        transcript = snapshot.get("transcript_log")
        if not isinstance(transcript, list):
            return self._NO_SNAPSHOT

        # P0: Preserve full event metadata, not just (role, content)
        result: list[ContextEvent] = []
        for idx, event in enumerate(transcript):
            if not isinstance(event, dict):
                continue

            role = str(event.get("role") or "").strip()
            if not role:
                continue

            # Extract all metadata fields (P0: preserve everything)
            metadata: dict[str, Any] = dict(event.get("metadata") or {})

            # Ensure dialog_act is present in metadata (critical for State-First)
            if "dialog_act" not in metadata:
                metadata["dialog_act"] = event.get("dialog_act", "")

            # P0 Bug Fix: Preserve kind, source_turns, artifact_id, created_at
            # These fields are defined in the Blueprint TranscriptEvent but were
            # previously lost during ContextEvent conversion. They are essential
            # for Event Sourcing provenance and artifact restoration.
            kind = str(event.get("kind") or "").strip()
            if kind:
                metadata["kind"] = kind

            source_turns = event.get("source_turns")
            if isinstance(source_turns, (list, tuple)) and source_turns:
                metadata["source_turns"] = list(source_turns)

            artifact_id = event.get("artifact_id")
            if artifact_id and isinstance(artifact_id, str) and artifact_id.strip():
                metadata["artifact_id"] = artifact_id.strip()

            created_at = event.get("created_at")
            if created_at and isinstance(created_at, str) and created_at.strip():
                metadata["created_at"] = created_at.strip()

            ctx_event = ContextEvent(
                event_id=str(event.get("event_id") or f"snapshot_{idx}").strip(),
                role=role,
                content=str(event.get("content") or ""),
                sequence=int(event.get("sequence") or idx),
                metadata=metadata,
            )
            result.append(ctx_event)

        return result if result else self._NO_SNAPSHOT

    def _should_raise_on_hard_stagnation(self) -> bool:
        read_only_streak = getattr(self._circuit_breaker, "_read_only_streak", 0)
        threshold = getattr(self._circuit_breaker.profile, "read_stagnation_threshold", 0)
        if threshold <= 0 or read_only_streak < threshold:
            return False
        scene = str(getattr(self._circuit_breaker, "scene", "") or "").strip().lower()
        if scene == "deep_analysis":
            return False

        # Architecture fix: only hard-break when there is ACTUAL stagnation,
        # not just a high read-only count. Multi-file refactoring tasks legitimately
        # require reading 8+ different files before writing anything.
        # Real stagnation requires either semantic repetition (same target) or
        # zero information gain (duplicate results).
        same_signature = getattr(self._circuit_breaker, "_consecutive_same_signature", 0)
        no_gain = getattr(self._circuit_breaker, "_consecutive_no_gain", 0)
        if same_signature < 3 and no_gain < 2:
            # The agent is reading different files with new information each time.
            # This is normal exploration, not stagnation. The SYSTEM WARNING
            # injected by the circuit breaker is sufficient nudge.
            return False

        # For analysis/advice-only requests, never hard-break even if stagnation
        # markers are present, because these tasks may legitimately loop over
        # the same file for comprehensive review.
        message = str(getattr(self, "_pending_user_message", "") or "").lower()
        analysis_signals = (
            "建议",
            "总结",
            "分析",
            "评估",
            "梳理",
            "归纳",
            "解析",
            "summarize",
            "summary",
            "advice",
            "suggest",
            "review",
            "assess",
            "analyze",
            "understand",
            "explore",
            "investigate",
            "audit",
        )
        has_analysis_intent = any(sig in message for sig in analysis_signals)
        modification_signals = (
            "fix",
            "修复",
            "bug",
            "错误",
            "refactor",
            "重构",
            "implement",
            "实现",
            "add",
            "添加",
            "change",
            "修改",
            "patch",
            "补丁",
            "rewrite",
            "重写",
            "optimize",
            "优化",
            "update",
            "更新",
        )
        has_modification_intent = any(sig in message for sig in modification_signals)
        if has_analysis_intent and not has_modification_intent:
            return False

        request = getattr(self, "request", None)
        domain = str(getattr(request, "domain", "") or "").strip().lower()
        task_id = str(getattr(request, "task_id", "") or "").strip()
        return (domain == "code" or bool(task_id)) and scene != "deep_analysis"

    @classmethod
    def from_request(
        cls,
        *,
        request: RoleTurnRequest,
        profile: RoleProfile,
    ) -> ToolLoopController:
        return cls(
            request=request,
            profile=profile,
            safety_policy=cls._resolve_safety_policy(request),
        )

    def build_context_request(self) -> ContextRequest:
        # Consume-on-Read: When building the context request, consume the pending
        # user message and track it so append_tool_cycle can write it to history.
        # This ensures the message is only sent once, regardless of whether
        # append_tool_cycle is called (normal path) or skipped (early return).
        self._last_consumed_message = self._pending_user_message
        self._pending_user_message = ""

        # Context OS snapshot (for state summary / working_state / artifacts)
        context_override = getattr(self.request, "context_override", None)
        context_os_snapshot: dict[str, Any] | None = None
        if isinstance(context_override, dict):
            snapshot_val = context_override.get("context_os_snapshot")
            if isinstance(snapshot_val, dict):
                context_os_snapshot = snapshot_val

        # FIX: Always pass _history to gateway. The gateway is responsible for:
        # 1. Expanding snapshot.transcript_log into prior messages (if available)
        # 2. Processing _history as current-turn additions
        # 3. Deduplicating to avoid double-inclusion
        # BUG-M03 Fix: Include metadata in tuple to preserve full event information.
        # Format: (role, content, metadata_dict) instead of just (role, content)
        history_with_metadata = tuple((e.role, e.content, e.metadata) for e in self._history)
        return ContextRequest(
            message=self._last_consumed_message,
            history=history_with_metadata,
            task_id=self.request.task_id,
            context_os_snapshot=context_os_snapshot,
        )

    def register_cycle(
        self,
        *,
        executed_tool_calls: list[ToolCallResult],
        deferred_tool_calls: list[ToolCallResult],
        tool_results: list[dict[str, Any]],
    ) -> str | None:
        tool_calls_count = len(executed_tool_calls) + len(deferred_tool_calls)
        self._total_tool_calls += tool_calls_count

        if (
            self.safety_policy.max_total_tool_calls > 0
            and self._total_tool_calls > self.safety_policy.max_total_tool_calls
        ):
            return (
                f"tool_loop_safety_exceeded: total tool calls exceeded limit={self.safety_policy.max_total_tool_calls}"
            )

        if (
            self.safety_policy.max_wall_time_seconds > 0
            and (time.monotonic() - self._started_at) > self.safety_policy.max_wall_time_seconds
        ):
            return f"tool_loop_safety_exceeded: wall time exceeded limit={self.safety_policy.max_wall_time_seconds}s"

        cycle_signature = self._build_cycle_signature(
            executed_tool_calls=executed_tool_calls,
            deferred_tool_calls=deferred_tool_calls,
            tool_results=tool_results,
        )
        if cycle_signature and cycle_signature == self._last_cycle_signature:
            self._stall_cycles += 1
        else:
            self._stall_cycles = 0
        self._last_cycle_signature = cycle_signature

        if self._stall_cycles > self.safety_policy.max_stall_cycles:
            return f"tool_loop_stalled: repeated identical tool cycle detected repeats={self._stall_cycles + 1}"
        return None

    def append_tool_cycle(
        self,
        *,
        assistant_message: str,
        tool_results: list[dict[str, Any]],
    ) -> None:
        """Append a complete tool cycle (user + assistant + tool results) to history.

        Uses ContextEvent to preserve metadata for Event Sourcing compliance.

        CRITICAL: The user message is ALWAYS written to history first, regardless
        of whether tool_results is empty. This ensures the consumed user message
        from build_context_request() is preserved even when no tools are executed
        or when tool_results is empty.
        """
        # Validate thinking tag compliance for assistant message
        assistant_text = str(assistant_message or "").strip()
        if assistant_text:
            is_valid, error_msg = self._validate_thinking_compliance(assistant_text)
            if not is_valid:
                # Record metrics only (relaxed enforcement - don't block execution)
                violation_type = "missing_open" if "必须以<thinking>" in error_msg else "other"
                get_dead_loop_metrics().record_thinking_violation(violation_type)
                # Relaxed: No format error injection to avoid interfering with tool error handling
                # Metric already recorded above, continue with assistant message as-is
                # This allows LLM to focus on actual tool errors instead of format compliance
        # ALWAYS write the user message to history first (consume-on-read semantics)
        # This is the message that was consumed in build_context_request().
        # We write it here regardless of tool_results to ensure it's preserved.
        user_message = str(self._last_consumed_message or "").strip()
        if user_message:
            self._history.append(
                ContextEvent(
                    event_id=f"user_{len(self._history)}",
                    role="user",
                    content=user_message,
                    sequence=len(self._history),
                    metadata={"kind": "user_turn", "source": "consumed_message"},
                )
            )

        # Write assistant message if present (assistant_text already validated above)
        if assistant_text:
            self._history.append(
                ContextEvent(
                    event_id=f"assistant_{len(self._history)}",
                    role="assistant",
                    content=assistant_text,
                    sequence=len(self._history),
                    metadata={"kind": "assistant_turn"},
                )
            )

        # Write tool results (may be empty, but we already wrote user message above)
        for item in tool_results:
            if not isinstance(item, dict):
                continue
            tool_name = str(item.get("tool") or "tool").strip() or "tool"
            self._history.append(
                ContextEvent(
                    event_id=f"tool_{len(self._history)}",
                    role="tool",
                    content=self._format_tool_history_result(tool_name=tool_name, payload=item),
                    sequence=len(self._history),
                    metadata={"kind": "tool_result", "tool": tool_name},
                )
            )
        self._last_consumed_message = ""

    def append_tool_result(self, tool_result: dict[str, Any], tool_args: dict[str, Any] | None = None) -> None:
        """Append a single tool result to history without duplicating assistant message.

        Uses ContextEvent to preserve metadata. This enables incremental execution
        where each tool result is immediately visible to the LLM before the
        next tool execution decision.

        Args:
            tool_result: The result dict from tool execution.
            tool_args: The original arguments passed to the tool call (for loop detection).
        """
        if not isinstance(tool_result, dict):
            return
        tool_name = str(tool_result.get("tool") or "tool").strip() or "tool"

        # Deduplicate repeated reads of the same file within the same turn
        # to prevent large file contents from bloating the context window.
        if tool_name == "read_file" and isinstance(tool_args, dict):
            file_path = str(tool_args.get("file_path") or tool_args.get("path") or "").strip()
            if file_path:
                # Find the most recent read_file result for the same file_path
                # and replace it if no assistant message was added after it.
                replace_index: int | None = None
                for idx in range(len(self._history) - 1, -1, -1):
                    ev = self._history[idx]
                    if ev.role == "assistant":
                        break  # Don't go past assistant messages
                    if ev.role == "tool" and ev.metadata.get("tool") == "read_file":
                        prev_args = ev.metadata.get("tool_args") or {}
                        prev_path = str(prev_args.get("file_path") or prev_args.get("path") or "").strip()
                        if prev_path == file_path:
                            replace_index = idx
                            break
                if replace_index is not None:
                    self._history[replace_index] = ContextEvent(
                        event_id=f"tool_{replace_index}",
                        role="tool",
                        content=self._format_tool_history_result(tool_name=tool_name, payload=tool_result),
                        sequence=replace_index,
                        metadata={"kind": "tool_result", "tool": tool_name, "tool_args": dict(tool_args)},
                    )
                    # Track successful calls for success loop detection
                    self._track_successful_call(tool_result, tool_name, tool_args=tool_args)
                    return

        self._history.append(
            ContextEvent(
                event_id=f"tool_{len(self._history)}",
                role="tool",
                content=self._format_tool_history_result(tool_name=tool_name, payload=tool_result),
                sequence=len(self._history),
                metadata={
                    "kind": "tool_result",
                    "tool": tool_name,
                    **({"tool_args": dict(tool_args)} if tool_args else {}),
                },
            )
        )
        # Track successful calls for success loop detection
        self._track_successful_call(tool_result, tool_name, tool_args=tool_args)

    def _track_successful_call(
        self, tool_result: dict[str, Any], tool_name: str, tool_args: dict[str, Any] | None = None
    ) -> None:
        """Track successful tool calls using semantic-aware progressive circuit breaker.

        Uses the new circuit_breaker.py implementation which provides:
        1. Semantic equivalence normalization (same file = equivalent operation)
        2. Information gain tracking (content fingerprinting)
        3. Progressive 3-level warnings (L1→L2→L3)
        4. Scene-adaptive thresholds (quick_fix/normal/deep_analysis)

        Also maintains legacy fields for backward compatibility with existing tests.

        Raises:
            ToolLoopCircuitBreakerError: When loop is detected and must be stopped.
        """
        # Only track successful calls
        success = tool_result.get("success", False)
        if not success:
            return

        # Lazy initialization for backward compatibility with tests using __new__
        if not hasattr(self, "_circuit_breaker"):
            self._circuit_breaker = ProgressiveCircuitBreaker(scene="normal")

        # Initialize legacy fields if not present (for tests using __new__)
        if not hasattr(self, "_recent_tool_names"):
            self._recent_tool_names = []
        if not hasattr(self, "_recent_successful_calls"):
            self._recent_successful_calls = []
        if not hasattr(self, "_recent_successful_counts"):
            self._recent_successful_counts = {}
        if not hasattr(self, "_read_only_streak"):
            self._read_only_streak = 0
        if not hasattr(self, "_workspace_modified"):
            self._workspace_modified = False

        # Use tool_args if provided, otherwise fall back to tool_result
        args = tool_args if tool_args is not None else tool_result.get("args", {})

        # Track tool names for cross-tool loop detection (legacy field)
        self._recent_tool_names.append(tool_name)
        if len(self._recent_tool_names) > self.CROSS_TOOL_LOOP_WINDOW:
            self._recent_tool_names = self._recent_tool_names[-self.CROSS_TOOL_LOOP_WINDOW :]

        # Determine if this is a read-only tool (for read-only stagnation tracking)
        _read_only_tools = {
            "read_file",
            "repo_read_head",
            "repo_read_tail",
            "repo_read_slice",
            "repo_read_around",
            "repo_rg",
            "search_code",
            "list_directory",
            "repo_tree",
            "file_exists",
            "glob",
        }
        _is_read_only = tool_name in _read_only_tools

        # Evaluate using progressive circuit breaker
        level, count = self._circuit_breaker.evaluate(tool_name, args, tool_result, is_read_only=_is_read_only)

        # Record metrics for monitoring
        if level != CircuitBreakerLevel.OK:
            get_dead_loop_metrics().record_circuit_breaker(
                level.value,
                tool_name=tool_name,
                details={"count": str(count), "scene": self._circuit_breaker.scene},
            )

        # Handle progressive escalation
        if level == CircuitBreakerLevel.BREAK:
            # Hard stop - raise exception
            raise ToolLoopCircuitBreakerError(
                self._circuit_breaker.get_warning_message(level, tool_name, count),
                breaker_type=level.value,
            )
        elif level == CircuitBreakerLevel.HARD:
            # Generic HARD remains warning-only; only read-only stagnation
            # in code execution flows escalates to a true stop.
            warning_msg = self._circuit_breaker.get_warning_message(level, tool_name, count)
            self._inject_progressive_warning(warning_msg, level, tool_name, count)
            if self._should_raise_on_hard_stagnation():
                raise ToolLoopCircuitBreakerError(
                    warning_msg,
                    breaker_type=level.value,
                )
        elif level == CircuitBreakerLevel.WARNING:
            # Soft warning - inject reminder but continue
            warning_msg = self._circuit_breaker.get_warning_message(level, tool_name, count)
            self._inject_progressive_warning(warning_msg, level, tool_name, count)
            # Don't raise for WARNING level - just inject the reminder
        # OK level - no action needed

        # Legacy field updates for backward compatibility with tests
        read_only_tools = {
            "read_file",
            "repo_read_head",
            "repo_read_tail",
            "repo_read_slice",
            "repo_read_around",
            "repo_rg",
            "search_code",
            "list_directory",
            "repo_tree",
            "file_exists",
            "glob",
        }

        if tool_name in read_only_tools:
            self._read_only_streak += 1
        else:
            self._workspace_modified = True
            self._read_only_streak = 0
            # Reset tracking for write tools (they change state, so repetition is meaningful)
            self._recent_successful_calls.clear()
            self._recent_successful_counts.clear()
            return

        # Create legacy signature for same-tool detection (uses full args hash, not semantic)
        if isinstance(args, dict):
            args_hash = json.dumps(args, sort_keys=True, ensure_ascii=False)
        else:
            args_hash = str(args) if args else ""

        call_key = (tool_name, args_hash)

        # Check if this is a repeat of the previous call
        if self._recent_successful_calls and self._recent_successful_calls[-1] == call_key:
            self._recent_successful_counts[call_key] = self._recent_successful_counts.get(call_key, 0) + 1
        else:
            self._recent_successful_calls = [call_key]
            self._recent_successful_counts = {call_key: 1}

        # Enforce max size to prevent unbounded growth
        if len(self._recent_successful_calls) > self.MAX_RECENT_CALLS:
            self._recent_successful_calls = self._recent_successful_calls[-self.MAX_RECENT_CALLS :]
            self._recent_successful_counts = {
                call: count
                for call, count in self._recent_successful_counts.items()
                if call in self._recent_successful_calls
            }

    def _detect_cross_tool_loop(self) -> bool:
        """Detect cross-tool loop patterns (ABAB, ABCABC, etc.).

        Returns:
            True if a repeating pattern is detected in recent tool calls.
        """
        if len(self._recent_tool_names) < 4:
            return False

        # Check for ABAB pattern (last 4 tools: A,B,A,B)
        last4 = self._recent_tool_names[-4:]
        if last4[0] == last4[2] and last4[1] == last4[3] and last4[0] != last4[1]:
            return True

        # Check for longer patterns (ABCABC for 6 tools)
        if len(self._recent_tool_names) >= 6:
            last6 = self._recent_tool_names[-6:]
            if (
                last6[0] == last6[3] and last6[1] == last6[4] and last6[2] == last6[5] and len(set(last6[:3])) == 3
            ):  # A, B, C are all different
                return True

        return False

    def _inject_progressive_warning(
        self, warning_message: str, level: CircuitBreakerLevel, tool_name: str, count: int
    ) -> None:
        """Inject a progressive warning message into the transcript.

        When the circuit breaker detects potential loops, it injects warnings
        to guide the LLM toward completing the task.
        """
        # Guard against tests using __new__ without _history initialized
        if not hasattr(self, "_history"):
            self._history = []

        self._history.append(
            ContextEvent(
                event_id=f"system_{len(self._history)}",
                role="system",
                content=warning_message,
                sequence=len(self._history),
                metadata={
                    "source": "circuit_breaker",
                    "level": level.value,
                    "tool": tool_name,
                    "count": count,
                },
            )
        )

    @staticmethod
    def _validate_thinking_compliance(content: str) -> tuple[bool, str]:
        """Validate assistant content complies with <thinking> tag requirement.

        Per prompt_templates.py:
        - Must start with <thinking> tag
        - Must have closing </thinking> tag
        - No content before <thinking> tag

        Args:
            content: Assistant message content

        Returns:
            Tuple of (is_valid, error_message)
        """
        content_stripped = content.strip()

        # Check if starts with <thinking (allowing attributes like <thinking:abc>)
        if not content_stripped.startswith("<thinking"):
            # Check if there's content before <thinking>
            think_pos = content_stripped.find("<thinking")
            if think_pos > 0:
                prefix = content_stripped[:think_pos].strip()
                if prefix:
                    return (
                        False,
                        f"<thinking>标签前有额外内容: '{prefix[:30]}...' ⚠️ 严禁在<thinking>之前输出任何角色台词!",
                    )
            return False, "回复必须以<thinking>标签开头"

        # Check for closing </thinking>
        if "</thinking>" not in content_stripped:
            return False, "缺少</thinking>闭合标签"

        # Check content before first <thinking>
        first_think_pos = content_stripped.find("<thinking")
        if first_think_pos > 0:
            prefix = content_stripped[:first_think_pos].strip()
            if prefix and not prefix.startswith("["):  # Allow system markers like [FORMAT VIOLATION]
                return False, f"<thinking>标签前有内容: '{prefix[:30]}...'"

        return True, ""

    @staticmethod
    def _normalize_history(history: list[tuple] | None) -> list[tuple[str, str]]:
        normalized: list[tuple[str, str]] = []
        for item in list(history or []):
            if not isinstance(item, tuple) or len(item) < 2:
                continue
            role = str(item[0] or "").strip()
            content = str(item[1] or "")
            if role:
                normalized.append((role, content))
        return normalized

    def _seed_tool_results(self, tool_results: list[dict[str, Any]] | None) -> None:
        for item in list(tool_results or []):
            if not isinstance(item, dict):
                continue
            tool_name = str(item.get("tool") or "tool").strip() or "tool"
            self._history.append(
                ContextEvent(
                    event_id=f"tool_{len(self._history)}",
                    role="tool",
                    content=self._format_tool_history_result(tool_name=tool_name, payload=item),
                    sequence=len(self._history),
                    metadata={"tool": tool_name},
                )
            )

    def _format_tool_history_result(
        self,
        *,
        tool_name: str,
        payload: dict[str, Any],
    ) -> str:
        """Format compact tool receipts for transcript context."""
        compact_payload = self._compact_tool_result_payload(
            tool_name=tool_name,
            payload=payload,
        )
        return format_tool_result(tool_name, compact_payload)

    def _compact_tool_result_payload(
        self,
        *,
        tool_name: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        compact: dict[str, Any] = {
            "tool": str(payload.get("tool") or tool_name).strip() or tool_name,
        }
        if "success" in payload:
            compact["success"] = bool(payload.get("success"))
        if "authorized" in payload:
            compact["authorized"] = bool(payload.get("authorized"))

        error_text = str(payload.get("error") or "").strip()
        if error_text:
            compact["error"] = self._trim_text(
                error_text,
                max_chars=_MAX_RESULT_ERROR_CHARS,
            )

        result_value: Any = payload.get("result")
        if result_value is None and "raw_result" in payload:
            result_value = payload.get("raw_result")
        if result_value is not None:
            compact_result = self._compact_value(result_value, depth=0)
            compact["result"] = self._promote_read_file_content(
                tool_name=tool_name,
                raw_result=result_value,
                compact_result=compact_result,
            )

        for key in ("operation", "file_path", "file", "args"):
            if key in payload:
                compact[key] = self._compact_value(payload.get(key), depth=0)
        return compact

    def _promote_read_file_content(
        self,
        *,
        tool_name: str,
        raw_result: Any,
        compact_result: Any,
    ) -> Any:
        """Preserve meaningful read_file content in transcript history.

        Root-cause fix:
        The generic 1600-char compaction made medium file reads look incomplete
        to the model, which could trigger repeated identical read_file cycles.
        """
        if str(tool_name or "").strip().lower() != "read_file":
            return compact_result
        if not isinstance(raw_result, dict) or not isinstance(compact_result, dict):
            return compact_result

        raw_content = raw_result.get("content")
        if not isinstance(raw_content, str):
            return compact_result
        if not self._can_promote_read_file_content(raw_content):
            if (
                isinstance(compact_result, dict)
                and isinstance(compact_result.get("content"), str)
                and compact_result.get("content") != raw_content
            ):
                compact_result["content_compacted_by_tool_loop"] = True
                compact_result["content_original_chars"] = len(raw_content)
            return compact_result

        max_chars = max(2048, min(_MAX_READ_FILE_CONTENT_CHARS, 200000))
        if len(raw_content) <= max_chars:
            compact_result["content"] = raw_content
            compact_result["content_compacted_by_tool_loop"] = False
            return compact_result

        # Content exceeds max_chars - calculate remaining budget for effective truncation.
        allowed_prompt_tokens = max(
            256,
            int(self._effective_context_window_tokens() * (1.0 - _READ_FILE_PROMOTION_HEADROOM_RATIO)),
        )
        used_tokens = self._estimate_history_tokens()
        remaining_budget_chars = (allowed_prompt_tokens - used_tokens) * 4
        effective_max_chars = min(max_chars, max(0, remaining_budget_chars))  # Respect budget constraint

        compact_result["content"] = self._trim_text(raw_content, max_chars=effective_max_chars)
        compact_result["content_compacted_by_tool_loop"] = True
        compact_result["content_original_chars"] = len(raw_content)
        # Truncation notice: tell the LLM the content is incomplete
        # so it uses targeted range reads instead of blind full re-reads
        compact_result["_truncation_notice"] = (
            f"Content truncated from {len(raw_content)} to {effective_max_chars} chars. "
            f"Use read_file with start_line/end_line for specific ranges."
        )
        return compact_result

    def _can_promote_read_file_content(self, content: str) -> bool:
        """Only keep verbose read_file content when context budget allows it."""
        max_context_tokens = self._effective_context_window_tokens()
        allowed_prompt_tokens = max(
            256,
            int(max_context_tokens * (1.0 - _READ_FILE_PROMOTION_HEADROOM_RATIO)),
        )
        used_tokens = self._estimate_history_tokens()
        projected_tokens = used_tokens + self._estimate_text_tokens(content)
        return projected_tokens <= allowed_prompt_tokens

    def _effective_context_window_tokens(self) -> int:
        context_policy = getattr(self.profile, "context_policy", None)
        configured = int(getattr(context_policy, "max_context_tokens", 0) or 0)
        if self._effective_context_window_tokens_cache is not None:
            return self._effective_context_window_tokens_cache

        model_context_tokens = self._resolve_model_context_window_tokens()
        effective = max(
            1024,
            model_context_tokens or configured or _DEFAULT_CONTEXT_WINDOW_TOKENS,
        )
        self._effective_context_window_tokens_cache = effective
        return effective

    def _resolve_model_context_window_tokens(self) -> int:
        """Resolve model context window tokens for current role request.

        Priority:
        1) profile.model/provider_id (if model is present)
        2) runtime role binding (when profile model is empty)
        3) fallback to 0 (caller uses context policy/default)
        """
        provider_id = str(getattr(self.profile, "provider_id", "") or "").strip()
        model = str(getattr(self.profile, "model", "") or "").strip()

        if not model:
            bound_provider, bound_model = self._resolve_runtime_role_binding()
            if bound_model:
                model = bound_model
                if not provider_id:
                    provider_id = bound_provider

        if not model:
            return 0

        workspace = str(getattr(self.request, "workspace", "") or "").strip() or "."
        try:
            spec = ModelCatalog(workspace=workspace).resolve(provider_id, model)
        except (RuntimeError, ValueError):
            return 0
        return max(0, int(getattr(spec, "max_context_tokens", 0) or 0))

    def _resolve_runtime_role_binding(self) -> tuple[str, str]:
        role_id = str(getattr(self.profile, "role_id", "") or "").strip()
        if not role_id:
            return "", ""
        try:
            provider_id, model = get_role_model(role_id)
        except (RuntimeError, ValueError):
            return "", ""
        return str(provider_id or "").strip(), str(model or "").strip()

    def _estimate_history_tokens(self) -> int:
        total = 0
        if self._pending_user_message.strip():
            total += self._estimate_text_tokens(self._pending_user_message) + 4
        for event in self._history:
            total += self._estimate_text_tokens(str(event.content or "")) + 4
            if str(event.role or "").strip():
                total += 1
        return total

    @staticmethod
    def _estimate_text_tokens(text: str) -> int:
        """Fast token estimate for budget gating (not billing-accurate)."""
        token = str(text or "")
        if not token:
            return 1
        # ASCII-heavy text ~= 4 chars/token. Keep a floor for CJK/mixed payloads.
        return max(1, int(len(token) / 4))

    def _compact_value(self, value: Any, *, depth: int) -> Any:
        if depth >= _MAX_RESULT_DEPTH:
            return "[TRUNCATED_DEPTH]"
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, str):
            return self._trim_text(value, max_chars=_MAX_RESULT_STRING_CHARS)
        if isinstance(value, list):
            items = [self._compact_value(item, depth=depth + 1) for item in value[:_MAX_RESULT_LIST_ITEMS]]
            if len(value) > _MAX_RESULT_LIST_ITEMS:
                items.append(f"[TRUNCATED_ITEMS:{len(value) - _MAX_RESULT_LIST_ITEMS}]")
            return items
        if isinstance(value, dict):
            compact_obj: dict[str, Any] = {}
            priority_keys = (
                "ok",
                "file",
                "path",
                "content",
                "truncated",
                "bytes",
                "line_count",
                "message",
                "error",
                "stdout",
                "stderr",
            )
            ordered_keys: list[str] = []
            for key in priority_keys:
                if key in value and key not in ordered_keys:
                    ordered_keys.append(key)
            for key in value:
                key_name = str(key)
                if key_name not in ordered_keys:
                    ordered_keys.append(key_name)

            selected_keys = ordered_keys[:_MAX_RESULT_OBJECT_KEYS]
            for key in selected_keys:
                compact_obj[key] = self._compact_value(
                    value.get(key),
                    depth=depth + 1,
                )
            omitted = len(ordered_keys) - len(selected_keys)
            if omitted > 0:
                compact_obj["_omitted_keys"] = omitted
            return compact_obj

        return self._trim_text(str(value), max_chars=_MAX_RESULT_STRING_CHARS)

    @staticmethod
    def _trim_text(text: str, *, max_chars: int) -> str:
        token = str(text or "")
        if len(token) <= max_chars:
            return token
        # Reserve space for the marker so total output <= max_chars
        omitted_chars = len(token) - max_chars
        marker = f"...[TRUNCATED:{omitted_chars} chars]..."
        available = max_chars - len(marker)
        head = max(1, int(available * 0.75))
        tail = max(1, available - head)
        return token[:head] + marker + token[-tail:]

    def _build_cycle_signature(
        self,
        *,
        executed_tool_calls: list[ToolCallResult],
        deferred_tool_calls: list[ToolCallResult],
        tool_results: list[dict[str, Any]],
    ) -> str:
        payload = {
            "executed": [self._tool_call_signature(item) for item in executed_tool_calls],
            "deferred": [self._tool_call_signature(item) for item in deferred_tool_calls],
            "results": [self._tool_result_signature(item) for item in tool_results if isinstance(item, dict)],
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @staticmethod
    def _tool_call_signature(call: ToolCallResult) -> dict[str, Any]:
        safe_args = call.args if isinstance(call.args, dict) else {"value": str(call.args or "")}
        return {
            "tool": str(call.tool or "").strip(),
            "args": safe_args,
        }

    @staticmethod
    def _tool_result_signature(result: dict[str, Any]) -> dict[str, Any]:
        payload = result if isinstance(result, dict) else {"value": str(result)}
        return {
            "tool": str(payload.get("tool") or "").strip(),
            "success": bool(payload.get("success", False)),
            "authorized": payload.get("authorized"),
            "error": str(payload.get("error") or "").strip()[:240],
            "result": payload.get("result"),
        }

    @classmethod
    def _resolve_safety_policy(cls, request: RoleTurnRequest | None = None) -> ToolLoopSafetyPolicy:
        """Resolve safety policy from environment variables and request metadata.

        Request metadata takes precedence over environment variables.
        """
        metadata = dict(request.metadata) if request else {}

        # Check for tool loop overrides in metadata
        max_calls_override = metadata.get("max_total_tool_calls")
        stall_cycles_override = metadata.get("max_stall_cycles")
        wall_time_override = metadata.get("max_wall_time_seconds")

        max_total_tool_calls = (
            cls._read_int_env(
                "KERNELONE_TOOL_LOOP_MAX_TOTAL_CALLS",
                default=int(max_calls_override) if max_calls_override else 64,
                minimum=1,
                maximum=512,
            )
            if not max_calls_override
            else int(max_calls_override)
        )

        max_stall_cycles = (
            cls._read_int_env(
                "KERNELONE_TOOL_LOOP_MAX_STALL_CYCLES",
                default=int(stall_cycles_override) if stall_cycles_override else 2,
                minimum=0,
                maximum=16,
            )
            if not stall_cycles_override
            else int(stall_cycles_override)
        )

        max_wall_time_seconds = (
            cls._read_int_env(
                "KERNELONE_TOOL_LOOP_MAX_WALL_TIME_SECONDS",
                default=int(wall_time_override) if wall_time_override else 900,
                minimum=30,
                maximum=7200,
            )
            if not wall_time_override
            else int(wall_time_override)
        )

        return ToolLoopSafetyPolicy(
            max_total_tool_calls=max_total_tool_calls,
            max_stall_cycles=max_stall_cycles,
            max_wall_time_seconds=max_wall_time_seconds,
        )

    @classmethod
    def _read_int_env(cls, name: str, *, default: int, minimum: int, maximum: int) -> int:
        raw = str(os.environ.get(name, str(default))).strip()
        try:
            parsed = int(raw)
        except (TypeError, ValueError):
            parsed = default
        return max(minimum, min(parsed, maximum))


__all__ = [
    "ContextEvent",  # Re-exported from context_event for backward compatibility
    "ToolLoopController",
    "ToolLoopSafetyPolicy",
]
