# Cells → KernelOne 整合实施计划

**版本**: v1.0
**日期**: 2026-04-03
**总工期**: 10-16天

---

## 1. 团队分工

### 1.1 团队设置 (11个团队)

| 团队 | 人数 | 负责任务 | 阶段 | 关键文件 |
|------|------|----------|------|----------|
| **Team Alpha** | 2 | CR-1: 删除director工具链 | P0 | director/execution/internal/tools/*.py |
| **Team Beta** | 2 | CR-2: 修复provider_runtime单例 | P0 | llm/provider_runtime/internal/providers.py |
| **Team Gamma** | 2 | CR-3: 统一Event系统 | P1 | kernelone/events/*.py, roles/*/events.py |
| **Team Delta** | 2 | CR-4: 修复audit双重写入 | P1 | audit/diagnosis/internal/connection_audit_service.py |
| **Team Epsilon** | 2 | H-1: 统一Budget基础设施 | P1 | kernelone/context/budget_gate.py, roles/kernel/token_budget.py |
| **Team Zeta** | 2 | H-2: 统一危险命令检测 | P1 | kernelone/security/dangerous_patterns.py |
| **Team Eta** | 2 | H-3: 统一存储路径 | P1 | kernelone/storage/paths.py |
| **Team Theta** | 2 | H-4: 统一Tool Loop | P1 | kernelone/tool/*.py |
| **Team Iota** | 2 | H-5: 统一Event Publishing | P1 | kernelone/events/*.py |
| **Team Kappa** | 2 | H-6: 统一LLM调用 | P1 | roles/adapters/internal/base.py |
| **Team Lambda** | 3 | 门禁验证 + 测试 + CI | All | 全量回归 |

### 1.2 每日 standup 汇报结构

```
09:00 Standup:
- 昨日完成
- 今日计划
- 阻塞项

17:00 Demo:
- 演示已完成的变更
- 代码审查
```

---

## 2. Phase 0: 准备 (Day 0)

### 2.1 环境准备

- [ ] 所有人拉取最新 main 分支
- [ ] 创建 feature branch: `feature/cells-kernelone-integration`
- [ ] 运行基线测试: `pytest polaris/ -x -q --tb=no`
- [ ] 记录基线测试数量

### 2.2 任务分配

Team Alpha → CR-1
Team Beta → CR-2
Team Gamma → CR-3
Team Delta → CR-4
Team Epsilon → H-1
Team Zeta → H-2
Team Eta → H-3
Team Theta → H-4
Team Iota → H-5
Team Kappa → H-6
Team Lambda → 验证 + CI

---

## 3. Phase 1: P0 Critical (Day 1-2)

### Day 1: Team Alpha + Beta 并行

#### Team Alpha: CR-1 删除director工具链

**上午**:
1. 识别所有 `from polaris.cells.director.execution.internal.tools import` 导入
2. 统计受影响文件数量
3. 准备迁移映射表

**下午**:
1. 执行删除: `rm polaris/cells/director/execution/internal/tools/`
2. 修改所有导入: `from polaris.kernelone.tools import ChainExecutor`
3. 运行 `ruff check . --fix`

**验证**:
```bash
grep -r "from polaris.cells.director.execution.internal.tools" polaris/ --include="*.py"
# 应无输出
python -m polaris.delivery.cli.director.cli_thin --workspace /tmp/test_repo --iterations 1
# 应正常完成
```

#### Team Beta: CR-2 修复provider_runtime单例

**上午**:
1. 分析 `llm/provider_runtime/internal/providers.py` 的 ProviderManager 类
2. 识别所有调用 `get_provider_manager()` 的地方
3. 确认 infrastructure 单例的导入路径

**下午**:
1. 修改 `get_provider_manager()` 返回 infrastructure 单例
2. 删除本地 ProviderManager 类 (如果完全重复)
3. 运行测试验证

**验证**:
```bash
python -c "
from polaris.infrastructure.llm.providers.provider_manager import get_provider_manager
m1 = get_provider_manager()
m2 = get_provider_manager()
assert m1 is m2
print('Singleton OK')
"
pytest polaris/infrastructure/llm/providers/tests/ -v
```

### Day 2: 验证 + Code Review

- Lambda 团队运行全量测试
- 所有 P0 变更合并到 feature branch
- 代码审查

---

## 4. Phase 2: P1 High (Day 3-7)

### Day 3: Team Gamma + Delta

#### Team Gamma: CR-3 统一Event系统

**任务**:
1. 创建 `kernelone/events/fact_events.py`
2. 创建 `kernelone/events/session_events.py`
3. 修改 `roles/kernel/internal/events.py` 使用新模块
4. 修改 `session_persistence.py` 使用新模块

#### Team Delta: CR-4 修复audit双重写入

**任务**:
1. 修改 `audit/diagnosis/internal/connection_audit_service.py`
2. 删除 JSONL 写入逻辑
3. 只保留 KernelAuditRuntime.emit_event()

### Day 4: Team Epsilon + Zeta

#### Team Epsilon: H-1 统一Budget基础设施

**任务**:
1. 扩展 `kernelone/context/budget_gate.py`
2. 添加 `allocate_section()` 和 `get_section_breakdown()`
3. 修改 `roles/kernel/token_budget.py` 委托给 gate

#### Team Zeta: H-2 统一危险命令检测

**任务**:
1. 创建 `kernelone/security/dangerous_patterns.py`
2. 修改 `roles/kernel/internal/policy/layer/budget.py` 使用新模块
3. 修改 `roles/kernel/internal/policy/sandbox_policy.py` 使用新模块
4. 删除重复模式定义

### Day 5: Team Eta + Theta

#### Team Eta: H-3 统一存储路径

**任务**:
1. 创建 `kernelone/storage/paths.py`
2. 实现 `resolve_signal_path()`, `resolve_artifact_path()` 等
3. 修改 `roles/adapters/internal/base.py` 使用新模块
4. 修改 `pm_adapter.py`, `qa_adapter.py` 等

#### Team Theta: H-4 统一Tool Loop

**任务**:
1. 创建 `kernelone/tool/compaction.py`
2. 创建 `kernelone/tool/safety.py`
3. 创建 `kernelone/tool/transcript.py`
4. 修改 `roles/kernel/context_event.py` 使用新模块

### Day 6: Team Iota + Kappa

#### Team Iota: H-5 统一Event Publishing

**任务**:
1. 创建 `kernelone/events/task_trace_events.py`
2. 修改 `roles/adapters/internal/base.py:_emit_task_trace_event()` 使用新模块
3. 统一所有 event 发布到 kernelone

#### Team Kappa: H-6 统一LLM调用

**任务**:
1. 删除 `roles/adapters/internal/base.py:_call_role_llm()`
2. 删除 `roles/adapters/internal/director/dialogue.py:role_llm_invoke()`
3. 确保所有调用通过 kernelone.llm

### Day 7: 验证

- Lambda 运行全量回归测试
- 修复发现的问题
- 合并到 feature branch

---

## 5. Phase 3: CI/CD (Day 8-9)

### Day 8-9: Team Lambda

**任务**:
1. 创建 `docs/governance/ci/scripts/run_cells_kernelone_gate.py`
2. 添加 `fitness-rules.yaml` 新规则
3. 运行全量测试确保无回归
4. 创建 rollback 方案

**验证**:
```bash
# 运行门禁
python docs/governance/ci/scripts/run_cells_kernelone_gate.py

# 运行全量测试
pytest polaris/ -x -q --tb=short

# 运行 ruff
ruff check . --fix
ruff format .
```

---

## 6. Phase 4: 上线 (Day 10-16)

### Day 10-14: 逐步合并

| 日期 | 合并内容 | 验证 |
|------|----------|------|
| Day 10 | CR-1, CR-2 | 回归测试 |
| Day 11-12 | CR-3, CR-4, H-1 | 回归测试 |
| Day 13-14 | H-2, H-3, H-4, H-5, H-6 | 回归测试 |

### Day 15-16: 全量验证

```bash
# 全量测试
pytest polaris/ -v --tb=short

# 门禁检查
python docs/governance/ci/scripts/run_cells_kernelone_gate.py

# 性能基准
python scripts/run_factory_e2e_smoke.py --workspace .
```

---

## 7. 风险登记

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| 删除director工具链破坏功能 | 低 | 高 | feature flag, 验证后删除 |
| Budget变更影响token计数 | 中 | 中 | 向后兼容, 配置开关 |
| Event格式变化影响日志 | 低 | 低 | adapter兼容旧格式 |

---

## 8. 进度追踪

### 每日更新模板

```markdown
## Day N (2026-04-XX)

### 完成
- [ ] CR-1: ...

### 进行中
- [ ] CR-2: ...

### 阻塞
- [ ] None

### 测试结果
- pytest: XXX passed, Y failed
- ruff: 0 errors
```

---

**计划制定**: 2026-04-03
**下次审查**: 2026-04-07 (Day 4)
