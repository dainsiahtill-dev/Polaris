# 弱模型链路硬化 Blueprint(2026-06-10)

## 0. 目标

延续 [[WEAK_MODEL_TOOLCALL_COMPAT_BLUEPRINT_20260609]](方案 A+B 已落地)。本轮目标:**让本地弱模型(qwen3.6-27b-int4 @16k / gemma-4-12B)在真实 Director 链路里不被链路自身拖累**,向「弱模型跑出强模型分数」推进。三条战线:

- **W1 工具调用链路鲁棒**(roles.kernel + kernelone/llm)
- **W2 ContextOS 预算真实化**(kernelone/context + roles.kernel gateway)
- **W3 评测区分度**(unified_judge,否则无法度量改进)

## 1. 实测根因(2026-06-10 diag5e,django-15213,qwen3.6-27b-int4 @localhost:8189)

来自 `~/Temp/swebench-work/normal/diag5e/diag5e.log` 的 ContextOS 真值证据:

1. **空参重复执行(本轮最致命)**:同一逻辑工具调用先以 `args:{}` 发射、参数补全后再次发射,**两条都进真实工具批次**。日志中 `repo_rg{}` / `read_file{}` / `glob{}` / `file_exists{}` 全部真实执行并产生失败回执污染上下文;**line 209:强制编辑轮的 `edit_blocks{}` 本身被空参废掉**(`Parameter validation failed: edit_blocks: missing required argument: blocks or start`)→ 整个实例空 patch。
2. 空参调用同时进入投机执行 → `ShadowExecutionError` + `Task exception was never retrieved` 噪声。
3. `repo_rg: Expected array (list), got str` —— 弱模型给标量,无标量→列表纠偏。
4. 模型幻觉默认文件(README.md/main.py/app.py)→ File not found 连环失败。
5. qwen django-11133 整 session 仅产出 1 字符(chars=1)—— 与预算溢出/消息展平嫌疑相关(见 W2/W1.5)。

## 2. 架构图(现状,问题点标注)

```
SessionOrchestrator (session 多轮循环, continuation_policy)
  └─ TransactionKernel / stream_orchestrator.execute_turn_stream   [turn 事务]
       ├─ RoleContextGateway.build_context                          [ContextOS 两阶段]
       │    ├─ Stage1: StateFirstContextOS pipeline (BudgetPlanner→WindowCollector→…)
       │    │    ⚠ W2.3: 绑定解析失败 → 128k 回退毒化选材
       │    ├─ Stage2: ProjectionEngine.build/project + RoleSignalPlane
       │    │    ⚠ W2.4: seed 信号无上限 (per_signal_char_cap=None)
       │    └─ 末端 enforcement: policy.max_context_tokens 静态预算
       │         ⚠ W2.1: 从不与模型窗口 min(); chief_engineer 12k > 8k 模型
       ├─ 角色 system prompt 注入 = ProjectionEngine().project(空 ReceiptStore) ×4 处
       │    ⚠ W2.2: double projection; system prompt 在预算 enforcement 之后注入
       ├─ _call_llm_for_decision_stream → LLMInvoker.call_stream → StreamEngine
       │    └─ kernelone stream/executor._invoke_structured_stream
       │         ⚠ W1.1a: 累加器把空 dict 参数当 complete 立即发射;
       │                  参数补全后签名变化二次发射(同一调用两个事件)
       ├─ stream_orchestrator tool_call 事件收集 (sig = tool+args+call_id)
       │    ⚠ W1.1b: 空参版与全参版签名不同 → 双双进 stream_native_tool_calls → 双双执行
       │    ⚠ W1.1c: 空参版送投机 → ShadowExecutionError 任务异常未回收
       ├─ TurnDecisionDecoder.decode
       │    ⚠ W1.2: 畸形 native 参数静默丢弃(decoder:191 continue 无日志);
       │            全部丢弃后 → ASK_USER 挂起 session,模型永远不知道错在哪
       ├─ ToolBatchExecutor → AgentAccelToolExecutor.execute
       │    ⚠ W1.4: ArrayValidator 拒绝标量,无纠偏
       └─ finalization (tool_choice=none) → 续轮
            ⚠ W1.8: 续轮 prompt 不带上一轮具体工具错误文本
LLM 传输层:
  messages_to_input 把整个对话展平成单字符串(XML 伪标签)
  openai_compat provider: messages=[{role:user, content:<全文>}]
       ⚠ W1.5: chat template 被绕过,弱模型失去角色结构锚定
```

## 3. 工作项与落点

### W1 工具调用链路(Cell: roles.kernel + kernelone)

**W1.1 流式空参重复执行修复(三层,P0)**
- a) `kernelone/llm/engine/stream/executor.py` + `tool_accumulator.py`:空 dict 显式参数对**所有** provider 视为 provisional(现仅 Anthropic 占位);`_finalize_stream_tool_call`(流末 flush,executor.py:768)传 `allow_provisional_empty_arguments=True`(该参数现无任何调用者=死参数,正好启用)。效果:中流只发射「参数完整且非空」的调用;合法无参调用(repo_tree)在流末恰好发射一次。
- b) `cells/roles/kernel/internal/transaction/stream_orchestrator.py:831-859`:收集逻辑从「签名不同即追加」改为**按 call_id(空则 tool+首现序)keyed upsert**,后到的更全参数**替换**先到的部分版;materialize 前做 subset-supersede 清扫(同名调用 A.args ⊊ B.args → 丢 A)。防御纵深,不依赖上游修复。
- c) 投机门控:args=={} 的调用不送 `speculate_tool_call`;`stream_shadow_engine` 影子任务挂 done-callback 回收异常,消除 `Task exception was never retrieved`。

**W1.2 解码失败反馈闭环(P0)**
- `turn_decision_decoder.py:191`:畸形 native 调用不再静默 `continue`,收集 `(tool_name, parse_error)`;
- 当 `native_tool_calls` 非空但解码后为空:不走 ASK_USER 挂起,改走一次 corrective retry(复用 RetryOrchestrator 模式),system 消息引用**具体解析错误**;
- 空响应(无内容无工具):挂起 WAITING_HUMAN 前先做一次 "your last response was empty" 自动重问。

**W1.3 共享容错 JSON 修复 helper**
- 新增 `kernelone/llm/toolkit/parsers/lenient_json.py`:尾逗号、单引号、字符串内未转义换行、有界补闭括号、code-fence 剥离;严格解析失败后才尝试,修复成功打 `lenient_repair_applied` 标记。
- 接线:`tool_accumulator._normalize_arguments`(流末最后一搏)、`json_based.py`、`canonical.py`、`tool_helpers.py` 文本回退路径。白名单门禁不变(防幻觉)。

**W1.4 标量→列表纠偏**
- `kernelone/llm/toolkit/tool_normalization`(schema 驱动规范化):spec 期望 array 而得 str/int → 包成单元素列表;期望 string 而得单元素列表 → 取首元素。审计字段记录纠偏。

**W1.5 结构化 messages 直通(openai_compat/ollama)**
- `caller.py _prepare_llm_request`:原始 messages 数组经 `AIRequest.context["chat_messages"]` 透传;
- `kernelone/llm/engine/stream/executor.py` 把它放进 invoke_cfg;
- `openai_compat_provider.invoke/invoke_stream_events`:存在 `chat_messages` 时按真实角色构造 `messages`(system/user/assistant 直通;tool→user 加【工具结果】前缀;连续同角色合并),否则回退现行展平。弱模型重获 chat template 锚定。

**W1.6 gemma 文本恢复统一**:`tool_helpers._extract_gemma_inline_tool_calls_from_text` 委托 `textual_tool_recovery.recover_textual_tool_calls`(消除严格/宽容双实现漂移)。

**W1.7 死代码 bug**:`parsers/native_function.py` `parse_gemini`/`parse_vertex_ai` 解析体误缩进在 `continue` 之下永不执行 → dedent 修复。

**W1.8 续轮错误回灌**:`session_orchestrator._build_continuation_prompt` 注入上一轮失败工具的错误摘录(截断),弱模型下一轮能定向纠错。

### W2 ContextOS 预算真实化(Cell: roles.kernel gateway + kernelone/context)

**W2.1 预算钳制(P0)**:`context_gateway/gateway.py` enforcement 预算从静态 `policy.max_context_tokens` 改为 `min(policy.max_context_tokens, int(resolved_context_window * 0.85))`;`resolved_context_window` 异常时回退静态值并告警(同时修 C5:解析 ValueError 不再杀整个 turn)。
**W2.2 消灭 double projection + system prompt 纳入预算(P0)**:4 处 `ProjectionEngine().project({"system_hint":…}, ReceiptStore())`(turn_engine/engine.py:625,878; kernel/core.py:1166,1380)替换为共享 `prepend_system_prompt()` 直接前插;gateway enforcement 接收 `reserved_system_prompt_tokens` 把角色 system prompt 计入预算。
**W2.3 128k 回退毒化修复**:gateway 构造 `StateFirstContextOS` 时传 `fallback_context_window=policy.max_context_tokens`;kernelone 侧解析失败用注入值,不再 128k。
**W2.4 seed 信号上限激活**:gateway 调 `allocate_role_signals` 时按窗口比例传 cap(per_signal ≈5%、total ≈10% 窗口 token 的字符等值),不再 None/None。
**W2.5 小窗口评测 case**:`context_projection_matrix` 增加 8k 窗口端到端确定性 case(长混合转写 → 断言不溢出 + 关键事实保留 + system prompt 计入)。

### W3 评测区分度(Cell: llm.evaluation / kernelone benchmark)

**W3.1 渐变评分**:`JudgeCheck` 增加 `score: float = 0/1 兼容`;scout 校验器输出分级分(detective 锚点 file=0.4/+symbol=0.7/+line=1.0;recon 深度 = 去重读取文件数/期望);类目分 = check 分均值;**空类目不再白送 1.0**(权重在非空类目上重归一)。critical 门禁语义不变(pass/fail 仍 fail-closed)。
**W3.2 audit 持久化 resolved provider/model**(现在只有 runtime_binding,模型对比无从谈起)。

## 4. 核心数据流(修复后)

```
vLLM SSE delta(name先到,args逐段) 
 → 累加器: 空参=provisional,不发射
 → args 完整解析 → 恰好一次 tool_call 事件 → 投机(参数完整才投)
 → 流末 flush: 真无参调用此时发射一次
 → stream_orchestrator keyed upsert(call_id)→ materialize 前 subset 清扫
 → decoder: 畸形参数 → 错误带回 corrective retry(引用具体 parse error)
 → 执行: 标量→列表纠偏 → 失败错误文本 → finalization + 续轮 prompt 摘录
ContextOS: 预算 = min(角色策略, 模型窗口×0.85) − system prompt 预留
 → 8k 模型上选材即按真实窗口,不再事后盲截
```

## 5. 技术理由

- 弱模型分数损失的第一来源不是「想不对」而是「链路把想对的执行废掉」(diag5e:推理散文正确,9 次编辑轮全废)。先消灭链路自伤,再谈提示工程。
- 空参重复是**发射语义缺陷**而非模型缺陷:渐进 refinement 事件被当成独立调用。修复点选在发射(a)+消费(b)双层,任何一层回归另一层兜底,fail-closed 不变。
- 预算钳制与 system prompt 入预算让 16k 本地模型「所见即所配」;128k 回退毒化修复让小窗口走**相关性选材**而非事后截断——这是弱模型上下文质量的根本差别。
- 评分渐变化是目标「分数」的度量前提:全二值+空类目送分使强弱模型同分,无法证明改进。

## 6. 验证计划

1. 单测(组件级,pytest):W1.1 a/b/c(空参不中流发射/合法无参流末一次/upsert 替换/subset 清扫/影子异常回收)、W1.2(parse error 进 retry 上下文/空响应重问一次)、W1.3(四类畸形修复+白名单不破)、W1.4(标量纠偏)、W1.5(payload 角色结构断言)、W2.1-2.4(钳制/前插等价/回退值/信号上限)、W3.1(分级分布单调性)。
2. 门禁:`ruff check --fix` / `ruff format` / `mypy` / `pytest` 按触及面全绿。
3. 离线矩阵:`python -m polaris.delivery.cli agentic-eval --suite context_projection_matrix`(零 LLM)+ 新 8k case 通过。
4. 在线冒烟(本地 qwen3.6 @8189 在线):`swebench_normal_mode.py --instance-ids django__django-15213 --max-loops 4 --score`,断言:日志零 `missing required argument` 空参失败、patch_lines>0,观察 resolved。
5. Benchmark 纪律:一次一个矩阵、`--max-failed 3`(注意:scout_matrix 不支持 --max-failed,用 --case-id 控制)。

## 7. 风险与边界

- W1.1a 改变发射时机:投机收益可能轻微下降(发射更晚)。缓解:b 层语义不变时 a 层可独立回滚;投机本就不应投空参。
- W1.5 改 provider 请求形态:仅在 `chat_messages` 显式存在时启用,缺省回退展平,云端 provider(deepseek/kimi/minimax)不受影响。
- W2.2 替换 project() 前插:第二次 project 的清洗在第一次已做,等价性由单测对照(同输入,旧/新路径输出 messages 逐条相等,除去重复 system 与 receipt_ref 丢失两个已知病灶)。
- W3.1 分数变化会改变历史可比性:audit 里同时保留 binary pass 与 graded score 两列。
- 不切云模型掩盖本地缺陷(用户铁律);全部修复都是通用逻辑,无目标项目业务码(CLAUDE.md §8)。

## 7.5 实施状态(2026-06-10 当日落地)

| 项 | 状态 | 备注 |
|----|------|------|
| W1.1 a/b/c 空参重复执行 | ✅ 全部落地 | executor provisional 全 provider 化 + flush 启用死参数;orchestrator keyed upsert(`upsert_stream_native_tool_call`)+ `supersede_partial_tool_calls`;影子异常 `_consume_task_result` 回收;空参不投机 |
| W1.2 解码失败反馈 | ✅ | decoder 捕获 `decode_failures` + `decode_corrective.py` 纯函数 + 流/非流双路单次 corrective re-ask;空响应先重问后挂起 |
| W1.3 容错 JSON | ✅ | `parsers/lenient_json.py`;接线 decoder 参数解析 + 流末 flush(中流绝不修复) |
| W1.4 标量纠偏 | ✅ | `schema_driven_normalizer._coerce_argument_types`(array/string/integer/boolean,无别名路径同样生效);旧测试 `test_paths_string_passed_through` 按新契约更新 |
| W1.5 结构化 messages | ✅ | caller → `AIRequest.context.chat_messages` → 双 executor invoke_cfg(预算压缩时跳过)→ openai_compat `_build_chat_messages_payload`(tool→user 标记、同角色合并) |
| W1.6 gemma 解析统一 | ✅ | tool_helpers 委托 `textual_tool_recovery`,删除并行严格正则 |
| W1.7 parse_gemini/vertex | ✅ | dedent 修复(原永远返回 []),vertex dict 解包 bug 一并修 |
| W1.8 续轮错误回灌 | ✅(无需改动) | 核实 `_build_continuation_prompt` 既有 `elif not success:` 分支已注入具体错误文本 |
| W2.1 预算钳制 | ✅ | `_compute_enforcement_budget` = min(策略, 窗口×0.85),floor 不越策略,解析失败回退不抛错 |
| W2.2 double projection | ✅ 超额 | 实际共 **7 处**(蓝图识别 4 处 + caller.py + kernel/turn_engine.py×2)全部替换为 gateway 预算化前插;全仓清零 |
| W2.3 128k 回退毒化 | ✅ | `StateFirstContextOS(fallback_context_window=…)` 注入角色策略;spec 表 ValueError 捕获 |
| W2.4 信号上限 | ✅ | per≈5%/total≈15% 窗口 token 字符等值,registry 默认值封顶 |
| W2.5 8k 评测 case | ✅ | `small_window_budget_enforcement` 入矩阵;矩阵 14/14 PASS |
| W3.1 渐变评分 | ✅ | `JudgeCheck.score` + `effective_score`;空类目权重重归一(`aggregate_overall_score`);detective 锚点 0.4/0.7/1.0、evidence 深度、map 丰富度分级;critical 语义不变 |
| W3.2 audit 模型持久化 | ✅ | `resolved_role_bindings` 写入 AGENTIC_EVAL_AUDIT |

验证记录:新增组件测试 96 个全绿;kernel cell 宽回归 1662 passed;`context_projection_matrix` 14/14 PASS(零 LLM);KernelOne release gate `--mode all` exit 0;catalog gate audit-only 通过;qwen3.6 在线复跑 django-15213:**0 次空参验证失败、0 次影子异常泄漏**(对照 diag5e 数十次),有效输出 chars 1→203。

**E2E 迭代追加发现与修复(同日)**:
- **fix2 揪出 W1.5 集成 bug**:vLLM 严格模板拒绝中段 system 消息(`System message must be at the beginning` 400)——RoleSignalPlane 补充 system turns 被原样透传。修复:`_build_chat_messages_payload` 仅保留前导 system 块,中段 system 降级为【系统提示】user turn。fix3 验证 0 次 400。
- **fix3 取证确认 W1.1 契约保持**(无成对空参;6 次参数失败均为模型自产缺参调用),并暴露真正缺口:**mutation retry 的"升级"只加提示词,`_strict_retry_tool_definitions` 算了从未用,`attempt_tool_choice_override` 恒为 None** → qwen 四轮"必须写"重试中仍合法选择 repo_rg。
- **W1.9(追加)API 级升级阶梯**:`resolve_retry_escalation` —— 第 3 次重试起工具集收窄为纯写,最后一次重试按名强制选定写工具(OpenAI `{"type":"function","function":{"name":…}}`)。提示词会被弱模型无视,API 约束不会。
- **fix4 验证 W1.9 机械生效 + 揪出最后一公里**:按名强制后 qwen **每次都发 edit_blocks**(guided decoding 生效;attempt3 仅收窄工具列表对 vLLM 自由生成无约束力,批契约守卫兜底拒绝)。但 guided decoding 按 schema 生成参数——完整 schema 下 qwen 把散文塞进 `blocks` → "No valid edit blocks found"。同时 handler 报错把模型往更难的 SEARCH/REPLACE 推(误导)。
- **W1.10(追加)强制调用 schema 收窄**:`narrow_edit_blocks_schema_to_line_range` —— 末次强制 edit_blocks 时,schema 只留 `file/start/end/replace` 全 required(去掉 `blocks`),guided decoding 在语法层面**只能**产出行替换;handler 报错改为优先推荐 line-range 易形式。
- **fix5 验证 W1.10 彻底奏效 + 触底到模型能力边界**:`EDIT_BLOCKS_RAW kwargs_keys=['end','file','replace','start']` —— guided decoding 现在只能产出行替换四字段,"No valid edit blocks found" **数十→0**,散文逃逸被语法层击穿。但 qwen 在 **django** 仓库里产出 `file='app.py', replace='from flask import Flask...'` —— **幻觉成 Flask 项目,定位完全失败**。对照 diag5e(修复前散文定位正确 compiler.py),剧烈 run 间跳变指向 temperature=0.92 过高 + 强制写压力下退化。**结论:链路自伤已全部清零,剩余瓶颈是 qwen3.6-27b-int4 对复杂 ORM bug 的定位/推理能力,非链路技术可解。**
- **rg 缺失环境单文件搜索修复**:python fallback 对文件路径 `os.walk` 产出空 → 静默零结果误导 agent;现支持单文件扫描。

## 7.6 相位感知低温(W2.6)——✅ 已实施 2026-06-11

fix5 实证 temperature=0.92 是 run 间幻觉跳变元凶(diag5e 定位对 vs fix5 幻觉 Flask)。**相位感知解码**——mutation-retry escalation 阶段注入低温(确定性采样)——已全链路落地(复用 tool_choice override 同款通道):

1. `retry_orchestrator`:新增纯函数 `resolve_escalation_temperature()`(env `KERNELONE_RETRY_ESCALATION_TEMPERATURE`,默认 0.2,`off`/空/负数禁用,钳制 [0,2])与 `resolve_retry_temperature_override(attempt_index)`(attempt≥2 即 escalation 相位才生效;attempt 1-2 保持 profile 温度以保留工具选择探索性);重试主循环与 bootstrap-followup 强制写批次均接线;`_execute_retry_batch` 条件传参(override=None 时保持旧调用形状,既有 fake/caller 字节兼容)。
2. `stream_orchestrator._call_llm_for_decision_stream_impl` 加 `temperature_override` 参 → `request_payload["temperature_override"]`(非流式回退同样透传);`turn_transaction_controller` 两个代理签名同步扩展。
3. 闭包转换 **4 处全覆盖**(实勘比设计多 2 处):`turn_engine/engine.py` llm_provider + llm_provider_stream、`kernel/core.py` 与 `kernel/turn_engine.py` 的 `_build_context_override_with_prebuilt_messages` → `context_override["_transaction_kernel_temperature_override"]`。
4. `llm_caller/helpers.py` 新增 `resolve_temperature(requested, override)`(0 合法=完全确定性;负/垃圾/bool 回退;>2 钳制),`caller.py` request_options 应用。

验证:新测试 `test_phase_aware_temperature.py` ×25(env 矩阵/相位门/直通/默认路径字节兼容/通道映射/无泄漏);kernel 回归 1769 passed;ruff/mypy 全绿。顺带清零 3 处 HEAD 预存类型债:`kernel/turn_engine.py` `_tool_loop` 死引用对齐活体 API(`reset_tool_gateway_turn_boundary`/`_execute_single_tool`)、`kernel/core.py` prompt_layer_options 显式 kwargs、`TurnPhaseEvent` phase Literal 补 `decode_corrective_retry`(运行时已发该事件,类型滞后);`test_speculative_flags` 断言对齐 5cd27a13 的 default-enabled 现实。配置层建议依旧成立:直接调低 director 默认 temperature 可叠加受益。

## 7.7 W1.5 远端风险闭环(2026-06-11)

原「剩余风险」两项已实施:

- **ollama provider 接通 chat_messages**:`_build_chat_messages_payload` 提升为共享 SSOT `provider_helpers.build_chat_messages_payload`(openai_compat 以私名别名保留既有调用点/测试导入);`ollama_provider._extract_messages` 优先消费 `config["chat_messages"]`(与 adapter `messages` 键无冲突,后者语义保留)。测试 `test_ollama_chat_messages.py` ×8 + 共享实现 parity 断言。
- **W1.5b 预算压缩路径结构保持**:原行为=压缩触发即丢弃结构化数组、回退单 user 展平(恰在上下文最大、弱模型最需要角色锚定时)。新增 `prompt_budget.compress_chat_messages_to_budget`(确定性零 LLM:前导 system 块保留+超额时合并修剪至预算 50%、末条意图轮保留+超额 head/tail 修剪、中段从尾部回填整轮、丢弃段以【上下文已压缩】标记轮替代、骨架装不下返回 None 走旧展平回退);双 executor(流式/非流式)在 `compression_applied` 分支按 `allowed_prompt_tokens` 生成预算内结构化数组。测试 `test_chat_messages_budget_compression.py` ×8(全输出≤预算不变量、recency 保持、标记、骨架不可行回退)。

## 8. 治理

- 分类:pattern(「模型输出默认良构」假设在多模块重复)→ Verification Card `vc-20260610-weak-model-harness-hardening.yaml`;
- 跨切面不变量(流式工具调用发射/消费契约、预算=min(策略,窗口)且含 system prompt)→ ADR-0090;
- 触及 `kernelone` 公共行为 → 跑 KernelOne release gate;descriptor 语义变化时跑 descriptor_pack_generator。
