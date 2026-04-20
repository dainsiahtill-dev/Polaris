# Polaris 废弃路径清理与架构收敛蓝图

**状态**: Active Implementation Plan  
**日期**: 2026-04-17  
**适用范围**: `polaris/kernelone/tool_execution/`, `polaris/cells/llm/evaluation/`, `polaris/cells/director/*/public/`, `polaris/infrastructure/di/`, `polaris/delivery/http/middleware/`, `polaris/bootstrap/assembly.py`

---

## 1. 背景

基于对 `polaris/` 全仓的深度审计，发现以下结构性债务：

- **多套工具执行路径**: `executor.py` / `executor_core.py` 已废弃但仍在 `__init__.py` 导出
- **多套 Benchmark 模型**: `benchmark_models.py` / `agentic_benchmark.py` / `deterministic_judge.py` 已废弃，但 `public/service.py` 和跨 Cell 测试仍直接引用
- **跨 Cell 内部边界导入**: `infrastructure/`、`delivery/`、`bootstrap/` 直接导入 `cell/internal/`，违反 Graph 最小自治边界原则
- **Feature Flags 双轨**: `feature_flags.py`（dead code）与 `runtime_feature_flags.py` 并存
- **Director 废弃公开契约**: `runtime/public/contracts.py` 和 `delivery/public/contracts.py` 是纯 re-export 垫片

---

## 2. 核心架构裁决

### 2.1 废弃模块处理原则

| 废弃模块 | 处理策略 | 理由 |
|---|---|---|
| `kernelone/tool_execution/executor.py` | **从 `__init__.py` 移除导出**，物理文件保留（零引用后可删） | 所有活跃引用已通过 `AgentAccelToolExecutor` 收敛 |
| `kernelone/tool_execution/executor_core.py` | 同上 | 同上 |
| `cells/llm/evaluation/internal/benchmark_models.py` | **变为内部向后兼容垫片**，重导出 canonical `unified_models` 并保留旧名别名 | 外部消费者（含跨 Cell 测试）仍通过旧名引用，需无损迁移窗口 |
| `cells/llm/evaluation/internal/agentic_benchmark.py` | **标记为仅内部测试兼容**，`public/service.py` 改为引用 canonical `unified_runner` | 避免 dual truth |
| `cells/llm/evaluation/internal/deterministic_judge.py` | 同上，改为引用 canonical `unified_judge` | 同上 |
| `cells/director/runtime/public/contracts.py` | **删除物理文件**，`__init__.py` 清空为占位符 | 零活跃外部引用，纯 re-export 垫片 |
| `cells/director/delivery/public/contracts.py` | 同上 | 同上 |

### 2.2 跨 Cell 边界修复策略（Port/Protocol 优先）

| 违规点 | 当前状态 | 修复方案 | 难度 |
|---|---|---|---|
| `infrastructure/di/factories.py` → `kernel/internal/metrics` | 直接导入 `MetricsCollector` 并操作私有属性 `_instance` | 改为通过 `kernel.public.service` 导入，使用已暴露的 `reset_metrics_collector_for_test()` | 低 |
| `infrastructure/di/factories.py` → `kernel/internal/constitution_adaptor` | 直接导入私有 `_global_registry` | 改为调用 `kernel.public.service.reset_role_action_registry_for_test()` | 低 |
| `infrastructure/llm/providers/openai_compat_provider.py` → `kernel/internal/context_gateway` | 内部实例化 `RoleContextGateway` | **短期**：改为 lazy import `kernel.public.service.RoleContextGateway`；**长期**：通过 DI 注入 `ContextBuilderPort`（需另开 ADR） | 中 |
| `delivery/http/middleware/metrics.py` → `kernel/internal/metrics` | 导入未公开的 `get_metrics_collector` | 在 `kernel.public.service` 新增 `get_kernel_metrics_collector()`，middleware 改为 public 导入 | 低 |
| `bootstrap/assembly.py` → `archive/run_archive/internal/archive_sink` | 直接实例化 `ArchiveSink` | 在 `archive.run_archive.public.service` 新增 `create_archive_sink(bus)` factory，bootstrap 调用 factory | 低 |

---

## 3. 实施计划

### Slice A: 已完成（2026-04-17）
- ✅ P0: `executor.py` / `executor_core.py` 从 `__init__.py` 移除
- ✅ P3: 删除 `feature_flags.py`（dead code），内联全部 4 处引用
- ✅ P4: 删除 Director 废弃契约垫片，清空 `__init__.py`

### Slice B: Benchmark 模块迁移（P1）

1. **`public/service.py` 切 canonical**
   - `AgenticBenchmarkCase` → `UnifiedBenchmarkCase`（canonical）
   - `AgenticJudgeConfig` → `JudgeConfig`（canonical）
   - `judge_agentic_case` → `unified_judge.judge_case`（canonical）
   - 旧类名作为 **TypeAlias** 保留在 `public/service.py` 的 `__all__` 中，实现无损兼容

2. **废弃内部模块收口**
   - `benchmark_models.py` 改为从 `unified_models` 重导出，并追加旧名别名
   - `agentic_benchmark.py` 改为从 `unified_runner` 重导出
   - `deterministic_judge.py` 改为从 `unified_judge` 重导出

3. **跨 Cell 测试修复**
   - `tests/test_llm_*.py` 中所有 `from ...internal.benchmark_models import` 改为 `from ...public.service import`
   - 同一 Cell 内部测试（`cells/llm/evaluation/tests/`）保留内部引用，不受影响

4. **物理删除**
   - 确认 48 小时无 regression 后，删除 `benchmark_models.py` / `agentic_benchmark.py` / `deterministic_judge.py`

### Slice C: 跨 Cell 边界修复（P2）

1. **Violation 1 & 2（factories.py）**
   - 移除两处 `internal/` 导入
   - 改为 `from polaris.cells.roles.kernel.public.service import MetricsCollector, reset_metrics_collector_for_test, reset_role_action_registry_for_test`
   - 删除对 `_global_registry` 和 `_instance` 的私有属性访问

2. **Violation 3（openai_compat_provider.py）**
   - 将 `RoleContextGateway` 的实例化改为从 `kernel.public.service` lazy import
   - 添加 TODO 注释，指向未来 DI Port 注入方案

3. **Violation 4（metrics middleware）**
   - 在 `kernel.public.service` 新增 `get_kernel_metrics_collector() -> MetricsCollector | None`
   - middleware 改为 `from polaris.cells.roles.kernel.public.service import get_kernel_metrics_collector`

4. **Violation 5（bootstrap assembly）**
   - 在 `archive.run_archive.public.service` 新增 `create_archive_sink(bus: MessageBus) -> ArchiveSink`
   - `bootstrap/assembly.py` 改为调用 factory，移除 `internal/` 导入

---

## 4. 验收标准

1. `ruff check` 在修改文件上零 error
2. `mypy` 在修改文件上零 error
3. `pytest` 相关测试集通过：
   - `polaris/kernelone/context/tests/`（已验证）
   - `polaris/cells/roles/kernel/tests/`（已验证）
   - `tests/test_llm_tool_calling_matrix.py`（已验证）
   - `polaris/cells/llm/evaluation/tests/`（迁移后验证）
4. 无新的跨 Cell `internal/` 导入引入

---

## 5. 风险与回滚

- **风险**: `public/service.py` 的 TypeAlias 可能在静态类型检查中产生循环引用
- **缓解**: TypeAlias 使用 `if TYPE_CHECKING` 保护
- **回滚**: 若 regression 无法快速修复，恢复 `benchmark_models.py` 的原始实现，保留 canonical 双轨并行
