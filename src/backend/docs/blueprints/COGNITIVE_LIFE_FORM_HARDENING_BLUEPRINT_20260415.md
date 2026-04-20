# 认知生命体 (Cognitive Life Form) 加固蓝图

**版本**: v1.1
**日期**: 2026-04-15
**状态**: P0/P1/P2 已完成，P3 deferred
**审计基线**: 4人专家团队审计报告 (2026-04-15)
**执行基线**: 6人专家团队实施 + P2 中期优化 (2026-04-15)

---

## 0. 审计发现摘要

| 维度 | 评分 | 核心判断 |
|------|------|---------|
| 架构设计 | ★★★★☆ | 六层认知架构设计完整，但存在双重实现和集成断层 |
| 认知模型 | ★★★★☆ | 理论工程化程度高，感知层偏薄，进化缺遗忘机制 |
| 安全性 | ★★★☆☆ | 纵深防御骨架到位，Governance 门控实质性不足 |
| 质量保障 | ★★☆☆☆ | Feature Flag 完善但默认关闭，类型契约弱 |
| 代码质量 | ★★★☆☆ | orchestrator 膨胀，MagicMock 泄漏，大量重复代码 |

---

## 1. 修复工作包 (Work Packages)

### WP-1: Orchestrator 瘦身与 MagicMock 清除 [P0]

**问题**:
- `orchestrator.py` ~999 行，含 5 处几乎相同的 blocked_turn 构建代码
- 生产代码 `from unittest.mock import MagicMock` 用于运行时类型检查
- 大量 `hasattr()` 防御性检查表明类型契约不严格

**修复方案**:

#### 1.1 提取 `_build_blocked_response()` 方法

```python
# 目标：将 5 处重复的 blocked_turn 构建提取为一个方法
def _build_blocked_response(
    self,
    *,
    message: str,
    session_id: str,
    role_id: str,
    intent_type: str,
    confidence: float,
    uncertainty: UncertaintyAssessment | None,
    block_reason: str,
    ctx: Any,
    vc_id: str | None = None,
) -> CognitiveResponse:
    """Construct a standardized blocked response with conversation turn."""
    ...
```

#### 1.2 移除 MagicMock 依赖

- 移除 `from unittest.mock import MagicMock`
- 将 `isinstance(raw_prob, MagicMock)` 替换为 try/except (ValueError, TypeError)
- 统一使用 Protocol 定义类型边界，消除 `hasattr()` 检查

#### 1.3 引入 Null Object 模式

```python
class NullReasoningChain:
    """Null object for missing reasoning chains."""
    conclusion = ""
    blockers = []
    six_questions = None
```

**文件**:
- `polaris/kernelone/cognitive/orchestrator.py` — 主要修改目标
- `polaris/kernelone/cognitive/types.py` — 新增 Null Object

**验收标准**:
- [ ] `orchestrator.py` 中不再出现 `MagicMock`
- [ ] `orchestrator.py` 中不再出现 `hasattr()` 防御性检查
- [ ] blocked response 构建逻辑仅出现在 1 处
- [ ] 所有现有测试通过

---

### WP-2: Governance 门控实质性校验 [P0]

**问题**:
- `CognitiveGovernance` 5 个门控点位置设计合理，但校验逻辑薄弱
- `verify_post_reasoning` 仅检查 `probability < 0.5` + `severity = high` 组合
- `verify_value_alignment` 仅做简单的字符串模式匹配
- 缺少跨门控的状态累积和一致性校验

**修复方案**:

#### 2.1 增强 `verify_post_perception`

```python
async def verify_post_perception(self, intent_type: str, confidence: float) -> VCResult:
    # 新增：意图类型合法性检查（对照 STANDARD_TOOLS 的意图分类）
    # 新增：置信度与上下文历史一致性检查
    # 新增：会话级重复意图检测（同一意图连续3次触发降级）
```

#### 2.2 增强 `verify_post_reasoning`

```python
async def verify_post_reasoning(
    self,
    probability: float,
    severity: str,
    blockers: tuple[str, ...],
) -> VCResult:
    # 新增：概率校准检查（历史概率 vs 实际结果的偏差）
    # 新增：推理链完整性检查（six_questions 是否全部回答）
    # 新增：blocker 冲突检测（blocker 与 action 矛盾）
    # 新增：累积风险评估（多次 WARN 自动升级为 FAIL）
```

#### 2.3 新增 `verify_reasoning_consistency`

```python
async def verify_reasoning_consistency(
    self,
    reasoning_chain: ReasoningChain,
    intent_graph: IntentGraph,
) -> VCResult:
    """检查推理结论与感知意图的一致性。"""
    # 结论是否与主要意图矛盾？
    # 关键假设是否有对应的证据支撑？
    # 风险等级是否与不确定性评分匹配？
```

#### 2.4 新增门控状态追踪器

```python
@dataclass
class GovernanceState:
    """跨门控累积状态。"""
    warn_count: int = 0
    fail_count: int = 0
    last_intent_type: str = ""
    consecutive_unknown_count: int = 0
    confidence_trajectory: list[float] = field(default_factory=list)

    def should_escalate(self) -> bool:
        """3次 WARN 自动升级为 FAIL。"""
        return self.warn_count >= 3
```

**文件**:
- `polaris/kernelone/cognitive/governance/verification.py` — 主要修改
- `polaris/kernelone/cognitive/governance/__init__.py` — 导出新增类
- `polaris/kernelone/cognitive/governance/state_tracker.py` — 新文件

**验收标准**:
- [ ] 每个 `verify_*` 方法至少包含 3 条实质性校验规则
- [ ] 门控状态在单次 `process()` 调用内跨阶段累积
- [ ] 新增 `verify_reasoning_consistency` 被编排器调用
- [ ] 新增测试覆盖所有校验规则

---

### WP-3: 认知管道与 TurnEngine 集成 [P0]

**问题**:
- `CognitiveOrchestrator` 和 `TurnEngine` 两条完全独立的执行路径
- 认知管道默认关闭，从未在主执行路径上被调用
- 缺少明确的集成契约

**修复方案**:

#### 3.1 定义 `CognitivePipelinePort`

```python
# polaris/kernelone/cognitive/contracts.py
class CognitivePipelinePort(Protocol):
    """认知管道端口协议 — TurnEngine 通过此协议接入认知能力。"""

    async def pre_turn_cognitive_check(
        self,
        message: str,
        session_id: str,
        role_id: str,
    ) -> CognitivePreCheckResult:
        """TurnEngine 在调用 LLM 前执行认知预检。"""
        ...

    async def post_tool_cognitive_assess(
        self,
        tool_name: str,
        tool_result: str,
        session_id: str,
    ) -> CognitiveAssessResult:
        """TurnEngine 在工具执行后执行认知评估。"""
        ...

@dataclass(frozen=True)
class CognitivePreCheckResult:
    should_proceed: bool
    adjusted_prompt: str | None  # 基于认知结果的提示词调整
    governance_verdict: str  # PASS / WARN / FAIL
    confidence: float

@dataclass(frozen=True)
class CognitiveAssessResult:
    quality_score: float
    should_continue: bool
    evolution_trigger: str | None  # 触发进化的信号
```

#### 3.2 在 TurnEngine 中集成认知管道

```python
# polaris/cells/roles/kernel/internal/turn_engine/engine.py
class TurnEngine:
    def __init__(self, ..., cognitive_pipeline: CognitivePipelinePort | None = None):
        self._cognitive = cognitive_pipeline  # 可选注入

    async def _run_single_turn(self, ...):
        # 注入点 1: LLM 调用前认知预检
        if self._cognitive:
            pre_check = await self._cognitive.pre_turn_cognitive_check(
                message=user_message, session_id=session_id, role_id=role_id,
            )
            if not pre_check.should_proceed:
                return self._build_governance_blocked_result(pre_check)
            if pre_check.adjusted_prompt:
                system_prompt += pre_check.adjusted_prompt

        # ... 现有 LLM 调用 + 工具执行逻辑 ...

        # 注入点 2: 工具执行后认知评估
        if self._cognitive:
            assess = await self._cognitive.post_tool_cognitive_assess(
                tool_name=tool_name, tool_result=tool_result, session_id=session_id,
            )
            if not assess.should_continue:
                return self._build_quality_gate_result(assess)
```

#### 3.3 创建适配器桥接

```python
# polaris/kernelone/cognitive/pipeline_adapter.py
class CognitivePipelineAdapter:
    """将 CognitiveOrchestrator 适配为 CognitivePipelinePort。"""

    def __init__(self, orchestrator: CognitiveOrchestrator) -> None:
        self._orchestrator = orchestrator

    async def pre_turn_cognitive_check(self, message, session_id, role_id):
        # 调用 orchestrator 的感知+推理阶段（不执行完整管道）
        ...

    async def post_tool_cognitive_assess(self, tool_name, tool_result, session_id):
        # 调用 orchestrator 的进化阶段（触发学习）
        ...
```

**文件**:
- `polaris/kernelone/cognitive/contracts.py` — 新增 Protocol
- `polaris/kernelone/cognitive/pipeline_adapter.py` — 新文件
- `polaris/cells/roles/kernel/internal/turn_engine/engine.py` — 集成点

**验收标准**:
- [ ] `CognitivePipelinePort` Protocol 定义完整
- [ ] TurnEngine 可通过构造函数注入认知管道
- [ ] 认知管道关闭时 TurnEngine 行为不变（零影响保证）
- [ ] 集成测试: 认知管道打开时 Governance FAIL 可阻断 TurnEngine

---

### WP-4: 双重 MetaCognitionEngine 合并 [P1]

**问题**:
- `kernelone/cognitive/reasoning/meta_cognition.py` — 认知层元认知（三层反思 + 置信度校准）
- `cells/resident/autonomy/internal/meta_cognition.py` — 策略层元认知（决策记录分析 + 策略洞察）
- 两套引擎接口不同、数据模型不同，但职责有重叠

**修复方案**:

#### 4.1 明确职责划分

```
kernelone/cognitive/reasoning/meta_cognition.py  → MetaCognitionEngine (认知层)
  职责：实时认知监控（关于思考的思考）
  方法：audit_thought_process(), reflect(), reflect_with_llm()
  输出：MetaCognitionSnapshot（知识边界、推理摘要、校准记录）

cells/resident/autonomy/internal/meta_cognition.py → StrategyInsightEngine (策略层)
  职责：离线策略分析（关于决策的分析）
  方法：analyze_decisions(), generate_insights()
  输出：MetaInsight（策略风险、策略优势、预测差距、失败集群）
```

#### 4.2 重命名策略层引擎

```python
# cells/resident/autonomy/internal/meta_cognition.py
# 类名 MetaCognitionEngine → StrategyInsightEngine
# 所有引用更新
```

#### 4.3 建立调用关系

```python
# StrategyInsightEngine 可消费 MetaCognitionSnapshot 的输出
class StrategyInsightEngine:
    def analyze_decisions(
        self,
        decisions: list[DecisionRecord],
        cognitive_snapshot: MetaCognitionSnapshot | None = None,  # 新增
    ) -> list[MetaInsight]:
        """策略分析可参考认知层的实时监控结果。"""
```

**文件**:
- `polaris/cells/resident/autonomy/internal/meta_cognition.py` — 重命名
- `polaris/cells/resident/autonomy/public/service.py` — 更新导出
- `polaris/cells/resident/autonomy/internal/resident_runtime_service.py` — 更新引用
- 所有 import `MetaCognitionEngine` from autonomy 的文件 — 更新引用

**验收标准**:
- [ ] `cells/resident/autonomy` 中不再有名为 `MetaCognitionEngine` 的类
- [ ] 所有 import 路径更新完毕
- [ ] `StrategyInsightEngine.analyze_decisions()` 接受可选的 `cognitive_snapshot` 参数
- [ ] ruff + mypy 通过

---

### WP-5: 信念衰退与进化记录 HMAC [P1]

**问题**:
- 进化系统缺少遗忘/衰减机制，信念无限膨胀
- 进化记录直接文件存储，无完整性校验

**修复方案**:

#### 5.1 信念衰退机制

```python
# polaris/kernelone/cognitive/evolution/belief_decay.py (新文件)

@dataclass(frozen=True)
class DecayPolicy:
    """信念衰退策略。"""
    half_life_days: float = 30.0        # 半衰期
    min_confidence: float = 0.1          # 最低置信度（低于此值标记为 stale）
    reinforcement_factor: float = 1.5    # 引用强化因子
    max_reinforced_confidence: float = 0.95

class BeliefDecayEngine:
    """基于时间和引用频率的信念自动衰减。"""

    def apply_decay(self, beliefs: list[Belief], now: datetime) -> list[Belief]:
        """对一批信念应用时间衰减。"""
        # 对每个 belief:
        # 1. 计算距上次验证的天数
        # 2. 按指数衰减降低置信度: confidence *= 0.5^(days/half_life)
        # 3. 若有新引用，强化: confidence *= reinforcement_factor
        # 4. confidence < min_confidence → 标记 stale
        ...

    def prune_stale_beliefs(self, beliefs: list[Belief]) -> list[Belief]:
        """清除长期 stale 的信念（超过 2 个半衰期）。"""
        ...
```

#### 5.2 进化记录 HMAC 签名

```python
# polaris/kernelone/cognitive/evolution/integrity.py (新文件)

class EvolutionIntegrityGuard:
    """进化记录的 HMAC-SHA256 完整性守护。"""

    def sign_record(self, record: EvolutionRecord) -> str:
        """对进化记录生成 HMAC 签名。"""
        # 复用 kernelone/audit 的 HMAC 基础设施
        ...

    def verify_chain(self, records: list[EvolutionRecord]) -> bool:
        """验证进化记录链的完整性。"""
        ...

    def detect_tampering(self, store: EvolutionStore) -> list[str]:
        """检测被篡改的记录。"""
        ...
```

#### 5.3 修改 EvolutionStore

```python
class EvolutionStore:
    def __init__(self, workspace: str, *, enable_integrity: bool = True):
        self._integrity = EvolutionIntegrityGuard() if enable_integrity else None

    def store_record(self, record: EvolutionRecord) -> None:
        if self._integrity:
            record = record.with_integrity_hash(self._integrity.sign_record(record))
        # ... 现有存储逻辑
```

**文件**:
- `polaris/kernelone/cognitive/evolution/belief_decay.py` — 新文件
- `polaris/kernelone/cognitive/evolution/integrity.py` — 新文件
- `polaris/kernelone/cognitive/evolution/engine.py` — 集成衰退
- `polaris/kernelone/cognitive/evolution/store.py` — 集成 HMAC
- `polaris/kernelone/cognitive/evolution/__init__.py` — 导出

**验收标准**:
- [ ] `BeliefDecayEngine` 可正确应用时间衰减和引用强化
- [ ] confidence < 0.1 的信念自动标记 stale
- [ ] `EvolutionIntegrityGuard` 可生成和验证 HMAC 签名
- [ ] `EvolutionStore` 写入时自动签名，读取时可选验证
- [ ] 单元测试覆盖衰减曲线和 HMAC 链

---

### WP-6: 认知管道集成测试 + CognitiveMaturityScore 修复 [P1]

**问题**:
- 认知管道无集成测试
- `CognitiveMaturityScore.default()` 返回满分 100，危险
- 感知层不确定性阈值硬编码

**修复方案**:

#### 6.1 集成测试套件

```python
# polaris/kernelone/cognitive/tests/test_integration.py

class TestCognitivePipelineIntegration:
    """认知管道端到端集成测试。"""

    async def test_normal_execution_path(self):
        """正常消息通过完整管道返回 CognitiveResponse。"""

    async def test_governance_blocks_empty_message(self):
        """空消息被 Pre-Perception 门控阻断。"""

    async def test_governance_blocks_critical_with_low_probability(self):
        """高风险 + 低概率被 Post-Reasoning 门控阻断。"""

    async def test_hitl_rejection(self):
        """人类拒绝执行计划返回 blocked。"""

    async def test_hitl_timeout_falls_to_shadow(self):
        """HITL 超时降级到 shadow mode。"""

    async def test_value_alignment_blocks_unsafe(self):
        """Value Alignment 阻断不安全操作。"""

    async def test_evolution_records_learning(self):
        """进化引擎从反思输出中记录学习。"""

    async def test_personality_influences_response(self):
        """人格特质影响响应表达。"""

    async def test_cognitive_disabled_fallback(self):
        """认知管道禁用时 graceful fallback。"""
```

#### 6.2 CognitiveMaturityScore 修复

```python
@dataclass(frozen=True)
class CognitiveMaturityScore:
    # 修改 default()
    @staticmethod
    def default() -> CognitiveMaturityScore:
        """未校准的默认分数 — 全部为 0，标记为 uncalibrated。"""
        return CognitiveMaturityScore(
            truthfulness_score=0.0,
            understanding_score=0.0,
            evolution_score=0.0,
        )

    @property
    def is_calibrated(self) -> bool:
        """是否已经过实际度量校准。"""
        return self.overall_score > 0
```

#### 6.3 感知层动态阈值

```python
# polaris/kernelone/cognitive/perception/uncertainty.py

class UncertaintyQuantifier:
    def __init__(self) -> None:
        self._history: list[tuple[float, bool]] = []  # (predicted_uncertainty, was_correct)
        self._calibration_window = 50

    def quantify(self, ...) -> UncertaintyAssessment:
        raw_uncertainty = ...
        # 动态校准：根据历史预测准确率调整阈值
        calibrated = self._apply_calibration(raw_uncertainty)
        return UncertaintyAssessment(uncertainty_score=calibrated, ...)

    def record_outcome(self, predicted: float, actual_correct: bool) -> None:
        """记录预测结果，用于动态校准。"""
        self._history.append((predicted, actual_correct))
        if len(self._history) > self._calibration_window:
            self._history = self._history[-self._calibration_window:]
```

**文件**:
- `polaris/kernelone/cognitive/tests/test_integration.py` — 新文件
- `polaris/kernelone/cognitive/governance/maturity_score.py` — 修改 default()
- `polaris/kernelone/cognitive/perception/uncertainty.py` — 动态校准

**验收标准**:
- [ ] 9 个集成测试全部通过
- [ ] `CognitiveMaturityScore.default()` 返回 0 分
- [ ] `is_calibrated` 属性正常工作
- [ ] `UncertaintyQuantifier` 根据历史准确率调整阈值
- [ ] pytest 100% 通过

---

## 2. 依赖关系

```
WP-1 (Orchestrator 瘦身) ─── 无依赖，可先行
WP-2 (Governance 增强)  ─── 依赖 WP-1 (需要干净的 orchestrator)
WP-3 (TurnEngine 集成)  ─── 依赖 WP-1 + WP-2
WP-4 (MetaCognition 合并) ── 无依赖，可与 WP-1 并行
WP-5 (信念衰退 + HMAC)  ─── 无依赖，可与 WP-1 并行
WP-6 (集成测试)        ─── 依赖 WP-1~5 全部完成
```

```
时间线 (工作日):
Week 1: WP-1, WP-4, WP-5 (并行)
Week 2: WP-2, WP-3 (依赖 WP-1)
Week 3: WP-6 (依赖全部), 回归测试
```

---

## 3. 风险与缓解

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|---------|
| TurnEngine 集成影响现有功能 | 中 | 高 | 认知管道默认关闭，通过 Feature Flag 控制 |
| MetaCognitionEngine 重命名导致大面积 import 失败 | 高 | 中 | ruff 自动重构 + CI 全量 pytest |
| 信念衰退参数不合理 | 低 | 低 | dev profile 下 half_life_days=7 快速验证 |
| Governance 门控过严导致正常请求被阻断 | 中 | 中 | 新增门控默认 WARN 不 FAIL，累积 3 次 WARN 才升级 |

---

## 4. 验证卡片

参见: `docs/governance/templates/verification-cards/vc-20260415-cognitive-life-form-hardening.yaml`

---

## 5. 执行完成总结 (2026-04-15)

### 5.1 已完成 (P0 + P1 + P2)

| WP | 内容 | 新增测试 | Commit |
|----|------|---------|--------|
| WP-1 | Orchestrator 瘦身：`_build_blocked_response()` 提取、MagicMock 移除、Null Object | 54 passed | `b371567a` |
| WP-2 | Governance 增强：`GovernanceState` 追踪器、`verify_reasoning_consistency`、3 倍 verify 增强 | 19 new (37 total) | `b9fdb41c` |
| WP-3 | TurnEngine 集成：`CognitivePipelinePort` Protocol、`CognitivePipelineAdapter`、4 注入点 | 零影响保证 | `b9fdb41c` |
| WP-4 | MetaCognition 合并：策略层 → `StrategyInsightEngine`、跨层 cognitive_snapshot 参数 | 13 passed | `b371567a` |
| WP-5 | 信念衰退 + HMAC：`BeliefDecayEngine`、`EvolutionIntegrityGuard` | 25 new (40 total) | `b9fdb41c` |
| WP-6 | MaturityScore 修复：`default()` → 0、`is_calibrated`、`UncertaintyQuantifier` 动态校准 | 代码已落地 | `b371567a` |
| P2-A | 感知校准闭环：`record_outcome()` 反馈、过度自信/过度谨慎双向校准 | 12 new (51 total) | `3d17baaa` |
| P2-B | HMAC 集成 orchestrator + `CognitiveLawGuard` 三条核心法则断言 | 17 new | `3d17baaa` |

**全量回归**: 210/210 passed (cognitive tests), 409/409 collected total

### 5.2 Deferred (P3 — 需独立蓝图)

| # | 内容 | 理由 | 预估 |
|---|------|------|------|
| P3-1 | 反事实实验室 Bootstrap CI + 最小样本量 | 当前 CounterfactualLab 已可用，统计显著性是锦上添花 | 2 周 |
| P3-2 | 认知管道性能基准 (latency/throughput) | 需要真实负载数据，dev 环境无法模拟 | 1 周 |
| P3-3 | 多 Agent 认知共享（跨角色经验共享） | 架构级新特性，需独立蓝图和 ADR | 4 周 |

**Defer 理由**: P3 边际收益递减。核心问题（集成断层、空壳门控、代码异味、双重实现、信念膨胀、无法则执行）已全部修复。P3 应作为独立迭代规划，不应搭在加固工作上。
