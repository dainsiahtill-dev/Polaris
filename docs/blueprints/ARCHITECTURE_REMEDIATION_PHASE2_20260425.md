# Architecture Remediation Blueprint - Phase 2 (Remainder)

**文档状态**: Draft
**创建日期**: 2026-04-25
**目标**: 完成 Phase 1 剩余的 6 项架构修复任务
**范围**: `polaris/delivery/`, `polaris/kernelone/`, `polaris/application/`, `polaris/infrastructure/`, `docs/graph/`

---

## 1. 背景与现状

Phase 1 (PRINCIPAL_ARCHITECT_17_EXPERT_FULL_REPO_EXECUTION_BLUEPRINT_20260425.md) 已完成以下工作：
- Phase 0 架构门禁创建（3 个 fence 测试）
- Wave 1-3 专家工作流执行（17/17 完成）
- ContextOS GAP-1 关键修复（TruthLog mutable reference）
- 507 个架构测试通过

### 遗留问题汇总

| # | 问题 | 优先级 | 依赖关系 |
|---|------|--------|---------|
| 1 | Delivery 导入重构 | P0 | 无 |
| 2 | KernelOne Port 引导 | P0 | 无 |
| 3 | ContextOS GAP-2 | P1 | 2 |
| 4 | Catalog 治理不匹配 | P1 | 无 |
| 5 | SessionArtifactStore 越界 | P2 | 3 |
| 6 | Infrastructure 模块拆分 | P2 | 无 |

---

## 2. 详细问题分析

### 2.1 Delivery 导入重构 (P0)

**问题描述**: `polaris/delivery/` 下 14 个文件直接从 `cells.*.internal` 导入，违反 ACGA 2.0 的 public/internal 隔离原则。

**违规文件列表** (来自 `test_delivery_internal_import_fence.py`):
```
polaris/delivery/cli/pm/chief_engineer_llm_tools.py
polaris/delivery/cli/director/director_llm_tools.py
polaris/delivery/cli/terminal_console.py
polaris/delivery/cli/director/console_host.py
polaris/delivery/http/routers/test_role_session_context_memory_router.py
polaris/delivery/http/routers/test_agent_router_canonical.py
polaris/delivery/cli/director/audit_decorator.py
polaris/delivery/http/routers/runtime.py
polaris/delivery/cli/audit/audit/handlers.py
polaris/delivery/http/middleware/metrics.py
polaris/delivery/cli/director/tests/test_orchestrator_e2e_integration.py
polaris/delivery/cli/director/tests/test_console_host_e2e_smoke.py
polaris/delivery/cli/tests/test_terminal_console.py
polaris/delivery/cli/pm/orchestration_engine.py
```

**解决方案**: 这些是 Phase 0 freeze the bleed 时确认的 baseline 违规。需要通过 application layer facade 进行重构：
1. 已创建的 facade: `polaris/application/runtime_admin.py`, `storage_admin.py`, `session_admin.py`
2. Delivery 应通过 `polaris.application.*` 而非 `polaris.cells.*.internal` 访问

**验收标准**:
- [ ] 所有 delivery 文件通过 application layer facade 访问 Cell 能力
- [ ] Phase 0 fence 测试仍然通过（baseline 不增长）

### 2.2 KernelOne Port 引导 (P0)

**问题描述**: Phase 1 创建了 4 个 Port 协议，但 bootstrap 未正式接入，架构层无法生效。

**已创建的 Port 协议**:
```
polaris/kernelone/ports/storage.py       # IFileSystemAdapterFactory
polaris/kernelone/ports/provider_registry.py  # IProviderRegistryPort
polaris/kernelone/ports/container.py      # IContainerPort
polaris/kernelone/ports/layout.py         # ILayoutResolverPort
```

**解决方案**:
1. 找到 bootstrap 入口 (`polaris/bootstrap/`)
2. 在启动流程中注入 port 实现
3. 移除 migration fallback，正式启用 port 模式

**验收标准**:
- [ ] Bootstrap 启动时注入 4 个 port 实现
- [ ] KernelOne release gate 仍通过

### 2.3 ContextOS GAP-2 (P1)

**问题描述**: `runtime._take_snapshot()` 直接访问 `_entries` 引用，可能导致状态污染。

**问题位置**: `polaris/kernelone/context/context_os/runtime.py`

**解决方案**:
1. 分析 `_take_snapshot()` 的具体实现
2. 替换直接引用为安全的复制或只读访问
3. 添加测试验证隔离性

**验收标准**:
- [ ] `_take_snapshot()` 不返回可变引用
- [ ] ContextOS hardening 测试通过

### 2.4 Catalog 治理不匹配 (P1)

**问题描述**: Catalog gate 发现 11 个 mismatch，影响 Cell graph 完整性。

**mismatch 类型**:
- `owned_path_not_contained`: manifest 路径与 catalog 不一致
- `catalog_not_superset`: catalog 声明的依赖 manifest 中未声明

**影响 Cell**:
- `orchestration.workflow_engine`: owned_paths mismatch
- `orchestration.workflow_runtime`: depends_on mismatch (2个)
- 等共 11 个 mismatch

**解决方案**:
1. 分析每个 mismatch 的根因
2. 确定是 manifest 错误还是 catalog 错误
3. 修复并验证

**验收标准**:
- [ ] `mc_blocker_count: 0`
- [ ] `run_catalog_governance_gate.py --mode fail-on-new` 通过

### 2.5 SessionArtifactStore 越界 (P2)

**问题描述**: `polaris/cells/roles/runtime/internal/session_artifact_store.py` 直接导入 `context_os.compress_if_changed`。

**解决方案**:
1. 将 `compress_if_changed` 移至 public contract
2. 或通过 port 抽象依赖
3. 更新 import 语句

**验收标准**:
- [ ] SessionArtifactStore 不直接依赖 internal 模块
- [ ] 相关测试通过

### 2.6 Infrastructure 模块拆分 (P2)

**问题描述**: `polaris/infrastructure/accel/` 和 `polaris/infrastructure/code_intelligence/` 未按 Cell 架构组织。

**解决方案**:
1. 评估是否应迁移为独立 Cell
2. 或整合到现有 Cell
3. 更新 cells.yaml 和相关导入

**验收标准**:
- [ ] 模块归属清晰
- [ ] catalog 治理通过

---

## 3. 架构图

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Delivery Layer                               │
│  (delivery/) → 应通过 application/ facade 访问 cells/               │
└─────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      Application Layer                              │
│  runtime_admin.py, storage_admin.py, session_admin.py              │
└─────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│                        KernelOne Layer                              │
│  ports/ (storage, provider_registry, container, layout)            │
│  bootstrap 引导接入                                                   │
└─────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│                         Cells Layer                                 │
│  roles.kernel, roles.runtime, director.*, 等                        │
│  TruthLog / WorkingState / ReceiptStore / ProjectionEngine         │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 4. 执行计划

### Expert 1: Delivery Import Refactoring
- 职责: 重构 delivery 文件，使用 application layer facade
- 入口: `polaris/application/runtime_admin.py`, `storage_admin.py`, `session_admin.py`
- 验收: Phase 0 fence 测试通过

### Expert 2: KernelOne Port Bootstrap Wiring
- 职责: Bootstrap 接入 4 个新 Port 协议
- 入口: `polaris/bootstrap/`, `polaris/kernelone/ports/`
- 验收: KernelOne release gate 通过

### Expert 3: ContextOS GAP-2 Fix
- 职责: 修复 `_take_snapshot()` 可变引用问题
- 入口: `polaris/kernelone/context/context_os/runtime.py`
- 验收: ContextOS hardening 测试通过

### Expert 4: Catalog Governance Reconciliation
- 职责: 修复 11 个 catalog mismatch
- 入口: `docs/graph/catalog/cells.yaml`, 各 cell.yaml
- 验收: `mc_blocker_count: 0`

### Expert 5: SessionArtifactStore Boundary Fix
- 职责: 消除 `compress_if_changed` 越界导入
- 入口: `polaris/cells/roles/runtime/internal/session_artifact_store.py`
- 验收: 无 internal 依赖

### Expert 6: Infrastructure Module Organization
- 职责: 评估并重组 `accel/`, `code_intelligence/`
- 入口: `polaris/infrastructure/`
- 验收: 模块归属清晰

---

## 5. 验证命令

```bash
# Phase 0 fence tests
python -m pytest tests/architecture/test_delivery_internal_import_fence.py -q

# KernelOne release gate
python docs/governance/ci/scripts/run_kernelone_release_gate.py --mode all

# ContextOS hardening
python -m pytest polaris/kernelone/context/tests/test_context_os_hardening.py -q

# Catalog governance
python docs/governance/ci/scripts/run_catalog_governance_gate.py --workspace . --mode fail-on-new
```

---

## 6. 风险评估

| 任务 | 风险 | 缓解措施 |
|------|------|---------|
| Delivery 导入重构 | 破坏现有功能 | 保留 shim 兼容层 |
| Bootstrap 引导 | 启动失败 | 保持 fallback |
| Catalog mismatch | 影响 Cell 关系 | 逐个分析后修复 |
