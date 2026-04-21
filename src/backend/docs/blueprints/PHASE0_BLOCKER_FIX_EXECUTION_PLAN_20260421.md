# Phase 0 执行蓝图：BLOCKER 级问题修复

## 版本: v1.0
## 日期: 2026-04-21
## 状态: 执行中

---

## 1. 问题概览

| 类别 | BLOCKER 数量 | 核心问题 |
|------|-------------|----------|
| 架构问题 | 4 | 未声明 Cell、边界模糊、Graph 不一致 |
| 代码重复 | 2 | director_logic 复制、WorkflowResult 复制 |
| 契约违规 | 6 | roles.runtime 直接导入 kernel 内部模块 |
| 技术债务 | 1 | context_os.models v1/v2 迁移 |
| **总计** | **13** | |

---

## 2. 工作流划分

### Team A: 契约修复组 (4 人)
**负责**: CONTRACT-001 ~ CONTRACT-006 (roles.runtime 内部导入修复)

**任务**:
1. 创建 `roles.kernel.public.transaction_contracts` 公开契约
2. 暴露 `Phase`, `PhaseManager`, `VERIFICATION_TOOLS`, `resolve_delivery_mode`
3. 修复 `session_orchestrator.py` 的导入路径

### Team B: 架构修复组 (3 人)
**负责**: ARCH-001 ~ ARCH-004 (Cell 边界与声明问题)

**任务**:
1. 为 5 个未声明 Cell 创建 cell.yaml
2. 修正 `factory.cognitive_runtime` 的 owned_paths
3. 重新划分 Cell 对外部目录的声称边界

### Team C: 代码去重组 (2 人)
**负责**: DUP-001, DUP-002 (重复代码合并)

**任务**:
1. 提取 `director_logic` 到 `polaris/domain/services/`
2. 合并 `PMWorkflowResult` 到 `polaris/domain/entities/`

### Team D: 技术债务组 (2 人)
**负责**: DEBT-001 (context_os.models v1→v2 迁移)

**任务**:
1. 创建迁移脚本
2. 分批迁移 42 处消费者

---

## 3. 执行顺序

```
Week 1 (Day 1-3):
├── Team A: 创建公开契约骨架
├── Team B: 识别 5 个未声明 Cell 的归属
├── Team C: 分析 director_logic 提取范围
└── Team D: 制定 context_os v2 迁移映射

Week 1 (Day 4-5):
├── Team A: 暴露 Phase/PhaseManager 常量
├── Team B: 为未声明 Cell 创建 cell.yaml
├── Team C: 提取 director_logic 到 shared
└── Team D: 迁移核心模块 (kernel/core.py)

Week 2 (Day 1-3):
├── Team A: 修复 session_orchestrator.py 导入
├── Team B: 修正 factory.cognitive_runtime 路径
├── Team C: 合并 WorkflowResult 模型
└── Team D: 迁移 context_gateway, intelligent_compressor

Week 2 (Day 4-5):
├── Team A: 验证所有导入通过公开契约
├── Team B: 更新 cells.yaml 与 cell.yaml 一致性
├── Team C: 运行测试验证
└── Team D: 迁移测试文件
```

---

## 4. 验证门禁

所有修复必须通过:
- `ruff check . --fix`
- `mypy --strict` (目标: 零错误)
- `pytest -x` (目标: 全部通过)
- `python -m polaris.docs.governance.ci.scripts.run_catalog_governance_gate --mode audit-only`

---

## 5. 风险管理

| 风险 | 缓解措施 |
|------|----------|
| 公开契约 API 破坏现有功能 | 保留旧导入路径作为兼容别名 |
| Cell 重新划分影响其他 Cell | 提前通知相关团队进行协调 |
| v1→v2 迁移引入回归 | 建立完整的测试套件 |

---

*本蓝图为执行指导文档*
