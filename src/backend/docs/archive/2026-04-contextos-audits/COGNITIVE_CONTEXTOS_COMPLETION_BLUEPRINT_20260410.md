# Cognitive Life Form & ContextOS 全量补全蓝图

**版本**: v2.0
**日期**: 2026-04-10
**审计**: 10专家Agent深度审计 (2026-04-10)
**状态**: 待执行
**执行分支**: feature/enhanced-logger

---

## Executive Summary

经过两轮10人专家Agent审计 + 根因修复，当前认知系统骨架完整但存在**5类12个关键缺口**：

| 类别 | 数量 | 典型 |
|------|------|------|
| 死代码（未集成） | 5 | RollbackManager方法、MetaCognition reflect()、generate_cognitive_role_response() |
| Feature Flag关闭 | 3 | Evolution、ValueAlignment、LLM |
| Stub占位符 | 6+ | 元认知三层、ContextOS P0项 |
| 未使用canonical库 | 2 | ValueAlignment未用dangerous_patterns.py |
| 不一致的开关控制 | 1 | console_host.py无法禁用认知 |

---

## 系统架构现状

```
┌──────────────────────────────────────────────────────────────────┐
│                      Cognitive Pipeline                           │
│  Perception → Reasoning → MetaCognition → Decision → Acting → Evolution │
└──────────────────────────────────────────────────────────────────┘
         │              │              │              │           │
         ▼              ▼              ▼              ▼           ▼
    [REAL-OK]    [Partially-OK]  [Dead Code]  [Rollback-Stub] [Disabled]
```

---

## P0 Critical - 死代码修复（必须落地）

### P0-1: RollbackManager 集成到 ActingPhaseHandler

**根因**: `ActingPhaseHandler` 存储了 `RollbackManager` 实例但从未调用任何方法。`execute_action()` 只是把 `rollback_plan.steps` 当元数据传递。

**目标**: 让 `prepare_rollback()` → `execute_rollback()` → `abort_rollback()` 真正被调用。

**修改文件**: `polaris/kernelone/cognitive/execution/acting_handler.py`

**修改点**:
1. `_execute_with_rollback()` 在执行工具前调用 `prepare_rollback(target_paths)`
2. 执行完成后若成功则调用 `execute_rollback(plan)` 若失败则调用 `abort_rollback(plan)`
3. `execute_rollback()` 返回 PARTIAL 时记录警告但不阻断流程
4. `execute_rollback()` 返回 ABORTED 时必须中断并报告

**验证**: 新增测试 `test_rollback_manager_integrated` 验证 prepare→execute→cleanup 完整链路。

---

### P0-2: MetaCognitionEngine.reflect() 集成到 Orchestrator

**根因**: `reflect()` 和 `reflect_with_llm()` 存在但从未被调用。Orchestrator 只调用了 `audit_thought_process()`。

**目标**: 在 `CognitiveOrchestrator.process()` 的 Step 6 (Evolution) 之前调用 `reflect()`，将 `ReflectionOutput` 传给 `EvolutionEngine.evolve_from_reflection()`。

**修改文件**: `polaris/kernelone/cognitive/orchestrator.py`

**修改点**:
1. 在 `pipeline_result` 之后、`evolution` 之前，调用 `await self._meta.reflect(task_result, intent)`
2. 将 `ReflectionOutput` 作为 `evolve_from_reflection()` 的输入
3. 若 `reflect()` 返回的 `pattern_level` 和 `meta_level` 有实际内容则传给演化引擎

**验证**: 新增测试 `test_meta_cognition_reflect_integrated` 验证调用链路。

---

### P0-3: CognitiveGovernance 验证方法集成到 Orchestrator

**根因**: `CognitiveGovernance` 有完整的6个验证方法 (`verify_pre_perception` 等) 但 `orchestrator.process()` 从未调用它们。

**目标**: 在 pipeline 每个阶段前后插入 governance 验证调用。

**修改文件**: `polaris/kernelone/cognitive/orchestrator.py`

**修改点**:
1. 在 `__init__` 接受 `enable_governance: bool = True` 参数
2. Step 1 (Perception) 之后调用 `verify_post_perception()`
3. Step 2 (Reasoning) 之前调用 `verify_pre_reasoning()`，之后调用 `verify_post_reasoning()`
4. Step 5 (Execution) 之前调用 `verify_pre_execution()`
5. 若任何验证返回 `blocked=True` 则中断 pipeline

**验证**: 新增测试 `test_governance_verification_called` 验证每个阶段都调用了验证。

---

### P0-4: generate_cognitive_role_response() 替代为 direct 调用

**根因**: `generate_cognitive_role_response()` 从未被调用，role_dialogue.py 直接内联了相同逻辑。

**目标**: 消除死代码，要么让此函数被调用，要么删除它。

**决策**: 删除 `generate_cognitive_role_response()` 函数（因为 role_dialogue.py 已直接内联实现）。保留 `CognitiveMiddleware.inject_into_context()` 等公共方法。

**修改文件**: `polaris/kernelone/cognitive/middleware.py`

**修改点**:
1. 删除 `generate_cognitive_role_response()` 函数（第246-318行）
2. 从 `__all__` 中移除导出
3. 更新 `__init__.py` 移除导出

---

### P0-5: console_host.py 支持 enable_cognitive 参数

**根因**: `RoleConsoleHost._get_cognitive_middleware()` 不接受 `enable_cognitive` 参数，无法覆盖默认启用状态。

**目标**: 让 Director CLI 的 console host 也支持 `enable_cognitive=False` 禁用认知中间件。

**修改文件**: `polaris/delivery/cli/director/console_host.py`

**修改点**:
1. `_get_cognitive_middleware()` 增加 `enable_cognitive: bool | None` 参数
2. `stream_turn()` 增加 `enable_cognitive: bool | None` 参数并透传
3. 确保 `get_cognitive_middleware()` 的 `enabled` 参数被正确传递

**验证**: 测试 `test_console_host_cognitive_can_be_disabled`

---

## P1 High - Stub/弱实现补全

### P1-1: ValueAlignmentService._evaluate_others_impact() 实现

**根因**: 该方法硬编码返回 0.9 分（总是 APPROVED），是4个评估维度中唯一的纯stub。

**目标**: 实现真正的"对他人的影响"评估。

**修改文件**: `polaris/cells/values/alignment_service.py`

**实现方案**:
```python
def _evaluate_others_impact(self, action: str, context: dict[str, Any]) -> tuple[float, str]:
    """评估对其他用户/团队的影响。"""
    # 检查是否修改共享资源
    shared_keywords = ["shared", "team", "public", "config", "org", "workspace"]
    # 检查是否影响其他用户可见的输出
    impact_keywords = ["broadcast", "notify", "email", "slack", "webhook", "public"]

    score = 0.5  # 默认中等影响
    for kw in impact_keywords:
        if kw in action.lower():
            score = min(1.0, score + 0.2)

    for kw in shared_keywords:
        if kw in action.lower():
            score = min(1.0, score + 0.15)

    verdict = "APPROVED" if score < 0.7 else "REVIEW"
    return score, verdict
```

**验证**: 测试 `test_value_alignment_others_impact_not_always_approved`

---

### P1-2: ValueAlignmentService 集成 dangerous_patterns.py

**根因**: `dangerous_patterns.py` 有完整的危险命令检测正则，但 `ValueAlignmentService` 自己重新实现了简陋的关键词匹配。

**目标**: 让 ValueAlignmentService 调用 canonical 的 `is_dangerous_command()` 函数。

**修改文件**: `polaris/cells/values/alignment_service.py`

**修改点**:
1. `from polaris.kernelone.security.dangerous_patterns import is_dangerous_command as _is_dangerous`
2. 在 `_evaluate_system_integrity()` 中调用 `_is_dangerous(action)`
3. 若返回 True，直接返回 (1.0, "REJECTED") 不走关键词匹配

**验证**: 危险命令（如 `rm -rf /`）必须被 ValueAlignmentService 拦截。

---

### P1-3: CriticalThinkingEngine _extract_assumptions() 增强

**根因**: `_extract_assumptions()` 只用简单关键词 ("should", "because", "will") 做模式匹配，产生的是通用假设而非针对结论的具体假设。

**目标**: 至少能根据 intent_type 推断相关假设类型。

**修改文件**: `polaris/kernelone/cognitive/reasoning/engine.py`

**修改点**:
```python
def _extract_assumptions(self, conclusion: str, intent_chain: IntentChain | None) -> list[Assumption]:
    # 保留现有关键词逻辑
    assumptions = []
    # ... 现有逻辑 ...

    # 增加: 基于 intent_type 的领域假设
    if intent_chain and intent_chain.surface_intent:
        intent_type = intent_chain.surface_intent.intent_type
        if intent_type == "modify_file":
            assumptions.append(Assumption(
                text="修改操作可能引入语法错误或逻辑错误",
                confidence=0.7,
                strength=0.8,
                source="intent_type",
            ))
        elif intent_type == "create_file":
            assumptions.append(Assumption(
                text="新文件可能与现有架构规范不一致",
                confidence=0.5,
                strength=0.5,
                source="intent_type",
            ))
    return assumptions
```

---

### P1-4: Evolution Engine - BiasDefenseEngine 和 KnowledgePrecipitation 占位符

**根因**: 蓝图 Phase 5 规划了这两个组件但不存在。

**目标**: 创建骨架类供后续实现填充，避免死代码。

**新建文件**:
1. `polaris/kernelone/cognitive/evolution/bias_defense.py` - `BiasDefenseEngine` 类（`detect_bias()` 返回空列表）
2. `polaris/kernelone/cognitive/evolution/knowledge_precipitation.py` - `KnowledgePrecipitation` 类（`precipitate()` 返回空dict）

**验证**: EvolutionEngine 能实例化这两个组件而不报错。

---

## P2 Medium - ContextOS Runtime Completion

### P2-1: ContextOS _receipt_matches_case 真实实现

**根因**: 永远返回 True，benchmark 质量评分无意义。

**修改文件**: `polaris/kernelone/context/strategy_benchmark.py`

**实现**: 参见 CONTEXTOS_COGNITIVE_RUNTIME_COMPLETION_BLUEPRINT_20260407.md P0-2

---

### P2-2: ContextOS Tool Semantic Search 真实实现

**根因**: `search_tools()` 返回空列表 + TODO。

**修改文件**: `polaris/kernelone/single_agent/tools/registry.py`

**实现**: 使用 embedding-based similarity search（可复用现有的 embedding 服务）

---

### P2-3: ContextOS Auth Context 实现

**根因**: 7个方法抛 NotImplementedError。

**实现**: 至少实现 stub 版本（返回空/默认值），避免 NotImplementedError 在运行时炸裂。

---

### P2-4: ContextOS MetricsCollector 真实数据

**根因**: `receipt_write_failure_rate` 等硬编码为 0.0。

**实现**: 从实际监控数据源读取指标值（即使初期返回0也应该是真实查询而非硬编码）。

---

## P3 Low - Feature Flag 默认值修正

### P3-1: 评估是否应默认启用 LLM

**问题**: `use_llm=False` 导致默认使用简陋的规则引擎。

**决策**: 保持 `use_llm=False`（避免 LLM 不可用时崩溃），但完善 fallback 路径。当 LLM 可用时应该使用 LLM。

**修改点**: 在 `create_llm_adapter()` 中增加环境检测，若 LLM provider 可用则自动启用。

---

### P3-2: 评估是否应默认启用 ValueAlignment

**问题**: `enable_value_alignment=False` 导致安全评估被跳过。

**决策**: 保持 `False`（因为 ValueAlignmentService 的 `_evaluate_others_impact` 还是 stub），修复 P1-1 后再考虑默认启用。

---

## 测试策略

| 测试文件 | 覆盖目标 |
|----------|----------|
| `test_rollback_manager_integrated.py` | P0-1: prepare→execute→cleanup 全链路 |
| `test_meta_cognition_reflect_integrated.py` | P0-2: reflect() 被调用 |
| `test_governance_verification_integrated.py` | P0-3: 6个验证方法被调用 |
| `test_middleware_cleanup.py` | P0-4: 删除 generate_cognitive_role_response |
| `test_console_host_cognitive_toggle.py` | P0-5: console_host 支持 enable_cognitive |
| `test_value_alignment_others_impact.py` | P1-1: others_impact 非stub |
| `test_value_alignment_dangerous_patterns.py` | P1-2: dangerous_patterns 集成 |
| `test_intent_based_assumptions.py` | P1-3: _extract_assumptions 增强 |
| `test_evolution_components_exist.py` | P1-4: BiasDefenseEngine 等可实例化 |

---

## 执行计划

| Agent | 任务 | P级别 | 依赖 |
|-------|------|-------|------|
| Agent-1 | P0-1: RollbackManager → ActingPhaseHandler 集成 | P0 | none |
| Agent-2 | P0-2: MetaCognition reflect() → Orchestrator 集成 | P0 | none |
| Agent-3 | P0-3: CognitiveGovernance → Orchestrator 集成 | P0 | none |
| Agent-4 | P0-4: 删除 generate_cognitive_role_response() | P0 | none |
| Agent-5 | P0-5: console_host.py enable_cognitive 支持 | P0 | none |
| Agent-6 | P1-1+1-2: ValueAlignmentService 补全 | P1 | P0-3 前 |
| Agent-7 | P1-3: CriticalThinkingEngine 假设增强 | P1 | none |
| Agent-8 | P1-4: Evolution BiasDefenseEngine + KnowledgePrecipitation 骨架 | P1 | none |
| Agent-9 | P2-1~P2-4: ContextOS Runtime Completion | P2 | none |
| Agent-10 | 全部测试 + 验收 | All | Agent-1~9 |

---

## 验证命令

```bash
# 1. Ruff 检查
ruff check polaris/kernelone/cognitive/ polaris/kernelone/context/ --fix

# 2. Mypy 检查
mypy polaris/kernelone/cognitive/ polaris/kernelone/context/ --strict

# 3. 测试
pytest polaris/kernelone/cognitive/tests/ polaris/kernelone/context/ -v

# 4. 冒烟测试
python -m polaris.kernelone.cognitive.cli "Create a new API endpoint" --role director
```

---

## 风险与缓解

| 风险 | 缓解 |
|------|------|
| 引入新Bug | 每个Agent独立测试文件，严格TDD |
| 破坏现有Feature Flag | 保持向后兼容，新增参数有默认值 |
| LLM调用在无网络环境崩溃 | 完善fallback路径 |
| RollbackManager 实际执行破坏文件 | ETag验证保证state drift时自动ABORT |

---

*Document Version: 2.0*
*Blueprint: COGNITIVE_CONTEXTOS_COMPLETION_BLUEPRINT_20260410.md*
*Created by: 10 Expert Agent Audit Team*
