"""角色适配器基类

提供公共功能和工具方法。
"""

from __future__ import annotations

import json
import logging
import os
import re
from abc import abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from polaris.cells.orchestration.workflow_runtime.public.service import RoleAdapterFactoryPort, RoleOrchestrationAdapter
from polaris.cells.runtime.task_runtime.public.service import TaskRuntimeService
from polaris.kernelone.fs.text_ops import write_text_atomic
from polaris.kernelone.process.command_executor import CommandExecutionService, CommandRequest
from polaris.kernelone.storage.paths import resolve_signal_path

_logger = logging.getLogger(__name__)


class BaseRoleAdapter(RoleOrchestrationAdapter):
    """角色适配器基类"""

    def __init__(self, workspace: str) -> None:
        self.workspace = workspace
        self._task_runtime: TaskRuntimeService | None = None
        self._message_bus: Any = None
        self._task_trace_seq: dict[str, int] = {}
        self._factory_port: RoleAdapterFactoryPort | None = None

    def _register_with_factory(self, port: RoleAdapterFactoryPort) -> None:
        """Register this adapter with the given factory port.

        Call this after construction to make the adapter available to the
        workflow runtime without importing workflow_runtime internal modules.

        Args:
            port: The RoleAdapterFactoryPort provided by the orchestrator.
        """
        self._factory_port = port
        port.register(self.role_id, self)
        _logger.debug("Registered adapter with factory port: role=%s", self.role_id)

    @property
    @abstractmethod
    def role_id(self) -> str:
        """角色标识"""
        ...

    def get_capabilities(self) -> list[str]:
        """获取角色能力列表 - 子类可覆盖"""
        return ["execute_task"]

    @property
    def task_runtime(self) -> TaskRuntimeService:
        """懒加载任务运行时服务（TaskBoard 统一入口）。"""
        if self._task_runtime is None:
            self._task_runtime = TaskRuntimeService(workspace=self.workspace)
        return self._task_runtime

    @property
    def task_board(self) -> TaskRuntimeService:
        """兼容属性：历史调用点继续使用 ``self.task_board``。"""
        return self.task_runtime

    def _build_env(self, overrides: dict[str, str] | None = None) -> dict[str, str]:
        """构建环境变量"""
        env = {
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
            "KERNELONE_WORKSPACE": self.workspace,
        }

        if overrides:
            env.update(overrides)

        return env

    def _count_file_changes(self, scope_paths: list[str]) -> dict[str, int]:
        """统计指定路径下的文件变更。

        通过比较文件修改时间与参考时间，统计新增、修改、删除的文件数量。
        同时估算新增/删除的行数（基于文件大小变化）。

        Args:
            scope_paths: 需要统计的文件路径列表（支持文件和目录）。

        Returns:
            变更统计字典，包含以下字段：
            - created: 新创建的文件数
            - modified: 修改的文件数
            - deleted: 删除的文件数
            - lines_added: 估算的新增行数
            - lines_removed: 估算的删除行数
            - lines_changed: 估算的变更行数（新增+删除）
        """
        if not scope_paths:
            return {
                "created": 0,
                "modified": 0,
                "deleted": 0,
                "lines_added": 0,
                "lines_removed": 0,
                "lines_changed": 0,
            }

        stats = {
            "created": 0,
            "modified": 0,
            "deleted": 0,
            "lines_added": 0,
            "lines_removed": 0,
            "lines_changed": 0,
        }

        # 尝试使用 git diff 获取准确的变更统计
        try:
            cmd_svc = CommandExecutionService(self.workspace)
            request = CommandRequest(
                executable="git",
                args=["diff", "--numstat", "--"],
                cwd=self.workspace,
                timeout_seconds=30,
            )
            result = cmd_svc.run(request)
            if result.get("ok") and result.get("stdout"):
                stdout = str(result.get("stdout", ""))
                for line in stdout.strip().split("\n"):
                    parts = line.split("\t")
                    if len(parts) >= 3:
                        added_str, removed_str, _ = parts[0], parts[1], parts[2]
                        # 处理二进制文件（显示为 "-"）
                        added = int(added_str) if added_str.isdigit() else 0
                        removed = int(removed_str) if removed_str.isdigit() else 0
                        stats["lines_added"] += added
                        stats["lines_removed"] += removed
                        if added > 0 and removed == 0:
                            stats["created"] += 1
                        elif removed > 0 and added == 0:
                            stats["deleted"] += 1
                        else:
                            stats["modified"] += 1

                stats["lines_changed"] = stats["lines_added"] + stats["lines_removed"]
                return stats
        except (RuntimeError, ValueError) as exc:
            _logger.debug("git stats unavailable, falling back to fs scan: %s", exc)

        # 回退：基于文件系统扫描估算
        reference_time = datetime.now(timezone.utc).timestamp() - 3600  # 1小时内视为"新"

        for scope_path in scope_paths:
            full_path = Path(self.workspace) / scope_path
            if not full_path.exists():
                continue

            files_to_check = [full_path] if full_path.is_file() else list(full_path.rglob("*"))

            for file_path in files_to_check:
                if not file_path.is_file():
                    continue

                try:
                    stat = file_path.stat()
                    mtime = stat.st_mtime

                    if mtime > reference_time:
                        # 估算行数（假设平均每行50字节）
                        estimated_lines = max(1, stat.st_size // 50)
                        if mtime > reference_time + 3540:  # 最近60秒内创建
                            stats["created"] += 1
                            stats["lines_added"] += estimated_lines
                        else:
                            stats["modified"] += 1
                            # 修改的文件假设一半行数变更
                            stats["lines_changed"] += estimated_lines // 2
                except (OSError, PermissionError):
                    continue

        stats["lines_changed"] = stats["lines_added"] + stats["lines_removed"]
        return stats

    @staticmethod
    def _coerce_board_task_id(task_id: Any) -> int | None:
        token = str(task_id or "").strip()
        if not token:
            return None
        if token.isdigit():
            return int(token)
        match = re.match(r"^task-(\d+)(?:-|$)", token)
        if match:
            return int(match.group(1))
        return None

    def _board_task_exists(self, task_id: Any) -> bool:
        normalized = self._coerce_board_task_id(task_id)
        if normalized is None:
            return False
        return self.task_runtime.task_exists(normalized)

    def _update_board_task(
        self,
        task_id: Any,
        *,
        status: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        normalized = self._coerce_board_task_id(task_id)
        if normalized is None:
            return False
        if not self.task_runtime.task_exists(normalized):
            return False
        self.task_runtime.update_task(
            normalized,
            status=status,
            metadata=metadata or {},
        )
        return True

    def _update_task_progress(
        self,
        task_id: str,
        phase: str,
        current_file: str | None = None,
        event_code: str | None = None,
        event_status: str | None = None,
        event_reason: str | None = None,
        event_detail: str | None = None,
        event_refs: dict[str, Any] | None = None,
    ) -> None:
        """更新任务进度"""
        try:
            metadata = {
                "adapter_phase": str(phase or "").strip(),
                "adapter_current_file": str(current_file or "").strip() or None,
            }
            normalized_code = str(event_code or "").strip()
            if normalized_code:
                metadata["adapter_event_code"] = normalized_code
            normalized_status = str(event_status or "").strip().lower()
            if normalized_status:
                metadata["adapter_event_status"] = normalized_status
            normalized_reason = str(event_reason or "").strip()
            if normalized_reason:
                metadata["adapter_event_reason"] = normalized_reason
            normalized_detail = str(event_detail or "").strip()
            if normalized_detail:
                metadata["adapter_event_detail"] = (
                    normalized_detail[:397] + "..." if len(normalized_detail) > 400 else normalized_detail
                )
            if isinstance(event_refs, dict) and event_refs:
                metadata["adapter_event_refs"] = dict(event_refs)  # type: ignore[assignment]
            metadata["adapter_event_ts"] = datetime.now(timezone.utc).isoformat()
            self._update_board_task(task_id, metadata=metadata)
        except (RuntimeError, ValueError) as exc:
            _logger.debug("board task update failed (non-critical): %s", exc)

    def _append_runtime_stage_signals(
        self,
        *,
        stage: str,
        task_id: str,
        signals: list[dict[str, Any]],
        context: dict[str, Any] | None = None,
        source: str | None = None,
    ) -> str | None:
        """持久化阶段信号，供 QA 统一读取与裁决。"""
        if not signals:
            return None
        stage_token = str(stage or "").strip().lower()
        if not stage_token:
            return None

        role_token = str(self.role_id or "").strip().lower() or "unknown"
        target = resolve_signal_path(self.workspace, role_token, stage_token)
        target.parent.mkdir(parents=True, exist_ok=True)

        normalized_rows: list[dict[str, Any]] = []
        run_id = str(context.get("run_id") or "").strip() if isinstance(context, dict) else ""
        task_token = str(task_id or "").strip()
        source_token = str(source or f"{role_token}_adapter").strip()

        for item in signals:
            if not isinstance(item, dict):
                continue
            code = str(item.get("code") or "").strip() or "unknown_signal"
            severity = str(item.get("severity") or "").strip().lower() or "info"
            detail = str(item.get("detail") or "").strip()
            payload = dict(item)
            payload["code"] = code
            payload["severity"] = severity
            payload["detail"] = detail
            payload["role"] = role_token
            payload["stage"] = stage_token
            payload["task_id"] = task_token
            payload["run_id"] = run_id
            payload["source"] = source_token
            payload["timestamp"] = datetime.now(timezone.utc).isoformat()
            normalized_rows.append(payload)

        if not normalized_rows:
            return None

        existing_rows: list[dict[str, Any]] = []
        if target.exists() and target.is_file():
            try:
                loaded = json.loads(target.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                loaded = {}
            if isinstance(loaded, dict):
                rows = loaded.get("signals")
                if isinstance(rows, list):
                    existing_rows = [row for row in rows if isinstance(row, dict)]

        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source": source_token,
            "role": role_token,
            "stage": stage_token,
            "run_id": run_id,
            "signals": [*existing_rows, *normalized_rows][-500:],
        }
        write_text_atomic(
            str(target),
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return f"runtime/signals/{stage_token}.{role_token}.signals.json"

    def _next_task_trace_seq(self, task_id: str) -> int:
        token = str(task_id or "").strip() or "unknown"
        current = int(self._task_trace_seq.get(token, 0))
        next_seq = current + 1
        self._task_trace_seq[token] = next_seq
        return next_seq

    async def _resolve_message_bus(self) -> Any:
        if self._message_bus is not None:
            return self._message_bus
        try:
            from polaris.cells.director.execution.public.service import DirectorService
            from polaris.infrastructure.di.container import get_container

            container = await get_container()
            director_service = await container.resolve_async(DirectorService)
            bus = getattr(director_service, "_bus", None)
            if bus is not None:
                self._message_bus = bus
            return bus
        except (RuntimeError, ValueError):
            return None

    async def _emit_task_trace_event(
        self,
        *,
        task_id: str,
        phase: str,
        step_kind: str,
        step_title: str,
        step_detail: str,
        status: str = "running",
        run_id: str = "",
        current_file: str | None = None,
        code: str | None = None,
        reason: str | None = None,
        refs: dict[str, Any] | None = None,
        attempt: int = 0,
        visibility: str = "debug",
    ) -> None:
        """写入任务板并广播 TASK_TRACE，供外部快速定位阻塞点。

        委托给 polaris.kernelone.events.task_trace_events.emit_task_trace_event
        """
        from polaris.kernelone.events.task_trace_events import emit_task_trace_event

        normalized_task_id = str(task_id or "").strip()
        if not normalized_task_id:
            return

        normalized_status = str(status or "").strip().lower() or "running"

        # Determine trace_type from status
        trace_type = _status_to_trace_type(normalized_status)

        # Build payload for emit_task_trace_event
        payload: dict[str, Any] = {
            "phase": str(phase or "").strip() or "executing",
            "step_kind": str(step_kind or "").strip() or "system",
            "step_title": str(step_title or "").strip()[:120] or "adapter_event",
            "step_detail": str(step_detail or "").strip(),
            "status": normalized_status,
            "run_id": str(run_id or "").strip(),
            "current_file": current_file,
            "code": code,
            "reason": reason,
            "refs": refs,
            "attempt": attempt,
            "visibility": visibility,
            "role": str(self.role_id or "").strip().lower(),
        }

        # Update task progress
        refs_payload: dict[str, Any] = dict(refs or {})
        if current_file:
            refs_payload["current_file"] = str(current_file).strip()
        if code:
            refs_payload["code"] = str(code).strip()
        if reason:
            refs_payload["reason"] = str(reason).strip()

        self._update_task_progress(
            normalized_task_id,
            payload["phase"],
            current_file=current_file,
            event_code=str(code).strip() if code else None,
            event_status=normalized_status,
            event_reason=str(reason).strip() if reason else None,
            event_detail=payload["step_detail"],
            event_refs=refs_payload,
        )

        # Emit via kernelone.events.task_trace_events
        await emit_task_trace_event(
            workspace=self.workspace,
            task_id=normalized_task_id,
            trace_type=trace_type,
            payload=payload,
        )

    @staticmethod
    def _resolve_kernel_validation_enabled(
        role_token: str,
        context: dict[str, Any] | None,
    ) -> bool:
        """Resolve whether kernel output validation should gate this turn.

        策略:
        - 默认关闭（各层只产出 signals，不做动作级硬裁决）
        - 可通过 context["validate_output"] 显式覆盖
        - 可通过环境变量 KERNELONE_<ROLE>_VALIDATE_OUTPUT 覆盖
        """
        if isinstance(context, dict) and "validate_output" in context:
            return bool(context.get("validate_output"))

        normalized_role = str(role_token or "").strip().lower()
        env_name = f"KERNELONE_{normalized_role.upper()}_VALIDATE_OUTPUT"
        env_value = str(os.environ.get(env_name, "") or "").strip().lower()
        if env_value in {"1", "true", "yes", "on"}:
            return True
        if env_value in {"0", "false", "no", "off"}:
            return False
        return False

    @staticmethod
    def _resolve_kernel_retry_budget(role_token: str) -> int:
        """解析角色内核重试预算。

        默认给 Director 1 次内核重试，避免瞬时空输出直接导致调度失败；
        允许通过环境变量覆盖为 0..3。
        """
        normalized_role = str(role_token or "").strip().lower()
        env_name = f"KERNELONE_{normalized_role.upper()}_KERNEL_MAX_RETRIES"
        default_value = "1"
        raw_value = str(os.environ.get(env_name, default_value)).strip()
        try:
            value = int(raw_value)
        except (RuntimeError, ValueError):
            value = int(default_value)
        return max(0, min(3, value))


def _status_to_trace_type(status: str) -> str:
    """Map status string to trace_type"""
    status_lower = status.lower()
    if status_lower in ("start", "starting"):
        return "start"
    if status_lower in ("error", "failed", "failure"):
        return "error"
    if status_lower in ("complete", "completed", "done", "success"):
        return "complete"
    return "step"
