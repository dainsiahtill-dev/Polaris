# Polaris 后端审计修复总蓝图 (Master Remediation Blueprint)

**版本**: 2026-04-30
**架构师**: Principal Architect
**团队规模**: 10 名资深 Python 工程师
**目标**: 全量落地审计报告中的剩余 10 项高优先级问题

---

## 1. 现状评估 (Current State Assessment)

### 1.1 已完成的修复 (Completed)

| 项 | 状态 |
|---|---|
| test_terminal_console.py 语法错误 | 已消除 |
| 27 个死测试文件清理 | 已删除 ~5000 LOC |
| ADR 编号冲突 | 3 组重复已重命名 |
| Pipeline stage 缺失 | 新增 3 个 stage |
| Fitness rule 升级 | outbox_atomic → enforced_non_regressive |
| cell.yaml 覆盖 | 60/60 完成 |
| holographic_runner.py 反向依赖 | 改用 kernelone RetryPolicy |
| test_safe_executor.py 导入错误 | DANGEROUS_PATTERNS 已移除 |
| conftest.py 格式化 | Ruff format 通过 |

### 1.2 剩余 10 项高优先级问题

| # | 问题 | 规模 | 复杂度 | 优先级 |
|---|---|---|---|---|
| P1 | 跨 Cell `internal/` 导入 (869 处) | 大 | 高 | Critical |
| P2 | `# type: ignore` 泛滥 (1509 处) | 极大 | 中 | High |
| P3 | 代码覆盖率 23.3% / 390 模块 0% | 极大 | 高 | High |
| P4 | 超大文件拆分 (>100KB × 4) | 小 | 中 | Medium |
| P5 | Application 层欠发育 (6 文件) | 中 | 高 | Medium |
| P6 | Subgraph 覆盖不足 (60 Cell / 6) | 中 | 中 | Medium |
| P7 | 旧根导入残留 (44 处 / 23 文件) | 中 | 低 | Low |
| P8 | KernelOne→Cells 反向依赖 (3 处) | 小 | 高 | Medium |
| P9 | Deprecated 标记清理 (381 处) | 中 | 低 | Low |
| P10 | 测试组织标准化 | 中 | 中 | Low |

---

## 2. 团队分工架构 (Team Assignment)

### 2.1 组织架构

```
Principal Architect (你)
├── Squad Alpha: 架构治理 (2 人)
│   ├── Engineer A1: 跨 Cell internal/ 导入治理 + CI 门禁
│   └── Engineer A2: Subgraph 扩展 + Cell 契约对齐
├── Squad Beta: 类型安全 (2 人)
│   ├── Engineer B1: # type: ignore 清理 (kernelone + cells)
│   └── Engineer B2: # type: ignore 清理 (delivery + infrastructure) + mypy strict
├── Squad Gamma: 测试工程 (3 人)
│   ├── Engineer G1: delivery 层测试补全 (155 个 0% 模块)
│   ├── Engineer G2: cells 层测试补全 (103 个 0% 模块)
│   └── Engineer G3: kernelone 层测试补全 (103 个 0% 模块) + 旧根导入清理
├── Squad Delta: 代码质量 (2 人)
│   ├── Engineer D1: 超大文件拆分 + Application 层重构
│   └── Engineer D2: Deprecated 清理 + 测试组织标准化
└── Squad Epsilon: 运行时底座 (1 人)
    └── Engineer E1: KernelOne→Cells 反向依赖根治 + 循环依赖清理
```

### 2.2 执行顺序与依赖关系

```
Week 1-2 (并行):
  ├── Squad Alpha: 建立跨 Cell internal/ 导入 CI 阻断门禁
  ├── Squad Beta: 批量清理 # type: ignore (低风险文件优先)
  ├── Squad Gamma: 启动 delivery 层测试补全
  ├── Squad Delta: 超大文件拆分
  └── Squad Epsilon: KernelOne 反向依赖根治方案

Week 3-4 (并行):
  ├── Squad Alpha: 渐进式修复 internal/ 导入 (按依赖图拓扑排序)
  ├── Squad Beta: mypy --strict 零警告冲刺
  ├── Squad Gamma: cells + kernelone 测试补全
  ├── Squad Delta: Application 层重构设计
  └── Squad Epsilon: 循环依赖根治实施

Week 5-6 (收尾):
  ├── Squad Alpha: Subgraph 扩展 (15+ 目标)
  ├── Squad Beta: 最终类型安全验证
  ├── Squad Gamma: 覆盖率回归验证 (目标 40%+)
  ├── Squad Delta: Deprecated 清理 + 测试组织标准化
  └── Squad Epsilon: 最终架构门禁验证
```

---

## 3. 技术方案详述

### 3.1 P1: 跨 Cell `internal/` 导入治理 (Squad Alpha)

**根因分析**: 307 个生产文件直接导入其他 Cell 的 `internal/` 目录，破坏 Public/Internal Fence。

**技术方案**:
1. **阶段 1a**: 在 `run_catalog_governance_gate.py` 中增加 `--check-internal-fence` 模式
   - 解析每个 Python 文件的 AST 提取 import 语句
   - 将文件路径映射到 owning Cell (通过 cells.yaml)
   - 判定规则: 若 import 目标路径包含 `/internal/` 且目标 Cell ≠ 源文件 Cell → 违规
   - 输出: 违规文件列表 + 建议的 public contract 替代路径

2. **阶段 1b**: 按依赖图拓扑排序修复
   - 叶子 Cell (无被依赖) 优先修复
   - 每修复一个文件，运行 pytest 回归验证
   - 对于无法通过现有 public contract 满足的调用，在目标 Cell 的 `public/` 中新增必要契约

3. **阶段 1c**: CI 集成
   - 在 `pipeline.template.yaml` 中新增 `cell_internal_fence_gate`
   - 设置为 `enforced_non_regressive`
   - Baseline: 当前 869 处违规作为初始基线，禁止新增

**关键约束**:
- 禁止创建新的 `common/` 或 `utils/` 来绕过 fence
- 必须复用已有 Cell 的 public contract
- 新增 public contract 需同步更新 cells.yaml

### 3.2 P2: `# type: ignore` 清理 (Squad Beta)

**根因分析**: 1509 处类型豁免掩盖了真实类型问题，导致 mypy --strict 无法通过。

**技术方案**:
1. **分类统计**: 按 ignore 原因分类
   ```bash
   grep -rn "# type: ignore\[" polaris/ | sed 's/.*\[\(.*\)\].*/\1/' | sort | uniq -c | sort -rn
   ```

2. **批量修复策略**:
   - **Category A - 缺少 return type**: 为函数添加 `-> None` / `-> dict[str, Any]` 等
   - **Category B - 变量类型推断失败**: 添加显式类型注解
   - **Category C - 第三方库无类型存根**: 创建 `.pyi` stub 文件或迁移到 typed 替代库
   - **Category D - 架构性类型冲突**: 标记为 `structural`，出 ADR 修复

3. **分阶段目标**:
   - Week 1-2: 清理 500 处低风险 ignore (Category A + B)
   - Week 3-4: 清理 500 处中风险 ignore (Category C)
   - Week 5-6: 处理 509 处高风险 ignore (Category D)，目标 ≤ 100 处保留

### 3.3 P3: 测试覆盖率提升 (Squad Gamma)

**根因分析**: 390 个模块 0% 覆盖，delivery 层最严重 (155 个)。

**技术方案**:
1. **Delivery 层 (Engineer G1)**:
   - HTTP 路由测试: 使用 `httpx.AsyncClient(transport=ASGITransport(app))` 做集成测试
   - CLI 命令测试: 使用 `click.testing.CliRunner` 或 `subprocess.run`
   - WebSocket 测试: 使用 `pytest-asyncio` + `websockets` 客户端
   - 目标: 为每个 router/handler 创建最小 3 个测试 (happy path, error, edge case)

2. **Cells 层 (Engineer G2)**:
   - 优先测试 public contracts (已覆盖的除外)
   - 为每个 service 类创建 mock-based 单元测试
   - 使用 `conftest.py` 的 `reset_singletons` fixture 保证隔离

3. **KernelOne 层 (Engineer G3)**:
   - 优先测试 contracts/ports (接口契约测试)
   - 为每个 tool_execution 模块创建参数化测试
   - 使用 `tmp_path` fixture 做文件系统隔离测试

### 3.4 P4: 超大文件拆分 (Squad Delta)

**技术方案**:
1. `cells/roles/kernel/internal/kernel/core.py` (>100KB)
   - 拆分为: `kernel/core/turn_engine.py`, `kernel/core/tool_loop.py`, `kernel/core/session_manager.py`

2. `delivery/cli/terminal_console.py` (>100KB)
   - 拆分为: `terminal/console.py`, `terminal/renderers.py`, `terminal/commands.py`

3. `kernelone/benchmark/holographic_runner.py` (>100KB)
   - 拆分为: `benchmark/holographic/runner.py`, `benchmark/holographic/stats.py`, `benchmark/holographic/reports.py`

4. `kernelone/context/context_os/runtime.py` (>100KB)
   - 拆分为: `context_os/runtime/engine.py`, `context_os/runtime/state.py`, `context_os/runtime/ports.py`

### 3.5 P5: Application 层重构 (Squad Delta)

**现状**: `polaris/application/` 仅 6 个 .py 文件，大量编排逻辑散落在 delivery 和 cells 中。

**目标架构**:
```
polaris/application/
├── __init__.py
├── orchestration/          # 用例编排 (从 cells/orchestration 迁移)
│   ├── pm_orchestrator.py
│   ├── director_orchestrator.py
│   └── qa_orchestrator.py
├── session/                # 会话生命周期管理
│   ├── session_manager.py
│   └── session_factory.py
├── workflow/               # 工作流编排
│   ├── workflow_coordinator.py
│   └── stage_transitions.py
├── health.py               # 健康检查 (已有)
├── runtime_admin.py        # 运行时管理 (已有)
├── session_admin.py        # 会话管理 (已有)
├── storage_admin.py        # 存储管理 (已有)
├── traceability_admin.py   # 可追溯性管理 (已有)
└── cognitive_runtime/
    └── service.py          # 认知运行时服务 (已有)
```

**迁移策略**:
1. 识别 delivery 层中包含业务编排逻辑的函数
2. 将编排逻辑提取到 application 层的 orchestrator
3. delivery 层保留仅 HTTP/WS/CLI 传输相关代码
4. 使用 application 层的 orchestrator 作为 delivery 和 cells 之间的中介

### 3.6 P6: Subgraph 扩展 (Squad Alpha)

**目标**: 60 Cell → 15+ Subgraph

**新增 Subgraph 计划**:
1. `llm_pipeline.yaml` - llm.dialogue → llm.provider_runtime → llm.tool_runtime → roles.kernel
2. `roles_execution_pipeline.yaml` - roles.host → roles.kernel → roles.runtime → roles.adapters
3. `context_assembly_pipeline.yaml` - context.catalog → context.engine → cells orchestation
4. `audit_pipeline.yaml` - audit.diagnosis → audit.evidence → audit.verdict
5. `director_workflow_pipeline.yaml` - director.planning → director.execution → director.tasking
6. `archive_pipeline.yaml` - archive.run_archive → archive.factory_archive → archive.task_snapshot_archive
7. `code_intelligence_pipeline.yaml` - code_intelligence.engine → context.engine
8. `finops_pipeline.yaml` - finops.budget_guard → runtime.execution_broker
9. `event_pipeline.yaml` - events.fact_stream → runtime.task_market
10. `knowledge_pipeline.yaml` - cognitive.knowledge_distiller → context.catalog

### 3.7 P7: 旧根导入清理 (Squad Gamma)

**策略**: 23 个文件，44 处导入。
- 架构测试文件 (test_architecture_invariants.py 等): 保留，它们用于验证迁移
- 其他文件: 批量转换为 `pytest.importorskip` 或删除

### 3.8 P8: KernelOne→Cells 反向依赖根治 (Squad Epsilon)

**策略**:
1. `benchmark/adapters/agentic_adapter.py`: 迁移至 `polaris/tests/benchmark/adapters/`
2. `benchmark/unified_runner.py`: 迁移 `stream_role_session_command` 调用点至 application 层
3. `cognitive/orchestrator.py`: 将 `AlignmentServiceAdapter` 改为 constructor injection，由 bootstrap 层注入

### 3.9 P9: Deprecated 标记清理 (Squad Delta)

**策略**:
1. 扫描所有 381 处 deprecated 标记
2. 对于已迁移至新路径的: 删除旧代码
3. 对于仍在使用的: 更新调用点至新路径，然后删除
4. 对于 shim 层: 保留但标记 `TRANSITIONAL` 过期日期

### 3.10 P10: 测试组织标准化 (Squad Delta)

**标准**:
```
- 单元测试: `polaris/tests/unit/<layer>/<cell>/<module>/`
- 集成测试: `polaris/tests/integration/<layer>/<cell>/`
- 架构测试: `polaris/tests/architecture/`
- 模块内测试: `<cell>/tests/` (仅限公共契约测试)
- E2E 测试: `polaris/tests/e2e/`
```

---

## 4. 验收标准 (Definition of Done)

### 4.1 质量门禁 (Quality Gates)

| 门禁 | 当前 | 目标 | 验收方式 |
|---|---|---|---|
| ruff check | 通过 | 通过 | `python -m ruff check . --fix` |
| ruff format | 通过 | 通过 | `python -m ruff format .` |
| mypy | 未运行 | 通过 | `python -m mypy polaris/` |
| pytest collect | 24,003 / 0 errors | ≥ 24,500 / 0 errors | `python -m pytest --collect-only -q` |
| test coverage | 23.3% | ≥ 40% | `pytest --cov=polaris` |
| cross-cell internal fence | 869 violations | ≤ 400 | `run_catalog_governance_gate --check-internal-fence` |
| type: ignore | 1509 | ≤ 200 | `grep -c "# type: ignore"` |
| ADR uniqueness | 通过 | 通过 | `ls docs/governance/decisions/adr-*.md` |
| cell.yaml coverage | 60/60 | 60/60 | `find polaris/cells -name cell.yaml | wc -l` |

### 4.2 禁止事项

- 禁止创建 `common/` 或 `utils/` 来绕过 Cell fence
- 禁止引入第二套 graph truth
- 禁止未经声明的副作用
- 禁止为了过测试回退历史旧实现

---

## 5. 风险与缓解 (Risks & Mitigations)

| 风险 | 概率 | 影响 | 缓解措施 |
|---|---|---|---|
| 跨 Cell 导入修复导致循环依赖 | 中 | 高 | 拓扑排序修复，每步回归测试 |
| 类型清理破坏运行时行为 | 低 | 高 | 每文件修改后运行相关测试 |
| Application 层重构引发回归 | 中 | 高 | 保留旧接口做 shim，渐进迁移 |
| 测试补全工作量超预期 | 高 | 中 | 优先核心路径，边缘模块标记 gap |
| mypy strict 无法通过 | 中 | 中 | 分阶段目标，允许保留少量 ignore |

---

## 6. 附录

### 6.1 工具脚本

```bash
# 统计跨 Cell internal/ 导入
grep -rn "from polaris\.cells\..*\.internal" --include="*.py" polaris/ | grep -v "__pycache__" | wc -l

# 统计 type: ignore
find polaris -name "*.py" ! -path "*/__pycache__/*" -exec grep -Hn "# type: ignore" {} \; | wc -l

# 统计 0% 覆盖模块
pytest --cov=polaris --cov-report=term-missing 2>/dev/null | grep "0%" | wc -l

# 验证 ADR 唯一性
ls docs/governance/decisions/adr-*.md | sed 's/.*adr-\([0-9]*\)-.*/\1/' | sort | uniq -d
```

### 6.2 参考文档

- `AGENTS.md` - 最高优先级执行规则
- `docs/AGENT_ARCHITECTURE_STANDARD.md` - 架构标准
- `docs/FINAL_SPEC.md` - 目标架构
- `docs/真正可执行的 ACGA 2.0 落地版.md` - ACGA 2.0 增强
- `docs/governance/ci/fitness-rules.yaml` - 治理规则
