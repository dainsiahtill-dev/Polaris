# Cell 依赖关系分析与合并方案

**日期**: 2026-04-13
**状态**: 分析完成

---

## 1. 摘要

对 15 个待处理 Cell 进行跨 Cell 内部导入分析，发现：

| Group | Cell 数量 | 跨 Cell 违规数 | 合并复杂度 | 建议 |
|-------|-----------|----------------|-----------|------|
| Director | 5 | 11+ | 极高（循环依赖） | 暂缓，等待架构重构 |
| Roles | 7 | 2+ | 高 | 暂缓，分组隔离 |
| Archive | 3 | 0 | 低 | 推进合并 |

**核心结论**: 多数 Cell 合并受限于循环跨 Cell 内部依赖，强行合并会破坏现有接口契约。优先推进 Archive 合并（无违规），Director/Roles 改为"架构债务跟踪"而非"合并执行"。

---

## 2. Director Group 分析

### 2.1 Cell 列表
- `director.execution`
- `director.delivery`
- `director.planning`
- `director.runtime`
- `director.tasking`

### 2.2 跨 Cell 内部违规导入（11+ 处）

#### director.execution.internal → director.tasking.internal
```
execution/internal/bootstrap_template_catalog.py:15
execution/internal/director_logic_rules.py:15
execution/internal/existence_gate.py:15
execution/internal/file_apply_service.py:15
execution/internal/patch_apply_engine.py:15
execution/internal/repair_service.py:15
execution/internal/task_lifecycle_service.py:16
execution/internal/worker_executor.py:15
execution/internal/worker_pool_service.py:15
```

#### director.execution.internal → director.planning.internal
```
execution/internal/director_logic_rules.py:15
```

#### director.planning.internal → director.execution.internal
```
planning/internal/context_gatherer.py:38
```

#### director.tasking.internal → director.execution.internal
```
tasking/internal/file_apply_service.py:157
```

### 2.3 循环依赖图
```
director.execution ←→ director.planning (双向)
director.execution ←→ director.tasking (双向)
```

### 2.4 其他跨 Cell 合法导入（public contracts）
- `director.execution.internal` → `roles.runtime.public.service` ✓
- `director.planning.internal` → `roles.runtime.public.service` ✓
- `director.planning.internal` → `runtime.task_runtime.public.service` ✓
- `director.tasking.internal` → `roles.runtime.public.service` ✓
- `director.tasking.internal` → `runtime.execution_broker.public` ✓
- `director.tasking.internal` → `audit.verdict.public.service` ✓
- `director.tasking.internal` → `audit.evidence.public.service` ✓

### 2.5 合并建议
**暂缓执行合并**。原因：
1. 存在多个双向循环依赖
2. 强行合并会破坏现有 `director.planning`、`director.tasking` 的 Cell 边界
3. 这些违规是已知的架构债务（记录在 fitness-rules.yaml）

**替代方案**: 将 Director 5 个 Cell 标记为 `composite cell`，声明为单一 Cell 边界，内部互相调用视为内部调用。但这需要重构 `cells.yaml` 结构和 CI 门禁规则。

---

## 3. Roles Group 分析

### 3.1 Cell 列表
- `roles.adapters`
- `roles.engine`
- `roles.host`
- `roles.kernel`
- `roles.profile`
- `roles.runtime`
- `roles.session`

### 3.2 跨 Cell 内部违规导入（2 处）

#### roles.kernel.internal → roles.session.internal
```
kernel/internal/kernel.py:21
kernel/internal/kernel/core.py:38 (imports metrics which uses session internal paths)
```

#### roles.runtime.internal → runtime.execution_broker.public / runtime.state_owner.public
```
runtime/internal/process_service.py:11,14,17
```

### 3.3 组内 public contract 合法导入
| 导入方 | 目标 | 性质 |
|--------|------|------|
| roles.adapters.internal | roles.kernel.public | ✓ |
| roles.adapters.internal | roles.profile.public | ✓ |
| roles.adapters.internal | roles.session.public | ✓ |
| roles.adapters.internal | roles.runtime.public | ✓ |
| roles.adapters.internal | llm.dialogue.public | ✓ |
| roles.adapters.internal | orchestration.workflow_runtime.public | ✓ |
| roles.kernel.internal | roles.profile.public | ✓ |
| roles.kernel.internal | roles.runtime.public | ✓ |
| roles.kernel.internal | roles.session.public | ✓ |

### 3.4 合并建议
**暂缓执行合并**。原因：
1. roles.kernel 和 roles.session 内部耦合是 ACGA 2.0 明确要求解耦的（kernel 是 AI 运行时底座，session 是状态管理）
2. roles.runtime → runtime.execution_broker/state_owner 违反 Cell 边界但属于 bootstrap 问题

**替代方案**:
- 认可 `roles.kernel + roles.session` 为事实上的单一 Cell（内部耦合但对外提供独立 public contracts）
- 记录 roles.runtime 的边界违规为技术债务

---

## 4. Archive Group 分析

### 4.1 Cell 列表
- `archive.factory_archive`
- `archive.run_archive`
- `archive.task_snapshot_archive`

### 4.2 跨 Cell 内部违规导入: 0 处

所有导入均为 archive 内部或 public contracts：
- `task_snapshot_archive` → `run_archive.public` ✓
- `factory_archive` → `run_archive.public` ✓
- `run_archive` → `storage.layout.public` ✓

### 4.3 合并建议
**推进合并**。三个 Archive Cell 可以合并为单一 `archive` Cell。

**合并方案**:
```
archive (新 Cell)
├── owned_paths: polaris/cells/archive/**
├── sub-Cells: factory_archive, run_archive, task_snapshot_archive (作为模块)
├── state_owners: 合并三个 Cell 的 state_owners
├── effects_allowed: 合并三个 Cell 的 effects_allowed
└── public_contracts: 合并三个 Cell 的 public contracts
```

---

## 5. 任务 #92（根因综合与修复建议）

基于以上分析，#92 任务（根因综合与修复建议）需要等待 #84-#91 全部完成后综合输出。

### 5.1 根因分类

| 根因类型 | 数量 | 主要 Cell |
|---------|------|----------|
| 循环内部依赖 | 6 | director.* |
| 跨 Cell 内部导入 | 4 | roles.*, director.* |
| 架构设计遗留 | 2 | roles.runtime |

### 5.2 修复优先级

| 优先级 | 问题 | 建议修复方式 |
|--------|------|-------------|
| P0 | director.execution ↔ director.tasking 循环 | 创建统一 director Cell |
| P1 | roles.kernel → roles.session.internal | 通过 public contracts 重构 |
| P2 | roles.runtime → runtime.state_owner | 移除或通过 Cell 公开契约 |

---

## 6. 下一步行动

1. **Archive 合并（#14）**: 目录结构已保留3个独立Cell，Catalog更新涉及49处跨Cell引用，风险高。**建议**：等待CI门禁支持后执行，或接受当前3-Cell结构作为"事实Cell簇"
2. **Director/Roles 合并（#12/#13）**: 改为架构债务跟踪，暂缓执行合并。已在 `fitness-rules.yaml` 中记录为 blocker 级别违规
3. **更新 cells.yaml**: 为 Director Group 添加 `composite_cell: true` 标记（或等 CI 门禁支持）
4. **#92 根因综合**: 等 #84-#91 完成后执行

## 7. Archive 合并详细方案（待执行）

### 7.1 代码层面（已完成 #76）
- 3个目录仍独立存在：`factory_archive/`, `run_archive/`, `task_snapshot_archive/`
- 未执行目录合并

### 7.2 Catalog 层面（待执行）
需要修改 `docs/graph/catalog/cells.yaml`：

1. **新增 `archive` Cell 条目**（合并后的统一入口）
2. **标记 3 个子 Cell 为 deprecated**（添加 `deprecated: true`, `merged_into: archive`）
3. **更新所有跨 Cell 引用**（在 `depends_on` 中将 `archive.*` 替换为 `archive`）

**风险提示**：当前 cells.yaml 中有 49+ 处引用指向 `archive.*`，批量修改极易出错，建议使用脚本自动化执行。

### 7.3 推荐执行方式
```bash
python docs/governance/ci/scripts/merge_archive_cells.py --dry-run  # 先 dry-run
python docs/governance/ci/scripts/merge_archive_cells.py --execute  # 确认无误后执行
```

---

## 7. 参考

- CLAUDE.md §2.4: Cell 复用优先 + KernelOne 底座优先
- CLAUDE.md §6.5: 工具别名映射铁律
- `docs/governance/ci/fitness-rules.yaml`: 交叉导入规则
- `docs/governance/decisions/adr-0066-benchmark-framework-convergence.md`: Cell 合并参考
