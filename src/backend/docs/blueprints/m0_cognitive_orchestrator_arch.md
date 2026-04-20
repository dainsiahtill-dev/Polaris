# M0 Cognitive Orchestrator Architecture

> 生成时间: 2026/04/13
> 分析基于: orchestrator.py, config.py, runtime_feature_flags.py

## 架构流程图

```
CognitiveOrchestrator.process(message)
         │
         ├──→ Governance.verify_pre_perception()
         ├──→ PerceptionLayer.process() → IntentGraph + UncertaintyAssessment
         ├──→ Governance.verify_post_perception()
         ├──→ Governance.verify_pre_reasoning()
         ├──→ CriticalThinkingEngine.analyze() / analyze_with_llm() → ReasoningChain
         ├──→ Governance.verify_post_reasoning()
         ├──→ MetaCognitionEngine.audit_thought_process() / reflect_with_llm()
         ├──→ Personality Posture Selection
         ├──→ ValueAlignment.evaluate() [if enabled]
         ├──→ Governance.verify_pre_execution()
         ├──→ CognitivePipeline.execute() → PipelineResult
         ├──→ Evolution.evolve_from_reflection() [if enabled]
         └──→ Response Building + Personality Apply → CognitiveResponse
```

## 各阶段输入/输出

| 阶段 | 输入 | 输出 | 开关依赖 |
|------|------|------|---------|
| Governance.pre_perception | message | gov_result | COGNITIVE_ENABLE_GOVERNANCE |
| Perception | message, session_id | IntentGraph, UncertaintyAssessment | PERCEPTION_ENABLED (未使用) |
| Governance.post_perception | intent_type, confidence | gov_result | COGNITIVE_ENABLE_GOVERNANCE |
| Governance.pre_reasoning | intent_type, confidence | gov_result | COGNITIVE_ENABLE_GOVERNANCE |
| Reasoning | intent_graph.chains[0] | ReasoningChain | COGNITIVE_USE_LLM |
| Governance.post_reasoning | probability, severity, blockers | gov_result | COGNITIVE_ENABLE_GOVERNANCE |
| MetaCognition | reasoning_chain | MetaCognitionSnapshot | COGNITIVE_USE_LLM |
| Posture Selection | intent_type, role_id, stakes | InteractionPosture | COGNITIVE_ENABLE_PERSONALITY |
| Value Alignment | action, user_intent | ValueAlignmentResult | COGNITIVE_ENABLE_VALUE_ALIGNMENT |
| Governance.pre_execution | execution_path, requires_confirmation | gov_result | COGNITIVE_ENABLE_GOVERNANCE |
| Pipeline Execute | intent_graph, uncertainty, reasoning_chain, meta_cognition | CognitivePipelineResult | 无直接开关 |
| Evolution | reflection_output | (记录学习) | COGNITIVE_ENABLE_EVOLUTION |
| Response Building | pipeline_result, posture, uncertainty | response_content | 无直接开关 |
| Personality Apply | response, posture, uncertainty_score | final_response | COGNITIVE_ENABLE_PERSONALITY |

## Fallback 路径现状

### 1. CognitiveRuntimeMode Fallback
```python
# 当 CognitiveRuntimeMode.OFF 时，返回: ok=False, error_code="cognitive_runtime_disabled"
```

### 2. Governance Block Fallback
- 返回 `CognitiveResponse` 并设置 `execution_path=ExecutionPath.BYPASS`, `blocked=True`

### 3. Value Alignment Block Fallback
- 返回 blocked turn 并记录到 session history
- `execution_path=ExecutionPath.BYPASS`

### 4. 推理引擎 Fallback
- `use_llm=False` → `analyze()` (非LLM模式)
- `use_llm=True` → `analyze_with_llm()`

### 5. Meta-Cognition Fallback
- `use_llm=False` → `audit_thought_process()`
- `use_llm=True` → `reflect_with_llm()`

### 6. Evolution Fallback
- `enable_evolution=False` → 跳过 Step 6 evolution 阶段

### 7. Personality Fallback
- `enable_personality=False` → 使用默认 `InteractionPosture.TRANSPARENT_REASONING`

### 8. Telemetry Fallback
- `enable_telemetry=False` → spans 为 no-op

## 关键发现

1. 所有 phase-gated 开关和大多数功能开关**默认均为关闭状态 (0/False)**
2. 唯一默认开启的是 `ENABLE_DEVILS_ADVOCATE=1` 和运行时模式 `SHADOW`
3. 治理开关 `COGNITIVE_ENABLE_GOVERNANCE` 是**独立配置**，不影响主流程
4. `COGNITIVE_USE_LLM` 仅影响 Reasoning 和 MetaCognition 阶段的实现方式
