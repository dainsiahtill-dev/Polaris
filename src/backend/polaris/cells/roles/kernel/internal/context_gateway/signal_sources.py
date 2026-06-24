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
from collections.abc import Callable, Mapping
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

            service = TaskRuntimeService(str(self._workspace))
            task = service.get_task(task_id)

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
                    history.append(f"描述: {desc[:1000]}")
                else:
                    history.append(f"描述: {desc}...")

            # D-13: 任务依赖图（上游/下游）
            dep_graph = self._build_dependency_graph(service, task)
            if dep_graph:
                history.append("")
                history.append(dep_graph)

            return "\n".join(history)

        except (RuntimeError, ValueError) as e:
            logger.debug(f"获取任务历史失败: {e}")
            return None

    def _build_dependency_graph(self, service: Any, task: dict[str, Any]) -> str | None:
        """构建任务依赖图（上游/下游）。"""
        task_id = task.get("id")
        if task_id is None:
            return None

        # 上游：blocked_by
        upstream_ids: list[int] = task.get("blocked_by") or task.get("blockedBy") or []

        # 下游：扫描所有任务找 blocked_by 含本 task_id 的
        downstream: list[dict[str, Any]] = []
        try:
            all_tasks = service.list_all()
            for other in all_tasks:
                other_dict = other.to_dict()
                other_blocked_by: list[int] = other_dict.get("blocked_by") or other_dict.get("blockedBy") or []
                if int(task_id) in other_blocked_by:
                    downstream.append(other_dict)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"获取下游任务失败: {e}")

        if not upstream_ids and not downstream:
            return None

        lines = ["【任务依赖图】"]

        # 渲染上游
        if upstream_ids:
            lines.append("上游（必须先完成）:")
            for dep_id in upstream_ids:
                dep_task = service.get_task(dep_id)
                if dep_task:
                    lines.append(f"  - {dep_id}: {dep_task.get('status', '?')} - {dep_task.get('subject', 'N/A')}")
                else:
                    lines.append(f"  - {dep_id}: (不存在)")
        else:
            lines.append("上游: 无")

        # 渲染下游
        if downstream:
            lines.append("下游（被本任务阻塞）:")
            for dep_task in downstream:
                lines.append(
                    f"  - {dep_task.get('id', '?')}: {dep_task.get('status', '?')} - {dep_task.get('subject', 'N/A')}"
                )
        else:
            lines.append("下游: 无")

        return "\n".join(lines)

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

    def get_file_ownership(self) -> str | None:
        """读取最近文件修改历史（D-11 并行 Director 文件归属信号的数据源）。

        从 workspace 的 .polaris/runtime/file-edits/events.jsonl 读取最近的写事件，
        提取文件路径和时间戳。任何失败/缺失 → None（不注入）。
        """
        try:
            import json

            event_file = self._workspace.resolve() / ".polaris" / "runtime" / "file-edits" / "events.jsonl"
            if not event_file.exists():
                return None

            # 读取最后 50 行（最近的事件）
            lines = event_file.read_text(encoding="utf-8").strip().splitlines()
            if not lines:
                return None

            recent_lines = lines[-50:] if len(lines) > 50 else lines

            # 解析事件并提取文件路径
            events: list[dict[str, Any]] = []
            for line in recent_lines:
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                    payload = event.get("payload") or {}
                    file_path = payload.get("file_path")
                    operation = payload.get("operation", "modify")
                    timestamp = payload.get("timestamp", "")
                    if file_path:
                        events.append(
                            {
                                "file_path": file_path,
                                "operation": operation,
                                "timestamp": timestamp,
                            }
                        )
                except (json.JSONDecodeError, KeyError, TypeError):
                    continue

            if not events:
                return None

            # 格式化为人类可读的列表
            rendered_lines = ["以下文件最近被其他 worker 修改（避免冲突）："]
            for event in events[-20:]:  # 最多显示 20 个
                op = event.get("operation", "modify")
                ts = event.get("timestamp", "")[:19]  # 截取到秒
                fp = event.get("file_path", "")
                rendered_lines.append(f"- [{op}] {fp} ({ts})")

            return "\n".join(rendered_lines)
        except (OSError, RuntimeError, ValueError, TypeError) as e:
            logger.debug(f"文件归属信号构建失败: {e}")
            return None

    def get_resident_agi_capabilities(self) -> str | None:
        """读取 Resident AGI 平台能力面（Role/ContextOS 同底座的决策契约）。

        优先使用 ``ContextGatewayConfig`` 注入的 provider，便于未来把能力面升级为
        由 Registry/AGI 自身审计模块动态产出；未注入时回退到 resident.autonomy 的
        canonical capability surface。任何失败/缺失 → None（不阻断非 AGI turn）。
        """
        try:
            result: Any | None = None
            provider = self._config.resident_agi_capability_provider
            if provider is not None:
                result = provider(str(self._workspace))
            if result is None:
                from polaris.cells.resident.autonomy.public.service import (
                    resident_agi_capability_surface_payload,
                )

                result = resident_agi_capability_surface_payload()
            if isinstance(result, str):
                return result.strip() or None
            if isinstance(result, Mapping):
                return self._render_resident_agi_capabilities(result)
            return None
        except (RuntimeError, ValueError, OSError, TypeError) as e:
            logger.debug(f"Resident AGI 能力面构建失败: {e}")
            return None

    @staticmethod
    def _render_resident_agi_capabilities(payload: Mapping[str, Any]) -> str | None:
        """Render the AGI capability surface into a compact role-signal card."""
        items_obj = payload.get("items")
        if not isinstance(items_obj, list):
            return None

        lines = [
            f"schema_version: {payload.get('schema_version', '')}",
            f"role_id: {payload.get('role_id', 'resident_agi')}",
            f"runtime_foundation: {payload.get('runtime_foundation', '')}",
            f"implementation_cell: {payload.get('implementation_cell', 'resident.autonomy')}",
            f"product_role: {payload.get('product_role', 'embedded_agi_supervisor')}",
            f"unattended_factory_role: {payload.get('unattended_factory_role', 'replace_human_supervision')}",
            "decision_boundary: hard_rules_then_resident_agi_supervisor",
            "",
            "non_bypass_rules:",
            "- Resident AGI is a RoleRuntime actor, not a sidecar or second orchestration system.",
            "- It may inspect audit evidence, final provider requests, runtime events, task profiles, and blueprints.",
            "- Execution-impacting actions must preserve PM -> Chief Engineer -> Director and canonical gates.",
            "",
            "capabilities:",
        ]
        for raw_item in items_obj[:20]:
            if not isinstance(raw_item, Mapping):
                continue
            capability_id = str(raw_item.get("capability_id") or "").strip()
            if not capability_id:
                continue
            category = str(raw_item.get("category") or "unknown").strip()
            access = str(raw_item.get("access") or "unknown").strip()
            risk = str(raw_item.get("risk_level") or "low").strip()
            name = str(raw_item.get("name") or capability_id).strip()
            purpose = str(raw_item.get("purpose") or "").strip()
            contract_ref = str(raw_item.get("contract_ref") or "").strip()
            endpoint = str(raw_item.get("endpoint") or "").strip()
            lines.append(f"- {capability_id} | {name} | category={category} | access={access} | risk={risk}")
            if purpose:
                lines.append(f"  purpose: {purpose}")
            if contract_ref:
                lines.append(f"  contract: {contract_ref}")
            if endpoint:
                lines.append(f"  endpoint: {endpoint}")
            guardrails = raw_item.get("guardrails") or ()
            if isinstance(guardrails, (list, tuple)):
                rendered_guardrails = [str(item).strip() for item in guardrails if str(item).strip()]
                if rendered_guardrails:
                    lines.append(f"  guardrails: {'; '.join(rendered_guardrails[:3])}")
            evidence_refs = raw_item.get("evidence_refs") or ()
            if isinstance(evidence_refs, (list, tuple)):
                rendered_refs = [str(item).strip() for item in evidence_refs if str(item).strip()]
                if rendered_refs:
                    lines.append(f"  evidence_refs: {', '.join(rendered_refs[:3])}")

        rendered = "\n".join(lines).strip()
        return rendered or None

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
