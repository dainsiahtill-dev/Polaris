# KernelOne 架构重构蓝图
## 基于10人团队审计的深度改善计划

**文档版本**: v1.0.0
**创建日期**: 2026-03-26
**审计团队**: 10人高级Python架构师团队
**状态**: 待执行

---

## 1. 执行摘要

### 1.1 审计范围

| 维度 | 审计团队 | 评分 |
|------|----------|------|
| 架构设计 | #1 架构设计专家 | 5.7/10 |
| 代码质量 | #2 代码质量专家 | 5.8/10 |
| 安全性 | #3 安全专家 | ⭐⭐⭐⭐⭐ |
| 性能 | #4 性能工程师 | ⭐⭐⭐⭐ |
| 可维护性 | #5 技术债务专家 | 🔴高风险 |
| 测试覆盖 | #6 测试架构师 | ⭐⭐ |
| 错误处理 | #7 错误处理专家 | ⭐⭐ |
| API契约 | #8 API设计师 | ⭐⭐⭐⭐ |
| 状态管理 | #9 并发专家 | ⭐⭐⭐ |
| UTF-8规范 | #10 国际化工程师 | ⭐⭐⭐⭐ |

### 1.2 问题统计

| 严重度 | 数量 | 描述 |
|--------|------|------|
| 🔴 CRITICAL | 18 | 立即行动 |
| 🟠 HIGH | 18 | 1周内处理 |
| 🟡 MEDIUM | 12 | 2周内处理 |

### 1.3 预期收益

| 指标 | 当前 | 优化后 | 提升 |
|------|------|--------|------|
| 架构合规 | 5.7/10 | 8.0/10 | +40% |
| 代码质量 | 5.8/10 | 7.5/10 | +29% |
| 错误韧性 | 5.7/10 | 8.0/10 | +40% |
| 性能 | 基准 | -35-95% | 显著 |
| 测试覆盖 | 20.9% | 35% | +67% |

---

## 2. 问题清单与修复方案

### 2.1 🔴 CRITICAL 问题 (Week 1)

#### C-01: 业务语义泄漏
**问题**: Polaris角色类型硬编码，违反类Linux基础设施定位
**位置**:
- `polaris/kernelone/audit/failure_envelope.py`
- `polaris/kernelone/context/compaction.py`

**修复方案**:
```python
# Before
role_type: str = "unknown"  # PM, Director, QA, Architect
failure_envelope.py: Polaris Legacy, PM output, Director loops

# After
role_type: str = "unknown"  # agent, user, system
failure_envelope.py: 使用通用 ErrorType 枚举
```

**负责人**: 团队#1 (架构设计)
**工时**: 4小时

---

#### C-02: Cell边界违规
**问题**: 直接导入Cell模块，违反Cell隔离规则
**位置**: `polaris/kernelone/prompts/meta_prompting.py`

**修复方案**:
```python
# Before
from polaris.cells.roles.kernel.public.role_alias import normalize_role_alias

# After - 通过KernelOne Port注入
class RoleAliasNormalizerPort(Protocol):
    def normalize(self, alias: str) -> str: ...

# 在bootstrap层注入实现
def register_role_alias_resolver(resolver: RoleAliasNormalizerPort): ...
```

**负责人**: 团队#1 (架构设计)
**工时**: 2小时

---

#### C-03: 5组循环依赖
**问题**: 模块无法独立部署和测试
**循环对**:
1. `context ↔ llm` (高风险)
2. `fs ↔ storage` (中风险)
3. `llm ↔ process` (低风险)
4. `memory ↔ prompts` (低风险)
5. `process ↔ llm` (低风险)

**修复方案**:
```python
# 为 context↔llm 引入中间抽象接口

# 新增: polaris/kernelone/context/ports.py
class LLMTokenBudgetPort(Protocol):
    """Context模块依赖LLM token budget的接口"""
    def allocate(self, tokens: int) -> BudgetAllocation: ...
    def release(self, allocation_id: str) -> None: ...

# 新增: polaris/kernelone/llm/ports.py
class ContextBudgetPort(Protocol):
    """LLM模块依赖Context的接口"""
    def register_budget_observer(self, observer: LLMTokenBudgetPort) -> None: ...
    def get_remaining_tokens(self) -> int: ...
```

**负责人**: 团队#5 (可维护性)
**工时**: 8小时

---

#### C-04: 3个并发安全漏洞
**问题**: 全局缓存无锁保护，竞态条件

**位置与修复**:

| 位置 | 问题 | 修复方案 | 工时 |
|------|------|----------|------|
| `storage/layout.py:95` | 全局缓存无锁 | 添加读写锁 | 1小时 |
| `context/engine/cache.py` | dict线程不安全 | 添加threading.Lock | 1小时 |
| `effect/tracker.py` | list.append非原子 | 添加threading.Lock | 1小时 |

**修复模板**:
```python
class ThreadSafeCache:
    def __init__(self):
        self._cache: dict[str, Any] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Any:
        with self._lock:
            return self._cache.get(key)

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._cache[key] = value
```

**负责人**: 团队#9 (状态管理)
**工时**: 3小时

---

#### C-05: 50处静默异常吞噬
**问题**: `except Exception: pass` 破坏可追踪性

**高风险位置**:
| 模块 | 数量 | 文件 |
|------|------|------|
| audit | 14 | audit_field.py |
| tools | 5 | runtime_executor.py |
| context | 3 | cache_manager.py, cache.py |
| llm/toolkit | 8 | parsers/*.py |
| process | 5 | async_contracts.py |
| telemetry | 2 | debug_stream.py |

**修复模板**:
```python
# Before (禁止)
except Exception:
    pass

# After (正确)
except SpecificException as e:
    logger.warning(
        "Operation failed in %s: %s",
        context_info,
        str(e),
        exc_info=True  # 记录完整堆栈
    )
    raise  # 或返回错误结果
```

**负责人**: 团队#7 (错误处理)
**工时**: 6小时

---

#### C-06: 极危代码复杂度
**问题**: 多个函数复杂度超过200，难以维护

**高风险文件**:
| 文件 | 复杂度 | 行数 | 建议 |
|------|--------|------|------|
| tool_normalization.py | 309 | 385 | 拆分为5-8个小函数 |
| runtime.py | 258 | 156 | 提取策略模式 |
| plan_parser.py | 167 | - | 重构解析逻辑 |
| config_store.py | 156 | 196 | 拆分为配置加载器 |

**负责人**: 团队#2 (代码质量)
**工时**: 16小时

---

### 2.2 🟠 HIGH 问题 (Week 2)

#### H-01: 三重嵌套超时
**位置**: `polaris/kernelone/llm/engine/executor.py`

**问题**: `_invoke_with_timeout` → `asyncio.wait_for` → `asyncio.to_thread` 三层嵌套

**修复**:
```python
# 统一为两层超时
async def _invoke_with_retry(...):
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(provider_instance.invoke, prompt, model, invoke_cfg),
            timeout=request_timeout  # 只用一层超时
        )
    except asyncio.TimeoutError:
        raise LLMTimeout(...)
```

**预期收益**: 延迟减少35%
**负责人**: 团队#4 (性能)
**工时**: 2小时

---

#### H-02: 缓存I/O浪费
**位置**: `polaris/kernelone/context/engine/cache.py`

**问题**: 每次缓存命中写磁盘，80%命中率=80%写I/O

**修复**:
```python
# 批量异步写入
if hit:
    self._pending_meta_updates[key] = now
    # 定时批量flush
    asyncio.get_event_loop().call_later(5.0, self._flush_meta_updates)
```

**预期收益**: I/O减少90%
**负责人**: 团队#4 (性能)
**工时**: 3小时

---

#### H-03: 同步fsync阻塞
**位置**: `polaris/kernelone/context/cache_manager.py`

**问题**: `_write_json` 同步刷盘，阻塞事件循环

**修复**:
```python
async def _write_json_async(self, path: str, data: dict) -> None:
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, self._write_json_sync, path, data)
```

**预期收益**: 写入延迟从50-200ms降到<5ms
**负责人**: 团队#4 (性能)
**工时**: 2小时

---

#### H-04: 48个全局可写变量
**问题**: 线程不安全，难以测试

**修复**: 改用配置注入
```python
# Before
_STREAM_TIMEOUT = 300.0

# After
@dataclass(frozen=True)
class StreamConfig:
    timeout_sec: float = 300.0
    buffer_size: int = 1000

# 入口点注入
config = StreamConfig(
    timeout_sec=resolve_env_float("llm_stream_timeout_sec"),
)
```

**负责人**: 团队#5 (可维护性)
**工时**: 8小时

---

#### H-05: 42个超大文件
**问题**: 可维护性差，文件超过500行

**TOP 10 需要拆分**:
| 文件 | 行数 | 拆分方案 |
|------|------|----------|
| stream_executor.py | 1583 | 拆分为executor/handler/normalizer/metrics |
| definitions.py | 1160 | 按工具类型拆分为definitions/*.py |
| async_contracts.py | 1127 | 提取到contracts/impl/ |
| workflow/engine.py | 1054 | 拆分为engine/nodes/strategies |
| config_store.py | 985 | 拆分为loaders/validators |

**负责人**: 团队#2 (代码质量)
**工时**: 24小时

---

#### H-06: 8处UTF-8隐式encode
**问题**: `.encode()` 未显式指定encoding

**位置与修复**:
```python
# audit.py (5处)
hashlib.sha256(original_text.encode("utf-8")).hexdigest()[:16]

# prompt_based.py (1处)
f"{tool_name}\n{content}".encode("utf-8")

# meta_prompting.py (1处)
f"{role_token}|{text}|...".encode("utf-8")

# locking.py (1处)
f"{os.getpid()} {time.time()}".encode("utf-8")
```

**负责人**: 团队#10 (UTF-8规范)
**工时**: 1小时

---

#### H-07: 沙箱配置安全风险
**位置**: `polaris/kernelone/llm/config_store.py:497`

**问题**: 默认值从danger-full-access改为safe，但环境变量可覆盖

**修复**:
```python
@field_validator("sandbox")
@classmethod
def validate_sandbox(cls, v: str) -> str:
    if v not in {"safe", "browser", "read-only"}:
        raise ValueError("danger-full-access is not allowed")
    return v
```

**负责人**: 团队#3 (安全性)
**工时**: 1小时

---

#### H-08: 错误传播断裂
**位置**: `polaris/kernelone/tools/runtime_executor.py`

**问题**: 返回dict而非抛出异常，破坏契约

**修复**:
```python
# Before
except Exception as exc:
    return {"ok": False, "error": str(exc)}

# After
except Exception as exc:
    raise ToolExecutionError(
        f"Tool {tool_name} failed: {exc}",
        tool_name=tool_name,
        cause=exc,
        retryable=False
    ) from exc
```

**负责人**: 团队#7 (错误处理)
**工时**: 2小时

---

### 2.3 🟡 MEDIUM 问题 (Week 3-4)

#### M-01: 296处相对导入 → 绝对导入
**负责人**: 团队#5 (可维护性)
**工时**: 4小时

#### M-02: 14个模块完全无测试
**负责人**: 团队#6 (测试覆盖)
**工时**: 16小时

#### M-03: 合并ErrorCategory重复枚举
**负责人**: 团队#8 (API契约)
**工时**: 2小时

#### M-04: 推广错误码规范
**负责人**: 团队#7 (错误处理)
**工时**: 4小时

#### M-05: 补充Protocol的@runtime_checkable
**负责人**: 团队#8 (API契约)
**工时**: 2小时

---

## 3. 重构任务分解

### 团队分配表

| 团队 | 职责 | 主要任务 | 工时 |
|------|------|----------|------|
| #1 | 架构设计 | C-01, C-02, C-03 | 14小时 |
| #2 | 代码质量 | C-06, H-05 | 40小时 |
| #3 | 安全性 | H-07 | 1小时 |
| #4 | 性能 | H-01, H-02, H-03 | 7小时 |
| #5 | 可维护性 | C-03, H-04, M-01 | 20小时 |
| #6 | 测试覆盖 | M-02 | 16小时 |
| #7 | 错误处理 | C-05, H-08, M-04 | 12小时 |
| #8 | API契约 | M-03, M-05 | 4小时 |
| #9 | 状态管理 | C-04 | 3小时 |
| #10 | UTF-8规范 | H-06 | 1小时 |

**总工时**: ~120小时 (约3周/人)

---

## 4. 实施计划

### Week 1: 止血 (P0问题)

```
Day 1-2:
├── 团队#1: 移除业务语义泄漏
├── 团队#1: 修复Cell边界违规
└── 团队#9: 修复3个并发安全漏洞

Day 3-4:
├── 团队#7: 修复50处静默异常
├── 团队#5: 消除context↔llm循环依赖
└── 团队#2: 重构normalize_tool_arguments

Day 5:
├── 团队审查与合并
└── CI门禁建立
```

### Week 2: 性能优化

```
Day 1-2:
├── 团队#4: 消除三重超时嵌套
└── 团队#4: 优化缓存I/O

Day 3-4:
├── 团队#4: 异步化同步fsync
├── 团队#5: 重构全局变量
└── 团队#10: 修复UTF-8违规

Day 5:
├── 团队审查与合并
└── 性能基准测试
```

### Week 3-4: 架构重构

```
Week 3:
├── 团队#2: 拆分超大文件 (TOP 5)
├── 团队#2: 重构极危复杂度函数
└── 团队#5: 消除剩余循环依赖

Week 4:
├── 团队#6: 补充测试
├── 团队#7: 推广错误码规范
├── 团队#8: 合并ErrorCategory
└── 全面回归测试
```

### Month 2: 质量提升

```
Week 5-6:
├── 完成所有超大文件拆分
├── 测试覆盖率提升至35%
├── CI门禁完善
└── 文档更新
```

---

## 5. 验证与测试

### 5.1 单元测试

```bash
# 运行所有测试
pytest polaris/kernelone/ -v --tb=short

# 静默异常检测 (目标: 0)
grep -rn "except Exception:" polaris/kernelone --include="*.py" | grep "pass$" | wc -l

# 循环依赖检测
python -m pycycle polaris/kernelone

# UTF-8合规检测
grep -rn "\.encode()" polaris/kernelone --include="*.py" | grep -v 'encoding=' | wc -l
```

### 5.2 性能基准

```bash
# 建立性能基准
pytest polaris/kernelone/tests/benchmarks/ --benchmark-json=baseline.json

# 验证优化效果
pytest polaris/kernelone/tests/benchmarks/ --benchmark-json=optimized.json
```

### 5.3 CI门禁

```bash
# Ruff检查
ruff check polaris/kernelone --select=E722

# 类型检查
cd polaris && mypy kernelone --ignore-missing-imports

# 复杂度检查
flake8 polaris/kernelone --max-line-length=120 --max-complexity=15
```

---

## 6. 风险与缓解

### 6.1 主要风险

| 风险 | 影响 | 概率 | 缓解措施 |
|------|------|------|----------|
| 重构破坏现有功能 | 高 | 中 | 完整测试覆盖，分阶段发布 |
| 循环依赖修复引入新bug | 中 | 低 | 单元测试+集成测试 |
| 性能优化效果不达预期 | 中 | 低 | 基准测试+渐进式优化 |

### 6.2 回归测试计划

- 每项修改后运行完整测试套件
- 关键路径手动验证
- 性能回归检测

---

## 7. 成功标准

### 7.1 量化指标

| 指标 | 目标 |
|------|------|
| 架构合规评分 | 8.0/10 |
| 代码质量评分 | 7.5/10 |
| 错误韧性评分 | 8.0/10 |
| 测试覆盖率 | 35% |
| CRITICAL问题 | 0个 |
| 静默异常 | 0个 |
| 循环依赖组数 | 0组 |

### 7.2 质量门禁

- [ ] ruff E722检查通过
- [ ] mypy类型检查通过
- [ ] flake8复杂度检查通过
- [ ] pytest测试通过率100%
- [ ] 性能基准不下降

---

## 8. 附录

### A. 文件清单

```
docs/blueprints/
├── KERNELONE_REFACTORING_BLUEPRINT_2026-03-26.md  # 本文档
├── audit_summary.md                                # 审计汇总
├── critical_fixes.md                              # CRITICAL修复详情
├── performance_optimization.md                     # 性能优化方案
├── concurrency_fixes.md                           # 并发安全修复
└── test_improvement_plan.md                       # 测试提升计划
```

### B. 参考文档

- ACGA 2.0 架构规范
- KernelOne 架构规范
- CLAUDE.md 强制规则

---

**文档状态**: 待执行
**下次审查**: 2026-04-02
**负责人**: 首席架构师
