# Polaris/KernelOne 全息收敛审计报告

**版本**: v2.0 | **日期**: 2026-04-04 | **专家数**: 10

---

## 执行摘要

| 严重度 | 数量 | 关键领域 |
|--------|------|----------|
| CRITICAL | 12 | 架构违规、Tool系统混乱、Event总线分裂 |
| HIGH | 35 | 重复代码、单例模式、类型冲突 |
| MEDIUM | 80+ | 命名混乱、魔法数字、异常吞噬 |

---

## 🔴 P0 - 生死级 (CRITICAL)

### [P0-001] ToolCall 类定义 6 处不兼容
**Expert**: Expert 7
| 位置 | 字段 |
|------|------|
| `kernelone/llm/contracts/tool.py:27` | id, name, arguments, source, raw, parse_error |
| `cells/roles/kernel/public/transcript_ir.py:210` | call_id, tool_name, args, provider, provider_meta |
| `cells/roles/kernel/internal/services/contracts.py:242` | id, name, arguments (简化版) |
| `cells/roles/kernel/internal/services/tool_executor.py:81` | tool, args, call_id |
| `cells/roles/adapters/internal/schemas/base.py:11` | tool, arguments, reasoning (Pydantic) |
| `kernelone/llm/toolkit/native_function_calling.py:59` | 别名到 canonical |

**问题**: 字段名不兼容 (`name` vs `tool` vs `tool_name`)，类型不兼容 (frozen dataclass vs mutable vs Pydantic)

---

### [P0-002] parse_tool_calls 多实现返回类型不兼容
**Expert**: Expert 7
- `kernelone/llm/toolkit/parsers/core.py:23` → `list[ParsedToolCall]`
- `kernelone/llm/toolkit/parsers/canonical.py:122` → `list[CanonicalToolCall]`
- `cells/roles/kernel/internal/output_parser.py:196` → `list[ToolCallResult]`
- `kernelone/llm/toolkit/native_function_calling.py:396` → `list[ToolCall]`

**问题**: 4种返回类型，调用链中类型不匹配

---

### [P0-003] STANDARD_TOOLS vs _TOOL_SPECS 双重权威
**Expert**: Expert 7
- `kernelone/llm/toolkit/definitions.py:1141` - STANDARD_TOOLS (list[ToolDefinition])
- `kernelone/tools/contracts.py:102` - _TOOL_SPECS (dict[str, ToolSpec])

**问题**: 两种完全不同的数据结构，executor 有显式 fallback 逻辑承认分裂

---

### [P0-004] ToolSpec/ToolDefinition 定义 3 处
**Expert**: Expert 7
- `kernelone/tools/tool_spec_registry.py:30` - ToolSpec (canonical_name, aliases...)
- `kernelone/llm/toolkit/definitions.py:47` - ToolDefinition (name, parameters...)
- `kernelone/agent/tools/contracts.py:146` - ToolSpec (tool_id, enabled...)

**问题**: 三种不兼容 schema

---

### [P0-005] 7 种不互操作的事件总线
**Expert**: Expert 9
1. `MessageBus` - async pub/sub
2. `EventRegistry` - typed event system
3. `TypedEventBusAdapter` - 部分桥接
4. `InMemoryAgentBusPort` - sync thread-safe
5. `KernelOneMessageBusPort` - NATS-backed
6. `InMemoryBroker/NATSBroker` - Neural Syndicate
7. `UEPEventPublisher` - UEP v2.0

**问题**: TypedEventBusAdapter 仅单向桥接，非全双工

---

### [P0-006] 事件类型字符串分裂 "tool_call" vs "tool.call"
**Expert**: Expert 9
- 6+ 处使用 `"tool_call"` (underscore)
- NATS/infrastructure 使用 `"tool.call"` (dot)

**问题**: 生产者和消费者使用不同约定导致静默路由失败

---

### [P0-007] KernelOne 导入 Cells 内部业务逻辑
**Expert**: Expert 3
- `kernelone/agent_runtime/neural_syndicate/nats_broker.py:47,100`
- `kernelone/agent_runtime/neural_syndicate/broker.py:43`
- `kernelone/agent_runtime/neural_syndicate/base_agent.py:57`
- `kernelone/llm/toolkit/__init__.py:23`

**违规**: ACGA 2.0 Rule 3 - KernelOne 必须不导入 Cells 业务逻辑

---

### [P0-008] 跨 Cell 内部导入绕过公开契约
**Expert**: Expert 3
7 个 Cell 导入 `cells/roles/runtime/internal` 而非公开契约:
- chief_engineer, architect, director (planning/execution)
- pm_planning, llm/control_plane, finops

**违规**: ACGA 2.0 Rule 2 - 跨 Cell 只能走公开契约

---

### [P0-009] ContextBudgetPort 两处不兼容定义
**Expert**: Expert 4
- `kernelone/llm/ports.py:31` - `get_remaining_tokens() -> int`
- `kernelone/context/contracts.py:123` - `allocate()`, `estimate_tokens()`, `truncate_to_budget()`

**问题**: 完全不同接口，导入顺序决定运行时失败

---

### [P0-010] ToolExecutorPort vs ToolExecutorProtocol 接口不兼容
**Expert**: Expert 4
- `kernelone/llm/contracts/tool.py:356` - `execute_call(*, workspace, tool_call)`
- `cells/roles/kernel/internal/services/contracts.py:436` - `execute(tool_name, args)`

**问题**: 方法签名完全不同

---

### [P0-011] 两个 WorkflowEngine 类重叠
**Expert**: Expert 10
- `kernelone/workflow/engine.py:194`
- `cells/orchestration/workflow_runtime/internal/runtime_engine/runtime/embedded/engine.py:65`

**问题**: embedded 版本是遗留复制，应迁移到 kernelone 版本

---

### [P0-012] TurnEngine vs TurnTransactionController 职责重叠
**Expert**: Expert 10
- TurnEngine (1549行) - "the only execution loop engine"
- TurnTransactionController (1067行) - 完全独立的 turn 执行循环

**问题**: 文档声称 TurnEngine 唯一，但 TurnTransactionController 提供独立实现

---

## 🟠 P1 - 高优先级 (HIGH)

### [P1-001] _utc_now() 函数 28+ 处复制
**Expert**: Expert 1
```python
def _utc_now() -> datetime:
    return datetime.now(timezone.utc)
```
分布在 kernelone/, infrastructure/, cells/ 各层。正确实现在 `infrastructure/accel/utils.py`

---

### [P1-002] DEFAULT_DEFECT_TICKET_FIELDS 4 处复制
**Expert**: Expert 1
分布在 director/execution 和 director/planning 的 logic.py 和 logic_rules.py

---

### [P1-003] parse_json_payload 4 处复制
**Expert**: Expert 1
与 P1-002 相同位置，完整函数体复制

---

### [P1-004] GENESIS_HASH 3 处复制
**Expert**: Expert 1
- `kernelone/contracts/technical/master_types.py:44`
- `kernelone/audit/contracts.py:13`
- `infrastructure/audit/stores/audit_store.py:86`

---

### [P1-005] CircuitBreakerOpenError 3 处定义继承不同
**Expert**: Expert 1, Expert 4
- `kernelone/llm/engine/resilience.py:210` → Exception
- `kernelone/llm/exceptions.py:246` → LLMError
- `kernelone/benchmark/chaos/rate_limiter.py:35` → Exception

---

### [P1-006] ShellDisallowedError 2 处复制
**Expert**: Expert 1
- `kernelone/process/contracts.py:82`
- `kernelone/process/async_contracts.py:169`

---

### [P1-007] TaskRuntimeState 2 处完全相同定义
**Expert**: Expert 10
- `kernelone/workflow/engine.py:141`
- `cells/orchestration/workflow_runtime/internal/runtime_engine/runtime/embedded/engine.py:34`

---

### [P1-008] ToolSpecRegistry 类级共享状态无重置
**Expert**: Expert 2
`_specs` 和 `_canonical_names` 在类级别，测试隔离问题

---

### [P1-009] ThemeManager 模块级急切单例
**Expert**: Expert 2
`styles.py:397` 模块导入时实例化，无 `reset_for_testing()`

---

### [P1-010] MetricsCollector 模块级全局指标
**Expert**: Expert 2
Counter/Histogram/Gauge 在模块级别，`reset()` 不清理 `_instance`

---

### [P1-011] JobManager 无公开重置
**Expert**: Expert 2
`_jobs` dict 累积，无 `clear_all_jobs()`

---

### [P1-012] AsyncIndexManager workspace-keyed 单例 bug
**Expert**: Expert 2
`__new__` 只检查 `None`，忽略 workspace 参数变化

---

### [P1-013] 三种 Token Estimator 接口
**Expert**: Expert 4
- `TokenEstimatorInterface(ABC)` - estimate_tokens(text, model)
- `TokenEstimator(Protocol)` - estimate_messages_tokens(messages)
- `TokenEstimatorPort(Protocol)` - 又一个签名

---

### [P1-014] KernelError 两处不同定义
**Expert**: Expert 4
- `kernelone/exceptions.py:49` - KernelOne 运行时错误基类
- `cells/roles/kernel/internal/services/contracts.py:31` - Kernel Cell 服务层异常

---

### [P1-015] BootstrapError 两处复制
**Expert**: Expert 4
- `bootstrap/launch_validation.py:168`
- `bootstrap/backend_bootstrap.py:40`

---

### [P1-016] FrozenInstanceError 继承 TypeError 非 Exception
**Expert**: Expert 4
`domain/models/config_snapshot.py:61` 命名像 Exception 但继承 TypeError

---

### [P1-017] Protocol 命名不一致
**Expert**: Expert 4
- Port 后缀: `ToolExecutorPort`, `ContextBudgetPort`
- I 前缀: `IAuditVerdictService`, `IRoleSessionService`
- 无后缀: `TokenEstimator`, `ToolExecutorProtocol`

---

### [P1-018] DEFAULT_TIMEOUT_SECONDS 不一致值
**Expert**: Expert 1
- `process/contracts.py:28` = 30
- `process/async_contracts.py:54` = 30
- `process/background_manager.py:88` = 300 (不同!)

---

### [P1-019] StreamResult 两处完全不同定义
**Expert**: Expert 1
- `process/async_contracts.py:117` - 进程执行结果
- `llm/engine/stream/config.py:166` - LLM 流结果

---

### [P1-020] 路径遍历安全检查异常吞噬
**Expert**: Expert 5
`cells/roles/kernel/internal/policy.py:659-660,678-679,688-689`
`except Exception: pass` 在安全关键代码中

---

### [P1-021] Tool Policy 层异常吞噬
**Expert**: Expert 5
`cells/roles/kernel/internal/policy/layer/tool.py:264-265,292-293,302-303`

---

### [P1-022] ProviderManager 双重定义遗留混淆
**Expert**: Expert 2
kernelone 版本废弃但仍存在，infrastructure 版本是权威

---

### [P1-023] 绝对导入 vs 相对导入不一致
**Expert**: Expert 6
- `provider_adapters/__init__.py:18-26` 使用绝对导入
- `robust_parser/__init__.py:31-36` 使用绝对导入
- 同包内应使用相对导入

---

### [P1-024] timeout=300 魔法数字 30+ 处
**Expert**: Expert 8
默认超时 5 分钟硬编码，无命名常量

---

### [P1-025] timeout=3600 魔法数字 18 处
**Expert**: Expert 8
工作流超时 1 小时硬编码

---

### [P1-026] MAX_FILE_SIZE=10MB 5 处重复
**Expert**: Expert 8
`10 * 1024 * 1024` 表达式复制 5 处

---

### [P1-027] POLARIS-only 环境变量无 KERNELONE_ 回退
**Expert**: Expert 8
50+ 处直接使用 `os.environ.get("KERNELONE_...)` 无回退

---

### [P1-028] TaskStateError/WorkerStateError 继承错误基类
**Expert**: Expert 5
应继承 `DomainException` 或 `StateError`，而非 `Exception`

---

### [P1-029] ConstitutionViolationError 继承错误基类
**Expert**: Expert 5
安全相关异常应正确分类

---

### [P1-030] 4 种 emit 路径无协调
**Expert**: Expert 9
- `emit_event()` - JSONL
- `MessageBus.publish()` - async pub/sub
- `EventRegistry.emit()` - typed
- `UEPEventPublisher.publish_stream_event()` - UEP

---

## 🟡 P2 - 中优先级 (MEDIUM)

### [P2-001] HandlerRegistry 两处名称冲突
**Expert**: Expert 1
- `kernelone/workflow/engine.py:75` - DI Protocol
- `kernelone/llm/toolkit/executor/handlers/__init__.py:18` - 工具处理器注册表

---

### [P2-002] Context Gatherer 常量 2 处复制
**Expert**: Expert 1
- `cells/director/execution/internal/context_gatherer.py:37-43`
- `cells/director/planning/internal/context_gatherer.py:27-33`

---

### [P2-003] DEFAULT_MAX_RETRIES 不一致值
**Expert**: Expert 1
- `kernelone/tools/constants.py:17` = 2
- `cells/roles/kernel/internal/retry_policy_engine.py:27` = 3

---

### [P2-004] StrategyRegistry/RoleOverlayRegistry 重置问题
**Expert**: Expert 2
`RoleOverlayRegistry._reset_instance()` 是私有方法

---

### [P2-005] AuditGateway/KernelAuditRuntime per-path 单例
**Expert**: Expert 2
需测试隔离调用 `shutdown_all()`

---

### [P2-006] EventRegistry 重置存在但 opt-in
**Expert**: Expert 2
需显式调用 `reset_default_registry()`

---

### [P2-007] ToolCallResult 三处定义
**Expert**: Expert 7
- `output_parser.py:47` - (tool, args)
- `benchmark/llm/tool_accuracy.py:89` - (case_id, tool_called, params, success...)
- `native_function_calling.py:64` - (tool_call_id, name, output, is_error)

---

### [P2-008] ParsedToolCall vs ToolCall vs CanonicalToolCall
**Expert**: Expert 7
三种相似但不同的类型，解析链中类型不匹配

---

### [P2-009] Tool Normalizers 三种机制重叠
**Expert**: Expert 7
TOOL_NORMALIZERS dict + SchemaDrivenNormalizer + 直接 arg_aliases

---

### [P2-010] TurnState 仅 1 处定义 (审计声称 5 处是错的)
**Expert**: Expert 9
`cells/roles/kernel/internal/turn_state_machine.py:16` 是唯一 TurnState Enum

---

### [P2-011] UEP Event Type 与 NATS 不匹配
**Expert**: Expert 9
UEP 用 `"tool_call"`，NATS infrastructure 用 `"tool.call"`

---

### [P2-012] JSONL Event 绕过 MessageBus
**Expert**: Expert 9
`emit_event()` 写 JSONL 不通过事件系统

---

### [P2-013] TurnStateMachine vs TaskPhase 命名混乱
**Expert**: Expert 10
- TurnState: IDLE, CONTEXT_BUILT, DECISION_REQUESTED...
- TaskPhase (domain): PENDING, PLANNING, VALIDATION...
- TaskPhase (runtime): INIT, PLANNING, ANALYZING...

---

### [P2-014] ExplorationPhase 两处定义
**Expert**: Expert 10
- `kernelone/context/strategy_contracts.py:27`
- `kernelone/context/exploration_policy.py:36`

---

### [P2-015] inline stdlib imports
**Expert**: Expert 6
`import re` 在函数内部而非模块级别

---

### [P2-016] hashlib 重复导入
**Expert**: Expert 6
同一类中多次 `import hashlib`

---

### [P2-017] 36+ 处 `except Exception: pass`
**Expert**: Expert 5
静默吞噬异常，最危险在安全检查代码

---

### [P2-018] 15+ 自定义异常直接继承 Exception
**Expert**: Expert 5
应继承统一异常层次结构

---

### [P2-019] cache TTL 值 300/600/120 重复
**Expert**: Expert 8
`cache_policies.py` 和 `cache_manager.py` 各定义一套

---

### [P2-020] buffer_size=1000 三处重复
**Expert**: Expert 8
stream config, telemetry, sse_streamer

---

## 修复优先级建议

### Phase 1 (Week 1-2): CRITICAL - 12 项
1. 统一 ToolCall 类定义 (P0-001)
2. 统一 parse_tool_calls 返回类型 (P0-002)
3. 合并 STANDARD_TOOLS/_TOOL_SPECS (P0-003)
4. 统一事件总线架构 (P0-005)
5. 修复事件类型字符串 (P0-006)
6. 修复架构违规 (P0-007, P0-008)

### Phase 2 (Week 3-4): HIGH - 35 项
- 代码去重: _utc_now, GENESIS_HASH, parse_json_payload
- 单例模式: ToolSpecRegistry, ThemeManager, MetricsCollector
- 类型统一: ContextBudgetPort, ToolExecutorPort

### Phase 3 (Week 5-6): MEDIUM - 80+ 项
- 命名规范化
- 魔法数字提取
- 异常处理改进

---

## 验证方法

```bash
# 代码质量
python -m ruff check polaris/
python -m mypy polaris/

# 测试通过
python -m pytest polaris/tests/ -q

# 架构检查
python docs/governance/ci/scripts/run_kernelone_release_gate.py --mode all
```