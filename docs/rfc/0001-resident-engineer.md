# RFC-0001: Resident Engineer

**Status**: Draft
**Author**: Claude + Polaris Core Team
**Created**: 2026-03-07
**Target Version**: v0.9.0 → v1.0.0

---

## 1. 一句话目标

把 Polaris 从"会执行任务的多智能体系统"，升级成"有持续身份、长期议程、可提议目标、会反思决策、能在安全边界内自我进化的常驻工程代理"。

---

## 2. 核心哲学

### 2.1 不是什么

- **不是 AGI**: 我们不追求通用智能，不追求自我意识的幻觉
- **不是 God Object**: 不会有一个 `core/agi/orchestrator.py` 统治一切
- **不是自主失控**: Resident 有提议权，默认没有无条件执行权
- **不是 prompt theater**: 不要假装会了， capability graph 必须有证据

### 2.2 是什么

- **常驻进程**: 有 `start/stop/tick/recover` 生命周期，backend 重启后能恢复
- **可审计**: 每个决策都有溯源（为什么选 A 不选 B，预期什么，实际如何）
- **可进化**: 能从历史运行中学习，但必须在 shadow 中验证才能晋级
- **有边界**: 运行模式从 `observe` 起步，门禁全过才到 `bounded_auto`

---

## 3. 架构边界

### 3.1 新增子系统

```
src/backend/app/resident/
├── __init__.py
├── models.py              # 5 个核心对象的数据模型
├── storage.py             # Decision trace, agenda, identity 的持久化
├── service.py             # ResidentService: start/stop/tick/recover
├── identity.py            # ResidentIdentity: 我是谁、维护什么、能力边界
├── agenda.py              # 持续议程：backlog、机会、风险、实验计划
├── decision_trace.py      # 决策溯源：选项、选择、预期、实际、裁决
├── meta_cognition.py      # 元认知：策略得分、决策质量评估
├── goal_governor.py       # 目标治理：提议 → 门禁 → 批准 → 执行
├── capability_graph.py    # 能力图：我会什么、不会什么、证据在哪
├── counterfactual_lab.py  # 反事实实验室：离线回放、策略比较
├── skill_foundry.py       # 技能工坊：从成功抽取可复用技能
└── self_improvement_lab.py # 自我改进实验室：shadow → A/B → promote

src/backend/api/v2/resident.py       # Resident 控制 API
src/frontend/src/app/components/resident/ResidentWorkspace.tsx  # UI
```

### 3.2 修改现有代码

```
src/backend/app/orchestration/workflows/pm_workflow.py:137
├── 注入 DecisionTracer
└── 关键决策点生成 decision_id

src/backend/app/orchestration/workflows/director_workflow.py:110
├── 注入 DecisionTracer
└── 任务分配决策记录选项与选择

src/backend/app/orchestration/runtime/embedded/engine.py:69
├── 执行决策注入决策溯源
└── 结果回填 actual_outcome

src/backend/core/polaris_loop/anthropomorphic/memory_store.py:318
├── 所有写入必须带 evidence refs
└── 支持检索日志导出

src/backend/core/polaris_loop/anthropomorphic/reflection.py:109
├── 降级为 insight provider
└── 不再承担元认知主职责

src/backend/core/polaris_loop/context_engine/engine.py:292,332
├── 清掉 placeholder
└── LLM summary 未接通时 fail-closed
```

---

## 4. 数据模型

### 4.1 ResidentIdentity

```python
@dataclass
class ResidentIdentity:
    """
    Resident 的持续身份
    存储于 workspace/.polaris/resident/identity.json
    """
    resident_id: str                    # UUID，创建后不变
    version: str                        # 身份模式版本
    created_at: datetime
    updated_at: datetime

    # 核心声明
    mission: str                        # 例如："维护 X 项目的工程卓越"
    values: List[str]                   # 例如：["安全优先", "可维护性", "透明决策"]
    owner: str                          # 人类负责人

    # 运行状态
    operating_mode: OperatingMode       # observe | propose | assist | bounded_auto | lab_only
    active_workspace: str               # 当前工作区

    # 谱系
    memory_lineage: str                 # 记忆版本，用于迁移
    capability_profile: CapabilityProfile  # 能力快照

    # 恢复状态
    last_tick: datetime
    pending_proposals: List[str]        # 未决目标提议 ID
    last_agenda_hash: str               # 议程校验

class OperatingMode(Enum):
    """运行模式，严格渐进"""
    OBSERVE = "observe"                 # 只观察、记录、学习
    PROPOSE = "propose"                 # 可以提议目标，需批准
    ASSIST = "assist"                   # 可以辅助执行，需人类确认
    BOUNDED_AUTO = "bounded_auto"       # 限定范围内的自动执行
    LAB_ONLY = "lab_only"               # 只在 shadow 环境运行
```

### 4.2 GoalProposal

```python
@dataclass
class GoalProposal:
    """
    Resident 提出的目标
    必须经过 GoalGovernor 的 5 道门禁
    """
    proposal_id: str
    resident_id: str
    created_at: datetime

    # 提议内容
    type: GoalType                      # maintenance | reliability | knowledge | capability
    source: str                         # 触发来源：failure_pattern | repeated_command | capability_gap | drift_detection
    title: str
    description: str
    motivation: str                     # 为什么提议这个目标

    # 证据链
    evidence_refs: List[str]            # 关联的 DecisionRecord ID
    supporting_metrics: Dict[str, float]

    # 资源估算
    expected_value: Dict[str, float]    # 各维度预期价值
    estimated_cost: Dict[str, float]    # 时间、token、计算资源
    risk_score: float                   # 0-1
    scope: str                          # 影响范围

    # 治理状态
    approval_state: ApprovalState
    gate_results: Dict[str, GateResult] # 各门禁结果
    approved_by: Optional[str]          # 批准人（系统或人类）

    # 执行跟踪
    pm_contract_id: Optional[str]       # 批准后转换成的 PM 任务

class GoalType(Enum):
    MAINTENANCE = "maintenance"         # 维护性工作：债务清理、重构
    RELIABILITY = "reliability"         # 可靠性提升：测试增强、监控
    KNOWLEDGE = "knowledge"             # 知识积累：文档、模式提取
    CAPABILITY = "capability"           # 能力提升：新技能、工具集成
```

### 4.3 DecisionRecord

```python
@dataclass
class DecisionRecord:
    """
    决策溯源记录
    append-only，存储于 workspace/.polaris/resident/decision_trace.jsonl
    """
    decision_id: str
    timestamp: datetime

    # 决策者上下文
    actor: str                          # pm | director | qa | resident
    goal_id: Optional[str]              # 所属目标
    task_id: Optional[str]              # 所属任务

    # 决策上下文
    context_refs: List[str]             # 引用的 memory、file、evidence
    situation_summary: str              # 当时的情况摘要

    # 选项与选择（关键）
    options: List[Option]               # 考虑的选项
    selected_option: str                # 选择的选项 ID
    selection_reason: str               # 选择理由

    # 预期 vs 实际
    expected_outcome: Dict[str, Any]    # 预期结果
    actual_outcome: Optional[Dict[str, Any]]  # 实际结果（后填）
    outcome_verdict: Optional[Verdict]  # 裁决：better | expected | worse | failed

    # 关联
    parent_decision_id: Optional[str]   # 父决策（形成链）
    child_decisions: List[str]          # 子决策

@dataclass
class Option:
    option_id: str
    description: str
    expected_pros: List[str]            # 预期优势
    expected_cons: List[str]            # 预期劣势
    confidence: float                   # 当时对该选项的置信度
    rejection_reason: Optional[str]     # 如果未被选择，为什么
```

### 4.4 ExperimentRecord

```python
@dataclass
class ExperimentRecord:
    """
    反事实实验记录
    存储于 workspace/.polaris/resident/experiments/
    """
    experiment_id: str
    created_at: datetime

    # 实验设计
    hypothesis: str                     # 假设：如果改 X，Y 会改善
    baseline_config: Dict[str, Any]     # 基线配置
    intervention: Dict[str, Any]        # 干预：改了什么

    # 场景
    test_scenarios: List[str]           # 测试场景 ID（历史运行）

    # 结果
    metrics_before: Dict[str, float]
    metrics_after: Dict[str, float]
    confidence: float                   # 统计置信度
    improvement_ratio: float            # 改善比例

    # 安全
    rollback_plan: str                  # 回滚方案
    actual_rollback_tested: bool        # 是否测试过回滚

    # 状态
    state: ExperimentState              # pending | running | completed | promoted | rejected
    promotion_date: Optional[datetime]
```

### 4.5 SkillArtifact

```python
@dataclass
class SkillArtifact:
    """
    可复用技能
    从成功运行中自动提取
    存储于 workspace/.polaris/resident/skills/
    """
    skill_id: str
    version: str
    created_at: datetime

    # 触发条件
    trigger: Trigger                    # 什么情况下激活
    preconditions: List[Condition]      # 前置条件

    # 执行步骤
    steps: List[Step]                   # 可执行步骤
    required_tools: List[str]           # 需要的工具

    # 证据与验证
    evidence_refs: List[str]            # 来源 DecisionRecord
    success_rate: float                 # 历史成功率

    # 失败模式
    failure_modes: List[FailureMode]    # 已知的失败方式

    # 元数据
    domain: str                         # 适用领域
    complexity_score: float             # 复杂度
    confidence: float                   # 整体置信度

@dataclass
class Trigger:
    """触发条件"""
    pattern: str                        # 匹配模式（代码、错误、任务描述）
    confidence_threshold: float         # 触发置信度阈值

@dataclass
class Step:
    """执行步骤"""
    order: int
    action: str                         # 动作类型
    params: Dict[str, Any]              # 参数模板
    verification: Optional[str]         # 验证方法
```

---

## 5. 运行模式

Resident 有 5 个严格渐进的运行模式：

```
observe → propose → assist → bounded_auto → lab_only
  ↑___________________________________________|
                （可回退）
```

| 模式 | 权限 | 人类干预 | 适用阶段 |
|------|------|---------|---------|
| **observe** | 只记录、不行动 | 启动/停止 | Phase 0-1 |
| **propose** | 可提议目标 | 批准每个提议 | Phase 2 |
| **assist** | 可辅助执行 | 确认关键步骤 | Phase 3 |
| **bounded_auto** | 限定范围自动 | 异常时介入 | Phase 4-5 |
| **lab_only** | 只在 shadow 运行 | 观察结果 | 实验期间 |

**模式切换条件**：
- 升级：当前模式稳定运行 N 天，通过安全审计
- 降级：决策质量下降、出现异常行为、人类命令

---

## 6. 分阶段实施计划

### Phase 0: 基座整备（2 周）

**目标**: 把"会说自己在思考"改成"真的能审计自己为什么这样决策"

**新增模块**:
- `resident/models.py` - 核心数据模型
- `resident/storage.py` - Decision trace 存储
- `resident/decision_trace.py` - 决策溯源核心

**修改现有代码**:
- `pm_workflow.py:137` - 注入决策溯源
- `director_workflow.py:110` - 注入决策溯源
- `reflection.py:109` - 支持 evidence refs
- `context_engine/engine.py:332` - 清掉 placeholder

**关键任务**:
1. 定义 `decision_trace.jsonl` append-only 格式
2. 给 PM/Director/QA 每个关键决策打 `decision_id`
3. 所有 memory/reflection 写入必须带 evidence refs
4. **硬门禁**: LLM summary 未接通时必须 fail-closed，不允许写假摘要

**验收标准**:
- [ ] 一次完整 PM→Director→QA 运行，关键决策覆盖率 >= 90%
- [ ] workspace/brain 中 Resident 相关记忆 evidence refs 覆盖率 >= 95%
- [ ] 不再出现 placeholder summary 落盘

---

### Phase 1: Resident Kernel（3 周）

**目标**: 让"灵魂驻留"先有真正的进程语义

**新增模块**:
- `resident/service.py` - ResidentService 生命周期
- `resident/identity.py` - 身份管理
- `resident/agenda.py` - 议程管理
- `api/v2/resident.py` - 控制 API

**修改现有代码**:
- `runtime_ws_status.py` - 投影 Resident 状态
- `runtime_projection.py` - 包含 Resident 维度

**关键任务**:
1. 实现 `ResidentService.start/stop/tick/recover`
2. 建立持久化身份文件 `identity.json`
3. 建立议程文件 `agenda.jsonl`
4. 支持 backend 重启后恢复 identity + agenda + mode + pending proposals
5. 把 resident 状态投影到现有 runtime websocket

**验收标准**:
- [ ] Backend 重启后 Resident 5 秒内恢复状态
- [ ] `wake/sleep/tick` 幂等
- [ ] UI 可看到 Resident mode、agenda、last decision、pending goals

---

### Phase 2: 元认知 v1（3 周）

**目标**: 反思"决策过程"，不是反思"输出文本"

**新增模块**:
- `resident/meta_cognition.py` - 元认知引擎
- `resident/capability_graph.py` - 能力图

**修改现有代码**:
- `reflection.py:109` - 降级为 insight provider
- `thinking/engine.py:54` - 支持选项提取

**关键任务**:
1. 每次决策记录备选方案，不只记最终选择
2. 记录预期结果与实际结果差异
3. 形成策略得分表：
   - `task_split` - 任务拆分策略
   - `test_first` - 测试优先策略
   - `surgical_patch` - 精确修补策略
   - `broad_refactor` - 大范围重构策略
4. 把旧 reflection 降级成 insight provider，不再承担元认知主职责

**验收标准**:
- [ ] 每个已执行任务都有 `options + selected + expected + actual`
- [ ] 至少形成 10 个可统计策略标签
- [ ] 能回答"为什么这次不用方案 B"

---

### Phase 3: 目标提议与治理（4 周）

**目标**: 给 Resident 提议目标的能力，但不给它无条件执行权

**新增模块**:
- `resident/goal_governor.py` - 目标治理

**修改现有代码**:
- `task_quality_gate.py` - 集成 goal 来源
- `planning_pipeline.py` - 支持 Resident 提议的目标

**关键任务**:
1. 定义 4 类目标：`maintenance`、`reliability`、`knowledge`、`capability`
2. Goal proposal 来源限制为：
   - 连续失败模式
   - 重复人工指令
   - 能力缺口
   - 文档/测试/策略漂移
3. 实现 5 道门禁：
   - `scope_gate` - 范围检查
   - `policy_gate` - 策略合规
   - `budget_gate` - 资源预算
   - `novelty_gate` - 新颖性评估
   - `approval_gate` - 最终批准
4. Proposal 被批准后，转换成标准 PM contract，不绕过 PM

**验收标准**:
- [ ] Resident 能从最近 20 次运行中提出候选目标
- [ ] 零个未批准目标被直接执行
- [ ] 每个 goal proposal 都有明确 evidence refs 和 budget

---

### Phase 4: 反事实实验室（4 周）

**目标**: 从"事后总结"升级为"离线比较如果当时换策略会怎样"

**新增模块**:
- `resident/counterfactual_lab.py` - 反事实实验

**修改现有代码**:
- `runtime/embedded/engine.py` - 支持 shadow 重放
- `error_classifier.py` - 输出失败模式标签

**关键任务**:
1. 基于历史失败 run 回放 alternative strategy
2. 先做**工程因果**，不做通用因果学术系统
3. 可干预维度限定为：
   - `prompt appendix`
   - `context pack policy`
   - `task split heuristic`
   - `retry strategy`
   - `execution order`
4. 形成 `intervention ledger`

**验收标准**:
- [ ] 至少 3 类失败能做离线 replay
- [ ] 每类失败能输出"最佳替代策略"和相对提升
- [ ] 所有实验都有 `baseline、intervention、metrics、rollback note`

---

### Phase 5: 技能工坊（5 周）

**目标**: 跨项目迁移的不是"模糊记忆"，而是"可验证技能"

**新增模块**:
- `resident/skill_foundry.py` - 技能提取与复用

**修改现有代码**:
- `meta_prompting.py` - 区分 hint vs skill
- `context_engine/providers.py` - 支持 skill retrieval

**关键任务**:
1. 从高质量成功 run 自动抽取 `SkillArtifact`
2. 技能必须包含：触发条件、前置条件、步骤、证据、失败模式
3. Skill retrieval 进入 PM planning 和 Director execution 的 context
4. 与现有 meta prompt hints 区分：hint 是短期修补，skill 是长期可复用能力

**验收标准**:
- [ ] 至少抽取 20 个技能样本
- [ ] 对重复任务族，计划质量或执行成功率有可测提升
- [ ] Skill 可被禁用、回滚、版本化

---

### Phase 6: 自我改进实验室（4 周）

**目标**: 允许 Resident 改进 Polaris，但只能在实验室里先证明

**新增模块**:
- `resident/self_improvement_lab.py` - 自我改进

**关键任务**:
1. 自改对象限制为：
   - `prompt strategy`
   - `context ranking`
   - `retry/backoff policy`
   - `task decomposition heuristic`
   - `goal scoring rule`
2. **禁止直接自动修改**（即使测试通过）：
   - `policy core`
   - `permission system`
   - `approval path`
3. 达到阈值后生成 patch proposal，由人类批准
4. 完整链路：`shadow → A/B → promote`

**验收标准**:
- [ ] 至少 1 个改进通过 shadow → A/B → promoted 全链路
- [ ] 任何失败改进都能自动回滚
- [ ] 不允许静默改主干

---

## 7. 关键里程碑

| 天数 | 里程碑 | 标志 |
|------|--------|------|
| **30** | Resident Kernel | 有 identity、agenda、decision trace；UI 可见 Resident 状态 |
| **60** | Goal Governor | Resident 可以稳定提议目标；所有提议都有 evidence 和 budget |
| **90** | Learning System | Counterfactual Lab 可回放失败；Skill Foundry 能抽取并复用技能 |
| **120** | Self-Improvement | 至少一个改进通过 shadow→A/B→promote；Resident 开始拥有"持续驻留的工程人格" |

---

## 8. 必须先还的技术债

| 文件 | 行号 | 问题 | 清理方式 |
|------|------|------|---------|
| `context_engine/engine.py` | 332 | Placeholder summary | Fail-closed，未接通时不写 |
| `thinking/engine.py` | 54 | UI 事件伪装成 reasoning | 明确标记为 observability-only |
| `standalone_agent.py` | 1203 | 模板式 autonomy | 标记为 experimental，不混入主叙事 |
| `reflection.py` | 109 | 承担元认知主职责 | 降级为 insight provider |

---

## 9. 硬门禁（Hard Gates）

1. **不存原始 CoT**: 只存结构化 `DecisionRecord`，原始 LLM 输出经过解析才能入库
2. **无证据不决策**: 不允许没有 evidence refs 的 resident memory 进入高优先级决策
3. **不假装会了**: UI 不能显示"系统会了"，除非 `capability_graph` 有证据支撑
4. **模式升级审计**: 从 `observe` → `propose` → `assist` 每次升级都需要：
   - N 天稳定运行
   - 决策质量评估报告
   - 人类批准

---

## 10. 接口定义

### 10.1 ResidentService API

```python
class ResidentService:
    async def start(self, workspace: str) -> ResidentIdentity
    async def stop(self) -> None
    async def tick(self) -> AgendaUpdate  # 主循环
    async def recover(self) -> ResidentIdentity  # 崩溃恢复

    async def propose_goal(self, context: dict) -> Optional[GoalProposal]
    async def evaluate_decision(self, decision_id: str) -> DecisionEvaluation

    # 运行模式管理
    async def request_mode_upgrade(self, target: OperatingMode) -> bool
    async def emergency_downgrade(self, reason: str) -> None
```

### 10.2 REST API (v2/resident.py)

```python
# 控制
POST   /v2/resident/start
POST   /v2/resident/stop
POST   /v2/resident/tick
GET    /v2/resident/status

# 身份与议程
GET    /v2/resident/identity
PUT    /v2/resident/identity
GET    /v2/resident/agenda
GET    /v2/resident/decisions
GET    /v2/resident/decisions/{id}

# 目标提议
GET    /v2/resident/proposals
POST   /v2/resident/proposals/{id}/approve
POST   /v2/resident/proposals/{id}/reject

# 技能与能力
GET    /v2/resident/skills
GET    /v2/resident/capabilities

# 实验
GET    /v2/resident/experiments
POST   /v2/resident/experiments/{id}/promote
POST   /v2/resident/experiments/{id}/rollback
```

---

## 11. 风险评估

| 风险 | 可能性 | 影响 | 缓解措施 |
|------|--------|------|---------|
| Decision trace 性能开销 | 中 | 中 | 异步写入、采样、压缩 |
| Resident 状态丢失 | 低 | 高 |  WAL + 定期快照 |
| Goal 提议质量差 | 中 | 中 | 5 道门禁、人类批准 |
| Self-improvement 失控 | 低 | 极高 | Shadow only、禁止改核心策略、人类批准 |
| 存储膨胀 | 中 | 中 | TTL、归档、摘要 |

---

## 12. 结论

这不是 3 个月做 AGI。这是 3-4 个月做出 **Resident v1**：一个会提议、会反思、会离线学习、可安全进化的常驻工程代理。

6 到 9 个月做出完整系统：
- 有持续身份（identity）
- 有长期议程（agenda）
- 可提议目标（goal governor）
- 会反思决策（meta-cognition）
- 能在安全边界内自我进化（self-improvement lab）

这条路是真能落地的，而且和 Polaris 现有架构是对齐的。

---

## 13. 下一步行动

**等待批准此 RFC 后**：

1. **创建目录结构**: `src/backend/app/resident/`
2. **实现 Phase 0**: `models.py`, `storage.py`, `decision_trace.py`
3. **技术债清理**: 清掉 4 个 placeholder
4. **集成验证**: PM→Director→QA 决策覆盖率 >= 90%

**需要决策的问题**:

1. 是否接受此 RFC 的 6 Phase 划分？
2. Resident 存储是否复用现有的 `workspace/.polaris/` 还是新建 `.polaris/resident/`？
3. 是否先在单个 workspace 试点，再全量推广？

---

**作者**: Claude + Polaris Core Team
**状态**: Draft awaiting review
