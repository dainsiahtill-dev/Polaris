# 智能路由引擎 - 10人团队任务分配

**项目**: Intelligent Role Routing Engine
**总工期**: 8周 (Phase 1-6)
**团队规模**: 10人
**创建日期**: 2026-04-12

---

## 项目概览

```
polaris/kernelone/role/routing/
├── __init__.py
├── engine.py                   # [包1] RoleRoutingEngine 核心
├── context.py                  # [包2] RoutingContext + SemanticIntentInferer
├── result.py                   # [包3] RoutingResult 数据结构
├── compatibility.py             # [包4] CompatibilityEngine + ConflictResolver
├── scoring.py                  # [包5] ScoringEngine (动态权重)
├── cache.py                    # [包6] RoutingCache 多级缓存
├── preference.py               # [包7] PreferenceLearner
├── semantic/                   # v1.1 新增：语义推断模块
│   ├── __init__.py
│   ├── inferrer.py            # [包2-B] SemanticIntentInferer
│   └── embedding_cache.py     # [包2-C] Embedding 缓存
├── rules/
│   ├── __init__.py
│   ├── loader.py              # [包8] RoutingRuleLoader + schema.yaml
│   ├── matcher.py             # [包9] RuleMatcher
│   └── registry.py            # [包10] 规则注册表
├── adapters/
│   ├── __init__.py
│   └── legacy_recipe.py       # [包3] LegacyRecipeAdapter
└── config/
    ├── __init__.py
    ├── schema.yaml            # 路由配置 Schema
    └── default_rules.yaml     # 默认规则集
```

---

## 依赖关系图 (v1.1 - 优化后)

```
Phase 1 (Week 1): 基础设施
[包2:Context] ──┬──> [包2-B:Semantic] ──> [包3:Result]
                └──> [包2-C:EmbeddingCache]

Phase 2 (Week 2-3): 规则引擎 ← 提前
[包8:RuleLoader] ──> [包10:Registry]
      │
      └──> [包9:Matcher]

Phase 3 (Week 3-4): 核心编排
[包4:Compat+Conflict] ──┬──> [包1:Engine]
[包5:Scoring] ───────────┘

Phase 4 (Week 4-5): 评分优化 + 推断
[包5:Scoring.Dynamic] ──> [包2-B:Semantic.SlowPath]

Phase 5 (Week 5-6): 缓存与学习
[包6:Cache] ──> [包7:Preference]

Phase 6 (Week 6-8): 集成发布
[包1:Engine] ──> [PromptBuilder] ──> Benchmark
```

---

## 工作包详细分配

### [包1] Engine 核心引擎
**负责人**: 架构师A
**依赖**: 包2, 包3, 包4, 包5, 包6, 包7, 包8, 包9, 包10
**工期**: Week 3-4 (Phase 3) - 骨架 + Week 6-8 (Phase 6) - 集成
**文件**: `polaris/kernelone/role/routing/engine.py`

**职责**:
- 实现 `RoleRoutingEngine` 核心类
- 实现 `route()`, `route_with_fallback()` 方法
- 协调各组件完成路由决策
- 暴露 `route_mode` 参数 (AUTO/MANUAL/MIXED)
- 集成 `ConflictResolver` 处理 MIXED 模式
- 集成 `PreferenceLearner` 进行用户偏好学习
- **Phase 6**: 与 `PromptBuilder` 深度集成

**验收标准**:
- [ ] `RoleRoutingEngine` 正确协调各组件
- [ ] AUTO/MANUAL/MIXED 三种模式正常工作
- [ ] `ConflictResolver` 集成正确
- [ ] 单元测试覆盖所有公共方法
- [ ] 与 `PromptBuilder` 集成测试通过

```python
# 核心接口
class RoleRoutingEngine:
    def route(self, context: RoutingContext) -> RoutingResult: ...
    def route_with_fallback(self, context: RoutingContext, max_candidates: int = 3) -> list[RoutingResult]: ...
    def learn_preference(self, user_id: str, persona_id: str, feedback: float) -> None: ...
```

---

### [包2] Context 数据结构
**负责人**: 研发B
**依赖**: 无
**工期**: Week 1 (Phase 1)
**文件**: `polaris/kernelone/role/routing/context.py`

**职责**:
- 实现 `RoutingContext` 数据类 (含 session_phase, workspace_state)
- 实现 `UserPreference` 数据类
- 实现 `SemanticIntentInferer` 两段式推断引擎
  - Phase 1: 规则匹配 (Fast Path, confidence > 0.8)
  - Phase 2: 语义推断 (Slow Path, 当 Fast Path 不足时)
- 实现 `EmbeddingCache` 向量缓存

**验收标准**:
- [ ] `RoutingContext` 包含 session_phase, workspace_state
- [ ] `SemanticIntentInferer` Fast Path 工作正常
- [ ] Slow Path 骨架完成 (Embedding/LLM 集成待 Phase 4)
- [ ] 单元测试覆盖边界情况

```python
@dataclass
class RoutingContext:
    task_type: str
    domain: str
    intent: str
    constraints: dict[str, Any]
    user_preference: UserPreference
    session_id: str = ""

@dataclass
class UserPreference:
    verbose_level: str = "medium"  # low, medium, high
    communication_style: str = "direct"  # direct, formal, casual
    formality: str = "neutral"  # casual, neutral, formal
    persona_style_preference: str = ""

class IntentInference:
    def infer_from_message(self, message: str) -> IntentInferenceResult: ...
```

---

### [包3] Result 数据结构
**负责人**: 研发C
**依赖**: 无
**工期**: Week 1 (Phase 1)
**文件**: `polaris/kernelone/role/routing/result.py`

**职责**:
- 实现 `RoutingResult` 数据类
- 实现 `ScoringResult` 数据类
- 实现 `RoleTriple` 数据类
- 实现 `LegacyRecipeAdapter` 适配器

**验收标准**:
- [ ] `RoutingResult` 包含评分、匹配详情、回退次数
- [ ] `LegacyRecipeAdapter.to_routing_result()` 正确转换旧Recipe
- [ ] 与现有 `RecipeLoader` 集成测试通过
- [ ] 单元测试覆盖所有字段

```python
@dataclass
class RoutingResult:
    anchor: AnchorConfig
    profession: ProfessionConfig
    persona: PersonaConfig
    score: float
    match_details: dict[str, float]
    fallback_count: int = 0

@dataclass
class ScoringResult:
    total_score: float
    details: dict[str, float]

@dataclass
class RoleTriple:
    anchor_id: str
    profession_id: str
    persona_id: str

class LegacyRecipeAdapter:
    def to_routing_result(self, recipe_id: str) -> RoutingResult: ...
```

---

### [包4] CompatibilityEngine + ConflictResolver
**负责人**: 研发D
**依赖**: 包2
**工期**: Week 3-4 (Phase 3)
**文件**: `polaris/kernelone/role/routing/compatibility.py`

**职责**:
- 实现 `CompatibilityEngine` 兼容性检查
- 实现 `is_compatible()` 方法检查三元组
- 实现 `get_compatible_set()` 获取兼容列表
- 实现推断兼容性的启发式算法
- **v1.1 新增**: 实现 `ConflictResolver` MIXED 模式冲突解决器
  - 解决用户显式指定与系统推断的冲突
  - 核心原则：专业性 > 娱乐性 (Professional > Persona)
  - 提供 fallback persona 策略

**验收标准**:
- [ ] 显式声明的兼容性检查正确
- [ ] 推断兼容性逻辑正确
- [ ] `compatible_anchors` / `compatible_personas` 正确处理
- [ ] `ConflictResolver` 正确解决 MIXED 模式冲突
- [ ] 单元测试覆盖兼容性边界

```python
class CompatibilityEngine:
    def is_compatible(self, anchor, profession, persona, context) -> bool: ...
    def get_compatible_set(self, profession_id, context) -> tuple[list[str], list[str]]: ...

class ConflictResolver:
    """MIXED 模式冲突解决器 (v1.1 新增)"""
    def resolve(self, manual, inferred, context) -> ResolvedTriple: ...
    def _get_fallback_persona(self, original_id, context) -> str: ...
```

---

### [包5] ScoringEngine 评分引擎
**负责人**: 研发E
**依赖**: 包3, 包4
**工期**: Week 3-4 (Phase 3) - 骨架 + Week 4-5 动态权重调优
**文件**: `polaris/kernelone/role/routing/scoring.py`

**职责**:
- 实现 `ScoringEngine` 多维度评分
- 实现专业匹配度计算 (`_calc_expertise`)
- 实现 Persona 风格匹配 (`_calc_persona_style`)
- 实现工作流匹配 (`_calc_workflow_fit`)
- 实现历史偏好计算 (`_calc_historical_preference`)
- **v1.1 新增**: 实现 `_get_dynamic_weights()` 上下文感知权重
- **v1.1 新增**: 实现 `_calc_phase_match()` 会话阶段匹配

**验收标准**:
- [ ] 5维度评分计算正确 (expertise, persona, workflow, phase, usage)
- [ ] **动态权重**: security_review (expertise 0.60), code_explanation (persona 0.35)
- [ ] 权重可配置
- [ ] `UsageTracker` 正确追踪历史使用
- [ ] 评分结果一致性测试

```python
class ScoringEngine:
    # 关键任务类型：专业能力必须占主导
    CRITICAL_TASKS = {"security_review", "performance_critical", "architecture_design"}

    def score_candidate(self, candidate: RoleTriple, context: RoutingContext) -> ScoringResult: ...
    def _get_dynamic_weights(self, context: RoutingContext) -> dict[str, float]: ...
    def _calc_phase_match(self, candidate: RoleTriple, context: RoutingContext) -> float: ...
```

---

### [包6] RoutingCache 多级缓存
**负责人**: 研发F
**依赖**: 包1 (部分)
**工期**: Week 4-5 (Phase 4)
**文件**: `polaris/kernelone/role/routing/cache.py`

**职责**:
- 实现 L1/L2/L3 三级缓存
- 实现 `context_hash` 精确匹配 (L1, TTL: 5min)
- 实现 `task_type + domain` 部分匹配 (L2, TTL: 15min)
- 实现用户级 persona 偏好缓存 (L3, 长期有效)
- 实现缓存失效策略

**验收标准**:
- [ ] L1/L2/L3 缓存正确工作
- [ ] TTL 过期正确
- [ ] 缓存命中率统计正确
- [ ] 并发访问安全

```python
class RoutingCache:
    def get(self, context: RoutingContext) -> RoutingResult | None: ...
    def set(self, context: RoutingContext, result: RoutingResult) -> None: ...
    def invalidate(self, context_hash: str) -> None: ...
    def get_stats(self) -> CacheStats: ...

@dataclass
class CacheStats:
    l1_hits: int
    l2_hits: int
    l3_hits: int
    misses: int
    hit_rate: float
```

---

### [包7] PreferenceLearner 偏好学习
**负责人**: 研发G
**依赖**: 包6
**工期**: Week 4-5 (Phase 4)
**文件**: `polaris/kernelone/role/routing/preference.py`

**职责**:
- 实现 `PreferenceLearner` 用户偏好学习
- 实现 `record_feedback()` 反馈记录
- 实现 `get_preferred_personas()` 偏好查询
- 实现 `PreferenceStore` 持久化存储
- 实现基于反馈的 persona 排序调整

**验收标准**:
- [ ] 反馈收集正确
- [ ] 偏好计算合理 (基于 style_tags 和 matches_preference)
- [ ] `PreferenceStore` 持久化正确
- [ ] 冷启动时有合理的默认行为

```python
class PreferenceLearner:
    def record_feedback(self, session_id: str, persona_id: str, feedback: Feedback) -> None: ...
    def get_preferred_personas(self, user_id: str, context: RoutingContext) -> list[str]: ...
    def _calc_style_similarity(self, persona: PersonaConfig, style: str) -> float: ...

@dataclass
class Feedback:
    session_id: str
    persona_id: str
    score: float  # 1.0 = 完全满意, 0.0 = 不满意
    timestamp: datetime
```

---

### [包8] RoutingRuleLoader 规则加载器
**负责人**: 研发H
**依赖**: 无
**工期**: Week 2-3 (Phase 2) ← **提前**
**文件**: `polaris/kernelone/role/routing/rules/loader.py`
**配置**: `polaris/kernelone/role/routing/config/schema.yaml`

**职责**:
- 实现 `RoutingRuleLoader` 规则加载器
- 实现 YAML Schema 验证
- 实现 `rules.yaml` 格式解析
- 实现动态规则重载机制
- 创建 `schema.yaml` 配置文件
- 创建 `default_rules.yaml` 默认规则集

**验收标准**:
- [ ] YAML Schema 验证正确
- [ ] 规则加载正确
- [ ] 规则优先级排序正确
- [ ] 动态重载测试通过

```yaml
# polaris/kernelone/role/routing/config/rules.yaml
rules:
  - id: python_new_code
    name: Python新项目开发
    priority: 100
    match:
      task_type: new_crate
      domain: python
    recommendation:
      anchor: polaris_director
      profession: python_principal_architect
      persona: null
```

---

### [包9] RuleMatcher 规则匹配器
**负责人**: 研发I
**依赖**: 包8, 包10
**工期**: Week 2-3 (Phase 2) ← **提前**
**文件**: `polaris/kernelone/role/routing/rules/matcher.py`

**职责**:
- 实现 `RuleMatcher` 规则匹配
- 实现多维度匹配 (task_type, domain, intent, session_phase, user_preference)
- 实现匹配优先级 (精确匹配 > 前缀匹配 > 正则匹配 > 默认)
- 实现 `CandidateGenerator` 候选生成

**验收标准**:
- [ ] 匹配优先级正确
- [ ] 候选生成正确
- [ ] Top-N 候选选择正确
- [ ] 匹配性能达标 (<10ms)

```python
class RuleMatcher:
    def match(self, context: RoutingContext) -> list[MatchedRule]: ...
    def _exact_match(self, rule: RoutingRule, context: RoutingContext) -> bool: ...
    def _prefix_match(self, rule: RoutingRule, context: RoutingContext) -> bool: ...
    def _regex_match(self, rule: RoutingRule, context: RoutingContext) -> bool: ...

class CandidateGenerator:
    def generate(self, context: RoutingContext, rules: list[MatchedRule]) -> list[RoleTriple]: ...
```

---

### [包10] 规则注册表
**负责人**: 研发J
**依赖**: 包8
**工期**: Week 2-3 (Phase 2) ← **提前**
**文件**: `polaris/kernelone/role/routing/rules/registry.py`

**职责**:
- 实现 `RuleRegistry` 全局规则注册表
- 实现 `register_rule()`, `unregister_rule()` 方法
- 实现规则变更事件通知
- 实现规则内置默认集

**验收标准**:
- [ ] 规则注册/注销正确
- [ ] 事件通知正确
- [ ] 默认规则集完整
- [ ] 线程安全

```python
class RuleRegistry:
    def __init__(self) -> None:
        self._rules: dict[str, RoutingRule] = {}
        self._listeners: list[RuleChangeListener] = []

    def register_rule(self, rule: RoutingRule) -> None: ...
    def unregister_rule(self, rule_id: str) -> None: ...
    def get_rules(self) -> list[RoutingRule]: ...
    def add_listener(self, listener: RuleChangeListener) -> None: ...
```

---

## 实施计划 (8周 - v1.1 优化版)

> **调整说明**: 规则引擎 (原 Phase 5) 提前到 Phase 2
> **理由**: 保证核心引擎从第一天起就是数据驱动的，而非写死硬编码

### Week 1: 基础设施 (Phase 1)
| 包 | 任务 | 负责人 |
|----|------|--------|
| 包2 | Context 数据结构 (含 session_phase, workspace_state) | 研发B |
| 包2-B | SemanticIntentInferer 骨架 (Fast Path) | 研发B |
| 包2-C | Embedding 缓存 | 研发B |
| 包3 | Result 数据结构 + LegacyRecipeAdapter | 研发C |

**里程碑**: 基础数据结构和 Fast Path 推断完成

### Week 2-3: 规则引擎 (Phase 2) ← **提前**
| 包 | 任务 | 负责人 |
|----|------|--------|
| 包8 | RoutingRuleLoader + schema.yaml | 研发H |
| 包10 | RuleRegistry 规则注册表 | 研发J |
| 包9 | RuleMatcher 规则匹配器 | 研发I |

**里程碑**: YAML 规则配置体系完成，核心引擎可数据驱动

### Week 3-4: 核心编排 (Phase 3)
| 包 | 任务 | 负责人 |
|----|------|--------|
| 包4 | CompatibilityEngine | 研发D |
| 包4-Ext | ConflictResolver MIXED模式冲突解决 | 研发D |
| 包5 | ScoringEngine 动态权重评分 | 研发E |
| 包1 | RoleRoutingEngine 核心引擎骨架 | 架构师A |

**里程碑**: 核心编排逻辑完成

### Week 4-5: 评分优化 + 推断 (Phase 4)
| 包 | 任务 | 负责人 |
|----|------|--------|
| 包5-Ext | ScoringEngine 动态权重调优 | 研发E |
| 包2-B-Ext | SemanticIntentInferer Slow Path (Embedding/LLM) | 研发B |
| 包6 | RoutingCache L1/L2/L3 缓存 | 研发F |
| 包7 | PreferenceLearner | 研发G |

**里程碑**: 动态权重和 Slow Path 推断完成

### Week 5-6: 缓存与学习 (Phase 5)
| 包 | 任务 | 负责人 |
|----|------|--------|
| 包6-Ext | 缓存失效策略 + 统计埋点 | 研发F |
| 包7-Ext | PreferenceLearner 持久化 | 研发G |
| ALL | 集成测试 | 全体 |

**里程碑**: 缓存和偏好学习完成

### Week 6-8: 集成与发布 (Phase 6)
| 包 | 任务 | 负责人 |
|----|------|--------|
| 包1 | 与 PromptBuilder 集成 | 架构师A |
| ALL | 端到端 Benchmark 测试 | 全体 |
| ALL | 性能优化 (<50ms/route) | 全体 |
| ALL | 文档完善 | 全体 |

**里程碑**: 所有测试通过，Benchmark 达标，文档完整

---

## 技术规范

### 1. 类型提示
所有函数必须有完整的类型提示：
```python
def route(self, context: RoutingContext) -> RoutingResult: ...
```

### 2. 文档字符串
所有公共类和方法必须有 Docstring：
```python
class RoleRoutingEngine:
    """智能角色路由引擎

    根据上下文智能推断最优的 Anchor + Profession + Persona 组合。
    支持 AUTO/MANUAL/MIXED 三种路由模式。
    """
```

### 3. 异常处理
```python
class RoutingError(Exception): ...
class CompatibilityError(RoutingError): ...
class ScoringError(RoutingError): ...
```

### 4. 日志规范
```python
logger = logging.getLogger(__name__)
logger.info(f"Routing decision: {result.anchor.id} + {result.profession.id}")
logger.debug(f"Candidates: {len(candidates)}")
```

### 5. 测试要求
```python
# 每个包必须有对应的测试文件
# test_engine.py
# test_context.py
# test_result.py
# ...

def test_routing_context_creation():
    context = RoutingContext(
        task_type="new_code",
        domain="python",
        intent="implement",
        constraints={},
        user_preference=UserPreference(),
    )
    assert context.task_type == "new_code"
```

---

## 质量门禁

| 检查项 | 工具 | 通过标准 |
|--------|------|----------|
| 代码格式 | ruff | 0 errors |
| 类型检查 | mypy | Success: no issues found |
| 单元测试 | pytest | 100% passed |
| 集成测试 | pytest | 100% passed |
| 性能基准 | custom | <50ms per route |

---

## 风险与缓解 (v1.1)

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| Phase 2 规则引擎太早 | 核心引擎仍可能写死硬编码 | 规则引擎输出必须经过 Engine 验证 |
| 循环依赖 | 构建失败 | 遵循依赖图顺序 |
| 评分算法调优 | 用户体验 | Phase 4 预留两周专门调优 |
| 缓存一致性 | 数据陈旧 | 明确的 TTL 策略 |
| MIXED 模式冲突解决 | 用户困惑 | 记录 warnings 并通知用户 |
| Slow Path (Embedding/LLM) 延迟 | 路由响应慢 | Fast Path 兜底，Slow Path 异步 |

---

## 通信与协作

1. **每日站会**: 每天 10:00 (异步)
2. **周报**: 每周五 18:00
3. **代码审查**: PR 必须经过至少1人 review
4. **接口变更**: 需提前在团队同步
