 ---
  ContextOS 运作原理深度审计报告

  概述

  ContextOS 是Polaris 的核心上下文管理系统，采用 State-First Architecture（状态优先架构），通过 Event Sourcing 模式管理对话历史和状态。它与 TurnEngine、Provider Adapter
  协同工作，形成完整的 LLM 请求链路。

  ---
  一、核心架构层次图

  ┌─────────────────────────────────────────────────────────────────────────────┐
  │                          用户请求入口                             │
  │            role_dialogue.generate_role_response(role, message)              │
  └─────────────────────────────────────────────────────────────────────────────┘
                                      ↓
  ┌─────────────────────────────────────────────────────────────────────────────┐
  │                     RoleExecutionKernel (角色执行内核)                       │
  │              polaris/cells/roles/kernel/internal/                            │
  │  提供: LLMCaller, OutputParser, PromptBuilder, ToolExecutor                  │
  └─────────────────────────────────────────────────────────────────────────────┘
                                      ↓
  ┌─────────────────────────────────────────────────────────────────────────────┐
  │                     ContextOS (状态优先上下文引擎)                           │
  │              polaris/kernelone/context/context_os/                           │
  │  核心类: StateFirstContextOS, ContextOSSnapshot, ContextOSProjection         │
  │  状态: transcript_log, working_state, artifact_store, episode_store          │
  └─────────────────────────────────────────────────────────────────────────────┘
                                      ↓
  ┌─────────────────────────────────────────────────────────────────────────────┐
  │                     TurnEngine (统一执行循环引擎)                            │
  │              polaris/cells/roles/kernel/internal/turn_engine/                │
  │  方法: run() (非流式), run_stream() (流式)                                   │
  │  组件: ToolLoopController, PolicyLayer, SafetyState                         │
  └─────────────────────────────────────────────────────────────────────────────┘
                                      ↓
  ┌─────────────────────────────────────────────────────────────────────────────┐
  │                     ProviderAdapter (消息格式适配)                           │
  │              polaris/kernelone/llm/provider_adapters/                         │
  │  实现: AnthropicMessagesAdapter, OpenAIResponsesAdapter, OllamaChatAdapter  │
  │  转换: transcript → provider-native messages                                 │
  └─────────────────────────────────────────────────────────────────────────────┘
                                      ↓
  ┌─────────────────────────────────────────────────────────────────────────────┐
  │                     BaseProvider (底层API调用)                               │
  │              polaris/kernelone/llm/providers/                                │
  │  实现: AnthropicCompatProvider, OpenAICompatProvider                        │
  │  方法: invoke(), invoke_stream(), invoke_stream_events()                    │
  └─────────────────────────────────────────────────────────────────────────────┘
                                      ↓
                                LLM API (Anthropic/OpenAI)

  ---
  二、ContextOS 核心数据模型

  2.1 TranscriptEvent（事件溯源基本单元）

  文件: polaris/kernelone/context/context_os/models.py:168

  @dataclass(frozen=True, slots=True)
  class TranscriptEvent:
      event_id: str                # 唯一事件 ID(UUID)
      sequence: int                # Turn 序列号（递增）
      role: str                    # 角色: user/assistant/tool/system
      kind: str                    # 事件类型: user_turn/assistant_turn/tool_result
      route: str                   # 路由决策: clear/patch/archive/summarize
      content: str                 # 内容文本
      source_turns: tuple[str, ...]  # 来源 turn ID 链
      artifact_id: str | None      # 关联 artifact ID（当 route=archive）
      created_at: str              # ISO8601 时间戳
      metadata: dict[str, Any]     # 元数据（关键字段）

  metadata 关键字段:

  ┌────────────────────┬──────────────────────────────────────┐
  │        字段        │                 说明                 │
  ├────────────────────┼──────────────────────────────────────┤
  │ dialog_act         │ 对话行为类型                         │
  ├────────────────────┼──────────────────────────────────────┤
  │ routing_confidence │ 路由置信度 (0.0-1.0)                 │
  ├────────────────────┼──────────────────────────────────────┤
  │ routing_reasons    │ 路由决策原因列表                     │
  ├────────────────────┼──────────────────────────────────────┤
  │ is_root            │ 是否为 Attention roots（pinned事件） │
  ├────────────────────┼──────────────────────────────────────┤
  │ source_turns       │ 来源 turn 链                         │
  └────────────────────┴──────────────────────────────────────┘

  2.2 ContextOSSnapshot（不可变快照）

  文件: polaris/kernelone/context/context_os/models.py:755

  @dataclass(frozen=True, slots=True)
  class ContextOSSnapshot:
      version: int = 1
      mode: str = "state_first_context_os_v1"
      adapter_id: str = "generic"
      transcript_log: tuple[TranscriptEvent, ...] = ()  # 核心：不可变事件列表
      working_state: WorkingState = field(default_factory=WorkingState)
      artifact_store: tuple[ArtifactRecord, ...] = ()
      episode_store: tuple[EpisodeCard, ...] = ()
      budget_plan: BudgetPlan | None = None
      updated_at: str = ""
      pending_followup: PendingFollowUp | None = None  # Attention Runtime 扩展

  关键设计原则:
  - frozen=True: 整个快照不可变
  - transcript_log 使用 tuple: 事件列表不可修改
  - 符合 Event Sourcing 模式：只能追加，不能删除/修改

  2.3 WorkingState（工作状态聚合）

  @dataclass(frozen=True, slots=True)
  class WorkingState:
      user_profile: UserProfileState      # 用户画像
      task_state: TaskStateView           # 任务状态
      decision_log: tuple[DecisionEntry, ...]  # 决策日志
      active_entities: tuple[StateEntry, ...]  # 活动实体
      active_artifacts: tuple[str, ...]   # 活动 artifact ID
      temporal_facts: tuple[StateEntry, ...]   # 时序事实
      state_history: tuple[StateEntry, ...]    # 状态历史

  ---
  三、StateFirstContextOS 运行时引擎

  文件: polaris/kernelone/context/context_os/runtime.py:128

  3.1 核心方法

  ┌─────────────────────────────┬──────────────────────────────────────────┐
  │            方法             │                   功能                   │
  ├─────────────────────────────┼──────────────────────────────────────────┤
  │ project()                   │ 生成 ContextOSProjection 投影视图        │
  ├─────────────────────────────┼──────────────────────────────────────────┤
  │ _merge_transcript()         │ 合并新旧 transcript_log（基于 event_id） │
  ├─────────────────────────────┼──────────────────────────────────────────┤
  │ _canonicalize_and_offload() │ 规范化事件并处理 offload                 │
  ├─────────────────────────────┼──────────────────────────────────────────┤
  │ _patch_working_state()      │ 从 transcript 提取状态Hints              │
  ├─────────────────────────────┼──────────────────────────────────────────┤
  │ reclassify_event()          │ 重新分类事件路由                         │
  ├─────────────────────────────┼──────────────────────────────────────────┤
  │ reopen_episode()            │ 重新打开已封存的 episode                 │
  ├─────────────────────────────┼──────────────────────────────────────────┤
  │ search_memory()             │ 查询记忆系统                             │
  └─────────────────────────────┴──────────────────────────────────────────┘

  3.2 project() 方法流程

  project(snapshot, events)
      │
      ├─ 1. 合并 transcript
      │   └─ _merge_transcript(snapshot.transcript_log, new_events)
      │       └─ 基于 event_id 去重合并
      │
      ├─ 2. 规范化事件
      │   └─ _canonicalize_and_offload(merged_transcript)
      │       ├─ classify_dialog_act() → DialogActResult
      │       ├─ domain_adapter.classify_event() → RoutingDecision
      │       └─ 处理 pending follow-up 状态
      │
      ├─ 3. 状态提取
      │   └─ _patch_working_state(canonicalized)
      │       ├─ 遍历每个事件
      │       ├─ domain_adapter.extract_state_hints()
      │       └─ _StateAccumulator 聚合 StateEntry
      │
      ├─ 4. 构建 artifact/episode
      │   ├─ _build_artifact_store()
      │   └─ _build_episode_store()
      │
      └─ 5. 返回 ContextOSProjection
          └─ compress(max_active_window_messages=18)

  ---
  四、ContextOSProjection 与压缩机制

  文件: polaris/kernelone/context/context_os/models.py:812

  4.1 投影视图结构

  @dataclass(frozen=True, slots=True)
  class ContextOSProjection:
      snapshot: ContextOSSnapshot              # 原始快照（不变）
      head_anchor: str                        # 前置锚点
      tail_anchor: str                        # 后置锚点
      active_window: tuple[TranscriptEvent, ...]  # 活动窗口（可压缩）
      artifact_stubs: tuple[ArtifactRecord, ...]  # artifact 摘要（可压缩）
      episode_cards: tuple[EpisodeCard, ...]      # episode 卡片（可压缩）
      run_card: RunCard | None                 # 运行卡片
      context_slice_plan: ContextSlicePlan | None  # 上下文切片计划

  4.2 compress() 方法（Phase 6 Safeguard）

  def compress(
      self,
      max_active_window_messages: int = 18,
      max_artifact_stubs: int = 4,
      max_episode_cards: int = 4,
  ) -> ContextOSProjection:
      # Phase 6 Event Sourcing Safeguard:
      # 只压缩 VIEW 层，snapshot.transcript_log 永不修改

      # 保留 is_root 标记的 pinned 事件
      pinned_events = [e for e in self.active_window if e.metadata.get("is_root")]

      # 压缩活动窗口（保留 pinned + 最近N条）
      combined = pinned_events + recent_events[:max_active_window_messages]

      # 返回新 Projection（snapshot 不变）
      return ContextOSProjection(
          snapshot=self.snapshot,  # UNCHANGED
          active_window=combined,
          ...
      )

  ---
  五、Domain Adapter 路由系统

  5.1 适配器接口

  文件: polaris/kernelone/context/context_os/domain_adapters/contracts.py

  class ContextDomainAdapter(Protocol):
      adapter_id: str

      def classify_event() -> DomainRoutingDecision
      #返回: route (clear/patch/archive/summarize), confidence, reasons

      def build_artifact() -> ArtifactRecord | None
      # 从大内容事件构建 artifact

      def extract_state_hints() -> DomainStatePatchHints
      # 提取状态提取Hints（用于 WorkingState）

      def should_seal_episode() -> bool
      # 判断是否封存 episode

  5.2 路由类别

  ┌───────────┬────────────┬────────────────────────────────────┐
  │   Route   │    说明    │              处理方式              │
  ├───────────┼────────────┼────────────────────────────────────┤
  │ clear     │ 清晰事件   │ 直接进入 active_window             │
  ├───────────┼────────────┼────────────────────────────────────┤
  │ patch     │ 补丁事件   │ 更新现有状态                       │
  ├───────────┼────────────┼────────────────────────────────────┤
  │ archive   │ 归档事件   │ 构建 artifact，存入 artifact_store │
  ├───────────┼────────────┼────────────────────────────────────┤
  │ summarize │ 可压缩事件 │ 标记为可压缩                       │
  └───────────┴────────────┴────────────────────────────────────┘

  5.3 DialogAct 分类

  class DialogAct(str):
      AFFIRM = "affirm"      # 确认: 需要/好的/可以
      DENY = "deny"          # 否定: 不/不要/不用
      PAUSE = "pause"        # 暂停: 先别/等一下
      REDIRECT = "redirect"  # 重定向: 改成另外一个
      CLARIFY = "clarify"    # 澄清: 什么意思/再说说
      COMMIT = "commit"      # 承诺: 就这样/确定
      CANCEL = "cancel"      # 取消: 取消/算了
      STATUS_ACK = "status_ack"  # 状态确认: 知道了/收到
      NOISE = "noise"        # 无意义/低信号
      UNKNOWN = "unknown"    # 未分类

  ---
  六、TurnEngine 执行循环

  文件: polaris/cells/roles/kernel/internal/turn_engine/engine.py

  6.1 核心流程

  run(request, role)
      │
      ├─ 1. 初始化
      │   ├─ _request_to_state(request) → ConversationState
      │   ├─ ToolLoopController.from_request() → _controller
      │   └─ _get_policy_layer(profile) → policy
      │
      └─ 2. 主循环 (while True)
          │
          ├─ a. 预算检查
          │   └─ policy.evaluate([], budget_state)
          │       └─ 若 stop_reason → 返回错误
          │
          ├─ b. 构建上下文
          │   └─ context = _controller.build_context_request()
          │       └─ 从 context_os_snapshot.transcript_log 种子 _history
          │
          ├─ c. LLM 调用
          │   └─ kernel._llm_caller.call(context)
          │       └─ 返回 LLMResponse
          │
          ├─ d. 解析响应
          │   └─ _materialize_assistant_turn(raw_output)
          │       ├─ parse_thinking() → thinking + clean_content
          │       └─ sanitize wrapper tags
          │
          ├─ e. 提取工具调用
          │   ├─ _parse_tool_calls_from_turn()
          │   ├─ dedupe_parsed_tool_calls()
          │   └─ split_tool_calls_by_write_budget()
          │
          ├─ f. 若无工具 → 返回最终结果
          │
          └─ g. 执行工具 (增量模式)
              ├─ for call in exec_tool_calls:
              │   ├─ _execute_single_tool() → result
              │   ├─ _controller.append_tool_result(result)
              │   │   # 立即追加到 _history（流式与非流式一致）
              │   └─ policy.evaluate(result)
              │       └─ 若 stop → 返回
              │
              └─ 继续下一轮循环

  6.2 流式 vs 非流式关键差异

  ┌──────────┬─────────────────────────┬─────────────────────┐
  │   特性   │          run()          │    run_stream()     │
  ├──────────┼─────────────────────────┼─────────────────────┤
  │ LLM 调用 │ call()                  │ call_stream()       │
  ├──────────┼─────────────────────────┼─────────────────────┤
  │ 工具执行 │ 执行完所有后追加        │ 每个执行后立即追加  │
  ├──────────┼─────────────────────────┼─────────────────────┤
  │ 返回     │ RoleTurnResult          │ AsyncIterator[dict] │
  ├──────────┼─────────────────────────┼─────────────────────┤
  │ thinking │ merge_stream_thinking() │ visible_delta()     │
  └──────────┴─────────────────────────┴─────────────────────┘

  ---
  七、ToolLoopController SSOT 设计

  文件: polaris/cells/roles/kernel/internal/tool_loop_controller.py

  7.1 P0 SSOT 强制执行

  @dataclass(slots=True)
  class ToolLoopController:
      """Owns transcript state for one assistant turn with tool execution.

      P0 SSOT Enforcement: This controller now requires context_os_snapshot as the
      sole source of history. The legacy request.history fallback has been eliminated.
      """

      request: RoleTurnRequest
      profile: RoleProfile
      safety_policy: ToolLoopSafetyPolicy
      _history: list[ContextEvent] = field(default_factory=list)  # 当前 turn scratchpad

  7.2 post_init 种子逻辑

  def __post_init__(self) -> None:
      # P0 SSOT: 只从 context_os_snapshot 种子历史
      snapshot_history = self._extract_snapshot_history()
      if snapshot_history is self._NO_SNAPSHOT:
          raise ValueError(
              "ToolLoopController requires context_os_snapshot for SSOT compliance."
          )

      # 种子到 scratchpad（当前 turn 的工作区）
      self._history = list(snapshot_history)

  7.3 Safety Policy 参数

  @dataclass(frozen=True, slots=True)
  class ToolLoopSafetyPolicy:
      max_total_tool_calls: int = 64      # KERNELONE_TOOL_LOOP_MAX_TOTAL_CALLS
      max_stall_cycles: int = 2           # KERNELONE_TOOL_LOOP_MAX_STALL_CYCLES
      max_wall_time_seconds: int = 900    # KERNELONE_TOOL_LOOP_MAX_WALL_TIME_SECONDS

  ---
  八、ProviderAdapter 消息转换

  文件: polaris/kernelone/llm/provider_adapters/anthropic_messages_adapter.py

  8.1 transcript → Anthropic messages

  def _build_anthropic_messages_from_transcript(state) -> list[dict]:
      messages = []
      for item in state.transcript:
          if item_type == "UserMessage":
              msg = {"role": "user", "content": [{"type": "text", "text": item.content}]}

          elif item_type == "AssistantMessage":
              blocks = [{"type": "text", "text": item.content}]
              msg = {"role": "assistant", "content": blocks}

          elif item_type == "ToolCall":
              # 合并到前一条 assistant 消息的 tool_calls
              tool_entry = {
                  "id": item.call_id,
                  "type": "function",
                  "function": {"name": item.tool_name, "arguments": json.dumps(item.args)}
              }

          elif item_type == "ToolResult":
              msg = {
                  "role": "user",
                  "content": [{
                      "type": "tool_result",
                      "tool_use_id": item.call_id,
                      "content": item.content
                  }]
              }

      return messages

  8.2 System Prompt 处理差异

  ┌───────────┬────────────────────────────────────────────────┐
  │ Provider  │               System Prompt 处理               │
  ├───────────┼────────────────────────────────────────────────┤
  │ Anthropic │ 独立 config["system"] 字段                     │
  ├───────────┼────────────────────────────────────────────────┤
  │ OpenAI    │ 第一条 {"role": "system", "content": ...} 消息 │
  ├───────────┼────────────────────────────────────────────────┤
  │ Ollama    │ 同 OpenAI                                      │
  └───────────┴────────────────────────────────────────────────┘

  ---
  九、Token 预算管理

  9.1 预算检查流程

  TokenBudgetManager.enforce(input_text, model_spec)
      │
      ├─ 1. 计算可用预算
      │   └─ available = max_context - reserved_output - safety_margin
      │
      ├─ 2. Token 估算
      │   └─ TokenEstimator.estimate(input_text)
      │       ├─ CHARS_PER_TOKEN = 4 (普通文本)
      │       ├─ CJK_CHARS_PER_TOKEN = 2 (中文)
      │       └─ CODE_CHARS_PER_TOKEN = 3 (代码)
      │
      ├─ 3. 检查超限
      │   └─ if requested > available:
      │       └─ 触发压缩策略
      │
      └─ 4. 返回决策
          └─ TokenBudgetDecision(allowed=True/False, compression=...)

  9.2 压缩策略优先级

  1. RoleContextCompressor: 对话内容智能压缩
  2. Code Rules: 删除 import/注释/空行
  3. Line Compaction: 保留头尾，压缩中间
  4. Hard Trim: 硬截断（兜底）

  ---
  十、Event Sourcing 不变性原则

  10.1 Phase 6 Safeguard 核心规则

  ContextOSSnapshot (frozen dataclass)
      └─ transcript_log: tuple[TranscriptEvent, ...]
          └─ 永不修改、压缩、截断

  ContextOSProjection (frozen dataclass)
      └─ snapshot: ContextOSSnapshot (不变)
      └─ active_window: tuple[TranscriptEvent, ...] (可压缩视图)
          └─ compress() 只修改此字段

  10.2 事件溯源保证

  1. 不可变性: transcript_log 是 tuple，不能追加/删除/修改
  2. 追加only: 新事件通过 _merge_transcript() 合并，生成新 tuple
  3. 审计追踪: event_id + sequence + source_turns 提供完整溯源链
  4. 视图分离: active_window 是可压缩视图，不影响原始 transcript

  ---
  十一、完整请求链路示例

  用户输入: "帮我实现登录功能"
      │
      ├─ 1. role_dialogue.generate_role_response(role="pm", message="...")
      │
      ├─ 2. RoleExecutionKernel.run()
      │   └─ 创建 TurnEngine
      │
      ├─ 3. TurnEngine.run()
      │   ├─ ToolLoopController.from_request()
      │   │   └─ 从 context_os_snapshot.transcript_log 种子 _history
      │   │   └─ 若新会话: transcript_log = ()
      │   │
      │   ├─ build_context_request()
      │   │   └─ 返回 ContextRequest
      │   │       ├─ history: self._history
      │   │       └─ pending_user_message: request.message
      │   │
      │   ├─ kernel._llm_caller.call(context_request)
      │   │
      │   └─ 解析响应 → 提取工具调用
      │       ├─ repo_rg("login")
      │       ├─ repo_read_head("auth.py")
      │       └─ precision_edit("auth.py", diff="...")
      │
      ├─ 4. ProviderAdapter.build_request()
      │   ├─ _build_anthropic_messages_from_transcript()
      │   └─ 返回 {"prompt": "...", "config": {"messages": [...], "system": "..."}}
      │
      ├─ 5. BaseProvider.invoke(prompt, model, config)
      │   └─ HTTP POST to Anthropic API
      │
      ├─ 6. ProviderAdapter.decode_response(raw_response)
      │   ├─ 提取 transcript_items
      │   ├─ 提取 tool_calls
      │   └─ 提取 usage
      │
      ├─ 7. 工具执行循环
      │   ├─ _execute_single_tool("repo_rg", {"pattern": "login"})
      │   ├─ append_tool_result(result)
      │   │   └─ self._history.append(ContextEvent(...))
      │   └─ 继续下一轮 LLM 调用（带有新 tool result）
      │
      └─ 8. Turn 结束
          └─ 返回 RoleTurnResult
              ├─ turn_history: [e.to_tuple() for e in _controller._history]
              └─ commit 到 ContextOS（下一 turn 的 transcript_log）

  ---
  十二、关键文件路径汇总

  ┌─────────────────────┬───────────────────────────────────────────────────────────────────────┐
  │        模块         │                               文件路径                                │
  ├─────────────────────┼───────────────────────────────────────────────────────────────────────┤
  │ ContextOS 模型      │ polaris/kernelone/context/context_os/models.py                        │
  ├─────────────────────┼───────────────────────────────────────────────────────────────────────┤
  │ ContextOS 运行时    │ polaris/kernelone/context/context_os/runtime.py                       │
  ├─────────────────────┼───────────────────────────────────────────────────────────────────────┤
  │ Domain Adapter 接口 │ polaris/kernelone/context/context_os/domain_adapters/contracts.py     │
  ├─────────────────────┼───────────────────────────────────────────────────────────────────────┤
  │ TurnEngine          │ polaris/cells/roles/kernel/internal/turn_engine/engine.py             │
  ├─────────────────────┼───────────────────────────────────────────────────────────────────────┤
  │ ToolLoopController  │ polaris/cells/roles/kernel/internal/tool_loop_controller.py           │
  ├─────────────────────┼───────────────────────────────────────────────────────────────────────┤
  │ PolicyLayer         │ polaris/cells/roles/kernel/internal/policy/layer.py                   │
  ├─────────────────────┼───────────────────────────────────────────────────────────────────────┤
  │ Anthropic Adapter   │ polaris/kernelone/llm/provider_adapters/anthropic_messages_adapter.py │
  ├─────────────────────┼───────────────────────────────────────────────────────────────────────┤
  │ TokenEstimator      │ polaris/kernelone/llm/engine/token_estimator.py                       │
  ├─────────────────────┼───────────────────────────────────────────────────────────────────────┤
  │ ModelCatalog        │ polaris/kernelone/llm/engine/model_catalog.py                         │
  └─────────────────────┴───────────────────────────────────────────────────────────────────────┘

  ---
  结论

  ContextOS 是一个精心设计的状态优先上下文引擎，其核心价值在于：

  1. Event Sourcing 模式: transcript_log 作为不可变事件流，提供完整审计追踪
  2. SSOT 强制: ToolLoopController 必须从 context_os_snapshot 种子，杜绝状态碎片化
  3. 视图分离: active_window 可压缩，但 snapshot.transcript_log 永不修改
  4. Domain Adapter: 可扩展的路由分类系统，支持不同业务领域的上下文管理
  5. TurnEngine 统一循环: 所有角色入口共用同一执行引擎，保证行为一致性

   ---
  十三、RunCard 运行卡片详解

  13.1 RunCard 数据结构

  文件: polaris/kernelone/context/context_os/models.py:350

  @dataclass(frozen=True, slots=True)
  class RunCard:
      """Run Card v2 - Extended with attention runtime fields.

      This extends the original RunCard with explicit attention semantics:
      - latest_user_intent: The most recent explicit user intent (may be short)
      - pending_followup_action: The action the assistant is awaiting confirmation for
      - pending_followup_status: Resolution status of pending follow-up
      - last_turn_outcome: The outcome of the last user turn (affirm/deny/redirect/etc.)
      """

      # === 基础字段（原 RunCard）===
      current_goal: str = ""                    # 当前目标
      hard_constraints: tuple[str, ...] = ()   # 硬约束（不可违反）
      open_loops: tuple[str, ...] = ()         # 开放循环（待解决事项）
      active_entities: tuple[str, ...] = ()    # 活动实体
      active_artifacts: tuple[str, ...] = ()   # 活动 artifact ID
      recent_decisions: tuple[str, ...] = ()   # 最近决策
      next_action_hint: str = ""               # 下一步行动提示

      # === Run Card v2: Attention Runtime 字段 ===
      latest_user_intent: str = ""             # 最近用户意图
      pending_followup_action: str = ""        # 等待确认的 follow-up 动作
      pending_followup_status: str = ""        # follow-up 状态
      last_turn_outcome: str = ""              # 上轮对话结果

  13.2 字段详解

  ┌─────────────────────────┬─────────────────┬───────────────────────┬──────────────────────────────────────────────────────────┐
  │          字段           │      类型       │         说明          │                           来源                           │
  ├─────────────────────────┼─────────────────┼───────────────────────┼──────────────────────────────────────────────────────────┤
  │ current_goal            │ str             │ 当前任务目标          │ working_state.task_state.current_goal                    │
  ├─────────────────────────┼─────────────────┼───────────────────────┼──────────────────────────────────────────────────────────┤
  │ hard_constraints        │ tuple[str, ...] │ 硬约束列表（最多6条） │ 从 user_profile.preferences + task_state.blocked_on 提取 │
  ├─────────────────────────┼─────────────────┼───────────────────────┼──────────────────────────────────────────────────────────┤
  │ open_loops              │ tuple[str, ...] │ 开放循环（最多6条）   │ working_state.task_state.open_loops                      │
  ├─────────────────────────┼─────────────────┼───────────────────────┼──────────────────────────────────────────────────────────┤
  │ active_entities         │ tuple[str, ...] │ 活动实体（最多8条）   │ working_state.active_entities                            │
  ├─────────────────────────┼─────────────────┼───────────────────────┼──────────────────────────────────────────────────────────┤
  │ active_artifacts        │ tuple[str, ...] │ 活动artifact ID       │ working_state.active_artifacts                           │
  ├─────────────────────────┼─────────────────┼───────────────────────┼──────────────────────────────────────────────────────────┤
  │ recent_decisions        │ tuple[str, ...] │ 最近决策（最多6条）   │ working_state.decision_log                               │
  ├─────────────────────────┼─────────────────┼───────────────────────┼──────────────────────────────────────────────────────────┤
  │ next_action_hint        │ str             │ 下一步提示            │ 最后一条 open_loop 或首个 deliverable                    │
  ├─────────────────────────┼─────────────────┼───────────────────────┼──────────────────────────────────────────────────────────┤
  │ latest_user_intent      │ str             │ 最近用户意图内容      │ 最后一条 role=user 的 TranscriptEvent.content            │
  ├─────────────────────────┼─────────────────┼───────────────────────┼──────────────────────────────────────────────────────────┤
  │ pending_followup_action │ str             │ 等待确认的动作        │ PendingFollowUp.action                                   │
  ├─────────────────────────┼─────────────────┼───────────────────────┼──────────────────────────────────────────────────────────┤
  │ pending_followup_status │ str             │ follow-up 状态        │ PendingFollowUp.status                                   │
  ├─────────────────────────┼─────────────────┼───────────────────────┼──────────────────────────────────────────────────────────┤
  │ last_turn_outcome       │ str             │ 上轮对话行为          │ 最后一条 user event 的 metadata.dialog_act               │
  └─────────────────────────┴─────────────────┴───────────────────────┴──────────────────────────────────────────────────────────┘

  13.3 _build_run_card() 构建逻辑

  文件: polaris/kernelone/context/context_os/runtime.py:1202

  def _build_run_card(
      self,
      *,
      working_state: WorkingState,
      transcript: tuple[TranscriptEvent, ...] | None = None,
      pending_followup: PendingFollowUp | None = None,
  ) -> RunCard:
      # 1. 从 working_state 提取基础字段
      current_goal = working_state.task_state.current_goal.value
      open_loops = tuple(item.value for item in working_state.task_state.open_loops[-6:])
      recent_decisions = tuple(item.value for item in working_state.decision_log[-6:])

      # 2. 确定 next_action_hint
      if open_loops:
          next_action_hint = open_loops[-1]
      elif working_state.task_state.deliverables:
          next_action_hint = working_state.task_state.deliverables[0].value

      # 3. 提取 latest_user_intent 和 last_turn_outcome
      latest_user_intent = ""
      last_turn_outcome = ""
      for event in reversed(sorted(transcript)):
          if event.role == "user":
              latest_user_intent = event.content
              last_turn_outcome = event.metadata.get("dialog_act", "unknown")
              break

      # 4. 处理 pending_followup 可见性
      # 已resolved 的 follow-up 只在 resolving turn 可见
      visible_followup = pending_followup
      if visible_followup and visible_followup.status != "pending":
          # 检查是否是当前 turn刚 resolve
          if not latest_user_event.metadata.get("followup_action"):
              visible_followup = None  # 隐藏已过期的 follow-up

      return RunCard(
          current_goal=current_goal,
          hard_constraints=_extract_hard_constraints(working_state),
          open_loops=open_loops,
          latest_user_intent=latest_user_intent,
          pending_followup_action=visible_followup.action if visible_followup else "",
          pending_followup_status=visible_followup.status if visible_followup else "",
          last_turn_outcome=last_turn_outcome,
          ...
      )

  13.4 RunCard 在Projection 中的位置

  @dataclass(frozen=True, slots=True)
  class ContextOSProjection:
      snapshot: ContextOSSnapshot
      head_anchor: str
      tail_anchor: str
      active_window: tuple[TranscriptEvent, ...]
      artifact_stubs: tuple[ArtifactRecord, ...]
      episode_cards: tuple[EpisodeCard, ...]
      run_card: RunCard | None          # ← 运行卡片
      context_slice_plan: ContextSlicePlan | None

  ---
  十四、PendingFollowUp 状态管理

  14.1 PendingFollowUp 数据结构

  文件: polaris/kernelone/context/context_os/models.py:125

  @dataclass(frozen=True, slots=True)
  class PendingFollowUp:
      """Represents a pending follow-up action from assistant that awaits user resolution.

      Example: Assistant asks "需要我帮你实现吗？" and waits for user response.
      """

      action: str = ""                 # Follow-up 动作内容
      source_event_id: str = ""        # 来源 assistant event ID
      source_sequence: int = 0         # 来源 turn 序列号
      status: str = "pending"          # pending|confirmed|denied|paused|redirected|expired
      updated_at: str = ""             # 状态更新时间

      def is_resolved(self) -> bool:
          return self.status in {"confirmed", "denied", "paused", "redirected", "expired"}

      def is_blocking(self) -> bool:
          """Pending follow-up blocks episode sealing until resolved."""
          return self.status == "pending"

  14.2 状态流转图

                      ┌──────────────┐
                      │   pending    │
                      │  (初始状态)   │
                      └──────────────┘
                             │
            ┌────────────────┼────────────────┐
            │                │                │
            ▼                ▼                ▼
      ┌──────────┐    ┌──────────┐    ┌──────────┐
      │confirmed │    │  denied  │    │  paused  │
      │ (确认)   │    │  (否定)   │    │  (暂停)   │
      └──────────┘    └──────────┘    └──────────┘
            │                │                │
            │                │                │
            ▼                ▼                ▼
      ┌──────────┐    ┌──────────┐    ┌──────────┐
      │ continue │    │  abort   │    │  defer   │
      │  执行    │    │  取消    │    │  等待    │
      └──────────┘    └──────────┘    └──────────┘

            ┌────────────────┴────────────────┐
            ▼                                  ▼
      ┌──────────┐                      ┌──────────┐
      │redirected│                      │ expired  │
      │(重定向)  │                      │(超时过期)│
      └──────────┘                      └──────────┘

  14.3 Follow-up 提取模式

  文件: polaris/kernelone/context/context_os/patterns.py

  # Assistant follow-up 模式匹配
  _ASSISTANT_FOLLOWUP_PATTERNS = [
      re.compile(r"(?:需要|要不要|是否|可以|帮你|为您)[^?]*?(?<action>[^?]+)\?"),
      # 匹配: "需要我帮你实现吗？" → action = "实现"
  ]

  def _extract_assistant_followup_action(text: str) -> str:
      """从 assistant 消息提取 follow-up 动作。"""
      for pattern in _ASSISTANT_FOLLOWUP_PATTERNS:
          match = pattern.search(content)
          if match:
              action = match.group("action")
              return _trim_text(action, max_chars=220)
      return ""

  ---
  十五、DialogAct 对话行为分类

  15.1 DialogAct 类型表

  ┌────────────┬──────────┬──────────────────────────┬────────┐
  │    类型    │ 中文含义 │        触发词示例        │ 优先级 │
  ├────────────┼──────────┼──────────────────────────┼────────┤
  │ AFFIRM     │ 确认     │ 需要、好的、可以、是、行 │ 高     │
  ├────────────┼──────────┼──────────────────────────┼────────┤
  │ DENY       │ 否定     │ 不、不要、不用、不行     │ 高     │
  ├────────────┼──────────┼──────────────────────────┼────────┤
  │ PAUSE      │ 暂停     │ 先别、等一下、暂停       │ 高     │
  ├────────────┼──────────┼──────────────────────────┼────────┤
  │ REDIRECT   │ 重定向   │ 改成另外一个、换成       │ 高     │
  ├────────────┼──────────┼──────────────────────────┼────────┤
  │ CLARIFY    │ 澄清     │ 什么意思、再说说、详细说 │ 高     │
  ├────────────┼──────────┼──────────────────────────┼────────┤
  │ COMMIT     │ 承诺     │ 就这样、确定、就这样吧   │ 高     │
  ├────────────┼──────────┼──────────────────────────┼────────┤
  │ CANCEL     │ 取消     │ 取消、算了、不要了       │ 高     │
  ├────────────┼──────────┼──────────────────────────┼────────┤
  │ STATUS_ACK │ 状态确认 │ 知道了、好的收到、明白   │ 中     │
  ├────────────┼──────────┼──────────────────────────┼────────┤
  │ NOISE      │ 无意义   │ 嗯、哦、啊               │ 低     │
  ├────────────┼──────────┼──────────────────────────┼────────┤
  │ UNKNOWN    │ 未分类   │ 其他                     │ 低     │
  └────────────┴──────────┴──────────────────────────┴────────┘

  15.2 DialogActClassifier 实现

  class DialogActClassifier:
      # 触发词模式
      AFFIRM_TRIGGERS = ("需要", "好的", "可以", "是", "行", "没问题", "ok")
      DENY_TRIGGERS = ("不", "不要", "不用", "不行", "no", "不需要")
      PAUSE_TRIGGERS = ("先别", "等一下", "暂停", "wait")
      REDIRECT_TRIGGERS = ("改成", "换成", "变成", "用另一个")
      CLARIFY_TRIGGERS = ("什么意思", "再说说", "详细说", "解释下")
      COMMIT_TRIGGERS = ("就这样", "确定", "就这样吧", "好的就这样")
      CANCEL_TRIGGERS = ("取消", "算了", "不要了", "abort")

      def classify(self, text: str) -> DialogActResult:
          content = _normalize_text(text)

          # 1. 精确匹配优先
          if any(t in content for t in self.AFFIRM_TRIGGERS):
              return DialogActResult(act=DialogAct.AFFIRM, confidence=0.9)

          # 2. 模糊匹配
          if any(t in content for t in self.DENY_TRIGGERS):
              return DialogActResult(act=DialogAct.DENY, confidence=0.9)

          # 3. 低信号检测
          if len(content) <= 2 and not self.is_high_priority(content):
              return DialogActResult(act=DialogAct.NOISE, confidence=0.5)

          # 4. 默认未知
          return DialogActResult(act=DialogAct.UNKNOWN, confidence=0.0)

  ---
  十六、Seal Guard 封存守卫

  16.1 Episode 封存条件

  def should_seal_episode(
      self,
      transcript: tuple[TranscriptEvent, ...],
      pending_followup: PendingFollowUp | None,
  ) -> bool:
      # A4: Seal Guard - 有 pending follow-up 时禁止封存
      if self.policy.prevent_seal_on_pending:
          if pending_followup and pending_followup.is_blocking():
              return False  # 禁止封存

      # 其他条件检查...
      return True

  16.2 EpisodeCard 结构

  @dataclass(frozen=True, slots=True)
  class EpisodeCard:
      """64/256/1k三层摘要的闭环历史卡片"""

      episode_id: str
      from_sequence: int              # 起始序列号
      to_sequence: int                # 结束序列号
      intent: str                     #意图摘要
      outcome: str                    # 结果摘要
      decisions: tuple[str, ...]      # 决策列表
      facts: tuple[str, ...]          # 事实列表
      artifact_refs: tuple[str, ...]  # artifact引用
      entities: tuple[str, ...]       # 活动实体
      reopen_conditions: tuple[str, ...]  # 重开条件
      digest_64: str                  # 64字符摘要
      digest_256: str                 # 256字符摘要
      digest_1k: str                  # 1k字符摘要
      sealed_at: float                # 封存时间戳
      status: str = "sealed"          # sealed|reopened

  ---
  十七、RunCard 使用场景

  17.1 在TurnEngine 中的注入

  # TurnEngine 构建 context_request 时
  def build_context_request(self) -> ContextRequest:
      return ContextRequest(
          history=self._history,          # 从 snapshot 种子
          pending_user_message=self._pending_user_message,
          run_card=self.request.context_override.get("run_card"),  # ←注入
          hard_constraints=...,open_loops=...,
      )

  17.2 在 CognitiveRuntimeService 中的使用

  文件: polaris/application/cognitive_runtime/service.py

  def get_run_card_for_session(self, session_id: str) -> dict:
      run_card = self.context_memory_service.get_state_for_session(session_id, "run_card")

      current_goal = str(run_card.get("current_goal") or "").strip()
      hard_constraints = [
          str(item) for item in (run_card.get("hard_constraints") or [])
      ]

      return {
          "current_goal": current_goal,
          "hard_constraints": hard_constraints,
          "open_loops": list(run_card.get("open_loops") or []),
          ...
      }

  def update_run_card_from_handoff(self, handoff: HandoffPack) -> dict:
      run_card = dict(handoff.run_card or {})

      # 从 handoff 更新 run_card
      if handoff.current_goal and not run_card.get("current_goal"):
          run_card["current_goal"] = handoff.current_goal
      if handoff.hard_constraints:
          run_card["hard_constraints"] = list(handoff.hard_constraints)

      return run_card

  ---
  十八、完整流程示例（含 RunCard）

  用户输入: "请帮我实现登录功能"
      │
      ├─ 1. ContextOS.project(messages=[...])
      │   │
      │   ├─ _merge_transcript() → transcript_log
      │   │
      │   ├─ _canonicalize_and_offload()
      │   │   ├─ classify_dialog_act("请帮我实现登录功能") → UNKNOWN
      │   │   ├─ domain_adapter.classify_event() → route="clear"
      │   │   └─ 检测 assistant follow-up: 无
      │   │
      │   ├─ _patch_working_state()
      │   │   ├─ 提取 task_state.current_goal = "实现登录功能"
      │   │   └─ 提取 active_entities = ["登录", "功能"]
      │   │
      │   ├─ _build_run_card()
      │   │   ├─ current_goal = "实现登录功能"
      │   │   ├─ latest_user_intent = "请帮我实现登录功能"
      │   │   ├─ last_turn_outcome = "unknown"
      │   │   └─ pending_followup = None
      │   │
      │   └─ 返回 ContextOSProjection
      │       └─ run_card = RunCard(current_goal="实现登录功能", ...)
      │
      ├─ 2. LLM 响应: "需要我帮你实现吗？"
      │   │
      │   ├─ 检测 follow-up pattern
      │   │   └─ action = "实现登录功能"
      │   │
      │   ├─ 创建 PendingFollowUp
      │   │   └─ action="实现登录功能", status="pending"
      │   │
      │   └─ 更新 snapshot.pending_followup
      │
      ├─ 3. 用户响应: "需要"
      │   │
      │   ├─ classify_dialog_act("需要") → AFFIRM (confidence=0.9)
      │   │
      │   ├─ 检测 follow-up resolution
      │   │   └─ pending_followup.status = "confirmed"
      │   │
      │   ├─ _build_run_card()
      │   │   ├─ latest_user_intent = "需要"
      │   │   ├─ last_turn_outcome = "affirm"
      │   │   ├─ pending_followup_action = "实现登录功能" (当前 turn 可见)
      │   │   └─ pending_followup_status = "confirmed"
      │   │
      │   └─ 返回 RunCard
      │       └─ last_turn_outcome = "affirm"  ← 关键：告诉 LLM 用户确认了
      │
      └─ 4. LLM 根据RunCard 继续执行
          └─ 看到 last_turn_outcome="affirm", pending_followup_status="confirmed"
          └─ 开始实际执行登录功能实现

  ---
  十九、RunCard 关键设计原则

  19.1 Attention Runtime语义

  RunCard v2 的核心价值在于提供显式的注意力语义：

  ┌──────────────┬──────────────────────────────────────────────────┐
  │     场景     │             RunCard 告知 LLM 的信息              │
  ├──────────────┼──────────────────────────────────────────────────┤
  │ 用户确认     │ last_turn_outcome="affirm" → 继续执行            │
  ├──────────────┼──────────────────────────────────────────────────┤
  │ 用户否定     │ last_turn_outcome="deny" → 停止/回退             │
  ├──────────────┼──────────────────────────────────────────────────┤
  │ 用户暂停     │ last_turn_outcome="pause" → 暂停当前动作         │
  ├──────────────┼──────────────────────────────────────────────────┤
  │ 用户重定向   │ last_turn_outcome="redirect" → 切换目标          │
  ├──────────────┼──────────────────────────────────────────────────┤
  │ 有待确认事项 │ pending_followup_status="pending" → 等待用户响应 │
  └──────────────┴──────────────────────────────────────────────────┘

  19.2 状态一致性保证

  1. PendingFollowUp 是第一类状态对象
    - 存储在 ContextOSSnapshot.pending_followup
    - 不可变，只能创建新实例替换
  2. 已 resolved 的 follow-up 只在 resolving turn 可见
    - 防止 LLM 看到过期的 follow-up 信息
  3. Seal Guard 阻止在有 pending follow-up 时封存 episode
    - 确保重要对话不会被过早归档

  19.3 RunCard 与其他组件的关系

  TranscriptEvent.metadata["dialog_act"]
          ↓
      DialogActClassifier
          ↓
  PendingFollowUp (第一类状态)
          ↓
      _build_run_card()
          ↓
  RunCard (运行卡片)
          ↓
  ContextOSProjection.run_card
          ↓
  TurnEngine.build_context_request()
          ↓
      LLM System Prompt