# 认知生命体加固 — 6人专家团队执行计划

**版本**: v1.0
**日期**: 2026-04-15
**蓝图**: `COGNITIVE_LIFE_FORM_HARDENING_BLUEPRINT_20260415.md`
**总工期**: 3 周 (15 工作日)

---

## 1. 团队组成

| 编号 | 角色 | 工作包 | Week 1 | Week 2 | Week 3 |
|------|------|--------|--------|--------|--------|
| **E1** | Python 高级工程师 (重构专家) | WP-1: Orchestrator 瘦身 | WP-1 实施 | Review WP-2/WP-3 集成 | 回归测试 |
| **E2** | Python 高级工程师 (安全专家) | WP-2: Governance 增强 | WP-2 设计 + 测试先行 | WP-2 实施 + WP-3 协助 | 回归测试 |
| **E3** | Python 高级工程师 (集成专家) | WP-3: TurnEngine 集成 | WP-3 设计 + Port 定义 | WP-3 实施 | 集成测试 |
| **E4** | Python 高级工程师 (认知系统) | WP-4: MetaCognition 合并 | WP-4 实施 | Review + 文档 | 回归测试 |
| **E5** | Python 高级工程师 (数据工程) | WP-5: 信念衰退 + HMAC | WP-5 实施 | Review + 性能基准 | 回归测试 |
| **E6** | Python 高级工程师 (测试专家) | WP-6: 集成测试 + 修复 | 测试框架搭建 + 6.2/6.3 | WP-6 集成测试 | 全量回归 |

---

## 2. 详细工作包分派

### E1: Orchestrator 瘦身与 MagicMock 清除 [WP-1]

**Week 1 (Day 1-5)**

Day 1-2: 重构 orchestrator.py
- 提取 `_build_blocked_response()` 方法，消除 5 处重复
- 移除 `from unittest.mock import MagicMock`
- 替换所有 `isinstance(..., MagicMock)` 为 try/except
- 替换 `hasattr()` 检查为 Protocol + Null Object

Day 3: 创建 Null Object 类型
- 在 `types.py` 中新增 `NullReasoningChain`
- 在 `contracts.py` 中确保所有 Protocol 字段有默认值

Day 4-5: 测试 + 验证
- 确保所有现有测试通过
- `ruff check . --fix && ruff format .`
- `mypy orchestrator.py`

**产出文件**:
- `polaris/kernelone/cognitive/orchestrator.py` — 修改
- `polaris/kernelone/cognitive/types.py` — 新增 Null Object

---

### E2: Governance 门控实质性校验 [WP-2]

**Week 1 (Day 1-2)**: 设计 + 测试先行
- 编写 `GovernanceState` 数据类
- 编写所有新增校验规则的测试用例（先测试后实现）

**Week 2 (Day 1-5)**: 实施
Day 1-2: 增强 `verification.py`
- 新增 `verify_reasoning_consistency()` 方法
- 增强 `verify_post_reasoning()` — 概率校准、推理完整性、blocker 冲突
- 增强 `verify_post_perception()` — 意图合法性、置信度一致性

Day 3: 创建 `state_tracker.py`
- `GovernanceState` 累积追踪器
- 3次 WARN 自动升级为 FAIL
- 连续 unknown intent 降级检测

Day 4-5: 集成到 orchestrator + 测试
- 更新 orchestrator 使用 `GovernanceState`
- 新增 `verify_reasoning_consistency` 调用点
- 全部测试通过

**产出文件**:
- `polaris/kernelone/cognitive/governance/verification.py` — 增强
- `polaris/kernelone/cognitive/governance/state_tracker.py` — 新文件
- `polaris/kernelone/cognitive/governance/__init__.py` — 更新导出

---

### E3: 认知管道与 TurnEngine 集成 [WP-3]

**Week 1 (Day 1-3)**: 设计 Port + 测试先行
- 在 `contracts.py` 定义 `CognitivePipelinePort` Protocol
- 定义 `CognitivePreCheckResult` 和 `CognitiveAssessResult`
- 编写 Port 集成的单元测试（mock orchestrator）

**Week 2 (Day 1-5)**: 实施
Day 1-2: 创建 `pipeline_adapter.py`
- `CognitivePipelineAdapter` 实现 `CognitivePipelinePort`
- `pre_turn_cognitive_check()` — 调用感知+推理但不执行
- `post_tool_cognitive_assess()` — 调用进化阶段

Day 3-4: 修改 TurnEngine
- 构造函数新增 `cognitive_pipeline: CognitivePipelinePort | None = None`
- 在 `_run_single_turn()` 中注入两个调用点
- 确保 `cognitive_pipeline=None` 时行为完全不变

Day 5: 集成测试
- 验证: Pipeline open + Governance FAIL → TurnEngine 停止
- 验证: Pipeline closed → TurnEngine 行为不变
- 验证: Pipeline open + 正常流程 → 无影响

**产出文件**:
- `polaris/kernelone/cognitive/contracts.py` — 新增 Protocol
- `polaris/kernelone/cognitive/pipeline_adapter.py` — 新文件
- `polaris/cells/roles/kernel/internal/turn_engine/engine.py` — 集成

---

### E4: 双重 MetaCognitionEngine 合并 [WP-4]

**Week 1 (Day 1-5)**

Day 1: 重命名策略层引擎
- `cells/resident/autonomy/internal/meta_cognition.py`
  - `MetaCognitionEngine` → `StrategyInsightEngine`
  - `MetaInsight` 保持不变（名称合适）
- 更新所有内部 import

Day 2: 更新外部引用
- `cells/resident/autonomy/public/service.py` — 更新导出名
- `cells/resident/autonomy/internal/resident_runtime_service.py` — 更新引用
- 全局 grep `from.*autonomy.*import.*MetaCognitionEngine` 更新

Day 3: 建立调用关系
- `StrategyInsightEngine.analyze_decisions()` 新增可选参数 `cognitive_snapshot`
- 当 `cognitive_snapshot` 提供时，参考认知层的实时监控结果增强策略分析

Day 4-5: 测试 + 验证
- 更新所有测试中的类名引用
- `ruff check . --fix && ruff format .`
- `mypy` 相关文件
- 全量 pytest

**产出文件**:
- `polaris/cells/resident/autonomy/internal/meta_cognition.py` — 重命名
- `polaris/cells/resident/autonomy/public/service.py` — 更新导出
- 所有 import 该类的文件 — 更新

---

### E5: 信念衰退与进化记录 HMAC [WP-5]

**Week 1 (Day 1-5)**

Day 1-2: 信念衰退引擎
- 创建 `evolution/belief_decay.py`
- 实现 `DecayPolicy` 数据类
- 实现 `BeliefDecayEngine`
  - `apply_decay()` — 指数衰减 + 引用强化
  - `prune_stale_beliefs()` — 清理过期信念
- 单元测试：验证衰减曲线、强化效果、pruning 逻辑

Day 3-4: 进化记录 HMAC
- 创建 `evolution/integrity.py`
- 实现 `EvolutionIntegrityGuard`
  - `sign_record()` — HMAC-SHA256
  - `verify_chain()` — 链式验证
  - `detect_tampering()` — 篡改检测
- 修改 `EvolutionStore` 集成签名

Day 5: 集成到 EvolutionEngine
- `EvolutionEngine` 在每次 `process_trigger()` 后应用信念衰退
- 添加 `evolve_and_decay()` 便捷方法
- 全部测试通过

**产出文件**:
- `polaris/kernelone/cognitive/evolution/belief_decay.py` — 新文件
- `polaris/kernelone/cognitive/evolution/integrity.py` — 新文件
- `polaris/kernelone/cognitive/evolution/engine.py` — 集成
- `polaris/kernelone/cognitive/evolution/store.py` — 集成 HMAC
- `polaris/kernelone/cognitive/evolution/__init__.py` — 导出

---

### E6: 集成测试 + CognitiveMaturityScore + 动态阈值 [WP-6]

**Week 1 (Day 1-5)**: 基础修复 + 测试框架

Day 1: CognitiveMaturityScore 修复
- `default()` 返回 0 分
- 新增 `is_calibrated` 属性
- 单元测试

Day 2: UncertaintyQuantifier 动态校准
- 新增 `_history` 列表和 `_calibration_window`
- 实现 `record_outcome()` 和 `_apply_calibration()`
- 单元测试

Day 3-5: 集成测试框架搭建
- 创建 `test_integration.py`
- 搭建 fixture（mock LLM、mock tool executor、test workspace）
- 编写前 3 个基础测试:
  - `test_normal_execution_path`
  - `test_governance_blocks_empty_message`
  - `test_cognitive_disabled_fallback`

**Week 2 (Day 1-5)**: 扩展集成测试

Day 1-2: Governance 门控测试
- `test_governance_blocks_critical_with_low_probability`
- `test_value_alignment_blocks_unsafe`
- `test_governance_accumulation_escalation` (依赖 WP-2)

Day 3-4: HITL 测试
- `test_hitl_rejection`
- `test_hitl_timeout_falls_to_shadow`

Day 5: 进化与人格测试
- `test_evolution_records_learning`
- `test_personality_influences_response`

**Week 3**: 全量回归
- 运行 `pytest polaris/ -v`
- 修复所有回归
- `ruff check . --fix && ruff format .`
- `mypy` 相关文件

**产出文件**:
- `polaris/kernelone/cognitive/tests/test_integration.py` — 新文件
- `polaris/kernelone/cognitive/governance/maturity_score.py` — 修改
- `polaris/kernelone/cognitive/perception/uncertainty.py` — 增强

---

## 3. 里程碑与交付物

| 里程碑 | 日期 | 交付物 | 验收标准 |
|--------|------|--------|---------|
| M1: Week 1 结束 | Day 5 | WP-1, WP-4, WP-5 代码完成 | ruff + mypy + 单元测试通过 |
| M2: Week 2 结束 | Day 10 | WP-2, WP-3 代码完成 | ruff + mypy + 单元测试通过 |
| M3: Week 3 结束 | Day 15 | WP-6 集成测试 + 全量回归 | pytest 100% 绿色 |

---

## 4. 协作规范

1. **每个 WP 完成后**: `ruff check . --fix && ruff format .` + `mypy <files>` + 相关 `pytest`
2. **每日合并前**: 确保不影响其他 WP 的文件（除共享接口外）
3. **共享接口变更**: 需提前通知依赖方 (WP-1 → WP-2 → WP-3 链)
4. **测试先行**: WP-2 和 WP-3 要求先写测试再实现
5. **Commit 消息**: `feat(cognitive): <简述>` 或 `fix(cognitive): <简述>`
