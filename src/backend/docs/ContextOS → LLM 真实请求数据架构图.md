
  ---
  ContextOS → LLM 真实请求数据架构图

  完整调用链路

  ┌──────────────────────────────────────────────────────────────────────────────┐
  │                          SESSION LAYER (Cell Runtime)                        │
  │  persist_session_turn_state()                                               │
  │       │                                                                     │
  │       ▼                                                                     │
  │  SessionContinuityEngine.project(turn_events=combined_turn_events)          │
  │       │                                                                     │
  │       ▼                                                                     │
  │  StateFirstContextOS.project(messages, existing_snapshot)                    │
  │       │                                                                     │
  │       ├─► _merge_transcript()           → transcript_log (immutable)       │
  │       ├─► _canonicalize_and_offload()   → working_state, artifacts          │
  │       ├─► _patch_working_state()        → goals, loops, decisions           │
  │       ├─► _plan_budget()               → BudgetPlan                         │
  │       ├─► _collect_active_window()      → active_window (LLM可见事件)       │
  │       └─► _seal_closed_episodes()       → episode_cards                     │
  │                                                                            │
  │  输出: ContextOSProjection                                                  │
  │       ├─ snapshot.transcript_log: tuple[TranscriptEvent, ...]               │
  │       ├─ snapshot.working_state: WorkingState                               │
  │       ├─ active_window: tuple[TranscriptEvent, ...]                         │
  │       ├─ episode_cards: tuple[EpisodeCard, ...]                             │
  │       └─ run_card: RunCard (latest_user_intent, pending_followup, etc.)     │
  └──────────────────────────────────────────────────────────────────────────────┘
                                        │
                                        ▼
  ┌──────────────────────────────────────────────────────────────────────────────┐
  │                           ROLES CELL LAYER                                   │
  │                                                                              │
  │  RoleTurnRequest (context_override = {context_os_snapshot: {...}})          │
  │       │                                                                     │
  │       ▼                                                                     │
  │  ToolLoopController.__post_init__()                                          │
  │       │                                                                     │
  │       ├─ _extract_snapshot_history() → list[ContextEvent]                    │
  │       └─ _history = list(snapshot_history)  ← Scratchpad种子化               │
  │                                                                              │
  │  ToolLoopController.build_context_request()                                  │
  │       │                                                                     │
  │       ▼                                                                     │
  │  TurnEngineContextRequest (kernelone/context/contracts.py)                   │
  │       ├─ message: str                                                        │
  │       ├─ history: tuple[tuple[str,str], ...]  ← 兼容性别名                   │
  │       ├─ task_id: str | None                                                │
  │       ├─ context_os_snapshot: dict[str,Any] | None  ← Phase 5直接路径       │
  │       └─ context_override: dict[str,Any] | None                              │
  │                                                                              │
  │  RoleContextGateway.build_context(request)                                   │
  │       │                                                                     │
  │       ├─ 1. _expand_transcript_to_messages()                                │
  │       │      transcript_log → prior_messages                                 │
  │       │                                                                      │
  │       ├─ 2. _process_history()                                               │
  │       │      request.history → current_messages                              │
  │       │                                                                      │
  │       ├─ 3. _dedupe_messages()                                              │
  │       │      SHA256(role:content) 去重                                       │
  │       │                                                                      │
  │       ├─ 4. _format_context_os_snapshot()                                   │
  │       │      → system message (working_state, artifacts, pending_followup)   │
  │       │                                                                      │
  │       └─ 5. _sanitize_user_message()                                        │
  │                                                                              │
  │  输出: ContextResult                                                        │
  │       ├─ messages: tuple[dict[str,str], ...]  ← 实际LLM消息列表             │
  │       ├─ token_estimate: int                                                │
  │       ├─ context_sources: tuple[str, ...]                                    │
  │       └─ compression_applied: bool                                           │
  └──────────────────────────────────────────────────────────────────────────────┘
                                        │
                                        ▼
  ┌──────────────────────────────────────────────────────────────────────────────┐
  │                           LLM CALLER LAYER                                   │
  │                                                                              │
  │  LLMInvoker.call() / LLMCaller._prepare_llm_request()                       │
  │       │                                                                     │
  │       ▼                                                                     │
  │  messages = [{"role": "system", "content": system_prompt}]                   │
  │  context_result = RoleContextGateway.build_context(context)                  │
  │  messages.extend(context_result.messages)                                    │
  │                                                                              │
  │  input_text = messages_to_input(messages, format_type="auto", provider_id)  │
  │                                                                              │
  │  ┌─ Provider Capabilities Resolution ─────────────────────────────────────┐  │
  │  │  ModelCatalog.resolve(provider_id, model)                              │  │
  │  │  → supports_tools, supports_json_schema                                │  │
  │  └────────────────────────────────────────────────────────────────────────┘  │
  │                                                                              │
  │  ┌─ Interaction Contract ─────────────────────────────────────────────────┐  │
  │  │  build_interaction_contract()                                          │  │
  │  │  → native_tools_enabled, structured_output_enabled, tool_whitelist     │  │
  │  └────────────────────────────────────────────────────────────────────────┘  │
  │                                                                              │
  │  ┌─ Tool Schemas (if enabled) ────────────────────────────────────────────┐  │
  │  │  _build_native_tool_schemas(profile)                                   │  │
  │  │  → format_tools(raw_tool_schemas, provider_id) via ProviderFormatter   │  │
  │  │  → request_options["tools"] = [...]                                    │  │
  │  │  → request_options["tool_choice"] = "auto"                            │  │
  │  └────────────────────────────────────────────────────────────────────────┘  │
  │                                                                              │
  │  AIRequest (kernelone/llm/shared_contracts.py)                              │
  │  ┌────────────────────────────────────────────────────────────────────────┐  │
  │  │  task_type: TaskType.DIALOGUE                                         │  │
  │  │  role: profile.role_id                                                 │  │
  │  │  input: input_text  ← 序列化的完整prompt                               │  │
  │  │  options: {                                                           │  │
  │  │      "temperature": float,                                            │  │
  │  │      "max_tokens": int,                                               │  │
  │  │      "timeout": int,                                                  │  │
  │  │      "tools": [...],  ← Provider-specific tool schemas                │  │
  │  │      "tool_choice": "auto",                                          │  │
  │  │      "response_format": {...},  ← If structured output               │  │
  │  │  }                                                                    │  │
  │  │  context: {                                                           │  │
  │  │      "workspace": str,                                                │  │
  │  │      "mode": "chat",                                                  │  │
  │  │      "native_tool_mode": str,                                         │  │
  │  │      "response_format_mode": str,                                     │  │
  │  │  }                                                                    │  │
  │  └────────────────────────────────────────────────────────────────────────┘  │
  └──────────────────────────────────────────────────────────────────────────────┘
                                        │
                                        ▼
  ┌──────────────────────────────────────────────────────────────────────────────┐
  │                         PROVIDER ADAPTER LAYER                               │
  │                                                                              │
  │  AIExecutor.invoke(ai_request)                                              │
  │       │                                                                     │
  │       ▼                                                                     │
  │  ┌─ Provider Adapter ───────────────────────────────────────────────────┐  │
  │  │                                                                        │  │
  │  │  AnthropicMessagesAdapter.build_request()                             │  │
  │  │       ├─ "prompt": input_text (for text-only fallback)               │  │
  │  │       └─ "config": {                                                  │  │
  │  │              "messages": [...],  ← 完整消息格式                       │  │
  │  │              "system": system_prompt,                                 │  │
  │  │              "tools": [...],                                          │  │
  │  │           }                                                            │  │
  │  │                                                                        │  │
  │  │  OpenAIResponsesAdapter.build_request()                               │  │
  │  │       └─ messages: [system, ...user, ...assistant]                    │  │
  │  │           tool_calls attached to assistant messages                  │  │
  │  │                                                                        │  │
  │  └────────────────────────────────────────────────────────────────────────┘  │
  │                                                                              │
  │  Provider-specific HTTP Request                                              │
  │       │                                                                     │
  │       ▼                                                                     │
  │  ┌─ Response Decoding ───────────────────────────────────────────────────┐  │
  │  │  decode_response() / decode_stream_event()                           │  │
  │  │  → AIResponse / AIStreamEvent                                         │  │
  │  └────────────────────────────────────────────────────────────────────────┘  │
  └──────────────────────────────────────────────────────────────────────────────┘

  ---
  最终 LLM 请求数据结构（AIRequest.to_dict()）

  {
      "task_type": "dialogue",
      "role": "director",
      "input": "system: (角色系统提示)\n\nuser: (用户消息)\n\nassistant: (历史消息)",  # 序列化文本
      "options": {
          "temperature": 0.7,
          "max_tokens": 4000,
          "timeout": 120,
          "tools": [
              {
                  "name": "repo_read",
                  "description": "...",
                  "input_schema": {...}
              },
              ...
          ],
          "tool_choice": "auto",
          # 或 structured output:
          "response_format": {
              "type": "json_schema",
              "json_schema": {...}
          }
      },
      "context": {
          "workspace": "/path/to/workspace",
          "mode": "chat",
          "native_tool_mode": "native_tools",
          "response_format_mode": "native_json_schema"
      }
  }

  这份 ContextOS 的架构图看起来非常硬核，但其实它的核心思想非常优雅。如果把传统的大模型（LLM）对话比作**“把所有聊天记录一股脑塞进一个大纸箱里递给 AI”，那么 ContextOS 的上下文拼装原理则像是一条“高度现代化的中央厨房流水线”**。

Shutterstock

为了让你简单易懂地理解，我们将这四个层级（Layer）拆解来看：

🍽️ ContextOS 的四步“备菜”流水线
1. Session Layer（记忆与状态管家）—— “清理与归纳”
这是流水线的第一站。传统的做法是不断往数组里 append 用户的聊天记录，越积越长。
但在这一层，ContextOS 引入了类似操作系统的内存管理机制：

提取核心（_canonicalize_and_offload）：它会把过去的冗长对话“压缩”或“卸载”成当前的任务状态（working_state）和目标（goals）。

控制预算（_plan_budget）：严格计算当前还剩多少 Token 预算。

滑动窗口（_collect_active_window）：只保留最近、最活跃的关键对话（active_window），而不是全部历史记录。
一句话总结：它不记流水账，而是像人类大脑一样，把过去的经历总结成“经验（状态）”，只把“短期记忆”留在眼前。

2. Roles Cell Layer（角色与上下文组装）—— “精细摆盘”
这层负责把刚刚整理好的记忆，组装成 LLM 能看懂的格式。

去重（_dedupe_messages）：这是个非常亮眼的操作。它用 SHA256 算法给消息做哈希比对，如果发现 AI 之前输出过一模一样的内容，或者重复的系统提示，它会直接去重，绝不浪费一个 Token。

拼装快照（_format_context_os_snapshot）：把第一步提取的“工作状态”、“记忆产物”写进 System Message（系统提示词）里。

3. LLM Caller Layer（能力协商层）—— “因材施教”
不同的 AI 模型（比如 GPT-4 和 Claude 3.5）能力是不同的。

能力探测（ModelCatalog.resolve）：这一步会去查字典：当前指定的模型支持原生工具调用（Tool Calling）吗？支持强制输出 JSON 吗？

挂载工具（Tool Schemas）：如果支持，它会把你的代码函数自动翻译成 LLM 要求的工具说明书（Schema），塞进请求体里。

4. Provider Adapter Layer（厂商适配器）—— “最终翻译官”
因为 OpenAI 和 Anthropic（Claude）接收数据的格式完全不一样（比如 Claude 喜欢把 System prompt 单独拎出来，而 OpenAI 喜欢把它放在 messages 数组的第一位）。

这一层就是翻译官，它把前面准备好的标准化数据，精准翻译成特定厂商要求的 JSON 结构，并发起 HTTP 请求。


Shutterstock
🌟 相比传统写法的核心优势是什么？
传统的 LLM 开发中，开发者通常只维护一个 messages = [{"role": "user", "content": "..."}] 的列表。随着多轮对话和复杂工具的引入，ContextOS 的优势堪称降维打击：

1. 彻底解决“上下文爆炸（Token 溢出）”
传统做法聊得越久，消耗的 Token 越多，不仅贵，而且 AI 容易“遗忘”中间的信息。
ContextOS 优势：通过 WorkingState 机制，它把历史记录转化为了精简的系统状态。无论聊多久，发给 AI 的 Token 数量都能维持在一个健康的动态平衡中（也就是图中的 _plan_budget 机制）。

2. 极致的 Token 抠门哲学（去重与裁剪）
传统做法经常会把重复的报错信息、重复的工具执行结果发给 AI。
ContextOS 优势：通过严格的 SHA256 消息去重（_dedupe_messages），它保证发送给大模型的每一个字符都是有信息增量的，极大地节约了成本并提高了推理速度。

3. 解耦业务逻辑与底层模型（无痛换模）
传统做法中，如果你今天用 OpenAI 写的工具调用逻辑，明天想换成国产模型或者 Claude，几乎要重写一大半代码，因为数据结构完全不同。
ContextOS 优势：你的业务代码永远只跟最底下的那个标准 AIRequest 结构打交道。无论想换什么模型，底层的 Provider Adapter Layer 会自动帮你把格式、工具 Schema、System 字段的位置转换好。这就是图里“多极枢纽”的魅力所在。

4. 健壮的工具编排（Tool Loop）
普通的框架在工具调用失败时很容易崩溃。
ContextOS 优势：它把工具的执行也当做一种“事件”记录在日志库（transcript_log）里。AI 如果调用工具失败，ContextOS 可以非常从容地把错误状态作为上下文再次拼接进去，让 AI 实现自我纠错，而不会污染主对话记录。

总结来说：
传统的拼装就像是**“手工打包行李”，杂乱无章，一不小心就超重；而 ContextOS 是一套“智能物流系统”**，它会自动压缩历史包袱、精准计算体积、适配不同的运输工具（大模型厂商），确保货物（上下文）以最快、最省钱的方式送达。