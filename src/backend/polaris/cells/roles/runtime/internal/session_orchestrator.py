"""Session Orchestrator - 服务端会话编排器。

负责回合级状态机轮转、ContinuationPolicy 仲裁、ShadowEngine 跨 Turn 预热，
以及 DevelopmentWorkflowRuntime 的 handoff 路由。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from polaris.cells.roles.kernel.internal.development_workflow_runtime import (
    DevelopmentWorkflowRuntime,
)
from polaris.cells.roles.kernel.public.turn_contracts import (
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
from polaris.cells.roles.runtime.internal.continuation_policy import (
    ContinuationPolicy,
    OrchestratorSessionState,
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
            from polaris.cells.roles.profile.internal.registry import load_core_roles, registry

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
                    from polaris.cells.roles.kernel.internal.llm_caller.tool_helpers import (
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
            from polaris.cells.roles.kernel.internal.transaction.cognitive_gateway import (
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
        """流式执行多 Turn 会话编排。

        核心循环：
        1. Checkpoint 会话状态
        2. 复用 ShadowEngine 推测结果（如有）
        3. 执行单次干净 Turn（内核零修改）
        4. 接收 TurnOutcomeEnvelope
        5. 增量持久化 Artifacts
        6. ContinuationPolicy 仲裁 + 路由分支
        7. 触发下一 Turn 推测预热
        """
        # 触发 SLM 全局单例预热（fire-and-forget）。
        # 当第一个 Turn 执行到 ContextOS Canonicalizer 时，模型大概率已驻留 VRAM。
        if self.state.turn_count == 0:
            try:
                from polaris.cells.roles.kernel.internal.transaction.cognitive_gateway import (
                    CognitiveGateway,
                )

                self._slm_warmup_task = asyncio.create_task(CognitiveGateway.default())
            except Exception:  # noqa: BLE001
                logger.debug("SLM singleton warmup trigger failed", exc_info=True)

        yield SessionStartedEvent(session_id=self.session_id)
        _first_prompt = prompt  # 用户传入的原始消息作为第一回合 prompt
        # 【关键修复】：prompt 在循环前初始化，每次迭代结束时更新。
        # 修复了第 2+ 回合 continuation prompt 丢失工作记忆的 bug。
        # 注意：is_first_turn 在每次迭代开始时重新计算，不在循环外设置一次。
        envelope: TurnOutcomeEnvelope | None = None

        # 【关键修复】：如果用户发送了新请求（与当前 goal 不同），更新 goal。
        # 根因：用户在同一会话中发送新指令（如"拆分 server.py"），但 goal 仍停留在旧的"总结代码"。
        # 导致 continuation prompt 的 <Goal> 和 <Instruction> 都反映旧意图，LLM 执行错误的 action。
        # 【关键修复】：检测"推进类短句"（如"开始落地啊"），保留原 goal，将推进指令注入 Instruction。
        # 根因：短推进指令覆盖完整 goal，导致 LLM 丢失"落地什么东西"的上下文。
        #
        # 【关键修复】：CONTINUATION TURNS 不应更新 goal。
        # 当 prompt 包含 SESSION_PATCH 或 <Goal>/<Progress> XML 块时，说明这是 orchestrator
        # 生成的 continuation prompt，不是真实用户输入。绝对不能把它当作新 goal！
        _is_continuation_prompt = _first_prompt is not None and (
            "<SESSION_PATCH>" in _first_prompt or ("<Goal>" in _first_prompt and "<Progress>" in _first_prompt)
        )
        if _first_prompt and _first_prompt != self.state.goal and not _is_continuation_prompt:
            _is_progression_shortcut = self._is_progression_shortcut(_first_prompt)
            if _is_progression_shortcut and self.state.goal:
                # 保留原 goal，将推进指令作为额外上下文存储
                self.state.structured_findings["_user_progression_hint"] = _first_prompt
                logger.debug(
                    "goal_preserved_against_shortcut: turn=%d goal=%s hint=%s",
                    self.state.turn_count,
                    self.state.goal[:60],
                    _first_prompt[:60],
                )
            else:
                self.state.goal = _first_prompt
                # FIX-20250421: 首次设置 goal 时，同时保存 original_goal（永不丢失）
                if not self.state.original_goal:
                    self.state.original_goal = _first_prompt
                    logger.debug("original_goal_set: %s", _first_prompt[:60])
                # 只有非第一回合才允许根据新请求跃迁到 implementing。
                # 第一回合总是 exploring——LLM 需要先读代码了解现状，再执行修改。
                if self.state.turn_count > 0:
                    _execution_markers = {
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
                    if any(m in _first_prompt for m in _execution_markers):
                        self.state.task_progress = "implementing"
                        self.state.structured_findings["task_progress"] = "implementing"
                        logger.debug(
                            "goal_and_progress_updated_for_mutation: turn=%d progress=implementing",
                            self.state.turn_count,
                        )
                logger.debug(
                    "goal_updated_from_new_prompt: turn=%d goal=%s",
                    self.state.turn_count,
                    _first_prompt[:60],
                )

        while True:
            is_first_turn = self.state.turn_count == 0
            prompt = (
                _first_prompt
                if is_first_turn
                else self._build_continuation_prompt(envelope or self._build_empty_envelope())
            )

            # 【Phase 1.3 增强】：首次启动时检索历史知识注入上下文
            if is_first_turn and _first_prompt:
                bootstrap_knowledge = await self._retrieve_bootstrapping_knowledge(_first_prompt)
                if bootstrap_knowledge:
                    prompt = f"{bootstrap_knowledge}\n\nCurrent Task: {prompt}"

            await self._checkpoint_session()

            # 1. ShadowEngine 推测结果复用
            if self._shadow_engine is not None:
                has_spec = getattr(self._shadow_engine, "has_valid_speculation", lambda _sid: False)(self.session_id)
                if has_spec:
                    pre_warmed = await getattr(
                        self._shadow_engine, "consume_speculation", self._noop_consume_speculation
                    )(self.session_id)
                    async for event in self._yield_pre_warmed_events(pre_warmed):
                        yield event

            # 2. 执行干净单 Turn
            envelope = None

            # 【关键修复】：从 RoleProfile 构建 tool_definitions（解决 orchestrator 路径
            # tool_definitions=[] 的根因）。
            # RoleRuntimeService.create_transaction_controller 将 tool_definitions 传给
            # TransactionKernel 内部，但不传给 orchestrator。导致 LLM 没有实际的写工具
            # function definitions，无法调用 write_file/edit_file。
            # 修复：直接从 RoleProfile 构建 tool_definitions 并传给 kernel。
            role_def, profile_tool_defs = self._get_role_and_tools()
            if profile_tool_defs:
                tool_definitions = profile_tool_defs

            # 【关键修复】：先在循环外部计算 can_continue，确保 continuation prompt
            # 能拿到上一回合正确的 envelope（而非 None / empty_envelope）。
            #
            # 原 bug：async for 循环内部构建 continuation prompt 时 envelope=None，
            # 错误回退到 _build_empty_envelope()，导致第 2+ 回合的 prompt 丢失工作记忆。
            # 修复后：先执行 turn → 更新 envelope → 判断 can_continue → 构建下一 prompt。

            # 【关键修复】：始终 prepend role definition 作为 system message，
            # 解决 "LLM keeps calling read_file" 的根因：
            # orchestrator 路径下 RoleRuntimeService 不传 role definition 给 orchestrator，
            # 导致 LLM 丢失 role identity（Director）和 write tool 权限上下文。
            turn_context: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
            if role_def:
                turn_context.insert(0, {"role": "system", "content": role_def})

            async for event in self.kernel.execute_stream(
                turn_id=f"{self.session_id}_turn{self.state.turn_count}",
                context=turn_context,
                tool_definitions=tool_definitions,
            ):
                yield event
                if isinstance(event, CompletionEvent):
                    envelope = self._build_envelope_from_completion(event)

            # 【关键修复】读工具-only 豁免：如果 LLM 已生成可见输出且只调用了读工具，
            # 直接结束会话，防止 ANALYSIS_ONLY / 误判 MATERIALIZE_CHANGES 导致的死循环。
            # 这是"LLM 主动交卷"的轻量级替代方案：LLM 用可见输出声明任务完成。
            #
            # 【关键修复】但 turn_kind=continue_multi_turn 时不应触发此豁免：
            # continue_multi_turn 意味着"需要继续写工具"，不是"读完了可以结束"。
            # 例如 LLM 调用 glob 而非 write_file 时，系统返回 continue_multi_turn，
            # 下一回合应强制调用写工具，而不是提前终止会话。
            _turn_kind = getattr(envelope.turn_result, "kind", "") if envelope else ""
            if (
                envelope is not None
                and envelope.continuation_mode == TurnContinuationMode.AUTO_CONTINUE
                and _turn_kind != "continue_multi_turn"
            ):
                _receipt = getattr(envelope.turn_result, "batch_receipt", None) or {}
                _results = _receipt.get("results", [])
                _has_write_tool = any(
                    str(r.get("tool_name", ""))
                    in {
                        "write_file",
                        "edit_file",
                        "create_file",
                        "append_to_file",
                        "precision_edit",
                        "edit_blocks",
                        "search_replace",
                        "repo_apply_diff",
                    }
                    for r in _results
                )
                _has_visible_output = bool(
                    envelope.turn_result.visible_content and str(envelope.turn_result.visible_content).strip()
                )
                if not _has_write_tool and _has_visible_output and self.state.turn_count >= 1:
                    logger.debug(
                        "read-only-termination-exempt: turn=%d visible_chars=%d results=%d",
                        self.state.turn_count,
                        len(str(envelope.turn_result.visible_content)),
                        len(_results),
                    )
                    envelope.continuation_mode = TurnContinuationMode.END_SESSION
                    # 修正 turn_result.kind 以匹配 END_SESSION 语义（TurnResult 为 frozen model，需重建）
                    envelope.turn_result = envelope.turn_result.model_copy(update={"kind": "final_answer"})

            self.state.turn_count += 1
            is_first_turn = False

            if envelope is None:
                yield ErrorEvent(
                    turn_id=self.session_id,
                    error_type="OrchestratorError",
                    message="Kernel completed without yielding CompletionEvent",
                )
                break

            # 3. 增量持久化 Artifacts
            if envelope.artifacts_to_persist:
                await self._artifact_store.persist(envelope.artifacts_to_persist)
                self.state.artifacts.update(self._artifact_store.get_artifact_map())
            # 更新 artifact hash 指纹用于 stagnation 检测
            if envelope.artifacts_to_persist or self.state.artifacts:
                self._update_artifact_hashes()

            # 3a. 注入 session_patch 到 structured_findings（上下文降维 ADR-0071）
            if envelope.session_patch:
                apply_session_patch(self.state, envelope.session_patch)

            # 【关键修复】：当 write 工具成功执行后，自动将 task_progress 推进到 "verifying"。
            # 根因：session_patch 的 task_progress 被硬编码为 "implementing"，
            # 导致 LLM 在写完之后仍停留在 "implementing" 指令，
            # 继续调用更多读/写工具而不是验证已完成的修改。
            # 通过检测 batch_receipt 中的 write 工具成功结果，
            # 主动将 progress 推进到 verifying 阶段。
            batch_receipt = getattr(envelope.turn_result, "batch_receipt", None) or {}
            results: list[dict[str, Any]] = batch_receipt.get("results", [])

            # 调试日志：追踪 batch_receipt 和 tool_calls
            logger.debug(
                "turn=%d batch_receipt_keys=%s results_count=%d turn_kind=%s",
                self.state.turn_count,
                list(batch_receipt.keys()) if batch_receipt else [],
                len(results),
                envelope.turn_result.kind,
            )

            write_tools_succeeded = any(
                item.get("tool_name") in {"write_file", "edit_file", "create_file"}
                and str(item.get("status", "")) == "success"
                for item in results
            )
            # 备用检测：即使 batch_receipt.results 不包含写工具结果，
            # 也检查 artifacts 中是否有新创建的文件（说明写工具已成功执行）。
            # 这是必要的，因为 batch_receipt 可能只记录第一个工具的结果。
            if not write_tools_succeeded and self.state.task_progress == "implementing":
                artifacts_with_files = [
                    k
                    for k, v in self.state.artifacts.items()
                    if isinstance(v, dict) and v.get("operation") in {"create", "modify"}
                ]
                if artifacts_with_files:
                    write_tools_succeeded = True
            if write_tools_succeeded and self.state.task_progress == "implementing":
                self.state.task_progress = "verifying"
                self.state.structured_findings["task_progress"] = "verifying"
                logger.debug("Auto-advanced task_progress to 'verifying' after successful write")

            # 【关键修复】：即使 batch_receipt.results 为空（LLM 执行写工具后，
            # 工具结果嵌入在 visible_content 而非 batch_receipt 中），
            # 仍通过 turn_kind="tool_batch_with_receipt" 检测到工具执行，
            # 自动推进到 verifying。
            if (
                not write_tools_succeeded
                and envelope.turn_result.kind == "tool_batch_with_receipt"
                and self.state.task_progress == "implementing"
            ):
                self.state.task_progress = "verifying"
                self.state.structured_findings["task_progress"] = "verifying"
                logger.debug(
                    "Auto-advanced task_progress to 'verifying' (tool_batch_with_receipt, no batch_receipt.results)"
                )

            # 【关键修复】：消费后清理 _user_progression_hint，防止推进短句在后续回合持续污染 Instruction。
            # 根因：_user_progression_hint 一旦写入 structured_findings 就会持久化到 checkpoint，
            # 如果不清理，LLM 在 Turn 3、Turn 4 仍看到 Turn 2 的 "开始落地啊"，导致指令混乱。
            if "_user_progression_hint" in self.state.structured_findings:
                del self.state.structured_findings["_user_progression_hint"]
                logger.debug("Cleared _user_progression_hint after consumption")

            # 首次回合：从原始 prompt 中提取 goal 填充到 state.goal
            # 解决 "多回合工作流继续但 goal 为空" 的根因。
            # checkpoint resume 时 goal 已从文件恢复（_try_load_checkpoint），无需覆盖。
            if self.state.turn_count == 1 and not self.state.goal:
                # 优先使用用户原始请求作为 goal（_first_prompt 在函数开头保存）
                self.state.goal = _first_prompt
                # 如果 session_patch 有更具体的指令，追加到 goal
                if envelope.session_patch and (instr := envelope.session_patch.get("instruction")):
                    self.state.goal = f"{_first_prompt}\n\n[补充指令]: {instr}"

            # 记录 turn 历史用于 Policy 检测
            self.state.turn_history.append(
                {
                    "turn_index": self.state.turn_count,
                    "continuation_mode": envelope.continuation_mode.value,
                    "error": envelope.turn_result.visible_content
                    if envelope.continuation_mode == TurnContinuationMode.END_SESSION
                    else None,
                }
            )

            # 4. 路由分支
            if envelope.continuation_mode == TurnContinuationMode.HANDOFF_DEVELOPMENT:
                yield TurnPhaseEvent.create(
                    turn_id=self.session_id,
                    phase="workflow_handoff",
                    metadata={"handoff_target": "development", "intent": envelope.next_intent},
                )
                # 如果内核已在流式路径中执行并透传了 DevelopmentWorkflowRuntime，
                # 则 Orchestrator 不再重复执行（通过 session_patch 中的标记识别）。
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
                await self._checkpoint_session()
                break

            if envelope.continuation_mode == TurnContinuationMode.HANDOFF_EXPLORATION:
                yield TurnPhaseEvent.create(
                    turn_id=self.session_id,
                    phase="workflow_handoff",
                    metadata={"handoff_target": "exploration", "intent": envelope.next_intent},
                )
                await self._checkpoint_session()
                break

            if envelope.continuation_mode == TurnContinuationMode.WAITING_HUMAN:
                yield SessionWaitingHumanEvent(
                    session_id=self.session_id,
                    reason=envelope.next_intent or "human_input_required",
                )
                await self._checkpoint_session()
                break

            if envelope.continuation_mode == TurnContinuationMode.END_SESSION:
                await self._checkpoint_session()
                break

            # 5. ContinuationPolicy 仲裁
            can_continue, reason = self.policy.can_continue(self.state, envelope)
            if not can_continue:
                await self._checkpoint_session()
                yield SessionCompletedEvent(
                    session_id=self.session_id,
                    reason=reason,
                )
                break

            # 6. 触发下一 Turn 的跨 Turn 推测
            if self._shadow_engine is not None and can_continue:
                start_cross_turn = getattr(self._shadow_engine, "start_cross_turn_speculation", None)
                if callable(start_cross_turn):
                    start_cross_turn(
                        session_id=self.session_id,
                        predicted_next_tools=self._predict_next_tools(envelope),
                        hints=envelope.speculative_hints,
                    )

            # 【关键修复】：在循环末尾更新 prompt，确保下一迭代使用正确的 continuation。
            # 使用当前的 envelope（已包含 SESSION_PATCH 和 batch_receipt）。
            prompt = self._build_continuation_prompt(envelope)

        await self._checkpoint_session()
        yield SessionCompletedEvent(session_id=self.session_id)

    async def _checkpoint_session(self) -> None:
        """持久化当前会话状态到本地 checkpoint 文件。

        包含完整的降维工作记忆，确保 resume 时能真正恢复"上一回合学到了什么"。
        加上 schema_version 以便未来字段迭代时兼容。
        """
        checkpoint_dir = Path(self.workspace) / ".polaris" / "checkpoints"
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
        checkpoint_path = checkpoint_dir / f"{self.session_id}.json"
        import json

        with open(checkpoint_path, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "schema_version": 2,
                    "session_id": self.state.session_id,
                    "turn_count": self.state.turn_count,
                    "goal": self.state.goal,
                    "task_progress": self.state.task_progress,
                    # 降维后的工作记忆（LLM 的合成结论，而非原始 artifacts）
                    "structured_findings": self.state.structured_findings,
                    "key_file_snapshots": self.state.key_file_snapshots,
                    "last_failure": self.state.last_failure,
                    # artifacts 存引用或压缩版（完整版由 SessionArtifactStore 管理）
                    "artifacts": self.state.artifacts,
                    "recent_artifact_hashes": self.state.recent_artifact_hashes,
                },
                handle,
                ensure_ascii=False,
                default=str,
            )

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
        turn = self.state.turn_count
        max_turns = self.state.max_turns

        # --- Zone 1: Goal ---
        # FIX-20250421: 强制置顶原始目标，不可变更
        goal_block = f"【核心任务 - 不可变更】\n{goal}\n\n当前执行目标: {self.state.goal or goal}"

        # --- Zone 2: Progress ---
        # FIX-20250421: 从最后一个 turn 的 PhaseManager 获取真实阶段
        _phase_str = progress
        if self.state.turn_history:
            last_turn = self.state.turn_history[-1]
            batch_receipt = last_turn.get("batch_receipt", {})
            from polaris.cells.roles.kernel.internal.transaction.phase_manager import (
                PhaseManager,
                extract_tool_results_from_batch_receipt,
            )

            tool_results = extract_tool_results_from_batch_receipt(batch_receipt)
            if tool_results:
                pm = PhaseManager()
                # 重放历史到 PhaseManager
                for turn_item in self.state.turn_history:
                    br = turn_item.get("batch_receipt", {})
                    trs = extract_tool_results_from_batch_receipt(br)
                    if trs:
                        pm.transition(trs)
                _phase_str = pm.current_phase.value

        progress_block = f"当前阶段: {_phase_str} | 回合: {turn} / {max_turns}"

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
        # FIX-20250421: 显示真正读取过的文件（从 state.read_files）
        if self.state.read_files:
            wm_parts.append("已成功读取的文件:")
            wm_parts.append(f"  - {', '.join(self.state.read_files)}")
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

        # 【关键修复】注入上回合 LLM 自己的 visible_content 到 WorkingMemory。
        # 根因：跨回合时 LLM 面对空 WorkingMemory，丢失自己上一回合的分析结论、
        # 实施步骤、错误码字典等关键上下文，导致每次 turn 都从零开始。
        # Fix：把 Turn N 的 visible_content 直接塞进 Turn N+1 的 WorkingMemory。
        _prev_visible = getattr(envelope.turn_result, "visible_content", None) or ""
        if _prev_visible and str(_prev_visible).strip():
            _prev_stripped = str(_prev_visible).strip()
            # 截断避免 token 爆炸，但保留足够上下文（3000 chars 约 750 tokens）
            if len(_prev_stripped) > 3000:
                _prev_stripped = _prev_stripped[:3000] + f"\n... [truncated, total {len(_prev_stripped)} chars]"
            wm_parts.append("上回合分析结果（你自己的输出）:")
            wm_parts.append(f"  {_prev_stripped}")

        # === 注入上回合工具执行结果（fixes "missing code context in turn 2" bug）===
        # 从 batch_receipt 提取工具结果，追加到 WorkingMemory
        batch_receipt = getattr(envelope.turn_result, "batch_receipt", None) or {}
        results: list[dict[str, Any]] = batch_receipt.get("results", [])
        if results:
            tool_result_lines: list[str] = []
            for item in results:
                tool_name = str(item.get("tool_name", ""))
                status = str(item.get("status", ""))
                success = status == "success"
                result_data = item.get("result")
                args = item.get("arguments", {})

                if tool_name in {"read_file", "repo_read_head"} and success:
                    # read_file 的参数可能是 file/filepath/path/target_file 等
                    path = (
                        args.get("file")
                        or args.get("filepath")
                        or args.get("path")
                        or args.get("target")
                        or args.get("target_file")
                        or args.get("file_path")
                        or "unknown"
                    )
                    # FIX-20250421: 记录真正读取过的文件
                    if path and path not in self.state.read_files:
                        self.state.read_files.append(path)
                    content = ""
                    if isinstance(result_data, dict):
                        content = str(result_data.get("result", result_data.get("content", "")))
                    elif isinstance(result_data, str):
                        content = result_data
                    # 截断过长内容避免 token 爆炸
                    if len(content) > 2000:
                        content = content[:2000] + f"\n... [truncated, total {len(content)} chars]"
                    tool_result_lines.append(f"  文件 `{path}` ({len(content)} chars):\n{content}")
                elif tool_name == "repo_rg" and success:
                    # repo_rg 不算真正读取文件，只记录搜索模式
                    pattern = args.get("pattern", "")
                    tool_result_lines.append(f"  搜索: {pattern}")
                elif tool_name in {"list_directory", "glob"} and success:
                    path = args.get("path", ".")
                    entries = []
                    if isinstance(result_data, dict):
                        entries = result_data.get("result", [])
                    elif isinstance(result_data, list):
                        entries = result_data
                    if entries:
                        names = [e.get("name", str(e)) if isinstance(e, dict) else str(e) for e in entries[:20]]
                        tool_result_lines.append(f"  目录 `{path}` 包含: {', '.join(names)}")
                elif not success:
                    tool_result_lines.append(f"  {tool_name} 执行失败: {item.get('error', 'unknown error')}")

            if tool_result_lines:
                wm_parts.append("上回合工具执行结果:")
                wm_parts.extend(tool_result_lines)

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
        _hint_line = f"【用户最新指令】{_progression_hint}\n" if _progression_hint else ""

        instruction_map = {
            "exploring": (
                f"{_hint_line}当前任务：{_goal_snippet}。\n"
                f"请继续探索和分析。优先确认问题根因，收集必要信息后再决定修复方案。"
            ),
            "investigating": (
                f"{_hint_line}当前任务：{_goal_snippet}。\n继续深入调查。已识别疑似文件，关注错误栈和调用链。"
            ),
            "implementing": (
                f"{_hint_line}当前任务：{_goal_snippet}。\n"
                f"现在进入修复阶段。请按最小改动原则执行修改，使用 write_file/edit_file 等工具落实代码变更。"
                f"严禁继续调用 repo_tree/read_file/glob/repo_rg 等探索工具——直接执行写入。"
            ),
            "verifying": (
                f"{_hint_line}当前任务：{_goal_snippet}。\n验证阶段。请运行测试或手动验证修复效果，确保无回归。"
            ),
            "done": (f"{_hint_line}当前任务：{_goal_snippet}。\n任务已完成。请汇总结果并以 END_SESSION 结束。"),
        }
        instruction = instruction_map.get(progress, f"{_hint_line}继续执行任务：{_goal_snippet}。")

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
        import json

        with open(checkpoint_path, encoding="utf-8") as handle:
            data: dict[str, Any] = json.load(handle)

        schema_version = data.get("schema_version")
        if schema_version != 2:
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
