# 🎯 Polaris 技术债审计报告

**审计日期**: 2026-04-01
**审计委员会**: 10人专家团队（LLM Systems Engineer / Tooling & Agent Engineer / ContextOS Specialist / Turn Engine Designer / Observability & Audit Engineer / Event-Driven Architect / Role/Agent System Designer / Performance & Scalability Engineer / Python Code Quality Guardian / Chief Architect）
**审计范围**: `polaris/` 核心系统（~1642 Python 文件）
**综合评分**: ⚠️ **5.2/10** （存在严重架构缺陷）

---

## 一、架构审计（Blueprint）

### 1.1 当前系统架构

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Client (Electron/CLI)                        │
└───────────────────────────────┬─────────────────────────────────────┘
                                │
                                ▼
┌───────────────────────────────────────────────────────────────────┐
│                    Delivery Layer (polaris/delivery/)              │
│              HTTP / WebSocket / CLI 传输适配层                       │
│                   ⚠️ 部分模块直接导入 domain.entities                 │
└───────────────────────────────┬───────────────────────────────────┘
                                │
                                ▼
┌───────────────────────────────────────────────────────────────────┐
│                   Application Layer (polaris/application/)          │
│                        用例编排、事务边界                            │
│                         仅 3 个 Python 文件                          │
└───────────────────────────────┬───────────────────────────────────┘
                                │
              ┌─────────────────┴─────────────────┐
              ▼                                   ▼
┌───────────────────────────┐      ┌─────────────────────────────────┐
│   Domain Layer            │      │   KernelOne (polaris/kernelone/) │
│   (polaris/domain/)       │      │   Agent/AI 类 Linux 运行时底座   │
│   业务规则、实体、策略     │      │   ⚠️ 仍含 Polaris 业务语义    │
│   40 个 Python 文件        │      │   442 个 Python 文件              │
└───────────────────────────┘      └─────────────────────────────────┘
                                │                    │
                                │                    │
                                ▼                    ▼
                    ┌───────────────────┐   ┌────────────────────────┐
                    │  Cells (~50+)     │   │ Infrastructure         │
                    │  polaris/cells/  │   │ (polaris/infrastructure/)
                    │  809 个 Python   │   │ 144 个 Python 文件       │
                    └───────────────────┘   └────────────────────────┘
```

### 1.2 核心数据流

```
LLM Input ──▶ Tool Selection ──▶ Context Assembly ──▶ Turn Processing
    │              │                   │                   │
    ▼              ▼                   ▼                   ▼
KernelOne    Effect Chain          Cell Catalog         State Update
LLM Runtime  (audit/trace)       (graph-first)       (single-writer)
```

### 1.3 模块依赖关系热力图

| 区域 | 风险等级 | 核心问题 |
|------|----------|----------|
| `kernelone/storage/layout.py` | 🔴 CRITICAL | 含 Polaris 业务语义（`tasks`, `docs`, `dispatch` 别名） |
| `polaris/cells/roles/kernel/internal/` | 🔴 CRITICAL | 双 Turn Engine 并存（`turn_engine/engine.py` vs `turn_transaction_controller.py`） |
| `polaris/kernelone/llm/` | 🔴 CRITICAL | fallback_model 参数被忽略、无 Provider 故障转移、Provider 实例无限期缓存 |
| `polaris/cells/audit/evidence/` | 🔴 CRITICAL | Git 操作失败静默返回空列表、审计事件签名字段永未填充 |
| `polaris/kernelone/events/` | 🔴 CRITICAL | 异步处理器 fire-and-forget、取消竞态条件、handler 异常被吞没 |
| `polaris/delivery/` 部分模块 | 🟠 HIGH | 直接导入 `domain.entities` 绕过 application 层 |
| `polaris/kernelone/context/` | 🟠 HIGH | 去重逻辑破坏合法重复调用、Token 估算不一致、向量存储失败静默 |
| `polaris/kernelone/tools/` | 🟠 HIGH | 双工具定义源、缓存键语义不匹配、写操作无幂等声明 |
| `polaris/kernelone/workflow/` | 🟡 MEDIUM | DAG 死锁可见性差、异常处理吞掉 CancelledError |
| `polaris/cells/roles/profile/` | 🟡 MEDIUM | 全局单例 Registry 跨请求污染、角色回退逻辑硬编码 |

### 1.4 技术债分布

```
          严重性分布

  BLOCKER  ████████████████████████████████████  35 项
  SUGGEST  ████████████████████████████████     32 项
  NITPICK  ██████████████████████████           25 项

  总计: 92 项问题
```

---

## 二、多专家审计结果

### 👤 LLM Systems Engineer

#### 🔴 BLOCKER

**[BLOCKER-01] `fallback_model` 参数完全被忽略 — 回退机制形同虚设**
- 📍 `polaris/kernelone/llm/runtime.py:143`
- 🧨 `fallback_model` 传入后被 `_ = fallback_model` 直接丢弃，无任何重试逻辑
- 🧠根因: 函数签名接收参数但从未使用，回退策略从未实现
- 🔧修复: 在主 Provider 失败后，尝试使用 `fallback_model` 重新调用
- ⚠️严重程度: BLOCKER — 单 Provider 瞬时故障导致永久失败

**[BLOCKER-02] 无跨 Provider 故障转移 — LLM 基础设施单点故障**
- 📍 `polaris/kernelone/llm/engine/executor.py:327-445`
- 🧨 `ResilienceManager` 仅在同一 Provider 内重试，无跨 Provider 切换能力
- 🧠根因: 弹性层工作在调用层面，Provider 选择在弹性层之前完成一次
- 🔧修复: 引入 `ProviderFailoverManager`，在可重试错误时选择备用 Provider
- ⚠️严重程度: BLOCKER — 整个 LLM 基础设施单点故障

**[BLOCKER-03] Provider 实例永久缓存不失效 — Provider 故障后系统不可用**
- 📍 `polaris/kernelone/llm/providers/registry.py:89-112`
- 🧨 `get_provider_instance()` 返回缓存实例，无论其健康状态
- 🧠根因: 无 TTL、无健康检查、无失败驱逐机制
- 🔧修复: 添加实例缓存 TTL（5分钟）或集成断路器
- ⚠️严重程度: BLOCKER — Provider 临时故障后系统永久不可用

**[BLOCKER-04] Token 预算检查在 Provider 选择之后执行 — 资源浪费**
- 📍 `polaris/kernelone/llm/engine/executor.py:362-388`
- 🧨 `token_budget.enforce()` 在 `model_catalog.resolve()` 之后执行，已浪费 Provider 解析时间
- 🧠根因: 预算检查在"执行调用"阶段，而非请求入口阶段
- 🔧修复: 在 `AIExecutor.invoke()` 的 `resolve_provider_model` 之后立即检查预算
- ⚠️严重程度: BLOCKER — 超出预算请求浪费延迟和资源

#### 🟡 SUGGESTION

**[SUGGESTION-01] 基于关键字的错误分类脆弱**
- 📍 `polaris/kernelone/llm/error_categories.py:59-93`
- 问题: 依赖小写字符串匹配，消息本地化或格式变化即失效
- 修复: 优先使用 `isinstance()` 检查异常类型，关键字匹配仅作最后手段

**[SUGGESTION-02] `AIRequest` 缺少输入验证**
- 📍 `polaris/kernelone/llm/shared_contracts.py:107-139`
- 问题: 空 input、超大输入、无效 provider_id 格式均未拒绝
- 修复: 添加 `__post_init__` 验证

**[SUGGESTION-03] `blocked_provider_types` 检查过晚**
- 📍 `polaris/kernelone/llm/runtime.py:220-234`
- 问题: 在加载配置后才检查，应在 Provider 选择时前置

#### ⚪ NITPICK

- 硬编码温度 0.2 不适配所有任务类型
- `asyncio.to_thread` 对同步 Provider 可能无法真正超时
- `Usage.estimate()` 使用 char/4 启发式，非实际 tokenizer

---

### 👤 Tooling & Agent Engineer

#### 🔴 BLOCKER

**[BLOCKER-05] 废弃工具别名（grep/ripgrep/search_code）处理器注册不一致**
- 📍 `polaris/kernelone/llm/toolkit/executor/handlers/search.py:54-59` + `repo.py:23-26`
- 🧨 `ripgrep` 和 `search_code` 在 `search.py` 被注释掉，但在 `repo.py` 注册为别名
- 🧠根因: 从 `definitions.py` 到 `contracts.py` 迁移遗留问题
- 🔧修复: 统一处理器注册，使 `grep` 为规范工具名，其余为废弃别名
- ⚠️严重程度: BLOCKER — 使用废弃别名时返回"Handler not implemented"

**[BLOCKER-06] 双工具定义源造成不一致**
- 📍 `polaris/kernelone/tools/contracts.py` vs `polaris/kernelone/llm/toolkit/definitions.py`
- 🧨 `_TOOL_SPECS`（30+工具）和 `STANDARD_TOOLS`（16工具）并行存在，参数定义不同
- 🧠根因: 历史迁移遗留，两者并存未统一
- 🔧修复: 废弃 `definitions.py` 的 `STANDARD_TOOLS`，以 `ToolSpecRegistry` + `_TOOL_SPECS` 为单一真相
- ⚠️严重程度: BLOCKER — 参数验证不一致、安全风险

**[BLOCKER-07] 缓存键不考虑语义归一化**
- 📍 `polaris/kernelone/tools/executor.py:96-97`
- 🧨 缓存键基于 CLI 参数字符串，但归一化在缓存查找之后
- 🧠根因: 归一化时机错误
- 🔧修复: 在计算缓存键之前归一化参数
- ⚠️严重程度: BLOCKER — 相同语义不同字符串导致错误缓存行为

**[BLOCKER-08] 命令执行安全验证在解析之后执行**
- 📍 `polaris/kernelone/llm/toolkit/executor/handlers/command.py:26-79`
- 🧨 `_contains_shell_operators()` 在 `_sanitize_llm_command_text()` 之后，且 `parse_command()` 在原始文本上
- 🧠根因: 安全验证非第一操作
- 🔧修复: `is_command_allowed()` 应为首要操作
- ⚠️严重程度: BLOCKER — 潜在命令注入风险

**[BLOCKER-09] 写工具可被重试无幂等声明**
- 📍 `polaris/kernelone/tools/executor.py:111-120` + `chain.py:32-34`
- 🧨 `run_tool_chain()` 对所有工具应用重试，包括 `write_file` 等写操作
- 🧠根因: 无幂等性或副作用声明，基于 `on_error` 策略决定重试
- 🔧修复: 添加 `idempotent: bool` 字段，写工具设 `false` 跳过重试
- ⚠️严重程度: BLOCKER — 重试导致重复写入和数据损坏

#### 🟡 SUGGESTION

**[SUGGESTION-04] 路径遍历检测不完整**
- 📍 `polaris/kernelone/llm/toolkit/tool_normalization/__init__.py:131-141`
- 缺少: `%2E%2E%2F`（大写）、`\.\./`（转义变体）、Unicode 等价物

**[SUGGESTION-05] 三个归一化系统并存**
- 📍 `contracts.normalize_tool_args()` / `tool_normalization.normalize_tool_arguments()` / `SchemaDrivenNormalizer`
- 问题: 三者可产生不同结果

**[SUGGESTION-06] `__getattr__` 惰性加载脆弱**
- 📍 `polaris/kernelone/tools/tool_spec_registry.py:364-384`
- 非线程安全，导入顺序可能引发 bug

#### ⚪ NITPICK

- `grep` normalizer 存在但处理器注册不清晰
- 命令别名翻译在两处有不同硬编码映射
- 写操作非原子（无临时文件模式）

---

### 👤 ContextOS Specialist

#### 🔴 BLOCKER

**[BLOCKER-10] 去重逻辑破坏合法重复工具调用**
- 📍 `polaris/kernelone/context/context_os/runtime.py:1053-1061`
- 🧨 `_collect_active_window` 通过归一化内容去重工具消息，相同工具的合法多次调用被静默丢弃
- 🧠根因: 去重只检查内容，不检查工具名+参数
- 🔧修复: 去重键应为 `(tool_name, norm_content)` 元组
- ⚠️严重程度: BLOCKER — 多次搜索同一内容丢失关键信息

**[BLOCKER-11] 压缩与预算阶梯 Token 估算不一致**
- 📍 `polaris/kernelone/context/compaction_strategy.py:267-283` / `history_materialization.py:603-621` / `engine.py:214-223`
- 🧨 三处使用不同估算公式，导致预算决策不一致
- 🧠根因: 无共享 token 估算工具，各模块自行实现
- 🔧修复: 创建单一 `polaris/kernelone/context/utils.py` 并统一导入
- ⚠️严重程度: BLOCKER — 预算触发决策不可预测

**[BLOCKER-12] 向量存储失败静默**
- 📍 `polaris/kernelone/memory/memory_store.py:399-411`
- 🧨 LanceDB 写入失败仅 `logger.warning`，项仍追加到 JSONL 和 `self.memories`，向量索引不完整
- 🧠根因: 错误处理仅记录，不设降级标志、不重试、不通知
- 🔧修复: 添加 `_vector_index_degraded` 标志和 `is_vector_index_healthy()` 方法
- ⚠️严重程度: BLOCKER — 向量检索漏掉失败的项无任何指示

**[BLOCKER-13] `SessionContinuityPack.compacted_through_seq` 重建时未正确更新**
- 📍 `polaris/kernelone/context/session_continuity.py:528-569`
- 🧨 增量包构建时，`compacted_through_seq` 被覆写为 `older_max_seq`，未考虑已有间隙
- 🧠根因: 增量消息选择过滤 `> existing_pack.compacted_through_seq`，但写入的是 `older_max_seq`
- 🔧修复: 使用 `max(existing_pack.compacted_through_seq, older_max_seq)`
- ⚠️严重程度: BLOCKER — 后续摘要可能重复包含早期消息

**[BLOCKER-14] `RoleSessionContextMemoryService._load_snapshot` 错误静默**
- 📍 `polaris/cells/roles/session/internal/context_memory_service.py:47-49`
- 🧨 任何异常返回 `None`，调用方返回空结果无失败指示
- 🔧修复: 记录异常，添加可选 `raise_on_error` 参数

#### 🟡 SUGGESTION

**[SUGGESTION-07] `RoleContextCompressor.micro_compact` 修改输入列表**
- 📍 `polaris/kernelone/context/compaction.py:392-439`
- 直接修改 `messages` 列表中的字典条目，导致原始列表损坏

**[SUGGESTION-08] `Context OSSnapshot` 反序列化不验证 schema 版本**
- 📍 `polaris/kernelone/context/context_os/models.py:768-795`
- 遇到未来版本静默接受，无迁移路径

**[SUGGESTION-09] `TieredAssetCacheManager` 持久缓存写入竞态**
- 📍 `polaris/kernelone/context/cache_manager.py:546-577`
- 元数据读写-修改-写入非原子

**[SUGGESTION-10] `ContextCatalogService._build_descriptor` 即使未使用也生成 embedding**
- 📍 `polaris/cells/context/catalog/service.py:416-419`
- 每个 cell 每次 `sync()` 都调用 `get_embedding`，即使 embedding 未被使用

#### ⚪ NITPICK

- TTL 过期检查使用 `<` 而非 `<=` 有浮点边界问题
- `StateFirstContextOS.resolved_context_window` 缓存永久有效
- `SessionContinuityEngine._extract_open_loops` 仅反转一次

---

### 👤 Turn Engine Designer

#### 🔴 BLOCKER

**[BLOCKER-15] `TurnEngine.run()` 无内在轮次限制 — 可能无限循环**
- 📍 `polaris/cells/roles/kernel/internal/turn_engine/engine.py:601-834`
- 🧨 `while True:` 循环无最大轮次限制，依赖外部 `PolicyLayer.evaluate()` 或空 `exec_tool_calls`
- 🧠根因: `round_index` 递增但从不与最大值比较
- 🔧修复: 添加 `if round_index >= self.config.max_turns` 检查
- ⚠️严重程度: BLOCKER — LLM 或策略层退化时可能无限循环

**[BLOCKER-16] `TurnTransactionController._execute_turn_stream()` 实际非流式**
- 📍 `polaris/cells/roles/kernel/internal/turn_transaction_controller.py:1038-1062`
- 🧨 先完整非流式执行，再返回单个 `CompletionEvent`；客户端收不到增量块
- 🧠根因: 注释说"目前先用非流式"，流式基础设施存在但未接入
- 🔧修复: 使用 `aiter_stream()` 逐块 yield，而非等待完成
- ⚠️严重程度: BLOCKER — 流式完全失效

**[BLOCKER-17] TurnTransactionController 事件处理器异常被吞没**
- 📍 `polaris/cells/roles/kernel/internal/turn_transaction_controller.py:192-198`
- 🧨 `_emit_phase_event()` 捕获所有异常并静默 `continue`
- 🧠根因: 错误抑制不传播、不记录
- 🔧修复: 至少 `logger.warning`，继续调用其他 handler
- ⚠️严重程度: BLOCKER — 调试不可能，系统可能处于不一致状态

#### 🟡 SUGGESTION

**[SUGGESTION-11] `run_stream()` 提前返回不递增 `round_index`**
- 📍 `polaris/cells/roles/kernel/internal/turn_engine/engine.py:1106-1145`
- 错误路径的轮次计数不准确

**[SUGGESTION-12] `ToolLoopController._extract_snapshot_history()` 元数据丢失**
- 📍 `polaris/cells/roles/kernel/internal/tool_loop_controller.py:145-169`
- 非字符串类型的 `source_turns` 被静默丢弃

**[SUGGESTION-13] `WorkflowEngine._run_dag()` 阻塞任务可见性差**
- 📍 `polaris/kernelone/workflow/engine.py:452-530`
- 仅聚合报告，无逐任务 `task_blocked` 事件

**[SUGGESTION-14] `TurnLedger.state_history` 使用字符串而非枚举**
- 📍 `polaris/cells/roles/kernel/internal/turn_transaction_controller.py:90`
- 丢失类型安全

#### ⚪ NITPICK

- `tool_choice="none"` 依赖 Provider 合规，非 Provider 强制

---

### 👤 Observability & Audit Engineer

#### 🔴 BLOCKER

**[BLOCKER-18] 审计事件签名从未填充 — 链完整性受损**
- 📍 `polaris/kernelone/audit/runtime.py:257`
- 🧨 `signature=""` 硬编码，从未计算或存储任何密码学签名
- 🧠根因: 审计事件契约定义了 `signature` 字段，但实现从未计算
- 🔧修复: 实现 HMAC-SHA256 签名
- ⚠️严重程度: BLOCKER — 整个审计链无法用于取证或合规

**[BLOCKER-19] 审计存储工厂未注册 — 直接使用抛 RuntimeError**
- 📍 `polaris/kernelone/audit/registry.py:25-33`
- 🧨 未提供默认 JSONL 实现，必须先显式 DI 接线
- 🔧修复: 注册默认 JSONL 存储

**[BLOCKER-20] Git 不可用时 Evidence Bundle 静默失败**
- 📍 `polaris/cells/audit/evidence/bundle_service.py:221-289`
- 🧨 `git diff` 失败返回空列表，无审计事件、无错误传播
- 🔧修复: 失败时发出 `INTERNAL_AUDIT_FAILURE` 事件
- ⚠️严重程度: BLOCKER — 证据包可创建为不完整/空，无任何指示

**[BLOCKER-21] 事件去重可通过零或负环境变量禁用**
- 📍 `polaris/kernelone/events/io_events.py:36,39`
- 🧨 `max(0.0, ...)` 允许 0.0 完全禁用去重，导致事件风暴
- 🔧修复: 要求最小正值 `max(0.001, ...)`
- ⚠️严重程度: BLOCKER — 配置错误导致无限事件重复

**[BLOCKER-22] 角色会话审计事件失败被吞没**
- 📍 `polaris/cells/audit/evidence/internal/role_session_audit_service.py:64-72`
- 🧨 `_runtime.emit_event()` 异常被裸 `except Exception` 捕获，仅警告日志
- 🔧修复: 实现本地 JSONL 降级

**[BLOCKER-23] 诊断引擎路径遍历检查不完整**
- 📍 `polaris/kernelone/audit/diagnosis.py:752-753`
- 🧨 `workspace not in resolved.parents` 对 symlink 或 `..` 处理不当
- 🔧修复: 使用 `os.path.commonpath()` 更健壮
- ⚠️严重程度: BLOCKER — 潜在路径遍历漏洞

#### 🟡 SUGGESTION

**[SUGGESTION-15] 异步处理器超时太短（最小 0.1s）**
- 📍 `polaris/kernelone/events/message_bus.py:25-32`

**[SUGGESTION-16] 事件架构中废弃 Polaris 角色仍在使用**
- 📍 `polaris/kernelone/events/schema.py:8`

**[SUGGESTION-17] 死信队列元数据丢失**
- 📍 `polaris/kernelone/events/message_bus.py:404-412`
- 仅存元数据，丢失的实际消息内容无法调试

#### ⚪ NITPICK

- `dropped_messages` vs `dropped_messages_count` 命名不一致
- 敏感数据过滤正则不完整
- 重复 `FailureClass` 枚举

---

### 👤 Event-Driven Architect

#### 🔴 BLOCKER

**[BLOCKER-24] 异步处理器 fire-and-forget 无错误传播**
- 📍 `polaris/kernelone/events/message_bus.py:419-449`
- 🧨 `asyncio.ensure_future()` 创建的任务存储在 `pending_async_handlers` 但仅通过 `asyncio.wait_for()` 等待一次
- 🧠根因: 发布者无法知道处理器是否失败
- 🔧修复: `publish()` 返回 `tuple[int, int, int]` (成功/失败/超时)
- ⚠️严重程度: BLOCKER — 事件可静默处理失败

**[BLOCKER-25] 处理器超时取消与任务完成竞态**
- 📍 `polaris/kernelone/events/message_bus.py:428-441`
- 🧨 `t.cancel()` 与任务完成之间存在竞态，任务可能被误标记为取消
- 🔧修复: 使用 `asyncio.shield()` 或在取消前检查 `t.done()`
- ⚠️严重程度: BLOCKER — 超时处理可能损坏处理器结果

**[BLOCKER-26] EventRegistry 在 CancelledError 时修改通配符列表**
- 📍 `polaris/kernelone/events/typed/registry.py:458-473`
- 🧨 迭代 `_wildcard_subscriptions` 时若被修改，可能 `RuntimeError: dictionary changed size during iteration`
- 🔧修复: 订阅列表修改原子化、迭代安全化
- ⚠️严重程度: BLOCKER — 并发修改导致 RuntimeError

**[BLOCKER-27] RuntimeEventFanout 处理器同步但无 awaitable 跟踪**
- 📍 `polaris/infrastructure/realtime/process_local/message_event_fanout.py:331-405`
- 🧨 同步处理器捕获所有异常仅 `debug` 级别日志，写入失败静默
- 🔧修复: 处理器返回 awaitable，至少 `WARNING` 级别
- ⚠️严重程度: BLOCKER — 文件事件扇出可静默失败

#### 🟡 SUGGESTION

**[SUGGESTION-18] atexit 处理器在许多环境失败**
- 📍 `polaris/infrastructure/realtime/process_local/message_event_fanout.py:542-562`
- 创建新事件循环在 uvicorn/Jupyter 中失败

**[SUGGESTION-19] MessageBus actor 队列无界默认 maxsize=100**
- 📍 `polaris/kernelone/events/message_bus.py:504-507`
- 无背压信号

**[SUGGESTION-20] `TypedEventBusAdapter` 裸 except 捕获 CancelledError**
- 📍 `polaris/kernelone/events/typed/bus_adapter.py:283-289`

**[SUGGESTION-21] 通配符订阅模式匹配 O(n)**
- 📍 `polaris/kernelone/events/typed/registry.py:380-386`

**[SUGGESTION-22] RealtimeSignalHub 线程通知静默丢弃**
- 📍 `polaris/infrastructure/realtime/process_local/signal_hub.py:351-375`

#### ⚪ NITPICK

- `UEPEventPublisher._adapter` 惰性加载非线程安全
- `_publish_llm_event_to_realtime_bridge` except 清晰度问题
- `file_event_broadcaster.broadcast_file_written` 无投递保证

---

### 👤 Role/Agent System Designer

#### 🔴 BLOCKER

**[BLOCKER-28] 全局单例 `RoleProfileRegistry` 跨所有角色执行共享**
- 📍 `polaris/cells/roles/profile/internal/registry.py:333`
- 🧨 模块级全局单例在多租户或并发执行场景造成跨请求状态污染
- 🧠根因: Registry 设计为进程级单例，但某些路径创建隔离实例
- 🔧修复: 要求显式 `registry` 参数依赖注入，不使用全局回退
- ⚠️严重程度: BLOCKER

**[BLOCKER-29] `RoleExecutionKernel._data_stores` 非隔离**
- 📍 `polaris/cells/roles/kernel/internal/kernel/core.py:189`
- 🧨 `dict[str, RoleDataStore]` 在内核实例内共享，角色 A 的数据可泄露到角色 B
- 🔧修复: 使 `_data_stores` 请求级作用域，执行后清理

**[BLOCKER-30] ChiefEngineerAdapter 硬编码角色回退无隔离**
- 📍 `polaris/cells/roles/kernel/internal/kernel/core.py:771-781`
- 🧨 无 profile 时静默回退到 `["director", "pm", "architect", "chief_engineer", "qa"]`，可能造成权限提升
- 🔧修复: 移除静默回退，无 profile 时显式错误
- ⚠️严重程度: BLOCKER — 审计轨迹损坏、跨角色权限提升

**[BLOCKER-31] `_build_analysis_message` 易受 prompt injection**
- 📍 `polaris/cells/roles/adapters/internal/chief_engineer_adapter.py:83-103`
- 🧨 用户提供的 `target` 直接插值到 prompt 无消毒
- 🔧修复: 应用 `_looks_like_prompt_injection()` 检查
- ⚠️严重程度: BLOCKER

**[BLOCKER-32] 废弃 `runtime/internal` 模块导入仍发出警告**
- 📍 `polaris/cells/roles/runtime/internal/__init__.py:27-34`
- 🧨 任何导入都触发 DeprecationWarning，污染 stderr 和测试输出
- 🔧修复: 仅在实际使用废弃 API 时警告

#### 🟡 SUGGESTION

**[SUGGESTION-23] `ConversationState` 为不完整占位符**
- 📍 `polaris/cells/roles/kernel/internal/policy/conversation_state.py`
- 标记为"Task #3 完成后替换"

**[SUGGESTION-24] Prompt injection 检测正则不完整**
- 📍 `polaris/cells/roles/kernel/internal/services/context_assembler.py:755-780`
- 可通过大小写变化、Unicode 同形字绕过

**[SUGGESTION-25] `_injected_llm_invoker` NotImplementedError 创建向后兼容陷阱**
- 📍 `polaris/cells/roles/kernel/internal/kernel/core.py:708-711`

**[SUGGESTION-26] `RoleProfileRegistry.CORE_ROLES` 验证不防止重复注册**
- 📍 `polaris/cells/roles/profile/internal/registry.py:306-318`

#### ⚪ NITPICK

- `allow_override=False` 仅在注册时强制
- `registry` 全局变量遮蔽标准库
- `or RoleProfileRegistry()` 模式创建不一致状态

---

### 👤 Performance & Scalability Engineer

#### 🔴 BLOCKER

**[BLOCKER-33] `open_text_log_append` 返回未关闭文件句柄**
- 📍 `polaris/kernelone/fs/text_ops.py:140-143`
- 🧨 函数返回原始 `TextIO` 句柄，调用者无法安全使用 with 语句
- 🔧修复: 改为 context manager
- ⚠️严重程度: BLOCKER — 长期运行进程文件句柄泄漏

**[BLOCKER-34] `tempfile.mkstemp` 文件描述符泄漏**
- 📍 `polaris/kernelone/fs/text_ops.py:77`
- 🧨 若 `os.fdopen()` 抛异常，fd 泄漏
- 🔧修复: fd 放入 try-finally

**[BLOCKER-35] NATS server 运行时文件句柄从不关闭**
- 📍 `polaris/infrastructure/messaging/nats/server_runtime.py:112-113`
- 🧨 `_stdout_handle` 和 `_stderr_handle` 在服务器停止时未清理
- 🔧修复: 添加 `close()` 方法

#### 🟡 SUGGESTION

**[SUGGESTION-27] `call_later` + lambda fire-and-forget 错误处理缺失**
- 📍 `polaris/kernelone/runtime/execution_runtime.py:577-580`

**[SUGGESTION-28] `BoundedCache` 缺乏时间驱逐**
- 📍 `polaris/kernelone/runtime/bounded_cache.py`
- 无 TTL，仅基于容量

**[SUGGESTION-29] `@lru_cache` 在数据库连接上无失效**
- 📍 `polaris/infrastructure/db/repositories/accel_state_db.py:70-76`

**[SUGGESTION-30] SQLite 每读操作创建连接**
- 📍 `polaris/infrastructure/cognitive_runtime/sqlite_store.py`
- 无连接池，高频读开销大

**[SUGGESTION-31] JSONL buffer 无界可能内存 spike**
- 📍 `polaris/kernelone/fs/jsonl/ops.py:77`

#### ⚪ NITPICK

- 健康检查访问私有属性 `_async_semaphore._value`

---

### 👤 Python Code Quality Guardian

#### 🔴 BLOCKER

**[BLOCKER-36] 存储适配器中异常吞噬隐藏 FileNotFoundError**
- 📍 `polaris/infrastructure/storage/adapter.py:110-111`
- 🧨 `read_text` 捕获 `FileNotFoundError` 后再捕获通用 `Exception` 返回 `None`
- 🔧修复: 区分文件不存在和其他 OSError

**[BLOCKER-37] 抄本服务静默异常吞噬**
- 📍 `polaris/domain/services/transcript_service.py:272-273,292-293`
- 无日志，无法诊断文件损坏

**[BLOCKER-38] 阶段执行器静默异常吞噬**
- 📍 `polaris/domain/state_machine/phase_executor.py:301-303,317-318,413-414`
- 多个 `except Exception:` 返回 `None`/`False` 无日志

**[BLOCKER-39] 后端引导静默异常吞噬**
- 📍 `polaris/bootstrap/backend_bootstrap.py:268-272`
- 关闭失败隐藏，资源可能处于坏状态

**[BLOCKER-40] 搜索网关静默异常 + 全局可变状态**
- 📍 `polaris/cells/context/engine/internal/search_gateway.py`
- 全局 `_service` 无线程安全

#### 🟡 SUGGESTION

**[SUGGESTION-32] 多个 CLI 入口点 sys.path 操纵**
- 📍 `polaris/delivery/cli/*.py` 等 20+ 文件

**[SUGGESTION-33] 全局可变单例状态**
- 📍 多处 `_transcript_service`, `_skill_service`, `_service`

**[SUGGESTION-34] 过度使用 type: ignore 注释**
- 📍 50+ `# type: ignore[...]`

#### ⚪ NITPICK

- `openai_sdk.py` 裸 except
- `metrics_collector` 异常 pass
- `architecture_guard_cli` 模块级 catch all

---

## 三、统一结论（Chief Architect）

### 3.1 Top 10 技术债（按危险排序）

| 排名 | ID | 问题 | 严重性 | 根因 |
|------|-----|------|--------|------|
| 1 | BLOCKER-18 | 审计事件签名从未填充 | BLOCKER | 实现遗漏，链完整性受损 |
| 2 | BLOCKER-09 | 写工具可被重试无幂等声明 | BLOCKER | 无副作用声明规范 |
| 3 | BLOCKER-15 | TurnEngine 可能无限循环 | BLOCKER | 无内在轮次限制 |
| 4 | BLOCKER-03 | Provider 实例永久缓存不失效 | BLOCKER | 无健康检查机制 |
| 5 | BLOCKER-02 | 无跨 Provider 故障转移 | BLOCKER | 架构缺失 |
| 6 | BLOCKER-06 | 双工具定义源不一致 | BLOCKER | 历史迁移遗留 |
| 7 | BLOCKER-16 | 流式执行完全失效 | BLOCKER | 流式基础设施未接入 |
| 8 | BLOCKER-20 | Evidence Bundle Git 失败静默 | BLOCKER | 错误处理不完整 |
| 9 | BLOCKER-17 | 事件处理器异常被吞没 | BLOCKER | 错误抑制模式 |
| 10 | BLOCKER-01 | fallback_model 参数被忽略 | BLOCKER | 实现遗漏 |

### 3.2 重构优先级路线图

```
Phase 1 (Week 1-2): 止血优先
├── 修复无限循环风险（TurnEngine max_turns）
├── 修复写工具重试幂等问题
├── 修复审计签名填充
└── 修复 Git 失败静默

Phase 2 (Week 3-4): 工具系统收敛
├── 统一工具定义源（废弃 definitions.py）
├── 修复 Provider 缓存和故障转移
├── 修复 fallback_model 实现
└── 修复流式执行

Phase 3 (Week 5-6): 上下文系统治理
├── 统一 Token 估算公式
├── 修复向量存储失败处理
├── 修复去重逻辑
└── 修复 SessionContinuity 序列更新

Phase 4 (Week 7-8): 事件系统加固
├── 异步处理器错误传播
├── 修复超时竞态条件
├── 修复取消并发修改
└── 修复 RuntimeEventFanout 处理器

Phase 5 (Week 9-10): 角色系统隔离
├── 移除全局单例 Registry
├── 修复 _data_stores 隔离
├── 移除硬编码角色回退
└── 修复 prompt injection 漏洞

Phase 6 (Week 11-12): 基础设施收口
├── 文件句柄泄漏修复
├── 异常处理规范化
├── sys.path 操纵移除
└── 全局状态消除
```

### 3.3 最小可落地重构方案（MVP）

**立即执行（不需评审）**:
1. TurnEngine 添加 `max_turns` 检查
2. 写工具添加 `idempotent: false` 跳过重试
3. 审计事件实现 HMAC 签名
4. Git 失败发出 `INTERNAL_AUDIT_FAILURE` 事件

**短期执行（1周内评审）**:
1. 统一工具定义源为 `contracts.py`
2. Provider 实例添加 TTL 缓存
3. 修复流式执行接入
4. 异常处理添加日志

**中期执行（2周内架构评审）**:
1. 角色系统依赖注入改造
2. 事件系统重构
3. Token 估算统一
4. 全局状态消除

### 3.4 风险评估

| 改动 | 风险等级 | 说明 |
|------|----------|------|
| TurnEngine max_turns | 🟢 低 | 仅添加检查，现有流不变 |
| 写工具幂等标记 | 🟢 低 | 新字段向后兼容 |
| 审计签名实现 | 🟡 中 | 需验证对现有事件的影响 |
| Provider 缓存 TTL | 🟡 中 | 需评估性能影响 |
| 流式执行接入 | 🔴 高 | 涉及核心执行路径 |
| 角色系统 DI | 🔴 高 | 影响所有角色执行 |
| 全局单例消除 | 🔴 高 | 跨多个模块 |

---

## 四、架构符合性评估

### 4.1 KernelOne 基座原则

| 原则 | 状态 | 证据 |
|------|------|------|
| KernelOne 无业务语义 | ❌ FAIL | `_LEGACY_LOGICAL_PREFIX_ALIASES` 含 `tasks`, `docs`, `dispatch` |
| KernelOne 仅导入内部 | ✅ PASS | 无从 delivery/application/domain 导入 |
| Cells 使用 KernelOne 基座 | ✅ PASS | Cells 正确依赖 kernelone contracts |
| Infrastructure 实现 kernelone ports | ✅ PASS | infrastructure/ 使用 kernelone contracts 正确 |

### 4.2 依赖方向

```
✅ 正确: bootstrap → delivery → application → domain/kernelone
✅ 正确: infrastructure → kernelone (ports)
❌ 违规: delivery → domain (应经 application)
❌ 违规: 多个 cells → filesystem (应经 state owner command ports)
```

### 4.3 状态所有权

```
❌ 违规: runtime/tasks/* 由 runtime.task_runtime 拥有
        但 director.planning/director.tasking/director.execution/roles.runtime
        均声明直接 fs.write:runtime/tasks/*

❌ 违规: runtime/events/* 由 events.fact_stream 拥有
        但 10+ cells 声明直接 fs.write:runtime/events/*
```

---

## 五、验证计划

### 5.1 必须验证的改动

| 改动 | 验证方法 | 通过标准 |
|------|----------|----------|
| TurnEngine max_turns | 单元测试 + e2e 压力测试 | 达到限制时正确停止 |
| 写工具幂等 | 集成测试 + benchmark 重试场景 | 写工具不重复执行 |
| 审计签名 | 单元测试 | 签名可验证 |
| Provider 缓存 TTL | 集成测试 | 失败实例被驱逐 |
| 流式执行 | E2E 测试 | 客户端接收增量块 |
| 角色隔离 | 集成测试 | 跨角色无状态泄露 |

### 5.2 回归测试覆盖

```bash
# 核心路径测试
pytest polaris/cells/roles/kernel/internal/turn_engine/ -v
pytest polaris/kernelone/llm/engine/ -v
pytest polaris/kernelone/events/ -v
pytest polaris/cells/audit/evidence/ -v

# 集成测试
pytest tests/test_roles_kernel.py -v
pytest tests/test_llm_agentic_benchmark.py -v
```

---

## 六、审计元数据

- **审计执行日期**: 2026-04-01
- **审计团队**: 10 人专家委员会
- **代码范围**: polaris/ (~1642 Python 文件)
- **发现总数**: 92 项（35 BLOCKER / 32 SUGGESTION / 25 NITPICK）
- **综合评分**: 5.2/10
- **架构健康状态**: ⚠️ 需要重大改进

---

*本报告由 Polaris 代码审计委员会生成*
*10 位专家分工协作，覆盖 LLM 系统、工具调用、上下文管理、执行引擎、审计可观测性、事件驱动、角色系统、性能扩展、Python 工程质量、架构治理*
