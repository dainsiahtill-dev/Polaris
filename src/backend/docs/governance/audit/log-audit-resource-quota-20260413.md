# 日志审计任务 #88：资源与配额分析报告

**审计日期**: 2026-04-13
**审计范围**: `polaris/kernelone/` 下的资源管理和配额控制
**审计目标**: 分析 budget 系统的完整链路、资源配额执行、硬限制生效、资源泄漏风险、断路器行为

---

## 1. 预算（Budget）系统完整链路分析

### 1.1 多层 Budget 架构

| 层级 | 文件 | 类/模块 | 职责 |
|------|------|---------|------|
| **LLM Token 层** | `llm/engine/prompt_budget.py` | `TokenBudgetManager` | 模型 prompt 预算决策、压缩路由 |
| **Context 组装层** | `context/budget_gate.py` | `ContextBudgetGate` | context window 安全边距执行 |
| **Chunk 追踪层** | `context/chunks/budget.py` | `ChunkBudgetTracker` | 分块 token 预算追踪 |
| **失败预算层** | `tool_execution/failure_budget.py` | `FailureBudget` | 工具失败 ALLOW/ESCALATE/BLOCK 三级决策 |
| **事件优化层** | `context/context_os/budget_optimizer.py` | `ORToolsBudgetOptimizer` | 基于 OR-Tools 的背包预算优化 |

### 1.2 Budget 链路图

```
TokenBudgetManager (prompt_budget.py)
    ↓ enforce() → 解析 max_context_tokens
    ↓ 计算 safety_margin_tokens = max(2048, 0.05C)
    ↓ 路由压缩请求 → CompressionRouter
        ↓ code_compress / line_compact / hard_trim
ContextBudgetGate (budget_gate.py)
    ↓ can_add() → 预算检查
    ↓ record_usage() → token 消费记录
    ↓ suggest_compaction() → 压缩建议
ChunkBudgetTracker (chunks/budget.py)
    ↓ try_admit() → 分块准入控制
    ↓ 优先级驱逐策略
FailureBudget (failure_budget.py)
    ↓ record_failure() → 失败计数
    ↓ ALLOW (>max_failures_per_tool=3)
    ↓ ESCALATE (>max_same_pattern=2)
    ↓ BLOCK (>max_failures_per_tool=3)
```

### 1.3 Budget 关键常量

**TokenBudgetManager** (`llm/engine/prompt_budget.py`):
- `safety_margin_ratio`: 0.12 (12%)
- `min_output_tokens`: 256
- `min_prompt_budget_tokens`: 256

**ContextBudgetGate** (`context/budget_gate.py`):
- `safety_margin`: 0.85 (默认 85% of model_window)
- `MIN_BUDGET_TOKENS`: 30,000 (绝对下限)

**FailureBudget** (`tool_execution/failure_budget.py`):
- `max_failures_per_tool`: 3
- `max_same_pattern`: 2
- `max_total_per_turn`: 10

---

## 2. 资源配额（Quota）执行分析

### 2.1 ResourceQuota 系统

**文件**: `polaris/kernelone/resource/quota.py`

| 配额项 | 默认值 | 说明 |
|--------|--------|------|
| `cpu_quota_ns` | 60,000,000,000 (60s) | CPU 时间配额 |
| `memory_bytes` | 2GB | 内存限制 |
| `max_concurrent_tools` | 10 | 并发工具执行上限 |
| `max_turns` | **50** | 对话轮次上限 |
| `max_wall_time_seconds` | 300 (5min) | 墙钟时间上限 |

**系统级限制**:
- `SYSTEM_CPU_QUOTA_NS`: 600,000,000,000 (600s)
- `SYSTEM_MEMORY_BYTES`: 16GB

### 2.2 QuotaStatus 枚举

| 状态 | 含义 |
|------|------|
| `ALLOWED` | 所有资源在限制内 |
| `DENIED_SINGLE` | 单项资源超限 |
| `DENIED_MULTIPLE` | 多项资源超限 |
| `SYSTEM_OVERLOADED` | 系统级资源超限 |

### 2.3 配额检查执行点

**ResourceQuotaManager** (`resource/quota.py`):
- `check_quota(agent_id)` → QuotaStatus
- `check_tool_quota(agent_id)` → (bool, reason)
- `acquire_concurrent_tool(agent_id)` → bool (原子性增加)
- `release_concurrent_tool(agent_id)` → void (原子性减少)

**已知问题**:
- `acquire_concurrent_tool` 支持懒分配（auto-allocate if not registered），但 `release_concurrent_tool` 静默忽略未注册的 agent_id，可能导致并发计数不对称

---

## 3. 硬限制生效分析 (max_turns, max_tokens)

### 3.1 max_turns 多层分布

| 组件 | 位置 | max_turns 值 | 限制机制 |
|------|------|--------------|----------|
| ResourceQuota | `resource/quota.py` | 50 | `is_within_quota()` |
| MultiAgent ResourceQuotaManager | `multi_agent/resource_quota.py` | N/A | 异步配额管理 |
| BudgetPolicy | `cognitive/` | >0 required | ValueError if <=0 |
| ContextOS Validators | `benchmark/validators/contextos_validators.py` | 50 | max_turns validation |

### 3.2 max_tokens 预算链

**TokenBudgetManager.enforce()** (`llm/engine/prompt_budget.py`):
```python
# 1. 计算可用 prompt tokens
hard_available = max_context_tokens - reserve_output_tokens - safety_margin_tokens
allowed_prompt_tokens = max(1, min(hard_available, max_context_tokens - reserve_output_tokens))

# 2. Claude Code formula: safety_margin = max(2048, 0.05C)
safety_margin_tokens = max(2048, int(max_context_tokens * 0.05))

# 3. 压缩后仍超 → 拒绝
if compressed_tokens > allowed_prompt_tokens:
    return TokenBudgetDecision(allowed=False, ...)
```

**ContextBudgetGate** (`context/budget_gate.py`):
```python
# effective_limit = model_window * safety_margin (default 0.85)
# can_add() → (bool, reason)
# record_usage() → 更新 _current_tokens
# suggest_compaction() → 按 usage_ratio 分级建议
```

### 3.3 已知问题

**BUG M-2**: `max_turns=0` 应表示"无限制"，当前实现会 raise ValueError。
- 位置: `polaris/kernelone/cognitive/tests/test_bugfixes_20260410.py`
- 状态: 已知但未修复（测试文档记录）

---

## 4. 资源泄漏风险分析

### 4.1 已发现的风险点

#### 4.1.1 FailureBudget 不自动重置

**文件**: `polaris/kernelone/tool_execution/failure_budget.py`

```python
def record_failure(self, pattern: ToolErrorPattern, ...) -> FailureResult:
    # 每次调用增加计数
    self._tool_failures[tool_key] += 1
    self._pattern_failures[pattern_key] += 1
    self._total_failures += 1
    self._recent_patterns.append(pattern_key)
```

**风险**:
- `FailureBudget` 在 turn 之间**不会自动重置**
- 依赖调用方显式调用 `reset()` 清理
- 如果 turn 之间的错误累积，可能导致误判

#### 4.1.2 Global Singleton 状态持久化

**文件**: `polaris/kernelone/resource/quota.py`

```python
_GLOBAL_MANAGER: ResourceQuotaManager | None = None
_GLOBAL_MANAGER_LOCK = threading.Lock()

def get_global_quota_manager() -> ResourceQuotaManager:
    if _GLOBAL_MANAGER is None:
        with _GLOBAL_MANAGER_LOCK:
            if _GLOBAL_MANAGER is None:
                _GLOBAL_MANAGER = ResourceQuotaManager()
    return _GLOBAL_MANAGER
```

**风险**:
- Global manager 生命周期跨越多个任务/会话
- `allocate()` 后如果 `release()` 未被调用，agent 资源永久占用
- `reset_global_quota_manager()` 仅用于测试

#### 4.1.3 InMemoryArtifactStorage 无自动清理

**文件**: `polaris/kernelone/context/context_os/storage.py`

```python
def _evict_if_needed_locked(self, additional_bytes: int = 0) -> int:
    while len(self._artifacts) >= self._max_artifacts:
        self._artifacts.popitem(last=False)
    while self._current_size_bytes + additional_bytes > self._max_size_bytes:
        ...
```

**风险**:
- LRU 驱逐依赖访问模式
- 长时间未访问的 artifact 可能永远不被驱逐
- 无 TTL 或 time-based eviction

### 4.2 防护良好的区域

| 组件 | 防护机制 | 评估 |
|------|----------|------|
| `BoundedCache` (`runtime/bounded_cache.py`) | LRU + max_size=1000 | ✅ 有界内存 |
| `ChunkBudgetTracker` (`context/chunks/budget.py`) | 线程锁 + RLock | ✅ 线程安全 |
| `InMemoryArtifactStorage` | max_artifacts + max_size_bytes | ✅ 有界 |
| `ResilienceManager` (`llm/engine/resilience.py`) | 有限重试次数 | ✅ 有界 |

---

## 5. 断路器（Circuit Breaker）行为分析

### 5.1 CircuitBreaker 状态机

**文件**: `polaris/kernelone/llm/engine/resilience.py`

```
           ┌─────────────────────────────────────────┐
           │                                         │
           ▼                                         │
       CLOSED ──(failures >= threshold)──▶ OPEN     │
           ▲                                         │
           │                                         │
    (success >= threshold)                    (timeout)
           │                                         │
           └──────────── HALF_OPEN ◀────────────────┘
                            │
                     (any failure)
                            │
                            ▼
                         OPEN
```

### 5.2 状态转换配置

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `failure_threshold` | 5 | CLOSED → OPEN 所需失败次数 |
| `recovery_timeout` | 60.0s | OPEN → HALF_OPEN 等待时间 |
| `half_open_max_calls` | 3 | HALF_OPEN 状态最大测试调用 |
| `success_threshold` | 2 | HALF_OPEN → CLOSED 所需成功次数 |
| `window_seconds` | 120.0s | 滑动窗口（当前未使用） |

### 5.3 重试策略

**retry_with_jitter()**:
- 指数退避: `delay = min(base_delay * 2^attempt, max_delay)`
- 抖动: ±10% 随机偏移
- 快速失败: 401, 403, 400, 422 不重试

**RetryConfig** (`llm/engine/resilience.py`):
```python
@dataclass
class RetryConfig:
    max_attempts: int = 2  # 默认 2 次尝试 = 1 次重试
    base_delay: float = 1.0
    max_delay: float = 30.0
    exponential_base: float = 2.0
    retryable_errors: list[ErrorCategory] = [TIMEOUT, RATE_LIMIT, NETWORK_ERROR]
```

### 5.4 Thundering Herd 防护

**Jitter 防护** (`calculate_backoff_with_jitter`):
- 每次重试添加随机抖动 (0-10%)
- 防止多个客户端同步重试造成雪崩

**断路器防护**:
- OPEN 状态立即拒绝所有请求 (CircuitBreakerOpenError)
- HALF_OPEN 限制并发测试请求 (half_open_max_calls=3)

---

## 6. 发现的问题汇总

| # | 类别 | 问题 | 严重程度 | 位置 |
|---|------|------|----------|------|
| 1 | 资源泄漏 | `FailureBudget` turn 间不自动重置 | 中 | `failure_budget.py` |
| 2 | 资源泄漏 | Global manager 生命周期跨越任务 | 中 | `resource/quota.py` |
| 3 | 资源泄漏 | InMemoryArtifactStorage 无 TTL 驱逐 | 低 | `storage.py` |
| 4 | 逻辑缺陷 | `max_turns=0` 语义错误 | 低 | `cognitive/tests/test_bugfixes_20260410.py` |
| 5 | 并发安全 | `acquire_concurrent_tool` 懒分配与 release 不对称 | 低 | `resource/quota.py` |

---

## 7. 测试覆盖分析

### 7.1 关键测试文件

| 测试文件 | 测试数 | 覆盖内容 |
|----------|--------|----------|
| `tests/test_resource_quota.py` | 41 | ResourceQuota, ResourceUsage, QuotaManager |
| `context/tests/test_budget_gate.py` | 20+ | ContextBudgetGate, 溢出场景 |
| `tool_execution/tests/test_failure_budget.py` | 16 | ALLOW/ESCALATE/BLOCK 决策 |
| `llm/engine/tests/test_circuit_breaker.py` | 42 | 状态转换, 重试, 抖动 |

### 7.2 测试覆盖缺口

| 缺口 | 风险级别 | 说明 |
|------|----------|------|
| `FailureBudget` turn 间重置 | 中 | 无自动化测试验证 turn 重置 |
| Global manager 泄漏 | 中 | 无长时间运行测试验证资源释放 |
| CircuitBreaker 与 ResilienceManager 集成 | 低 | 单元测试存在，集成测试有限 |

---

## 8. 审计结论

### 8.1 优势

1. **多层 Budget 设计**: Token → Context → Chunk → Failure 四层分离，各司其职
2. **线程安全**: 广泛使用 RLock/threading.Lock/AioLock
3. **有界资源**: BoundedCache, InMemoryArtifactStorage 都有明确上限
4. **Circuit Breaker 完整**: 包含 HALF_OPEN 状态和防雪崩机制
5. **测试覆盖**: 核心组件都有对应的单元测试

### 8.2 风险项

| 风险 | 严重度 | 建议 |
|------|--------|------|
| `FailureBudget` 不自动重置 | 中 | 在 turn 开始时显式 reset() |
| Global manager 可能泄漏 | 中 | 监控 `get_all_agents()` 清理 |
| `max_turns=0` 语义错误 | 低 | 文档记录，评估修复成本 |
| InMemoryArtifactStorage 无 TTL | 低 | 考虑添加基于时间的驱逐 |

### 8.3 验证结果

- **Ruff 检查**: ✅ 无 lint 错误
- **测试收集**: ✅ Budget 相关测试全部可收集
- **CircuitBreaker 测试**: ✅ 42 个测试全部通过

---

## 9. 参考文件索引

| 文件 | 用途 |
|------|------|
| `polaris/kernelone/resource/quota.py` | ResourceQuotaManager |
| `polaris/kernelone/multi_agent/resource_quota.py` | Multi-Agent ResourceQuotaManager |
| `polaris/kernelone/llm/engine/prompt_budget.py` | TokenBudgetManager |
| `polaris/kernelone/context/budget_gate.py` | ContextBudgetGate |
| `polaris/kernelone/context/chunks/budget.py` | ChunkBudgetTracker |
| `polaris/kernelone/tool_execution/failure_budget.py` | FailureBudget |
| `polaris/kernelone/llm/engine/resilience.py` | CircuitBreaker, ResilienceManager |
| `polaris/kernelone/runtime/bounded_cache.py` | BoundedCache |
| `polaris/kernelone/context/context_os/storage.py` | InMemoryArtifactStorage |
| `polaris/kernelone/context/context_os/budget_optimizer.py` | ORToolsBudgetOptimizer |
| `polaris/kernelone/llm/runtime.py` | Provider failure tracking |

---

**审计结论**: Budget 系统设计完整，多层防御有效。资源泄漏风险存在于 global singleton 和 FailureBudget 的状态管理，需在 turn 边界显式清理。CircuitBreaker 实现规范，包含完整的 HALF_OPEN 恢复机制。