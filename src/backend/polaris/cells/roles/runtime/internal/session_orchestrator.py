"""Session Orchestrator - 服务端会话编排器。

负责回合级状态机轮转、ContinuationPolicy 仲裁、ShadowEngine 跨 Turn 预热，
以及 DevelopmentWorkflowRuntime 的 handoff 路由。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from polaris.cells.roles.kernel.public.transaction_contracts import (
    ModificationContract,
    Phase,
    PhaseManager,
    extract_tool_results_from_batch_receipt,
    resolve_delivery_mode,
)
from polaris.cells.roles.kernel.public.turn_contracts import (
    FailureClass,
    TurnContinuationMode,
    TurnOutcomeEnvelope,
    TurnResult,
)
from polaris.cells.roles.kernel.public.turn_events import (
    CompletionEvent,
    ErrorEvent,
    SessionCompletedEvent,
    SessionStartedEvent,
    SessionWaitingHumanEvent,
    TurnEvent,
    TurnPhaseEvent,
)
from polaris.cells.roles.kernel.public.workflow_contracts import (
    DevelopmentWorkflowRuntime,
)
from polaris.cells.roles.runtime.internal.continuation_policy import (
    ContinuationPolicy,
    InvariantViolationError,
    OrchestratorSessionState,
    SessionInvariants,
    apply_session_patch,
    extract_session_patch_from_text,
    get_active_findings,
    strip_session_patch_block,
)
from polaris.cells.roles.runtime.internal.session_artifact_store import (
    SessionArtifactStore,
)

# Lazy-loaded role profile cache (avoids circular import at module level)
_role_profile_cache: dict[str, tuple[str, list[dict[str, Any]]]] = {}  # role -> (role_definition, tool_definitions)

logger = logging.getLogger(__name__)

_WRITE_TOOL_NAMES = {
    "write_file",
    "edit_file",
    "create_file",
    "append_to_file",
    "precision_edit",
    "edit_blocks",
    "search_replace",
    "repo_apply_diff",
    "apply_diff",
}

_READ_TOOL_NAMES = {
    "read_file",
    "repo_read_head",
    "repo_read_slice",
    "repo_read_tail",
    "repo_read_around",
    "repo_read_range",
}

_EXPLORATION_ONLY_TOOLS = {
    "glob",
    "repo_rg",
    "repo_tree",
    "list_directory",
    "search_code",
    "grep",
    "ripgrep",
    "find",
}

_PHASE_PROGRESS_ALIASES = {
    "investigating": "content_gathered",
}

_PHASE_PROGRESS_PRIORITY = {
    "exploring": 1,
    "content_gathered": 2,
    "implementing": 3,
    "verifying": 4,
    "done": 5,
}


@dataclass
class SessionStateReducer:
    """Canonical post-turn reducer for orchestrator session state."""

    state: OrchestratorSessionState
    phase_manager: PhaseManager = field(default_factory=PhaseManager)
    # Canonical field name kept as task_contract for backward compatibility
    # with checkpoint payloads and downstream continuation logic.
    task_contract: ModificationContract = field(default_factory=ModificationContract)

    @property
    def modification_contract(self) -> ModificationContract:
        """Compatibility alias used by newer mutation-guard naming."""
        return self.task_contract

    @modification_contract.setter
    def modification_contract(self, value: ModificationContract) -> None:
        self.task_contract = value

    def current_phase(self) -> Phase:
        """Return the authoritative session phase."""
        return self.phase_manager.current_phase

    def restore_phase_manager(
        self,
        payload: dict[str, Any] | None,
        *,
        fallback_progress: str | None = None,
    ) -> None:
        """Restore PhaseManager from checkpoint payload or fallback progress."""
        if payload:
            self.phase_manager = PhaseManager.from_dict(payload)
            return
        normalized = self._normalize_progress(fallback_progress or self.state.task_progress)
        if normalized in {phase.value for phase in Phase}:
            self.phase_manager = PhaseManager.from_dict({"current_phase": normalized})
        else:
            self.phase_manager = PhaseManager()

    def restore_task_contract(self, payload: dict[str, Any] | None) -> None:
        """Restore ModificationContract from checkpoint payload (schema >= 5)."""
        if payload and isinstance(payload, dict):
            self.task_contract = ModificationContract.from_dict(payload)
        else:
            self.task_contract = ModificationContract()

    def restore_modification_contract(self, payload: dict[str, Any] | None) -> None:
        """Compatibility wrapper for old call-sites."""
        self.restore_task_contract(payload)

    def mutation_obligation_satisfied(self, batch_receipt: Any | None = None) -> bool:
        """Return True when a successful write receipt has already been observed."""
        if self._has_successful_write_receipt(batch_receipt):
            return True
        return any(
            self._has_successful_write_receipt(record.get("batch_receipt")) for record in self.state.turn_history
        )

    def is_successful_read_only_execution(self, batch_receipt: Any | None) -> bool:
        """Return True only for successful read-only tool execution."""
        tool_results = extract_tool_results_from_batch_receipt(self._normalize_batch_receipt(batch_receipt))
        if not tool_results:
            return False
        return all(
            result.success and not result.is_write and result.tool_name in _READ_TOOL_NAMES for result in tool_results
        )

    @staticmethod
    def _has_successful_write_receipt(batch_receipt: Any | None) -> bool:
        tool_results = extract_tool_results_from_batch_receipt(
            SessionStateReducer._normalize_batch_receipt(batch_receipt)
        )
        return any(result.success and result.is_write for result in tool_results)

    def _is_materialize_changes_mode(self) -> bool:
        """Return True when the current session is running in mutation mode."""
        return str(self.state.delivery_mode or "").lower() == "materialize_changes"

    def enforce_materialize_changes_guard(self, envelope: TurnOutcomeEnvelope) -> TurnOutcomeEnvelope:
        """Prevent premature final answers before a materialized task actually mutates state."""
        if not self._is_materialize_changes_mode():
            return envelope
        if self.mutation_obligation_satisfied(envelope.turn_result.batch_receipt):
            return envelope
        current_phase = self.phase_manager.current_phase
        if current_phase not in {Phase.EXPLORING, Phase.CONTENT_GATHERED}:
            return envelope
        if envelope.continuation_mode != TurnContinuationMode.END_SESSION:
            return envelope
        if envelope.turn_result.kind != "final_answer":
            return envelope

        session_patch = dict(envelope.session_patch)
        session_patch.setdefault(
            "mandatory_instruction",
            (
                "MATERIALIZE_CHANGES 任务尚未完成实际修改。"
                "请继续下一回合：若还未读取核心文件则先读取，"
                "若已读取则必须直接执行 write/edit 工具而不是结束会话。"
            ),
        )
        return TurnOutcomeEnvelope(
            turn_result=envelope.turn_result.model_copy(update={"kind": "continue_multi_turn"}),
            continuation_mode=TurnContinuationMode.AUTO_CONTINUE,
            next_intent=session_patch["mandatory_instruction"],
            session_patch=session_patch,
            artifacts_to_persist=list(envelope.artifacts_to_persist),
            speculative_hints=dict(envelope.speculative_hints),
            failure_class=envelope.failure_class,
        )

    def apply_turn_outcome(
        self,
        envelope: TurnOutcomeEnvelope,
        *,
        turn_index: int,
        timestamp_ms: int | None = None,
        stop_reason: str | None = None,
    ) -> dict[str, Any]:
        """Apply a canonical turn outcome to session state and history."""
        batch_receipt = self._normalize_batch_receipt(envelope.turn_result.batch_receipt)
        session_patch = dict(envelope.session_patch)
        if session_patch:
            apply_session_patch(self.state, session_patch)
            # FIX-20250422-v3: 从 SESSION_PATCH 提取 modification_plan 更新 ModificationContract
            if session_patch.get("modification_plan"):
                self.modification_contract.update_from_session_patch(session_patch, turn_index)

        self._remember_read_files(batch_receipt)
        self._update_materialize_exploration_streak(batch_receipt)
        normalized_progress = self._derive_task_progress(envelope, batch_receipt)
        self.state.task_progress = normalized_progress
        self.state.structured_findings["task_progress"] = normalized_progress
        self.state.last_failure = self._build_last_failure(envelope, stop_reason)

        record = self._build_turn_record(
            envelope=envelope,
            turn_index=turn_index,
            batch_receipt=batch_receipt,
            session_patch=session_patch,
            stop_reason=stop_reason,
            timestamp_ms=timestamp_ms,
            normalized_progress=normalized_progress,
        )
        self.state.turn_history.append(record)
        return record

    def checkpoint_payload(self) -> dict[str, Any]:
        """Build the canonical checkpoint payload."""
        return {
            "schema_version": 5,
            "session_id": self.state.session_id,
            "turn_count": self.state.turn_count,
            "goal": self.state.goal,
            "task_progress": self.state.task_progress,
            "structured_findings": self.state.structured_findings,
            "key_file_snapshots": self.state.key_file_snapshots,
            "last_failure": self.state.last_failure,
            "artifacts": self.state.artifacts,
            "recent_artifact_hashes": self.state.recent_artifact_hashes,
            "turn_history": self.state.turn_history,
            "original_goal": self.state.original_goal,
            "read_files": self.state.read_files,
            "delivery_mode": self.state.delivery_mode,
            "session_invariants": self.state.session_invariants.to_dict(),
            "phase_manager": self.phase_manager.to_dict(),
            "modification_contract": self.modification_contract.to_dict(),
        }

    def _derive_task_progress(
        self,
        envelope: TurnOutcomeEnvelope,
        batch_receipt: dict[str, Any],
    ) -> str:
        tool_results = extract_tool_results_from_batch_receipt(batch_receipt)
        if tool_results:
            phase = self.phase_manager.transition(tool_results)
            normalized = phase.value
        else:
            normalized = self._normalize_progress(self.state.task_progress)
        if envelope.continuation_mode == TurnContinuationMode.END_SESSION:
            return "done"
        return normalized

    def _build_last_failure(
        self,
        envelope: TurnOutcomeEnvelope,
        stop_reason: str | None,
    ) -> dict[str, Any] | None:
        failure_class = getattr(envelope, "failure_class", None)
        visible_content = getattr(envelope.turn_result, "visible_content", "")
        next_intent = getattr(envelope, "next_intent", None)
        if failure_class is None and not stop_reason and not visible_content:
            return None
        summary = stop_reason or next_intent or visible_content
        if not summary:
            return None
        failure_class_value = failure_class.value if failure_class else ""
        return {
            "summary": str(summary)[:500],
            "failure_class": failure_class_value,
        }

    def _build_turn_record(
        self,
        *,
        envelope: TurnOutcomeEnvelope,
        turn_index: int,
        batch_receipt: dict[str, Any],
        session_patch: dict[str, Any],
        stop_reason: str | None,
        timestamp_ms: int | None,
        normalized_progress: str,
    ) -> dict[str, Any]:
        return {
            "turn_index": turn_index,
            "turn_id": str(envelope.turn_result.turn_id),
            "turn_kind": envelope.turn_result.kind,
            "continuation_mode": envelope.continuation_mode.value,
            "task_progress": normalized_progress,
            "phase": self.phase_manager.current_phase.value,
            "failure_class": getattr(getattr(envelope, "failure_class", None), "value", None),
            "stop_reason": stop_reason,
            "session_patch": session_patch,
            "batch_receipt": batch_receipt,
            "speculative_hints": dict(envelope.speculative_hints),
            "visible_content": envelope.turn_result.visible_content,
            "error": envelope.next_intent if envelope.continuation_mode == TurnContinuationMode.WAITING_HUMAN else None,
            "timestamp_ms": timestamp_ms or 0,
        }

    def _remember_read_files(self, batch_receipt: dict[str, Any]) -> None:
        for result in batch_receipt.get("results", []):
            if not isinstance(result, dict):
                continue
            tool_name = str(result.get("tool_name", ""))
            if tool_name not in _READ_TOOL_NAMES or str(result.get("status", "")) != "success":
                continue
            args = result.get("arguments", {})
            path = self._extract_path_from_payload(args) if isinstance(args, dict) else None
            if not path:
                path = self._extract_path_from_payload(result.get("result"))
            if isinstance(path, str) and path and path not in self.state.read_files:
                self.state.read_files.append(path)

    def _update_materialize_exploration_streak(self, batch_receipt: dict[str, Any]) -> None:
        """Track repeated exploration-only turns for mutation workflows."""
        key = "_exploration_only_streak"
        marker = "EXPLORATION_STREAK_HARD_BLOCK"
        mandatory = str(self.state.structured_findings.get("mandatory_instruction", "") or "")

        if not self._is_materialize_changes_mode():
            self.state.structured_findings.pop(key, None)
            if marker in mandatory:
                cleaned = "\n".join(line for line in mandatory.splitlines() if marker not in line).strip()
                if cleaned:
                    self.state.structured_findings["mandatory_instruction"] = cleaned
                else:
                    self.state.structured_findings.pop("mandatory_instruction", None)
            return

        tool_names: list[str] = []
        for result in batch_receipt.get("results", []):
            if not isinstance(result, dict):
                continue
            name = str(result.get("tool_name", "")).strip()
            if name:
                tool_names.append(name)

        has_write = any(name in _WRITE_TOOL_NAMES for name in tool_names)
        has_read = any(name in _READ_TOOL_NAMES for name in tool_names)
        only_exploration = bool(tool_names) and all(name in _EXPLORATION_ONLY_TOOLS for name in tool_names)

        streak = int(self.state.structured_findings.get(key, 0) or 0)
        if only_exploration and not has_read and not has_write:
            streak += 1
        elif has_read or has_write:
            streak = 0
        self.state.structured_findings[key] = streak

        if streak >= 2 and marker not in mandatory:
            hard_block_line = (
                "EXPLORATION_STREAK_HARD_BLOCK: 已连续多回合只调用探索工具。"
                "下一回合禁止再次仅用 glob/repo_rg/list_directory/repo_tree，"
                "必须至少调用 read_file（或直接 write 工具）。"
            )
            if mandatory.strip():
                self.state.structured_findings["mandatory_instruction"] = f"{mandatory}\n{hard_block_line}"
            else:
                self.state.structured_findings["mandatory_instruction"] = hard_block_line
        elif streak == 0 and marker in mandatory:
            cleaned = "\n".join(line for line in mandatory.splitlines() if marker not in line).strip()
            if cleaned:
                self.state.structured_findings["mandatory_instruction"] = cleaned
            else:
                self.state.structured_findings.pop("mandatory_instruction", None)

    @staticmethod
    def _normalize_batch_receipt(batch_receipt: Any) -> dict[str, Any]:
        if batch_receipt is None:
            return {}
        if isinstance(batch_receipt, dict):
            return dict(batch_receipt)
        model_dump = getattr(batch_receipt, "model_dump", None)
        if callable(model_dump):
            dumped = model_dump()
            return dumped if isinstance(dumped, dict) else {}
        return {}

    @staticmethod
    def _extract_path_from_payload(payload: Any) -> str | None:
        """Extract a file path token from common tool payload shapes."""
        if isinstance(payload, dict):
            for key in ("file", "filepath", "path", "target", "target_file", "file_path"):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    return value
            nested = payload.get("result")
            nested_path = SessionStateReducer._extract_path_from_payload(nested)
            if nested_path:
                return nested_path
        elif isinstance(payload, list):
            for item in payload:
                nested_path = SessionStateReducer._extract_path_from_payload(item)
                if nested_path:
                    return nested_path
        return None

    @staticmethod
    def _normalize_progress(progress: str) -> str:
        candidate = _PHASE_PROGRESS_ALIASES.get(progress, progress)
        return candidate if candidate in _PHASE_PROGRESS_PRIORITY else "exploring"


class RoleSessionOrchestrator:
    """服务端会话编排器。对外是统一入口，对内负责状态机轮转。

    与 StreamShadowEngine v2 深度融合，支持跨 Turn 推测预热。

    Args:
        session_id: 会话唯一标识。
        kernel: TurnTransactionController 实例。
        workspace: 工作区目录。
        role: 角色名，默认 director。
        max_auto_turns: 最大自动连续回合数。
        shadow_engine: 可选的 StreamShadowEngine，用于跨 Turn 推测。
    """

    def __init__(
        self,
        session_id: str,
        kernel: Any,
        workspace: str,
        role: str = "director",
        max_auto_turns: int = 10,
        shadow_engine: Any | None = None,
    ) -> None:
        self.session_id = session_id
        self.kernel = kernel
        self.workspace = workspace
        self.role = role
        self.policy = ContinuationPolicy(max_auto_turns=max_auto_turns)
        self.state = OrchestratorSessionState(
            session_id=session_id,
            goal="",
            turn_count=0,
            max_turns=max_auto_turns,
            artifacts={},
        )
        self._state_reducer = SessionStateReducer(self.state)
        self._artifact_store = SessionArtifactStore(
            workspace=workspace,
            session_id=session_id,
        )
        self._shadow_engine = shadow_engine
        self._slm_warmup_task: asyncio.Task[Any] | None = None
        # 尝试从 checkpoint 恢复会话状态（多 Turn resume）
        self._try_load_checkpoint()

    def _get_role_and_tools(self) -> tuple[str, list[dict[str, Any]]]:
        """Get the role definition and tool definitions for the orchestrator's role (cached per role).

        Builds:
        - role_definition: concise role identity + tool constraints as a system message
        - tool_definitions: OpenAI-format tool schemas from the role's tool whitelist

        This is critical for the orchestrator path where RoleRuntimeService.create_transaction_controller
        does NOT pass tool_definitions to the orchestrator (it only passes them to the TransactionKernel
        internally). Without this fix, the orchestrator passes tool_definitions=[] to the kernel,
        so the LLM never has actual write tool definitions and can't call write_file.

        Returns:
            Tuple of (role_definition_str, tool_definitions_list)
        """
        global _role_profile_cache
        if self.role in _role_profile_cache:
            return _role_profile_cache[self.role]

        role_def = ""
        tool_defs: list[dict[str, Any]] = []

        try:
            from polaris.cells.roles.profile.public.service import load_core_roles, registry

            if not registry.list_roles():
                load_core_roles()
            profile = registry.get_profile(self.role)
            if profile is not None:
                # Build role definition
                parts: list[str] = []
                parts.append(f"You are the {profile.display_name} ({profile.role_id}).")
                if profile.description:
                    parts.append(f"Description: {profile.description}")
                if profile.responsibilities:
                    parts.append("Responsibilities:")
                    for resp in profile.responsibilities:
                        parts.append(f"  - {resp}")

                # Tool constraints (critical for write tool enforcement)
                tool_policy = getattr(profile, "tool_policy", None)
                if tool_policy is not None:
                    whitelist = getattr(tool_policy, "whitelist", None)
                    if whitelist:
                        allowed = ", ".join(whitelist) if whitelist else "none"
                        parts.append(f"Allowed tools: {allowed}")
                    allow_write = getattr(tool_policy, "allow_code_write", False)
                    if allow_write:
                        parts.append("You have permission to write and modify code files.")
                    else:
                        parts.append("You do NOT have permission to write or modify files.")

                role_def = "\n".join(parts)

                # Build tool definitions from role whitelist
                try:
                    from polaris.cells.roles.kernel.public.llm_caller_contracts import (
                        build_native_tool_schemas,
                    )

                    tool_defs = build_native_tool_schemas(profile)
                except Exception:  # noqa: BLE001
                    tool_defs = []

            _role_profile_cache[self.role] = (role_def, tool_defs)
        except Exception:  # noqa: BLE001
            _role_profile_cache[self.role] = ("", [])

        return _role_profile_cache.get(self.role, ("", []))

    async def _retrieve_bootstrapping_knowledge(self, query: str) -> str:
        """Retrieve relevant historical knowledge for session bootstrapping.

        Called at first turn to inject cross-session learned patterns.

        Args:
            query: The session's initial goal/prompt

        Returns:
            Formatted knowledge context to prepend to prompt, or empty string
        """
        if not query or len(query.strip()) < 3:
            return ""

        try:
            from polaris.cells.cognitive.knowledge_distiller.public.contracts import (
                RetrieveKnowledgeQueryV1,
            )
            from polaris.cells.cognitive.knowledge_distiller.public.service import (
                KnowledgeDistillerService,
            )

            service = KnowledgeDistillerService(workspace=self.workspace)
            result = service.retrieve_knowledge(
                RetrieveKnowledgeQueryV1(
                    workspace=self.workspace,
                    query=query,
                    top_k=5,
                    role_filter=self.role,
                    min_confidence=0.4,
                )
            )

            if not result.knowledge_units:
                return ""

            # Format knowledge for prompt injection
            lines = ["\n[Historical Knowledge - Relevant Past Sessions]:\n"]
            for i, unit in enumerate(result.knowledge_units[:3], 1):
                lines.append(f"{i}. [{unit.knowledge_type}] {unit.pattern_summary}")
                if unit.prevention_hint:
                    lines.append(f"   Prevention: {unit.prevention_hint}")
            lines.append("[/Historical Knowledge]\n")

            logger.debug(
                "Bootstrapped session %s with %d knowledge units from %d total",
                self.session_id,
                len(result.knowledge_units),
                result.total_available,
            )
            return "\n".join(lines)

        except (ImportError, AttributeError, RuntimeError) as exc:
            logger.debug("Knowledge bootstrapping failed: %s", exc)
            return ""

    async def close(self) -> None:
        """优雅关闭编排器，取消后台预热任务并清理 Gateway 资源。"""
        if self._slm_warmup_task is not None and not self._slm_warmup_task.done():
            self._slm_warmup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._slm_warmup_task
        # 清理 CognitiveGateway 单例资源（warmup task 等）
        try:
            from polaris.cells.roles.kernel.public.transaction_contracts import (
                CognitiveGateway,
            )

            gateway = CognitiveGateway.get_default_instance_sync()
            if gateway is not None:
                await gateway.close()
        except (ImportError, AttributeError, RuntimeError):
            pass

    async def execute_stream(
        self,
        prompt: str,
        *,
        context: dict[str, Any] | None = None,
    ) -> AsyncIterator[TurnEvent]:
        """流式执行多 Turn 会话编排。"""
        del context

        if self.state.turn_count == 0:
            try:
                from polaris.cells.roles.kernel.public.transaction_contracts import (
                    CognitiveGateway,
                )

                self._slm_warmup_task = asyncio.create_task(CognitiveGateway.default())
            except Exception:  # noqa: BLE001
                logger.debug("SLM singleton warmup trigger failed", exc_info=True)

        yield SessionStartedEvent(session_id=self.session_id)
        first_prompt = prompt
        envelope: TurnOutcomeEnvelope | None = None
        completion_event: CompletionEvent | None = None
        session_terminal_event: SessionWaitingHumanEvent | None = None
        session_completed_reason: str | None = None
        suppress_session_completion = False
        deferred_error: ErrorEvent | None = None
        checkpoint_error: ErrorEvent | None = None

        is_continuation_prompt = first_prompt is not None and (
            "<SESSION_PATCH>" in first_prompt or ("<Goal>" in first_prompt and "<Progress>" in first_prompt)
        )
        is_model_output_regurgitation = (
            first_prompt is not None and self.state.turn_count > 0 and self._is_model_output(first_prompt)
        )
        if is_model_output_regurgitation:
            logger.warning(
                "model_output_regurgitation_blocked: turn=%d prompt=%s...",
                self.state.turn_count,
                first_prompt[:60],
            )
            first_prompt = self.state.original_goal or self.state.goal
        elif first_prompt and first_prompt != self.state.goal and not is_continuation_prompt:
            is_progression_shortcut = self._is_progression_shortcut(first_prompt)
            if is_progression_shortcut and self.state.goal:
                self.state.structured_findings["_user_progression_hint"] = first_prompt
                logger.debug(
                    "goal_preserved_against_shortcut: turn=%d goal=%s hint=%s",
                    self.state.turn_count,
                    self.state.goal[:60],
                    first_prompt[:60],
                )
            else:
                self.state.goal = first_prompt
                if not self.state.original_goal:
                    self.state.original_goal = first_prompt
                    logger.debug("original_goal_set: %s", first_prompt[:60])
                else:
                    logger.debug("original_goal_preserved: %s", self.state.original_goal[:60])
                if self.state.turn_count > 0:
                    execution_markers = {
                        "拆分",
                        "修改",
                        "创建",
                        "新建",
                        "重构",
                        "优化",
                        "修复",
                        "实现",
                        "写入",
                        "删除",
                        "移除",
                        "替换",
                        "更新",
                        "落地",
                        "实施",
                        "完善",
                    }
                    if any(marker in first_prompt for marker in execution_markers):
                        self.state.task_progress = "implementing"
                        self.state.structured_findings["task_progress"] = "implementing"
                logger.debug(
                    "goal_updated_from_new_prompt: turn=%d goal=%s",
                    self.state.turn_count,
                    first_prompt[:60],
                )

        if first_prompt and self.state.turn_count == 0 and not self.state.session_invariants.is_frozen():
            contract = resolve_delivery_mode(first_prompt)
            delivery_mode = contract.mode.value
            self.state.delivery_mode = delivery_mode
            original_goal = self.state.original_goal or self.state.goal or first_prompt
            if not self.state.original_goal:
                self.state.original_goal = original_goal
            initial_phase = self.state.task_progress or "exploring"
            self.state.session_invariants.freeze(
                delivery_mode=delivery_mode,
                original_goal=original_goal,
                phase=initial_phase,
            )

        try:
            while True:
                is_first_turn = self.state.turn_count == 0
                # Phase 3: HARD-GATE — 危险操作必须人类审批，不能自动继续
                if is_first_turn and first_prompt:
                    hard_gate_check = self._check_hard_gate(first_prompt)
                    if hard_gate_check is not None:
                        logger.warning(
                            "hard_gate_triggered: turn=0 reason=%s",
                            hard_gate_check,
                        )
                        yield SessionWaitingHumanEvent(
                            session_id=self.session_id,
                            reason=f"DESTRUCTIVE_OPERATION_REQUIRES_APPROVAL:{hard_gate_check}",
                        )
                        return
                if not is_first_turn and self.state.session_invariants.is_frozen():
                    try:
                        self.state.session_invariants.validate(
                            current_delivery_mode=self.state.delivery_mode,
                            current_original_goal=self.state.original_goal,
                            current_phase=self.state.task_progress,
                        )
                    except InvariantViolationError as inv_exc:
                        logger.error("InvariantViolation detected: %s", inv_exc)
                        deferred_error = ErrorEvent(
                            turn_id=self.session_id,
                            error_type="InvariantViolation",
                            message=f"Session State 不变式违规: {inv_exc}",
                        )
                        session_completed_reason = f"invariant_violation:{inv_exc}"
                        break

                current_prompt = (
                    first_prompt
                    if is_first_turn
                    else self._build_continuation_prompt(envelope or self._build_empty_envelope())
                )
                if is_first_turn and first_prompt:
                    bootstrap_knowledge = await self._retrieve_bootstrapping_knowledge(first_prompt)
                    if bootstrap_knowledge:
                        current_prompt = f"{bootstrap_knowledge}\n\nCurrent Task: {current_prompt}"

                await self._checkpoint_session()

                if self._shadow_engine is not None:
                    has_spec = getattr(self._shadow_engine, "has_valid_speculation", lambda _sid: False)(
                        self.session_id
                    )
                    if has_spec:
                        pre_warmed = await getattr(
                            self._shadow_engine, "consume_speculation", self._noop_consume_speculation
                        )(self.session_id)
                        async for event in self._yield_pre_warmed_events(pre_warmed):
                            yield event

                envelope = None
                completion_event = None
                tool_definitions: list[dict[str, Any]] = []
                role_def, profile_tool_defs = self._get_role_and_tools()
                if profile_tool_defs:
                    tool_definitions = profile_tool_defs

                turn_context: list[dict[str, Any]] = [{"role": "user", "content": current_prompt}]
                if role_def:
                    turn_context.insert(0, {"role": "system", "content": role_def})

                async for event in self.kernel.execute_stream(
                    turn_id=f"{self.session_id}_turn{self.state.turn_count}",
                    context=turn_context,
                    tool_definitions=tool_definitions,
                ):
                    yield event
                    if isinstance(event, CompletionEvent):
                        completion_event = event
                        envelope = self._build_envelope_from_completion(event)

                if envelope is None:
                    deferred_error = ErrorEvent(
                        turn_id=self.session_id,
                        error_type="OrchestratorError",
                        message="Kernel completed without yielding CompletionEvent",
                    )
                    session_completed_reason = "missing_completion_event"
                    break

                envelope = self._state_reducer.enforce_materialize_changes_guard(envelope)
                envelope = self._apply_read_only_termination_exemption(envelope)

                self.state.turn_count += 1
                if envelope.artifacts_to_persist:
                    await self._artifact_store.persist(envelope.artifacts_to_persist)
                    self.state.artifacts.update(self._artifact_store.get_artifact_map())
                if envelope.artifacts_to_persist or self.state.artifacts:
                    self._update_artifact_hashes()

                if self.state.turn_count == 1 and not self.state.goal:
                    self.state.goal = first_prompt
                    if envelope.session_patch and (instruction := envelope.session_patch.get("instruction")):
                        self.state.goal = f"{first_prompt}\n\n[补充指令]: {instruction}"

                turn_record = self._state_reducer.apply_turn_outcome(
                    envelope,
                    turn_index=self.state.turn_count,
                    timestamp_ms=completion_event.timestamp_ms if completion_event else None,
                )

                if "_user_progression_hint" in self.state.structured_findings:
                    del self.state.structured_findings["_user_progression_hint"]

                if envelope.continuation_mode == TurnContinuationMode.HANDOFF_DEVELOPMENT:
                    turn_record["stop_reason"] = "handoff_development"
                    yield TurnPhaseEvent.create(
                        turn_id=self.session_id,
                        phase="workflow_handoff",
                        metadata={"handoff_target": "development", "intent": envelope.next_intent},
                    )
                    if not envelope.session_patch.get("_development_handoff_executed"):
                        runtime = DevelopmentWorkflowRuntime(
                            tool_executor=self.kernel.tool_runtime,
                            shadow_engine=self._shadow_engine,
                        )
                        async for dev_event in runtime.execute_stream(
                            intent=envelope.next_intent or "",
                            session_state=self.state,
                        ):
                            yield dev_event
                    suppress_session_completion = True
                    break

                if envelope.continuation_mode == TurnContinuationMode.HANDOFF_EXPLORATION:
                    turn_record["stop_reason"] = "handoff_exploration"
                    yield TurnPhaseEvent.create(
                        turn_id=self.session_id,
                        phase="workflow_handoff",
                        metadata={"handoff_target": "exploration", "intent": envelope.next_intent},
                    )
                    suppress_session_completion = True
                    break

                if envelope.continuation_mode == TurnContinuationMode.WAITING_HUMAN:
                    turn_record["stop_reason"] = envelope.next_intent or "human_input_required"
                    session_terminal_event = SessionWaitingHumanEvent(
                        session_id=self.session_id,
                        reason=turn_record["stop_reason"],
                    )
                    break

                if envelope.continuation_mode == TurnContinuationMode.END_SESSION:
                    turn_record["stop_reason"] = envelope.next_intent or "end_session"
                    session_completed_reason = turn_record["stop_reason"]
                    break

                can_continue, reason = self.policy.can_continue(self.state, envelope)
                if not can_continue:
                    turn_record["stop_reason"] = reason
                    session_completed_reason = reason
                    break

                if self._shadow_engine is not None:
                    start_cross_turn = getattr(self._shadow_engine, "start_cross_turn_speculation", None)
                    if callable(start_cross_turn):
                        start_cross_turn(
                            session_id=self.session_id,
                            predicted_next_tools=self._predict_next_tools(envelope),
                            hints=envelope.speculative_hints,
                        )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("session orchestrator failed: session_id=%s", self.session_id)
            deferred_error = ErrorEvent(
                turn_id=self.session_id,
                error_type=type(exc).__name__,
                message=str(exc),
            )
            session_completed_reason = "orchestrator_exception"
        finally:
            try:
                await self._checkpoint_session()
            except Exception as exc:
                logger.exception("checkpoint persistence failed: session_id=%s", self.session_id)
                checkpoint_error = ErrorEvent(
                    turn_id=self.session_id,
                    error_type="CheckpointPersistenceError",
                    message=str(exc),
                )
                if (
                    session_completed_reason is None
                    and session_terminal_event is None
                    and not suppress_session_completion
                ):
                    session_completed_reason = "checkpoint_persistence_error"
            try:
                await self.close()
            except Exception:
                logger.exception("session orchestrator close failed: session_id=%s", self.session_id)

        if deferred_error is not None:
            yield deferred_error
        if checkpoint_error is not None:
            yield checkpoint_error
        if session_terminal_event is not None:
            yield session_terminal_event
        elif not suppress_session_completion:
            yield SessionCompletedEvent(session_id=self.session_id, reason=session_completed_reason)

    def _apply_read_only_termination_exemption(self, envelope: TurnOutcomeEnvelope) -> TurnOutcomeEnvelope:
        """Convert read-only auto-continue turns with visible output into final answers."""
        if envelope.continuation_mode != TurnContinuationMode.AUTO_CONTINUE:
            return envelope
        if envelope.turn_result.kind == "continue_multi_turn":
            return envelope

        receipt = self._state_reducer._normalize_batch_receipt(envelope.turn_result.batch_receipt)
        if self._state_reducer._is_materialize_changes_mode() and not self._state_reducer.mutation_obligation_satisfied(
            receipt
        ):
            return envelope
        if not self._state_reducer.is_successful_read_only_execution(receipt):
            return envelope
        results = receipt.get("results", [])
        has_write_tool = any(
            isinstance(result, dict) and str(result.get("tool_name", "")) in _WRITE_TOOL_NAMES for result in results
        )
        has_visible_output = bool(envelope.turn_result.visible_content and envelope.turn_result.visible_content.strip())
        if has_write_tool or not has_visible_output or self.state.turn_count < 1:
            return envelope

        logger.debug(
            "read-only-termination-exempt: turn=%d visible_chars=%d results=%d",
            self.state.turn_count,
            len(envelope.turn_result.visible_content),
            len(results),
        )
        return TurnOutcomeEnvelope(
            turn_result=envelope.turn_result.model_copy(update={"kind": "final_answer"}),
            continuation_mode=TurnContinuationMode.END_SESSION,
            next_intent=envelope.next_intent,
            session_patch=dict(envelope.session_patch),
            artifacts_to_persist=list(envelope.artifacts_to_persist),
            speculative_hints=dict(envelope.speculative_hints),
            failure_class=envelope.failure_class,
        )

    async def _checkpoint_session(self) -> None:
        """持久化当前会话状态到本地 checkpoint 文件。

        包含完整的降维工作记忆，确保 resume 时能真正恢复"上一回合学到了什么"。
        加上 schema_version 以便未来字段迭代时兼容。
        """
        checkpoint_dir = Path(self.workspace) / ".polaris" / "checkpoints"
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
        checkpoint_path = checkpoint_dir / f"{self.session_id}.json"
        payload = self._state_reducer.checkpoint_payload()
        tmp_path = checkpoint_path.with_suffix(f"{checkpoint_path.suffix}.tmp")

        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, default=str)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, checkpoint_path)

        # Phase 1.5: 将 structured_findings 作为派生记忆持久化到 artifact store
        # 标记为 derived_memory（非独立 truth source，可从 truthlog 重建）
        if self.state.structured_findings:
            await self._artifact_store.store_structured_findings(
                findings=self.state.structured_findings,
                source_turn_id=f"{self.session_id}_turn{self.state.turn_count}",
                schema_version="1.0",
            )

    def _build_continuation_prompt(self, envelope: TurnOutcomeEnvelope) -> str:
        """基于降维后的 structured_findings 构建下一 Turn 的 continuation prompt。

        使用 4-zone XML 结构（ADR-0071 上下文降维 Step 4）：
        - <Goal>: 当前任务目标
        - <Progress>: 当前阶段与回合
        - <WorkingMemory>: 已确认事实 / 待验证假设 / 最近失败 / 工具执行结果
        - <Instruction>: 下一 Turn 的行动指引

        关键原则：LLM 只看 structured_findings，不看原始 artifacts。

        注入 batch_receipt 中的工具执行结果到 WorkingMemory（fixes "missing code context in turn 2" bug）：
        上回合的工具返回结果（如 read_file 的文件内容）直接作为 WorkingMemory 的一部分，
        让 LLM 在下一回合能"看见"之前读取的文件内容，而非面对空上下文。
        """
        findings = get_active_findings(self.state.structured_findings)
        # FIX-20250421: 使用 original_goal（永不丢失）替代 goal（可能被覆盖）
        goal = self.state.original_goal or self.state.goal or "（未设定明确目标）"
        progress = self.state.task_progress
        normalized_progress = self._state_reducer._normalize_progress(progress)
        turn = self.state.turn_count
        max_turns = self.state.max_turns

        # --- Zone 1: Goal ---
        # FIX-20250421: 强制置顶原始目标，不可变更
        # FIX-20250421-v4: 注入 delivery_mode，确保跨 Turn 不丢失
        # FIX-20250422-SUPER: SUPER_MODE handoff 强制使用 materialize_changes
        _delivery_mode = getattr(self.state, "delivery_mode", None) or "unknown"
        if "[SUPER_MODE_HANDOFF]" in goal or "[/SUPER_MODE_HANDOFF]" in goal:
            _delivery_mode = "materialize_changes"
        goal_block = f"【核心任务 - 不可变更】\n{goal}\n\n当前执行目标: {self.state.goal or goal}\n<DeliveryMode>{_delivery_mode}</DeliveryMode>"

        # --- Zone 2: Progress ---
        phase_alias = self._state_reducer.current_phase().value
        if _PHASE_PROGRESS_PRIORITY.get(normalized_progress, 0) > _PHASE_PROGRESS_PRIORITY.get(phase_alias, 0):
            phase_alias = normalized_progress
        if progress == "done":
            phase_alias = "done"
        progress_block = f"当前阶段: {phase_alias} | 回合: {turn} / {max_turns}"

        # --- Zone 3: WorkingMemory ---
        # 已确认的事实
        confirmed_facts: list[str] = []
        if error_summary := findings.get("error_summary"):
            confirmed_facts.append(f"错误摘要: {error_summary}")
        if suspected := findings.get("suspected_files"):
            files = suspected if isinstance(suspected, list) else [suspected]
            confirmed_facts.append(f"疑似问题文件: {', '.join(files)}")
        if patched := findings.get("patched_files"):
            files = patched if isinstance(patched, list) else [patched]
            confirmed_facts.append(f"已修复文件: {', '.join(files)}")
        if verified := findings.get("verified_results"):
            verified_list = verified if isinstance(verified, list) else [verified]
            confirmed_facts.append(f"验证结果: {', '.join(verified_list)}")

        # 待验证的假设
        pending_hypotheses: list[str] = []
        if pending := findings.get("pending_files"):
            pending_hypotheses = pending if isinstance(pending, list) else [pending]

        # 最近失败
        recent_failure = ""
        if self.state.last_failure:
            recent_failure = self.state.last_failure.get("summary", str(self.state.last_failure))

        # 构建 WorkingMemory
        wm_parts: list[str] = []
        if confirmed_facts:
            wm_parts.append("已确认:")
            for fact in confirmed_facts:
                wm_parts.append(f"  - {fact}")
        # FIX-20250421: 显示真正读取过的文件（从 state.read_files），只保留最近 5 个
        if self.state.read_files:
            _recent_files = self.state.read_files[-5:]  # 限制最近 5 个文件
            wm_parts.append("已成功读取的文件:")
            wm_parts.append(f"  - {', '.join(_recent_files)}")
            if len(self.state.read_files) > 5:
                wm_parts.append(f"  ... 还有 {len(self.state.read_files) - 5} 个更早的文件")
        # 注入最近使用的读工具（来自 continue_multi_turn 的 SESSION_PATCH）
        if recent_reads := findings.get("recent_reads"):
            reads = recent_reads if isinstance(recent_reads, list) else [recent_reads]
            wm_parts.append("最近读取工具:")
            wm_parts.append(f"  - {', '.join(reads)}")
        if pending_hypotheses:
            wm_parts.append("待验证:")
            for hyp in pending_hypotheses:
                wm_parts.append(f"  - {hyp}")
        if recent_failure:
            wm_parts.append("最近失败:")
            wm_parts.append(f"  - {recent_failure}")
        if mandatory_instruction := findings.get("mandatory_instruction"):
            wm_parts.append("强制推进要求:")
            wm_parts.append(f"  - {mandatory_instruction}")

        # FIX-20250422-v3: 注入 ModificationContract 状态到 WorkingMemory
        if self._state_reducer.modification_contract.status.value != "empty":
            wm_parts.append(self._state_reducer.modification_contract.format_for_prompt())

        # 【关键修复】注入上回合 LLM 自己的 visible_content 到 WorkingMemory。
        # 根因：跨回合时 LLM 面对空 WorkingMemory，丢失自己上一回合的分析结论、
        # 实施步骤、错误码字典等关键上下文，导致每次 turn 都从零开始。
        # Fix：把 Turn N 的 visible_content 直接塞进 Turn N+1 的 WorkingMemory。
        # FIX-20250421-P2: 减少 WorkingMemory token 消耗，从 3000 降到 1500 chars
        _prev_visible = getattr(envelope.turn_result, "visible_content", None) or ""
        if _prev_visible and str(_prev_visible).strip():
            _prev_stripped = str(_prev_visible).strip()
            # 截断避免 token 爆炸（1500 chars 约 375 tokens）
            if len(_prev_stripped) > 1500:
                _prev_stripped = _prev_stripped[:1500] + f"\n... [truncated, total {len(_prev_stripped)} chars]"
            wm_parts.append("上回合分析结果（你自己的输出）:")
            wm_parts.append(f"  {_prev_stripped}")

        # === 注入上回合工具执行结果（fixes "missing code context in turn 2" bug）===
        # 从 batch_receipt 提取工具结果，追加到 WorkingMemory
        batch_receipt = getattr(envelope.turn_result, "batch_receipt", None) or {}
        results: list[dict[str, Any]] = batch_receipt.get("results", [])
        if results:

            def _extract_path(args_payload: Any, result_payload: Any) -> str:
                if isinstance(args_payload, dict):
                    path = SessionStateReducer._extract_path_from_payload(args_payload)
                    if path:
                        return path
                path = SessionStateReducer._extract_path_from_payload(result_payload)
                return path or "unknown"

            def _extract_glob_entries(result_payload: Any) -> list[str]:
                if isinstance(result_payload, dict):
                    direct = result_payload.get("results")
                    if isinstance(direct, list):
                        return [str(item) for item in direct]
                    nested = result_payload.get("result")
                    if isinstance(nested, dict):
                        nested_results = nested.get("results")
                        if isinstance(nested_results, list):
                            return [str(item) for item in nested_results]
                    if isinstance(nested, list):
                        return [str(item) for item in nested]
                if isinstance(result_payload, list):
                    return [str(item) for item in result_payload]
                return []

            def _extract_search_hits(result_payload: Any) -> tuple[str, list[dict[str, Any]]]:
                if isinstance(result_payload, dict):
                    query = str(result_payload.get("query", "") or "")
                    hit_rows = result_payload.get("results")
                    if isinstance(hit_rows, list):
                        return query, [row for row in hit_rows if isinstance(row, dict)]
                    nested = result_payload.get("result")
                    if isinstance(nested, dict):
                        nested_query = str(nested.get("query", "") or "")
                        nested_hits = nested.get("results")
                        if isinstance(nested_hits, list):
                            return nested_query or query, [row for row in nested_hits if isinstance(row, dict)]
                return "", []

            tool_result_lines: list[str] = []
            for item in results:
                tool_name = str(item.get("tool_name", ""))
                status = str(item.get("status", ""))
                success = status == "success"
                result_data = item.get("result")
                args = item.get("arguments", {})

                if tool_name in {"read_file", "repo_read_head"} and success:
                    path = _extract_path(args, result_data)
                    # FIX-20250421: 记录真正读取过的文件
                    if path and path not in self.state.read_files:
                        self.state.read_files.append(path)
                    content = ""
                    if isinstance(result_data, dict):
                        nested_result = result_data.get("result")
                        if isinstance(nested_result, dict):
                            content = str(nested_result.get("content", nested_result.get("result", "")))
                        elif isinstance(nested_result, str):
                            content = nested_result
                        else:
                            content = str(result_data.get("content", ""))
                    elif isinstance(result_data, str):
                        content = result_data
                    # FIX-20250422: 避免在 WorkingMemory 中显示截断标记，防止 LLM 误判为未完整读取
                    # 对于大文件，只显示前 500 字符的预览 + 明确说明已完整读取
                    content_len = len(content)
                    if content_len > 500:
                        preview = content[:500]
                        # 确保预览在行边界截断
                        last_newline = preview.rfind("\n")
                        if last_newline > 400:
                            preview = preview[:last_newline]
                        tool_result_lines.append(
                            f"  文件 `{path}` (已完整读取 {content_len} 字符):\n"
                            f"{preview}\n"
                            f"  ... [以上为前 {len(preview)} 字符预览，完整内容已通过工具读取，可直接用于修改]"
                        )
                    else:
                        tool_result_lines.append(f"  文件 `{path}` ({content_len} chars):\n{content}")
                elif tool_name == "repo_rg" and success:
                    pattern = ""
                    if isinstance(args, dict):
                        pattern = str(args.get("pattern", "") or "")
                    query, hits = _extract_search_hits(result_data)
                    effective_query = pattern or query or "(empty-pattern)"
                    if hits:
                        preview_tokens: list[str] = []
                        for row in hits[:8]:
                            file_token = str(row.get("file", "?"))
                            line_token = row.get("line")
                            if line_token is not None:
                                preview_tokens.append(f"{file_token}:{line_token}")
                            else:
                                preview_tokens.append(file_token)
                        tool_result_lines.append(
                            f"  搜索 `{effective_query}` 命中 {len(hits)} 处: {', '.join(preview_tokens)}"
                        )
                    else:
                        tool_result_lines.append(f"  搜索 `{effective_query}` 未返回可用命中")
                elif tool_name in {"list_directory", "glob"} and success:
                    path = "."
                    if isinstance(args, dict):
                        path = str(args.get("path", ".") or ".")
                    if isinstance(result_data, dict):
                        path = str(result_data.get("path", path) or path)
                    entries = _extract_glob_entries(result_data)
                    if entries:
                        names = [e.get("name", str(e)) if isinstance(e, dict) else str(e) for e in entries[:20]]
                        tool_result_lines.append(f"  目录 `{path}` 包含: {', '.join(names)}")
                    else:
                        tool_result_lines.append(f"  目录 `{path}` 未返回条目")
                elif not success:
                    tool_result_lines.append(f"  {tool_name} 执行失败: {item.get('error', 'unknown error')}")

            if tool_result_lines:
                # FIX-20250421-P2: 限制工具结果总大小，避免 token 爆炸
                _max_tool_result_chars: int = 3000  # 工具结果总预算
                _current_length: int = 0
                _filtered_lines: list[str] = []
                for line in tool_result_lines:
                    if _current_length + len(line) <= _max_tool_result_chars:
                        _filtered_lines.append(line)
                        _current_length += len(line)
                    else:
                        _filtered_lines.append(
                            f"  ... 还有 {len(tool_result_lines) - len(_filtered_lines)} 个工具结果被省略"
                        )
                        break
                wm_parts.append("上回合工具执行结果:")
                wm_parts.extend(_filtered_lines)

        if not wm_parts:
            wm_parts.append("（暂无工作记忆）")
        working_memory_block = "\n".join(wm_parts)

        # --- Zone 4: Instruction ---
        # 【关键修复】Instruction 动态注入实际 goal，防止 LLM 执行错误的 action。
        # 根因：硬编码的 "请继续探索和分析问题" 与用户的实际请求（如"拆分 server.py"）不匹配，
        # 导致 LLM 盲目读文件而不是执行写入。
        _goal_snippet = goal_block[:80] if len(goal_block) > 80 else goal_block

        # 【关键修复】注入用户的推进类短句（如"开始落地啊"）到 Instruction 中。
        # 根因：推进短句被丢弃，LLM 不知道用户最新的催促/推进意图。
        _progression_hint = findings.get("_user_progression_hint", "")
        _mandatory_instruction = findings.get("mandatory_instruction", "")
        _hint_line = f"【用户最新指令】{_progression_hint}\n" if _progression_hint else ""
        _mandatory_line = f"【系统强制要求】{_mandatory_instruction}\n" if _mandatory_instruction else ""
        _exploration_streak = int(findings.get("_exploration_only_streak", 0) or 0)
        _materialize_exploring_instruction = (
            "当前任务是代码修改（MATERIALIZE_CHANGES）。本回合必须执行工具动作，禁止纯文本分析。"
        )
        if self._state_reducer._is_materialize_changes_mode():
            if self.state.read_files:
                _materialize_exploring_instruction += (
                    "你已经有可用读取上下文，下一步必须直接调用 write_file/edit_file 等写工具。"
                    "禁止继续重复 glob/repo_rg。"
                )
            else:
                _materialize_exploring_instruction += (
                    "必须先对已定位候选文件调用 read_file；仅当候选文件仍未知时，才允许一次 glob/repo_rg。"
                )
            if _exploration_streak >= 2:
                _materialize_exploring_instruction += (
                    " EXPLORATION_STREAK_HARD_BLOCK: 当前已触发探索熔断，"
                    "禁止再次仅调用 glob/repo_rg/list_directory/repo_tree。"
                )
        else:
            _materialize_exploring_instruction = "请继续探索和分析。优先确认问题根因，收集必要信息后再决定修复方案。"

        # FIX-20250422: 角色专业化提示词
        # Director 只负责执行，不负责探索；Architect/PM 负责规划和探索
        _role_hint = ""
        if self.role == "director":
            _role_hint = (
                "【角色定位】你是 Director（工部侍郎），职责是执行代码修改，不是探索或规划。"
                "你只使用 read_file 确认已知文件内容，然后立即用 write_file/edit_file 执行修改。"
                "严禁使用 repo_tree/repo_rg/glob/list_directory 等探索工具。"
            )
        elif self.role == "architect":
            _role_hint = (
                "【角色定位】你是 Architect（中书令），职责是架构设计和蓝图制定。"
                "你可以使用探索工具了解代码库结构，然后输出清晰的设计文档和修改计划。"
            )
        elif self.role == "pm":
            _role_hint = (
                "【角色定位】你是 PM（尚书令），职责是任务分解和项目管理。"
                "你可以使用探索工具了解项目状态，然后输出可执行的任务列表和验收标准。"
            )

        instruction_map = {
            "exploring": (
                f"{_mandatory_line}{_hint_line}当前任务：{_goal_snippet}。\n{_materialize_exploring_instruction}\n{_role_hint}"
            ),
            "content_gathered": (
                f"{_mandatory_line}{_hint_line}当前任务：{_goal_snippet}。\n{_role_hint}\n"
                + (
                    "你的修改计划已确认。现在必须直接执行写入修改，禁止继续用 glob/repo_rg 扩散探索。"
                    if self._state_reducer.modification_contract.status.value == "ready"
                    else "你已经完成必要读取。请在 SESSION_PATCH 中声明 modification_plan "
                    '（格式: [{"target_file": "path", "action": "描述"}]），然后执行写入修改。'
                )
            ),
            "investigating": (
                f"{_mandatory_line}{_hint_line}当前任务：{_goal_snippet}。\n{_role_hint}\n继续深入调查。已识别疑似文件，关注错误栈和调用链。"
            ),
            "implementing": (
                f"{_mandatory_line}{_hint_line}当前任务：{_goal_snippet}。\n{_role_hint}\n"
                f"现在进入修复阶段。请按最小改动原则执行修改，使用 write_file/edit_file 等工具落实代码变更。"
                f"严禁继续调用 repo_tree/read_file/glob/repo_rg 等探索工具——直接执行写入。"
            ),
            "verifying": (
                f"{_mandatory_line}{_hint_line}当前任务：{_goal_snippet}。\n{_role_hint}\n验证阶段。请运行测试或手动验证修复效果，确保无回归。"
            ),
            "done": (
                f"{_mandatory_line}{_hint_line}当前任务：{_goal_snippet}。\n{_role_hint}\n任务已完成。请汇总结果并以 END_SESSION 结束。"
            ),
        }
        instruction = instruction_map.get(
            progress,
            instruction_map.get(normalized_progress, f"{_mandatory_line}{_hint_line}继续执行任务：{_goal_snippet}。"),
        )

        return (
            f"<Goal>\n{goal_block}\n</Goal>\n"
            f"<Progress>\n{progress_block}\n</Progress>\n"
            f"<WorkingMemory>\n{working_memory_block}\n</WorkingMemory>\n"
            f"<Instruction>\n{instruction}\n</Instruction>"
        )

    @staticmethod
    def _is_progression_shortcut(prompt: str) -> bool:
        """检测用户输入是否为"推进类短句"（如"开始落地啊"）。

        推进类短句的特征：
        1. 长度很短（< 20 字符）
        2. 包含推进词（继续、开始、落地、去、写、改、做、执行、推进、动手）
        3. 不包含明确的文件/功能/模块名称（如 .py, server, file）
        4. 不包含明确的动作目标（如"拆分 server.py"）

        如果检测到推进类短句，应保留原 goal，将短句注入到 <Instruction> 中。
        """
        if not prompt or len(prompt) > 20:
            return False

        # 【修复 ContextOS 状态同步】：强执行动词（落地、实施、写、改、执行）
        # 本身就意味着完整的执行意图，不应被视为"保留旧 goal 的推进短句"。
        # 只保留弱推进词（继续、开始、去、做、推进等）。
        _progression_markers = {
            "继续",
            "开始",
            "去",
            "做",
            "推进",
            "动手",
            "干",
            "上",
            "弄",
            "搞",
        }
        has_progression = any(m in prompt for m in _progression_markers)
        if not has_progression:
            return False

        # 如果包含明确的文件/模块/功能名称，说明是完整指令，不是推进类短句
        _explicit_target_markers = {
            ".py",
            ".js",
            ".ts",
            ".json",
            "server",
            "file",
            "模块",
            "文件",
            "函数",
            "类",
            "接口",
            "路由",
            "服务",
        }
        has_explicit_target = any(m in prompt.lower() for m in _explicit_target_markers)
        if has_explicit_target:
            return False

        # 【修复 ContextOS 上下文指代】：如果用户提到了上一轮的分析结果
        # （"以上"、"这些"、"前文"等），说明这是基于上下文的完整执行指令，
        # 不是推进短句。如"落地以上建议" = 完整指令，不应保留旧 goal。
        _context_referencers = {"以上", "这些", "这个", "前文", "前面", "上述"}
        return not any(m in prompt for m in _context_referencers)

    @staticmethod
    def _is_model_output(text: str) -> bool:
        """检测文本是否为模型输出回灌（而非真实用户输入）。

        模型输出的特征（基于审计日志中的模式）：
        1. 包含 markdown 标题（### 建议后续行动）
        2. 包含 "目标文件已定位"、"相关模块" 等分析性语言
        3. 包含编号列表（1. **立即读取**）
        4. 包含 "下一步："、"预期改进方向" 等总结性语言

        Args:
            text: 待检测的文本

        Returns:
            True 如果文本看起来像模型输出
        """
        if not text:
            return False
        # 模型输出特征模式
        _model_output_markers = [
            "### ",  # markdown 标题
            "**目标文件已定位**",
            "**相关模块**",
            "**建议后续行动**",
            "**预期改进方向**",
            "**关键发现**",
            "**分析与总结**",
            "**下一步：**",
            "1. **",
            "2. **",
            "3. **",
            "4. **",
            "- 主文件：",
            "- 测试文件：",
            "立即读取",
            "检查测试覆盖",
            "确认功能完整性",
            "集成点检查",
        ]
        _marker_hits = sum(1 for m in _model_output_markers if m in text)
        # 命中 3 个及以上特征即判定为模型输出
        return _marker_hits >= 3

    @staticmethod
    def _build_empty_envelope() -> TurnOutcomeEnvelope:
        """创建空 envelope（用于首次非 first_turn 的类型安全降级）。"""
        return TurnOutcomeEnvelope(
            turn_result=TurnResult(
                turn_id="",  # type: ignore[arg-type]
                kind="final_answer",  # type: ignore[arg-type]
                visible_content="",
                decision={},
            ),
            continuation_mode=TurnContinuationMode.END_SESSION,
            session_patch={},
        )

    @staticmethod
    def _build_envelope_from_completion(event: CompletionEvent) -> TurnOutcomeEnvelope:
        """从 CompletionEvent 构建 TurnOutcomeEnvelope（ADR-0080 工作记忆管线集成）。

        核心改进：不再硬编码 kind="final_answer" 和 continuation_mode=AUTO_CONTINUE，
        而是从 CompletionEvent 的结构化字段（turn_kind / error）直接推断，
        实现数据平面与控制平面的隔离。

        ADR-0080：从 event.visible_content 中提取 <SESSION_PATCH> 块，
        注入 session_patch；将已提取的块从 visible_content 中剥离。

        TurnId 在 Pydantic 模型中被定义为 NewType("TurnId", str)，直接传 str 即可。
        """
        # ADR-0080: 从 LLM 输出文本中提取 session_patch（若已预解析则直接用）
        session_patch: dict[str, Any]
        if event.session_patch:
            session_patch = event.session_patch
        else:
            extracted = extract_session_patch_from_text(event.visible_content)
            session_patch = dict(extracted) if extracted else {}

        # ADR-0080: 将 SESSION_PATCH 块从 visible_content 中剥离（不在回复中暴露）
        visible_content = strip_session_patch_block(event.visible_content)

        # 从 CompletionEvent 的结构化信号推断 continuation_mode（数据平面/控制平面隔离）
        turn_kind = event.turn_kind or "final_answer"
        if turn_kind == "ask_user":
            continuation_mode = TurnContinuationMode.WAITING_HUMAN
        elif turn_kind == "handoff_workflow":
            continuation_mode = TurnContinuationMode.HANDOFF_EXPLORATION
        elif turn_kind == "handoff_development":
            continuation_mode = TurnContinuationMode.HANDOFF_DEVELOPMENT
        elif turn_kind == "continue_multi_turn":
            continuation_mode = TurnContinuationMode.AUTO_CONTINUE
        elif turn_kind == "final_answer":
            continuation_mode = TurnContinuationMode.END_SESSION
        elif turn_kind in ("inline_patch_escape_blocked", "mutation_bypass_blocked"):
            # Blocked finalization kinds must end the session, not AUTO_CONTINUE
            continuation_mode = TurnContinuationMode.END_SESSION
        else:
            # tool_batch_with_receipt → 默认自动继续
            continuation_mode = TurnContinuationMode.AUTO_CONTINUE

        # 从 session_patch 或 decision metadata 透传 next_intent（供 HANDOFF_DEVELOPMENT 使用）
        next_intent: str | None = session_patch.get("next_intent")
        if not next_intent:
            next_intent = event.error if turn_kind == "ask_user" else None

        failure_class: FailureClass | None = None
        if event.status == "failed" and turn_kind != "ask_user":
            failure_class = FailureClass.RUNTIME_FAILURE

        # TurnId = NewType("TurnId", str)，直接使用字符串
        turn_result = TurnResult(
            turn_id=event.turn_id,  # type: ignore[arg-type]
            kind=turn_kind,  # type: ignore[arg-type]
            visible_content=visible_content,
            decision={},
            batch_receipt=event.batch_receipt,
        )

        return TurnOutcomeEnvelope(
            turn_result=turn_result,
            continuation_mode=continuation_mode,
            next_intent=next_intent,
            session_patch=session_patch,
            failure_class=failure_class,
        )

    def _update_artifact_hashes(self) -> None:
        """计算当前 artifacts 的指纹并追加到 recent_artifact_hashes。

        用于 stagnation 检测：若连续两回合指纹相同且没有 speculative_hints，
        则判定为哈希停滞（ADR-0080）。
        """
        import hashlib
        import json

        fingerprint = hashlib.sha256(
            json.dumps(self.state.artifacts, sort_keys=True, default=str).encode()
        ).hexdigest()[:16]
        self.state.recent_artifact_hashes.append(fingerprint)
        # 保留最近 5 条，防止无界增长
        self.state.recent_artifact_hashes = self.state.recent_artifact_hashes[-5:]

    @staticmethod
    async def _noop_consume_speculation(_session_id: str) -> dict[str, Any]:
        return {}

    def _try_load_checkpoint(self) -> None:
        """尝试从 checkpoint 文件恢复会话状态。

        如果 checkpoint 文件存在且 schema_version 匹配，则恢复降维工作记忆；
        否则使用默认初始状态（is_first_turn=True）。
        静默失败（文件不存在/损坏/版本不兼容），不影响主流程。
        """
        checkpoint_path = Path(self.workspace) / ".polaris" / "checkpoints" / f"{self.session_id}.json"
        if not checkpoint_path.exists():
            return
        try:
            self._load_checkpoint(checkpoint_path)
        except (OSError, ValueError, KeyError) as exc:
            # checkpoint 文件不存在（OSError）、schema 不兼容（ValueError）
            # 或数据损坏（KeyError），静默忽略，使用初始状态
            logger.warning("Failed to load checkpoint %s: %s", checkpoint_path, exc)

    def _load_checkpoint(self, checkpoint_path: Path) -> None:
        """从 checkpoint JSON 文件恢复 OrchestratorSessionState。

        读取完整的降维工作记忆（structured_findings, task_progress,
        key_file_snapshots, turn_count 等），使多 Turn 会话能够从中断处继续。

        Raises:
            FileNotFoundError: checkpoint 文件不存在（调用方已验证）
            ValueError: schema_version 不匹配或数据损坏
        """
        with open(checkpoint_path, encoding="utf-8") as handle:
            data: dict[str, Any] = json.load(handle)

        schema_version = data.get("schema_version")
        if schema_version not in {2, 3, 4, 5}:
            raise ValueError(f"Unsupported checkpoint schema_version: {schema_version}")

        # 恢复 state 字段
        self.state.session_id = data.get("session_id", self.session_id)
        self.state.goal = data.get("goal", "")
        self.state.turn_count = data.get("turn_count", 0)
        self.state.task_progress = data.get("task_progress", "exploring")
        self.state.structured_findings = data.get("structured_findings", {})
        self.state.key_file_snapshots = data.get("key_file_snapshots", {})
        self.state.last_failure = data.get("last_failure")
        self.state.artifacts = data.get("artifacts", {})
        self.state.recent_artifact_hashes = data.get("recent_artifact_hashes", [])
        self.state.turn_history = data.get("turn_history", [])

        # FIX-20250421-v3: 恢复 Session State 硬化字段（schema_version >= 3）
        if schema_version >= 3:
            self.state.original_goal = data.get("original_goal", "")
            self.state.read_files = data.get("read_files", [])
            self.state.delivery_mode = data.get("delivery_mode")
            _invariants_data = data.get("session_invariants")
            if _invariants_data:
                self.state.session_invariants = SessionInvariants.from_dict(_invariants_data)
                logger.debug("session_invariants_restored: delivery_mode=%s", self.state.delivery_mode)

        self._state_reducer.restore_phase_manager(
            data.get("phase_manager") if schema_version >= 4 else None,
            fallback_progress=self.state.task_progress,
        )

        # Schema v5: restore ModificationContract
        self._state_reducer.restore_modification_contract(
            data.get("modification_contract") if schema_version >= 5 else None,
        )

    @staticmethod
    async def _yield_pre_warmed_events(_pre_warmed: dict[str, Any]) -> AsyncIterator[TurnEvent]:
        """产出 ShadowEngine 预热结果的事件流。骨架实现，待 ShadowEngine v2 扩展。"""
        return
        yield  # type: ignore[unreachable]

    @staticmethod
    def _predict_next_tools(envelope: TurnOutcomeEnvelope) -> list[dict[str, Any]]:
        """基于当前 Envelope 预测下一 Turn 可能的工具调用。"""
        predicted: list[dict[str, Any]] = []
        intent = (envelope.next_intent or "").lower()
        if "test" in intent or "pytest" in intent:
            predicted.append({"tool_name": "execute_command", "arguments": {"command": "pytest"}})
        if "write" in intent or "patch" in intent or "fix" in intent:
            predicted.append({"tool_name": "write_file", "arguments": {"path": "", "content": ""}})
        if "read" in intent or "查看" in intent:
            predicted.append({"tool_name": "read_file", "arguments": {"path": ""}})
        return predicted

    # ---------------------------------------------------------------------------
    # HARD-GATE Approval Protocol（危险操作强制人类审批）
    # ---------------------------------------------------------------------------

    # 危险操作关键词 — 匹配到则触发 HARD-GATE
    _HARD_GATE_TRIGGERS: frozenset[str] = frozenset(
        {
            "rm -rf",
            "rm -r /",
            "delete all",
            "drop table",
            "drop database",
            "truncate table",
            "reset hard",
            "force push --force",
            "force push origin",
            "--force-with-lease",
            "chmod -r 777",
            "chmod 777",
        }
    )

    # 破坏性操作类别 → 人类需要确认的操作
    _DESTRUCTIVE_OPERATION_MARKERS: dict[str, str] = {
        "rm -rf": "DELETE_DIRECTORY_RECURSIVE",
        "rm -r /": "DELETE_FILESYSTEM_ROOT",
        "delete all": "DELETE_ALL_FILES",
        "drop table": "DROP_DATABASE_TABLE",
        "drop database": "DROP_DATABASE",
        "truncate table": "TRUNCATE_TABLE",
        "reset hard": "GIT_RESET_HARD",
        "force push": "GIT_FORCE_PUSH",
        "chmod -r 777": "PERMISSION_ESCALATION_777",
        "chmod 777": "PERMISSION_ESCALATION_777",
    }

    @classmethod
    def _check_hard_gate(cls, prompt: str) -> str | None:
        """检测危险操作，返回操作类型字符串；若无需审批则返回 None。

        HARD-GATE 协议（来自 Superpowers brainstorming skill）：
        重大破坏性操作必须人类审批，不能自动执行。

        检测逻辑：prompt 全文匹配危险关键词列表。
        """
        if not prompt:
            return None
        prompt_lower = prompt.lower()
        for trigger in cls._HARD_GATE_TRIGGERS:
            if trigger in prompt_lower:
                op_type = cls._DESTRUCTIVE_OPERATION_MARKERS.get(trigger, trigger.upper())
                return op_type
        return None
