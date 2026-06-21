# 生产级稳定性验证蓝图 (Production Stability Validation Blueprint)

**日期**: 2026-06-08
**状态**: 进行中
**目标**: 完整政事堂 → PM → Chief Engineer → Director → QA 多轮复杂项目连续跑、故障注入、rollback 审计、性能压测

---

## 1. 执行摘要 (Executive Summary)

当前 `run-production-stability-validation.mjs` 已实现 4 个 gate 中的 3 个完全通过：
- `governance` ✅ — graph/cell catalog hard-fail，0 blockers
- `fault_injection_rollback` ✅ — 29 个单元/集成测试（transaction rollback, director pool chaos, rollback guard）
- `performance_stress` ✅ — 15 个性能/压测（endpoint, tool, audit package）

**唯一失败 gate**：`full_chain`（需要 `KERNELONE_E2E_USE_REAL_SETTINGS=1` 配置 real PM/Director/QA runtime）

本蓝图定义如何在现有基础设施上扩展为完整的长期生产级验证系统，覆盖：
- 多轮角色链路（政事堂 → PM → Chief Engineer → Director → QA）
- 故障注入与降级验证
- Rollback 链路真实性审计
- 性能压测与容量评估

---

## 2. 当前验证基础设施 (Current Validation Infrastructure)

### 2.1 入口脚本
```
infrastructure/scripts/run-production-stability-validation.mjs
```
**架构**: Node.js MJS 脚本，4 个 gate，顺序执行，支持 `--repeat N`（多轮重复）和 `--only-gate`（单 gate 调试）

**输出**: `test-results/production-stability/production-stability-audit.json`
```json
{
  "schema": "polaris.e2e.production_stability_validation.v1",
  "status": "PASS|FAIL|DRY_RUN",
  "gates": [
    {
      "id": "governance",
      "title": "...",
      "required": true,
      "real_chain_required": false,
      "skipped": false,
      "commands": [["python", "src/backend/docs/governance/ci/scripts/run_catalog_governance_gate.py", ...]],
      "status": "PASS",
      "results": [...],
      "findings": []
    }
  ],
  "summary": {
    "gate_count": 4,
    "required_count": 4,
    "required_fail_count": 0
  }
}
```

### 2.2 当前 Gate 定义

| Gate ID | 标题 | real_chain_required | 当前状态 |
|---------|------|---------------------|---------|
| `full_chain` | Dual-entry full-chain PM/Chief Engineer/Director/QA runtime | **true** | ❌ BLOCKED（缺 credentials） |
| `fault_injection_rollback` | Fault injection, transaction rollback, and recovery guards | false | ✅ PASS（29 tests） |
| `performance_stress` | Endpoint/performance/stress audit package | false | ✅ PASS（15 tests） |
| `governance` | Graph and Cell governance hard-fail | false | ✅ PASS（0 blockers） |

### 2.3 已有验证能力（可直接复用）

| 能力 | 路径 | 说明 |
|------|------|------|
| `RoleContextGateway.record_projection_outcome()` | `cells/roles/kernel/internal/context_gateway/gateway.py` | 生产学习闭环反馈 |
| `RollbackGuard` / `GitStashRollbackGuard` | `cells/chief_engineer/blueprint/internal/rollback_guard.py` | Director 级内存快照 + git stash 回滚 |
| `DirectorPool` + `ScopeConflictDetector` | `cells/chief_engineer/blueprint/internal/director_pool.py` | 多 Director 并行调度 + 冲突检测 |
| `StabilityScorer` | `cells/roles/kernel/internal/speculation/stability_scorer.py` | speculation 稳定性评分（阈值 0.82） |
| `ProjectionEngine.sort_events()` | `kernelone/context/projection_engine.py` | 自适应事件排序（`ENABLE_PROJECTION_ADAPTIVE_ORDERING` 环境变量） |
| `EvaluationRunner` + `TimeoutConfig` | `cells/llm/evaluation/internal/runner.py` | 超时保护的评测套件运行器 |
| `super_pipeline_config.SuperPipelineContext` | `delivery/cli/super_pipeline_config.py` | 多 stage pipeline（architect→pm→chief_engineer→director） |

### 2.4 已有评测矩阵套件

| Suite | 入口 | 类型 |
|-------|------|------|
| `tool_calling_matrix` | `run_tool_calling_matrix_suite()` | CLI 矩阵（agentic_eval） |
| `speculation_matrix` | `run_speculation_matrix_suite()` | CLI 矩阵（agentic_eval） |
| `context_projection_matrix` | `run_context_projection_matrix_suite()` | 确定性矩阵（无需 LLM） |
| `projection_adaptive_matrix` | `run_projection_adaptive_matrix_suite()` | A/B ON/OFF 自适应排序评测 |
| `agentic_benchmark` | `run_agentic_benchmark_suite()` | 端到端智能体基准 |
| `interview` | `generate_interview_answer()` | LLM 面试评估 |
| `session_workflow_matrix` | `run_session_workflow_suite()` | Session 工作流矩阵 |

---

## 3. 目标架构 (Target Architecture)

### 3.1 验证层次模型

```
┌─────────────────────────────────────────────────────────────┐
│  Layer 0: Governance Gate (CI/CD 门禁)                       │
│  catalog_governance_hard_fail / kernelone_release_gate      │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│  Layer 1: Unit Stability Gate                               │
│  fault_injection_rollback (29 tests) ✅                      │
│  performance_stress (15 tests) ✅                            │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│  Layer 2: Integration Stability Gate                        │
│  multi-role pipeline (PM→CE→Director→QA)                    │
│  - Real runtime execution (needs credentials)                │
│  - Fault injection (network partition, OOM, timeout)          │
│  - Rollback audit (RollbackGuard + GitStashRollbackGuard)    │
│  - Performance stress (concurrent directors, memory)          │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│  Layer 3: Long-Running Stability Gate                       │
│  --repeat N with per-round audit                            │
│  - N× full pipeline runs                                     │
│  - Fault injection per round                                 │
│  - Cumulative pass/fail trends                               │
└─────────────────────────────────────────────────────────────┘
```

### 3.2 新增 Gate 设计

#### Gate: `multi_role_integration`
```
id: multi_role_integration
title: Multi-role PM/Chief Engineer/Director/QA integration validation
required: true
real_chain_required: true
skipped: false
commands:
  - [npm, run, test:e2e:dual-full-chain, --, --require-all-candidate-runtime]
evidence:
  - test-results/electron-dual-full-chain/dual-entry-full-chain-summary.json
```

#### Gate: `fault_injection_e2e`
```
id: fault_injection_e2e
title: End-to-end fault injection across role pipeline
required: true
real_chain_required: true
skipped: false
commands:
  - [python, -m, pytest, src/backend/polaris/tests/agent_stress/test_fault_injection_e2e.py, -q]
evidence:
  - src/backend/polaris/tests/agent_stress/test_fault_injection_e2e.py
```

#### Gate: `rollback_audit_e2e`
```
id: rollback_audit_e2e
title: Rollback guard end-to-end audit (DirectorPool + RollbackGuard)
required: true
real_chain_required: false
skipped: false
commands:
  - [python, -m, pytest, src/backend/polaris/cells/chief_engineer/blueprint/tests/test_rollback_guard_integration.py, -q]
evidence:
  - src/backend/polaris/cells/chief_engineer/blueprint/tests/test_rollback_guard_integration.py
```

#### Gate: `projection_adaptive_matrix`
```
id: projection_adaptive_matrix
title: ProjectionEngine adaptive ordering A/B validation (ON vs OFF)
required: true
real_chain_required: false
skipped: false
commands:
  - [python, -m, polaris.delivery.cli.agentic_eval, --suite, projection_adaptive_matrix, --workspace, .]
evidence:
  - runtime/llm_tests/reports/rep-*.json
```

### 3.3 故障注入场景矩阵

| 场景 | 注入方式 | 验证点 |
|------|---------|--------|
| Network partition | `DRIZZLE_FAULT_NETWORK_DELAY_MS` env | Director 重试 + RollbackGuard 触发 |
| OOM / Memory pressure | `MEMORY_LIMIT_MB` env | DirectorPool split/reassign |
| LLM provider timeout | `KERNELONE_LLM_TIMEOUT_SEC=1` | TurnEngine fallback + 降级 |
| Tool execution failure | Mock `write_file` failure | TransactionKernel rollback |
| Director conflict | Overlapping file scopes | ScopeConflictDetector + reassign |
| Context budget exhaustion | Large payload injection | ProjectionEngine receipt offload |

### 3.4 Rollback 审计链路

```
TransactionKernel.execute()
  ↓ [exception]
  RollbackGuard.rollback_director(director_id)
  ↓ [if parallel mode]
  GitStashRollbackGuard.rollback(task_id)
  ↓
  Verify: files restored, task status = pending
  ↓
  Audit: record to test-results/production-stability/rollback-audit.json
```

---

## 4. 阶段计划 (Phased Implementation Plan)

### Phase 1: 激活 full_chain Gate（1-2 天）

**目标**: 让 `full_chain` gate 可执行（解决 KERNELONE_E2E_USE_REAL_SETTINGS 依赖）

**步骤**:
1. 在 `.env.example` 中添加 `KERNELONE_E2E_USE_REAL_SETTINGS=1` 模板
2. 检查 `test-results/electron-dual-full-chain/dual-entry-full-chain-summary.json` 是否存在
3. 若存在则 gate 通过；若不存在则执行 `npm run test:e2e:dual-full-chain`
4. 在 `run-production-stability-validation.mjs` 中实现 `real_chain_required` 检测逻辑

**验证**: `node infrastructure/scripts/run-production-stability-validation.mjs --only-gate full_chain` 返回 PASS

### Phase 2: 新增 projection_adaptive_matrix Gate（2-3 天）

**目标**: 将 `projection_adaptive_matrix` 套件纳入 production stability 验证

**步骤**:
1. 确认 `run_projection_adaptive_matrix_suite()` 已注册到 `EvaluationRunner.SUITE_RUNNERS`
2. 在 `run-production-stability-validation.mjs` 中添加新 gate 条目
3. 验证 `--suite projection_adaptive_matrix` CLI 调用正常
4. 添加门禁阈值：`adaptive_affects_prompt >= 1` 时 PASS

**关键文件**:
- `cells/llm/evaluation/internal/projection_adaptive_matrix.py` ✅ 已存在
- `cells/llm/evaluation/public/service.py` ✅ 已注册
- `delivery/cli/agentic_eval.py` ✅ 已注册

**验证**: `python -m polaris.delivery.cli.agentic_eval --suite projection_adaptive_matrix --workspace .` 返回 OK

### Phase 3: 新增 fault_injection_e2e 套件（3-5 天）

**目标**: 实现端到端故障注入测试套件

**步骤**:
1. 创建 `src/backend/polaris/tests/agent_stress/test_fault_injection_e2e.py`
2. 使用 `DirectorPool` + 故障注入 env 实现以下场景：
   - `test_network_partition_triggers_rollback`
   - `test_oom_triggers_director_split`
   - `test_llm_timeout_triggers_fallback`
   - `test_tool_failure_triggers_transaction_rollback`
   - `test_director_conflict_triggers_reassign`
   - `test_context_budget_exhaustion_triggers_receipt_offload`
3. 每个测试输出 `test-results/production-stability/fault-injection-{scenario}.json`

**验证**: `pytest src/backend/polaris/tests/agent_stress/test_fault_injection_e2e.py -v` 全绿

### Phase 4: 新增 rollback_audit_e2e 套件（2-3 天）

**目标**: 验证 RollbackGuard 在真实 DirectorPool 场景下的行为

**步骤**:
1. 创建 `src/backend/polaris/cells/chief_engineer/blueprint/tests/test_rollback_guard_integration.py`
2. 使用真实 workspace（临时目录）创建 DirectorPool，提交任务，触发 rollback
3. 验证文件内容恢复到 snapshot 状态
4. 验证 `rollback-audit.json` 包含完整的 timeline（snapshot → task → rollback → verify）

**验证**: `pytest src/backend/polaris/cells/chief_engineer/blueprint/tests/test_rollback_guard_integration.py -v`

### Phase 5: 集成长期运行 Gate（2-3 天）

**目标**: 实现 `--repeat N` 多轮连续验证 + 累积趋势分析

**步骤**:
1. 扩展 `run-production-stability-validation.mjs` 的 `--repeat` 逻辑：
   - 每轮输出 `test-results/production-stability/round-{N}/production-stability-audit.json`
   - 累积输出 `test-results/production-stability/production-stability-cumulative.json`
2. 累积报告包含：
   - 每轮 pass/fail 计数
   - 失败趋势（连续 N 轮失败 → 告警）
   - 性能回归（latency_p99 > baseline × 1.5 → 告警）
3. 实现提前终止：`--max-failed 3`（3 轮失败后停止）

**验证**: `node infrastructure/scripts/run-production-stability-validation.mjs --repeat 5 --max-failed 3 --skip-real-chain` 正常执行

### Phase 6: 性能压测扩展（3-5 天）

**目标**: 将性能压测覆盖到完整 multi-role pipeline

**步骤**:
1. 创建 `src/backend/polaris/tests/performance/test_multi_role_pipeline_performance.py`
   - 并发 Director 数量：1 / 3 / 5
   - 吞吐量：turns/second 随并发 Director 数量变化曲线
   - 内存：峰值内存随 active_tasks 增长曲线
2. 扩展 `test_v2_endpoint_performance.py` 加入 multi-role 端点测试
3. 添加 `test_tool_performance.py` 的工具级压测（write_file, read_file, glob, repo_tree）

**验证**: `pytest src/backend/polaris/tests/performance/test_multi_role_pipeline_performance.py -v`

---

## 5. 成功标准 (Success Criteria)

### 5.1 Gate 级别

| Gate | 指标 | 阈值 |
|------|------|------|
| `governance` | blockers + high | = 0 |
| `fault_injection_rollback` | unit tests passed | = 100% |
| `performance_stress` | tests passed | = 100% |
| `full_chain` | E2E summary exists + status=PASS | — |
| `projection_adaptive_matrix` | adaptive_affects_prompt | >= 1 |
| `fault_injection_e2e` | scenarios passed | = 100% |
| `rollback_audit_e2e` | integration tests passed | = 100% |

### 5.2 多轮稳定性指标

| 指标 | 阈值 | 说明 |
|------|------|------|
| `repeat_10_pass_rate` | >= 90% | 10 轮重复中至少 9 轮全 PASS |
| `max_consecutive_failures` | <= 2 | 连续失败超过 2 轮触发告警 |
| `performance_regression_threshold` | latency_p99 < baseline × 1.5 | 性能回归检测 |
| `rollback_success_rate` | >= 95% | RollbackGuard 成功恢复比例 |

---

## 6. 关键风险与缓解 (Key Risks & Mitigations)

| 风险 | 影响 | 缓解 |
|------|------|------|
| `full_chain` gate 持续失败（credentials 问题） | 无法验证真实 PM→Chief Engineer→Director→QA 链路 | Phase 1 优先解决；提供 mock 模式降级 |
| 故障注入测试 flakiness | 误报率高 | 使用确定性故障注入（env vars），避免 timing race |
| RollbackGuard 在非 git repo 工作区失败 | 回滚失败 | 检测 `.git` 存在性，降级到 `GitStashRollbackGuard` |
| ProjectionEngine 自适应权重导致非确定性排序 | 难以复现问题 | `ENABLE_PROJECTION_ADAPTIVE_ORDERING=0` 退回纯时序 |
| 多轮运行时间过长 | CI 超时 | `--max-failed 3` 提前终止；分层并行执行 |

---

## 7. 实施优先级 (Implementation Priority)

```
Phase 1 (高优先级，1-2天)
  └── 激活 full_chain gate（无成本，高价值信号）

Phase 2 (高优先级，2-3天)
  └── 新增 projection_adaptive_matrix gate（已实现，注册即可）

Phase 3 (中优先级，3-5天)
  └── fault_injection_e2e 套件（新开发）

Phase 4 (中优先级，2-3天)
  └── rollback_audit_e2e 套件（新开发）

Phase 5 (中优先级，2-3天)
  └── 长期运行与累积报告（增强现有脚本）

Phase 6 (低优先级，3-5天)
  └── 性能压测扩展（需要更多资源）
```

---

## 8. 监控与告警 (Monitoring & Alerting)

### 8.1 指标采集

在 `production-stability-audit.json` 中新增字段：
```json
{
  "metrics": {
    "gate_durations_ms": {"governance": 1234, "fault_injection_rollback": 2380},
    "round_pass_rates": [1.0, 1.0, 0.8, 1.0, 1.0],
    "cumulative_failures": 1,
    "performance_latency_p99_ms": 1523,
    "rollback_success_rate": 0.95
  }
}
```

### 8.2 告警规则

- 连续 3 轮 `full_chain` FAIL → PagerDuty / Slack 告警
- `cumulative_failures > 5` → 邮件摘要
- `performance_latency_p99_ms > baseline × 1.5` → 性能回归标记

---

## 9. 技术债务与后续优化 (Technical Debt & Future Optimizations)

1. **descriptor pack 生成自动化**：当前手动运行，需集成到 CI
2. **Coverage 缺口**：当前 23.3%，`cells/roles/kernel` 和 `kernelone/context` 需要重点覆盖
3. **Playwright E2E 稳定性**：full-chain E2E 测试需要优化以减少 flakiness
4. **多工作区支持**：当前只支持单 repo，可扩展到 multi-workspace 验证
5. **可视化报告**：将 JSON 报告转换为 HTML dashboard

---

*本蓝图为第一版，随着 Phase 实施将持续更新。每 Phase 完成后更新状态与完成度。*
