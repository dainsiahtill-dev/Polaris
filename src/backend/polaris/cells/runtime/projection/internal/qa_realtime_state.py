"""QA Realtime Service - QA 状态实时聚合服务

本模块提供 QA 角色的实时状态聚合，用于统一 WebSocket 推送。
QA 作为主流程中的服务运行，通过监听消息总线和状态文件获取状态。
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from polaris.cells.runtime.projection.internal.io_helpers import build_cache_root
from polaris.cells.runtime.projection.internal.runtime_v2 import (
    QATaskNode,
    QATaskState,
    ReviewVerdict,
    RoleState,
    RoleType,
)

logger = logging.getLogger("polaris.qa_realtime_state")


@dataclass
class QARealtimeState:
    """QA 实时状态"""

    role: RoleType = RoleType.QA
    state: RoleState = RoleState.IDLE
    task_id: str | None = None
    task_title: str | None = None
    detail: str | None = None
    updated_at: datetime = field(default_factory=datetime.now)

    # QA 特定字段
    audits: list[QATaskNode] = field(default_factory=list)
    pending_count: int = 0
    completed_count: int = 0
    failed_count: int = 0


class QARealtimeProvider:
    """QA 实时状态提供者

    从 QA 服务状态和审计结果聚合 QA 状态。
    """

    def __init__(self, workspace: str, ramdisk_root: str = "") -> None:
        self.workspace = workspace
        self.ramdisk_root = ramdisk_root
        self.cache_root = build_cache_root(ramdisk_root, workspace)

    async def get_state(self) -> QARealtimeState:
        """获取 QA 当前状态"""
        state = QARealtimeState()

        # 从状态文件读取 QA 审计结果
        audit_summary = self._load_audit_summary()

        if audit_summary:
            state.audits = self._convert_audits(audit_summary.get("audits", []))
            state.pending_count = audit_summary.get("pending", 0)
            state.completed_count = audit_summary.get("completed", 0)
            state.failed_count = audit_summary.get("failed", 0)

            # 根据审计数量确定状态
            if state.pending_count > 0:
                state.state = RoleState.VERIFICATION
            elif state.completed_count > 0:
                state.state = RoleState.COMPLETED
            else:
                state.state = RoleState.IDLE

        return state

    def _load_audit_summary(self) -> dict[str, Any]:
        """从状态文件加载审计摘要"""
        # 尝试从 runtime/status 目录加载
        status_dir = os.path.join(self.cache_root, "runtime", "status")

        # 查找 QA 状态文件
        possible_files = [
            os.path.join(status_dir, "qa.summary.json"),
            os.path.join(status_dir, "qa.audits.json"),
        ]

        import json

        for filepath in possible_files:
            if os.path.isfile(filepath):
                try:
                    with open(filepath, encoding="utf-8") as f:
                        data = json.load(f)
                        return data if isinstance(data, dict) else {}
                except (RuntimeError, ValueError) as exc:
                    logger.debug("failed to parse QA state file '%s' (non-critical): %s", filepath, exc)

        return {}

    def _convert_audits(self, audits: list[dict]) -> list[QATaskNode]:
        """将审计结果转换为 QA 任务节点"""
        result = []

        for audit in audits:
            audit_state = str(audit.get("state", "")).upper()
            if audit_state in {"COMPLETED", "DONE", "PASSED"}:
                qa_state = QATaskState.COMPLETED
            elif audit_state in {"FAILED", "ERROR", "REJECTED"}:
                qa_state = QATaskState.FAILED
            elif audit_state in {"IN_PROGRESS", "RUNNING"}:
                qa_state = QATaskState.IN_PROGRESS
            else:
                qa_state = QATaskState.PENDING

            verdict_str = str(audit.get("verdict", "")).upper()
            if verdict_str == "APPROVED":
                verdict = ReviewVerdict.APPROVED
            elif verdict_str == "REJECTED":
                verdict = ReviewVerdict.REJECTED
            else:
                verdict = None

            result.append(
                QATaskNode(
                    id=str(audit.get("id", "")),
                    target_task_id=str(audit.get("task_id", "")),
                    target_type=str(audit.get("target_type", "task"))
                    if audit.get("target_type") in ("task", "code_change", "file")
                    else "task",  # type: ignore[arg-type]
                    state=qa_state,
                    verdict=verdict,
                    issues=audit.get("issues", []),
                    reviewed_by=str(audit.get("reviewer", "")) or None,
                    created_at=self._parse_datetime(audit.get("created_at")) or datetime.now(),  # type: ignore[arg-type]
                    completed_at=self._parse_datetime(audit.get("completed_at")),
                )
            )

        return result

    def _parse_datetime(self, value: Any) -> datetime | None:
        """解析日期时间"""
        if value is None:
            return None
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except (RuntimeError, ValueError) as exc:
                logger.debug("datetime parse failed in QA realtime state (non-critical): %s", exc)
        return None


async def build_qa_realtime_state(
    workspace: str,
    ramdisk_root: str = "",
) -> dict[str, Any]:
    """构建 QA 实时状态的字典格式

    用于 WebSocket 推送。
    """
    provider = QARealtimeProvider(workspace, ramdisk_root)
    state = await provider.get_state()

    return {
        "role": state.role.value,
        "state": state.state.value,
        "task_id": state.task_id,
        "task_title": state.task_title,
        "detail": state.detail,
        "updated_at": state.updated_at.isoformat(),
        "audits": [a.model_dump(mode="json") for a in state.audits],
        "pending_count": state.pending_count,
        "completed_count": state.completed_count,
        "failed_count": state.failed_count,
    }
