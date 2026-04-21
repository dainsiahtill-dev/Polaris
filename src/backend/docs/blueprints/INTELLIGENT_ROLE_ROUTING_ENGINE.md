# Intelligent Role Routing Engine (智能角色路由引擎)

**版本**: v1.0
**状态**: 设计中
**创建日期**: 2026-04-12
**负责人**: Chief Engineer

---

## 1. 核心理念：双模式智能路由

**三种路由模式：**

| 模式 | 说明 | 使用场景 |
|------|------|----------|
| `AUTO` | 系统根据意图/上下文智能推断最优组合 | 通用对话、用户未明确指定 |
| `MANUAL` | 用户固定 (anchor_id, profession_id, persona_id) | 专业用户、明确需求 |
| `MIXED` | 部分固定，部分推断 | 偏好某项但其他灵活的 |

**路由决策流程：**

```
用户请求
    │
    ├─→ mode == "manual"?
    │    ├─ YES → 直接返回 (anchor_id, profession_id, persona_id)
    │    └─ NO
    │        │
    │        ├─→ 意图推断 → 领域识别 → Profession选择
    │        ├─→ 兼容性过滤
    │        ├─→ 评分排序
    │        └─→ 返回最优组合
```

---

## 2. 意图推断引擎

### 2.1 推断规则表

```python
# 意图 → 任务类型
INTENT_TO_TASK = {
    "implement": "new_code",
    "create": "new_crate",
    "fix": "bug_fix",
    "improve": "system_improvement",
    "optimize": "performance_critical",
    "refactor": "refactor",
    "review": "code_review",
    "audit": "security_review",
    "design": "architecture_design",
    "deploy": "deployment",
}

# 任务类型 → 推荐Profession (按优先级)
TASK_TO_PROFESSION = {
    "new_crate": ["python_principal_architect", "software_engineer"],
    "bug_fix": ["quality_engineer", "software_engineer"],
    "security_review": ["security_auditor"],
    "performance_critical": ["python_principal_architect"],
    "architecture_design": ["python_principal_architect"],
    "deployment": ["devops_engineer"],
    "ml_work": ["ml_engineer"],
    "data_pipeline": ["data_engineer"],
}

# 领域 → 默认Profession
DOMAIN_TO_PROFESSION = {
    "python": "python_principal_architect",
    "typescript": "typescript_frontend_architect",
    "rust": "rust_systems_engineer",
    "devops": "devops_engineer",
    "data": "data_engineer",
    "ml": "ml_engineer",
    "security": "security_auditor",
}
```

### 2.2 两段式混合路由 (Hybrid Semantic Router)

**设计原则**: Fast Path (规则) 优先，Slow Path (语义) 兜底

```python
class SemanticIntentInferer:
    """两段式意图推断引擎

    Phase 1: 极速规则匹配 (Fast Path) - 毫秒级响应
    Phase 2: 语义向量/LLM 分类器 (Slow Path) - 当 Fast Path 置信度 < 0.8 时触发
    """

    def infer(self, message: str) -> IntentInferenceResult:
        # Phase 1: 极速规则匹配
        rule_result = self._fast_path_match(message)
        if rule_result.confidence > 0.8:
            return rule_result

        # Phase 2: 语义推断 (置信度不足时)
        return self._semantic_infer(message)

    def _fast_path_match(self, message: str) -> IntentInferenceResult:
        """关键词/正则快速匹配"""
        message_lower = message.lower()

        # 意图关键词
        intent_keywords = {
            "implement": ["实现", "写代码", "implement", "write code"],
            "fix": ["修复", "fix", "bug"],
            "design": ["设计", "架构", "design", "architecture"],
            "review": ["审查", "review", "检查"],
            "deploy": ["部署", "deploy", "发布"],
        }

        # 领域关键词
        domain_keywords = {
            "python": ["python", "django", "flask", "fastapi"],
            "typescript": ["typescript", "react", "vue", "前端"],
            "rust": ["rust", "cargo"],
            "devops": ["docker", "k8s", "kubernetes", "ci/cd"],
            "security": ["安全", "security", "漏洞"],
        }

        intent = self._match_keywords(message_lower, intent_keywords)
        domain = self._match_keywords(message_lower, domain_keywords)

        # 计算置信度：完全匹配=1.0，部分匹配=0.6，无匹配=0.0
        confidence = 1.0 if intent and domain else (0.6 if intent or domain else 0.0)

        return IntentInferenceResult(
            intent=intent or "analyze",
            domain=domain or "general",
            task_type=self._intent_to_task(intent),
            confidence=confidence,
            method="rule_based",
        )

    def _semantic_infer(self, message: str) -> IntentInferenceResult:
        """语义推断 (Slow Path)

        使用 Embedding 相似度 或 LLM 结构化输出 (JSON Mode)
        """
        # TODO: 集成 embedding similarity 或 LLM classifier
        # 示例：用户说"帮我看看这段代码有没有内存泄漏的风险"
        # 无关键词，但 LLM 应识别为 security_review + memory_analysis
        return IntentInferenceResult(
            intent="analyze",
            domain="security",
            task_type="security_review",
            confidence=0.7,
            method="semantic_llm",
        )

    def _match_keywords(self, text: str, keywords: dict[str, list[str]]) -> str | None:
        """前缀匹配关键词"""
        for key, words in keywords.items():
            for word in words:
                if word in text:
                    return key
        return None

    def _intent_to_task(self, intent: str | None) -> str:
        """意图 → 任务类型映射"""
        mapping = {
            "implement": "new_code",
            "create": "new_crate",
            "fix": "bug_fix",
            "design": "architecture_design",
            "review": "code_review",
            "audit": "security_review",
            "deploy": "deployment",
        }
        return mapping.get(intent or "", "default")


@dataclass
class IntentInferenceResult:
    """意图推断结果"""
    intent: str
    domain: str
    task_type: str
    confidence: float  # 0.0 - 1.0
    method: str  # "rule_based" | "semantic_llm"
```

---

## 3. 路由决策流程

```
用户请求
    │
    ├─→ 1. ContextCollector 收集上下文
    │       - task_type, domain, intent, constraints
    │
    ├─→ 2. RuleMatcher 匹配规则
    │       - 精确匹配 > 前缀匹配 > 正则匹配 > 默认
    │
    ├─→ 3. CandidateGenerator 生成候选
    │       - 根据匹配规则选出Top-N候选
    │
    ├─→ 4. CompatibilityFilter 过滤
    │       - 检查 compatible_anchors/professions/personas
    │       - 移除不兼容组合
    │
    ├─→ 5. ScoringEngine 评分排序
    │       - 多维度评分: expertise_match, persona_style, ...
    │
    ├─→ 6. FallbackChain 执行降级
    │       - 主选失败 → 次选 → ... → 默认
    │
    └─→ 7. ResultCache 缓存结果
            - 下次相同上下文直接返回
```

### 3.1 架构组件图 (v1.1)

```
┌─────────────────────────────────────────────────────────────────┐
│                     RoleRoutingEngine                            │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │          Context & State Collector                      │  │
│  │  - task_type, domain, intent (via SemanticRouter)       │  │
│  │  - session_phase (ideation/execution/verification)      │  │ ← 新增
│  │  - constraints, user_preference                         │  │
│  │  - workspace_state (当前项目状态)                        │  │ ← 新增
│  └──────────────────────────────────────────────────────────┘  │
│                            ↓                                     │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │              SemanticIntentInferer (Hybrid)              │  │ ← 新增
│  │  Phase 1: 规则匹配 (confidence > 0.8 → 直接返回)         │  │
│  │  Phase 2: 语义推断 (Embedding/LLM, confidence ≤ 0.8)     │  │
│  └──────────────────────────────────────────────────────────┘  │
│                            ↓                                     │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │                   RuleMatcher                             │  │
│  │  ┌────────────┐ ┌────────────┐ ┌────────────────────┐   │  │
│  │  │ task_rules │ │domain_rules│ │ phase_rules        │   │  │
│  │  │ (权重:0.4) │ │(权重:0.3)  │ │ (session感知)     │   │  │
│  │  └────────────┘ └────────────┘ └────────────────────┘   │  │
│  └──────────────────────────────────────────────────────────┘  │
│                            ↓                                     │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │              ConflictResolver (MIXED模式)                  │  │ ← 新增
│  │  显式声明优先 + 松弛化 (Relaxation) 策略                  │  │
│  └──────────────────────────────────────────────────────────┘  │
│                            ↓                                     │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │                 CompositionEngine                         │  │
│  │  1. 候选集生成 (Candidate Generation)                     │  │
│  │  2. 兼容性过滤 (Compatibility Filtering)                   │  │
│  │  3. 动态权重评分 (Contextual Dynamic Weighting)           │  │ ← 更新
│  │  4. Fallback链 (Fallback Chain)                          │  │
│  └──────────────────────────────────────────────────────────┘  │
│                            ↓                                     │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │                   CacheManager                            │  │
│  │  L1: context_hash (TTL:5min)                             │  │
│  │  L2: task_type+domain (TTL:15min)                        │  │
│  │  L3: user_preference (长期)                              │  │
│  └──────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

### 3.2 评分算法 (v1.1 - 动态权重)

```python
class ScoringEngine:
    """多维度评分引擎 - 支持上下文感知的动态权重"""

    # 关键任务类型：专业能力必须占主导
    CRITICAL_TASKS = {"security_review", "performance_critical", "architecture_design"}

    # 风格优先任务：Persona 匹配更重要
    STYLE_PRIORITY_TASKS = {"code_explanation", "casual_chat", "tutorial"}

    def score(self, candidate: RoleTriple, context: RoutingContext) -> float:
        # Step 1: 获取上下文感知的动态权重
        weights = self._get_dynamic_weights(context)

        # Step 2: 计算各维度得分
        scores = {
            "expertise_match": self._calc_expertise_match(candidate, context),
            "persona_style_match": self._calc_persona_match(candidate, context),
            "workflow_match": self._calc_workflow_match(candidate, context),
            "phase_match": self._calc_phase_match(candidate, context),  # v1.1 新增
            "usage_score": self._get_usage_score(candidate),
        }

        return sum(scores[k] * weights[k] for k in weights)

    def _get_dynamic_weights(self, context: RoutingContext) -> dict[str, float]:
        """上下文感知的动态权重计算

        不同的任务类型对维度的容忍度不同：
        - security_review: 专业能力必须占绝对主导 (0.60)
        - code_explanation: Persona 风格匹配可以更高 (0.35)
        """
        # 基础权重
        weights = {
            "expertise_match": 0.35,
            "persona_style_match": 0.25,
            "workflow_match": 0.20,
            "phase_match": 0.10,  # v1.1: 会话阶段匹配
            "usage_score": 0.10,
        }

        # 关键任务提权 (Critical Task Boost)
        if context.task_type in self.CRITICAL_TASKS:
            weights["expertise_match"] = 0.60
            weights["persona_style_match"] = 0.10
            weights["workflow_match"] = 0.15
            weights["phase_match"] = 0.10
            weights["usage_score"] = 0.05

        # 风格优先任务 (Style Priority Boost)
        elif context.task_type in self.STYLE_PRIORITY_TASKS:
            weights["expertise_match"] = 0.20
            weights["persona_style_match"] = 0.35
            weights["workflow_match"] = 0.15
            weights["phase_match"] = 0.15
            weights["usage_score"] = 0.15

        # 阶段绑定：验证阶段更看重 QA
        if context.session_phase == "verification":
            if context.task_type in {"code_review", "security_review", "testing"}:
                weights["expertise_match"] += 0.10
                weights["phase_match"] = 0.15

        # 用户明确风格要求时调整
        if context.user_preference.formality == "strict":
            weights["workflow_match"] += 0.10
            weights["persona_style_match"] -= 0.05

        return self._normalize_weights(weights)

    def _normalize_weights(self, weights: dict[str, float]) -> dict[str, float]:
        """归一化权重，确保总和为 1.0"""
        total = sum(weights.values())
        return {k: v / total for k, v in weights.items()}

    def _calc_phase_match(self, candidate: RoleTriple, context: RoutingContext) -> float:
        """计算会话阶段匹配度 (v1.1 新增)

        Polaris 体系本质是状态机：
        - ideation → blueprint → execution → verification
        """
        # 从 Anchor 配置中获取 active_phases
        anchor = candidate.anchor
        if hasattr(anchor, 'routing') and hasattr(anchor.routing, 'active_phases'):
            if context.session_phase in anchor.routing.active_phases:
                return 1.0
            return 0.3  # 不匹配但可用

        # 通用 Anchor 默认支持所有阶段
        return 0.5
```

---

## 4. 配置Schema扩展

### 4.1 Profession 扩展

```yaml
# polaris/assets/roles/professions/python_principal_architect.yaml

id: python_principal_architect
type: profession
version: "1.0"

name: Python 首席架构师

# 路由规则声明
routing:
  # 支持的任务类型
  supported_tasks:
    - new_crate
    - system_improvement
    - performance_critical
    - refactor

  # 支持的领域
  supported_domains:
    - python
    - python_backend
    - python_ml

  # 意图映射
  intent_mapping:
    implement: python_principal_architect
    design: python_principal_architect
    review: security_auditor

  # 兼容性声明
  compatible_anchors:
    - polaris_director
    - polaris_qa

  compatible_personas:
    - gongbu_shilang
    - zhongshuling
    # 如果为空，表示与所有persona兼容

  # 评分提升
  score_boost:
    when:
      task_type: performance_critical
      boost: 0.2  # +20% 分数

  # 互斥规则
  excludes:
    - rust_systems_engineer  # 不能同时使用
```

### 4.2 Anchor 扩展

```yaml
# polaris/assets/roles/anchors/polaris_director.yaml

id: polaris_director
type: system_anchor
version: "1.0"

routing:
  # 推荐的意图
  recommended_intents:
    - implement
    - design
    - plan

  # 工作流类型映射
  workflow_mapping:
    new_crate: blueprint_then_execute
    refactor: sequential
    bug_fix: sequential

  # 优先级
  priority: 10  # 数值越高越优先

  compatible_personas:
    - gongbu_shilang
    - zhongshuling
```

### 4.3 Persona 扩展

```yaml
# polaris/assets/roles/personas/cyberpunk_hacker.yaml

id: cyberpunk_hacker
type: persona
version: "1.0"

name: 夜之城黑客

routing:
  # Persona风格标签
  style_tags:
    - tech_savvy
    - efficient
    - minimal_talk
    - hacker_culture

  # 匹配的用户偏好
  matches_preference:
    verbose_level: low
    communication_style: direct
    formality: casual

  # 推荐使用场景
  recommended_for:
    tasks:
      - code_review
      - security_audit
    domains:
      - devops
      - security
    intents:
      - analyze
      - review

  # 互斥的Domain
  excludes_domains:
    - legal
    - hr
```

### 4.4 路由规则配置

```yaml
# polaris/assets/roles/routing/rules.yaml

rules:
  # 规则名称
  - id: python_new_code
    name: Python新项目开发
    priority: 100

    # 匹配条件
    match:
      task_type: new_crate
      domain: python

    # 推荐的组合
    recommendation:
      anchor: polaris_director
      profession: python_principal_architect
      persona: null  # null表示由系统随机或从workspace固化

  # 通配规则
  - id: default_security
    name: 默认安全审查
    priority: 1
    match:
      task_type: security_review

    recommendation:
      anchor: polaris_qa
      profession: security_auditor
      persona: mentu_xiaozhong

  # Persona风格规则
  - id: casual_style
    name: 随性风格用户
    priority: 50
    match:
      user_preference:
        formality: casual

    recommendation:
      persona: cyberpunk_hacker  # 强制使用特定persona
```

---

## 5. 核心接口设计

### 5.1 RoleRoutingEngine

```python
class RoleRoutingEngine:
    """智能角色路由引擎"""

    def __init__(self, workspace: str = "") -> None:
        self._composer = get_role_composer()
        self._rule_loader = RoutingRuleLoader()
        self._cache = RoutingCache()
        self._preference_store = PreferenceStore(workspace)

    def route(
        self,
        context: RoutingContext,
    ) -> RoutingResult:
        """根据上下文路由到最优组合"""

    def route_with_fallback(
        self,
        context: RoutingContext,
        max_candidates: int = 3,
    ) -> list[RoutingResult]:
        """返回多个候选，按评分排序"""

    def learn_preference(
        self,
        user_id: str,
        persona_id: str,
        feedback: float,  # 1.0 = 完全满意, 0.0 = 不满意
    ) -> None:
        """根据反馈学习用户偏好"""


@dataclass
class RoutingContext:
    """路由上下文"""
    task_type: str                    # new_crate, refactor, bug_fix, ...
    domain: str                       # python, typescript, rust, ...
    intent: str                       # implement, design, analyze, review, ...
    constraints: dict[str, Any]       # time_budget, complexity, ...
    user_preference: UserPreference   # 用户的沟通风格偏好
    session_id: str = ""              # 用于学习
    # --- v1.1 新增字段 ---
    session_phase: str = "ideation"   # ideation→blueprint→execution→verification
    workspace_state: dict[str, Any] = field(default_factory=dict)  # 当前项目状态


@dataclass
class RoutingResult:
    """路由结果"""
    anchor: AnchorConfig
    profession: ProfessionConfig
    persona: PersonaConfig
    score: float                     # 综合评分 0.0 - 1.0
    match_details: dict[str, float]  # 各维度评分详情
    fallback_count: int = 0          # 回退次数
```

### 5.2 CompatibilityEngine

```python
class CompatibilityEngine:
    """兼容性检查引擎"""

    def is_compatible(
        self,
        anchor: AnchorConfig,
        profession: ProfessionConfig,
        persona: PersonaConfig,
        context: RoutingContext,
    ) -> bool:
        """检查三元组是否兼容"""

    def get_compatible_set(
        self,
        profession_id: str,
        context: RoutingContext,
    ) -> tuple[list[str], list[str]]:
        """获取兼容的anchor和persona列表"""
        profession = get_profession_loader().load(profession_id)

        # 合并显式声明和推断的兼容列表
        anchors = profession.routing.compatible_anchors.copy()
        personas = profession.routing.compatible_personas.copy()

        # 推断兼容性
        if not anchors:
            anchors = self._infer_compatible_anchors(profession)

        if not personas:
            personas = self._infer_compatible_personas(
                profession,
                context.user_preference
            )

        return anchors, personas


class ConflictResolver:
    """MIXED 模式冲突解决器 (v1.1 新增)

    解决用户显式指定与系统推断之间的冲突。
    核心原则：专业性 > 娱乐性 (Professional > Persona)
    """

    def resolve(
        self,
        manual: RoutingManualSpec | None,
        inferred: RoutingInference,
        context: RoutingContext,
    ) -> ResolvedTriple:
        """解决冲突，返回最终三元组

        Args:
            manual: 用户显式指定 (可为 None)
            inferred: 系统推断结果
            context: 路由上下文
        """
        if manual is None:
            return ResolvedTriple(
                anchor_id=inferred.anchor_id,
                profession_id=inferred.profession_id,
                persona_id=inferred.persona_id,
                resolution="inferred_only",
                warnings=[],
            )

        # 检查 persona 与 profession 的互斥
        persona_conflict = self._check_persona_profession_conflict(
            manual.persona_id, inferred.profession_id
        )

        if persona_conflict:
            # 策略：专业性永远大于娱乐性
            fallback_persona = self._get_fallback_persona(
                inferred.persona_id, context
            )

            return ResolvedTriple(
                anchor_id=manual.anchor_id or inferred.anchor_id,
                profession_id=inferred.profession_id,  # 保持专业
                persona_id=fallback_persona,
                resolution="persona_relaxed",
                warnings=[
                    f"Persona '{manual.persona_id}' 与专业 '{inferred.profession_id}' 不兼容，"
                    f"已切换到兼容 Persona '{fallback_persona}'"
                ],
            )

        # 无冲突，合并
        return ResolvedTriple(
            anchor_id=manual.anchor_id or inferred.anchor_id,
            profession_id=manual.profession_id or inferred.profession_id,
            persona_id=manual.persona_id or inferred.persona_id,
            resolution="manual_preferred",
            warnings=[],
        )

    def _check_persona_profession_conflict(
        self, persona_id: str, profession_id: str
    ) -> bool:
        """检查 Persona 与 Profession 是否互斥"""
        persona = get_persona_loader().load(persona_id)
        if not persona or not hasattr(persona, 'routing'):
            return False

        # 检查 excludes_domains
        profession = get_profession_loader().load(profession_id)
        if profession and hasattr(profession, 'domain'):
            if profession.domain in persona.routing.get('excludes_domains', []):
                return True

        return False

    def _get_fallback_persona(
        self, original_persona_id: str, context: RoutingContext
    ) -> str:
        """获取兼容的 fallback persona

        策略：保持风格相似但与当前 profession 兼容
        """
        # TODO: 实现基于 style_tags 的相似度匹配
        # 当前实现：返回默认 persona
        return "gongbu_shilang"


@dataclass
class RoutingManualSpec:
    """用户显式指定的路由规格"""
    anchor_id: str | None = None
    profession_id: str | None = None
    persona_id: str | None = None


@dataclass
class RoutingInference:
    """系统推断的路由结果"""
    anchor_id: str
    profession_id: str
    persona_id: str
    confidence: float


@dataclass
class ResolvedTriple:
    """解决冲突后的最终三元组"""
    anchor_id: str
    profession_id: str
    persona_id: str
    resolution: str  # "inferred_only" | "manual_preferred" | "persona_relaxed"
    warnings: list[str]
```

### 5.3 ScoringEngine

```python
class ScoringEngine:
    """评分引擎"""

    def __init__(self) -> None:
        self._usage_tracker = UsageTracker()

    def score_candidate(
        self,
        candidate: RoleTriple,
        context: RoutingContext,
    ) -> ScoringResult:
        """对候选组合进行多维度评分"""

        return ScoringResult(
            total_score=self._weighted_sum(
                expertise=self._calc_expertise(candidate, context),
                persona=self._calc_persona_style(candidate, context),
                workflow=self._calc_workflow_fit(candidate, context),
                history=self._calc_historical_preference(candidate, context),
            ),
            details={
                "expertise_match": self._calc_expertise(...),
                "persona_style": self._calc_persona_style(...),
                "workflow_fit": self._calc_workflow_fit(...),
                "user_preference": self._calc_historical_preference(...),
            }
        )

    def _calc_expertise(
        self,
        candidate: RoleTriple,
        context: RoutingContext,
    ) -> float:
        """计算专业匹配度"""
        expertise = candidate.profession.expertise

        # 精确匹配domain
        if context.domain in expertise:
            return 1.0

        # 前缀匹配
        for exp in expertise:
            if context.domain.startswith(exp.split("_")[0]):
                return 0.8

        # 模糊匹配
        domain_keywords = context.domain.split("_")
        matches = sum(1 for kw in domain_keywords if kw in expertise)
        return matches / len(domain_keywords) if domain_keywords else 0.0
```

---

## 6. 缓存与学习

### 6.1 多级缓存

```
┌─────────────────────────────────────────────────────────────┐
│                      路由缓存层级                             │
│  ┌───────────────────────────────────────────────────────┐  │
│  │ L1: Exact Match (context_hash)                       │  │
│  │     - 完全相同的上下文                                 │  │
│  │     - TTL: 5分钟                                     │  │
│  └───────────────────────────────────────────────────────┘  │
│  ┌───────────────────────────────────────────────────────┐  │
│  │ L2: Partial Match (task_type + domain)                 │  │
│  │     - 相同任务类型和领域                              │  │
│  │     - TTL: 15分钟                                   │  │
│  └───────────────────────────────────────────────────────┘  │
│  ┌───────────────────────────────────────────────────────┐  │
│  │ L3: User Preference (user_id)                        │  │
│  │     - 用户级别的persona偏好                           │  │
│  │     - TTL: 长期有效                                  │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

### 6.2 偏好学习

```python
class PreferenceLearner:
    """用户偏好学习器"""

    def record_feedback(
        self,
        session_id: str,
        persona_id: str,
        feedback: Feedback,
    ) -> None:
        """记录用户反馈"""

    def get_preferred_personas(
        self,
        user_id: str,
        context: RoutingContext,
    ) -> list[str]:
        """获取用户偏好的persona列表"""
        preferences = self._load_preference(user_id)

        # 根据上下文返回排序后的persona列表
        style = context.user_preference.communication_style

        return sorted(
            preferences.persona_history,
            key=lambda p: self._calc_style_similarity(p, style),
            reverse=True
        )
```

---

## 7. 回退机制

### 7.1 Fallback Chain

```python
FALLBACK_STRATEGY = {
    # 主链路失败时的回退顺序
    "primary": [
        # 1. 保持profession，降低persona要求
        ("same_profession", "relaxed_persona"),
        # 2. 使用更通用的profession
        ("broader_profession", "same_persona"),
        # 3. 使用默认组合
        ("default_anchor", "default_profession", "random_persona"),
    ],

    # 时间压力下的快速路径
    "time_constrained": [
        ("cached_result", None),  # 直接使用缓存
        ("last_used", None),       # 使用上次结果
        ("default", None),          # 使用默认
    ],
}
```

### 7.2 降级规则

| 场景 | 降级动作 |
|------|----------|
| Profession未找到 | 使用 `software_engineer` |
| Persona未找到 | 使用 `gongbu_shilang` |
| 评分都低于阈值 | 使用最高评分候选 |
| 全部失败 | 返回 `(polaris_director, software_engineer, gongbu_shilang)` |

---

## 8. 实现计划 (v1.1 - 优化后)

> **调整说明**: 将 Phase 5 (规则引擎) 提前到 Phase 2
> **理由**: YAML 规则配置和加载是整个系统的"骨架"，如果在后期才做，核心引擎容易写死硬编码逻辑。先定义好 Schema 和 Config Loader，保证核心引擎从第一天起就是数据驱动的。

### Phase 1: 核心数据结构 (Week 1)
- [ ] `RoutingContext` 数据结构 (含 session_phase, workspace_state)
- [ ] `RoutingResult`, `ScoringResult`, `RoleTriple` 数据类
- [ ] `SemanticIntentInferer` 两段式推断引擎骨架
- [ ] `LegacyRecipeAdapter` 向后兼容适配器

### Phase 2: 规则引擎 (Week 2-3) ← **提前**
- [ ] `RoutingRuleLoader` YAML 规则加载器
- [ ] `schema.yaml` 路由配置 Schema
- [ ] `RuleRegistry` 规则注册表
- [ ] `RuleMatcher` 规则匹配器 (精确 > 前缀 > 正则 > 默认)
- [ ] 动态规则重载机制

### Phase 3: 核心编排 (Week 3-4)
- [ ] `CompatibilityEngine` 兼容性检查
- [ ] `ConflictResolver` MIXED 模式冲突解决
- [ ] `RoleRoutingEngine` 核心编排引擎
- [ ] 基础回退链 (Fallback Chain)

### Phase 4: 评分与推断 (Week 4-5)
- [ ] `ScoringEngine` 动态权重评分
- [ ] `SemanticIntentInferer` Phase 2 语义推断 (Embedding/LLM)
- [ ] `UsageTracker` 历史使用追踪
- [ ] 关键任务权重调优 (security 0.6, style 0.35)

### Phase 5: 缓存与学习 (Week 5-6)
- [ ] L1/L2/L3 三级缓存
- [ ] `PreferenceLearner` 偏好学习
- [ ] `PreferenceStore` 持久化存储
- [ ] 缓存失效策略

### Phase 6: 集成与发布 (Week 6-8)
- [ ] 与 `PromptBuilder` 集成
- [ ] 端到端 Benchmark 测试
- [ ] 性能优化 (<50ms per route)
- [ ] 监控指标埋点
- [ ] 文档完善

---

## 9. 向后兼容

### 9.1 现有Recipe处理

```python
class LegacyRecipeAdapter:
    """旧Recipe适配器"""

    def to_routing_result(self, recipe_id: str) -> RoutingResult:
        """将固定Recipe转换为RoutingResult"""
        recipe = get_recipe_loader().load(recipe_id)

        return RoutingResult(
            anchor=get_anchor_loader().load(recipe.anchor),
            profession=get_profession_loader().load(recipe.profession),
            persona=get_persona_loader().load(recipe.persona),
            score=1.0,  # 完全匹配，无降级
            match_details={"legacy_recipe": True},
        )
```

### 9.2 迁移策略

1. **Phase 1-3**: 新系统与Recipe共存，Recipe优先
2. **Phase 4-5**: 逐渐将Recipe转换为路由规则
3. **Phase 6**: 完全切换到新系统，Recipe作为预定义规则保留

---

## 10. 监控指标

| 指标 | 说明 |
|------|------|
| `routing.cache_hit_rate` | 缓存命中率 |
| `routing.avg_candidates` | 平均候选数量 |
| `routing.fallback_rate` | 回退触发率 |
| `routing.avg_score` | 平均匹配分数 |
| `routing.preference_accuracy` | 偏好预测准确率 |

---

## 11. 文件结构 (v1.1)

```
polaris/kernelone/role/routing/
├── __init__.py
├── engine.py                  # RoleRoutingEngine 核心
├── context.py                 # RoutingContext 数据结构
│                               # + SemanticIntentInferer (两段式)
├── result.py                  # RoutingResult, ScoringResult, RoleTriple
├── compatibility.py           # CompatibilityEngine
│                               # + ConflictResolver (v1.1)
├── scoring.py                 # ScoringEngine (动态权重)
├── cache.py                   # RoutingCache 多级缓存
├── preference.py              # PreferenceLearner
├── semantic/                  # v1.1 新增：语义推断模块
│   ├── __init__.py
│   ├── inferrer.py           # SemanticIntentInferer
│   └── embedding_cache.py    # Embedding 缓存
├── rules/
│   ├── __init__.py
│   ├── loader.py             # RoutingRuleLoader
│   ├── matcher.py           # RuleMatcher
│   └── registry.py           # 规则注册表
├── adapters/
│   ├── __init__.py
│   └── legacy_recipe.py     # LegacyRecipeAdapter
└── config/
    ├── __init__.py
    ├── schema.yaml           # 路由配置 Schema
    └── default_rules.yaml    # 默认规则集
```

**新增文件说明**:
| 文件 | 用途 |
|------|------|
| `semantic/inferrer.py` | 两段式混合路由 (Fast Rule + Slow Semantic) |
| `semantic/embedding_cache.py` | Embedding 向量缓存 |
| `compatibility.py` + `ConflictResolver` | MIXED 模式冲突解决 |
| `config/default_rules.yaml` | 开箱即用的默认规则集 |
