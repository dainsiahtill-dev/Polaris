"""Role Context Gateway - 角色上下文网关

根据角色的上下文策略，差异化构建LLM上下文。

# -*- coding: utf-8 -*-
UTF-8 编码验证: 本文所有文本使用 UTF-8
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from polaris.kernelone.context.context_os import StateFirstContextOS
from polaris.kernelone.context.context_os.domain_adapters import get_context_domain_adapter
from polaris.kernelone.context.context_os.models_v2 import ContextOSSnapshotV2 as ContextOSSnapshot
from polaris.kernelone.context.contracts import (
    TurnEngineContextRequest as ContextRequest,
    TurnEngineContextResult as ContextResult,
)
from polaris.kernelone.context.history_materialization import SessionContinuityStrategy
from polaris.kernelone.context.projection_engine import ProjectionEngine
from polaris.kernelone.errors import BudgetExceededError
from polaris.kernelone.events.context_events import ContextEvent, EventType, get_event_writer

# emit_event / resolve_run_dir are referenced by GatewayTelemetry through THIS module's
# namespace (gateway_telemetry._gateway_module().emit_event / .resolve_run_dir) so the
# in-package tests' patch("...gateway.emit_event" / ".resolve_run_dir") stays effective.
from polaris.kernelone.events.io_events import emit_event  # noqa: F401  (re-exported for telemetry + test monkeypatch)
from polaris.kernelone.llm.reasoning import ReasoningStripper
from polaris.kernelone.storage.io_paths import (
    resolve_run_dir,  # noqa: F401  (re-exported for telemetry + test monkeypatch)
)
from polaris.kernelone.telemetry.debug_stream import emit_debug_event
from polaris.kernelone.telemetry.metrics import METRIC_CONTEXT_LATENCY_P95, record_metric
from polaris.kernelone.telemetry.trace import new_trace_id, set_trace_id

from .blueprint_step_card import build_blueprint_step_card
from .compression_engine import CompressionEngine
from .context_override_processor import ContextOverrideProcessor
from .gateway_helpers import (
    _capability_profile_ref_from_request,
    _coerce_float,
    _deep_merge_strategy_payload,
)
from .gateway_telemetry import GatewayTelemetry
from .projection_dict_builder import ProjectionDictBuilder
from .security import SecuritySanitizer
from .signal_sources import SignalSourceProvider
from .task_boundary_filter import filter_context_override_for_current_task
from .token_estimator import TokenEstimator

if TYPE_CHECKING:
    from polaris.cells.roles.profile.public.service import RoleProfile
    from polaris.kernelone.context.strategy_contracts import StrategyReceipt

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ContextGatewayConfig:
    """ContextGateway configuration.

    Provides fine-grained control over context building behavior.
    """

    # Enable StateOwner uniqueness validation
    enforce_state_owner_uniqueness: bool = True

    # Enable prompt injection detection
    detect_prompt_injection: bool = True

    # Enable budget validation error handling
    handle_budget_validation_errors: bool = True

    # Maximum user message characters before truncation
    max_user_message_chars: int = 4000

    # Additional context sources to include
    extra_sources: tuple[str, ...] = field(default_factory=tuple)

    # Role-specific signal data sources. The kernel owns signal assembly but not
    # business asset lookup; runtime/adapters inject owner-cell public readers.
    blueprint_overview_provider: Callable[[str, str], Any | None] | None = None
    verdict_history_provider: Callable[[str, str], Any | None] | None = None
    resident_agi_capability_provider: Callable[[str], Any | None] | None = None
    resident_agi_decision_trace_provider: Callable[[str], Any | None] | None = None


class DuplicateStateOwnerError(Exception):
    """Raised when duplicate StateOwners are detected in a context request."""

    def __init__(self, state_owners: list[str]) -> None:
        self.state_owners = state_owners
        super().__init__(f"Duplicate state owners found: {state_owners}")


class RoleContextGateway:
    """RoleContextGateway - 统一 Context 入口

    职责:
    - 统一入口，角色无关的 context 投影
    - TokenBudget 强制执行
    - StateOwner 唯一性保证

    主链路径:
        TransactionKernel/RoleExecution
            |
            v
        RoleContextGateway.build_context()
            |
            v
        StateFirstContextOS.project()
            |
            v
        ProjectionEngine.project()
            |
            v
        压缩 + TokenBudget 强制

    禁止行为:
    - 直接构建 messages 列表（应走 build_context）
    - 绕过 StateFirstContextOS
    """

    def __init__(
        self,
        profile: RoleProfile,
        workspace: Path | str = "",
        config: ContextGatewayConfig | None = None,
    ) -> None:
        """初始化上下文网关

        Args:
            profile: 角色Profile
            workspace: 工作区路径
            config: ContextGatewayConfig for fine-grained behavior control.
        """
        self.profile = profile
        self.policy = profile.context_policy
        self.workspace = Path(workspace) if workspace else Path.cwd()
        self._config = config if config is not None else ContextGatewayConfig()
        # Shared continuity strategy — uses default SessionContinuityPolicy for deterministic
        # fallback. The gateway's RoleContextPolicy (self.policy) is independent and
        # controls token limits and compression strategy separately.
        self._continuity_strategy = SessionContinuityStrategy()
        self._reasoning_stripper = ReasoningStripper()

        # Phase 2: Initialize StateFirstContextOS for intelligent context projection
        # ADR-0090 I4.4: inject the role policy budget as the resolution fallback so
        # an unresolvable provider/model binding never poisons stage-1 selection
        # with the 128k default.
        self._context_os = StateFirstContextOS(
            domain_adapter=get_context_domain_adapter(getattr(profile, "context_domain", None) or "generic"),
            provider_id=getattr(profile, "provider_id", None) or None,
            model=getattr(profile, "model", None) or None,
            workspace=str(self.workspace),
            fallback_context_window=int(self.policy.max_context_tokens),
        )

        # Initialize collaborators
        self._token_estimator = TokenEstimator()
        self._security = SecuritySanitizer()
        # learning_key=role_id：让各角色的投影自适应权重跨 turn 按角色独立累积
        # （模块级状态存储，绕过 gateway/ProjectionEngine 每 turn 新建的清零）。
        self._projection_engine = ProjectionEngine(learning_key=str(getattr(profile, "role_id", "") or "default"))
        # ADR-0090 I4.1: enforcement budget = min(role policy, model window × 0.85).
        # The static role yaml budget (e.g. chief_engineer 12k) can EXCEED a small
        # local model's window (16k qwen, 8k variants) — enforce against the
        # tighter of the two so the provider never sees an over-window prompt.
        self._enforcement_budget_tokens = self._compute_enforcement_budget()
        self._compression_engine = CompressionEngine(
            max_context_tokens=self._enforcement_budget_tokens,
            compression_strategy=str(self.policy.compression_strategy or "none"),
            max_history_turns=self.policy.max_history_turns,
            token_estimator=self._token_estimator,
            continuity_strategy=self._continuity_strategy,
            profile=self.profile,
            workspace=self.workspace,
            reasoning_stripper=self._reasoning_stripper,
        )

        # PR-11: Event writer for context operations telemetry
        self._event_writer = get_event_writer()

        # Per-run context observation emitters (context.build / prefix_drift).
        self._telemetry = GatewayTelemetry(
            workspace=self.workspace,
            role_id=str(getattr(profile, "role_id", "") or ""),
        )

        # Role-signal data-source readers (project structure / task history /
        # repo identity / scout anchors / blueprint overview / verdict history)
        # plus the cheap pre-assembly budget-pressure estimate.
        self._signal_sources = SignalSourceProvider(
            workspace=self.workspace,
            config=self._config,
            policy=self.policy,
            token_estimator=self._token_estimator,
            trigger_pct_resolver=self._context_budget_trigger_pct,
        )

        # context_override filtering + history tool-message fallback materialization.
        self._override_processor = ContextOverrideProcessor(
            detect_prompt_injection=self._config.detect_prompt_injection,
        )

        # ProjectionEngine-payload assembly (role signals + snapshot/receipt folding).
        self._projection_dict_builder = ProjectionDictBuilder(self)

    _MODEL_WINDOW_SAFETY_RATIO = 0.85
    _MIN_ENFORCEMENT_BUDGET_TOKENS = 1024

    def _compute_enforcement_budget(self) -> int:
        """ADR-0090 I4.1: clamp the role budget to the resolved model window."""
        policy_budget = int(self.policy.max_context_tokens)
        try:
            resolved_window = int(self._context_os.resolved_context_window)
        except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
            logger.warning(
                "context window resolution failed; using role policy budget %d: %s",
                policy_budget,
                exc,
            )
            return policy_budget
        if resolved_window <= 0:
            return policy_budget
        clamped = min(policy_budget, int(resolved_window * self._MODEL_WINDOW_SAFETY_RATIO))
        # The floor protects against absurdly small windows but must never RAISE
        # the budget above the role policy.
        floor = min(self._MIN_ENFORCEMENT_BUDGET_TOKENS, policy_budget)
        return max(floor, clamped)

    async def build_context(
        self,
        request: ContextRequest,
        *,
        system_prompt: str | None = None,
    ) -> ContextResult:
        """构建上下文

        Args:
            request: 上下文请求
            system_prompt: 角色 system prompt。提供时 (ADR-0090 I4.2/I4.3)：
                其 token 估计计入 enforcement 预算预留，并由 gateway 直接前插为
                首条 system 消息 —— 调用方不得再做第二次 projection 注入。

        Returns:
            上下文构建结果

        Raises:
            DuplicateStateOwnerError: If duplicate StateOwners are detected.
        """
        # PR-11: Trace context and timing for context projection latency
        trace_id = new_trace_id()
        set_trace_id(trace_id)
        start_time = time.monotonic()

        try:
            return await self._build_context_impl(request, start_time, system_prompt=system_prompt)
        finally:
            set_trace_id("")

    def record_projection_outcome(self, *, success: bool, tokens_used: int = 0) -> dict[str, float]:
        """Feed turn outcome back into the role-scoped ProjectionEngine.

        RoleContextGateway owns the role-keyed ProjectionEngine instance for the
        turn. Calling this from the role execution path closes the production
        learning loop: ContextOS projection -> TransactionKernel outcome ->
        adaptive weights for the next turn.
        """
        self._projection_engine.record_outcome(success=success, tokens_used=tokens_used)
        return self._projection_engine.get_adaptive_weights()

    async def _build_context_impl(
        self,
        request: ContextRequest,
        start_time: float,
        system_prompt: str | None = None,
    ) -> ContextResult:
        """Internal implementation of build_context with timing instrumentation."""
        sources: list[str] = []

        # ── StateOwner 唯一性验证 ──
        if self._config.enforce_state_owner_uniqueness:
            state_owners = self._extract_state_owners(request)
            if len(set(state_owners)) != len(state_owners):
                raise DuplicateStateOwnerError(state_owners)

        state_first_mode_active = self._is_state_first_mode_active_from_receipt(request.strategy_receipt)

        context_os_snapshot = getattr(request, "context_os_snapshot", None)
        has_snapshot = context_os_snapshot is not None and isinstance(context_os_snapshot, dict)
        capability_profile_ref = _capability_profile_ref_from_request(request)
        strategy_override, strategy_override_sources = self._extract_strategy_override(request)
        strategy_override_applied = bool(strategy_override)
        recent_window_messages = self._effective_recent_window_messages(strategy_override)
        context_budget_trigger_pct = self._context_budget_trigger_pct(strategy_override)
        # ADR-0090 I4.2: the role system prompt is part of the prompt the provider
        # sees — reserve its tokens BEFORE enforcement instead of injecting it
        # unbudgeted afterwards.
        reserved_system_prompt_tokens = 0
        if system_prompt:
            reserved_system_prompt_tokens = self._token_estimator.estimate(
                [{"role": "system", "content": system_prompt}]
            )
        if reserved_system_prompt_tokens >= self._enforcement_budget_tokens:
            raise BudgetExceededError(
                "role system prompt exceeds context enforcement budget",
                limit=self._enforcement_budget_tokens,
                requested=reserved_system_prompt_tokens,
                current=0,
                suggestion="shorten the role system prompt or use a model binding with a larger context window",
            )
        enforcement_budget_tokens = max(1, self._enforcement_budget_tokens - reserved_system_prompt_tokens)
        effective_context_budget_tokens = max(
            1,
            int(enforcement_budget_tokens * context_budget_trigger_pct),
        )
        if strategy_override_applied:
            sources.append("cognitive_strategy_override")

        _projection = None

        # Convert request.history to list[dict] for project()
        proj_input: list[dict[str, Any]] = []
        for item in request.history or []:
            if isinstance(item, dict):
                proj_input.append(item)
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                proj_input.append({"role": item[0], "content": item[1]})

        logger.debug(
            "[DEBUG][ContextGateway] _build_context_impl: has_snapshot=%s proj_input=%d max_history=%d effective_recent=%d focus=%r",
            has_snapshot,
            len(proj_input),
            self.policy.max_history_turns,
            recent_window_messages,
            getattr(request, "focus", "") or "",
        )
        if has_snapshot:
            _snapshot = cast("dict[str, Any]", context_os_snapshot)
            snapshot = ContextOSSnapshot.from_mapping(_snapshot)
            logger.debug(
                "[DEBUG][ContextGateway] snapshot restored: tx_events=%d artifacts=%d episodes=%d",
                len(snapshot.transcript_log) if snapshot else 0,
                len(snapshot.artifact_store) if snapshot else 0,
                len(snapshot.episode_store) if snapshot else 0,
            )
            _projection = await self._context_os.project(
                messages=proj_input,
                existing_snapshot=snapshot,
                recent_window_messages=recent_window_messages,
                focus=getattr(request, "focus", "") or "",
            )
            state_first_mode_active = True
        else:
            _projection = await self._context_os.project(
                messages=proj_input,
                existing_snapshot=None,
                recent_window_messages=recent_window_messages,
                focus=getattr(request, "focus", "") or "",
            )

        projection_dict, receipt_store, extra_sources = self._projection_dict_builder.build(_projection, request)
        projection_report = self._context_os.get_last_projection_report() or {}
        projection_id = str(projection_report.get("projection_id") or "").strip()
        context_result_id = str(projection_report.get("context_result_id") or "").strip()
        messages = list(self._projection_engine.project(projection_dict, receipt_store))
        sources.extend(extra_sources)
        if messages:
            sources.append(
                "state_first_context_os_projection" if has_snapshot else "state_first_context_os_initial_projection"
            )

        # ── Fallback: Include tool messages from history when not in state-first mode ──
        if not state_first_mode_active and not has_snapshot and request.history:
            history_tool_messages = self._override_processor.extract_tool_messages_from_history(request.history)
            logger.debug(
                "[DEBUG][ContextGateway] fallback: state_first=%s has_snapshot=%s history_len=%d tool_msgs=%d",
                state_first_mode_active,
                has_snapshot,
                len(request.history) if request.history else 0,
                len(history_tool_messages),
            )
            if history_tool_messages:
                # Pre-process tool messages: truncate if too large, add CONTEXT_TRUNCATED marker
                processed_tool_messages = self._override_processor.process_tool_messages_for_fallback(
                    history_tool_messages,
                    max_chars=2000,  # Allow some chars for the tool result
                )
                messages.extend(processed_tool_messages)
                sources.append("history_tool_fallback")

        # ── Context Override Processing with Prompt Injection Detection ──
        context_override = getattr(request, "context_override", None)
        if context_override and isinstance(context_override, dict):
            filtered_context_override, filtered_count = filter_context_override_for_current_task(
                request, context_override
            )
            override_msg = self._override_processor.process_context_override(filtered_context_override)
            if override_msg:
                messages.insert(0, override_msg)
                sources.append("context_override")
                if filtered_count:
                    sources.append("context_override_task_boundary_filter")

        # ── ContextOS routing audit telemetry ──
        raw_history_tokens = self._token_estimator.estimate(proj_input)
        projected_tokens = self._token_estimator.estimate(messages)
        route_counts: dict[str, int] = {}
        active_window_size = 0
        if _projection is not None:
            active_window_size = len(_projection.active_window)
            for event in _projection.active_window:
                route = str(getattr(event, "route", "clear")).lower()
                route_counts[route] = route_counts.get(route, 0) + 1

        # ── Phase 2: BudgetPlan validation error handling ──
        if _projection is not None and _projection.snapshot is not None:
            budget_plan = _projection.snapshot.budget_plan
            if budget_plan is not None and budget_plan.validation_error:
                logger.warning("BudgetPlan validation error: %s", budget_plan.validation_error)
                messages = self._compression_engine.emergency_truncate(messages, max_tokens=enforcement_budget_tokens)
                sources.append("budget_violation_emergency_truncate")

        # 6. 估算token数
        token_estimate = self._token_estimator.estimate(messages)
        original_token_estimate = token_estimate
        compression_applied = False
        budget_pressure_detected = token_estimate > effective_context_budget_tokens
        context_decision_hints = {
            "source": "roles.kernel.context_gateway",
            "budget_pressure": bool(budget_pressure_detected),
            "suppress_expensive_context_tools": bool(budget_pressure_detected),
            "suppress_mutating_tools": self._should_suppress_mutating_tools(request),
        }

        # 7. 应用统一压缩策略（预算 = min(角色策略, 模型窗口×0.85) − system prompt 预留）
        if token_estimate > enforcement_budget_tokens:
            if state_first_mode_active:
                messages, token_estimate = self._compression_engine.emergency_truncate_with_limit(
                    messages, enforcement_budget_tokens
                )
                compression_applied = token_estimate <= enforcement_budget_tokens
            elif not state_first_mode_active:
                messages, token_estimate = self._compression_engine.apply_compression(messages, token_estimate)
                compression_applied = True

        # Guaranteed-fit last resort: apply_compression targets the engine's
        # construction-time window and only trims dialogue, so a system-heavy
        # projection (PM planning's oversized "Current goal" plane, no assistant
        # turns to compress) can survive compression still over budget and crash
        # the turn before any write. Force the assembly under
        # enforcement_budget_tokens — which already reserves the role system_prompt
        # inserted just below — by truncating the oversized system planes.
        if token_estimate > enforcement_budget_tokens:
            messages, token_estimate = self._compression_engine.emergency_truncate_with_limit(
                messages, enforcement_budget_tokens
            )
            compression_applied = True

        # ADR-0090 I4.3: the role system prompt is prepended HERE, post-enforcement
        # and pre-budgeted — callers must not run a second projection to inject it.
        if system_prompt:
            messages.insert(0, {"role": "system", "content": system_prompt})
            sources.append("role_system_prompt")
            token_estimate += reserved_system_prompt_tokens

        if token_estimate > self._enforcement_budget_tokens:
            # order-4 diagnostic (2026-06-15): BudgetExceededError dominates the
            # weak-Director SOLO path (L2-08 8x, L2-11 6x) — it crashes the turn
            # before any write. Log the per-message token breakdown BEFORE raising
            # so the compress-to-fit fix targets the right plane (role system_prompt
            # vs injected blueprint_step signal card vs dialogue) instead of guessing.
            try:
                breakdown = [
                    {
                        "role": str(message.get("role") or "?"),
                        "tokens": int(self._token_estimator.estimate([message])),
                        "head": str(message.get("content") or "")[:60].replace("\n", " "),
                    }
                    for message in messages
                ]
                logger.warning(
                    "BudgetExceededError DIAGNOSTIC: budget=%s system_prompt_reserved=%s total=%s breakdown=%s",
                    self._enforcement_budget_tokens,
                    reserved_system_prompt_tokens,
                    token_estimate,
                    breakdown,
                )
            except Exception:  # noqa: BLE001 — diagnostics must never mask the real error
                logger.warning("BudgetExceededError DIAGNOSTIC failed to build the message breakdown")
            raise BudgetExceededError(
                "assembled role context exceeds context enforcement budget",
                limit=self._enforcement_budget_tokens,
                requested=token_estimate,
                current=self._enforcement_budget_tokens,
                suggestion="reduce projected context, compress history, or use a larger context window",
            )

        emit_debug_event(
            category="context",
            label="assembled",
            source="roles.kernel.context_gateway",
            payload={
                "workspace": str(self.workspace),
                "role": str(getattr(self.profile, "role_id", "") or ""),
                "message_count": len(messages),
                "context_sources": list(sources),
                "token_estimate_before": int(original_token_estimate),
                "token_estimate_after": int(token_estimate),
                "max_context_tokens": int(self.policy.max_context_tokens),
                "enforcement_budget_tokens": int(enforcement_budget_tokens),
                "reserved_system_prompt_tokens": int(reserved_system_prompt_tokens),
                "context_budget_trigger_pct": float(context_budget_trigger_pct),
                "effective_context_budget_tokens": int(effective_context_budget_tokens),
                "budget_pressure_detected": bool(budget_pressure_detected),
                "context_decision_hints": dict(context_decision_hints),
                "compression_applied": bool(compression_applied),
                "compression_strategy": str(self.policy.compression_strategy or "none"),
                "recent_window_messages": int(recent_window_messages),
                "base_recent_window_messages": int(self.policy.max_history_turns),
                "strategy_override_applied": bool(strategy_override_applied),
                "state_first_mode_active": bool(state_first_mode_active),
                "capability_profile_ref": capability_profile_ref,
            },
        )

        # PR-11: Write context projection event with latency
        duration_ms = (time.monotonic() - start_time) * 1000

        projection_event_metadata: dict[str, Any] = {
            "token_estimate": token_estimate,
            "compression_applied": compression_applied,
            "role": str(getattr(self.profile, "role_id", "") or ""),
            "recent_window_messages": int(recent_window_messages),
            "strategy_override_applied": bool(strategy_override_applied),
            "projection_id": projection_id,
            "context_result_id": context_result_id,
        }
        if capability_profile_ref is not None:
            projection_event_metadata["capability_profile_sha256"] = capability_profile_ref["sha256"]
            projection_event_metadata["capability_profile_source"] = capability_profile_ref["source"]
        projection_event = ContextEvent.create(
            EventType.CONTEXT_PROJECTION,
            duration_ms=duration_ms,
            metadata=projection_event_metadata,
        )
        self._event_writer.write(projection_event)

        # Record latency metric
        record_metric(METRIC_CONTEXT_LATENCY_P95, duration_ms)

        # Emit a per-run context.build observation (mirrors ContextEngine) so the
        # realtime ContextOS dashboard surfaces projections / in-window item counts
        # for THIS role turn — not just PM planning's prompt_context. Best-effort.
        self._telemetry.emit_context_build_observation(
            request,
            items_count=active_window_size,
            total_tokens=token_estimate,
            message_count=len(messages),
            projection_id=projection_id,
        )

        # Headroom T1-B step 1: emit a per-session prefix-stability observation
        # (NON-MUTATING — does not change request bytes or reorder tools). Lets
        # the ContextOS dashboard / RoleSignalPlane surface whether the cache-hot
        # prefix drifts across turns and busts the local prompt cache.
        self._telemetry.emit_prefix_drift_observation(
            request,
            messages=messages,
            system_prompt=system_prompt,
        )

        logger.debug(
            "[DEBUG][ContextGateway] _build_context_impl end: messages=%d token_estimate=%d/%d compression=%s sources=%s",
            len(messages),
            token_estimate,
            self.policy.max_context_tokens,
            compression_applied,
            sources,
        )
        return ContextResult(
            messages=tuple(messages),
            token_estimate=token_estimate,
            context_sources=tuple(sources),
            compression_applied=compression_applied,
            compression_strategy=str(self.policy.compression_strategy or "none"),
            metadata={
                "raw_history_tokens": raw_history_tokens,
                "projected_tokens": projected_tokens,
                "final_tokens": token_estimate,
                "active_window_size": active_window_size,
                "route_counts": route_counts,
                "state_first_mode_active": state_first_mode_active,
                "gateway_compression_applied": compression_applied,
                "context_sources": list(sources),
                "strategy_override_applied": strategy_override_applied,
                "strategy_override_sources": list(strategy_override_sources),
                "recent_window_messages": recent_window_messages,
                "base_recent_window_messages": self.policy.max_history_turns,
                "context_budget_trigger_pct": context_budget_trigger_pct,
                "effective_context_budget_tokens": effective_context_budget_tokens,
                "budget_pressure_detected": budget_pressure_detected,
                "context_decision_hints": context_decision_hints,
                "projection_adaptive_weights": self._projection_engine.get_adaptive_weights(),
                "capability_profile_ref": capability_profile_ref,
                "projection_id": projection_id,
                "context_result_id": context_result_id,
            },
        )

    def _extract_strategy_override(self, request: ContextRequest) -> tuple[dict[str, Any], tuple[str, ...]]:
        merged: dict[str, Any] = {}
        sources: list[str] = []

        value = getattr(request, "strategy_override", None)
        if isinstance(value, Mapping):
            _deep_merge_strategy_payload(merged, value)
            sources.append("request.strategy_override")

        context_override = getattr(request, "context_override", None)
        if isinstance(context_override, Mapping):
            for key in ("strategy_override", "cognitive_strategy_override"):
                nested = context_override.get(key)
                if isinstance(nested, Mapping):
                    _deep_merge_strategy_payload(merged, nested)
                    sources.append(f"context_override.{key}")

            metadata = context_override.get("metadata")
            if isinstance(metadata, Mapping):
                for key in ("strategy_override", "cognitive_strategy_override"):
                    nested = metadata.get(key)
                    if isinstance(nested, Mapping):
                        _deep_merge_strategy_payload(merged, nested)
                        sources.append(f"context_override.metadata.{key}")

        return merged, tuple(sources)

    def _effective_recent_window_messages(self, strategy_override: Mapping[str, Any]) -> int:
        base_window = max(1, int(self.policy.max_history_turns or 1))
        if not strategy_override:
            return base_window

        exploration = strategy_override.get("exploration")
        exploration_payload = exploration if isinstance(exploration, Mapping) else {}
        depth = _coerce_float(exploration_payload.get("max_expansion_depth"))
        aggressive = bool(exploration_payload.get("neighbor_expansion_aggressive"))

        read_escalation = strategy_override.get("read_escalation")
        read_payload = read_escalation if isinstance(read_escalation, Mapping) else {}
        full_read_allowed = bool(read_payload.get("full_read_allowed"))

        cognitive_runtime = strategy_override.get("cognitive_runtime")
        cognitive_payload = cognitive_runtime if isinstance(cognitive_runtime, Mapping) else {}
        cognitive_applied = bool(cognitive_payload.get("applied"))

        requested = base_window
        if depth is not None and depth >= 4:
            requested = max(requested, base_window * 2)
        elif depth is not None and depth >= 3:
            requested = max(requested, base_window + max(2, base_window // 2))
        if aggressive:
            requested = max(requested, base_window + 4)
        if full_read_allowed:
            requested = max(requested, base_window + 2)
        if cognitive_applied:
            requested = max(requested, base_window + 2)

        return min(max(base_window, requested), max(base_window, 32))

    @staticmethod
    def _context_budget_trigger_pct(strategy_override: Mapping[str, Any]) -> float:
        compaction = strategy_override.get("compaction") if strategy_override else None
        compaction_payload = compaction if isinstance(compaction, Mapping) else {}
        ratio = _coerce_float(compaction_payload.get("trigger_at_budget_pct"))
        if ratio is None:
            return 1.0
        return min(1.0, max(0.1, ratio))

    @staticmethod
    def _should_suppress_mutating_tools(request: ContextRequest) -> bool:
        context_override = getattr(request, "context_override", None)
        context_payload = context_override if isinstance(context_override, Mapping) else {}
        mode = (
            str(
                context_payload.get("delivery_mode")
                or context_payload.get("interaction_mode")
                or context_payload.get("mode")
                or ""
            )
            .strip()
            .lower()
        )
        return mode in {
            "analysis",
            "analyze",
            "analyze_only",
            "audit",
            "diagnostic",
            "read_only",
            "review",
        }

    def _recon_mode_active(self) -> bool:
        """Recon mode: starve the scout of pre-fed code content so it MUST use
        read/search tools to discover (information-asymmetry forcing function).

        Sourced from the role profile's ``context_policy.recon_mode`` (the
        durable 档位) OR the ``KERNELONE_SCOUT_RECON_MODE`` env toggle scoped to
        the scout role (for benchmark gray-rollout without touching production
        scout). The project-structure TREE is intentionally kept (path scaffold);
        only code-snippet CONTENT is withheld.
        """
        if bool(getattr(self.policy, "recon_mode", False)):
            return True
        env = os.getenv("KERNELONE_SCOUT_RECON_MODE", "").strip().lower()
        role = str(getattr(self.profile, "role_id", "") or "").strip().lower()
        return env in {"1", "true", "yes", "on"} and role == "scout"

    def build_system_context(self, base_prompt: str, appendix: str | None = None) -> str:
        """构建系统上下文（提示词部分）

        Args:
            base_prompt: 基础系统提示词
            appendix: 追加提示词

        Returns:
            完整的系统提示词
        """
        parts = [base_prompt]

        # 追加提示词（仅追加，不覆盖）。Recon 模式下不预喂代码内容，强制工具侦察。
        if appendix and self.policy.include_code_snippets and not self._recon_mode_active():
            parts.append("\n\n【追加上下文】\n" + appendix)

        return "\n".join(parts)

    async def _process_history(
        self,
        history: list[tuple[Any, ...]] | tuple[Any, ...],
        *,
        state_first_mode_active: bool = False,
    ) -> list[dict[str, Any]]:
        """处理历史消息

        根据策略限制历史轮数，并对历史内容进行注入检测。

        BUG-M03 Fix: Now handles 3-element tuples (role, content, metadata) from
        ContextEvent objects, preserving metadata in the messages.
        """
        if not history:
            return []

        summary_message: dict[str, str] | None = None
        # 限制历史轮数
        max_turns = self.policy.max_history_turns
        if len(history) > max_turns:
            # 当 State-First 模式激活时，不再生成 gateway 级旧摘要，
            # 避免与 Context OS 的对象化连续性语义冲突。
            if self.policy.compression_strategy == "summarize" and not state_first_mode_active:
                older_history = history[:-max_turns]
                # BUG-M03 Fix: Handle both 2-element and 3-element tuples
                summary_items = []
                for item in older_history:
                    if len(item) >= 3:
                        role, content = item[0], item[1]
                    else:
                        role, content = item[0], item[1]
                    summary_items.append({"role": str(role or ""), "content": str(content or "")})
                continuity_pack = await self._continuity_strategy.build_pack(
                    summary_items,
                    focus="Earlier dialogue before the recent session window.",
                    recent_window_messages=max_turns,
                )
                summary_text = await self._compression_engine._build_continuity_prompt_block_from_messages(
                    summary_items=summary_items,
                    continuity_pack=continuity_pack,
                    focus="Earlier dialogue before the recent session window.",
                    recent_window_messages=max_turns,
                )
                if summary_text:
                    summary_message = {
                        "role": "system",
                        "content": summary_text,
                    }
            history = history[-max_turns:]

        messages: list[dict[str, Any]] = []
        if summary_message is not None:
            messages.append(summary_message)
        # BUG-M03 Fix: Handle 3-element tuples (role, content, metadata)
        for item in history:
            if len(item) >= 3:
                role, content, metadata = item[0], item[1], item[2]
            else:
                role, content = item[0], item[1]
                metadata = {}
            stripped_content = self._reasoning_stripper.strip(str(content or "")).cleaned_text
            if str(role or "").strip().lower() == "tool":
                sanitized_content = stripped_content
            else:
                sanitized_content = self._security.sanitize_history_content(
                    stripped_content, detect_injection=self._config.detect_prompt_injection
                )
            msg = {"role": role, "content": sanitized_content}
            # BUG-M03 Fix: Preserve metadata if present
            if metadata:
                msg["metadata"] = metadata
            messages.append(msg)

        return messages

    def _get_project_structure(self) -> str | None:
        """Backward-compatible delegate to SignalSourceProvider.get_project_structure."""
        return self._signal_sources.get_project_structure()

    def _get_repo_identity(self) -> str | None:
        """Backward-compatible delegate to SignalSourceProvider.get_repo_identity."""
        return self._signal_sources.get_repo_identity()

    @staticmethod
    def _get_blueprint_step(request: Any) -> str | None:
        """Backward-compatible delegate to blueprint_step_card.build_blueprint_step_card."""
        return build_blueprint_step_card(request)

    def _get_scout_anchors(self) -> str | None:
        """Backward-compatible delegate to SignalSourceProvider.get_scout_anchors."""
        return self._signal_sources.get_scout_anchors()

    def _get_file_ownership(self) -> str | None:
        """Backward-compatible delegate to SignalSourceProvider.get_file_ownership."""
        return self._signal_sources.get_file_ownership()

    def _get_resident_agi_capabilities(self) -> str | None:
        """Backward-compatible delegate to SignalSourceProvider.get_resident_agi_capabilities."""
        return self._signal_sources.get_resident_agi_capabilities()

    def _get_resident_agi_decision_trace(self) -> str | None:
        """Backward-compatible delegate to SignalSourceProvider.get_resident_agi_decision_trace."""
        return self._signal_sources.get_resident_agi_decision_trace()

    def _get_task_history(self, task_id: str) -> str | None:
        """Backward-compatible delegate to SignalSourceProvider.get_task_history."""
        return self._signal_sources.get_task_history(task_id)

    def _get_blueprint_overview(self, task_id: str) -> str | None:
        """Backward-compatible delegate to SignalSourceProvider.get_blueprint_overview."""
        return self._signal_sources.get_blueprint_overview(task_id)

    def _estimate_signal_budget_pressure(self, projection: Any, request: ContextRequest) -> bool:
        """Backward-compatible delegate to SignalSourceProvider.estimate_signal_budget_pressure."""
        return self._signal_sources.estimate_signal_budget_pressure(projection, request)

    def _get_verdict_history(self, task_id: str) -> str | None:
        """Backward-compatible delegate to SignalSourceProvider.get_verdict_history."""
        return self._signal_sources.get_verdict_history(task_id)

    def _is_state_first_mode_active_from_receipt(self, receipt: StrategyReceipt | None) -> bool:
        """Determine if State-First Context OS mode is active based on strategy receipt."""
        if receipt is None:
            return False
        return bool(getattr(receipt, "compaction_triggered", False))

    def _extract_state_owners(self, request: ContextRequest) -> list[str]:
        """Extract StateOwner identifiers from the request."""
        state_owners: list[str] = []

        # Extract from history
        for item in request.history or []:
            if isinstance(item, dict):
                owner = item.get("state_owner") or item.get("owner")
                if owner:
                    state_owners.append(str(owner))
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                # Check metadata in 3-element tuples
                if len(item) >= 3 and isinstance(item[2], dict):
                    owner = item[2].get("state_owner") or item[2].get("owner")
                    if owner:
                        state_owners.append(str(owner))

        # Extract from context_os_snapshot
        if request.context_os_snapshot:
            snapshot = request.context_os_snapshot
            if isinstance(snapshot, dict):
                working_state = snapshot.get("working_state", {})
                if isinstance(working_state, dict):
                    owner = working_state.get("state_owner") or working_state.get("owner")
                    if owner:
                        state_owners.append(str(owner))

        return state_owners


__all__ = ["ContextGatewayConfig", "DuplicateStateOwnerError", "RoleContextGateway"]
