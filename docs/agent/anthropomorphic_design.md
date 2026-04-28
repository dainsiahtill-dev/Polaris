# Anthropomorphic Architecture Design

This document outlines the integration of cognitive architecture concepts from _Generative Agents_ into Polaris, strictly adhering to engineering constraints like traceability, replayability, and append-only immutability.

## 0. Why Anthropomorphic Core (工程动机)

拟人化核心不是“演出效果”，而是无人值守稳定性的工程机制：

- **Negotiable Roles**: When Director is stuck, PM/Chief Engineer/QA can negotiate on the same task based on evidence chain, rather than letting the pipeline freeze on a single path.
- **自我修复闭环**：通过角色对话把失败转成可执行动作（补文件、缩作用域、调整施工图），减少“直接 FAIL 终止”频率。
- **弱模型可用化**：Director 即使能力较弱，也可按Chief Engineer施工图执行；系统把“模型能力差异”转成“流程可治理”。
- **可追溯可回放**：所有协商与决策仍落在 append-only 证据流中（`events.jsonl` + `DIALOGUE.jsonl`），保证审计与复盘。

与传统“单线路定死”自动化相比，拟人化核心提供的是**可讨论、可纠偏、可迭代**的运行控制面。

## 1. Core Architecture

### 1.1 Module Mapping & Abstraction

We introduce a dedicated `anthropomorphic` module to encapsulate cognitive features.

| Concept              | Polaris Impl                | Responsibility                                                                              |
| :------------------- | :------------------------------ | :------------------------------------------------------------------------------------------ |
| **Truth Source**     | `events.jsonl`                  | Immutable, append-only raw log of all system actions. **MUST** include stable UUIDs.        |
| **Memory Stream**    | `MEMORY.jsonl`                  | Derived stream. Enriched/Compressed representation of events with embedding and importance. |
| **Reflection**       | `REFLECTIONS.jsonl`             | Derived stream. Higher-level insights and heuristics generated from memories.               |
| **Inner Voice**      | `DIALOGUE.jsonl` (type=thought) | Extracted thinking summaries from model outputs.                                            |
| **Reflection Logic** | `reflection_module`             | `Scheduler` (When), `Generator` (How), `Store` (Persistence).                               |
| **Retrieval**        | `memory_store`                  | Complex scoring (Relevance/Recency/Importance) + Diversity Filtering.                       |

### 1.2 Persistence Strategy (The "Derivation" Rule)

To maintain the "Truth Source" invariant:

1.  **`events.jsonl`**: The **only** source of truth. Every event MUST have a pre-generated, deterministic UUID (`event_id`).
2.  **`MEMORY.jsonl` / `REFLECTIONS.jsonl`**: Strictly derived.
    - **Online Path (Fast)**: As events occur, they are immediately processed (rule-based keywords/importance) and appended to `MEMORY.jsonl`.
    - **Offline Path (Rebuild)**: A cleanup utility can delete derived logs and rebuild them purely from `events.jsonl`.
3.  **Embedding Consistency**: Embeddings are computed ONCE at MemoryItem creation. `embedding_id` MUST equal `memory_id` (1:1 mapping). If missing, rebuilds can regenerate them.

## 2. Schema Definitions

### 2.1 Memory Item (`MEMORY.jsonl`)

Enriched representation of an event.

```python
class MemoryItem(BaseModel):
    id: str = Field(default_factory=lambda: f"mem_{uuid4()}")
    source_event_id: str  # Stable UUID from events.jsonl
    step: int             # Canonical clock: Global Event Sequence (0, 1, 2...)
    timestamp: datetime
    role: str             # PM / Director / QA

    type: str             # observation / plan / reflection_summary
    kind: str             # error | info | success | warning | debug (Severity)

    text: str             # Natural language content
    importance: int       # 1-10
    keywords: List[str]
    hash: str             # SHA1(text + type + role + context) for deduplication

    context: Dict[str, Any] # { "run_id": "...", "phase": "..." }
    # embedding_id is implicitly self.id
```

### 2.2 Reflection Node (`REFLECTIONS.jsonl`)

Heuristics or summaries derived from memories.

```python
class ReflectionNode(BaseModel):
    id: str = Field(default_factory=lambda: f"ref_{uuid4()}")
    created_step: int
    expiry_steps: int     # How long this insight remains valid (Decay)

    type: str             # heuristic / summary / preference
    scope: List[str]      # e.g., ["npm", "network"] - limits applicability
    confidence: float     # 0.0 - 1.0

    text: str
    evidence_mem_ids: List[str] # Back-links to memories that formed this
    importance: int
```

### 2.3 Prompt Context Event (`events.jsonl`)

Logged _before_ every LLM call to ensure observability.

```json
{
  "kind": "observation",
  "type": "prompt_context",
  "content": {
    "run_id": "current_run_123",
    "phase": "director.execution",
    "step": 456, // Global sequence number
    "persona_id": "director.v1",
    "retrieved_mem_ids": ["mem_A", "mem_B"],
    "retrieved_ref_ids": ["ref_X"],
    "strategy": "vector_similarity",
    "token_usage_estimate": 850
  }
}
```

## 3. Interfaces

### 3.1 Reflection Module

```python
class ReflectionScheduler:
    def should_reflect(self, context: Context) -> bool:
        """
        Triggers:
        - Every N steps (e.g., 50)
        - Error rate > Threshold
        - Consecutive failures >= 3
        """
        pass

class ReflectionGenerator:
    def generate(self, memories: List[MemoryItem], objective: str) -> List[ReflectionNode]:
        """Calls LLM to abstract insights."""
        pass
```

## 4. Retrieval Logic

### 4.1 Scoring Formula

$$ Score = w*{rel} \cdot Tier2Rel(q, m) + w*{rec} \cdot e^{\frac{-(current_step - m.step)}{\tau}} + w\_{imp} \cdot \frac{Imp}{10} $$

- **Step Clock**: Recency uses `global_event_seq`, ensuring all modules share one timeline.
- **Relevance**:
  - _Tier 1 (Vector)_: Cosine Similarity.
  - _Tier 2 (Keyword)_: Jaccard/BM25 (fallback if vector DB unavailable).

### 4.2 Candidate Pruning & Diversity

1.  **Deduplication**: Filter by `hash` (prevent identical log entries).
2.  **Diversity Rule (Based on `kind`)**:
    - `error`: Max 5 items
    - `info`: Max 3 items
    - `success`: Max 3 items
    - Others: Max 2 items

## 5. Integration Guardrails

### 5.1 Token Budget (Hard Limits)

To prevent context overflow:

- **Total Injection Budget**: Max 1200 Tokens
- **Per-Item Limits**:
  - Persona: ≤ 120 tokens
  - Memories (Top-K): ≤ 10 items, each ≤ 200 chars
  - Reflections: ≤ 3 items, each ≤ 240 chars

### 5.2 Prompt Ordering (MUST)

1.  **System/Persona**: "You are [Role]..."
2.  **Contract (IMMUTABLE)**: "Your Goal is X. Acceptance Criteria are Y."
3.  **Relevant Memories**: "Relevant past experiences (Time relative to step X):..."
4.  **Current State**: "Current step is..."
5.  **Output Format / Invariants**: JSON Schema enforcement.

### 5.3 Fallbacks

- If Vector DB fails -> Keyword Search.
- If Persona missing -> Standard Assistant.

---

## 6. Inner Voice Integration

本章节描述如何将模型的"思考内容"抽取并展示为拟人化的"内心独白"对话。

### 6.1 设计目标

把"思考内容"当作一种特殊消息类型，实现以下效果：

| Speaker              | 消息类型                   | 视觉样式               |
| -------------------- | -------------------------- | ---------------------- |
| Director             | 对外汇报 (`say`)           | 正常亮度，可读、可交付 |
| Director·Inner Voice | 内心独白 (`thought`)       | 低亮度/半透明/可折叠   |
| QA                   | 裁决与证据引用 (`say`)     | 更冷静的色调           |
| System               | 状态机变化 (`mode_change`) | 系统色                 |

**示例内心独白：**

> "我需要先读哪些文件…我怀疑类型定义重复…我打算先跑 type-check…"

> **核心约束**：内心独白仍然是只读旁路投影，不干预系统事实（符合 UI Read-Only 不变量）。

### 6.2 Thinking Normalizer（思考内容规范化器）

#### 6.2.1 抽取规则优先级

从最确定到最不确定：

| 优先级 | 来源类型       | 置信度 | 匹配方式                                                                          |
| ------ | -------------- | ------ | --------------------------------------------------------------------------------- |
| 1      | **结构化字段** | `high` | 响应中有 `reasoning_summary` / `thinking` / `reasoning` 字段                      |
| 2      | **标签块抽取** | `high` | `<think>...</think>`, `<analysis>...</analysis>`, `BEGIN_THINKING...END_THINKING` |
| 3      | **启发式匹配** | `low`  | 识别 `Thoughts:`, `Reasoning:`, `Plan:` 等段落头                                  |

> ⚠️ **警告**：启发式匹配必须保守，避免把正文误判成 thinking。

#### 6.2.2 规范化输出 Schema

```typescript
type NormalizedThinking = {
  kind: "reasoning_summary" | "think_block" | "heuristic";
  content: string;
  confidence: "high" | "medium" | "low";
};
```

#### 6.2.3 Provider 适配策略

| Provider                             | 思考来源                         | 抽取方式                           |
| ------------------------------------ | -------------------------------- | ---------------------------------- |
| **CLI (Codex/Gemini)**               | stdout/stderr 中的思考段落       | 捕获完整输出后 `extractThinking()` |
| **Ollama / 本地 HTTP**               | 响应正文中的 `<think>` 标签      | 正则抽取                           |
| **第三方 HTTPS (OpenAI-compatible)** | `reasoning_summary` 字段（如有） | 结构化字段优先                     |

> 💡 **现实做法**：把 thinking 视作可选信号，有就展示，没有就不显示。

### 6.4 角色人格化独白

通过 `thought.kind` 子类型区分不同角色的独白风格：

| Kind          | 说明             | 示例                                       |
| ------------- | ---------------- | ------------------------------------------ |
| `inner_voice` | 常规内心独白     | "我需要先检查类型定义…"                    |
| `reflection`  | 触发反思时的独白 | "上次改 auth 模块出了问题，这次要先写测试" |
| `flashback`   | 检索记忆时的独白 | "我记得之前处理过类似的端口冲突…"          |

**不同角色的语气差异：**

| Persona      | 独白语气     | 关注点                     |
| ------------ | ------------ | -------------------------- |
| **PM**       | 项目经理旁白 | 风险/依赖/节奏/资源        |
| **Director** | 工程师自检   | 证据/计划/回滚/技术细节    |
| **QA**       | 冷静裁决     | 证据引用/失败签名/测试覆盖 |

### 6.5 Glass Mind 融合

Dashboard 右侧的 **Glass Mind** 侧栏新增 **Inner Voice Feed** 区块：

```
┌─────────────────────────────────────┐
│  🔮 Glass Mind                      │
├─────────────────────────────────────┤
│  📚 Retrieved Memories (2)          │
│  ├─ mem_A: "上次 npm install 超时"  │
│  └─ mem_B: "auth 模块需要测试"      │
├─────────────────────────────────────┤
│  💭 Inner Voice Feed                │
│  ├─ "我怀疑类型定义重复…"           │
│  └─ "先跑 type-check 验证…"         │
├─────────────────────────────────────┤
│  🧠 Active Reflections (1)          │
│  └─ ref_X: "npm 命令需要超时保护"   │
└─────────────────────────────────────┘
```

**交互能力：**

| 功能     | 说明                                                       |
| -------- | ---------------------------------------------------------- |
| 全局开关 | `Show Inner Voice` (默认关/或默认折叠)                     |
| 过滤器   | 只看 PM / Director / QA 的 thought                         |
| 关联定位 | thought 卡片右上角 `↗`，点击跳到对应 run 的 phase/timeline |

### 6.6 UI 渲染规范

#### 6.6.1 样式规范（赛博朋克但克制）

```css
/* Inner Voice Card Styles */
.thought-card {
  /* 透明度 60% */
  opacity: 0.6;

  /* 背景玻璃态 + 紫色细边 glow */
  background: rgba(139, 92, 246, 0.08);
  border: 1px solid rgba(139, 92, 246, 0.3);
  box-shadow: 0 0 8px rgba(139, 92, 246, 0.2);

  /* 字体偏细、略小 */
  font-size: 0.875rem;
  font-weight: 300;
}

.thought-card:hover {
  opacity: 0.85;
}

.thought-card .icon {
  /* 左侧小图标 */
  content: "🧠"; /* 或 ◌ */
}

```

#### 6.6.2 折叠行为

- **默认折叠**：显示首行摘要 + "展开"按钮
- **展开后**：最多显示 N 行（建议 10 行），避免刷屏
- **批量折叠**：提供"折叠所有 Inner Voice"按钮

#### 6.6.3 区分于正常对话

| 元素     | 正常对话 (say) | 内心独白 (thought) |
| -------- | -------------- | ------------------ |
| 边框颜色 | 青色 glow      | 紫色 glow          |
| 透明度   | 100%           | 60%                |
| 字重     | normal         | light              |
| 默认状态 | 展开           | 折叠               |

---

## 7. Persona Configuration

### 7.1 Persona Schema (`prompts/role_persona.yaml`)

```yaml
personas:
  pm:
    name: "Project Manager"
    style: "strategic, risk-aware, dependency-focused"
    forbidden:
      - "直接修改代码"
      - "跳过验收标准"
    inner_voice_tone: "项目经理旁白，关注风险和资源"

  director:
    name: "Technical Director"
    style: "evidence-driven, precise, engineering-minded"
    forbidden:
      - "模棱两可的表述"
      - "无证据的判断"
    inner_voice_tone: "工程师自检，严谨引用日志证据"

  qa:
    name: "Quality Assurance"
    style: "objective, cold, evidence-citing"
    forbidden:
      - "主观评价"
      - "忽略测试结果"
    inner_voice_tone: "冷静裁决者，仅基于事实"
```

### 7.2 Persona Injection

Persona 内容在 prompt 组装时注入到 system message 开头：

```
[System] You are {persona.name}. Your style is {persona.style}.
Forbidden actions: {persona.forbidden}
```
