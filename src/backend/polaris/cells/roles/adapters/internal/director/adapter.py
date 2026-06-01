"""Director 角色适配器核心类

实现 Director 角色的统一编排接口。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from typing import Any

from polaris.cells.llm.dialogue.public.service import generate_role_response

from ..base import BaseRoleAdapter
from ..director_execution_backend import (
    DirectorExecutionBackendRequest,
    resolve_director_execution_backend,
)
from .adapter_sequential import (
    build_sequential_config,
    execute_hybrid,
    execute_sequential,
)
from .dialogue import get_settings_safe
from .execute_method import execute_director_task
from .execution import DirectorPatchExecutor
from .helpers import (
    _DEFAULT_LLM_CALL_TIMEOUT_SECONDS,
    is_empty_role_response,
    taskboard_snapshot_brief,
)
from .state_tracking import DirectorStateTracker
from .state_utils import (
    compose_projection_requirement,
    default_projection_slug,
)

logger = logging.getLogger(__name__)


class DirectorAdapter(BaseRoleAdapter):
    """Director 角色适配器

    职责：
    - 任务执行
    - 代码改写
    - 验证与测试
    - 工具调用
    """

    def __init__(self, workspace: str, task_board: Any = None, task_runtime: Any = None) -> None:
        if task_board is None and task_runtime is None:
            super().__init__(workspace)
        else:
            self.workspace = workspace
            self._task_runtime = task_runtime
            self._task_board = task_board if task_board else task_runtime
        self._state_tracker = DirectorStateTracker(workspace)
        self._execution = DirectorPatchExecutor(workspace)

    @property
    def role_id(self) -> str:
        return "director"

    def get_capabilities(self) -> list[str]:
        return [
            "execute_task",
            "write_code",
            "edit_file",
            "run_command",
            "verify_result",
            "sequential_execution",
            "adaptive_strategy_selection",
            "intelligent_self_correction",
            "multi_objective_optimization",
        ]

    # -------------------------------------------------------------------------
    # Main Execute Method
    # -------------------------------------------------------------------------

    async def execute(
        self,
        task_id: str,
        input_data: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """执行 Director 任务"""
        # Phase 2.4: Pre-execution strategy selection based on task characteristics
        directive = str(input_data.get("input") or input_data.get("directive") or "").strip()
        task_data = input_data.get("task") or input_data
        selected_strategy = self._select_execution_strategy(directive, task_data, context)
        if selected_strategy != "default":
            logger.info("Director strategy selected: %s for task %s", selected_strategy, task_id)

        # Inject strategy into context for downstream use
        if context is not None:
            ctx_metadata = context.get("metadata") if isinstance(context, dict) else None
            if ctx_metadata is None:
                ctx_metadata = {}
                context["metadata"] = ctx_metadata
            if isinstance(ctx_metadata, dict):
                ctx_metadata["director_strategy"] = selected_strategy

        return await execute_director_task(self, task_id, input_data, context)

    def _select_execution_strategy(
        self,
        directive: str,
        task: dict[str, Any],
        context: dict[str, Any],
    ) -> str:
        """Phase 2.4: Select optimal execution strategy based on task characteristics.

        Args:
            directive: Task directive text
            task: Task data dictionary
            context: Execution context (may contain architect constraints)

        Returns:
            Strategy name: 'default', 'incremental', 'aggressive', 'conservative', 'focused'
        """
        strategy_factors: list[str] = []

        # Check architect constraints from context
        ctx_metadata = context.get("metadata") if isinstance(context, dict) else None
        architect_constraints = []
        if isinstance(ctx_metadata, dict):
            architect_constraints = ctx_metadata.get("architect_constraints", [])

        # Check for concerns from architect
        has_architect_concerns = any(c.get("type") == "concern" for c in architect_constraints if isinstance(c, dict))
        if has_architect_concerns:
            return "conservative"  # Be careful when architect raised concerns

        # Analyze task complexity
        if len(directive) > 300:
            strategy_factors.append("complex_directive")
        if "test" in directive.lower() or "verify" in directive.lower():
            strategy_factors.append("verification_focused")
        if "refactor" in directive.lower() or "重构" in directive:
            strategy_factors.append("refactoring")

        # Check for file targets
        target_files = task.get("target_files", []) if isinstance(task, dict) else []
        scope_files = task.get("scope_paths", []) if isinstance(task, dict) else []
        total_files = len(target_files) + len(scope_files)

        if total_files >= 10:
            strategy_factors.append("large_scope")
        elif total_files >= 5:
            strategy_factors.append("medium_scope")

        # Determine strategy
        if "large_scope" in strategy_factors and "complex_directive" in strategy_factors:
            return "incremental"
        if "refactoring" in strategy_factors:
            return "conservative"
        if "verification_focused" in strategy_factors:
            return "focused"
        if "medium_scope" in strategy_factors and "complex_directive" in strategy_factors:
            return "aggressive"
        return "default"

    def _apply_intelligent_correction(
        self,
        attempt_result: dict[str, Any],
        previous_attempts: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Phase 2.4: Apply intelligent self-correction based on failure patterns.

        Args:
            attempt_result: Result of current execution attempt
            previous_attempts: List of previous attempt results

        Returns:
            Modified result with correction hints
        """
        if attempt_result.get("success", False):
            return attempt_result

        # Analyze failure patterns from previous attempts
        failure_types: dict[str, int] = {}
        for prev in previous_attempts:
            error = str(prev.get("error") or "")
            if "timeout" in error.lower():
                failure_types["timeout"] = failure_types.get("timeout", 0) + 1
            elif "syntax" in error.lower() or "语法" in error:
                failure_types["syntax_error"] = failure_types.get("syntax_error", 0) + 1
            elif "not found" in error.lower() or "找不到" in error:
                failure_types["missing_dependency"] = failure_types.get("missing_dependency", 0) + 1
            elif "permission" in error.lower() or "权限" in error:
                failure_types["permission"] = failure_types.get("permission", 0) + 1
            else:
                failure_types["unknown"] = failure_types.get("unknown", 0) + 1

        # Generate correction hints based on failure patterns
        correction_hints: list[str] = []
        for failure_type, count in failure_types.items():
            if count >= 2:
                if failure_type == "timeout":
                    correction_hints.append("Consider breaking down into smaller steps")
                elif failure_type == "syntax_error":
                    correction_hints.append("Check syntax before applying changes")
                elif failure_type == "missing_dependency":
                    correction_hints.append("Ensure all dependencies are available first")
                elif failure_type == "permission":
                    correction_hints.append("Verify file permissions before writing")

        if correction_hints:
            attempt_result["_correction_hints"] = correction_hints

        return attempt_result

    # -------------------------------------------------------------------------
    # Sequential Engine Configuration
    # -------------------------------------------------------------------------

    def _get_sequential_config(self, context: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """Get Sequential configuration from settings and context."""
        settings = get_settings_safe()
        return build_sequential_config(settings, context)

    # -------------------------------------------------------------------------
    # Sequential Engine Execution
    # -------------------------------------------------------------------------

    async def _execute_sequential(
        self,
        task: dict[str, Any],
        task_id: str,
        run_id: str,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute task using Sequential Engine."""
        seq_config = self._get_sequential_config(context)
        if not seq_config:
            return {"success": False, "error": "Sequential not enabled"}
        return await execute_sequential(
            self.workspace,
            self.role_id,
            task,
            task_id,
            run_id,
            context,
            seq_config,
            self._invoke_role_dialogue_with_timeout,
            self._emit_task_trace_event,
            self._build_director_message,
        )

    async def _execute_hybrid(
        self,
        task: dict[str, Any],
        task_id: str,
        run_id: str,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute task using Hybrid Engine."""
        seq_config = self._get_sequential_config(context)
        if not seq_config:
            return {"success": False, "error": "Sequential not enabled"}
        return await execute_hybrid(
            self.workspace,
            self.role_id,
            task,
            task_id,
            run_id,
            context,
            seq_config,
            self._emit_task_trace_event,
        )

    # -------------------------------------------------------------------------
    # Role LLM Invocation
    # -------------------------------------------------------------------------

    async def _invoke_role_dialogue(
        self,
        message: str,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """调用 Director LLM (via canonical public dialogue API)."""
        settings = get_settings_safe()
        llm_max_retries = self._resolve_kernel_retry_budget(self.role_id)

        primary_response = await generate_role_response(
            workspace=self.workspace,
            settings=settings,
            role=self.role_id,
            message=message,
            context=context,
            validate_output=False,
            max_retries=llm_max_retries,
        )
        primary = {
            "content": str(primary_response.get("response") or "")
            if isinstance(primary_response, dict)
            else str(primary_response or ""),
            "success": True,
            "error": "",
            "raw_response": primary_response,
        }
        if is_empty_role_response(primary):
            fallback_response = await generate_role_response(
                workspace=self.workspace,
                settings=settings,
                role=self.role_id,
                message=message,
                context=context,
                validate_output=True,
                max_retries=max(1, llm_max_retries),
            )
            fallback = {
                "content": str(fallback_response.get("response") or "")
                if isinstance(fallback_response, dict)
                else str(fallback_response or ""),
                "success": True,
                "error": "",
                "raw_response": fallback_response,
            }
            if not is_empty_role_response(fallback):
                return fallback
            try:
                return await self._invoke_direct_runtime_provider(message)
            except (OSError, RuntimeError, TimeoutError, TypeError, ValueError) as exc:
                fallback["error"] = str(fallback.get("error") or f"director_empty_role_response:{exc}")
                return fallback
        return primary

    async def _invoke_direct_runtime_provider(
        self,
        message: str,
        *,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        """Call the configured Director provider without the chat/tool kernel wrapper."""
        return await asyncio.to_thread(
            self._invoke_direct_runtime_provider_sync,
            message,
            timeout_seconds=timeout_seconds,
        )

    def _invoke_direct_runtime_provider_sync(
        self,
        message: str,
        *,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        from polaris.infrastructure.llm.provider_runtime_adapter import AppLLMRuntimeAdapter
        from polaris.kernelone.llm.runtime import invoke_role_runtime_provider

        settings = get_settings_safe()
        configured_timeout = int(getattr(settings, "llm_timeout", 0) or 0) or int(_DEFAULT_LLM_CALL_TIMEOUT_SECONDS)
        if timeout_seconds is not None:
            configured_timeout = max(1, int(float(timeout_seconds)))
        result = invoke_role_runtime_provider(
            role=self.role_id,
            workspace=self.workspace,
            prompt=message,
            fallback_model="",
            timeout=configured_timeout,
            adapter=AppLLMRuntimeAdapter(),
            blocked_provider_types={
                "",
                "codex",
            },
        )
        if not result.attempted or not result.ok:
            error_message = str(result.error or "").strip() or "runtime_provider_unavailable"
            if not result.attempted:
                raise RuntimeError(f"Director runtime provider binding is not configured: {error_message}")
            raise RuntimeError(f"Director runtime provider invocation failed: {error_message}")

        output = str(result.output or "")
        return {
            "content": output,
            "response": output,
            "success": True,
            "role": self.role_id,
            "model": result.model,
            "provider": result.provider_id,
        }

    async def _invoke_role_dialogue_with_timeout(
        self,
        message: str,
        *,
        context: dict[str, Any] | None,
        timeout_seconds: float,
        stage_label: str,
    ) -> dict[str, Any]:
        """Call role LLM with timeout."""
        timeout = max(0.1, float(timeout_seconds or _DEFAULT_LLM_CALL_TIMEOUT_SECONDS))
        try:
            response = await asyncio.wait_for(
                self._invoke_role_dialogue(message, context=context),
                timeout=timeout,
            )
            if isinstance(response, dict):
                return response
            return {
                "content": "",
                "success": False,
                "error": f"director_{stage_label}_invalid_llm_payload",
                "raw_response": response,
            }
        except asyncio.TimeoutError:
            return {
                "content": "",
                "success": False,
                "error": f"director_{stage_label}_llm_timeout",
                "raw_response": {"error": "timeout", "timeout": True},
            }
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            return {
                "content": "",
                "success": False,
                "error": f"director_{stage_label}_llm_error:{exc}",
                "raw_response": {"error": str(exc), "exception_type": type(exc).__name__},
            }

    # -------------------------------------------------------------------------
    # Task Retrieval
    # -------------------------------------------------------------------------

    def _get_task(self, task_id: str) -> dict | None:
        """获取任务信息"""
        return self.task_board.get_task(task_id)

    def _select_pending_board_task(self) -> dict[str, Any] | None:
        """当编排任务没有 TaskBoard 映射时，回退到可执行的真实待办任务。"""
        return self.task_runtime.select_next_task(prefer_resumable=True)

    def _materialize_runtime_task(
        self,
        requested_task_id: str,
        input_data: dict[str, Any],
    ) -> dict[str, Any]:
        """将迁移期编排任务物化为 runtime.task_runtime 的 canonical task。"""
        input_metadata_raw = input_data.get("metadata")
        input_metadata: dict[str, Any] = input_metadata_raw if isinstance(input_metadata_raw, dict) else {}
        subject = str(
            input_data.get("subject")
            or input_metadata.get("title")
            or input_metadata.get("subject")
            or input_data.get("input")
            or ""
        ).strip()
        if not subject:
            subject = f"Director task {requested_task_id}"
        description = str(
            input_data.get("description")
            or input_metadata.get("description")
            or input_metadata.get("goal")
            or input_data.get("input")
            or ""
        ).strip()
        metadata = self._build_materialized_metadata(requested_task_id, input_data)
        return self.task_runtime.ensure_task_row(
            external_task_id=requested_task_id,
            subject=subject,
            description=description,
            metadata=metadata,
        )

    def _build_materialized_metadata(self, requested_task_id: str, input_data: dict[str, Any]) -> dict[str, Any]:
        """Build metadata dict for materialized runtime task."""
        if input_data is None:
            input_data = {}
        input_metadata_raw = input_data.get("metadata")
        input_metadata: dict[str, Any] = input_metadata_raw if isinstance(input_metadata_raw, dict) else {}
        metadata: dict[str, Any] = {
            "goal": str(input_data.get("goal") or input_metadata.get("goal") or "").strip(),
            "scope": str(input_data.get("scope") or input_metadata.get("scope") or "").strip(),
            "steps": (
                input_data.get("steps")
                if isinstance(input_data.get("steps"), list)
                else input_metadata.get("steps")
                if isinstance(input_metadata.get("steps"), list)
                else []
            ),
            "phase": str(input_data.get("phase") or input_metadata.get("phase") or "implementation").strip(),
            "pm_task_id": str(
                input_data.get("pm_task_id")
                or input_metadata.get("pm_task_id")
                or input_metadata.get("task_id")
                or input_metadata.get("id")
                or requested_task_id
            ).strip(),
            "source": "director_adapter.materialized_orchestration_task",
        }
        input_metadata_no_proj = (
            {k: v for k, v in input_metadata.items() if k != "projection"} if input_metadata else {}
        )
        metadata.update(input_metadata_no_proj)
        return metadata

    # -------------------------------------------------------------------------
    # Execution Backend Resolution
    # -------------------------------------------------------------------------

    def _resolve_execution_backend_request(
        self,
        *,
        task_id: str,
        task: dict[str, Any],
        input_data: dict[str, Any],
        context: dict[str, Any],
    ) -> DirectorExecutionBackendRequest:
        """解析执行后端请求"""
        request = resolve_director_execution_backend(
            input_data=input_data,
            task=task,
            context=context,
            default_project_slug=default_projection_slug(task_id, task, input_data),
        )
        if not request.requirement and request.execution_backend != "projection_refresh_mapping":
            request = replace(
                request,
                requirement=compose_projection_requirement(task, input_data),
            )
        return request

    def _persist_execution_backend_metadata(
        self,
        task_id: str,
        request: DirectorExecutionBackendRequest,
    ) -> None:
        """持久化执行后端元数据"""
        if not task_id:
            return
        self._update_board_task(
            task_id,
            metadata=request.to_task_metadata(),
        )

    # -------------------------------------------------------------------------
    # Director Message Building
    # -------------------------------------------------------------------------

    def _build_director_message(self, task: dict[str, Any], *, text_patch_mode: bool = False) -> str:
        """构建 Director 角色消息"""
        subject = task.get("subject", "")
        description = DirectorStateTracker.sanitize_task_description(str(task.get("description") or ""))
        raw_metadata = task.get("metadata")
        metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
        goal = str(metadata.get("goal") or task.get("goal") or "").strip()
        scope = metadata.get("scope", task.get("scope", ""))
        steps = metadata.get("steps") if isinstance(metadata.get("steps"), list) else task.get("steps")
        acceptance = (
            metadata.get("acceptance") if isinstance(metadata.get("acceptance"), list) else task.get("acceptance")
        )
        raw_adapter_result = metadata.get("adapter_result")
        adapter_result: dict[str, Any] = raw_adapter_result if isinstance(raw_adapter_result, dict) else {}
        qa_rework_reason = str(metadata.get("qa_rework_reason") or adapter_result.get("qa_rework_reason") or "").strip()
        qa_rework_evidence = metadata.get("qa_rework_evidence") or adapter_result.get("qa_rework_evidence")

        def _stringify_list(value: Any) -> list[str]:
            if isinstance(value, list):
                return [str(item or "").strip() for item in value if str(item or "").strip()]
            token = str(value or "").strip()
            if not token:
                return []
            return [part.strip() for part in token.split(",") if part.strip()] or [token]

        scope_items = _stringify_list(scope)
        step_items = _stringify_list(steps)
        acceptance_items = _stringify_list(acceptance)
        qa_rework_items = _stringify_list(qa_rework_evidence)

        lines = [
            f"任务: {subject}",
            "",
            f"描述: {description}" if description else "",
            "",
            f"目标: {goal}" if goal else "",
            f"范围: {', '.join(scope_items)}" if scope_items else "",
            "",
            "执行步骤:",
            *[f"- {item}" for item in step_items],
            "",
            "验收标准:",
            *[f"- {item}" for item in acceptance_items],
            "",
            "QA 返工要求:" if qa_rework_reason else "",
            f"- 原因: {qa_rework_reason}" if qa_rework_reason else "",
            *[f"- 证据: {item}" for item in qa_rework_items],
            "必须修复 QA 证据中的真实文件并重新运行相关验证，不得仅确认既有 scope 存在。" if qa_rework_reason else "",
            "",
            "禁止输出 TODO/FIXME/NotImplemented 等占位实现。",
            "不得把示例路径当成目标文件；必须使用任务范围中的真实相对路径。",
            "",
        ]
        if text_patch_mode:
            lines.extend(
                [
                    "当前运行时要求纯文本补丁。只输出可解析的文件块，不要解释。",
                    "创建或替换文件时使用如下格式，每个文件一个块:",
                    "relative/path.ext",
                    "```language",
                    "完整文件内容",
                    "```",
                    "修改已有文件时也可以使用 PATCH_FILE，但 PATCH_FILE 后必须是真实相对路径。",
                    "不要把 unified diff 或 ```diff 代码块当成文件内容输出；Markdown 文件块必须包含完整最终文件内容。",
                    "不要输出 `PATCH_FILE path` 后再跟 ```diff 代码块；若使用 PATCH_FILE 协议，必须使用运行时可解析的正式协议格式。",
                    "不要输出任何占位路径。",
                ]
            )
        else:
            lines.extend(
                [
                    "请通过运行时正式写入工具完成修改；若只能返回文本，输出可解析的文件块。",
                    "文本文件块格式:",
                    "relative/path.ext",
                    "```language",
                    "完整文件内容",
                    "```",
                ]
            )

        return "\n".join(line for line in lines if line != "")

    # -------------------------------------------------------------------------
    # Progress Update Methods (matching base class signatures)
    # -------------------------------------------------------------------------

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
        if event_status:
            self._update_board_task(task_id, status=event_status)

    def _update_board_task(
        self,
        task_id: str,
        status: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """更新 TaskBoard 任务"""
        if metadata:
            self.task_board.update_task(task_id, metadata=metadata)
            return True
        elif status:
            self.task_board.update_task(task_id, status=status)
            return True
        return False

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
        """发射任务追踪事件"""
        logger.debug(
            "Task trace: task_id=%s phase=%s step=%s",
            task_id,
            phase,
            step_kind,
        )

    def _append_runtime_stage_signals(
        self,
        *,
        stage: str,
        task_id: str,
        signals: list[dict[str, Any]],
        context: dict[str, Any] | None = None,
        source: str | None = None,
    ) -> str | None:
        """追加运行时阶段信号"""
        return None

    def _taskboard_snapshot_brief(self, snapshot: dict[str, Any]) -> str:
        """TaskBoard 快照简要描述"""
        return taskboard_snapshot_brief(snapshot)
