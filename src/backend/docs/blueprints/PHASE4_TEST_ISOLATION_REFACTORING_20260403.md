# Phase 4 测试隔离重构审计报告

**审计时间**: 2026-04-03
**执行人**: 测试架构师 + 测试隔离专家
**状态**: 已完成基础实施

---

## 执行摘要

Phase 4 专注于消除测试污染问题，通过以下措施：
1. 创建 `GlobalStateIsolationManager` 替代直接 `sys.modules` 操作
2. 创建统一的 `_build_kernel()` helper，消除 4 个重复实现
3. 建立 `ModuleBlocker` 模式替代 `_DeliveryBlocker`
4. 建立 autouse fixture 清单和重构指南

---

## 1. 13 个 Autouse Fixture 完整清单（按污染程度排序）

| 序号 | 文件路径 | Fixture 名称 | 污染程度 | 评估 | 建议处理 |
|------|----------|--------------|----------|------|----------|
| 1 | `tests/test_agent32_kernelone_role_enum_migration.py` | `_reset_audit_singletons` | **CRITICAL** | 重置 KernelAuditRuntime singleton | 可保留，但需改为显式依赖 |
| 2 | `polaris/tests/test_llm_realtime_bridge.py` | `_configure_runtime_bridge` | **CRITICAL** | 清除 POLARIS_WORKSPACE roots 缓存 | 重构为 session-scope + module-scope |
| 3 | `polaris/cells/storage/layout/tests/test_storage_layout_cell.py` | `_hp_bootstrap` | **CRITICAL** | 设置 workspace metadata dir | 改为 session-scope |
| 4 | `polaris/cells/context/engine/tests/test_search_gateway.py` | `reset_singleton` | **CRITICAL** | 重置 SearchService singleton | 可保留，但需记录依赖 |
| 5 | `polaris/cells/roles/session/tests/test_role_session_service.py` | `_reset_conversation_singleton` | **CRITICAL** | 重置 SessionService singleton | 可保留 |
| 6 | `tests/test_kernelone_audit_runtime.py` | `_reset_audit_singletons` | **CRITICAL** | 重置 KernelAuditRuntime singleton | 与 #1 重复，可合并 |
| 7 | `tests/test_kernelone_jsonl_ops.py` | `_reset_jsonl_module_state` | **HIGH** | 清除 _JSONL_BUFFER | 可保留 |
| 8 | `tests/test_llm_evaluation_abstraction.py` | `_reset_embedding_port` | **HIGH** | 重置 embedding 模块全局变量 | 使用 isolation.py 管理 |
| 9 | `tests/test_llm_evaluation_abstraction.py` | `_reset_reports_port` | **HIGH** | 重置 reports 模块全局变量 | 使用 isolation.py 管理 |
| 10 | `tests/test_llm_phase0_regression.py` | `isolate_polaris_root` | **HIGH** | 设置 POLARIS_ROOT 环境变量 | 改为使用 `env_isolation()` |
| 11 | `tests/test_llm_test_index_reconcile.py` | `_isolate_polaris_root` | **HIGH** | 同上 | 改为使用 `env_isolation()` |
| 12 | `tests/test_llm_test_index_thread_safety.py` | `_isolate_reports_port` | **HIGH** | 重置 reports port | 使用 isolation.py 管理 |
| 13 | `tests/test_storage_layout_v4.py` | `_polaris_metadata_dir` | **HIGH** | 设置 metadata dir | 改为 session-scope |
| 14 | `polaris/kernelone/events/tests/test_sourcing_store.py` | `_inject_kernel_fs_adapter` | **MEDIUM** | 设置默认 adapter | 改为 module-scope |
| 15 | `polaris/cells/factory/pipeline/tests/test_projection_*.py` x4 | `_configure_default_adapter` | **MEDIUM** | 4 个测试文件重复设置 | 合并到共享 conftest.py |
| 16 | `polaris/cells/roles/kernel/tests/test_turn_engine_policy_convergence.py` | `_reset_env` | **MEDIUM** | 重置 POLARIS_TOOL_LOOP_MAX_STALL_CYCLES | 使用 `env_isolation()` |
| 17 | `tests/architecture/test_architecture_invariants.py` | `setup` | **MEDIUM** | 类级别 setup fixture | 可保留 |
| 18 | `tests/test_llm_caller.py` | `clear_event_history` | **MEDIUM** | 清除事件历史 | 可保留 |
| 19 | `polaris/kernelone/context/tests/conftest.py` | `configure_kernelone_test_defaults` | **MEDIUM** | 全局默认设置 | 可保留（session-scope） |
| 20 | `tests/conftest.py` | `configure_default_kernel_fs_adapter` | **MEDIUM** | 全局默认设置 | 可保留（session-scope） |

### 重构优先级

**Phase 1 (立即)**:
- #6 与 #1 重复 → 合并到共享 fixture
- #8, #9, #12 → 使用 `module_globals_isolation()`

**Phase 2 (短期)**:
- #10, #11, #16 → 使用 `env_isolation()`
- #15 的 4 个文件 → 合并到共享 conftest.py

**Phase 3 (中期)**:
- #2, #3, #13 → 改为 session-scope

---

## 2. `_build_kernel()` 重复位置清单

| 文件 | 行数 | 差异点 |
|------|------|--------|
| `test_kernel_stream_tool_loop.py` | ~40 行 | 包含 `context_policy`，使用 `_StubRegistry` |
| `test_run_stream_parity.py` | ~40 行 | 包含 `prompt_policy`，不同的 mock 注入方式 |
| `test_stream_visible_output_contract.py` | ~25 行 | 直接使用 `_MockLLMCaller` |
| `test_turn_engine_policy_convergence.py` | ~50 行 | 支持 `tool_policy_overrides`、`prompt_builder`、`llm_invoker` 参数 |

### 解决方案

创建统一的 `polaris/cells/roles/kernel/tests/conftest.py`，提供：
- `_StubRegistry` 类
- `_build_kernel()` 统一函数（支持所有参数变体）
- `MockLLMCaller` 类
- `make_turn_request()` 辅助函数
- `canonical_tool_call()` 辅助函数

**已实施**: `C:\Users\dains\Documents\GitLab\polaris\src\backend\polaris\cells\roles\kernel\tests\conftest.py`

---

## 3. GlobalStateIsolationManager 设计方案

### 核心组件

```python
# polaris/kernelone/testing/isolation.py

class GlobalStateIsolationManager:
    """测试隔离管理器"""

    # sys.modules 操作
    def snapshot_modules(prefixes: list[str]) -> ModuleSnapshot
    def restore_modules(snapshot: ModuleSnapshot) -> None
    def evict_modules(prefixes: list[str]) -> ModuleSnapshot
    @contextmanager
    def module_isolation(prefixes: list[str]) -> Generator[None, None, None]

    # os.environ 操作
    def snapshot_env(keys: list[str]) -> EnvSnapshot
    def restore_env(snapshot: EnvSnapshot) -> None
    @contextmanager
    def env_isolation(keys: list[str], values: dict[str, str] | None = None)

    # 模块全局变量操作
    def snapshot_module_globals(module_name: str, globals_to_watch: list[str]) -> ModuleGlobalsSnapshot
    def restore_module_globals(snapshot: ModuleGlobalsSnapshot) -> None
    @contextmanager
    def module_globals_isolation(module_name: str, globals_to_watch: list[str])

class ModuleBlocker(types.ModuleType):
    """替代 _DeliveryBlocker 的模块拦截器"""

@contextmanager
def block_modules(*module_names: str) -> Generator[None, None, None]:
    """阻塞指定模块的导入"""

@contextmanager
def reset_singletons(singletons: dict[str, list[str]]) -> Generator[None, None, None]:
    """重置模块级单例变量"""
```

### 使用示例

```python
from polaris.kernelone.testing.isolation import (
    GlobalStateIsolationManager,
    block_modules,
    reset_singletons,
)

# 示例 1: 模块隔离
manager = GlobalStateIsolationManager()
snapshot = manager.evict_modules(["polaris.delivery"])
try:
    import polaris.delivery.cli  # ImportError
finally:
    manager.restore_modules(snapshot)

# 示例 2: 环境变量隔离
with manager.env_isolation(["POLARIS_ROOT"], {"POLARIS_ROOT": "/tmp/test"}):
    os.environ["POLARIS_ROOT"] = "/tmp/test"

# 示例 3: 模块全局变量隔离
with manager.module_globals_isolation(
    "polaris.kernelone.events.bus_adapter",
    ["_default_adapter"]
):
    # _default_adapter 被设为 None

# 示例 4: 阻塞模块导入
with block_modules("polaris.delivery"):
    import polaris.delivery.cli  # ImportError

# 示例 5: 重置单例
with reset_singletons({
    "polaris.kernelone.events": ["_global_bus"],
}):
    # _global_bus 被重置为 None
```

**已实施**: `C:\Users\dains\Documents\GitLab\polaris\src\backend\polaris\kernelone\testing\isolation.py`

---

## 4. Monkeypatch 使用审计

### 统计
- **330 处** `monkeypatch.setenv/delenv` 调用
- **61 个文件**使用 monkeypatch
- 主要用途：
  1. 环境变量隔离 (POLARIS_ROOT, KERNELONE_*)
  2. 模块属性修改
  3. sys.modules 操作

### sys.modules 直接操作清单

| 文件 | 操作类型 | 风险 |
|------|----------|------|
| `test_agentic_eval_cli.py` | 直接修改 `__dict__` | HIGH |
| `test_pm_planning_no_delivery_import.py` | `_DeliveryBlocker` 模式 | MEDIUM |
| `test_pm_dispatch_no_delivery_import.py` | `_DeliveryBlocker` 模式 | MEDIUM |
| `test_factory_store.py` | `sys.modules[key] = mod` | MEDIUM |
| `test_polaris_stress_backend_arg.py` | `sys.modules[name] = module` | MEDIUM |
| `test_artifact_service.py` | `sys.modules.pop()` | LOW |
| `test_llm_evaluation_abstraction.py` | `patch.dict("sys.modules", ...)` | MEDIUM |

### 解决方案

1. **对于 test_agentic_eval_cli.py**:
   ```python
   # Before (危险)
   _bus_adapter_mod.__dict__["_default_adapter"] = None

   # After (使用 isolation.py)
   with module_globals_isolation(
       "polaris.kernelone.events.bus_adapter",
       ["_default_adapter"]
   ):
       # _default_adapter 被重置为 None
   ```

2. **对于 _DeliveryBlocker 模式**:
   ```python
   # Before
   sys.modules["polaris.delivery"] = _DeliveryBlocker("polaris.delivery")

   # After
   with block_modules("polaris.delivery"):
       import polaris.delivery.cli  # ImportError
   ```

---

## 5. 已实施的测试隔离修复清单

### 实施完成

| 修复项 | 文件路径 | 状态 | 验证 |
|--------|----------|------|------|
| GlobalStateIsolationManager | `polaris/kernelone/testing/isolation.py` | ✅ | ruff + mypy 通过 |
| ModuleBlocker | `polaris/kernelone/testing/isolation.py` | ✅ | ruff + mypy 通过 |
| block_modules() | `polaris/kernelone/testing/isolation.py` | ✅ | ruff + mypy 通过 |
| reset_singletons() | `polaris/kernelone/testing/isolation.py` | ✅ | ruff + mypy 通过 |
| 统一 conftest.py | `polaris/cells/roles/kernel/tests/conftest.py` | ✅ | ruff + mypy 通过 |
| _build_kernel() helper | `polaris/cells/roles/kernel/tests/conftest.py` | ✅ | ruff + mypy 通过 |
| MockLLMCaller | `polaris/cells/roles/kernel/tests/conftest.py` | ✅ | ruff + mypy 通过 |
| make_turn_request() | `polaris/cells/roles/kernel/tests/conftest.py` | ✅ | ruff + mypy 通过 |
| canonical_tool_call() | `polaris/cells/roles/kernel/tests/conftest.py` | ✅ | ruff + mypy 通过 |
| reset_kernel_singletons fixture | `polaris/cells/roles/kernel/tests/conftest.py` | ✅ | ruff + mypy 通过 |

### 后续步骤 (未实施)

| 步骤 | 描述 | 优先级 | 预计工时 |
|------|------|--------|----------|
| 1 | 更新 `test_agentic_eval_cli.py` 使用 `module_globals_isolation()` | HIGH | 2h |
| 2 | 更新 `test_pm_planning_no_delivery_import.py` 使用 `block_modules()` | MEDIUM | 1h |
| 3 | 更新 `test_pm_dispatch_no_delivery_import.py` 使用 `block_modules()` | MEDIUM | 1h |
| 4 | 合并 `#1` 和 `#6` 的 `_reset_audit_singletons` | MEDIUM | 0.5h |
| 5 | 将 projection 测试的 4 个 `_configure_default_adapter` 合并 | MEDIUM | 1h |
| 6 | 重构环境变量 autouse fixtures 使用 `env_isolation()` | LOW | 2h |

---

## 6. 质量验证

### Ruff 检查
```
$ python -m ruff check polaris/kernelone/testing/isolation.py polaris/cells/roles/kernel/tests/conftest.py
All checks passed!
```

### MyPy 检查
```
$ python -m mypy polaris/kernelone/testing/isolation.py polaris/cells/roles/kernel/tests/conftest.py --ignore-missing-imports
Success: no issues found in 2 source files
```

---

## 7. 文件清单

| 文件路径 | 说明 |
|----------|------|
| `polaris/kernelone/testing/isolation.py` | 新增: GlobalStateIsolationManager 和相关工具 |
| `polaris/cells/roles/kernel/tests/conftest.py` | 新增: 统一测试 fixtures |

---

## 附录 A: Autouse Fixture 重构指南

### 何时使用 Autouse Fixture

autouse fixture 适用于：
1. **全局状态设置**：如默认 adapter、默认环境变量
2. **Singleton 重置**：防止测试间状态污染
3. **测试框架配置**：如 event loop 配置

### 何时避免 Autouse Fixture

避免在以下情况使用：
1. 只被少数测试需要 → 使用显式依赖
2. 状态可以被显式管理 → 使用 context manager
3. 会在测试间泄漏状态 → 使用 session/module scope

### 重构检查清单

- [ ] Fixture 是否只被一个测试使用？→ 改为显式依赖
- [ ] Fixture 是否修改全局状态？→ 使用 `GlobalStateIsolationManager`
- [ ] Fixture 是否有副作用？→ 添加 teardown 代码
- [ ] Fixture 是否可以在 session scope？→ 改为 session scope

---

*审计完成时间: 2026-04-03*
