# AGI Resident 实施路线图

> 状态: IN_PROGRESS - Phase 1.3
> 最后更新: 2024-03-08
> 当前任务: Phase 1.3 技能提案确认流 (实现中)
> 负责: Claude Code

---

## 总体原则

1. **基于现有架构演化** - 不复造平行系统，Resident 内核已存在
2. **文档先行** - 每阶段开始前写入设计文档，中断时可恢复
3. **状态分离** - 持久治理状态(Goal/Skill) vs 瞬时执行投影(Progress)
4. **统一证据模型** - Decision/Review/Experiment 共用 change_set/evidence_bundle

---

## 当前架构盘点

### 已存在的基础能力

| 模块 | 文件 | 能力 |
|------|------|------|
| Resident 内核 | `service.py`, `models.py` | 决策记录、目标治理、技能抽取、自动 tick |
| 工程规范 | `resident-engineering-rfc.md` | 架构设计文档 |
| Director 集成 | `director_workflow.py` | 关键阶段写 Resident decision，发 task progress |
| 前端消费 | `useRuntime.ts`, `App.tsx` | 消费 resident snapshot 和任务进度 |
| 文件变更 | `file_event_broadcaster.py` | 文件变更广播、patch 统计 |
| Diff 组件 | `RealTimeFileDiff.tsx` | 现成 diff 展示组件 |
| 代码搜索 | `code_search.py` | 代码块索引 + 文本匹配 |
| 审查框架 | `review_gate.py` | 独立审计、风险分析 |
| 对话基础 | `AIDialoguePanel.tsx`, `role_session_service.py` | 角色聊天会话 |

### 关键缺口

- [ ] 统一的证据包模型 (change_set/evidence_bundle)
- [ ] 目标执行投影 (runtime progress → goal progress)
- [ ] 决策与代码变更的关联追溯
- [ ] 技能提案人工确认流
- [ ] 审查建议的 UI 展示流

---

## Phase 1: 证据与追溯 (3-4 周)

### 1.1 统一变更证据模型 [P0]

**状态**: ⏳ 待开始

#### 目标
建立 `EvidenceBundle` 统一模型，支持 Decision/Review/Experiment 的共同需求。

#### 设计文档
- [ ] `docs/resident/design/evidence-bundle.md` - 数据模型设计
- [ ] `docs/resident/design/decision-traceability.md` - 决策追溯方案

#### 实现任务
- [ ] 创建 `src/backend/app/resident/evidence_models.py`
  - `EvidenceBundle` dataclass
  - `FileChange` dataclass
  - `TestRunEvidence` dataclass
  - `PerfEvidence` dataclass
- [ ] 修改 `src/backend/app/resident/models.py`
  - `DecisionRecord` 新增 `evidence_bundle_id` 字段
- [ ] 创建 `src/backend/app/resident/evidence_service.py`
  - `create_bundle_from_working_tree()` - 从当前工作树创建证据包
  - `create_bundle_from_run()` - 从 Director run 创建证据包
  - `get_bundle()` - 获取证据包详情
  - `compare_bundles()` - 对比两个证据包
- [ ] 修改 `src/backend/app/resident/service.py`
  - `record_decision()` 自动打包证据
  - `tick()` 生成技能提案时附带证据引用
- [ ] 数据库迁移脚本
  - `decisions` 表新增 `evidence_bundle_id` 字段
- [x] 测试
  - `src/backend/tests/test_resident_evidence_integration.py`

#### 验收标准
```python
# 决策记录时自动创建证据包
decision = resident_service.record_decision({
    'actor': 'director',
    'summary': '优化错误处理',
    'verdict': 'success',
})
assert decision.evidence_bundle_id is not None

# 证据包包含完整的变更信息
bundle = evidence_service.get_bundle(decision.evidence_bundle_id)
assert bundle.base_sha is not None  # 执行前的 commit
assert bundle.head_sha is not None  # 执行后的 commit
assert len(bundle.change_set) > 0   # 文件变更列表
assert bundle.test_results is not None  # 测试结果
```

#### 中断恢复检查点
1. 数据模型设计文档完成 → 可恢复
2. evidence_models.py 实现 → 可恢复
3. 数据库迁移完成 → 可恢复
4. service.py 集成完成 → 可恢复

---

### 1.2 目标执行投影 [P0]

**状态**: ⏳ 待开始

#### 目标
建立 `goal_execution_projection` 派生视图，**不污染**持久化的 `GoalProposal`。

#### 设计文档
- [ ] `docs/resident/design/goal-execution-projection.md`

#### 实现任务
- [ ] 创建 `src/backend/app/resident/execution_projection.py`
  - `build_goal_execution_projection(goal_id, workspace)` → `GoalExecutionView`
  - `infer_stage(tasks)` - 从任务列表推断阶段
  - `estimate_eta(tasks)` - 估算剩余时间
- [ ] 修改 `src/backend/app/services/runtime_projection.py`
  - 在构建 runtime projection 时包含 goal execution 信息
- [ ] 修改 `src/backend/api/v2/runtime_ws.py`
  - WebSocket 推送 `goal_execution_update` 事件
- [ ] 前端: 修改 `src/frontend/src/app/components/resident/GoalItem.tsx`
  - 显示进度条 ( Stage: planning ████░░ 40% )
  - 显示当前任务
  - 显示 ETA

#### 验收标准
```typescript
// 前端接收实时进度更新
{
  type: 'goal_execution_update',
  payload: {
    goal_id: 'goal-xxx',
    stage: 'coding',           // planning | coding | testing | review
    percent: 0.65,             // 65%
    current_task: '重构 error_handler.ts',
    eta_minutes: 12
  }
}
```

#### 中断恢复检查点
1. 后端 projection 服务实现 → 可恢复
2. WebSocket 事件集成 → 可恢复
3. 前端进度条组件 → 可恢复

---

### 1.3 技能提案确认流 [P1]

**状态**: ⏳ 待开始

#### 目标
`tick()` 自动生成的技能先作为 `SkillProposal` 等待人工确认，**不自动升格**。

#### 设计文档
- [ ] `docs/resident/design/skill-proposal.md`

#### 实现任务
- [ ] 修改 `src/backend/app/resident/models.py`
  - 新增 `SkillProposal` 模型（状态: pending_review/approved/rejected/merged）
  - `extracted_from: List[str]` - 关联的 decision_ids
  - `confidence: float` - 统计置信度
- [ ] 修改 `src/backend/app/resident/service.py`
  - `tick()` 生成 `SkillProposal` 而不是直接写入 `skills`
  - 新增 `approve_skill_proposal()`
  - 新增 `reject_skill_proposal()`
- [ ] 前端: 修改 `ResidentWorkspace` 概览页
  - 新增 "💡 AGI 发现可复用模式" 提醒卡片
  - [查看提案] [忽略] [批准入库] 按钮

#### 验收标准
```python
# tick 发现模式后生成提案
proposal = resident_service.tick()
assert proposal['type'] == 'skill_proposal'
assert proposal['status'] == 'pending_review'
assert len(proposal['extracted_from']) >= 3  # 至少 3 次决策

# 用户批准后升格为技能
skill = resident_service.approve_skill_proposal(proposal_id)
assert skill in resident_service.get_skills()
```

---

## Phase 2: 审查与分解 (4-6 周)

### 2.1 收敛版代码审查 [P0]

**状态**: ⏳ 待开始

#### 目标
**不做** PR 平台集成/保存即审查，只做：
1. Director 任务完成后自动审查
2. 用户按需触发审查

#### 设计文档
- [ ] `docs/resident/design/converged-code-review.md`

#### 实现任务
- [ ] 创建 `src/backend/app/resident/review_service.py`
  - `review_after_task(workspace, task_result)` - 任务后审查
  - `review_on_demand(workspace, change_bundle)` - 按需审查
- [ ] 修改 `src/backend/app/orchestration/workflows/director_task_workflow.py`
  - 任务完成后调用 `review_service.review_after_task()`
- [ ] 创建 `src/backend/app/resident/review_models.py`
  - `ReviewResult` - 复用 EvidenceBundle
  - 关联到 DecisionRecord (actor='review_assistant')
- [ ] 前端: 修改 `ContextSidebar`
  - 新增 "审查建议" 面板
  - 展示风险和改进建议
  - [接受] [忽略] [讨论] 按钮

#### 验收标准
```python
# Director 任务完成后自动触发审查
task_result = director_service.execute_task(task)
review = review_service.review_after_task(workspace, task_result)
assert review.verdict in ['success', 'blocked', 'warning']
assert review.evidence_bundle_id is not None
```

---

### 2.2 合同树任务分解 [P1]

**状态**: ⏳ 待开始

#### 目标
LLM 分解输出必须是**合同树 + 验收标准**，不是自由文本。

#### 设计文档
- [ ] `docs/resident/design/contract-tree-decomposition.md`

#### 实现任务
- [ ] 创建 `src/backend/app/llm/usecases/task_decomposition.py`
  - `decompose_goal_to_contracts(parent_goal, context)` → `List[ContractNode]`
- [ ] 创建 `src/backend/app/resident/contract_models.py`
  - `ContractNode` - 合同节点
  - `AcceptanceCriteria` - 验收标准
  - `VerificationMethod` - 验证方法
- [ ] 修改 `src/backend/app/resident/service.py`
  - `create_goal()` 支持接收合同树
  - 将合同树存入 goal 的 `contract_tree` 字段
- [ ] 前端: 修改 GoalItem
  - 展开显示合同树结构
  - 每个合同节点显示验收标准

#### 验收标准
```python
contracts = task_decomposition.decompose_goal_to_contracts(
    parent_goal={'title': '优化性能'},
    context=evidence_bundle
)
for contract in contracts:
    assert contract.title
    assert len(contract.acceptance_criteria) > 0
    assert contract.verification_method in ['static_analysis', 'test', 'benchmark', 'human_review']
```

---

## Phase 3: 信任与问答 (6-8 周)

### 3.3 知识库问答 [P0]

**状态**: ⏳ 待开始

**注意**: 不做 Copilot 级补全，只做"审计与解释"场景的知识问答。

#### 设计文档
- [ ] `docs/resident/design/knowledge-qa.md`

#### 实现任务
- [ ] 增强 `src/backend/app/services/code_search.py`
  - 添加符号索引（函数、类、接口）
  - 关联证据引用（代码块最近被哪些决策修改）
- [ ] 创建 `src/backend/app/resident/qa_service.py`
  - `explain_code(workspace, file_path, line_range)`
  - `find_related_decisions(workspace, query)`
  - `answer_question(workspace, question)`
- [ ] 前端: 复用 `AIDialoguePanel`
  - 新增 `/explain [代码引用]` 命令
  - 回答包含证据引用链接

#### 验收标准
```
用户: /explain src/utils/error.ts:45-60
AGI: 这是错误处理核心函数，最近被以下决策修改：
     - 2024-01-15: 添加 trace_id 上下文 ([查看决策](#))
     当前实现基于技能: "异步错误处理模式"
```

---

### 3.1 三元治理模型 [P1]

**状态**: ⏳ 待开始

**注意**: 不复造 `trust_level`，细化现有的 `ResidentMode`。

#### 设计文档
- [ ] `docs/resident/design/triple-governance.md`

#### 实现任务
- [ ] 修改 `src/backend/app/resident/models.py`
  - `OperationPolicy` - 操作策略配置
  - `max_risk_level: int`
  - `required_verification: List[str]`
  - `auto_rollback_on_failure: bool`
- [ ] 修改 `src/backend/app/resident/service.py`
  - 每个操作前检查 policy
  - 高风险操作自动转为提案

---

## Phase 4: 实验 (8-10 周)

### 3.2 实验环境隔离 [P1]

**状态**: ⏳ 待开始

**注意**: 不做完整 git worktree，用 branch-based 实验。

#### 设计文档
- [ ] `docs/resident/design/branch-based-experiment.md`

#### 实现任务
- [ ] 创建 `src/backend/app/resident/experiment_service.py`
  - `run_counterfactual_experiment(workspace, baseline_strategy, counterfactual_strategy)`
  - 创建实验分支、应用策略、运行测试、对比结果
- [ ] 复用 Phase 1 的 `EvidenceBundle` 存储实验结果
- [ ] 前端: 展示实验对比报告

---

## Phase 5: 战略储备 (12周+)

### 4.x 跨项目学习、市场、协作 [P2]

**状态**: 📋 待规划

暂不详细展开，作为战略方向储备。

---

## 当前阶段状态看板

### Phase 1.1 统一变更证据模型
```
[✅] 设计文档: evidence-bundle.md
[✅] 设计文档: decision-traceability.md
[✅] 实现: evidence_models.py
[✅] 实现: evidence_service.py
[✅] 测试: test_resident_evidence_integration.py
[✅] 数据库迁移 (DecisionRecord 使用 dataclass，无需 SQL 迁移)
[✅] 集成: service.py
[✅] 前端: EvidenceViewer 组件
[✅] API: /decisions/{id}/evidence 端点
```

### Phase 1.2 目标执行投影
```
[✅] 设计文档: goal-execution-projection.md
[✅] 实现: execution_projection.py
[✅] 集成: service.py (get_goal_execution_view, list_goal_executions)
[✅] 集成: api/v2/resident.py (/goals/{id}/execution, /goals/execution/bulk)
[✅] 集成: runtime_projection.py (goal_executions in status)
[✅] 前端: GoalItem 进度条 + ExecutionProgressBar 组件
[✅] 测试: test_execution_projection.py
```

### Phase 1.3 技能提案确认流
```
[✅] 设计文档: skill-proposal.md
[✅] 实现: SkillProposal 模型 (models.py)
[✅] 实现: storage.py 存储方法
[⏳] 实现: service.py 业务逻辑 (approve/reject)
[⏳] 修改: tick() 生成提案
[⏳] API: /skill-proposals 端点
[⏳] 前端: 提案提醒卡片
```

---

## 快速导航

| 想要... | 查看... |
|---------|---------|
| 理解整体架构 | `../resident-engineering-rfc.md` |
| 查看当前 API | `../resident-api.md` |
| 了解价值主张 | `../agi-value-proposition.md` |
| 查看实施计划 | 本文档 |
| 开始 Phase 1.1 | `design/evidence-bundle.md` (待创建) |

---

## 变更日志

| 日期 | 变更 | 作者 |
|------|------|------|
| 2024-03-08 | 初版创建 | Claude Code |
