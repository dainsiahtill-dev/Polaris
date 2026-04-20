"""
Exploration Workflow Runtime - 复杂探索工作流运行时

核心职责：
1. 接收 TransactionKernel 移交的复杂探索任务
2. 执行多步工具调用协调
3. 状态管理（探索进度、已探索路径、ledger）
4. 结果聚合与返回
5. 支持 checkpoint / resume

触发条件：
- 异步工具（create_pull_request等）
- 大量只读工具（>=5个read_file）
- 明确标记[handoff_workflow]
"""

import asyncio
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from polaris.cells.roles.kernel.public.turn_contracts import (
    ToolCallId,
    ToolEffectType,
    ToolExecutionMode,
    ToolInvocation,
    TurnDecision,
    TurnId,
)
from polaris.cells.roles.kernel.public.turn_events import (
    CompletionEvent,
    ContentChunkEvent,
    ToolBatchEvent,
)
from polaris.domain.cognitive_runtime.models import ContextHandoffPack


class ExplorationStatus(Enum):
    """探索状态"""

    PLANNING = "planning"  # 规划中
    EXPLORING = "exploring"  # 探索中
    COMPLETED = "completed"  # 完成
    FAILED = "failed"  # 失败
    CANCELLED = "cancelled"  # 取消


@dataclass
class ExplorationStep:
    """探索步骤"""

    step_id: int
    action: str  # "read_file", "analyze", "synthesize"
    target: str | None = None
    arguments: dict = field(default_factory=dict)
    status: ExplorationStatus = ExplorationStatus.PLANNING
    result: Any = None
    error: str | None = None


@dataclass
class ExplorationPlan:
    """探索计划"""

    initial_tools: list[ToolInvocation]
    strategy: str  # "breadth_first", "depth_first", "adaptive"
    max_steps: int = 10
    budget_ms: int = 60000  # 最大探索时间


@dataclass
class ExplorationResult:
    """探索结果"""

    turn_id: TurnId
    status: ExplorationStatus
    steps_completed: int
    tools_executed: list[dict]
    discoveries: list[str]  # 关键发现摘要
    synthesis: str | None  # 最终综合
    duration_ms: int = 0
    error: str | None = None


class ExplorationWorkflowRuntime:
    """
    复杂探索工作流运行时

    接收 TransactionKernel 移交的复杂任务，协调多步探索。
    支持 ContextHandoffPack 作为入口，以及 checkpoint/resume。

    使用示例：
        runtime = ExplorationWorkflowRuntime(
            tool_executor=my_executor,
            synthesis_llm=my_llm
        )

        result = await runtime.execute(
            decision=handoff_decision,
            turn_id="turn_1"
        )
    """

    def __init__(
        self,
        tool_executor: Callable,  # async def executor(tool_name, arguments) -> dict
        synthesis_llm: Callable | None = None,  # async def synthesize(context) -> str
        max_steps: int = 10,
        timeout_ms: int = 60000,
    ) -> None:
        self.tool_executor = tool_executor
        self.synthesis_llm = synthesis_llm
        self.max_steps = max_steps
        self.timeout_ms = timeout_ms

        # 探索历史
        self._explored_paths: set[str] = set()
        self._discovery_cache: dict[str, Any] = {}

        # Ledger: 记录每一步执行结果用于 checkpoint / resume
        self._ledger: list[dict[str, Any]] = []

        # Handoff context (populated when entering from ContextHandoffPack)
        self._handoff_context: dict[str, Any] = {}

    async def execute(self, decision: TurnDecision, turn_id: TurnId) -> ExplorationResult:
        """
        执行探索工作流

        流程：
        1. 分析初始工具调用意图
        2. 制定探索计划
        3. 按计划执行工具
        4. 收集探索结果
        5. 综合分析（可选）
        """
        start_ms = int(time.time() * 1000)
        decision.get("metadata", {})

        # 构建探索计划
        plan = self._create_plan(decision)

        # 执行探索
        tools_executed: list[dict] = []

        try:
            # 执行初始工具批次
            if plan.initial_tools:
                initial_results = await self._execute_tools(plan.initial_tools)
                tools_executed.extend(initial_results)

                # 分析初始结果，调整探索计划
                discoveries = self._analyze_results(initial_results)
                self._discovery_cache.update(discoveries)

            # 继续探索（如果需要）
            remaining_steps = plan.max_steps - len(plan.initial_tools)
            if remaining_steps > 0 and plan.strategy != "single_batch":
                additional_results = await self._continue_exploration(plan, remaining_steps)
                tools_executed.extend(additional_results)

            # 综合分析（_synthesize 内部含兜底逻辑，即使 synthesis_llm 为 None 也会生成摘要）
            synthesis = await self._synthesize(tools_executed)

            duration_ms = int(time.time() * 1000) - start_ms

            result = ExplorationResult(
                turn_id=turn_id,
                status=ExplorationStatus.COMPLETED,
                steps_completed=len(tools_executed),
                tools_executed=tools_executed,
                discoveries=list(self._discovery_cache.keys()),
                synthesis=synthesis,
                duration_ms=duration_ms,
            )
            self._ledger.append({"phase": "execute", "turn_id": str(turn_id), "status": "completed"})
            return result

        except asyncio.TimeoutError:
            duration_ms = int(time.time() * 1000) - start_ms
            result = ExplorationResult(
                turn_id=turn_id,
                status=ExplorationStatus.FAILED,
                steps_completed=len(tools_executed),
                tools_executed=tools_executed,
                discoveries=list(self._discovery_cache.keys()),
                synthesis=None,
                duration_ms=duration_ms,
                error="Exploration timed out",
            )
            self._ledger.append({"phase": "execute", "turn_id": str(turn_id), "status": "timeout"})
            return result

        except (RuntimeError, ValueError) as e:
            duration_ms = int(time.time() * 1000) - start_ms
            result = ExplorationResult(
                turn_id=turn_id,
                status=ExplorationStatus.FAILED,
                steps_completed=len(tools_executed),
                tools_executed=tools_executed,
                discoveries=list(self._discovery_cache.keys()),
                synthesis=None,
                duration_ms=duration_ms,
                error=str(e),
            )
            self._ledger.append({"phase": "execute", "turn_id": str(turn_id), "status": "error", "error": str(e)})
            return result

    async def execute_stream(
        self,
        decision: TurnDecision,
        turn_id: TurnId,
    ) -> AsyncIterator[ToolBatchEvent | ContentChunkEvent | CompletionEvent]:
        """
        流式执行探索工作流，产出标准 TurnEvent 事件。

        事件序列：
        - ToolBatchEvent (started / success / error / timeout) × N
        - ContentChunkEvent (synthesis，可选)
        - CompletionEvent (success / failed)
        """
        start_ms = int(time.time() * 1000)
        plan = self._create_plan(decision)
        tools_executed: list[dict] = []

        try:
            # 执行初始工具批次
            if plan.initial_tools:
                async for event in self._execute_tools_stream(plan.initial_tools, str(turn_id)):
                    yield event
                    if (
                        isinstance(event, ToolBatchEvent)
                        and event.status in ("success", "error", "timeout")
                        and event.arguments is not None
                    ):
                        tools_executed.append(
                            {
                                "call_id": event.call_id,
                                "tool_name": event.tool_name,
                                "status": event.status,
                                "result": event.result,
                                "arguments": event.arguments,
                            }
                        )

                discoveries = self._analyze_results(tools_executed)
                self._discovery_cache.update(discoveries)
                self._ledger.append(
                    {
                        "phase": "initial_tools",
                        "turn_id": str(turn_id),
                        "tool_count": len(tools_executed),
                    }
                )

            # 继续探索
            remaining_steps = plan.max_steps - len(plan.initial_tools)
            if remaining_steps > 0 and plan.strategy != "single_batch":
                async for event in self._continue_exploration_stream(plan, remaining_steps, str(turn_id)):
                    yield event
                    if (
                        isinstance(event, ToolBatchEvent)
                        and event.status in ("success", "error", "timeout")
                        and event.arguments is not None
                    ):
                        tools_executed.append(
                            {
                                "call_id": event.call_id,
                                "tool_name": event.tool_name,
                                "status": event.status,
                                "result": event.result,
                                "arguments": event.arguments,
                            }
                        )
                self._ledger.append(
                    {
                        "phase": "continue_exploration",
                        "turn_id": str(turn_id),
                        "tool_count": len(tools_executed),
                    }
                )

            # 综合分析（_synthesize 内部含兜底逻辑，即使 synthesis_llm 为 None 也会生成摘要）
            synthesis = await self._synthesize(tools_executed)
            if synthesis:
                yield ContentChunkEvent(
                    turn_id=str(turn_id),
                    chunk=synthesis,
                )

            duration_ms = int(time.time() * 1000) - start_ms
            yield CompletionEvent(
                turn_id=str(turn_id),
                status="success",
                duration_ms=duration_ms,
                llm_calls=0,
                tool_calls=len(tools_executed),
            )
            self._ledger.append({"phase": "execute_stream", "turn_id": str(turn_id), "status": "completed"})

        except asyncio.TimeoutError:
            duration_ms = int(time.time() * 1000) - start_ms
            yield CompletionEvent(
                turn_id=str(turn_id),
                status="failed",
                duration_ms=duration_ms,
                llm_calls=0,
                tool_calls=len(tools_executed),
            )
            self._ledger.append({"phase": "execute_stream", "turn_id": str(turn_id), "status": "timeout"})

        except Exception as exc:  # noqa: BLE001
            duration_ms = int(time.time() * 1000) - start_ms
            yield CompletionEvent(
                turn_id=str(turn_id),
                status="failed",
                duration_ms=duration_ms,
                llm_calls=0,
                tool_calls=len(tools_executed),
            )
            self._ledger.append(
                {"phase": "execute_stream", "turn_id": str(turn_id), "status": "error", "error": str(exc)}
            )

    def enter_from_handoff(self, handoff_pack: ContextHandoffPack) -> TurnDecision:
        """从 ContextHandoffPack 进入，提取 handoff 信息并构造 TurnDecision。"""
        self._handoff_context = {
            "handoff_reason": handoff_pack.reason,
            "current_goal": handoff_pack.current_goal,
            "run_card": dict(handoff_pack.run_card),
            "receipt_refs": list(handoff_pack.receipt_refs),
        }
        self._ledger.append({"phase": "handoff_entry", "handoff_id": handoff_pack.handoff_id})

        from polaris.cells.roles.kernel.public.turn_contracts import BatchId, FinalizeMode, ToolBatch, TurnDecisionKind

        return TurnDecision(
            turn_id=TurnId(handoff_pack.handoff_id),
            kind=TurnDecisionKind.HANDOFF_WORKFLOW,
            visible_message=handoff_pack.current_goal or "Exploring from handoff...",
            reasoning_summary="Entered from ContextHandoffPack",
            tool_batch=ToolBatch(batch_id=BatchId(f"handoff_{handoff_pack.handoff_id}")),
            finalize_mode=FinalizeMode.NONE,
            domain="document",
            metadata={
                "handoff_reason": handoff_pack.reason,
                "current_goal": handoff_pack.current_goal,
                "run_card": dict(handoff_pack.run_card),
                "receipt_refs": list(handoff_pack.receipt_refs),
            },
        )

    def _checkpoint_step(self, step_id: str) -> dict[str, Any]:
        """在执行步骤后落盘局部 checkpoint（WAL 模式）。"""
        self._ledger.append({"phase": "checkpoint", "step_id": step_id, "timestamp_ms": int(time.time() * 1000)})
        return self.checkpoint()

    def checkpoint(self) -> dict[str, Any]:
        """创建当前运行时的 checkpoint。"""
        return {
            "ledger": list(self._ledger),
            "explored_paths": list(self._explored_paths),
            "discovery_cache_keys": list(self._discovery_cache.keys()),
            "handoff_context": dict(self._handoff_context),
        }

    def resume(self, checkpoint: dict[str, Any]) -> None:
        """从 checkpoint 恢复运行时状态。"""
        self._ledger = list(checkpoint.get("ledger", []))
        self._explored_paths = set(checkpoint.get("explored_paths", []))
        self._discovery_cache = dict.fromkeys(checkpoint.get("discovery_cache_keys", []))
        self._handoff_context = dict(checkpoint.get("handoff_context", {}))
        self._ledger.append({"phase": "resume", "checkpoint_size": len(self._ledger)})

    def _create_plan(self, decision: TurnDecision) -> ExplorationPlan:
        """创建探索计划"""
        metadata = decision.get("metadata", {})
        handoff_reason = metadata.get("handoff_reason", "unknown")

        tool_batch = decision.get("tool_batch")
        initial_tools = tool_batch.get("invocations", []) if tool_batch else []

        # 根据handoff原因确定策略
        if handoff_reason == "async_operation":
            strategy = "single_batch"
        elif handoff_reason == "too_many_tools":
            strategy = "adaptive"
        else:
            strategy = "breadth_first"

        return ExplorationPlan(initial_tools=initial_tools, strategy=strategy, max_steps=self.max_steps)

    async def _execute_tools(self, tools: list[ToolInvocation]) -> list[dict]:
        """执行工具列表"""
        results = []

        for tool in tools:
            tool_name = tool.get("tool_name", "unknown")
            arguments = tool.get("arguments", {})
            call_id = str(tool.get("call_id", ""))

            try:
                result = await asyncio.wait_for(
                    self.tool_executor(tool_name, arguments),
                    timeout=30,  # 单个工具30秒超时
                )

                results.append(
                    {
                        "call_id": call_id,
                        "tool_name": tool_name,
                        "status": "success",
                        "result": result,
                        "arguments": arguments,
                    }
                )

                # 缓存发现
                if tool_name == "read_file":
                    path = arguments.get("path", "")
                    content = result.get("result", "") if isinstance(result, dict) else str(result)
                    self._discovery_cache[path] = content

            except asyncio.TimeoutError:
                results.append(
                    {
                        "call_id": call_id,
                        "tool_name": tool_name,
                        "status": "timeout",
                        "error": "Tool execution timed out",
                        "arguments": arguments,
                    }
                )

            except Exception as e:  # noqa: BLE001
                results.append(
                    {
                        "call_id": call_id,
                        "tool_name": tool_name,
                        "status": "error",
                        "error": str(e),
                        "arguments": arguments,
                    }
                )

        return results

    async def _execute_tools_stream(
        self,
        tools: list[ToolInvocation],
        turn_id: str,
    ) -> AsyncIterator[ToolBatchEvent]:
        """流式执行工具列表，产出 ToolBatchEvent。"""
        for tool in tools:
            tool_name = tool.get("tool_name", "unknown")
            arguments = dict(tool.get("arguments", {}))
            call_id = str(tool.get("call_id", ""))
            start_ms = int(time.time() * 1000)

            yield ToolBatchEvent(
                turn_id=turn_id,
                batch_id=f"{turn_id}_exploration",
                tool_name=tool_name,
                call_id=call_id,
                status="started",
                progress=0.0,
                arguments=arguments,
            )

            try:
                result = await asyncio.wait_for(
                    self.tool_executor(tool_name, arguments),
                    timeout=30,
                )
                duration_ms = int(time.time() * 1000) - start_ms

                if tool_name == "read_file":
                    path = arguments.get("path", "")
                    content = result.get("result", "") if isinstance(result, dict) else str(result)
                    self._discovery_cache[path] = content

                # Checkpoint before yielding (WAL pattern)
                self._checkpoint_step(f"{call_id}_success")

                yield ToolBatchEvent(
                    turn_id=turn_id,
                    batch_id=f"{turn_id}_exploration",
                    tool_name=tool_name,
                    call_id=call_id,
                    status="success",
                    progress=1.0,
                    arguments=arguments,
                    result=result,
                    execution_time_ms=duration_ms,
                )

            except asyncio.TimeoutError:
                duration_ms = int(time.time() * 1000) - start_ms
                self._checkpoint_step(f"{call_id}_timeout")
                yield ToolBatchEvent(
                    turn_id=turn_id,
                    batch_id=f"{turn_id}_exploration",
                    tool_name=tool_name,
                    call_id=call_id,
                    status="timeout",
                    progress=1.0,
                    arguments=arguments,
                    error="Tool execution timed out",
                    execution_time_ms=duration_ms,
                )

            except Exception as e:  # noqa: BLE001
                duration_ms = int(time.time() * 1000) - start_ms
                self._checkpoint_step(f"{call_id}_error")
                yield ToolBatchEvent(
                    turn_id=turn_id,
                    batch_id=f"{turn_id}_exploration",
                    tool_name=tool_name,
                    call_id=call_id,
                    status="error",
                    progress=1.0,
                    arguments=arguments,
                    error=str(e),
                    execution_time_ms=duration_ms,
                )

    async def _continue_exploration(self, plan: ExplorationPlan, max_additional: int) -> list[dict]:
        """继续探索（自适应策略）"""
        additional_results: list[dict[str, Any]] = []

        # 根据策略决定是否继续
        if plan.strategy == "single_batch":
            return additional_results  # 单一批次，不继续

        # 简单策略：根据已有发现推断下一步
        for i in range(min(max_additional, 3)):  # 最多再执行3步
            if not self._discovery_cache:
                break

            # 启发式：查找imports
            imports_to_resolve = self._extract_imports()
            if imports_to_resolve:
                for imp in imports_to_resolve[:2]:  # 最多跟踪2个import
                    result = await self._execute_tools(
                        [
                            ToolInvocation(
                                call_id=ToolCallId(f"auto_{i}_{imp}"),
                                tool_name="read_file",
                                arguments={"path": imp},
                                effect_type=ToolEffectType.READ,
                                execution_mode=plan.initial_tools[0].get("execution_mode")
                                if plan.initial_tools
                                else ToolExecutionMode.READONLY_PARALLEL,
                            )
                        ]
                    )
                    additional_results.extend(result)

        return additional_results

    async def _continue_exploration_stream(
        self,
        plan: ExplorationPlan,
        max_additional: int,
        turn_id: str,
    ) -> AsyncIterator[ToolBatchEvent]:
        """流式继续探索。"""
        if plan.strategy == "single_batch":
            return

        for i in range(min(max_additional, 3)):
            if not self._discovery_cache:
                break

            imports_to_resolve = self._extract_imports()
            if imports_to_resolve:
                for imp in imports_to_resolve[:2]:
                    async for event in self._execute_tools_stream(
                        [
                            ToolInvocation(
                                call_id=ToolCallId(f"auto_{i}_{imp}"),
                                tool_name="read_file",
                                arguments={"path": imp},
                                effect_type=ToolEffectType.READ,
                                execution_mode=plan.initial_tools[0].get("execution_mode")
                                if plan.initial_tools
                                else ToolExecutionMode.READONLY_PARALLEL,
                            )
                        ],
                        turn_id,
                    ):
                        yield event

    def _analyze_results(self, results: list[dict]) -> dict[str, Any]:
        """分析工具执行结果，提取关键发现"""
        discoveries = {}

        for result in results:
            if result.get("status") != "success":
                continue

            tool_name = result.get("tool_name", "")
            data = result.get("result", {})

            if tool_name == "read_file" and isinstance(data, dict):
                path = result.get("arguments", {}).get("path", "")
                content = data.get("result", "")
                if path and content:
                    discoveries[path] = content

            elif tool_name == "list_directory":
                entries = data.get("result", []) if isinstance(data, dict) else []
                dir_path = result.get("arguments", {}).get("path", "")
                discoveries[f"dir:{dir_path}"] = entries

        return discoveries

    def _extract_imports(self) -> list[str]:
        """从已有发现中提取需要跟踪的imports"""
        imports = []

        for _path, content in self._discovery_cache.items():
            if not isinstance(content, str):
                continue

            # 简单import提取（实际应使用AST）
            for line in content.split("\n"):
                line = line.strip()
                if line.startswith("import ") or line.startswith("from "):
                    parts = line.split()
                    if len(parts) >= 2:
                        module = parts[1].split(".")[0]
                        if module not in imports:
                            imports.append(module)

        return imports[:5]  # 限制数量

    def _generate_fallback_synthesis(self, tools_executed: list[dict]) -> str | None:
        """当 synthesis_llm 不可用时，生成基于 discoveries 的兜底摘要。"""
        if not tools_executed:
            return None

        lines: list[str] = []
        lines.append(f"完成 {len(tools_executed)} 个工具调用：")
        lines.append("")

        for item in tools_executed:
            tool_name = item.get("tool_name", "unknown")
            status = item.get("status", "unknown")
            arguments = item.get("arguments", {})

            if tool_name == "read_file":
                path = arguments.get("path", "unknown")
                symbol = "✅" if status == "success" else "❌"
                lines.append(f"- {symbol} 读取 `{path}` ({status})")
            elif tool_name == "list_directory":
                path = arguments.get("path", "unknown")
                symbol = "✅" if status == "success" else "❌"
                lines.append(f"- {symbol} 列出目录 `{path}` ({status})")
            else:
                symbol = "✅" if status == "success" else "❌"
                lines.append(f"- {symbol} {tool_name} ({status})")

        if self._discovery_cache:
            lines.append("")
            lines.append("发现文件/路径：")
            for path in sorted(self._discovery_cache.keys()):
                lines.append(f"- `{path}`")

        return "\n".join(lines)

    async def _synthesize(self, tools_executed: list[dict]) -> str | None:
        """综合分析探索结果"""
        if not self.synthesis_llm:
            # 兜底：即使无 synthesis_llm，也返回基于 discoveries 的基本摘要
            return self._generate_fallback_synthesis(tools_executed)

        # 构建上下文
        context = {
            "tools_executed": len(tools_executed),
            "discoveries": list(self._discovery_cache.keys()),
            "results": tools_executed,
        }

        result = await self.synthesis_llm(context)
        return result  # type: ignore[no-any-return]

    def cancel(self) -> None:
        """取消探索"""
        # 可以实现取消令牌
        pass


# Backward compatibility alias
ExplorationWorkflow = ExplorationWorkflowRuntime
