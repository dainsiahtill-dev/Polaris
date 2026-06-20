"""Role-signal data-source readers for :class:`RoleContextGateway`.

Extracted (behavior-preserving) from ``gateway.py`` during the G8 god-class
decomposition (blueprint REMAINING_04_gateway-py.md, step 3). These are the
signal data-source readers the gateway hands to ``allocate_role_signals`` plus
the cheap pre-assembly budget-pressure estimate.

The kernel owns signal assembly but NOT business asset lookup: ``blueprint`` /
``verdict`` data sources arrive only through ``ContextGatewayConfig`` provider
callbacks injected by runtime/adapters, so ``roles.kernel`` never imports the
``chief_engineer.blueprint`` / ``qa.audit_verdict`` owner cells. The in-method
lazy imports for ``TaskRuntimeService`` / ``scout_anchor_store`` /
``repo_identity`` and their ``try/except`` graceful-degradation are LOAD-BEARING
(circular-import + cross-cell ACGA direction) and preserved verbatim.

# -*- coding: utf-8 -*-
UTF-8 编码验证: 本文所有文本使用 UTF-8
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from polaris.kernelone.context.contracts import TurnEngineContextRequest as ContextRequest
from polaris.kernelone.fs import format_workspace_tree

from .gateway_helpers import render_blueprint_overview, render_verdict_history

if TYPE_CHECKING:
    from .gateway import ContextGatewayConfig
    from .token_estimator import TokenEstimator

logger = logging.getLogger(__name__)


class SignalSourceProvider:
    """Resolves the per-role signal data sources for one gateway.

    Holds the gateway's ``workspace`` / ``config`` / ``policy`` /
    ``token_estimator`` plus the gateway's ``context_budget_trigger_pct``
    computation (passed as a callable so the budget knob stays single-sourced on
    the gateway). Every reader degrades to ``None`` / ``False`` on failure so a
    missing data source never breaks a turn.
    """

    def __init__(
        self,
        *,
        workspace: Path,
        config: ContextGatewayConfig,
        policy: Any,
        token_estimator: TokenEstimator,
        trigger_pct_resolver: Callable[[Any], float],
    ) -> None:
        self._workspace = workspace
        self._config = config
        self._policy = policy
        self._token_estimator = token_estimator
        self._trigger_pct_resolver = trigger_pct_resolver

    def get_project_structure(self) -> str | None:
        """获取项目结构信息

        使用标准树状格式 (tree characters) 来明确表示层级关系，
        避免 LLM 将平铺列表误读为层级结构导致路径幻觉。
        """
        try:
            return format_workspace_tree(
                self._workspace,
                max_dirs=20,
                max_files=10,
                max_sub_items=5,
                exclude_hidden=True,
                exclude_dirs=(".github", ".vscode", "__pycache__", ".git"),
            )
        except (RuntimeError, ValueError) as e:
            logger.warning(f"获取项目结构失败: {e}")
            return None

    def get_repo_identity(self) -> str | None:
        """仓库身份卡（确定性反幻觉 grounding,Phase-1 B1）。"""
        try:
            from .repo_identity import build_repo_identity_card

            return build_repo_identity_card(str(self._workspace))
        except (RuntimeError, ValueError, OSError) as e:
            logger.debug(f"仓库身份卡构建失败: {e}")
            return None

    def get_scout_anchors(self) -> str | None:
        """侦察锚点卡（Phase-2 A7 定位持久化）。"""
        try:
            from polaris.kernelone.context.scout_anchor_store import (
                format_anchor_card,
                load_scout_anchors,
            )

            return format_anchor_card(load_scout_anchors(str(self._workspace)))
        except (RuntimeError, ValueError, OSError) as e:
            logger.debug(f"侦察锚点卡构建失败: {e}")
            return None

    def get_task_history(self, task_id: str) -> str | None:
        """获取任务历史"""
        try:
            from polaris.cells.runtime.task_runtime.public.service import TaskRuntimeService

            task = TaskRuntimeService(str(self._workspace)).get_task(task_id)

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

    def get_blueprint_overview(self, task_id: str) -> str | None:
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
            result = provider(task_id, str(self._workspace))
            if isinstance(result, str):
                return result.strip() or None
            return render_blueprint_overview(result)
        except Exception as exc:  # noqa: BLE001 - 数据源失败必须优雅降级为不注入
            logger.debug(f"获取蓝图概览失败: {exc}")
            return None

    def get_verdict_history(self, task_id: str) -> str | None:
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
            result = provider(task_id, str(self._workspace))
            if isinstance(result, str):
                return result.strip() or None
            return render_verdict_history(result)
        except Exception as exc:  # noqa: BLE001 - 数据源失败必须优雅降级为不注入
            logger.debug(f"获取判定历史失败: {exc}")
            return None

    def estimate_signal_budget_pressure(self, projection: Any, request: ContextRequest) -> bool:
        """保守预估"角色信号分配阶段是否已处于预算压力"。

        在真正组装/压缩前给一个便宜的早期信号：若历史窗口的估算 token 已超过
        ``max_context_tokens * trigger_pct``，视为压力 → 允许熔断断流未变化的 nice-to-have。
        任何失败 → False（不熔断 → 保持逐字节一致，安全优先）。
        """
        try:
            strategy_override = request.strategy_override or {}
            trigger_pct = self._trigger_pct_resolver(strategy_override)
            threshold = max(1, int(self._policy.max_context_tokens * trigger_pct))
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
