# ContextOS 统一上下文架构 - 执行计划

**版本**: 1.0
**日期**: 2026-03-30
**蓝图**: `CONTEXTOS_UNIFIED_CONTEXT_ARCHITECTURE_20260330.md`
**团队**: `TEAM_COMPOSITION_CONTEXTOS_20260330.md`

---

## 1. 执行阶段概览

| Phase | 内容 | 周期 | 负责人 |
|-------|------|------|--------|
| Phase 0 | 环境准备与基线收集 | Week 0 | 全员 |
| Phase 1 | P0 级重构（元数据+双轨消除） | Week 1-2 | 首席重构 A |
| Phase 2 | P1 级重构（压缩+类型统一） | Week 3-4 | 首席重构 B |
| Phase 3 | P2 级重构（摘要+接口） | Week 5-6 | 首席重构 C |
| Phase 4 | 集成与端到端验证 | Week 7-8 | 首席重构 D |
| Phase 5 | 质量门禁与 CR | Week 9-10 | 质量门禁组 |

---

## 2. Phase 0: 环境准备（Week 0）

### 2.1 创建工作分支

```bash
git checkout -b feature/contextos-unified-architecture
git push -u origin feature/contextos-unified-architecture
```

### 2.2 基线测试收集

```bash
# 收集当前测试状态
pytest tests/roles/kernel/internal/test_tool_loop_controller.py --collect-only -q
pytest tests/roles/kernel/internal/test_context_gateway.py --collect-only -q
pytest tests/kernelone/context/ --collect-only -q

# 记录基线
pytest --tb=no -q 2>&1 | tee baseline_test_report.txt
```

### 2.3 基线代码指标

| 指标 | 当前值 | 目标值 |
|------|--------|--------|
| tool_loop_controller.py LOC | ~670 | ~750 (新增) |
| context_gateway.py LOC | ~1034 | ~1100 (新增) |
| llm_caller.py LOC | ~2792 | 不变 |
| P0 测试覆盖 | 0 | 100% |
| 类型错误 (mypy) | 待测量 | 0 |

---

## 3. Phase 1: P0 级重构（Week 1-2）

### 3.1 P0-1: 消除双轨制

**文件**: `polaris/cells/roles/kernel/internal/tool_loop_controller.py`

**改动点**:

| 行号 | 改动内容 | 任务 |
|------|----------|------|
| ~78-94 | `__post_init__()` 消除 request.history 回退 | Task-005 |
| ~96-125 | `_extract_snapshot_history()` 返回 `list[ContextEvent]` | Task-002 |
| ~55-71 | `_history` 类型改为 `list[ContextEvent]` | Task-003 |
| ~206-228 | `append_tool_cycle()` 使用 ContextEvent | Task-004 |

**具体代码改动**:

```python
# Line 54-71: 新增 ContextEvent 数据类
@dataclass(frozen=True, slots=True)
class ContextEvent:
    """标准上下文事件类型，替代 (role, content) 元组"""
    event_id: str
    role: str
    content: str
    sequence: int
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_tuple(self) -> tuple[str, str]:
        return (self.role, self.content)

# Line 55: _history 类型变更
# Before: _history: list[tuple[str, str]] = field(default_factory=list)
# After:
_history: list[ContextEvent] = field(default_factory=list)

# Line 96-125: _extract_snapshot_history() 返回 ContextEvent 列表
def _extract_snapshot_history(self) -> list[ContextEvent] | object:
    ...

# Line 78-94: __post_init__() 强制要求 snapshot
def __post_init__(self) -> None:
    self._pending_user_message = str(self.request.message or "")
    snapshot_history = self._extract_snapshot_history()
    if snapshot_history is self._NO_SNAPSHOT:
        raise ValueError(
            "ToolLoopController requires context_os_snapshot. "
            "request.history fallback is deprecated."
        )
    self._history = snapshot_history
    self._seed_tool_results(self.request.tool_results)
```

### 3.2 P0-2: 元数据保留

**同一文件额外改动**:

| 行号 | 改动内容 | 任务 |
|------|----------|------|
| ~231-245 | `append_tool_result()` 使用 ContextEvent | Task-004 |
| ~570-602 | `_build_cycle_signature()` 适配 ContextEvent | Task-004 |

---

## 4. Phase 2: P1 级重构（Week 3-4）

### 4.1 P1-1: 统一压缩策略

**文件**: `polaris/cells/roles/kernel/internal/context_gateway.py`

**改动点**:

| 行号 | 改动内容 | 任务 |
|------|----------|------|
| ~50-55 | 新增 `ContextOverflowError` | Task-007 |
| ~697-752 | `apply_compression()` 消除双轨压缩 | Task-008, Task-009 |
| ~282-290 | 移除 `state_first_mode_active` 跳过逻辑 | Task-009 |

### 4.2 P1-2: ContextRequest 统一

**文件**:
- `polaris/kernelone/context/contracts.py`
- `polaris/cells/roles/kernel/internal/context_gateway.py`

**改动点**:

| 位置 | 改动内容 | 任务 |
|------|----------|------|
| contracts.py ~182 | `TurnEngineContextRequest` 补充完整字段 | Task-010 |
| context_gateway.py ~22-27 | 删除局部定义，改为导入 | Task-011 |

---

## 5. Phase 3: P2 级重构（Week 5-6）

### 5.1 P2-1: 快照摘要修复

**文件**: `polaris/cells/roles/kernel/internal/context_gateway.py`

**改动点**:

| 行号 | 改动内容 | 任务 |
|------|----------|------|
| ~551-595 | `_format_context_os_snapshot()` 添加 verbosity 参数 | Task-013 |

### 5.2 P2-2: 延迟序列化接口

**文件**: `polaris/cells/roles/kernel/internal/llm_caller.py`

**新增**:

```python
# 新增 ProviderFormatter Protocol (约 Line 105)
class ProviderFormatter(Protocol):
    """Provider 特异性格式化接口"""

    def format_messages(
        self,
        messages: list[ContextEvent]
    ) -> list[dict[str, str]]:
        ...

    def format_tool_result(
        self,
        tool_name: str,
        result: dict[str, Any],
    ) -> str:
        ...
```

---

## 6. Phase 4: 集成（Week 7-8）

### 6.1 接口兼容性检查

```bash
# 检查所有导入是否正常
python -c "
from polaris.cells.roles.kernel.internal.tool_loop_controller import ToolLoopController, ContextEvent
from polaris.cells.roles.kernel.internal.context_gateway import RoleContextGateway
from polaris.kernelone.context.contracts import TurnEngineContextRequest
print('All imports OK')
"
```

### 6.2 端到端测试

```bash
# 运行完整角色执行流程
pytest tests/roles/kernel/internal/test_turn_engine.py -v --tb=short
pytest tests/roles/kernel/internal/test_kernel.py -v --tb=short
```

---

## 7. Phase 5: 质量门禁（Week 9-10）

### 7.1 Ruff 检查

```bash
ruff check polaris/kernelone/context/context_os/
ruff check polaris/cells/roles/kernel/internal/tool_loop_controller.py
ruff check polaris/cells/roles/kernel/internal/context_gateway.py
ruff format polaris/kernelone/context/context_os/
ruff format polaris/cells/roles/kernel/internal/tool_loop_controller.py
ruff format polaris/cells/roles/kernel/internal/context_gateway.py
```

### 7.2 Mypy 检查

```bash
mypy polaris/kernelone/context/context_os/runtime.py
mypy polaris/kernelone/context/context_os/models.py
mypy polaris/cells/roles/kernel/internal/tool_loop_controller.py
mypy polaris/cells/roles/kernel/internal/context_gateway.py
```

### 7.3 完整测试套件

```bash
pytest tests/kernelone/context/ tests/roles/kernel/internal/ -v --tb=short
```

---

## 8. 里程碑

| 里程碑 | 日期 | 验收条件 |
|--------|------|----------|
| M1: P0 重构完成 | Week 2 结束 | P0 测试 100% 通过 |
| M2: P1 重构完成 | Week 4 结束 | P1 测试 100% 通过 |
| M3: P2 重构完成 | Week 6 结束 | P2 测试 100% 通过 |
| M4: 集成完成 | Week 8 结束 | 端到端测试通过 |
| M5: 最终验收 | Week 10 结束 | 所有质量门禁通过 |

---

## 9. 风险应对

### 9.1 向后兼容性风险

**场景**: 外部代码依赖旧的 `(role, content)` 元组接口

**应对**:
1. 保留 `ContextEvent.to_tuple()` 方法
2. 在过渡期添加 `DEPRECATION_WARNING` 日志
3. 使用 feature flag 控制行为切换

### 9.2 性能风险

**场景**: `ContextEvent` 对象创建开销高于元组

**应对**:
1. 使用 `__slots__` 优化内存
2. 基准测试对比

### 9.3 压缩策略收紧风险

**场景**: 某些现有功能依赖超限上下文

**应对**:
1. 使用 `verbosity=debug` 模式验证
2. 分阶段收紧限制

---

## 10. 附录：文件改动清单

```
polaris/kernelone/context/contracts.py
  + TurnEngineContextRequest 补充字段

polaris/cells/roles/kernel/internal/tool_loop_controller.py
  + ContextEvent 数据类
  ~ _history: list[tuple] → list[ContextEvent]
  ~ __post_init__() 消除回退
  ~ _extract_snapshot_history() 返回 ContextEvent 列表
  ~ append_tool_cycle() 使用 ContextEvent
  ~ append_tool_result() 使用 ContextEvent

polaris/cells/roles/kernel/internal/context_gateway.py
  + ContextOverflowError
  + _format_context_os_snapshot() verbosity 参数
  + ProviderFormatter Protocol
  ~ _apply_compression() 统一压缩
  ~ 导入改为从 contracts.py

polaris/cells/roles/kernel/internal/llm_caller.py
  + NativeProviderFormatter
  + AnnotatedProviderFormatter
```
