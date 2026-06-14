"""Role Context Gateway - 角色上下文网关

根据角色的上下文策略，差异化构建LLM上下文。

# -*- coding: utf-8 -*-
UTF-8 编码验证: 本文所有文本使用 UTF-8
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Sequence, cast

from polaris.kernelone.context.context_os import StateFirstContextOS
from polaris.kernelone.context.context_os.domain_adapters import get_context_domain_adapter
from polaris.kernelone.context.context_os.models_v2 import ContextOSSnapshotV2 as ContextOSSnapshot
from polaris.kernelone.context.contracts import (
    TurnEngineContextRequest as ContextRequest,
    TurnEngineContextResult as ContextResult,
)
from polaris.kernelone.context.history_materialization import SessionContinuityStrategy
from polaris.kernelone.context.projection_engine import ProjectionEngine
from polaris.kernelone.context.receipt_store import ReceiptStore
from polaris.kernelone.events.context_events import ContextEvent, EventType, get_event_writer
from polaris.kernelone.fs import format_workspace_tree
from polaris.kernelone.llm.reasoning import ReasoningStripper
from polaris.kernelone.telemetry.debug_stream import emit_debug_event
from polaris.kernelone.telemetry.metrics import METRIC_CONTEXT_LATENCY_P95, record_metric
from polaris.kernelone.telemetry.trace import new_trace_id, set_trace_id

from .compression_engine import CompressionEngine
from .projection_formatter import ProjectionFormatter
from .security import SecuritySanitizer
from .token_estimator import TokenEstimator

if TYPE_CHECKING:
    from polaris.cells.roles.profile.public.service import RoleProfile
    from polaris.kernelone.context.strategy_contracts import StrategyReceipt

logger = logging.getLogger(__name__)

_CONTROL_PLANE_CONTEXT_KEYS = {
    "allowed_provider_ids",
    "allowed_provider_types",
    "blocked_provider_ids",
    "blocked_provider_types",
    "cognitive_runtime_enabled",
    "cognitive_runtime_mode",
    "cognitive_runtime_required",
    "context_os_expected",
    "context_os_snapshot",
    "cognitive_strategy_override",
    "domain",
    "factory_run_id",
    "host_kind",
    "llm_provider_policy",
    "metadata",
    "model_allowlist",
    "model_blocklist",
    "provider_allowlist",
    "provider_blocklist",
    "provider_policy",
    "role_runtime_required",
    "run_id",
    "runtime_session_id",
    "session_context_config",
    "session_id",
    "state_first_context_os",
    "strategy_override",
    "stream_options",
    "task_id",
    "workspace",
    "workspace_root",
}


def _copy_strategy_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _copy_strategy_value(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_copy_strategy_value(item) for item in value]
    return value


def _deep_merge_strategy_payload(target: dict[str, Any], source: Mapping[str, Any]) -> None:
    for key, value in source.items():
        key_text = str(key)
        if not key_text:
            continue
        existing = target.get(key_text)
        if isinstance(value, Mapping) and isinstance(existing, dict):
            _deep_merge_strategy_payload(existing, value)
            continue
        target[key_text] = _copy_strategy_value(value)


def _capability_profile_ref_from_request(request: ContextRequest) -> dict[str, Any] | None:
    context_override = getattr(request, "context_override", None)
    if not isinstance(context_override, Mapping):
        return None

    metadata = context_override.get("metadata")
    metadata_payload = metadata if isinstance(metadata, Mapping) else {}
    raw_profile = metadata_payload.get("capability_profile")
    if raw_profile is None:
        raw_profile = context_override.get("capability_profile")
    if not isinstance(raw_profile, Mapping):
        return None

    profile = {str(key): _copy_strategy_value(value) for key, value in raw_profile.items() if str(key)}
    digest = hashlib.sha256(
        json.dumps(
            profile,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()
    source = (
        str(profile.get("source") or "").strip()
        or str(metadata_payload.get("capability_profile_source") or "").strip()
        or "unknown"
    )
    return {
        "sha256": digest,
        "source": source,
        "schema_version": _coerce_int(profile.get("schema_version")),
        "role_id": str(profile.get("role_id") or "").strip(),
        "provider_id": str(profile.get("provider_id") or "").strip(),
        "provider_type": str(profile.get("provider_type") or "").strip(),
        "model": str(profile.get("model") or "").strip(),
        "model_window_tokens": _coerce_int(profile.get("model_window_tokens")),
        "model_output_limit_tokens": _coerce_int(profile.get("model_output_limit_tokens")),
        "supports_native_tools": bool(profile.get("supports_native_tools")),
        "supports_json_schema": bool(profile.get("supports_json_schema")),
        "supports_stream_native_tools": bool(profile.get("supports_stream_native_tools")),
        "tool_count": _coerce_int(profile.get("tool_count")),
        "native_tool_mode": str(profile.get("native_tool_mode") or "").strip(),
        "response_format_mode": str(profile.get("response_format_mode") or "").strip(),
    }


def _coerce_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def render_blueprint_overview(result: Any) -> str | None:
    """把蓝图状态结果渲染成简洁概览字符串（纯函数，便于单测）。

    result 期望具备 ok/summary/recommendations/risks 字段（duck-typed）。
    not ok 或无实质内容 → None（→ 不注入信号）。
    """
    if not getattr(result, "ok", False):
        return None
    parts: list[str] = []
    summary = str(getattr(result, "summary", "") or "").strip()
    if summary:
        parts.append(summary)
    recs = tuple(getattr(result, "recommendations", ()) or ())
    if recs:
        parts.append("推荐:\n" + "\n".join(f"- {r}" for r in recs))
    risks = tuple(getattr(result, "risks", ()) or ())
    if risks:
        parts.append("风险:\n" + "\n".join(f"- {k}" for k in risks))
    text = "\n".join(parts).strip()
    return text or None


def render_verdict_history(result: Any) -> str | None:
    """把 QA 判定结果渲染成简洁概览（纯函数，便于单测）。

    result 期望具备 ok/verdict/score/findings/suggestions（duck-typed）。
    not ok（无持久化判定）→ None（→ 不注入）。
    """
    if not getattr(result, "ok", False):
        return None
    verdict = str(getattr(result, "verdict", "") or "").strip()
    if not verdict:
        return None
    parts: list[str] = [f"最新判定: {verdict} (score={float(getattr(result, 'score', 0.0)):.2f})"]
    findings = tuple(getattr(result, "findings", ()) or ())
    if findings:
        parts.append("问题:\n" + "\n".join(f"- {f}" for f in findings))
    suggestions = tuple(getattr(result, "suggestions", ()) or ())
    if suggestions:
        parts.append("建议:\n" + "\n".join(f"- {s}" for s in suggestions))
    return "\n".join(parts).strip() or None


def _coerce_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


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
        TurnEngine/RoleExecution
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
        self._projection_formatter = ProjectionFormatter()
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
        enforcement_budget_tokens = max(
            min(self._MIN_ENFORCEMENT_BUDGET_TOKENS, self._enforcement_budget_tokens),
            self._enforcement_budget_tokens - reserved_system_prompt_tokens,
        )
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

        projection_dict, receipt_store, extra_sources = self._build_projection_dict(_projection, request)
        messages = list(self._projection_engine.project(projection_dict, receipt_store))
        sources.extend(extra_sources)
        if messages:
            sources.append(
                "state_first_context_os_projection" if has_snapshot else "state_first_context_os_initial_projection"
            )

        # ── Fallback: Include tool messages from history when not in state-first mode ──
        if not state_first_mode_active and not has_snapshot and request.history:
            history_tool_messages = self._extract_tool_messages_from_history(request.history)
            logger.debug(
                "[DEBUG][ContextGateway] fallback: state_first=%s has_snapshot=%s history_len=%d tool_msgs=%d",
                state_first_mode_active,
                has_snapshot,
                len(request.history) if request.history else 0,
                len(history_tool_messages),
            )
            if history_tool_messages:
                # Pre-process tool messages: truncate if too large, add CONTEXT_TRUNCATED marker
                processed_tool_messages = self._process_tool_messages_for_fallback(
                    history_tool_messages,
                    max_chars=2000,  # Allow some chars for the tool result
                )
                messages.extend(processed_tool_messages)
                sources.append("history_tool_fallback")

        # ── Context Override Processing with Prompt Injection Detection ──
        context_override = getattr(request, "context_override", None)
        if context_override and isinstance(context_override, dict):
            override_msg = self._process_context_override(context_override)
            if override_msg:
                messages.insert(0, override_msg)
                sources.append("context_override")

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
            if state_first_mode_active and has_snapshot:
                messages, token_estimate = self._compression_engine.emergency_truncate_with_limit(
                    messages, enforcement_budget_tokens
                )
                compression_applied = token_estimate <= enforcement_budget_tokens
            elif not state_first_mode_active:
                messages, token_estimate = self._compression_engine.apply_compression(messages, token_estimate)
                compression_applied = True

        # ADR-0090 I4.3: the role system prompt is prepended HERE, post-enforcement
        # and pre-budgeted — callers must not run a second projection to inject it.
        if system_prompt:
            messages.insert(0, {"role": "system", "content": system_prompt})
            sources.append("role_system_prompt")
            token_estimate += reserved_system_prompt_tokens

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

    def _process_context_override(self, context_override: dict[str, Any]) -> dict[str, Any] | None:
        """Process context_override dict with prompt injection detection.

        Args:
            context_override: Dict of context key-value pairs to inject.

        Returns:
            Message dict with filtered content, or None if empty.
        """
        if not context_override or not isinstance(context_override, dict):
            return None

        filtered_items: list[str] = []
        has_injection = False

        for key, value in context_override.items():
            if not isinstance(key, str):
                continue
            normalized_key = key.strip().lower()
            if normalized_key.startswith("_") or normalized_key in _CONTROL_PLANE_CONTEXT_KEYS:
                continue

            str_value = str(value) if value is not None else ""

            # Detect prompt injection patterns
            if self._config.detect_prompt_injection:
                if SecuritySanitizer.looks_like_prompt_injection(str_value):
                    # Degrade, don't destroy: platform-internal payloads
                    # (cognitive_guidance, session_turn_events) routinely
                    # contain instruction-like text and were being replaced
                    # wholesale with a [FILTERED] stub, deleting the model's
                    # own strategy guidance every turn (factory-bench L2-10
                    # live). Same neutralization contract as history content:
                    # escape + mark untrusted, keep the information.
                    has_injection = True
                    neutralized = SecuritySanitizer.sanitize_history_content(str_value, detect_injection=True)
                    filtered_items.append(f"{key}: {neutralized}")
                    logger.warning("Prompt injection detected in context_override key: %s", key)
                    continue

                # Check for dangerous key names
                if any(d in key.lower() for d in ("system", "role", "ignore", "override")):
                    has_injection = True
                    filtered_items.append(f"{key}: [FILTERED_SUSPICIOUS_KEY]")
                    logger.warning("Suspicious context_override key: %s", key)
                    continue

            filtered_items.append(f"{key}: {str_value}")

        if not filtered_items:
            return None

        content = "\n".join(filtered_items)
        if has_injection:
            content = "⚠️ CONTEXT_OVERRIDE_WITH_FILTERED_CONTENT:\n" + content

        return {"role": "system", "name": "context_override", "content": content}

    def _extract_tool_messages_from_history(
        self, history: Sequence[tuple[str, str] | dict[str, str]]
    ) -> list[dict[str, Any]]:
        """Extract tool messages from history for fallback when state-first mode is inactive.

        Args:
            history: List of (role, content) tuples or dict messages.

        Returns:
            List of tool message dicts.
        """
        tool_messages: list[dict[str, Any]] = []
        for item in history:
            if isinstance(item, dict):
                role = str(item.get("role", "")).lower()
                content = item.get("content", "")
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                role = str(item[0]).lower()
                content = str(item[1])
            else:
                continue

            if role == "tool":
                tool_messages.append({"role": "tool", "content": content})
        return tool_messages

    def _process_tool_messages_for_fallback(
        self,
        tool_messages: list[dict[str, Any]],
        max_chars: int = 2000,
    ) -> list[dict[str, Any]]:
        """Process tool messages for fallback, truncating large content.

        Args:
            tool_messages: List of tool message dicts.
            max_chars: Maximum characters before truncation.

        Returns:
            List of processed tool messages with CONTEXT_TRUNCATED markers if needed.
        """
        processed: list[dict[str, Any]] = []
        for msg in tool_messages:
            content = str(msg.get("content", ""))
            if len(content) > max_chars:
                truncated_content = content[:max_chars]
                new_content = (
                    f"{truncated_content}\n\n"
                    f"[CONTEXT_TRUNCATED: Original {len(content)} chars, truncated to {max_chars} chars]"
                )
                processed.append({"role": "tool", "content": new_content})
            else:
                # Always return a copy to avoid mutation of the original object
                processed.append({"role": msg.get("role", "tool"), "content": content})
        return processed

    def _build_projection_dict(
        self,
        projection: Any,
        request: ContextRequest,
    ) -> tuple[dict[str, Any], ReceiptStore, list[str]]:
        """Build a ProjectionEngine-compatible dict from a ContextOSProjection.

        Large tool outputs are stored in a ReceiptStore and referenced via
        receipt_refs instead of being inlined into message content.
        All supplemental context (project structure, task history, snapshot,
        strategy receipt, user message) is folded into the projection dict so
        that message generation is fully owned by ProjectionEngine.
        """
        receipt_store = ReceiptStore(workspace=str(self.workspace))
        sources: list[str] = []
        sorted_events = ProjectionFormatter.sort_events_by_routing_priority(projection.active_window)

        supplemental_turns: list[dict[str, Any]] = []

        # 2+3. Role-scoped signal plane（泛化自原硬编码的 project_structure / task_history）。
        # 由 RoleSignalRegistry 按角色 + context_policy 解析适用信号，再分配进 supplemental_turns。
        # 这里以"无上限/无压力"调用 → 与旧实现逐字节一致（seed 信号永不被卸载/丢弃）；
        # 预算/熔断机制由后续 signal 与 CompressionEngine 协同时再启用。
        from .role_signal_freshness import (
            get_previous_freshness,
            record_injected_freshness,
        )
        from .role_signals import (
            DEFAULT_PER_SIGNAL_CHAR_CAP,
            DEFAULT_TOTAL_CHAR_BUDGET,
            RoleSignalRegistry,
            SignalBuildContext,
            allocate_role_signals,
        )

        signal_ctx = SignalBuildContext(
            role=str(getattr(self.profile, "role_id", "") or ""),
            phase="",
            task_id=str(request.task_id or ""),
            policy_flags={
                "include_project_structure": bool(self.policy.include_project_structure),
                "include_task_history": bool(self.policy.include_task_history),
                # 角色专属信号开关（默认 False；按角色 profile opt-in）。
                "include_blueprint_overview": bool(getattr(self.policy, "include_blueprint_overview", False)),
                "include_verdict_history": bool(getattr(self.policy, "include_verdict_history", False)),
                # 仓库身份卡（Phase-1 B1）：seed 级反幻觉 grounding,默认开启。
                "include_repo_identity": bool(getattr(self.policy, "include_repo_identity", True)),
                # 侦察锚点卡（Phase-2 A7）：scout 定位持久化,默认开启。
                "include_scout_anchors": bool(getattr(self.policy, "include_scout_anchors", True)),
                # 施工步骤蓝图（三层裂变 I2）：弱执行者局部上帝视角,默认开启。
                "include_blueprint_step": bool(getattr(self.policy, "include_blueprint_step", True)),
            },
            get_project_structure=self._get_project_structure,
            get_task_history=self._get_task_history,
            get_repo_identity=self._get_repo_identity,
            get_scout_anchors=self._get_scout_anchors,
            # blueprint_overview 只通过配置注入的数据源读取；roles.kernel 不认识
            # chief_engineer.blueprint 的业务模块。
            get_blueprint_overview=lambda: self._get_blueprint_overview(str(request.task_id or "")),
            # verdict_history 同样只走配置注入的数据源，避免 kernel 反向依赖 QA owner Cell。
            get_verdict_history=lambda: self._get_verdict_history(str(request.task_id or "")),
            get_blueprint_step=lambda: self._get_blueprint_step(request),
        )
        # 跨 turn freshness 记忆（按 task_id）：压力下断流"自上次注入未变化"的 nice-to-have，
        # 把窗口让给即时工具结果。无压力时 budget_pressure=False → 不断流 → 与旧实现逐字节一致。
        _cache_key = str(request.task_id or "")
        _budget_pressure = self._estimate_signal_budget_pressure(projection, request)
        # ADR-0090 W2.4: scale signal caps to the enforcement budget so seed
        # signals (【项目结构】/【任务历史】) cannot eat a small model's window.
        # ≈3 chars/token; per-signal ≈5% and total ≈15% of the enforcement
        # budget, ceilinged at the registry defaults (large windows unchanged).
        _signal_char_equiv = self._enforcement_budget_tokens * 3
        _per_signal_cap = min(DEFAULT_PER_SIGNAL_CHAR_CAP, max(1000, int(_signal_char_equiv * 0.05)))
        _total_signal_budget = min(DEFAULT_TOTAL_CHAR_BUDGET, max(3000, int(_signal_char_equiv * 0.15)))
        _signal_alloc = allocate_role_signals(
            registry=RoleSignalRegistry(),
            ctx=signal_ctx,
            receipt_store=receipt_store,
            budget_pressure=_budget_pressure,
            previous_freshness=get_previous_freshness(_cache_key),
            per_signal_char_cap=_per_signal_cap,
            total_char_budget=_total_signal_budget,
        )
        supplemental_turns.extend(_signal_alloc.turns)
        sources.extend(_signal_alloc.sources)
        # 记住本 turn 实际注入信号的 freshness，供下一 turn 的熔断判断。
        _injected_fresh = _signal_alloc.telemetry.get("injected_freshness")
        if isinstance(_injected_fresh, dict) and _injected_fresh:
            record_injected_freshness(_cache_key, _injected_fresh)

        # 4. Add Context OS state summary as supplemental system message (optional)
        if projection is not None and projection.snapshot is not None:
            proj_snapshot = projection.snapshot
            _has_artifacts = bool(getattr(proj_snapshot, "artifact_store", ()))
            _has_pending = bool(getattr(proj_snapshot, "pending_followup", None))
            if _has_artifacts or _has_pending:
                from polaris.kernelone.context.context_os.models import SnapshotSummaryView

                summary_dict = SnapshotSummaryView.from_snapshot(proj_snapshot)
                snapshot_summary = self._projection_formatter.format_context_os_snapshot(summary_dict)
                supplemental_turns.append(
                    {
                        "role": "system",
                        "content": snapshot_summary,
                        "name": "context_os_snapshot_detail",
                    }
                )
                sources.append("context_os_snapshot_detail")
        else:
            strategy_receipt = request.strategy_receipt
            if strategy_receipt is not None:
                receipt_content = self._projection_formatter.format_strategy_receipt_style(strategy_receipt)
                supplemental_turns.append(
                    {
                        "role": "system",
                        "content": receipt_content,
                        "name": "strategy_receipt",
                    }
                )
                sources.append("strategy_receipt")

        # 5. Add user message
        user_message = self._security.sanitize_user_message(
            request.message, detect_injection=self._config.detect_prompt_injection
        )

        proj_dict = self._projection_engine.build_payload(
            active_window=sorted_events,
            receipt_store=receipt_store,
            head_anchor=projection.head_anchor,
            tail_anchor=projection.tail_anchor,
            run_card=projection.run_card,
            supplemental_turns=supplemental_turns,
            user_message=user_message,
        )
        logger.debug(
            "[DEBUG][ContextGateway] _build_projection_dict: active_window=%d supplemental=%d sources=%s head_len=%d tail_len=%d",
            len(sorted_events),
            len(supplemental_turns),
            sources,
            len(projection.head_anchor),
            len(projection.tail_anchor),
        )
        return proj_dict, receipt_store, sources

    def _messages_from_projection(self, projection: Any) -> list[dict[str, Any]]:
        """Backward-compatible delegate to ProjectionFormatter.messages_from_projection."""
        return self._projection_formatter.messages_from_projection(projection)

    def _estimate_tokens(self, messages: list[dict[str, str]]) -> int:
        """Backward-compatible delegate to TokenEstimator.estimate."""
        return self._token_estimator.estimate(messages)

    def _apply_compression(
        self,
        messages: list[dict[str, str]],
        current_tokens: int,
    ) -> tuple[list[dict[str, str]], int]:
        """Backward-compatible delegate to CompressionEngine.apply_compression."""
        return self._compression_engine.apply_compression(messages, current_tokens)

    def _emergency_fallback(self, messages: list[dict[str, str]]) -> tuple[list[dict[str, str]], int]:
        """Backward-compatible delegate to CompressionEngine.emergency_fallback."""
        return self._compression_engine.emergency_fallback(messages)

    def _format_context_os_snapshot(
        self,
        snapshot: dict[str, Any],
        verbosity: str = "summary",
    ) -> str:
        """Backward-compatible delegate to ProjectionFormatter.format_context_os_snapshot."""
        return ProjectionFormatter.format_context_os_snapshot(snapshot, verbosity=verbosity)

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
        """获取项目结构信息

        使用标准树状格式 (tree characters) 来明确表示层级关系，
        避免 LLM 将平铺列表误读为层级结构导致路径幻觉。
        """
        try:
            return format_workspace_tree(
                self.workspace,
                max_dirs=20,
                max_files=10,
                max_sub_items=5,
                exclude_hidden=True,
                exclude_dirs=(".github", ".vscode", "__pycache__", ".git"),
            )
        except (RuntimeError, ValueError) as e:
            logger.warning(f"获取项目结构失败: {e}")
            return None

    def _get_repo_identity(self) -> str | None:
        """仓库身份卡（确定性反幻觉 grounding,Phase-1 B1）。"""
        try:
            from .repo_identity import build_repo_identity_card

            return build_repo_identity_card(str(self.workspace))
        except (RuntimeError, ValueError, OSError) as e:
            logger.debug(f"仓库身份卡构建失败: {e}")
            return None

    @staticmethod
    def _get_blueprint_step(request: Any) -> str | None:
        """施工步骤卡（三层裂变 I2）：从请求上下文读取 construction_step。

        有界注入：签名骨架 + 接口定名 + verify 判据 + 行数预算。严禁全文
        （16k 窗口实证 W1.5c 后可用提示仅 ~5-6k tokens）。数据由 director
        消费路径放入 context_override（市场 leaf 步任务的 payload 字段）。
        """
        context_override = getattr(request, "context_override", None)
        if not isinstance(context_override, dict):
            return None
        step = context_override.get("construction_step")
        if not isinstance(step, dict):
            return None
        lines: list[str] = []
        step_id = str(step.get("step_id") or "").strip()
        target_file = str(step.get("target_file") or "").strip()
        if step_id or target_file:
            est = step.get("est_lines")
            lines.append(f"step {step_id}: {target_file}" + (f" (≤{est}行)" if est else ""))
        signatures = [str(s).strip() for s in (step.get("signatures") or []) if str(s).strip()]
        if signatures:
            lines.append("signatures: " + "; ".join(signatures[:12]))
        # Skeleton step (I3-r30): without this the weak model tries to fully implement
        # every signature in one write and truncates (finish_reason=length). Force
        # minimal empty stubs only — the fill steps implement the bodies later.
        if step.get("skeleton_stub_only"):
            lines.append(
                "[骨架步·只写空桩] 本步只为上述每个签名写一个最小空函数体"
                "（JS: `function 名(){}`；Python: `def 名(): pass`），"
                "严禁实现任何逻辑、严禁填写函数体内容——逻辑由后续填充步逐个补。"
                "一次 write_file 写完全部空桩即可，务必极简以一轮落盘"
                "（试图实现整套逻辑会超出输出预算被截断、导致本步零落盘失败）。"
            )
        # Fill step (I3-r31): without this the weak model tries to implement the WHOLE
        # file at once (truncates) or stuffs prose into edit_blocks. Force a bounded,
        # code-only, anchored edit of ONLY this fill's assigned functions.
        if step.get("fill_scope_only"):
            lines.append(
                "[填充步·只实现被分配的函数] 本步只实现上面 signatures 列出的这几个函数/方法的函数体，"
                "其它桩一律别动、也别实现。做法：先 read_file 看到这几个函数当前的空桩原文，"
                "再用 edit_blocks 对每个函数各做一次 SEARCH/REPLACE"
                "（SEARCH=空桩原文逐字照抄，REPLACE=实现后的完整函数）。"
                "edit_blocks 的 blocks/replace 参数里只放纯代码——严禁任何说明/计划/意图文字"
                "（例如 'replace lines 1-46 with full implementation' 是错的，会被工具拒收）。"
                "严禁 write_file 整文件重写、严禁实现未分配的函数（一次实现整个文件会超预算截断、零落盘）。"
            )
        interfaces = [str(s).strip() for s in (step.get("interface_names") or []) if str(s).strip()]
        if interfaces:
            lines.append("interfaces: " + ", ".join(interfaces[:16]))
        # Interface coherence (I3-r28): the frozen identifiers OTHER files already
        # exposed. The weak Director must reuse these exact names so cross-file refs
        # resolve at runtime (live: main.js must call the id index.html froze, not
        # invent its own). Bounded to a few files/names to respect the 16k window.
        consumed = context_override.get("consumed_interfaces")
        if isinstance(consumed, dict) and consumed:
            consumed_lines: list[str] = []
            for other_target in sorted(consumed)[:6]:
                entry = consumed.get(other_target)
                if not isinstance(entry, dict):
                    continue
                names = [str(n).strip() for n in (entry.get("identifiers") or []) if str(n).strip()][:10]
                if names:
                    consumed_lines.append(f"  {other_target} 已公开: {', '.join(names)}")
            if consumed_lines:
                lines.append("跨文件接口(必须复用完全相同的名字，勿自创):")
                lines.extend(consumed_lines)
        verify = str(step.get("verify") or "").strip()
        if verify:
            lines.append(f"verify: {verify}")
        depends = [str(s).strip() for s in (step.get("depends_on") or []) if str(s).strip()]
        if depends:
            lines.append("depends_on(已完成): " + ", ".join(depends[:8]))
        # Fix-13 缺陷清单（punch list）：改建式步骤的现状勘察。没有它，
        # 弱执行者读到看似完整的目标文件会判定"已完成"拒绝动笔
        # （live I3-r13: 编辑模式 0/5，三次重试全零 diff）。
        pre_state = context_override.get("pre_state_verify")
        if isinstance(pre_state, dict):
            failing = [str(c).strip() for c in (pre_state.get("failing_clauses") or []) if str(c).strip()]
            total = pre_state.get("total_clauses")
            if pre_state.get("exit_code") == 0:
                lines.append(
                    "现状勘察: 验收判据当前已通过（可能由前置步骤满足）。仍须按本步合同产生实际改进；不产生任何文件变更将被拒收。"
                )
            elif failing:
                lines.append(
                    f"现状勘察(缺陷清单): 目标文件当前未通过验收，缺 {len(failing)}/{total} 项。你的任务就是补齐下列各项:"
                )
                for index, clause in enumerate(failing[:8], 1):
                    lines.append(f"  缺{index}: {clause[:160]}")
                lines.append("文件已存在不等于任务完成；必须实际修改文件使上述各项全部通过。")
            else:
                lines.append("现状勘察: 验收判据当前未通过。必须实际修改目标文件使 verify 通过；不产生变更将被拒收。")
        failure = context_override.get("last_failure")
        if isinstance(failure, dict):
            failure_message = str(failure.get("error_message") or "").strip()
            failure_code = str(failure.get("error_code") or "").strip()
            if failure_message or failure_code:
                lines.append(f"上次尝试失败({failure_code}): {failure_message[:240]}")
                # R7-B (I3-r28, Self-Refine): the prose "don't rewrite" hint empirically
                # fails — the weak model rewrites the file smaller anyway. Replace it with
                # an imperative, format-specific directive that names the anchored edit verb
                # and warns about the deterministic shrink gate (R7-C) that will reject a
                # degraded rewrite.
                lines.append(
                    "[修复轮·只做定点编辑] 目标文件已存在且是可用代码，只因上述原因失败。"
                    "只修这一处：用 edit_blocks 发 SEARCH/REPLACE 块（或 file+start+end+replace 行区间编辑），"
                    "严禁用 write_file 整文件重写。保留所有既有函数/类/逻辑，不得缩短或删除既有内容——"
                    "任何使文件明显变小或丢失既有功能的回应都会被自动拒收并退回重做。"
                )
        return "\n".join(lines) or None

    def _get_scout_anchors(self) -> str | None:
        """侦察锚点卡（Phase-2 A7 定位持久化）。"""
        try:
            from polaris.kernelone.context.scout_anchor_store import (
                format_anchor_card,
                load_scout_anchors,
            )

            return format_anchor_card(load_scout_anchors(str(self.workspace)))
        except (RuntimeError, ValueError, OSError) as e:
            logger.debug(f"侦察锚点卡构建失败: {e}")
            return None

    def _get_task_history(self, task_id: str) -> str | None:
        """获取任务历史"""
        try:
            from polaris.cells.runtime.task_runtime.public.service import TaskRuntimeService

            task = TaskRuntimeService(str(self.workspace)).get_task(task_id)

            if not task:
                return None

            # 格式化任务信息
            history = [
                f"任务ID: {task_id}",
                f"状态: {task.get('status', 'unknown')}",
                f"标题: {task.get('subject', 'N/A')}",
            ]

            if task.get("description"):
                desc = task.get("description")
                if isinstance(desc, str):
                    history.append(f"描述: {desc[:200]}...")
                else:
                    history.append(f"描述: {desc}...")

            return "\n".join(history)

        except (RuntimeError, ValueError) as e:
            logger.debug(f"获取任务历史失败: {e}")
            return None

    def _get_blueprint_overview(self, task_id: str) -> str | None:
        """读取本任务最新蓝图概览（ChiefEngineer 角色专属信号的数据源）。

        数据源由运行时/适配层通过 ContextGatewayConfig 注入。roles.kernel 只负责编排
        signal 与渲染，不直接 import chief_engineer.blueprint。任何失败/缺失 → None。
        """
        if not task_id:
            return None
        provider = self._config.blueprint_overview_provider
        if provider is None:
            return None
        try:
            result = provider(task_id, str(self.workspace))
            if isinstance(result, str):
                return result.strip() or None
            return render_blueprint_overview(result)
        except Exception as exc:  # noqa: BLE001 - 数据源失败必须优雅降级为不注入
            logger.debug(f"获取蓝图概览失败: {exc}")
            return None

    def _estimate_signal_budget_pressure(self, projection: Any, request: ContextRequest) -> bool:
        """保守预估"角色信号分配阶段是否已处于预算压力"。

        在真正组装/压缩前给一个便宜的早期信号：若历史窗口的估算 token 已超过
        ``max_context_tokens * trigger_pct``，视为压力 → 允许熔断断流未变化的 nice-to-have。
        任何失败 → False（不熔断 → 保持逐字节一致，安全优先）。
        """
        try:
            strategy_override = request.strategy_override or {}
            trigger_pct = self._context_budget_trigger_pct(strategy_override)
            threshold = max(1, int(self.policy.max_context_tokens * trigger_pct))
            history_messages = [
                {"role": str(role), "content": str(content)} for role, content in (request.history or ()) if content
            ]
            if not history_messages:
                return False
            estimate = self._token_estimator.estimate(history_messages)
            return int(estimate) > threshold
        except Exception:  # noqa: BLE001 - 预估失败必须安全降级为"无压力"
            logger.debug("signal budget pressure estimate failed", exc_info=True)
            return False

    def _get_verdict_history(self, task_id: str) -> str | None:
        """读取本任务最新 QA 判定历史（QA 角色专属信号的数据源）。

        数据源由运行时/适配层通过 ContextGatewayConfig 注入。roles.kernel 只负责编排
        signal 与渲染，不直接 import qa.audit_verdict。任何失败/缺失 → None。
        """
        if not task_id:
            return None
        provider = self._config.verdict_history_provider
        if provider is None:
            return None
        try:
            result = provider(task_id, str(self.workspace))
            if isinstance(result, str):
                return result.strip() or None
            return render_verdict_history(result)
        except Exception as exc:  # noqa: BLE001 - 数据源失败必须优雅降级为不注入
            logger.debug(f"获取判定历史失败: {exc}")
            return None

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
