"""ProjectionEngine-payload assembly for :class:`RoleContextGateway`.

Extracted (behavior-preserving) from ``gateway.py`` during the G8 god-class
decomposition (blueprint REMAINING_04_gateway-py.md, step 7). The gateway keeps a
thin delegating shim ``_build_projection_dict`` (a frozen test reach-in).

The builder reads the gateway's collaborators/state through a back-reference
because this method folds together six signal readers, the budget-pressure
estimate, the projection formatter, the security sanitizer and the projection
engine — passing each one individually would just reconstruct the gateway. The
three in-method lazy imports (``make_offload_capture`` /
``role_signal_freshness`` / ``role_signals`` / ``SnapshotSummaryView``) are
LOAD-BEARING (CCR producer-loop / circular-import avoidance) and preserved
verbatim.

# -*- coding: utf-8 -*-
UTF-8 编码验证: 本文所有文本使用 UTF-8
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from polaris.kernelone.context.contracts import TurnEngineContextRequest as ContextRequest
from polaris.kernelone.context.receipt_store import ReceiptStore

from .projection_formatter import ProjectionFormatter

if TYPE_CHECKING:
    from .gateway import RoleContextGateway

logger = logging.getLogger(__name__)


class ProjectionDictBuilder:
    """Builds a ProjectionEngine-compatible payload from a ContextOSProjection.

    Holds a back-reference to the owning gateway so it can reach the role-signal
    readers, budget knobs and shared collaborators without duplicating them.
    """

    def __init__(self, gateway: RoleContextGateway) -> None:
        self._gateway = gateway

    def build(
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
        gateway = self._gateway
        # CCR producer loop closure (T1-A): mirror every offloaded original into
        # the process CCR cache so a later context_retrieve resolves the same
        # [receipt_ref:ID] pointer the model sees. The hook is workspace-scoped
        # (make_offload_capture) so concurrent workspaces sharing this process do
        # NOT cross-resolve each other's payloads. Floor-safe: best-effort and
        # changes no prompt text, so the L2 success path is intact.
        from polaris.kernelone.llm.toolkit.original_payload_cache import make_offload_capture

        receipt_store = ReceiptStore(
            workspace=str(gateway.workspace),
            on_offload=make_offload_capture(str(gateway.workspace)),
        )
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
            role=str(getattr(gateway.profile, "role_id", "") or ""),
            phase="",
            task_id=str(request.task_id or ""),
            policy_flags={
                "include_project_structure": bool(gateway.policy.include_project_structure),
                "include_task_history": bool(gateway.policy.include_task_history),
                # 角色专属信号开关（默认 False；按角色 profile opt-in）。
                "include_blueprint_overview": bool(getattr(gateway.policy, "include_blueprint_overview", False)),
                "include_verdict_history": bool(getattr(gateway.policy, "include_verdict_history", False)),
                # 仓库身份卡（Phase-1 B1）：seed 级反幻觉 grounding,默认开启。
                "include_repo_identity": bool(getattr(gateway.policy, "include_repo_identity", True)),
                # 侦察锚点卡（Phase-2 A7）：scout 定位持久化,默认开启。
                "include_scout_anchors": bool(getattr(gateway.policy, "include_scout_anchors", True)),
                # 施工步骤蓝图（三层裂变 I2）：弱执行者局部上帝视角,默认开启。
                "include_blueprint_step": bool(getattr(gateway.policy, "include_blueprint_step", True)),
                # 文件归属信号（D-11）：并行 Director 文件修改历史,默认开启。
                "include_file_ownership": bool(getattr(gateway.policy, "include_file_ownership", True)),
            },
            get_project_structure=gateway._get_project_structure,
            get_task_history=gateway._get_task_history,
            get_repo_identity=gateway._get_repo_identity,
            get_scout_anchors=gateway._get_scout_anchors,
            # blueprint_overview 只通过配置注入的数据源读取；roles.kernel 不认识
            # chief_engineer.blueprint 的业务模块。
            get_blueprint_overview=lambda: gateway._get_blueprint_overview(str(request.task_id or "")),
            # verdict_history 同样只走配置注入的数据源，避免 kernel 反向依赖 QA owner Cell。
            get_verdict_history=lambda: gateway._get_verdict_history(str(request.task_id or "")),
            get_blueprint_step=lambda: gateway._get_blueprint_step(request),
            # file_ownership 读取 workspace 内的 file-edits/events.jsonl（D-11）。
            get_file_ownership=gateway._get_file_ownership,
        )
        # 跨 turn freshness 记忆（按 task_id）：压力下断流"自上次注入未变化"的 nice-to-have，
        # 把窗口让给即时工具结果。无压力时 budget_pressure=False → 不断流 → 与旧实现逐字节一致。
        _cache_key = str(request.task_id or "")
        _budget_pressure = gateway._estimate_signal_budget_pressure(projection, request)
        # ADR-0090 W2.4: scale signal caps to the enforcement budget so seed
        # signals (【项目结构】/【任务历史】) cannot eat a small model's window.
        # ≈3 chars/token; per-signal ≈5% and total ≈15% of the enforcement
        # budget, ceilinged at the registry defaults (large windows unchanged).
        _signal_char_equiv = gateway._enforcement_budget_tokens * 3
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
                snapshot_summary = gateway._projection_formatter.format_context_os_snapshot(summary_dict)
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
                receipt_content = gateway._projection_formatter.format_strategy_receipt_style(strategy_receipt)
                supplemental_turns.append(
                    {
                        "role": "system",
                        "content": receipt_content,
                        "name": "strategy_receipt",
                    }
                )
                sources.append("strategy_receipt")

        # 5. Add user message
        user_message = gateway._security.sanitize_user_message(
            request.message, detect_injection=gateway._config.detect_prompt_injection
        )

        proj_dict = gateway._projection_engine.build_payload(
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
