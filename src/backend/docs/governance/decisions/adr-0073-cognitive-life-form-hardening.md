# ADR-0073: 认知生命体加固决策

**日期**: 2026-04-15
**状态**: Implemented (P0/P1/P2 completed, P3 deferred)
**影响范围**: `polaris/kernelone/cognitive/`, `polaris/cells/resident/autonomy/`, `polaris/cells/roles/kernel/internal/turn_engine/`

## 背景

2026-04-15 四人专家团队审计发现认知生命体系统存在以下核心问题：

1. **Orchestrator 代码质量缺陷**: 生产代码引入 `MagicMock`，5 处重复 block 逻辑，大量 `hasattr()` 防御
2. **Governance 门控空壳**: 5 个门控点位置正确但校验逻辑薄弱，缺少跨门控状态累积
3. **认知管道与主路径脱节**: `CognitiveOrchestrator` 和 `TurnEngine` 完全独立，认知管道默认关闭
4. **双重实现**: `MetaCognitionEngine` 在认知层和策略层各有一套
5. **进化系统缺少遗忘机制**: 信念无限膨胀，进化记录无完整性校验

## 决策

### D1: Orchestrator 瘦身
- 提取 `_build_blocked_response()` 消除 5 处重复
- 移除 `MagicMock` 依赖，改用 Null Object + try/except
- 消除 `hasattr()` 检查，用 Protocol 强制类型契约

### D2: Governance 门控增强
- 每个 `verify_*` 方法至少包含 3 条实质性校验规则
- 新增 `GovernanceState` 跨门控累积追踪（3次 WARN → FAIL）
- 新增 `verify_reasoning_consistency()` 一致性校验

### D3: 认知管道集成 TurnEngine
- 定义 `CognitivePipelinePort` Protocol
- TurnEngine 通过构造函数注入认知管道（可选，默认 None）
- 两个注入点: LLM 调用前预检 + 工具执行后评估
- **关键约束**: 认知管道关闭时 TurnEngine 行为零影响

### D4: MetaCognitionEngine 职责分离
- 策略层重命名为 `StrategyInsightEngine`
- 认知层保持 `MetaCognitionEngine`
- 策略层可消费认知层的 `MetaCognitionSnapshot`

### D5: 信念衰退 + HMAC 完整性
- 指数衰减（half_life=30天）+ 引用强化
- `EvolutionStore` 写入时自动 HMAC-SHA256 签名

## 影响评估

| 决策 | 向后兼容 | 破坏性变更 |
|------|---------|-----------|
| D1 | ✅ 内部重构，无 API 变更 | 无 |
| D2 | ✅ 门控接口不变，行为收紧 | WARN→FAIL 的边界变化 |
| D3 | ✅ 注入可选，默认关闭 | 无 |
| D4 | ⚠️ 公共导出名变更 | `autonomy.MetaCognitionEngine` → `StrategyInsightEngine` |
| D5 | ✅ 新增能力，无破坏性变更 | 无 |

## 相关文件

- 蓝图: `docs/blueprints/COGNITIVE_LIFE_FORM_HARDENING_BLUEPRINT_20260415.md`
- 团队计划: `docs/blueprints/COGNITIVE_LIFE_FORM_HARDENING_TEAM_PLAN_20260415.md`
- 验证卡片: `docs/governance/templates/verification-cards/vc-20260415-cognitive-life-form-hardening.yaml`
