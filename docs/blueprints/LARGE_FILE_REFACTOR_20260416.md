# 大文件重构蓝图: 模块化拆分与职责收敛

**日期**: 2026-04-16  
**范围**: `src/backend/polaris/cells/roles/kernel/internal/` 下最近未提交的大文件 (>1200行)  
**目标**: 遵循 SRP、消除 God Class、保持外部 API 绝对兼容

---

## 1. 现状诊断

| 文件 | 行数 | 核心类 | 问题 |
|------|------|--------|------|
| `turn_engine/engine.py` | 2459 | `TurnEngine` | 循环编排、Quota、工具执行、结果构造、Context Pruning 全部耦合 |
| `context_gateway.py` | 1616 | `RoleContextGateway` | 安全消毒、Token估算、压缩策略、投影格式化全部在一个类 |
| `llm_caller/invoker.py` | 1842 | `LLMInvoker` | 非流/流式/结构化三种调用模式 + 事件发射全部堆叠 |

**共性反模式**:
- God Class: 单个类承担 5+ 个独立职责
- 私有方法爆炸: 每个类包含 15~25 个 `_` 私有方法
- 流式/非流式代码路径 80% 重复但内联在一起
- 测试困难: 无法单独 Mock 子系统

---

## 2. 重构原则

1. **无损兼容 (Zero Breaking Change)**: 原文件的公共类和方法签名 100% 保留。
2. **委托模式 (Delegation)**: 原类退化为 Facade，将请求转发给新提取的 collaborator。
3. **单一职责 (SRP)**: 每个新模块只负责一个明确的子域。
4. **显式依赖注入**: 所有 collaborator 通过 `__init__` 注入，便于测试。
5. **类型安全**: 100% 类型注解，通过 `mypy --strict`。

---

## 3. 模块拆分方案

### 3.1 TurnEngine (`turn_engine/`)

#### 目标架构

```
turn_engine/
├── __init__.py          # 导出 TurnEngine 及兼容别名
├── engine.py            # TurnEngine Facade (精简至 ~400行)
├── quota_manager.py     # `TurnQuotaManager` — 配额检查与记录
├── turn_materializer.py # `TurnMaterializer` — AssistantTurn 构造
├── result_builder.py    # `RunResultBuilder` — RoleTurnResult 组装
├── context_pruner.py    # `ContextPruner` — HALLUCINATION_LOOP 剪枝
└── stream_handler.py    # `StreamEventHandler` — 流式事件规范化
```

#### 职责映射

| 原方法 | 新家 | 说明 |
|--------|------|------|
| `_get_quota_manager` / `_check_quota_before_turn` / `_record_turn_in_quota` | `TurnQuotaManager` | 完全提取 |
| `_materialize_assistant_turn` / `_materialize_stream_visible_turn` | `TurnMaterializer` | 包含 native tool call 归一化 |
| `_build_turn_result_metadata` / `_build_run_result` | `RunResultBuilder` | 结果构造逻辑集中 |
| `_prune_context_for_loop_break` / `_check_and_handle_loop_break` / `_inject_loop_break_signal` / `_handle_blocked_tool_pruning` | `ContextPruner` | 剪枝策略独立 |
| `run_stream()` 中 chunk 解析与 yield | `StreamEventHandler` | 流式差异化逻辑收敛 |

#### 数据流

```
kernel.run(request) -> TurnEngine.run()
    -> TurnQuotaManager.check_before_turn()
    -> TurnMaterializer.materialize(raw_output)
    -> kernel._execute_single_tool()
    -> ContextPruner.check_loop_break()
    -> RunResultBuilder.build()
    -> return RoleTurnResult
```

---

### 3.2 RoleContextGateway (`context_gateway/`)

#### 目标架构

```
context_gateway/
├── __init__.py              # 导出 RoleContextGateway
├── gateway.py               # RoleContextGateway Facade (~350行)
├── security.py              # `SecuritySanitizer` — 提示词注入检测 + 用户消息消毒
├── token_estimator.py       # `TokenEstimator` — CJK-aware 估算
├── compression_engine.py    # `CompressionEngine` — L1/L2 压缩策略
├── projection_formatter.py  # `ProjectionFormatter` — ContextOSProjection -> messages
└── constants.py             # 正则、CJK范围、路由优先级等常量
```

#### 职责映射

| 原方法 | 新家 | 说明 |
|--------|------|------|
| `_sanitize_user_message` / `_sanitize_history_content` / `_looks_like_prompt_injection` | `SecuritySanitizer` | 安全子域 |
| `_estimate_tokens` / `_is_cjk_char` | `TokenEstimator` | 估算算法独立 |
| `_apply_compression` / `_smart_content_truncation` / `_emergency_fallback` 等 | `CompressionEngine` | 压缩策略独立 |
| `_messages_from_projection` / `_sort_events_by_routing_priority` / `_format_context_os_snapshot` | `ProjectionFormatter` | 投影格式化独立 |
| `_expand_transcript_to_messages` / `_dedupe_messages` | `ProjectionFormatter` | 消息处理 |
| 模块级正则/常量 | `constants.py` | 消除顶部 150+ 行常量 |

---

### 3.3 LLMInvoker (`llm_caller/invoker/`)

#### 目标架构

```
llm_caller/
├── invoker/
│   ├── __init__.py         # 导出 LLMInvoker
│   ├── invoker.py          # LLMInvoker Facade (~300行)
│   ├── sync_executor.py    # `SyncCallExecutor` — call() 逻辑
│   ├── stream_executor.py  # `StreamCallExecutor` — call_stream() 逻辑
│   ├── structured_executor.py # `StructuredCallExecutor` — call_structured() 逻辑
│   └── event_emitter.py    # `InvokerEventEmitter` — UEP + LLM 事件统一发射
└── caller.py               # 已有文件，保持不变
```

#### 职责映射

| 原方法 | 新家 | 说明 |
|--------|------|------|
| `call()` 主体 (~380行) | `SyncCallExecutor.execute()` | 包含 cache、fallback、response 解析 |
| `call_stream()` 主体 (~460行) | `StreamCallExecutor.execute()` | 包含 reconnect、dedupe、SLO |
| `call_structured()` 主体 (~350行) | `StructuredCallExecutor.execute()` | Instructor + fallback |
| `_emit_call_start_event` / `_emit_call_end_event` / `_emit_call_error_event` / `_emit_call_retry_event` / `_publish_uep_lifecycle_event` | `InvokerEventEmitter` | 事件发射统一化 |
| `_is_cache_eligible` / `_allow_native_tool_text_fallback` | 保留为 `LLMInvoker` 静态方法或移入 util | 简单判断函数 |

---

## 4. 关键技术决策

### 4.1 Facade 模式保证兼容性

每个原文件中的公共类保留，但瘦身为 **Orchestrator/Facade**:

```python
class TurnEngine(TurnEngineCompatMixin):
    def __init__(self, kernel, ...):
        self._quota = TurnQuotaManager()
        self._materializer = TurnMaterializer(kernel)
        self._result_builder = RunResultBuilder(kernel)
        self._pruner = ContextPruner()
        self._stream_handler = StreamEventHandler()
        # ... 原有逻辑通过委托实现
```

**好处**:
- 所有外部调用方 (`kernel.run()`, `WorkflowRoleAdapter`) 无需修改代码。
- 重构风险完全内聚在 `turn_engine/` 包内。

### 4.2 类型契约

每个新模块顶部显式声明 `TYPE_CHECKING` 导入，避免循环依赖:

```python
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from polaris.cells.roles.profile.public.service import RoleProfile
    from polaris.cells.roles.kernel.internal.policy import PolicyLayer
```

### 4.3 异常处理

- 所有新模块的 public 方法必须捕获预期异常并返回结构化结果，严禁裸 `except:`。
- 允许的最小异常捕获: `except (RuntimeError, ValueError) as exc:`。

### 4.4 测试策略

每个新提取的模块必须配有独立单元测试:

```
tests/turn_engine/test_quota_manager.py
tests/turn_engine/test_context_pruner.py
tests/context_gateway/test_compression_engine.py
tests/context_gateway/test_security.py
tests/llm_caller/test_sync_executor.py
tests/llm_caller/test_event_emitter.py
```

---

## 5. 迁移步骤 (执行顺序)

1. **TurnEngine 先拆**: 它是循环核心，被其他两个文件间接依赖的风险最低。
2. **ContextGateway 后拆**: 依赖 TurnEngine 的 `_request_to_state` 等，但主要是独立上下文构建。
3. **LLMInvoker 最后拆**: 它依赖 `LLMCaller._prepare_llm_request`，接口相对稳定。

---

## 6. 验证门禁

每完成一个文件的拆分，必须执行:

```bash
# 1. Ruff
ruff check <target_dir> --fix && ruff format <target_dir>

# 2. MyPy (strict)
mypy <target_dir> --strict

# 3. Pytest
pytest tests/turn_engine/ -v
pytest tests/context_gateway/ -v
pytest tests/llm_caller/ -v
```

**失败不得合并** (fail-closed)。

---

## 7. 风险与边界

| 风险 | 缓解措施 |
|------|----------|
| 循环导入 | 使用 `TYPE_CHECKING` + 延迟局部 import |
| 行为漂移 | 原文件公共方法保留为 Facade，100% 委托 |
| 流式/非流式不一致 | `StreamEventHandler` 与 `TurnMaterializer` 共用同一归一化逻辑 |
| 测试覆盖率下降 | 每个新模块必须配独立单元测试 |

---

## 8. 预期收益

| 指标 | 目标 |
|------|------|
| 单文件最大行数 | < 500 行 |
| 每个类的私有方法数 | < 10 个 |
| 单元测试可独立 mock | 是 |
| mypy strict | 0 errors |
| ruff | 0 warnings |
