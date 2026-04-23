# 认知生命体 (Cognitive Life Form) 统一实施路线图

**版本**: v1.0
**日期**: 2026-04-08
**状态**: 10专家团队规划完成，待实施

---

## 1. 架构概述

### 1.1 核心定位

认知生命体是 Polaris 所有角色（PM/Architect/Chief Engineer/Director/QA/Scout）共享的**认知内核 (Cognitive Kernel)**，而非独立功能模块。

```
┌─────────────────────────────────────────────────────────────┐
│                     Role (角色特定层)                        │
│  PM Agent | Architect Agent | Chief Engineer | Director | QA│
├─────────────────────────────────────────────────────────────┤
│                  Cognitive Life Form Kernel                 │
│  ┌─────────┬──────────┬──────────┬───────────┬─────────┐ │
│  │Perception│ Reasoning │ Decision │ Execution │Evolution │ │
│  │ (感知层) │ (推理层)  │ (决策层)  │  (执行层)  │ (演化层) │ │
│  └─────────┴──────────┴──────────┴───────────┴─────────┘ │
│           6 协议: Intent | Critical | Meta | Cautious |   │
│                   Reflection | Value Alignment             │
├─────────────────────────────────────────────────────────────┤
│               KernelOne Runtime (共享底座)                   │
│  TurnEngine | ContextOS | Memory | Events | Tools          │
└─────────────────────────────────────────────────────────────┘
```

### 1.2 三大核心法则 (Law)

| # | 法则 | 执行要求 |
|---|------|----------|
| L1 | **Truthfulness > Consistency** |宁可承认错误也不维护虚假一致性 |
| L2 | **Understanding > Execution** | 必须理解意图后才能执行，必须在执行中监控理解 |
| L3 | **Evolution > Correctness** | 没有"永远正确"，只有"持续趋向正确" |

### 1.3 六协议架构

| 协议 | 核心功能 | 主要组件 |
|------|----------|----------|
| **Intent Understanding** | Surface→Deep→Unstated Needs 推理 | IntentGraph, SemanticParser, UncertaintyQuantifier |
| **Critical Thinking** | 六问法, Devil's Advocate | CriticalThinkingEngine, CTEngineMiddleware |
| **Meta-Cognition** | 思维自省, 置信度校准 | MetaCognitionEngine, ReflectionEngine |
| **Cautious Execution** | L0-L4风险分级, 回滚机制 | CautiousExecutionPolicy, RollbackManager |
| **Reflection & Evolution** | Belief Tracking, 知识沉淀 | EvolutionStore, BiasDefenseEngine |
| **Value Alignment** | 4视角评估, 陌生人测试 | ValueAlignmentService, IndependentAuditService |

---

## 2. 实施阶段总览

### Phase 0: 基础设施 (Week 1-2)

**目标**: 建立认知内核的骨架和公共组件

| # | 组件 | 文件位置 | 依赖 |
|---|------|----------|------|
| 0.1 | 认知生命体 Cell 目录 | `polaris/cells/cognitive/` | none |
| 0.2 | CognitiveRuntime 核心接口 | `polaris/kernelone/cognitive/contracts.py` | none |
| 0.3 | ClarityLevel 枚举 | `polaris/kernelone/cognitive/types.py` | none |
| 0.4 | ThinkingOutput/ActingOutput TypedDict | `polaris/kernelone/cognitive/types.py` | 0.3 |
| 0.5 | 事件类型注册 | `polaris/kernelone/events/typed/schemas.py` | EventSystem |
| 0.6 | 三法则 Schema | `docs/governance/schemas/cognitive-laws.schema.yaml` | none |

### Phase 1: 感知层 (Week 3-5)

**目标**: 构建 Intent Graph 和 Perception Engine

| # | 组件 | 文件位置 | 依赖 |
|---|------|----------|------|
| 1.1 | IntentGraph 数据模型 | `polaris/kernelone/cognitive/intent_graph/models.py` | 0.1, 0.3 |
| 1.2 | SemanticParser | `polaris/kernelone/cognitive/perception/semantic_parser.py` | IntentGraph |
| 1.3 | IntentInference | `polaris/kernelone/cognitive/perception/intent_inference.py` | SemanticParser |
| 1.4 | UncertaintyQuantifier | `polaris/kernelone/cognitive/perception/uncertainty.py` | IntentInference |
| 1.5 | ContextModeler | `polaris/kernelone/cognitive/perception/context_modeler.py` | UncertaintyQuantifier |
| 1.6 | IntentGraphStore | `polaris/kernelone/cognitive/intent_graph/store.py` | 1.1, MemoryStore |
| 1.7 | ContextOS Bridge | `polaris/kernelone/cognitive/intent_graph/context_os_bridge.py` | 1.6, ContextOS |

**集成点**: `polaris/kernelone/context/context_os/runtime.py` - `StateFirstContextOS.project()`

### Phase 2: 推理层 (Week 5-7)

**目标**: Critical Thinking Engine 和 Meta-Cognition

| # | 组件 | 文件位置 | 依赖 |
|---|------|----------|------|
| 2.1 | CriticalThinkingEngine | `polaris/kernelone/cognitive/reasoning/critical_thinking.py` | 1.2, 1.3 |
| 2.2 | SixQuestions Prompt Templates | `polaris/kernelone/cognitive/reasoning/six_questions.py` | 2.1 |
| 2.3 | DevilAdvocate | `polaris/kernelone/cognitive/reasoning/devil_advocate.py` | 2.2 |
| 2.4 | CTEngineMiddleware | `polaris/kernelone/cognitive/reasoning/ct_middleware.py` | 2.1 |
| 2.5 | MetaCognitionEngine | `polaris/kernelone/cognitive/meta/metah cognition_engine.py` | 1.4 |
| 2.6 | ReflectionEngine (3级) | `polaris/kernelone/cognitive/meta/reflection_engine.py` | 2.5 |
| 2.7 | MetaCognitionSnapshot dataclass | `polaris/kernelone/cognitive/meta/snapshot.py` | 2.5, 2.6 |
| 2.8 | ConfidenceCalibration | `polaris/kernelone/cognitive/meta/confidence.py` | 1.4, 2.6 |

**集成点**: 
- `polaris/kernelone/context/context_os/runtime.py` - HOOK #1 (after `_patch_working_state`), HOOK #2 (audit_reasoning_chain before `_seal_closed_episodes`)
- `polaris/kernelone/llm/engine/executor.py` - LLM pipeline middleware

### Phase 3: 决策层 (Week 7-9)

**目标**: Cautious Execution 和 Value Alignment

| # | 组件 | 文件位置 | 依赖 |
|---|------|----------|------|
| 3.1 | RiskLevel 枚举 (L0-L4) | `polaris/kernelone/cognitive/execution/risk_levels.py` | 0.3 |
| 3.2 | CautiousExecutionPolicy | `polaris/kernelone/cognitive/execution/cautious_policy.py` | 3.1 |
| 3.3 | RollbackManager | `polaris/kernelone/cognitive/execution/rollback_manager.py` | 3.1, Memory |
| 3.4 | DangerousPatternDetector | `polaris/kernelone/cognitive/execution/dangerous_patterns.py` | 3.1 |
| 3.5 | ValueAlignmentService | `polaris/cells/values/alignment_service.py` | 1.1, 2.5 |
| 3.6 | 4D Evaluation Matrix | `polaris/cells/values/evaluation_matrix.py` | 3.5 |
| 3.7 | StrangerTest | `polaris/cells/values/stranger_test.py` | 3.5 |
| 3.8 | ConflictResolution | `polaris/cells/values/conflict_resolution.py` | 3.6 |

**集成点**:
- `polaris/kernelone/llm/toolkit/executor/core.py` - Risk check before handler dispatch
- `polaris/cells/roles/kernel/internal/policy/layer/facade.py` - PolicyLayer 集成

### Phase 4: 执行层 (Week 9-11)

**目标**: Thinking-Acting 分离和执行引擎

| # | 组件 | 文件位置 | 依赖 |
|---|------|----------|------|
| 4.1 | ThinkingPhaseEngine | `polaris/kernelone/cognitive/execution/thinking_engine.py` | 2.1, 2.6 |
| 4.2 | ActingPhaseHandler | `polaris/kernelone/cognitive/execution/acting_handler.py` | 3.1, 3.3 |
| 4.3 | ClarityLevelAssigner | `polaris/kernelone/cognitive/execution/clarity.py` | 2.8, 4.1 |
| 4.4 | ThinkingPhaseEvent types | `polaris/kernelone/events/typed/schemas.py` | 0.5 |
| 4.5 | RoleTurnResult 扩展 | `polaris/cells/roles/profile/internal/schema.py` | 0.3, 4.1 |
| 4.6 | TextualEventBridge 扩展 | `polaris/delivery/cli/textual/event_bridge.py` | 4.4 |

**集成点**:
- `polaris/cells/roles/kernel/internal/turn_engine/engine.py` - `_run_thinking_phase()` 插入
- `polaris/cells/roles/kernel/internal/prompt_templates.py` - Thinking/Acting phase prompts

### Phase 5: 演化层 (Week 11-13)

**目标**: Memory Evolution 和 Belief Tracking

| # | 组件 | 文件位置 | 依赖 |
|---|------|----------|------|
| 5.1 | EvolutionStore | `polaris/kernelone/cognitive/evolution/store.py` | MemoryStore |
| 5.2 | Belief dataclass | `polaris/kernelone/cognitive/evolution/belief.py` | 5.1 |
| 5.3 | EvolutionRecord | `polaris/kernelone/cognitive/evolution/record.py` | 5.2 |
| 5.4 | BeliefTriggerClassifier | `polaris/kernelone/cognitive/evolution/triggers.py` | 5.2 |
| 5.5 | BiasDefenseEngine | `polaris/kernelone/cognitive/evolution/bias_defense.py` | 5.4 |
| 5.6 | KnowledgePrecipitation | `polaris/kernelone/cognitive/evolution/precipitation.py` | 5.3, 2.6 |
| 5.7 | MemoryManager Extension | `polaris/kernelone/akashic/memory_manager.py` | 5.1 |

**集成点**:
- `polaris/kernelone/akashic/memory_manager.py` - Add evolution tier
- `polaris/kernelone/akashic/protocols.py` - Add `EvolutionPort`

### Phase 6: 治理与验证 (Week 13-15)

**目标**: 认知成熟度评分和三法则验证

| # | 组件 | 文件位置 | 依赖 |
|---|------|----------|------|
| 6.1 | TruthfulnessMetrics | `polaris/kernelone/cognitive/governance/truthfulness.py` | 2.5 |
| 6.2 | UnderstandingMetrics | `polaris/kernelone/cognitive/governance/understanding.py` | 2.7, 3.5 |
| 6.3 | EvolutionMetrics | `polaris/kernelone/cognitive/governance/evolution_metrics.py` | 5.3, 5.6 |
| 6.4 | CognitiveMaturityScore | `polaris/kernelone/cognitive/governance/maturity_score.py` | 6.1, 6.2, 6.3 |
| 6.5 | Verification Cards Schema | `docs/governance/schemas/verification-card.schema.yaml` | none |
| 6.6 | CognitiveGovernancePipeline | `docs/governance/ci/cognitive-life-form-governance-pipeline.yaml` | 6.5 |
| 6.7 | CognitiveLifeFormGate | `docs/governance/ci/scripts/run_cognitive_life_form_gate.py` | 6.6 |

### Phase 7: 人格与交互 (Week 15-17)

**目标**: Conversational Personality 和角色认知特质

| # | 组件 | 文件位置 | 依赖 |
|---|------|----------|------|
| 7.1 | CognitiveTraits | `polaris/kernelone/cognitive/personality/traits.py` | 0.3 |
| 7.2 | InteractionPosture | `polaris/kernelone/cognitive/personality/posture.py` | 7.1 |
| 7.3 | CognitiveExpressions | `polaris/kernelone/cognitive/personality/expressions.py` | 7.1, 2.8 |
| 7.4 | COGNITIVE_PERSONALITY_LAYER | `polaris/cells/roles/kernel/internal/prompt_templates.py` | 7.1, 7.2 |
| 7.5 | PromptBuilder 扩展 | `polaris/cells/roles/kernel/internal/prompt_builder.py` | 7.4 |
| 7.6 | ConfidenceMetadata | `polaris/cells/roles/kernel/internal/llm_caller/response_types.py` | 2.8 |

---

## 3. 关键技术决策

### 3.1 ContextOS Bridge 策略

Context Pack → Intent Graph 采用 Bridge Adapter Pattern，不修改原始 ContextOS：

```
ContextOS (现有) ──Bridge──→ IntentGraph (新)
     │                           │
     └── ContextOSSnapshot ───────┘
```

### 3.2 Feature Flag rollout

每个 Phase 独立 feature flag，按 workspace 级别控制：

```python
KERNELONE_COGNITIVE_PERCEPTION = "perception_v1"
KERNELONE_COGNITIVE_REASONING = "reasoning_v1"
KERNELONE_COGNITIVE_EXECUTION = "execution_v1"
```

### 3.3 风险等级定义

| 等级 | 描述 | 执行策略 |
|------|------|----------|
| L0 | 只读查询, 内存操作 | 直接执行 |
| L1 | 创建新文件, 无系统影响 | Sandbox 内执行 |
| L2 | 修改现有文件, 需回滚计划 | 回滚快照 + 验证 |
| L3 | 删除操作, 跨系统变更 | 回滚准备 + 用户确认 |
| L4 | DB写入, 系统配置, 不可逆 | 生成脚本, 用户自行执行 |

### 3.4 Value Alignment 优先级

```
User Long-term Interest > System Integrity > Others > Future
```

---

## 4. 依赖关系图

```
Phase 0 ─┬─→ Phase 1 ─┬─→ Phase 2 ─┬─→ Phase 3 ─┬─→ Phase 4
         │             │             │             │
         └─────────────┴─────────────┴─────────────┘
                              │
                         Phase 5 (独立，可并行)
                              │
                         Phase 6 (依赖所有前序)
                              │
                         Phase 7 (依赖 0-5)
```

---

## 5. 度量指标

### 5.1 Cognitive Maturity Score (0-100)

| 等级 | 分数 | 特征 |
|------|------|------|
| Tool | 0-20 | 仅执行指令，无理解 |
| Aware | 21-40 | 意图理解，浅层推理 |
| Reflective | 41-60 | 反思能力，元认知 |
| Adaptive | 61-80 | 动态调整，持续演化 |
| Evolutionary | 81-100 | 主动进化，价值观驱动 |

### 5.2 三法则度量

| 法则 | 指标 |
|------|------|
| L1: Truthfulness | truthfulness_admission_rate, false_consistency_incidents |
| L2: Understanding | intent_inference_accuracy, assumption_verification_rate |
| L3: Evolution | mistake_diversity_index, recurrence_rate, evolution_velocity |

---

## 6. 验证门禁

### 6.1 CognitiveLifeFormGate

```bash
python docs/governance/ci/scripts/run_cognitive_life_form_gate.py \
    --mode all \
    --workspace <path>
```

**验证项**:
- Unit tests > 90% coverage
- Integration shadow mode
- A/B test 存活率
- Rollback 成功率

### 6.2 Verification Cards

每协议必须有对应 Verification Card：
- VC-Intent-001: Intent Graph 准确性
- VC-Critical-001: 六问法 覆盖度
- VC-Meta-001: 置信度校准误差
- VC-Cautious-001: L4 危险操作拦截率
- VC-Evolution-001: Belief 追踪完整度
- VC-Value-001: Stranger Test 通过率

---

## 7. 专家团队贡献

| Expert | 负责领域 | 关键输出 |
|--------|----------|----------|
| Expert 1 | Intent Graph & Perception | 6层感知架构, ContextOS Bridge |
| Expert 2 | Meta-Cognition & Self-Reflection | 3级Reflection输出, HOOK位置 |
| Expert 3 | Critical Thinking Engine | Six Questions, Devil's Advocate |
| Expert 4 | Cautious Execution | L0-L4风险分级, RollbackManager |
| Expert 5 | Value Alignment | 4D评估矩阵, 冲突解决优先级 |
| Expert 6 | Memory Evolution | Belief Tracking, BiasDefense |
| Expert 7 | Thinking-Acting Separation | ClarityLevel, Phase分离架构 |
| Expert 8 | Conversational Personality | 8种认知特质, InteractionPosture |
| Expert 9 | Integration Architecture | 7-Phase rollout, Feature Flag |
| Expert 10 | Core Principles Governance | 三法则度量, 成熟度评分 |

---

## 8. 风险与缓解

| 风险 | 影响 | 缓解策略 |
|------|------|----------|
| ContextOS 性能下降 | 高 | Bridge 懒加载, 增量更新 |
| Feature Flag 复杂度 | 中 | 统一配置管理, workspace 级别 |
| 回滚机制误用 | 高 | L4 强制用户执行, 不可绕过 |
| 过度工程化 | 中 | 复用现有组件, 不创造新轮子 |
| 向后兼容 | 高 | Phase 7 才清理旧逻辑 |

---

**下一步**: 待用户确认后开始 Phase 0 实施。
