"""Role Context Gateway - 角色上下文网关

根据角色的上下文策略，差异化构建LLM上下文。

# -*- coding: utf-8 -*-
UTF-8 编码验证: 本文所有文本使用 UTF-8
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from polaris.kernelone.context.context_os import StateFirstContextOS
from polaris.kernelone.context.context_os.domain_adapters import get_context_domain_adapter
from polaris.kernelone.context.context_os.models import ContextOSSnapshot
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
        self._context_os = StateFirstContextOS(
            domain_adapter=get_context_domain_adapter(getattr(profile, "context_domain", None) or "generic"),
            provider_id=getattr(profile, "provider_id", None) or None,
            model=getattr(profile, "model", None) or None,
            workspace=str(self.workspace),
        )

        # Initialize collaborators
        self._token_estimator = TokenEstimator()
        self._security = SecuritySanitizer()
        self._projection_formatter = ProjectionFormatter()
        self._projection_engine = ProjectionEngine()
        self._compression_engine = CompressionEngine(
            max_context_tokens=self.policy.max_context_tokens,
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

    async def build_context(self, request: ContextRequest) -> ContextResult:
        """构建上下文

        Args:
            request: 上下文请求

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
            return await self._build_context_impl(request, start_time)
        finally:
            set_trace_id("")

    async def _build_context_impl(self, request: ContextRequest, start_time: float) -> ContextResult:
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

        _projection = None

        # Convert request.history to list[dict] for project()
        proj_input: list[dict[str, Any]] = []
        for item in request.history or []:
            if isinstance(item, dict):
                proj_input.append(item)
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                proj_input.append({"role": item[0], "content": item[1]})

        if has_snapshot:
            _snapshot = cast("dict[str, Any]", context_os_snapshot)
            snapshot = ContextOSSnapshot.from_mapping(_snapshot)
            _projection = await self._context_os.project(
                messages=proj_input,
                existing_snapshot=snapshot,
                recent_window_messages=self.policy.max_history_turns,
                focus=getattr(request, "focus", "") or "",
            )
            state_first_mode_active = True
        else:
            _projection = await self._context_os.project(
                messages=proj_input,
                existing_snapshot=None,
                recent_window_messages=self.policy.max_history_turns,
                focus=getattr(request, "focus", "") or "",
            )

        projection_dict, receipt_store, extra_sources = self._build_projection_dict(_projection, request)
        messages = list(self._projection_engine.project(projection_dict, receipt_store))
        sources.extend(extra_sources)
        if messages:
            sources.append(
                "state_first_context_os_projection" if has_snapshot else "state_first_context_os_initial_projection"
            )

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
                messages = self._compression_engine.emergency_truncate(
                    messages, max_tokens=self.policy.max_context_tokens
                )
                sources.append("budget_violation_emergency_truncate")

        # 6. 估算token数
        token_estimate = self._token_estimator.estimate(messages)
        original_token_estimate = token_estimate
        compression_applied = False

        # 7. 应用统一压缩策略
        if token_estimate > self.policy.max_context_tokens:
            if state_first_mode_active and has_snapshot:
                messages, token_estimate = self._compression_engine.emergency_truncate_with_limit(
                    messages, self.policy.max_context_tokens
                )
                compression_applied = token_estimate <= self.policy.max_context_tokens
            elif not state_first_mode_active:
                messages, token_estimate = self._compression_engine.apply_compression(messages, token_estimate)
                compression_applied = True

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
                "compression_applied": bool(compression_applied),
                "compression_strategy": str(self.policy.compression_strategy or "none"),
                "state_first_mode_active": bool(state_first_mode_active),
            },
        )

        # PR-11: Write context projection event with latency
        duration_ms = (time.monotonic() - start_time) * 1000

        projection_event = ContextEvent.create(
            EventType.CONTEXT_PROJECTION,
            duration_ms=duration_ms,
            metadata={
                "token_estimate": token_estimate,
                "compression_applied": compression_applied,
                "role": str(getattr(self.profile, "role_id", "") or ""),
            },
        )
        self._event_writer.write(projection_event)

        # Record latency metric
        record_metric(METRIC_CONTEXT_LATENCY_P95, duration_ms)

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
            },
        )

    def build_system_context(self, base_prompt: str, appendix: str | None = None) -> str:
        """构建系统上下文（提示词部分）

        Args:
            base_prompt: 基础系统提示词
            appendix: 追加提示词

        Returns:
            完整的系统提示词
        """
        parts = [base_prompt]

        # 追加提示词（仅追加，不覆盖）
        if appendix and self.policy.include_code_snippets:
            parts.append("\n\n【追加上下文】\n" + appendix)

        return "\n".join(parts)

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

        # 2. Add project structure info (if policy allows)
        if self.policy.include_project_structure:
            structure_info = self._get_project_structure()
            if structure_info:
                supplemental_turns.append(
                    {
                        "role": "system",
                        "content": f"【项目结构】\n{structure_info}",
                        "name": "project_structure",
                    }
                )
                sources.append("project_structure")

        # 3. Add task history (if policy allows and task_id is present)
        if self.policy.include_task_history and request.task_id:
            task_history = self._get_task_history(request.task_id)
            if task_history:
                supplemental_turns.append(
                    {
                        "role": "system",
                        "content": f"【任务历史】\n{task_history}",
                        "name": "task_history",
                    }
                )
                sources.append("task_history")

        # 4. Add Context OS state summary as supplemental system message (optional)
        if projection is not None and projection.snapshot is not None:
            proj_snapshot = projection.snapshot
            if proj_snapshot.artifact_store or proj_snapshot.pending_followup:
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
