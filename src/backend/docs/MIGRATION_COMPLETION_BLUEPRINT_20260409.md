# Polaris 迁移完成蓝图 v1.0
## Migration Completion Blueprint

**版本**: v1.0 | **日期**: 2026-04-09 | **目标**: 完成所有迁移，消除"迁移中"状态

---

## 一、现状分析

### 1.1 文档过时问题

| 文档 | 日期 | 状态 | 问题 |
|------|------|------|------|
| `STOPGAP_FEATURE_AUDIT_2026-03-25.md` | 2026-03-25 | **过时** | 列出已清理的债务 |
| `FULL_CONVERGENCE_AUDIT_20260405.md` | 2026-04-05 | **最新** | 56 CRITICAL / 94 HIGH / 178+ MEDIUM |
| `MASTER_CONVERGENCE_AUDIT_20260405.md` | 2026-04-05 | 最新摘要 | 同上 |

### 1.2 已验证完成状态

根据 2026-04-05 全量审计：

| 类别 | 问题数 | 已修复 | 待处理 |
|------|--------|--------|--------|
| CRITICAL (P0) | 56 | 28 | 28 |
| HIGH (P1) | 94 | 36 | 58 |
| MEDIUM (P2) | 178+ | 55+ | 123+ |

### 1.3 核心问题

1. **STOPGAP 文档过时**：`standalone_runner.py` 和 `tui_console.py` 已删除，但 STOPGAP 文档仍列为"冻结存在"
2. **迁移状态混乱**：多处代码/文档标记"in progress"或"migration"，但实际已完成
3. **剩余债务确认**：8个文件经文件系统验证确实存在兼容层债务

---

## 二、目标定义

### 2.1 核心目标

```
【目标1】消除过时文档
├── 归档 STOPGAP_FEATURE_AUDIT_2026-03-25.md
├── 创建新的债务清单（基于2026-04-05审计）
└── 所有"迁移中"状态标记为"已完成"

【目标2】清理已确认的8个真实债务文件
├── P0-1: Workflow Runtime 混合路径（2文件确认存在）
├── P0-2: Compat IO Utils（1文件确认存在）
├── P1-1/2/3/4: Delivery 双协议（4文件确认存在）
└── 遗留 Guard 文件（1文件确认存在）

【目标3】验证并处理26个"待验证"文件
└── 确认或消除每个文件的债务状态

【目标4】建立文档自动更新机制
└── 防止未来文档再次过时
```

### 2.2 成功标准

```
✅ STOPGAP 文档已归档，不再作为参考
✅ 所有迁移状态标记为 COMPLETED
✅ 8个确认债务文件已清理或明确迁移路径
✅ 26个待验证文件状态已确认
✅ 代码中无 "migration" / "in progress" / "TODO: migrate" 等模糊状态标记
```

---

## 三、执行计划

### 3.1 第一阶段：文档终结（1-2天）

**任务1.1**: 归档过时 STOPGAP 文档
```
操作：
1. 将 STOPGAP_FEATURE_AUDIT_2026-03-25.md 重命名为
   STOPGAP_FEATURE_AUDIT_2026-03-25_ARCHIVED.md
2. 在文件头部添加 ARCHIVED 标记和原因
3. 创建新的债务清单文档 MIGRATION_DEBT_INVENTORY_20260409.md
```

**任务1.2**: 创建统一债务清单
```
创建 MIGRATION_DEBT_INVENTORY_20260409.md，内容包括：
1. 基于 FULL_CONVERGENCE_AUDIT_20260405.md 的最新债务状态
2. 已验证存在的8个债务文件
3. 26个待验证文件清单
4. 每个债务的"完成状态"标记
```

**任务1.3**: 清除代码中的迁移状态标记
```
搜索并清除：
- "migration in progress"
- "TODO.*migrate"
- "in progress"
- "phase X migration"
等模糊状态标记

替换为明确的完成状态或移除
```

### 3.2 第二阶段：债务清理（3-5天）

**任务2.1**: Workflow Runtime 混合路径清理
```
文件：
- polaris/cells/orchestration/workflow_runtime/internal/runtime_engine/runtime/embedded/store_sqlite.py
- polaris/cells/orchestration/workflow_runtime/internal/runtime_engine/workflows/generic_pipeline_workflow.py

操作：
1. 确认是否有调用方仍在使用
2. 如果无调用方 → 删除文件
3. 如果有调用方 → 重构为纯 facade 并添加完成标记
```

**任务2.2**: Compat IO Utils 清理
```
文件：
- polaris/infrastructure/compat/io_utils.py

操作：
1. 追踪所有调用方
2. 将能力迁移回各自归属的 KernelOne contract
3. 删除 compat/io_utils.py
4. 更新所有调用方导入
```

**任务2.3**: Delivery 双协议清理
```
文件：
- polaris/delivery/http/routers/agent.py
- polaris/delivery/ws/runtime_endpoint.py
- polaris/delivery/http/v2/pm.py
- polaris/delivery/http/v2/director.py

操作：
1. 确认 v1 协议是否还有调用方
2. 如果无调用方 → 删除 v1 兼容代码
3. 如果有调用方 → 添加明确废弃标记和完成日期
```

**任务2.4**: Guard 文件处理
```
文件：
- polaris/cells/roles/runtime/__guard__.py

操作：
1. 确认 Phase 4 模块是否已全部删除
2. 如果已删除 → 删除 __guard__.py
3. 如果仍需保留 → 精简为最小必要检查
```

### 3.3 第三阶段：待验证文件确认（2-3天）

**任务3.1**: 验证26个待验证文件
```
每个文件检查：
1. 文件是否存在
2. 是否仍被引用
3. 如果无引用 → 删除
4. 如果有引用 → 评估是否需要迁移
```

**待验证文件清单**：
```
polaris/cells/orchestration/pm_planning/internal/pipeline_ports.py
polaris/cells/orchestration/pm_planning/pipeline.py
polaris/cells/orchestration/pm_dispatch/internal/error_classifier.py
polaris/cells/orchestration/pm_dispatch/internal/dispatch_pipeline.py
polaris/cells/orchestration/pm_dispatch/internal/iteration_state.py
polaris/cells/llm/evaluation/internal/readiness_tests.py
polaris/cells/llm/dialogue/internal/role_dialogue.py
polaris/cells/llm/control_plane/public/contracts.py
polaris/cells/roles/engine/internal/sequential_adapter.py
polaris/cells/roles/kernel/internal/output_parser.py
polaris/domain/models/task.py
polaris/domain/services/token_service.py
polaris/kernelone/_runtime_config.py
polaris/kernelone/trace/context.py
polaris/kernelone/fs/control_flags.py
polaris/kernelone/audit/gateway.py
polaris/cells/roles/runtime/internal/__init__.py
polaris/cells/roles/runtime/internal/role_agent_service.py
polaris/cells/orchestration/workflow_runtime/internal/runtime_backend_adapter.py
polaris/cells/orchestration/workflow_runtime/internal/runtime_engine/activities/pm_activities.py
polaris/cells/orchestration/workflow_runtime/internal/runtime_engine/activities/director_activities.py
```

### 3.4 第四阶段：文档自动化（1-2天）

**任务4.1**: 建立债务追踪机制
```
创建：polaris/docs/TECHNICAL_DEBT_TRACKER.md

内容：
- 债务ID、描述、状态、负责人
- 自动更新脚本（基于代码中的 DEPRECATED/compatibility 标记）
```

**任务4.2**: 添加 CI 门禁
```
在 CI 中添加：
1. 检测新代码中是否引入 compat/legacy 模式
2. 检测 deprecated 标记是否有过期日期
3. 阻止新的技术债务引入 main
```

---

## 四、团队分工（10人专家团队）

### 团队A：文档终结组（2人）
- **负责人**：文档审计专家
- **任务**：归档 STOPGAP，创建新债务清单，清除迁移状态标记
- **交付物**：MIGRATION_DEBT_INVENTORY_20260409.md

### 团队B：Workflow Runtime 清理组（2人）
- **负责人**：工作流架构师
- **任务**：清理 store_sqlite.py、generic_pipeline_workflow.py
- **交付物**：干净的 workflow_runtime 目录

### 团队C：Compat IO 清理组（2人）
- **负责人**：系统集成专家
- **任务**：追踪 io_utils 调用方，迁移能力，删除 compat 模块
- **交付物**：polaris/infrastructure/compat/ 目录清理完成

### 团队D：Delivery 协议清理组（2人）
- **负责人**：API 架构师
- **任务**：清理 v1 协议，确认 v2 为唯一协议
- **交付物**：双协议并存问题解决

### 团队E：验证与自动化组（2人）
- **负责人**：DevOps 专家
- **任务**：验证26个待验证文件，建立自动化追踪
- **交付物**：债务追踪机制 + CI 门禁

---

## 五、里程碑

| 里程碑 | 目标日期 | 完成标准 |
|--------|----------|----------|
| M1: 文档终结 | 2026-04-10 | STOPGAP 已归档，新债务清单已创建 |
| M2: 债务清理 | 2026-04-12 | 8个确认债务文件已处理 |
| M3: 待验证完成 | 2026-04-14 | 26个待验证文件状态已确认 |
| M4: 自动化上线 | 2026-04-15 | CI 门禁已添加，债务追踪机制运行 |

---

## 六、风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| 删除兼容层导致现有功能故障 | 高 | 删除前全面测试，保留降级路径 |
| 待验证文件被未发现代码引用 | 中 | 全面 grep + 运行时监控 |
| 文档自动化难以维护 | 低 | 最小化自动化，优先手动追踪 |

---

## 七、后续行动

**立即开始**：
1. 并行启动5个团队小组
2. 每日 standup 同步进度
3. 每完成一个任务模块更新债务清单状态

**完成后**：
1. 发布最终债务清单 v1.0
2. 建立季度债务审查机制
3. 将此蓝图文档标记为 COMPLETED

---

*维护团队：Polaris 架构委员会*
*最后更新：2026-04-09*
