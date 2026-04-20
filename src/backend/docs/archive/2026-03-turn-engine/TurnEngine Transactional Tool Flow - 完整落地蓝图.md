TurnEngine Transactional Tool Flow - 完整落地蓝图

  第一部分：当前架构诊断

  1.1 关键问题确认
  ┌─────────────────────────┬─────────────────────────────────────────────────────────┬──────────┐
  │          问题           │                        当前位置                         │ 严重程度 │
  ├─────────────────────────┼─────────────────────────────────────────────────────────┼──────────┤
  │ 双通道执行来源          │ output_parser.py:331-372 (native) + :80-147 (textual)   │ 架构级   │
  ├─────────────────────────┼─────────────────────────────────────────────────────────┼──────────┤
  │ Turn内continuation loop │ turn_engine.py:741-834 (while循环)                      │ 架构级   │
  ├─────────────────────────┼─────────────────────────────────────────────────────────┼──────────┤
  │ thinking可被解析执行    │ output_parser.py 无显式防护                             │ 中       │
  ├─────────────────────────┼─────────────────────────────────────────────────────────┼──────────┤
  │ Transcript驱动循环      │ tool_loop_controller.py:143-163 (append→rebuild→recall) │ 架构级   │
  ├─────────────────────────┼─────────────────────────────────────────────────────────┼──────────┤
  │ stream/run分叉实现      │ run():651-885 vs run_stream():887-1349                  │ 高       │
  └─────────────────────────┴─────────────────────────────────────────────────────────┴──────────┘
  1.2 当前执行流（问题根源）

  ┌─────────────────────────────────────────────────────────────┐
  │  User Message                                               │
  └──────────────────────┬──────────────────────────────────────┘
                         ▼
  ┌─────────────────────────────────────────────────────────────┐
  │  TurnEngine.run() / run_stream()                            │
  │  ┌───────────────────────────────────────────────────────┐ │
  │  │  while True:  ◄────────────────────────────────────┐  │ │
  │  │    ├─ PolicyLayer.evaluate()                       │  │ │
  │  │    ├─ llm_caller.call()                            │  │ │
  │  │    ├─ Parse thinking + tool calls                  │  │ │
  │  │    ├─ Execute tools                                │  │ │
  │  │    ├─ ToolLoopController.append_tool_cycle()       │  │ │
  │  │    └─ Build context → loop back ───────────────────┘  │ │
  │  └───────────────────────────────────────────────────────┘ │
  └──────────────────────┬──────────────────────────────────────┘
                         ▼
                ┌─────────────────┐
                │  问题：循环内部  │
                │  没有显式提交点  │
                │  无法区分        │
                │  decision/tools/ │
                │  finalization    │
                └─────────────────┘

  ---
  第二部分：目标架构设计

  2.1 核心契约（冻结版本）

  文件：polaris/cells/roles/kernel/public/turn_contracts.py

  from typing import TypedDict, Literal, Any, NewType
  from dataclasses import dataclass
  from enum import Enum, auto

  # ============ 基础类型 ============
  ToolCallId = NewType("ToolCallId", str)
  TurnId = NewType("TurnId", str)
  BatchId = NewType("BatchId", str)


  # ============ Turn决策类型 ============
  class TurnDecisionKind(Enum):
      FINAL_ANSWER = "final_answer"      # 直接回答，无工具
      TOOL_BATCH = "tool_batch"          # 需要执行工具批
      ASK_USER = "ask_user"              # 需要澄清
      HANDOFF_WORKFLOW = "handoff_workflow"  # 移交工作流层


  class FinalizeMode(Enum):
      """工具执行后的收口策略"""
      NONE = "none"          # 直接返回工具结果，不再请求LLM
      LOCAL = "local"        # 本地模板渲染结果
      LLM_ONCE = "llm_once"  # 允许一次显式总结请求（禁止再调工具）


  class ToolExecutionMode(Enum):
      READONLY_PARALLEL = "readonly_parallel"   # 可并行
      WRITE_SERIAL = "write_serial"             # 必须串行
      ASYNC_RECEIPT = "async_receipt"           # 异步，返回pending


  # ============ 核心数据结构 ============

  class ToolInvocation(TypedDict):
      call_id: ToolCallId
      tool_name: str
      arguments: dict[str, Any]
      execution_mode: ToolExecutionMode


  class ToolBatch(TypedDict):
      batch_id: BatchId
      invocations: list[ToolInvocation]
      # 执行策略
      parallel_readonly: list[ToolInvocation]   # 并行执行
      serial_writes: list[ToolInvocation]       # 串行执行
      async_receipts: list[ToolInvocation]      # 异步返回receipt


  class TurnDecision(TypedDict):
      """单个turn的唯一决策来源"""
      turn_id: TurnId
      kind: TurnDecisionKind

      # 用户可见内容（仅显示，不执行）
      visible_message: str

      # 推理摘要（telemetry only，永不执行）
      reasoning_summary: str | None

      # 工具批（仅当kind=TOOL_BATCH时有效）
      tool_batch: ToolBatch | None

      # 收口策略
      finalize_mode: FinalizeMode

      # 领域上下文
      domain: Literal["document", "code"]  # 影响默认策略

      # 扩展元数据
      metadata: dict[str, Any]


  # ============ 执行结果 ============

  class ToolExecutionResult(TypedDict):
      call_id: ToolCallId
      tool_name: str
      status: Literal["success", "error", "pending", "timeout"]
      result: Any
      execution_time_ms: int
      effect_receipt: dict[str, Any] | None  # 写操作的effect审计


  class BatchReceipt(TypedDict):
      """工具批执行完成的收据"""
      batch_id: BatchId
      turn_id: TurnId

      # 执行统计
      results: list[ToolExecutionResult]
      success_count: int
      failure_count: int
      pending_async_count: int

      # 是否需要移交workflow
      has_pending_async: bool

      # 原始结果（用于finalization）
      raw_results: list[dict[str, Any]]


  class TurnFinalization(TypedDict):
      """LLM_ONCE模式的最终收口"""
      turn_id: TurnId
      mode: Literal["none", "local", "llm_once"]

      # 最终用户可见内容
      final_visible_message: str

      # 是否触发workflow（复杂任务需要继续探索）
      needs_followup_workflow: bool
      workflow_reason: str | None


  class TurnResult(TypedDict):
      """单个turn的完整结果"""
      turn_id: TurnId

      # 结果类型
      kind: Literal["final_answer", "tool_batch_with_receipt", "handoff_workflow", "ask_user"]

      # 用户可见内容
      visible_content: str

      # 完整决策记录（用于audit）
      decision: TurnDecision

      # 工具执行收据（若适用）
      batch_receipt: BatchReceipt | None

      # 收口记录（若适用）
      finalization: TurnFinalization | None

      # 移交工作流上下文（若适用）
      workflow_context: dict[str, Any] | None

      # 性能指标
      metrics: dict[str, Any]

  2.2 状态机（唯一真相源）

  文件：polaris/cells/roles/kernel/internal/turn_state_machine.py

  from enum import Enum, auto
  from dataclasses import dataclass, field
  from typing import Optional
  import time


  class TurnState(Enum):
      """Turn事务状态机"""
      IDLE = auto()
      CONTEXT_BUILT = auto()
      DECISION_REQUESTED = auto()         # LLM请求已发出
      DECISION_RECEIVED = auto()          # 原始响应已收到
      DECISION_DECODED = auto()           # TurnDecision已解码

      # 分支：直接完成
      FINAL_ANSWER_READY = auto()

      # 分支：需要工具
      TOOL_BATCH_EXECUTING = auto()
      TOOL_BATCH_EXECUTED = auto()

      # 分支：收口
      FINALIZATION_REQUESTED = auto()     # 仅LLM_ONCE模式
      FINALIZATION_RECEIVED = auto()

      # 分支：移交
      HANDOFF_WORKFLOW = auto()

      # 终止状态
      COMPLETED = auto()
      FAILED = auto()


  # ============ 状态转换规则（硬编码，禁止绕开） ============

  VALID_TRANSITIONS: dict[TurnState, set[TurnState]] = {
      TurnState.IDLE: {TurnState.CONTEXT_BUILT},
      TurnState.CONTEXT_BUILT: {TurnState.DECISION_REQUESTED},
      TurnState.DECISION_REQUESTED: {TurnState.DECISION_RECEIVED, TurnState.FAILED},
      TurnState.DECISION_RECEIVED: {TurnState.DECISION_DECODED, TurnState.FAILED},
      TurnState.DECISION_DECODED: {
          TurnState.FINAL_ANSWER_READY,
          TurnState.TOOL_BATCH_EXECUTING,
          TurnState.HANDOFF_WORKFLOW,
          TurnState.FAILED
      },
      TurnState.TOOL_BATCH_EXECUTING: {TurnState.TOOL_BATCH_EXECUTED, TurnState.FAILED},
      TurnState.TOOL_BATCH_EXECUTED: {
          TurnState.COMPLETED,           # finalize_mode=none
          TurnState.FINALIZATION_REQUESTED,  # finalize_mode=llm_once
          TurnState.COMPLETED,           # finalize_mode=local
          TurnState.HANDOFF_WORKFLOW,    # 有pending async
          TurnState.FAILED
      },
      TurnState.FINALIZATION_REQUESTED: {TurnState.FINALIZATION_RECEIVED, TurnState.FAILED},
      TurnState.FINALIZATION_RECEIVED: {TurnState.COMPLETED, TurnState.FAILED},
      TurnState.FINAL_ANSWER_READY: {TurnState.COMPLETED},
      TurnState.HANDOFF_WORKFLOW: {TurnState.COMPLETED},  # 移交完成，当前turn结束
      TurnState.COMPLETED: set(),  # 终止状态
      TurnState.FAILED: set(),     # 终止状态
  }

  # 关键约束：禁止循环
  FORBIDDEN_TRANSITIONS: set[tuple[TurnState, TurnState]] = {
      # 工具执行后禁止回到决策请求（这是旧continuation loop的根源）
      (TurnState.TOOL_BATCH_EXECUTED, TurnState.DECISION_REQUESTED),
      (TurnState.TOOL_BATCH_EXECUTED, TurnState.CONTEXT_BUILT),

      # finalization禁止触发新一轮工具
      (TurnState.FINALIZATION_REQUESTED, TurnState.TOOL_BATCH_EXECUTING),

      # 禁止从任何状态跳回IDLE（必须完成或失败）
      (TurnState.TOOL_BATCH_EXECUTING, TurnState.IDLE),
      (TurnState.FINALIZATION_REQUESTED, TurnState.IDLE),
  }


  class InvalidStateTransitionError(Exception):
      """状态转换违规"""
      pass


  @dataclass
  class TurnStateMachine:
      """
      单个turn的状态机实例
      每个turn有且只有一个状态机
      """
      turn_id: str
      _state: TurnState = field(default=TurnState.IDLE)
      _history: list[tuple[TurnState, float]] = field(default_factory=list)
      _metadata: dict[str, Any] = field(default_factory=dict)

      def __post_init__(self):
          self._history.append((self._state, time.time()))

      @property
      def state(self) -> TurnState:
          return self._state

      def transition_to(self, new_state: TurnState, context: dict[str, Any] | None = None) -> None:
          """
          执行状态转换，强制检查规则
          所有状态转换必须经过此函数
          """
          # 检查是否允许
          if new_state not in VALID_TRANSITIONS.get(self._state, set()):
              # 额外检查禁止列表
              if (self._state, new_state) in FORBIDDEN_TRANSITIONS:
                  raise InvalidStateTransitionError(
                      f"FORBIDDEN transition: {self._state.name} -> {new_state.name}. "
                      f"This indicates an architectural violation (e.g., continuation loop)."
                  )
              raise InvalidStateTransitionError(
                  f"Invalid transition: {self._state.name} -> {new_state.name}"
              )

          # 执行转换
          old_state = self._state
          self._state = new_state
          self._history.append((new_state, time.time()))

          if context:
              self._metadata[f"{old_state.name}_to_{new_state.name}"] = context

      def is_terminal(self) -> bool:
          return self._state in {TurnState.COMPLETED, TurnState.FAILED}

      def get_history(self) -> list[tuple[TurnState, float]]:
          return self._history.copy()

      def assert_in_state(self, expected: TurnState | set[TurnState]) -> None:
          """断言当前状态，用于开发时防护"""
          expected_set = {expected} if isinstance(expected, TurnState) else expected
          if self._state not in expected_set:
              raise AssertionError(
                  f"Expected state in {expected_set}, but current state is {self._state}"
              )

  ---
  第三部分：Phase详细实施

  Phase 1: 契约与状态机（第1-2周）

  1.1 新建文件结构

  polaris/cells/roles/kernel/
  ├── public/
  │   ├── __init__.py
  │   ├── turn_contracts.py          # 新建：核心契约
  │   └── turn_events.py             # 新建：观测事件
  ├── internal/
  │   ├── __init__.py
  │   ├── turn_state_machine.py      # 新建：状态机
  │   ├── turn_decision_decoder.py   # 新建：决策解码器
  │   ├── turn_transaction_controller.py  # 新建：事务控制器
  │   ├── turn_ledger.py             # 新建：Turn记录（替代transcript驱动）
  │   ├── turn_engine.py             # 重写：入口包装
  │   ├── tool_batch_runtime.py      # 新建：工具批运行时
  │   └── legacy/
  │       ├── output_parser.py       # 移动：原文件归档
  │       └── tool_loop_controller.py # 移动：原文件归档
  └── tests/
      ├── test_turn_contracts.py     # 新建：契约测试
      ├── test_state_machine.py      # 新建：状态机测试
      ├── test_decision_decoder.py   # 新建：解码器测试
      └── test_transaction_controller.py  # 新建：事务测试

  1.2 决策解码器实现

  文件：polaris/cells/roles/kernel/internal/turn_decision_decoder.py

  """
  TurnDecisionDecoder - 执行授权点收口

  核心职责：
  1. 从LLM响应中解码出唯一的TurnDecision
  2. 确保thinking永远不会产生可执行工具
  3. 统一native tool calls和textual wrappers为一个来源
  """

  import json
  from typing import Any
  from dataclasses import dataclass

  from polaris.cells.roles.kernel.public.turn_contracts import (
      TurnDecision, TurnDecisionKind, ToolBatch, ToolInvocation,
      FinalizeMode, ToolExecutionMode, ToolCallId, TurnId
  )


  @dataclass(frozen=True)
  class RawLLMResponse:
      """LLM原始响应结构"""
      content: str                    # 完整正文
      thinking: str | None           # reasoning内容
      native_tool_calls: list[dict]   # provider原生工具调用
      model: str
      usage: dict[str, int]


  class TurnDecisionDecodeError(Exception):
      pass


  class TurnDecisionDecoder:
      """
      单一职责：把任何格式的LLM响应，转换为唯一的TurnDecision

      关键约束：
      - thinking内容永不参与工具解析
      - native和textual不独立执行，而是合并后统一决策
      """

      # 领域默认策略
      DOMAIN_DEFAULTS: dict[str, FinalizeMode] = {
          "document": FinalizeMode.LLM_ONCE,  # 文档域需要总结
          "code": FinalizeMode.NONE,           # 代码域直接返回结果
      }

      def __init__(self, domain: str = "document"):
          self.domain = domain
          self._default_finalize = self.DOMAIN_DEFAULTS.get(domain, FinalizeMode.LLM_ONCE)

      def decode(self, response: RawLLMResponse, turn_id: TurnId) -> TurnDecision:
          """
          解码LLM响应为TurnDecision

          决策优先级：
          1. 如果有final_answer标记 → FINAL_ANSWER
          2. 如果有工具调用 → TOOL_BATCH
          3. 如果需要澄清 → ASK_USER
          4. 如果任务复杂 → HANDOFF_WORKFLOW
          """

          # Step 1: 提取所有工具调用（合并native和textual）
          all_tools = self._extract_tool_calls(response)

          # Step 2: 判断是否直接回答
          if self._is_final_answer(response, all_tools):
              return TurnDecision(
                  turn_id=turn_id,
                  kind=TurnDecisionKind.FINAL_ANSWER,
                  visible_message=response.content,
                  reasoning_summary=response.thinking,
                  tool_batch=None,
                  finalize_mode=FinalizeMode.NONE,
                  domain=self.domain,
                  metadata={"source": "direct_answer"}
              )

          # Step 3: 构建ToolBatch
          if all_tools:
              tool_batch = self._build_tool_batch(all_tools, turn_id)

              # Step 4: 确定finalize_mode
              finalize_mode = self._determine_finalize_mode(response, all_tools)

              # Step 5: 检查是否需要移交workflow（复杂任务）
              if self._should_handoff_to_workflow(all_tools, response):
                  return TurnDecision(
                      turn_id=turn_id,
                      kind=TurnDecisionKind.HANDOFF_WORKFLOW,
                      visible_message=response.content,
                      reasoning_summary=response.thinking,
                      tool_batch=tool_batch,  # 传递工具批给workflow
                      finalize_mode=finalize_mode,
                      domain=self.domain,
                      metadata={"handoff_reason": "complex_exploration_needed"}
                  )

              return TurnDecision(
                  turn_id=turn_id,
                  kind=TurnDecisionKind.TOOL_BATCH,
                  visible_message=response.content,
                  reasoning_summary=response.thinking,
                  tool_batch=tool_batch,
                  finalize_mode=finalize_mode,
                  domain=self.domain,
                  metadata={"tool_count": len(all_tools)}
              )

          # Step 6: 无法确定意图，请求澄清
          return TurnDecision(
              turn_id=turn_id,
              kind=TurnDecisionKind.ASK_USER,
              visible_message="我需要更多信息才能继续。请澄清您的需求。",
              reasoning_summary=response.thinking,
              tool_batch=None,
              finalize_mode=FinalizeMode.NONE,
              domain=self.domain,
              metadata={"source": "clarification_needed"}
          )

      def _extract_tool_calls(self, response: RawLLMResponse) -> list[ToolInvocation]:
          """
          提取工具调用：合并native和textual，但确保单一来源

          关键逻辑：
          - native_tool_calls和textual解析结果合并
          - 按signature去重
          - thinking内容不参与此过程
          """
          tools: list[ToolInvocation] = []
          seen_signatures: set[str] = set()

          # 解析native tool calls
          for native in response.native_tool_calls:
              tool = self._parse_native_tool(native)
              sig = self._signature(tool)
              if sig not in seen_signatures:
                  tools.append(tool)
                  seen_signatures.add(sig)

          # 解析textual wrappers（仅正文，不包括thinking）
          textual_tools = self._parse_textual_tools(response.content)
          for tool in textual_tools:
              sig = self._signature(tool)
              if sig not in seen_signatures:
                  tools.append(tool)
                  seen_signatures.add(sig)

          return tools

      def _parse_native_tool(self, native: dict) -> ToolInvocation:
          """解析provider原生格式"""
          return ToolInvocation(
              call_id=ToolCallId(native.get("id", self._generate_id())),
              tool_name=native["function"]["name"],
              arguments=json.loads(native["function"]["arguments"]),
              execution_mode=self._infer_execution_mode(native["function"]["name"])
          )

      def _parse_textual_tools(self, content: str) -> list[ToolInvocation]:
          """
          解析textual工具调用

          约束：
          - 只解析[TOOL_CALL]或<tool_call>标准格式
          - 拒绝[READ_FILE]等旧格式（防止误解析）
          """
          tools: list[ToolInvocation] = []

          # 使用canonical parser（从原有逻辑迁移）
          from .tool_call_protocol import CanonicalToolCallParser
          parser = CanonicalToolCallParser()

          parsed = parser.parse(content)
          for call in parsed:
              tools.append(ToolInvocation(
                  call_id=ToolCallId(self._generate_id()),
                  tool_name=call["name"],
                  arguments=call["arguments"],
                  execution_mode=self._infer_execution_mode(call["name"])
              ))

          return tools

      def _build_tool_batch(self, tools: list[ToolInvocation], turn_id: TurnId) -> ToolBatch:
          """按执行模式分类工具"""
          parallel = [t for t in tools if t["execution_mode"] == ToolExecutionMode.READONLY_PARALLEL]
          serial = [t for t in tools if t["execution_mode"] == ToolExecutionMode.WRITE_SERIAL]
          async_tools = [t for t in tools if t["execution_mode"] == ToolExecutionMode.ASYNC_RECEIPT]

          return ToolBatch(
              batch_id=BatchId(f"{turn_id}_batch"),
              invocations=tools,
              parallel_readonly=parallel,
              serial_writes=serial,
              async_receipts=async_tools
          )

      def _determine_finalize_mode(self, response: RawLLMResponse, tools: list[ToolInvocation]) -> FinalizeMode:
          """
          确定finalize_mode

          策略：
          - 如果LLM显式指定 → 使用指定值
          - 否则使用领域默认值
          """
          # 检查LLM是否显式指定
          content = response.content.lower()
          if "[finalize_mode:none]" in content:
              return FinalizeMode.NONE
          elif "[finalize_mode:local]" in content:
              return FinalizeMode.LOCAL
          elif "[finalize_mode:llm_once]" in content:
              return FinalizeMode.LLM_ONCE

          # 检查是否有写操作
          has_writes = any(
              t["execution_mode"] == ToolExecutionMode.WRITE_SERIAL
              for t in tools
          )

          # 写操作默认NONE（工具结果即最终答案）
          if has_writes:
              return FinalizeMode.NONE

          # 使用领域默认
          return self._default_finalize

      def _should_handoff_to_workflow(self, tools: list[ToolInvocation], response: RawLLMResponse) -> bool:
          """
          判断是否应该移交workflow层

          触发条件：
          1. 明确标记[handoff_workflow]
          2. 工具数量超过阈值
          3. 包含async工具
          """
          if "[handoff_workflow]" in response.content.lower():
              return True

          if len(tools) > 5:  # 大量工具调用
              return True

          if any(t["execution_mode"] == ToolExecutionMode.ASYNC_RECEIPT for t in tools):
              return True

          return False

      def _is_final_answer(self, response: RawLLMResponse, tools: list[ToolInvocation]) -> bool:
          """判断是否直接回答"""
          if not tools and "[final_answer]" in response.content:
              return True
          return len(tools) == 0 and not response.native_tool_calls

      def _infer_execution_mode(self, tool_name: str) -> ToolExecutionMode:
          """根据工具名推断执行模式"""
          readonly_tools = {"read_file", "list_directory", "grep", "search_code", "glob"}
          write_tools = {"write_file", "edit_file", "delete_file", "bash"}
          async_tools = {"create_pull_request", "submit_job", "trigger_ci"}

          if tool_name in readonly_tools:
              return ToolExecutionMode.READONLY_PARALLEL
          elif tool_name in write_tools:
              return ToolExecutionMode.WRITE_SERIAL
          elif tool_name in async_tools:
              return ToolExecutionMode.ASYNC_RECEIPT
          else:
              # 默认串行（安全优先）
              return ToolExecutionMode.WRITE_SERIAL

      def _signature(self, tool: ToolInvocation) -> str:
          """生成工具签名用于去重"""
          arg_str = json.dumps(tool["arguments"], sort_keys=True)
          return f"{tool['tool_name']}:{arg_str}"

      def _generate_id(self) -> str:
          import uuid
          return str(uuid.uuid4())[:8]

  1.3 关键测试（Phase 1门禁）

  文件：polaris/cells/roles/kernel/tests/test_decision_decoder.py

  import pytest
  from polaris.cells.roles.kernel.internal.turn_decision_decoder import (
      TurnDecisionDecoder, RawLLMResponse
  )
  from polaris.cells.roles.kernel.public.turn_contracts import (
      TurnDecisionKind, FinalizeMode, ToolExecutionMode
  )


  class TestThinkingNeverExecutable:
      """验证：thinking内容永远不会产生工具调用"""

      def test_thinking_with_tool_syntax_not_executed(self):
          """thinking中包含[TOOL_CALL]不应被执行"""
          decoder = TurnDecisionDecoder(domain="document")

          response = RawLLMResponse(
              content="我将帮您查找文件。",  # 正文中无工具
              thinking="""
              让我先思考一下。我需要使用工具：
              [TOOL_CALL]{"tool": "read_file", "args": {"path": "secret.txt"}}[/TOOL_CALL]
              """,
              native_tool_calls=[],
              model="claude",
              usage={}
          )

          decision = decoder.decode(response, "turn_1")

          # 尽管thinking中有工具语法，但不应被解析
          assert decision["kind"] == TurnDecisionKind.FINAL_ANSWER
          assert decision["tool_batch"] is None


  class TestSingleExecutionSource:
      """验证：native和textual不独立执行"""

      def test_native_and_textual_deduplicated(self):
          """同一工具同时出现在native和textual中，只执行一次"""
          decoder = TurnDecisionDecoder(domain="document")

          response = RawLLMResponse(
              content='[TOOL_CALL]{"name": "read_file", "arguments": {"path": "main.py"}}[/TOOL_CALL]',
              thinking=None,
              native_tool_calls=[{
                  "id": "call_1",
                  "function": {
                      "name": "read_file",
                      "arguments": '{"path": "main.py"}'
                  }
              }],
              model="gpt-4",
              usage={}
          )

          decision = decoder.decode(response, "turn_2")

          assert decision["kind"] == TurnDecisionKind.TOOL_BATCH
          # 只应有一个工具（去重后）
          assert len(decision["tool_batch"]["invocations"]) == 1


  class TestFinalizeModeDetermination:
      """验证：finalize_mode正确确定"""

      def test_write_tools_default_none(self):
          """写操作默认finalize_mode=NONE"""
          decoder = TurnDecisionDecoder(domain="document")

          response = RawLLMResponse(
              content='[TOOL_CALL]{"name": "write_file", "arguments": {"path": "test.py", "content": "x"}}[/TOOL_CALL]',
              thinking=None,
              native_tool_calls=[],
              model="claude",
              usage={}
          )

          decision = decoder.decode(response, "turn_3")

          assert decision["finalize_mode"] == FinalizeMode.NONE

      def test_explicit_llm_once_respected(self):
          """LLM显式指定[finalize_mode:llm_once]"""
          decoder = TurnDecisionDecoder(domain="code")

          response = RawLLMResponse(
              content='[TOOL_CALL]{"name": "read_file", "arguments": {"path": "main.py"}}[/TOOL_CALL] [finalize_mode:llm_once]',
              thinking=None,
              native_tool_calls=[],
              model="claude",
              usage={}
          )

          decision = decoder.decode(response, "turn_4")

          assert decision["finalize_mode"] == FinalizeMode.LLM_ONCE

  ---
  Phase 2: TurnTransactionController（第3-4周）

  2.1 事务控制器实现

  文件：polaris/cells/roles/kernel/internal/turn_transaction_controller.py

  """
  TurnTransactionController - 事务型Turn执行

  核心变更：
  1. 删除while continuation loop
  2. 使用显式状态机驱动
  3. 工具执行后不再自动请求LLM
  """

  import asyncio
  import time
  from dataclasses import dataclass, field
  from typing import AsyncIterator, Callable

  from polaris.cells.roles.kernel.public.turn_contracts import (
      TurnDecision, TurnResult, TurnDecisionKind, FinalizeMode,
      TurnFinalization, BatchReceipt
  )
  from polaris.cells.roles.kernel.internal.turn_state_machine import (
      TurnStateMachine, TurnState, InvalidStateTransitionError
  )
  from polaris.cells.roles.kernel.internal.turn_decision_decoder import (
      TurnDecisionDecoder, RawLLMResponse
  )
  from polaris.cells.roles.kernel.internal.tool_batch_runtime import ToolBatchRuntime


  @dataclass
  class TurnContext:
      """Turn执行上下文"""
      user_message: str
      conversation_history: list[dict]
      domain: str = "document"
      workspace: str = "."


  class TurnTransactionController:
      """
      事务型Turn控制器

      执行流程：
      1. DECISION_REQUESTED -> 请求LLM决策
      2. DECISION_DECODED -> 解码TurnDecision
      3. 分支：
         - FINAL_ANSWER -> 直接完成
         - TOOL_BATCH -> 执行工具
         - HANDOFF_WORKFLOW -> 移交
      4. TOOL_BATCH_EXECUTED -> 根据finalize_mode收口
      5. COMPLETED

      关键约束：
      - 没有while loop
      - 每个turn最多2次LLM请求（decision + optional finalization）
      - finalization请求强制tool_choice=none
      """

      def __init__(
          self,
          llm_caller: Callable,  # 依赖注入
          tool_runtime: ToolBatchRuntime,
          domain_policy: dict | None = None
      ):
          self._llm_caller = llm_caller
          self._tool_runtime = tool_runtime
          self._domain_policy = domain_policy or {}

      async def execute(self, context: TurnContext, turn_id: str) -> TurnResult:
          """
          执行单个事务型turn

          这是run()和run_stream()的共同核心
          """
          # 创建状态机
          sm = TurnStateMachine(turn_id=turn_id)

          try:
              # Phase 1: 构建上下文
              sm.transition_to(TurnState.CONTEXT_BUILT)

              # Phase 2: 请求决策
              sm.transition_to(TurnState.DECISION_REQUESTED)
              decision_response = await self._request_decision(context)
              sm.transition_to(TurnState.DECISION_RECEIVED)

              # Phase 3: 解码决策
              decoder = TurnDecisionDecoder(domain=context.domain)
              raw_response = self._to_raw_response(decision_response)
              decision = decoder.decode(raw_response, turn_id)
              sm.transition_to(TurnState.DECISION_DECODED, {"decision_kind": decision["kind"].value})

              # Phase 4: 根据决策类型执行
              if decision["kind"] == TurnDecisionKind.FINAL_ANSWER:
                  return await self._handle_final_answer(sm, decision)

              elif decision["kind"] == TurnDecisionKind.TOOL_BATCH:
                  return await self._handle_tool_batch(sm, decision, context)

              elif decision["kind"] == TurnDecisionKind.HANDOFF_WORKFLOW:
                  return await self._handle_handoff(sm, decision)

              elif decision["kind"] == TurnDecisionKind.ASK_USER:
                  return await self._handle_ask_user(sm, decision)

              else:
                  raise ValueError(f"Unknown decision kind: {decision['kind']}")

          except InvalidStateTransitionError as e:
              # 架构违规，立即失败
              sm.transition_to(TurnState.FAILED)
              raise TurnExecutionError(f"State machine violation: {e}") from e

          except Exception as e:
              sm.transition_to(TurnState.FAILED)
              raise TurnExecutionError(f"Turn execution failed: {e}") from e

      async def _request_decision(self, context: TurnContext) -> dict:
          """
          请求LLM决策

          这是第一次LLM调用（必须）
          """
          messages = self._build_messages(context)

          response = await self._llm_caller.call(
              messages=messages,
              tools=self._get_available_tools(),
              # 允许返回工具调用
              tool_choice="auto"
          )

          return response

      async def _handle_final_answer(
          self,
          sm: TurnStateMachine,
          decision: TurnDecision
      ) -> TurnResult:
          """处理直接回答"""
          sm.transition_to(TurnState.FINAL_ANSWER_READY)
          sm.transition_to(TurnState.COMPLETED)

          return TurnResult(
              turn_id=decision["turn_id"],
              kind="final_answer",
              visible_content=decision["visible_message"],
              decision=decision,
              batch_receipt=None,
              finalization=None,
              workflow_context=None,
              metrics={"llm_calls": 1, "tool_calls": 0}
          )

      async def _handle_tool_batch(
          self,
          sm: TurnStateMachine,
          decision: TurnDecision,
          context: TurnContext
      ) -> TurnResult:
          """处理工具批执行"""
          # 执行工具批
          sm.transition_to(TurnState.TOOL_BATCH_EXECUTING)
          receipt = await self._tool_runtime.execute(decision["tool_batch"])
          sm.transition_to(TurnState.TOOL_BATCH_EXECUTED, {
              "success_count": receipt["success_count"],
              "failure_count": receipt["failure_count"]
          })

          # 根据finalize_mode决定收口策略
          if decision["finalize_mode"] == FinalizeMode.NONE:
              # 直接返回工具结果
              visible = self._format_tool_results(receipt)
              sm.transition_to(TurnState.COMPLETED)

              return TurnResult(
                  turn_id=decision["turn_id"],
                  kind="tool_batch_with_receipt",
                  visible_content=visible,
                  decision=decision,
                  batch_receipt=receipt,
                  finalization=None,
                  workflow_context=None,
                  metrics={"llm_calls": 1, "tool_calls": len(receipt["results"])}
              )

          elif decision["finalize_mode"] == FinalizeMode.LOCAL:
              # 本地模板渲染
              visible = self._local_render(receipt, decision)
              sm.transition_to(TurnState.COMPLETED)

              return TurnResult(
                  turn_id=decision["turn_id"],
                  kind="tool_batch_with_receipt",
                  visible_content=visible,
                  decision=decision,
                  batch_receipt=receipt,
                  finalization=TurnFinalization(
                      turn_id=decision["turn_id"],
                      mode=FinalizeMode.LOCAL,
                      final_visible_message=visible,
                      needs_followup_workflow=False,
                      workflow_reason=None
                  ),
                  workflow_context=None,
                  metrics={"llm_calls": 1, "tool_calls": len(receipt["results"])}
              )

          elif decision["finalize_mode"] == FinalizeMode.LLM_ONCE:
              # 允许一次LLM收口（禁止再调工具）
              return await _execute_llm_once_finalization(sm, decision, receipt, context)

          else:
              raise ValueError(f"Unknown finalize_mode: {decision['finalize_mode']}")

      async def _execute_llm_once_finalization(
          self,
          sm: TurnStateMachine,
          decision: TurnDecision,
          receipt: BatchReceipt,
          context: TurnContext
      ) -> TurnResult:
          """
          执行LLM_ONCE收口

          关键约束：
          1. 这是第二次也是最后一次LLM调用
          2. 强制tool_choice=none（禁止再调工具）
          3. 如果LLM仍返回工具调用，视为协议错误
          """
          sm.transition_to(TurnState.FINALIZATION_REQUESTED)

          # 构建收口请求（包含工具结果）
          messages = self._build_finalization_messages(decision, receipt, context)

          response = await self._llm_caller.call(
              messages=messages,
              tools=self._get_available_tools(),
              tool_choice="none"  # 强制禁止工具！
          )

          sm.transition_to(TurnState.FINALIZATION_RECEIVED)

          # 验证：收口响应不应包含工具调用
          if response.get("tool_calls"):
              # 协议违规：finalization不应产生工具调用
              sm.transition_to(TurnState.FAILED)
              raise ProtocolViolationError(
                  "Finalization phase returned tool calls. "
                  "This violates the single-transaction contract."
              )

          final_content = response["content"]
          sm.transition_to(TurnState.COMPLETED)

          return TurnResult(
              turn_id=decision["turn_id"],
              kind="tool_batch_with_receipt",
              visible_content=final_content,
              decision=decision,
              batch_receipt=receipt,
              finalization=TurnFinalization(
                  turn_id=decision["turn_id"],
                  mode=FinalizeMode.LLM_ONCE,
                  final_visible_message=final_content,
                  needs_followup_workflow=False,
                  workflow_reason=None
              ),
              workflow_context=None,
              metrics={"llm_calls": 2, "tool_calls": len(receipt["results"])}
          )

      async def _handle_handoff(
          self,
          sm: TurnStateWorkflow,
          decision: TurnDecision
      ) -> TurnResult:
          """处理workflow移交"""
          sm.transition_to(TurnState.HANDOFF_WORKFLOW)
          sm.transition_to(TurnState.COMPLETED)

          return TurnResult(
              turn_id=decision["turn_id"],
              kind="handoff_workflow",
              visible_content=decision["visible_message"],
              decision=decision,
              batch_receipt=None,  # workflow层接管
              finalization=None,
              workflow_context={
                  "reason": decision["metadata"].get("handoff_reason"),
                  "initial_tool_batch": decision.get("tool_batch"),
                  "domain": decision["domain"]
              },
              metrics={"llm_calls": 1, "tool_calls": 0, "handoff": True}
          )

      async def _handle_ask_user(
          self,
          sm: TurnStateMachine,
          decision: TurnDecision
      ) -> TurnResult:
          """处理需要澄清"""
          sm.transition_to(TurnState.COMPLETED)

          return TurnResult(
              turn_id=decision["turn_id"],
              kind="ask_user",
              visible_content=decision["visible_message"],
              decision=decision,
              batch_receipt=None,
              finalization=None,
              workflow_context=None,
              metrics={"llm_calls": 1, "tool_calls": 0}
          )

      # ===== Helper methods =====

      def _build_messages(self, context: TurnContext) -> list[dict]:
          """构建LLM消息"""
          messages = context.conversation_history.copy()
          messages.append({"role": "user", "content": context.user_message})
          return messages

      def _build_finalization_messages(
          self,
          decision: TurnDecision,
          receipt: BatchReceipt,
          context: TurnContext
      ) -> list[dict]:
          """构建收口请求消息"""
          messages = context.conversation_history.copy()

          # 添加原始决策
          messages.append({
              "role": "assistant",
              "content": decision["visible_message"]
          })

          # 添加工具结果
          tool_results_text = self._format_tool_results_for_llm(receipt)
          messages.append({
              "role": "user",  # 系统角色呈现工具结果
              "content": f"工具执行结果：\n{tool_results_text}\n\n请基于以上结果给出最终回答。"
          })

          return messages

      def _format_tool_results(self, receipt: BatchReceipt) -> str:
          """格式化工具结果为可见文本（NONE模式）"""
          lines = []
          for result in receipt["results"]:
              status_icon = "✓" if result["status"] == "success" else "✗"
              lines.append(f"{status_icon} {result['tool_name']}: {result['status']}")
          return "\n".join(lines)

      def _local_render(self, receipt: BatchReceipt, decision: TurnDecision) -> str:
          """本地模板渲染（LOCAL模式）"""
          # 简单模板，可扩展
          tool_summaries = []
          for result in receipt["results"]:
              if result["tool_name"] in {"read_file", "list_directory"}:
                  tool_summaries.append(f"已读取 {result['result'].get('path', 'unknown')}")
              elif result["tool_name"] == "grep":
                  tool_summaries.append(f"搜索到 {result['result'].get('match_count', 0)} 个匹配")

          return f"已完成以下操作：\n" + "\n".join(f"- {s}" for s in tool_summaries)

      def _to_raw_response(self, llm_response: dict) -> RawLLMResponse:
          """转换LLM响应格式"""
          return RawLLMResponse(
              content=llm_response.get("content", ""),
              thinking=llm_response.get("thinking"),
              native_tool_calls=llm_response.get("tool_calls", []),
              model=llm_response.get("model", "unknown"),
              usage=llm_response.get("usage", {})
          )

      def _get_available_tools(self) -> list[dict]:
          """获取可用工具列表"""
          # 从tool registry加载
          from polaris.kernelone.llm.toolkit.definitions import TOOL_DEFINITIONS
          return list(TOOL_DEFINITIONS.values())


  class TurnExecutionError(Exception):
      """Turn执行错误"""
      pass


  class ProtocolViolationError(Exception):
      """协议违规错误"""
      pass

  2.2 流式执行支持

  文件：polaris/cells/roles/kernel/internal/streaming_controller.py

  """
  StreamingController - 流式输出的事务化支持

  核心设计：
  - 流式输出不改变事务语义
  - 事件流仅用于UI显示，不驱动执行
  - 执行仍由TurnTransactionController决定
  """

  from typing import AsyncIterator
  from dataclasses import dataclass

  from polaris.cells.roles.kernel.public.turn_events import (
      TurnPhaseEvent, ContentChunkEvent, ToolBatchEvent,
      FinalizationEvent, CompletionEvent, ErrorEvent
  )


  @dataclass
  class StreamConfig:
      """流式配置"""
      yield_thinking: bool = True
      yield_tool_progress: bool = True
      buffer_size: int = 1024


  class StreamingController:
      """
      流式控制器

      职责：
      1. 把TurnTransactionController的执行过程转为事件流
      2. 保持事务语义不变
      3. 让用户看到decision/tool_batch/finalization阶段
      """

      def __init__(self, transaction_controller: TurnTransactionController):
          self._tx = transaction_controller

      async def execute_stream(
          self,
          context: TurnContext,
          turn_id: str,
          config: StreamConfig | None = None
      ) -> AsyncIterator[TurnPhaseEvent]:
          """
          流式执行turn

          事件序列：
          1. phase:decision_requested
          2. content_chunk（LLM输出片段）
          3. phase:decision_completed
          4. phase:tool_batch_started（如果有工具）
          5. tool_progress（工具执行进度）
          6. phase:tool_batch_completed
          7. phase:finalization_requested（如果finalize_mode=llm_once）
          8. content_chunk（收口输出）
          9. phase:finalization_completed
          10. completion
          """
          config = config or StreamConfig()

          try:
              # 启动事务执行，但拦截中间事件
              async for event in self._execute_with_interception(context, turn_id, config):
                  yield event

          except Exception as e:
              yield ErrorEvent(
                  turn_id=turn_id,
                  error_type=type(e).__name__,
                  message=str(e)
              )

      async def _execute_with_interception(
          self,
          context: TurnContext,
          turn_id: str,
          config: StreamConfig
      ) -> AsyncIterator[TurnPhaseEvent]:
          """执行并拦截为事件流"""

          # 阶段1：决策请求
          yield TurnPhaseEvent(turn_id=turn_id, phase="decision_requested")

          # 创建streaming LLM调用（yield chunks）
          decision_chunks = []
          async for chunk in self._stream_decision_llm(context):
              decision_chunks.append(chunk)
              if chunk["type"] == "content":
                  yield ContentChunkEvent(
                      turn_id=turn_id,
                      chunk=chunk["text"],
                      is_finalization=False
                  )
              elif chunk["type"] == "thinking" and config.yield_thinking:
                  yield ContentChunkEvent(
                      turn_id=turn_id,
                      chunk=chunk["text"],
                      is_thinking=True
                  )

          # 组装完整响应
          full_response = self._assemble_chunks(decision_chunks)
          yield TurnPhaseEvent(turn_id=turn_id, phase="decision_completed")

          # 解码决策（非流式，瞬时）
          decoder = TurnDecisionDecoder(domain=context.domain)
          decision = decoder.decode(self._to_raw(full_response), turn_id)

          # 阶段2：工具执行（如果有）
          if decision["kind"] == TurnDecisionKind.TOOL_BATCH:
              yield TurnPhaseEvent(turn_id=turn_id, phase="tool_batch_started")

              # 逐个yield工具进度
              receipt = await self._execute_tools_streaming(
                  decision["tool_batch"],
                  turn_id,
                  config
              )

              yield TurnPhaseEvent(turn_id=turn_id, phase="tool_batch_completed")

              # 阶段3：收口（如果需要）
              if decision["finalize_mode"] == FinalizeMode.LLM_ONCE:
                  yield TurnPhaseEvent(turn_id=turn_id, phase="finalization_requested")

                  async for chunk in self._stream_finalization_llm(decision, receipt):
                      yield ContentChunkEvent(
                          turn_id=turn_id,
                          chunk=chunk["text"],
                          is_finalization=True  # 标记这是收口输出
                      )

                  yield TurnPhaseEvent(turn_id=turn_id, phase="finalization_completed")

          yield CompletionEvent(turn_id=turn_id, status="success")

      async def _stream_decision_llm(self, context: TurnContext) -> AsyncIterator[dict]:
          """流式请求决策LLM"""
          # 调用streaming LLM API
          messages = self._build_messages(context)
          async for chunk in self._llm_caller.call_stream(messages=messages):
              yield chunk

      async def _execute_tools_streaming(
          self,
          tool_batch: ToolBatch,
          turn_id: str,
          config: StreamConfig
      ) -> BatchReceipt:
          """流式执行工具（yield进度）"""
          # 先执行并行只读工具
          readonly_tasks = [
              self._execute_tool_with_progress(t, turn_id)
              for t in tool_batch["parallel_readonly"]
          ]

          results = []
          for coro in asyncio.as_completed(readonly_tasks):
              result = await coro
              results.append(result)
              yield ToolBatchEvent(
                  turn_id=turn_id,
                  tool_name=result["tool_name"],
                  status=result["status"],
                  progress=len(results) / len(readonly_tasks)
              )

          # 串行执行写工具
          for tool in tool_batch["serial_writes"]:
              result = await self._execute_tool_with_progress(tool, turn_id)
              results.append(result)
              yield ToolBatchEvent(
                  turn_id=turn_id,
                  tool_name=result["tool_name"],
                  status=result["status"],
                  progress=len(results) / len(tool_batch["invocations"])
              )

          # 组装receipt
          return BatchReceipt(
              batch_id=tool_batch["batch_id"],
              turn_id=turn_id,
              results=results,
              success_count=sum(1 for r in results if r["status"] == "success"),
              failure_count=sum(1 for r in results if r["status"] == "error"),
              pending_async_count=len(tool_batch["async_receipts"]),
              has_pending_async=len(tool_batch["async_receipts"]) > 0,
              raw_results=results
          )

  ---
  Phase 3: ToolBatchRuntime（第5-6周）

  文件：polaris/cells/roles/kernel/internal/tool_batch_runtime.py

  """
  ToolBatchRuntime - 工具批执行运行时

  核心特性：
  1. 并行只读、串行写、异步receipt分离
  2. 每个工具带effect receipt
  3. 失败策略可配置
  """

  import asyncio
  from typing import Any
  from dataclasses import dataclass

  from polaris.cells.roles.kernel.public.turn_contracts import (
      ToolBatch, BatchReceipt, ToolExecutionResult, ToolExecutionMode
  )


  @dataclass
  class ExecutionConfig:
      """执行配置"""
      readonly_timeout_seconds: float = 30.0
      write_timeout_seconds: float = 60.0
      fail_fast_on_write_error: bool = True
      max_parallel_readonly: int = 10


  class ToolBatchRuntime:
      """
      工具批运行时

      执行策略：
      - READONLY_PARALLEL: asyncio.gather并行
      - WRITE_SERIAL: 一个一个执行，失败可中止
      - ASYNC_RECEIPT: 启动异步任务，立即返回pending receipt
      """

      def __init__(self, tool_executor: Any, config: ExecutionConfig | None = None):
          self._executor = tool_executor
          self._config = config or ExecutionConfig()

      async def execute(self, batch: ToolBatch) -> BatchReceipt:
          """
          执行工具批

          执行顺序：
          1. 并行执行所有READONLY_PARALLEL
          2. 串行执行WRITE_SERIAL（失败时根据配置中止）
          3. 启动ASYNC_RECEIPT任务（不等待完成）
          """
          all_results: list[ToolExecutionResult] = []

          # 阶段1：并行只读
          if batch["parallel_readonly"]:
              readonly_results = await self._execute_parallel_readonly(
                  batch["parallel_readonly"]
              )
              all_results.extend(readonly_results)

          # 阶段2：串行写
          if batch["serial_writes"]:
              write_results = await self._execute_serial_writes(
                  batch["serial_writes"]
              )
              all_results.extend(write_results)

              # 检查是否需要中止
              if self._config.fail_fast_on_write_error:
                  failures = [r for r in write_results if r["status"] == "error"]
                  if failures:
                      # 中止剩余任务（如果有）
                      return self._build_receipt(batch, all_results, aborted=True)

          # 阶段3：异步任务（启动即返回pending）
          if batch["async_receipts"]:
              async_results = await self._execute_async_receipts(
                  batch["async_receipts"]
              )
              all_results.extend(async_results)

          return self._build_receipt(batch, all_results)

      async def _execute_parallel_readonly(
          self,
          tools: list[ToolInvocation]
      ) -> list[ToolExecutionResult]:
          """并行执行只读工具"""
          semaphore = asyncio.Semaphore(self._config.max_parallel_readonly)

          async def execute_with_limit(tool: ToolInvocation) -> ToolExecutionResult:
              async with semaphore:
                  return await self._execute_single_tool(
                      tool,
                      timeout=self._config.readonly_timeout_seconds
                  )

          tasks = [execute_with_limit(t) for t in tools]
          return await asyncio.gather(*tasks, return_exceptions=True)

      async def _execute_serial_writes(
          self,
          tools: list[ToolInvocation]
      ) -> list[ToolExecutionResult]:
          """串行执行写工具"""
          results = []

          for tool in tools:
              result = await self._execute_single_tool(
                  tool,
                  timeout=self._config.write_timeout_seconds
              )
              results.append(result)

              # 如果失败且配置为fail_fast，停止后续执行
              if result["status"] == "error" and self._config.fail_fast_on_write_error:
                  # 记录剩余工具为aborted
                  for remaining in tools[len(results):]:
                      results.append(ToolExecutionResult(
                          call_id=remaining["call_id"],
                          tool_name=remaining["tool_name"],
                          status="aborted",
                          result=None,
                          execution_time_ms=0,
                          effect_receipt=None
                      ))
                  break

          return results

      async def _execute_async_receipts(
          self,
          tools: list[ToolInvocation]
      ) -> list[ToolExecutionResult]:
          """
          执行异步工具

          策略：启动后台任务，立即返回pending receipt
          实际结果将通过workflow层后续查询
          """
          results = []

          for tool in tools:
              # 启动异步任务（不等待）
              task_id = await self._executor.start_async_task(
                  tool_name=tool["tool_name"],
                  arguments=tool["arguments"]
              )

              # 立即返回pending receipt
              results.append(ToolExecutionResult(
                  call_id=tool["call_id"],
                  tool_name=tool["tool_name"],
                  status="pending",
                  result={"async_task_id": task_id},
                  execution_time_ms=0,
                  effect_receipt={"status": "pending", "task_id": task_id}
              ))

          return results

      async def _execute_single_tool(
          self,
          tool: ToolInvocation,
          timeout: float
      ) -> ToolExecutionResult:
          """执行单个工具"""
          start_time = time.time()

          try:
              # 使用现有的tool executor
              result = await asyncio.wait_for(
                  self._executor.execute(
                      tool_name=tool["tool_name"],
                      arguments=tool["arguments"]
                  ),
                  timeout=timeout
              )

              execution_time_ms = int((time.time() - start_time) * 1000)

              # 生成effect receipt（用于审计）
              effect_receipt = self._generate_effect_receipt(tool, result)

              return ToolExecutionResult(
                  call_id=tool["call_id"],
                  tool_name=tool["tool_name"],
                  status="success",
                  result=result,
                  execution_time_ms=execution_time_ms,
                  effect_receipt=effect_receipt
              )

          except asyncio.TimeoutError:
              return ToolExecutionResult(
                  call_id=tool["call_id"],
                  tool_name=tool["tool_name"],
                  status="timeout",
                  result={"error": f"Timeout after {timeout}s"},
                  execution_time_ms=int(timeout * 1000),
                  effect_receipt=None
              )
          except Exception as e:
              return ToolExecutionResult(
                  call_id=tool["call_id"],
                  tool_name=tool["tool_name"],
                  status="error",
                  result={"error": str(e)},
                  execution_time_ms=int((time.time() - start_time) * 1000),
                  effect_receipt=None
              )

      def _generate_effect_receipt(
          self,
          tool: ToolInvocation,
          result: Any
      ) -> dict[str, Any]:
          """生成effect receipt（写操作审计）"""
          return {
              "tool_name": tool["tool_name"],
              "arguments": tool["arguments"],
              "timestamp": time.time(),
              "result_hash": hash(str(result)),  # 简化，实际用更安全的hash
              "receipt_version": "1.0"
          }

      def _build_receipt(
          self,
          batch: ToolBatch,
          results: list[ToolExecutionResult],
          aborted: bool = False
      ) -> BatchReceipt:
          """构建BatchReceipt"""
          return BatchReceipt(
              batch_id=batch["batch_id"],
              turn_id=batch["turn_id"],
              results=results,
              success_count=sum(1 for r in results if r["status"] == "success"),
              failure_count=sum(1 for r in results if r["status"] == "error"),
              pending_async_count=sum(1 for r in results if r["status"] == "pending"),
              has_pending_async=any(r["status"] == "pending" for r in results),
              raw_results=results
          )

  ---
  Phase 4: Workflow Handoff（第7-8周）

  文件：polaris/cells/orchestration/workflow_runtime/exploration_workflow.py

  """
  ExplorationWorkflow - 多步探索工作流

  职责：
  1. 接管TurnEngine移交的复杂任务
  2. 管理read-analyze-read循环
  3. 处理async工具等待
  4. 在budget限制内收敛
  """

  from dataclasses import dataclass


  @dataclass
  class ExplorationBudget:
      """探索预算"""
      max_turns: int = 5
      max_tool_calls: int = 20
      max_wall_time_seconds: float = 300.0


  class ExplorationWorkflow:
      """
      探索工作流

      使用场景：
      - 需要多次读文件才能回答的问题
      - async工具pending需要等待
      - 需要反复试探的代码分析
      """

      def __init__(
          self,
          turn_engine_factory: Callable,  # 创建新的turn engine实例
          budget: ExplorationBudget | None = None
      ):
          self._turn_engine_factory = turn_engine_factory
          self._budget = budget or ExplorationBudget()

      async def run(self, initial_context: WorkflowContext) -> WorkflowResult:
          """
          执行探索工作流

          模式：多次调用TurnEngine，每次一个事务
          """
          start_time = time.time()
          total_tool_calls = 0

          context = initial_context
          history: list[TurnResult] = []

          for turn_num in range(self._budget.max_turns):
              # 检查预算
              if time.time() - start_time > self._budget.max_wall_time_seconds:
                  return WorkflowResult(
                      status="budget_exceeded",
                      error="Wall time budget exceeded",
                      partial_results=history
                  )

              # 创建新的turn（每个turn独立事务）
              turn_engine = self._turn_engine_factory()

              turn_result = await turn_engine.execute(
                  context=self._build_turn_context(context, history),
                  turn_id=f"{context.workflow_id}_turn_{turn_num}"
              )

              history.append(turn_result)
              total_tool_calls += turn_result["metrics"]["tool_calls"]

              if total_tool_calls > self._budget.max_tool_calls:
                  return WorkflowResult(
                      status="budget_exceeded",
                      error="Tool call budget exceeded",
                      partial_results=history
                  )

              # 收敛判断
              if turn_result["kind"] == "final_answer":
                  return WorkflowResult(
                      status="success",
                      answer=turn_result["visible_content"],
                      turns_used=turn_num + 1,
                      history=history
                  )

              # 处理handoff（理论上不应递归，但防御性编程）
              if turn_result["kind"] == "handoff_workflow":
                  # 如果是另一个exploration，继续迭代
                  # 否则返回错误
                  continue

              # 更新context进行下一轮
              context = self._update_context(context, turn_result)

          # 达到max_turns仍未收敛
          return WorkflowResult(
              status="not_converged",
              error=f"Did not converge after {self._budget.max_turns} turns",
              partial_results=history
          )

  ---
  Phase 5: 测试与验证（贯穿全程）

  5.1 核心测试文件清单
  ┌────────────────────────────────┬──────────────────────────┬──────────┐
  │            测试文件            │         测试重点         │ 门禁级别 │
  ├────────────────────────────────┼──────────────────────────┼──────────┤
  │ test_state_machine.py          │ 状态转换规则、禁止循环   │ Blocker  │
  ├────────────────────────────────┼──────────────────────────┼──────────┤
  │ test_decision_decoder.py       │ thinking不执行、来源唯一 │ Blocker  │
  ├────────────────────────────────┼──────────────────────────┼──────────┤
  │ test_transaction_controller.py │ 无continuation loop      │ Blocker  │
  ├────────────────────────────────┼──────────────────────────┼──────────┤
  │ test_finalization_policy.py    │ llm_once禁止工具         │ Blocker  │
  ├────────────────────────────────┼──────────────────────────┼──────────┤
  │ test_stream_run_parity.py      │ stream/run等价           │ Critical │
  ├────────────────────────────────┼──────────────────────────┼──────────┤
  │ test_tool_batch_runtime.py     │ 并行/串行/异步正确       │ Critical │
  ├────────────────────────────────┼──────────────────────────┼──────────┤
  │ test_workflow_handoff.py       │ handoff链路完整          │ Critical │
  └────────────────────────────────┴──────────────────────────┴──────────┘
  5.2 关键测试用例

  # test_transaction_controller.py

  class TestNoContinuationLoop:
      """验证：删除continuation loop"""

      @pytest.mark.asyncio
      async def test_tool_execution_does_not_trigger_llm_continuation(self):
          """
          关键测试：工具执行后不再自动请求LLM

          旧行为：工具结果append到history，自动进入下一轮LLM
          新行为：根据finalize_mode决定，默认直接完成
          """
          # 准备：mock llm caller，记录调用次数
          call_count = 0

          async def mock_llm_caller(**kwargs):
              nonlocal call_count
              call_count += 1

              # 第一次返回工具调用
              if call_count == 1:
                  return {
                      "content": "我将读取文件",
                      "tool_calls": [{
                          "id": "call_1",
                          "function": {
                              "name": "read_file",
                              "arguments": '{"path": "test.py"}'
                          }
                      }]
                  }

              # 不应有第二次调用（finalize_mode=none）
              raise AssertionError("Should not make second LLM call when finalize_mode=none")

          controller = TurnTransactionController(
              llm_caller=mock_llm_caller,
              tool_runtime=mock_tool_runtime
          )

          result = await controller.execute(
              context=TurnContext(user_message="读取test.py", conversation_history=[]),
              turn_id="test_1"
          )

          # 只应有一次LLM调用
          assert call_count == 1
          assert result["metrics"]["llm_calls"] == 1


  class TestFinalizationToolChoiceNone:
      """验证：finalization强制tool_choice=none"""

      @pytest.mark.asyncio
      async def test_llm_once_finalization_cannot_issue_tools(self):
          """
          关键测试：llm_once收口时，如果LLM返回工具调用，视为协议错误
          """
          call_count = 0

          async def mock_llm_caller(**kwargs):
              nonlocal call_count
              call_count += 1

              if call_count == 1:
                  # 第一次：返回工具调用
                  return {
                      "content": "我将读取文件",
                      "tool_calls": [{
                          "id": "call_1",
                          "function": {
                              "name": "read_file",
                              "arguments": '{"path": "test.py"}'
                          }
                      }]
                  }

              if call_count == 2:
                  # 验证：第二次调用强制了tool_choice=none
                  assert kwargs.get("tool_choice") == "none", \
                      "Finalization must force tool_choice=none"

                  # 模拟LLM违规：在finalization返回工具
                  return {
                      "content": "我再查一下",
                      "tool_calls": [{
                          "id": "call_2",
                          "function": {
                              "name": "grep",
                              "arguments": '{"pattern": "test"}'
                          }
                      }]
                  }

          controller = TurnTransactionController(
              llm_caller=mock_llm_caller,
              tool_runtime=mock_tool_runtime
          )

          # 应抛出协议违规错误
          with pytest.raises(ProtocolViolationError) as exc_info:
              await controller.execute(
                  context=TurnContext(
                      user_message="读取并总结",
                      conversation_history=[],
                      domain="document"  # document域默认llm_once
                  ),
                  turn_id="test_2"
              )

          assert "Finalization cannot issue tools" in str(exc_info.value)

  ---
  Phase 6: CLI与观测（第9-10周）

  6.1 观测事件系统

  文件：polaris/cells/roles/kernel/public/turn_events.py

  from typing import Literal
  from dataclasses import dataclass


  @dataclass(frozen=True)
  class TurnPhaseEvent:
      """阶段事件"""
      turn_id: str
      phase: Literal[
          "decision_requested",
          "decision_completed",
          "tool_batch_started",
          "tool_batch_completed",
          "finalization_requested",
          "finalization_completed",
          "workflow_handoff",
          "failed"
      ]
      timestamp_ms: int
      metadata: dict


  @dataclass(frozen=True)
  class ContentChunkEvent:
      """内容片段（流式）"""
      turn_id: str
      chunk: str
      is_thinking: bool = False
      is_finalization: bool = False  # 标记是否是收口输出


  @dataclass(frozen=True)
  class ToolBatchEvent:
      """工具执行进度"""
      turn_id: str
      tool_name: str
      status: str
      progress: float  # 0.0-1.0


  @dataclass(frozen=True)
  class FinalizationEvent:
      """收口事件"""
      turn_id: str
      mode: str  # "none", "local", "llm_once"


  @dataclass(frozen=True)
  class CompletionEvent:
      """完成事件"""
      turn_id: str
      status: Literal["success", "failed", "handoff"]


  @dataclass(frozen=True)
  class ErrorEvent:
      """错误事件"""
      turn_id: str
      error_type: str
      message: str

  6.2 CLI渲染

  # polaris/delivery/cli/turn_renderer.py

  class TurnCLIRenderer:
      """CLI渲染器"""

      def __init__(self, console: Console):
          self._console = console
          self._current_spinner = None

      def on_event(self, event: TurnPhaseEvent):
          """处理事件"""

          if isinstance(event, TurnPhaseEvent):
              if event.phase == "decision_requested":
                  self._current_spinner = self._console.status("[bold green]Analyzing...")
                  self._current_spinner.start()

              elif event.phase == "decision_completed":
                  if self._current_spinner:
                      self._current_spinner.stop()
                  self._console.print("[bold green]✓[/bold green] Decision complete")

              elif event.phase == "tool_batch_started":
                  self._console.print("\n[bold blue]Executing tools...[/bold blue]")

              elif event.phase == "tool_batch_completed":
                  self._console.print("[bold blue]✓ Tools complete[/bold blue]\n")

              elif event.phase == "finalization_requested":
                  # 关键：明确标记这是收口阶段
                  self._current_spinner = self._console.status(
                      "[bold yellow]Finalizing (synthesizing results)..."
                  )
                  self._current_spinner.start()

              elif event.phase == "finalization_completed":
                  if self._current_spinner:
                      self._current_spinner.stop()
                  self._console.print("[bold yellow]✓ Finalization complete[/bold yellow]")

          elif isinstance(event, ContentChunkEvent):
              if event.is_finalization:
                  # 收口输出使用不同颜色，让用户明确知道这是第二阶段
                  self._console.print(f"[yellow]{event.chunk}[/yellow]", end="")
              elif event.is_thinking:
                  # thinking灰色显示
                  self._console.print(f"[dim]{event.chunk}[/dim]", end="")
              else:
                  self._console.print(event.chunk, end="")

          elif isinstance(event, ToolBatchEvent):
              icon = "✓" if event.status == "success" else "✗"
              color = "green" if event.status == "success" else "red"
              self._console.print(
                  f"  [{color}]{icon}[/{color}] {event.tool_name} "
                  f"({int(event.progress * 100)}%)"
              )

  ---
  第四部分：迁移与回滚策略

  4.1 渐进式迁移

  # turn_engine.py - 兼容层

  class TurnEngine:
      """
      兼容层：保持原有接口，内部路由到新实现
      """

      def __init__(self, use_transactional: bool = True):
          self._use_transactional = use_transactional
          if use_transactional:
              self._controller = TurnTransactionController(...)
          else:
              self._legacy = LegacyTurnEngine(...)  # 保留旧实现

      async def run(self, request, role, **kwargs):
          if self._use_transactional:
              return await self._run_transactional(request, role, **kwargs)
          else:
              return await self._legacy.run(request, role, **kwargs)

      async def _run_transactional(self, request, role, **kwargs):
          """新实现包装"""
          context = TurnContext(
              user_message=request.message,
              conversation_history=request.history,
              domain=self._infer_domain(role)
          )

          result = await self._controller.execute(context, turn_id=generate_id())

          # 转换为旧格式（兼容）
          return self._adapt_result(result)

  4.2 特性开关

  # config/turn_engine.yaml

  turn_engine:
    implementation: "transactional"  # 或 "legacy" 用于回滚

    transactional:
      # 新配置
      default_finalize_mode:
        document: "llm_once"
        code: "none"

      enforce_single_commit_point: true
      enforce_no_continuation_loop: true

    legacy:
      # 旧配置保留
      max_continuation_loops: 10

  4.3 回滚条件

  触发立即回滚到legacy的条件：

  1. 生产环境出现ProtocolViolationError
  2. stream/run parity测试失败率>5%
  3. 用户反馈"工具执行后无响应"（finalize_mode配置错误）
  4. 性能回归（事务化开销 unacceptable）

  ---
  第五部分：团队分工（8人）
  ┌────────────────────────┬───────────┬──────────────────────────────────────────┬────────────────────────────────┐
  │          角色          │ 负责Phase │                核心交付物                │            关键技能            │
  ├────────────────────────┼───────────┼──────────────────────────────────────────┼────────────────────────────────┤
  │ Tech Lead              │ 全阶段    │ 架构裁决、代码审查、接口冻结             │ 10年+ Python，状态机，风险管理 │
  ├────────────────────────┼───────────┼──────────────────────────────────────────┼────────────────────────────────┤
  │ State Machine Engineer │ Phase 1   │ turn_state_machine.py, turn_contracts.py │ 复杂状态机，类型系统           │
  ├────────────────────────┼───────────┼──────────────────────────────────────────┼────────────────────────────────┤
  │ Decision Decoder       │ Phase 1-2 │ turn_decision_decoder.py                 │ 协议解析，LLM输出处理          │
  ├────────────────────────┼───────────┼──────────────────────────────────────────┼────────────────────────────────┤
  │ Transaction Controller │ Phase 2-3 │ turn_transaction_controller.py           │ asyncio，事务设计              │
  ├────────────────────────┼───────────┼──────────────────────────────────────────┼────────────────────────────────┤
  │ Tool Runtime Engineer  │ Phase 3   │ tool_batch_runtime.py                    │ 并发控制，副作用管理           │
  ├────────────────────────┼───────────┼──────────────────────────────────────────┼────────────────────────────────┤
  │ Workflow Engineer      │ Phase 4   │ exploration_workflow.py                  │ 工作流引擎，异步任务           │
  ├────────────────────────┼───────────┼──────────────────────────────────────────┼────────────────────────────────┤
  │ Observability Engineer │ Phase 5-6 │ turn_events.py, 审计日志                 │ 结构化日志，事件系统           │
  ├────────────────────────┼───────────┼──────────────────────────────────────────┼────────────────────────────────┤
  │ QA Architect           │ 贯穿      │ 测试矩阵、门禁设计、回归防护             │ 测试架构，边界case设计         │
  └────────────────────────┴───────────┴──────────────────────────────────────────┴────────────────────────────────┘
  ---
  第六部分：验收标准

  6.1 功能验收

  - thinking内容中包含工具语法不会执行
  - native和textual工具调用去重，不重复执行
  - 工具执行后默认不再请求LLM（finalize_mode=none）
  - llm_once模式下第二次请求强制tool_choice=none
  - 如果finalization返回工具调用，抛出ProtocolViolationError
  - stream和run输出等价（parity测试）
  - CLI明确显示finalization阶段

  6.2 性能验收

  - 单turn延迟不增加>10%（对比legacy）
  - 内存使用不增加>20%
  - 并发处理能力不变

  6.3 安全验收

  - 所有状态转换经过状态机验证
  - 禁止的状态转换触发InvalidStateTransitionError
  - 写操作带effect receipt