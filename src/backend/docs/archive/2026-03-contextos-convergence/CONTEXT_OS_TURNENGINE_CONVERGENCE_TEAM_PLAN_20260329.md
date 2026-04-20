# Context OS 与 TurnEngine 架构收敛执行计划

**日期**: 2026-03-29
**状态**: Draft
**团队**: Python 架构与代码治理实验室
**规模**: 6 人高级 Python 工程师
**周期**: 4 周（2026-03-30 ~ 2026-04-26）

---

## 1. 团队架构

```
┌─────────────────────────────────────────────────────────────┐
│                      Tech Lead / Architect                    │
│                    [陈架构] - 架构决策与审查                   │
└───────────────────────────┬─────────────────────────────────┘
                            │
        ┌───────────────────┼───────────────────┐
        ▼                   ▼                   ▼
┌───────────────┐   ┌───────────────┐   ┌───────────────┐
│  Senior A    │   │  Senior B     │   │  Senior C     │
│ [王核心]      │   │ [李运行时]     │   │ [张上下文]     │
│ TurnEngine   │   │ Stream/Non-   │   │ Context OS    │
│ 开发者       │   │ Stream Parity │   │ 开发者        │
└───────┬───────┘   └───────┬───────┘   └───────┬───────┘
        │                   │                   │
        └───────────────────┼───────────────────┘
                            ▼
        ┌───────────────────────────────────────────┐
        │              Junior Support (2人)          │
        │         [测试工程师] + [文档工程师]          │
        │        测试覆盖 + 文档更新 + CI/CD         │
        └───────────────────────────────────────────┘
```

---

## 2. 角色与职责

### 2.1 Tech Lead / Architect

**角色**: [陈架构]
**职责**:
- 架构决策拍板（ContextRequest 统一方案、历史来源决策点）
- 代码审查（所有 PR 必须经过）
- 与产品/项目对接，确认需求边界
- 风险评估与缓解
- 每周 architecture review meeting 主持

**技术领域**:
- 整体系统架构
- Context OS 核心设计
- LLM 集成架构

**工作量**: 50% 管理 + 50% 代码

---

### 2.2 Senior A — TurnEngine 开发者

**角色**: [王核心]
**职责**:
- Phase 1: Stream/Non-Stream parity 修复验证
- Phase 2: `UnifiedContextRequest` 在 TurnEngine 侧的落地
- Phase 5: 统一历史来源实现
- 维护 `polaris/cells/roles/kernel/internal/turn_engine.py`
- 维护 `polaris/cells/roles/kernel/internal/tool_loop_controller.py`

**关键代码路径**:
```
polaris/cells/roles/kernel/internal/turn_engine.py
    ├── run()
    ├── run_stream()
    └── build_context_request()

polaris/cells/roles/kernel/internal/tool_loop_controller.py
    ├── build_context_request()
    ├── append_tool_result()
    └── append_tool_cycle()
```

**验收条件**:
- `test_parity_stream_vs_nonstream` 通过
- TurnEngine 单测 100% 通过
- benchmark L1-L5 parity 检查通过

---

### 2.3 Senior B — Runtime/Service 开发者

**角色**: [李运行时]
**职责**:
- Phase 1: `_persist_session_turn_state` 修复与验证
- Phase 4: 消除 Legacy 回退逻辑
- 维护 `polaris/cells/roles/runtime/public/service.py`

**关键代码路径**:
```
polaris/cells/roles/runtime/public/service.py
    ├── _persist_session_turn_state()  ← 核心修改点
    ├── _build_session_request()
    ├── execute_role_session()
    └── stream_chat_turn()
```

**验收条件**:
- `_persist_session_turn_state` 无 `turn_history=None` 回退分支
- Stream 和 Non-stream 模式 `turn_history` 参数传递一致
- integration test `test_session_persistence_parity` 通过

---

### 2.4 Senior C — Context OS 开发者

**角色**: [张上下文]
**职责**:
- Phase 2: `UnifiedContextRequest` 在 `kernelone/context/` 侧的定义
- Phase 3: Context OS 直接集成到 LLM 上下文
- Phase 6: Context OS 压缩协同
- 维护 `polaris/kernelone/context/context_os/`
- 维护 `polaris/kernelone/context/session_continuity.py`
- 维护 `polaris/cells/roles/kernel/internal/context_gateway.py`

**关键代码路径**:
```
polaris/kernelone/context/context_os/
    ├── models.py          # ContextOSSnapshot, ContextOSProjection
    ├── runtime.py         # StateFirstContextOS
    └── evaluation.py      # ContextOSRolloutGate

polaris/kernelone/context/session_continuity.py
    └── SessionContinuityEngine.project()

polaris/cells/roles/kernel/internal/context_gateway.py
    ├── ContextRequest → UnifiedContextRequest
    └── build_context()
```

**验收条件**:
- `UnifiedContextRequest` 在 `kernelone/context/contracts.py` 中定义
- Context OS 投影直接作为 LLM 上下文来源
- `test_context_os_integration` 通过

---

### 2.5 Senior D — Testing Engineer

**角色**: [赵测试]
**职责**:
- Phase 1-6: 所有阶段的测试编写与验证
- 维护 benchmark 测试套件
- 维护 parity integration tests
- 自动化测试覆盖报告

**关键测试文件**:
```
polaris/tests/benchmark/
    ├── test_parity_stream_vs_nonstream.py  ← 新增
    ├── test_context_os_integration.py       ← 新增
    └── test_turn_engine_parity.py           ← 新增

polaris/cells/roles/kernel/tests/
    ├── test_turn_engine.py
    └── test_context_gateway.py
```

**验收条件**:
- 所有新增代码有对应测试
- benchmark L1-L5 14/14 PASS
- 测试覆盖率 >= 85%

---

### 2.6 Senior E — DevOps / CI Engineer

**角色**: [孙DevOps]
**职责**:
- 维护 CI/CD pipeline
- benchmark 自动化运行与报告
- 代码质量门禁（ruff, mypy, pytest）
- 文档维护

**关键文件**:
```
.github/workflows/
    ├── benchmark.yml      ← 新增
    └── code-quality.yml

docs/blueprints/CONTEXT_OS_TURNENGINE_CONVERGENCE_BLUEPRINT_20260329.md  ← 本蓝图
```

**验收条件**:
- CI pipeline 绿色
- benchmark 报告自动生成
- 文档更新与代码同步

---

## 3. 协作流程

### 3.1 每日站会

**时间**: 每天 10:00 (15 分钟)
**形式**: Slack/飞书群
**内容**:
1. 昨日完成
2. 今日计划
3. 阻塞问题

### 3.2 周会

**时间**: 每周一 14:00 (1 小时)
**形式**: 线下/视频会议
**议程**:
1. Architecture review (陈架构主持)
2. Phase 进度检查
3. 风险评估
4. 下周计划

### 3.3 PR 流程

```
开发者 → PR → Tech Lead 审查 → 合并
  │         │
  │         └─▶ 必须通过:
  │              - ruff check
  │              - mypy
  │              - pytest
  │              - benchmark smoke test
  │
  └─▶ PR 描述必须包含:
       - 改了什么
       - 为什么改
       - 如何验证
```

### 3.4 分支策略

```
main                    ← 稳定分支
├── feature/parity-fix  ← Phase 1
├── feature/unify-context-request  ← Phase 2
├── feature/context-os-direct-integration  ← Phase 3
├── feature/remove-legacy  ← Phase 4
├── feature/unify-history-source  ← Phase 5
└── feature/context-os-compression  ← Phase 6
```

---

## 4. 阶段任务分配

### Week 1 (03-30 ~ 04-05): Phase 1-2

| 任务 | 负责人 | 输出物 |
|------|--------|--------|
| P0-1 已修复 | [李运行时] | PR 已合并 |
| P1-2 删除回退分支 | [李运行时] | PR |
| P1-3 Stream/Non-stream 统一路径 | [王核心] | PR |
| P1-4 Parity test | [赵测试] | 测试文件 |
| P2-1 UnifiedContextRequest 定义 | [张上下文] | contracts.py |
| P2-2 RoleContextGateway 更新 | [张上下文] | PR |

### Week 2 (04-06 ~ 04-12): Phase 3-4

| 任务 | 负责人 | 输出物 |
|------|--------|--------|
| P3-1 ContextOSnapshot 处理 | [张上下文] | PR |
| P3-2 SessionContinuityEngine 集成 | [张上下文] | PR |
| P3-3 _persist 直接构建投影 | [李运行时] | PR |
| P3-4 消除 strategy_receipt 路径 | [王核心] | PR |
| P4-1 删除 else 回退分支 | [李运行时] | PR |
| P4-2 删除 _build_post_turn_history | [李运行时] | PR |
| P4-3 清理 legacy 历史逻辑 | [王核心] | PR |

### Week 3 (04-13 ~ 04-19): Phase 5-6 + 集成

| 任务 | 负责人 | 输出物 |
|------|--------|--------|
| P5-1/2/3/4 统一历史来源 | [王核心] | PR |
| P6-1 ContextCompressor 迁移 | [张上下文] | PR |
| P6-2 compress() 实现 | [张上下文] | PR |
| P6-3/4 消除 legacy 压缩分支 | [张上下文] | PR |
| 集成测试 | [赵测试] | 测试文件 |

### Week 4 (04-20 ~ 04-26): 验证与收尾

| 任务 | 负责人 | 输出物 |
|------|--------|--------|
| Full benchmark run | [赵测试] | 报告 |
| CI/CD 更新 | [孙DevOps] | pipeline |
| 文档更新 | [孙DevOps] | docs/ |
| Architecture review | [陈架构] | 会议纪要 |
| 性能测试 | [王核心] | 报告 |

---

## 5. 质量门禁

### 5.1 代码质量

```bash
# 必须全部通过
ruff check . --fix
ruff format .
mypy polaris/cells/roles/ polaris/kernelone/context/
pytest polaris/cells/roles/kernel/tests/ -v
```

### 5.2 Benchmark 验收

```bash
# 目标: 14/14 PASS
python -m polaris.delivery.cli.agentic_eval --suite tool_calling_matrix --workspace /tmp/benchmark
```

### 5.3 Parity 检查

```bash
# Stream vs Non-stream 输出一致性
pytest tests/benchmark/test_parity_stream_vs_nonstream.py -v
```

---

## 6. 风险登记

| ID | 风险 | 可能性 | 影响 | 缓解措施 | 状态 |
|----|------|--------|------|----------|------|
| R1 | Phase 1 修复引入新 bug | 中 | 高 | 完整测试覆盖 + canary | 监控 |
| R2 | Context OS 性能下降 | 低 | 中 | 增量投影 | 观察 |
| R3 | 团队理解不一致 | 中 | 中 | 每日沟通 | 进行中 |
| R4 | benchmark 不稳定 | 高 | 高 | 多次运行取峰值 | 重点 |

---

## 7. 沟通与报告

### 7.1 每日报告模板

```
## [日期] 每日进展

### 完成
- [任务1]

### 计划
- [任务2]

### 阻塞
- [问题]
```

### 7.2 周报模板

```
## Week N (日期范围)

### 进度
- Phase X: 80% 完成
- Phase Y: 开始

### 质量指标
- pytest: 500 passed
- benchmark: 10/14 PASS

### 风险
- R1: 新发现

### 下周计划
- ...
```
