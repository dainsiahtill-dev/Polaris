# ContextOS World-Class Context Management Blueprint

**Version**: 1.1.0 (Architecture Review Integrated)
**Date**: 2026-04-28
**Status**: Proposed
**Scope**: `polaris/kernelone/context/`, `polaris/cells/roles/kernel/internal/context_gateway/`
**Classification**: Strategic Architecture

---

## 0. Inviable Invariant (Read This First)

> **Attention is advisory, Contract is authoritative.**
>
> ContextOS 3.0 的注意力机制只能影响排序、压缩、召回、预算，不得覆盖合同、验收标准、TruthLog、硬性证据。
> 语义相关性高的内容不能压过合同；最近的错误日志不能压过 PM_TASKS.json；模型喜欢的叙事不能压过真实 evidence。
>
> 这是 HarborPilot / Polaris 的核心哲学：**合同不可变，事实流优先，证据高于叙事。**

---

## 1. Executive Summary

### 1.1 Current State Assessment

Polaris ContextOS is already one of the most sophisticated context management systems in the AI agent ecosystem. Through comprehensive code audit and analysis of 150+ related documents, we have identified:

- **7-stage projection pipeline** with immutable snapshots
- **Content-addressable storage** (ContentStore) with SHA-256 deduplication
- **Tiered summarization** (extractive → structured → semantic → truncation)
- **Claude Code-inspired token budgeting** with multi-level reserves
- **Dead-loop prevention** with 4-layer circuit breakers
- **Session continuity** with signal-scored sliding windows

**Architecture Score**: B+ (industry-leading foundation, gaps in adaptivity and cross-session intelligence)

### 1.2 The Gap to World-Class

| Dimension | Current | World-Class Target | Gap |
|-----------|---------|-------------------|-----|
| **Attention Mechanism** | Static pin + recency score | Dynamic intent-aware attention | Missing semantic relevance scoring |
| **Cross-Session Memory** | Per-session ContentStore | Unified semantic memory with promotion/demotion | Akashic exists but not integrated into ContextOS projection |
| **Predictive Compression** | Reactive (budget exceeded → compress) | Predictive (anticipate needs → pre-compress) | No lookahead model |
| **Context Explainability** | Debug logs only | Full provenance + decision rationale | No structured decision log |
| **Multi-Modal Context** | Text-only with MIME guessing | Unified representation for code/docs/diagrams | Tree-sitter only covers code |
| **Adaptive Budgeting** | Fixed ratios (0.18C output, 0.10C tool) | Phase-aware dynamic allocation | No task-phase adaptation |
| **Continuity Quality** | Signal scoring (±4 points) | Semantic embedding similarity | No vector-based relevance |

### 1.3 Design Philosophy

**"The best context management is the one the agent doesn't notice"**

A world-class context system should:
1. **Feel infinite** — Never lose critical information due to window limits
2. **Feel intelligent** — Surface exactly what's needed, when it's needed
3. **Feel transparent** — Every decision is inspectable and justifiable
4. **Feel continuous** — Cross-session memory flows naturally into current context

### 1.4 Industry Positioning

Polaris 在可审计性、不可变投影、内容寻址、上下文决策可回放等基础设施维度上，已经走向了与主流交互式 AI 编码工具不同的系统路线。主流工具更强调实时编辑体验与 IDE 集成，而 Polaris 更强调长期自治运行中的上下文治理、证据链和可复现性。

---

## 1.5 Hard Invariants (ContextOS 3.0 Must Preserve)

| # | Invariant | Rationale | Violation Consequence |
|---|-----------|-----------|----------------------|
| **INV-1** | 合同永远最高优先级 | goal / acceptance_criteria / hard_constraints / current_task_id 不得被 Attention/Compression 排除 | Agent loses track of what it's supposed to do |
| **INV-2** | TruthLog 是事实源，不是候选素材库 | TruthLog 事件可被摘要/降分辨率，但不能被"判定为不重要而消失"；至少保留 stub (event_id + kind + timestamp + artifact_refs + summary) | Evidence chain breaks, audit trail lost |
| **INV-3** | 摘要不是事实 | 所有 summary 必须带 derived_from / created_by / created_at / compression_policy / lossiness | LLM treats summaries as ground truth |
| **INV-4** | 被排除的内容也要可解释 | 不只解释"为什么选中"，还要解释"为什么没选中" | Context debugging becomes impossible |
| **INV-5** | Phase 不能由 LLM 单独决定 | Phase detection = deterministic_signals + optional_llm_hint + hysteresis | Phase oscillation, budget thrashing |
| **INV-6** | 原文不可丢失 | Multi-Resolution Store 的 full resolution 永不自动删除；压缩只是创建新投影 | Information loss is irreversible |
| **INV-7** | 智能层只能建议，不能裁决 | Intelligence Advisory Layer 输出的是 score + recommendation，Deterministic Kernel Layer 做最终决策 | Black-box context management |

---

## 2. Target Architecture: ContextOS 3.0 — "Auditable Attention-First Context Engine"

### 2.1 Core Insight: From State-First to Attention-First

Current architecture is **State-First**: WorkingState is the source of truth, transcript is secondary.

World-class architecture is **Attention-First**: What the LLM should *pay attention to* is the primary design target. State is a means to that end.

```text
State-First (Current):
  TruthLog → WorkingState → Projection → LLM Prompt

Attention-First (Target):
  TruthLog → AttentionGraph → WorkingState + RelevanceScore → Projection → LLM Prompt
```

### 2.2 Three-Layer Architecture (Critical Design Decision)

ContextOS 3.0 MUST be explicitly decomposed into three layers to prevent "intelligent black box" risk:

```text
ContextOS 3.0 Runtime
├── Layer 1: Deterministic Kernel Layer
│   ├── Contract protection (goal, acceptance_criteria, hard_constraints)
│   ├── Token hard budget enforcement
│   ├── Pinned content rules
│   ├── TruthLog reference integrity
│   ├── ContentStore addressing
│   ├── Immutable projection pipeline
│   └── Original content preservation (INV-6)
│
├── Layer 2: Intelligence Advisory Layer
│   ├── Attention scoring (multi-signal, NOT decision-making)
│   ├── Phase detection (deterministic signals + optional LLM hint)
│   ├── Memory relevance scoring
│   ├── Compression recommendation
│   └── Candidate ranking
│   └── OUTPUT: { candidate_id, recommended_action, confidence, reason_codes }
│   └── CONSTRAINT: Cannot modify TruthLog, cannot exclude contracts
│
└── Layer 3: Audit / Replay Layer
    ├── context_decisions.jsonl (per-candidate decision trace)
    ├── projection_report.json (per-projection summary)
    ├── Budget trace (phase → allocation → utilization)
    ├── Score breakdown (semantic, recency, contract_overlap, phase_affinity)
    ├── Compression lineage (full → extractive → structured → stub)
    └── Memory lineage (source → freshness → conflict_status)
```

**Key Rule**: Layer 2 can only *recommend*. Layer 1 makes the final decision. Layer 3 records everything.

### 2.3 Architecture Diagram

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│                         L6: Application Layer                                │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────────────┐  │
│  │  Role Session   │  │   Turn Engine   │  │  Exploration Loop           │  │
│  │   Orchestrator  │  │   Controller    │  │  (WorkingSetAsm)            │  │
│  └────────┬────────┘  └────────┬────────┘  └──────────────┬──────────────┘  │
└───────────┼────────────────────┼──────────────────────────┼─────────────────┘
            │                    │                          │
            ▼                    ▼                          ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                      L5: KernelOne - Attention-First ContextOS               │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │                    ContextSessionProtocol (CRUD + Query)             │    │
│  └───────────────────────────┬─────────────────────────────────────────┘    │
│                              │                                               │
│  ┌───────────────────────────┴─────────────────────────────────────────┐    │
│  │                  Attention-First Context Engine                      │    │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌────────────┐  │    │
│  │  │  TruthLog   │  │ Attention   │  │  Working    │  │  Token     │  │    │
│  │  │  Service    │──│   Graph     │──│   State     │──│  Budget    │  │    │
│  │  │ (Append-only│  │ (Semantic   │  │  Manager    │  │  Engine    │  │    │
│  │  │  Event Sourcing)│ Relevance) │  │             │  │ (Dynamic)  │  │    │
│  │  └─────────────┘  └─────────────┘  └─────────────┘  └────────────┘  │    │
│  │           │                │                │              │          │    │
│  │           ▼                ▼                ▼              ▼          │    │
│  │  ┌─────────────────────────────────────────────────────────────────┐  │    │
│  │  │              Multi-Tier Memory Subsystem                         │  │    │
│  │  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────────┐  │  │    │
│  │  │  │ Working  │ │ Episodic │ │Semantic  │ │   External       │  │  │    │
│  │  │  │ Memory   │ │ Memory   │ │ Memory   │ │   Memory         │  │  │    │
│  │  │  │(Session) │ │(Turn)    │ │(Cross-  │ │   (Vector Store) │  │  │    │
│  │  │  │          │ │          │ │ Session) │ │                  │  │  │    │
│  │  │  └──────────┘ └──────────┘ └──────────┘ └──────────────────┘  │  │    │
│  │  └─────────────────────────────────────────────────────────────────┘  │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                              │                                               │
│  ┌───────────────────────────┴─────────────────────────────────────────┐    │
│  │              Projection Engine (7-Stage Pipeline v3)                 │    │
│  │  ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐  │    │
│  │  │Transcript│ │Attention│ │State   │ │Budget  │ │Window  │ │Episode│  │    │
│  │  │Merger   │→│Router   │→│Patcher │→│Planner │→│Collector│→│Sealer │  │    │
│  │  └────────┘ └────────┘ └────────┘ └────────┘ └────────┘ └────────┘  │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                              │                                               │
│                              ▼                                               │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │              Adaptive Compression Registry                           │    │
│  │  ┌──────────────┐ ┌──────────────┐ ┌─────────────────────────────┐  │    │
│  │  │  Semantic    │ │  Structured  │ │  Predictive                 │  │    │
│  │  │  Compressor  │ │  Compressor  │ │  Compressor                 │  │    │
│  │  │ (Embedding-  │ │ (AST-based)  │ │  (Need Anticipation)        │  │    │
│  │  │  aware)      │ │              │ │                             │  │    │
│  │  └──────────────┘ └──────────────┘ └─────────────────────────────┘  │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Key Innovations

### 3.1 Innovation 1: Attention Graph (Semantic Relevance Scoring)

**Problem**: Current `_collect_active_window()` uses static rules (pinned sequences + recency + route type). It cannot distinguish between *important* old events and *irrelevant* old events.

**Solution**: Maintain an `AttentionGraph` that scores every transcript event by semantic relevance to the **current intent**.

```python
@dataclass(frozen=True, slots=True)
class AttentionNode:
    """A node in the attention graph representing one transcript event."""
    event_id: str
    sequence: int
    content_ref: ContentRef
    # Semantic embedding of the event content (cached)
    embedding: tuple[float, ...] | None = None
    # Static scores (computed once)
    recency_score: float = 0.0
    route_priority: float = 0.0
    # Dynamic scores (recomputed each turn)
    intent_relevance: float = 0.0
    cross_reference_count: int = 0
    # Final attention weight (normalized 0-1)
    attention_weight: float = 0.0


class AttentionGraph:
    """Semantic relevance graph for context prioritization.

    Maintains a dynamic graph where edges represent semantic relationships
    between events. The current intent (derived from latest user message + goal)
    acts as a "query" that propagates through the graph, scoring relevance.
    """

    def __init__(
        self,
        embedding_provider: EmbeddingProviderPort | None = None,
        similarity_threshold: float = 0.72,
    ):
        self._nodes: dict[str, AttentionNode] = {}
        self._edges: dict[str, set[str]] = {}  # event_id -> related event_ids
        self._embedding_provider = embedding_provider
        self._similarity_threshold = similarity_threshold

    def compute_intent_relevance(
        self,
        current_intent_embedding: tuple[float, ...],
        event_embedding: tuple[float, ...],
    ) -> float:
        """Compute cosine similarity between current intent and event."""
        return cosine_similarity(current_intent_embedding, event_embedding)

    def propagate_relevance(
        self,
        source_event_ids: set[str],
        decay_factor: float = 0.85,
        max_hops: int = 3,
    ) -> dict[str, float]:
        """Propagate relevance scores through the graph (PageRank-style).

        Similar to how human memory works: thinking about X reminds you of Y,
        which reminds you of Z, but the association weakens with each hop.
        """
        scores: dict[str, float] = {eid: 1.0 for eid in source_event_ids}
        for _hop in range(max_hops):
            new_scores: dict[str, float] = {}
            for eid, score in scores.items():
                for neighbor in self._edges.get(eid, set()):
                    new_scores[neighbor] = new_scores.get(neighbor, 0.0) + score * decay_factor
            scores = new_scores
        return scores

    def select_for_attention(
        self,
        token_budget: int,
        min_recent: int = 3,
    ) -> tuple[TranscriptEvent, ...]:
        """Greedy selection of events by attention_weight until token_budget."""
        # ... implementation ...
```

**Why this is world-class**:
- Mirrors human cognitive attention: current intent primes related memories
- Prevents "important but old" information from being lost
- Makes context decisions *explainable*: "This event was kept because it's semantically related to your current goal"

### 3.2 Innovation 2: Phase-Aware Dynamic Budgeting

**Problem**: Current budget uses fixed ratios (0.18C for output, 0.10C for tools) regardless of task phase. A "planning" phase needs more context (to understand the full problem), while an "execution" phase needs more tool budget.

**Solution**: Detect task phase from WorkingState and dynamically adjust budget allocation.

```python
@dataclass(frozen=True, slots=True)
class PhaseAwareBudgetPlan:
    """Dynamic budget allocation based on detected task phase."""

    # Detected phase from WorkingState
    phase: TaskPhase  # PLANNING | EXPLORING | IMPLEMENTING | REVIEWING | DEBUGGING

    # Phase-specific allocations (as ratios of total context window)
    # Planning: need more context (understand problem), less tool budget
    # Implementing: need more tool budget, context can be more focused
    # Debugging: need error logs + recent context, high retrieval budget
    _PHASE_PROFILES: ClassVar[dict[TaskPhase, BudgetProfile]] = {
        TaskPhase.PLANNING: BudgetProfile(
            output_reserve_ratio=0.15,
            tool_reserve_ratio=0.08,
            retrieval_ratio=0.18,  # High retrieval for research
            context_focus="broad",  # Keep more diverse context
        ),
        TaskPhase.EXPLORING: BudgetProfile(
            output_reserve_ratio=0.12,
            tool_reserve_ratio=0.15,  # More tool budget for exploration
            retrieval_ratio=0.12,
            context_focus="targeted",  # Focus on exploration targets
        ),
        TaskPhase.IMPLEMENTING: BudgetProfile(
            output_reserve_ratio=0.20,  # More output for code generation
            tool_reserve_ratio=0.12,
            retrieval_ratio=0.08,
            context_focus="recent",  # Recent implementation context
        ),
        TaskPhase.REVIEWING: BudgetProfile(
            output_reserve_ratio=0.18,
            tool_reserve_ratio=0.06,
            retrieval_ratio=0.15,  # High retrieval for cross-reference
            context_focus="comprehensive",  # Need full picture
        ),
        TaskPhase.DEBUGGING: BudgetProfile(
            output_reserve_ratio=0.15,
            tool_reserve_ratio=0.10,
            retrieval_ratio=0.20,  # Very high retrieval for error docs
            context_focus="error-centric",  # Error logs + stack traces prioritized
        ),
    }
```

**Detection heuristics**:
```python
def detect_task_phase(working_state: WorkingState) -> TaskPhase:
    """Detect current task phase from working state signals."""
    # Debugging: recent error events, traceback in transcript
    if has_recent_error_events(working_state, window=3):
        return TaskPhase.DEBUGGING

    # Implementing: accepted plan exists, open_loops contain implementation tasks
    if working_state.task_state.accepted_plan and any(
        is_implementation_task(loop) for loop in working_state.task_state.open_loops
    ):
        return TaskPhase.IMPLEMENTING

    # Exploring: high ratio of read-only tool calls in recent turns
    if recent_read_only_ratio(working_state, window=3) > 0.7:
        return TaskPhase.EXPLORING

    # Reviewing: explicit review markers or QA-related goals
    if is_review_goal(working_state.task_state.current_goal):
        return TaskPhase.REVIEWING

    # Default: planning
    return TaskPhase.PLANNING
```

### 3.3 Innovation 3: Predictive Compression (Need Anticipation)

**Problem**: All compression is reactive — we only compress when budget is exceeded. By then, valuable context may have already been lost.

**Solution**: Anticipate future context needs based on task patterns and pre-compress strategically.

```python
class PredictiveCompressor:
    """Anticipates future context needs and pre-compresses strategically.

    Based on:
    1. Historical pattern analysis (what did similar tasks need?)
    2. Working state trajectory (where is the task heading?)
    3. Explicit predictions from the LLM ("Next I'll need to check X")
    """

    def __init__(self, pattern_store: TaskPatternStore):
        self._pattern_store = pattern_store

    def predict_future_needs(
        self,
        working_state: WorkingState,
        recent_turns: int = 5,
    ) -> ContextNeedsPrediction:
        """Predict what context will be needed in the next 2-3 turns."""
        # Pattern matching against historical similar tasks
        pattern = self._pattern_store.find_similar_pattern(working_state)

        # Extract explicit forward references from assistant messages
        forward_refs = extract_forward_references(
            working_state.transcript[-recent_turns:]
        )

        # Predict likely next tools based on current open loops
        predicted_tools = predict_next_tools(working_state.task_state.open_loops)

        return ContextNeedsPrediction(
            likely_tools=predicted_tools,
            forward_references=forward_refs,
            pattern_suggestion=pattern.suggested_context if pattern else None,
        )

    def pre_compress(
        self,
        transcript: tuple[TranscriptEvent, ...],
        prediction: ContextNeedsPrediction,
        target_headroom: float = 0.15,  # Keep 15% budget headroom
    ) -> tuple[TranscriptEvent, ...]:
        """Pre-compress transcript to create budget headroom.

        Strategy:
        1. NEVER compress content predicted to be needed
        2. Aggressively compress content unlikely to be needed
        3. Create multi-resolution summaries (full → summary → stub)
        """
        # ... implementation ...
```

**Example**:
- Assistant says: "I'll need to check the database schema next"
- PredictiveCompressor marks schema-related events as "predicted_need: high"
- Even if budget pressure builds, these events are compressed last

### 3.4 Innovation 4: Multi-Resolution Context Store

**Problem**: Current ContentStore stores content at single resolution. When we need to save space, we truncate — losing information permanently.

**Solution**: Store content at multiple resolutions, allowing "focus" and "defocus" operations.

```python
@dataclass(frozen=True, slots=True)
class MultiResolutionContent:
    """Content stored at multiple resolutions for adaptive recall."""
    hash: str
    full: ContentRef              # Original content (may be evicted)
    summary: ContentRef           # ~30% of original length
    stub: ContentRef              # ~5% of original (key facts only)
    peek: str                     # ~100 chars inline (always available)
    # Metadata for resolution selection
    compression_quality: float    # 0-1 score of summary fidelity
    last_accessed_resolution: str  # "full" | "summary" | "stub"


class MultiResolutionStore(ContentStore):
    """Content store with multi-resolution support.

    When memory pressure increases:
    1. First: evict 'full' resolutions (keep summary + stub)
    2. Then: evict 'summary' resolutions (keep stub only)
    3. Finally: evict stubs (keep peek only)

    When an evicted resolution is needed:
    1. Try to reconstruct from higher-resolution version
    2. If unavailable, return placeholder with reconstruction hint
    """

    def get_at_resolution(
        self,
        ref: ContentRef,
        resolution: str = "auto",  # "auto" selects based on budget pressure
    ) -> str:
        """Get content at specified resolution."""
        mr_content = self._get_multi_resolution(ref.hash)

        if resolution == "auto":
            resolution = self._select_resolution(mr_content)

        if resolution == "full" and mr_content.full:
            return self._store.get(mr_content.full)
        elif resolution == "summary" and mr_content.summary:
            return self._store.get(mr_content.summary)
        elif resolution == "stub" and mr_content.stub:
            return self._store.get(mr_content.stub)

        return mr_content.peek  # Always available
```

### 3.5 Innovation 5: Context Decision Log (Explainability)

**Problem**: When context is compressed or events are dropped, there's no record of *why*. Debugging context issues requires reading the full code.

**Solution**: Every context decision produces a structured decision log that can be inspected, audited, and used for improvement.

```python
@dataclass(frozen=True, slots=True)
class ContextDecision:
    """A single context management decision with full provenance."""
    timestamp: str
    decision_type: str  # "compress" | "evict" | "pin" | "summarize" | "select"
    target_event_id: str | None
    reason: str
    # Quantitative rationale
    token_budget_before: int
    token_budget_after: int
    attention_score: float | None
    phase: TaskPhase | None
    # Alternative options considered
    alternatives: tuple[str, ...] = ()
    # Human-readable explanation
    explanation: str = ""


class ContextDecisionLog:
    """Immutable log of all context management decisions.

    Enables:
    1. Debugging: "Why was my error log truncated?"
    2. Audit: "Did we lose critical information?"
    3. Learning: "Which decisions led to poor outcomes?"
    4. Replay: "Reconstruct context with different parameters"
    """

    def __init__(self, max_decisions: int = 1000):
        self._decisions: list[ContextDecision] = []
        self._max_decisions = max_decisions

    def record(self, decision: ContextDecision) -> None:
        self._decisions.append(decision)
        if len(self._decisions) > self._max_decisions:
            # Archive old decisions to cold storage
            self._archive_old_decisions()

    def query(
        self,
        event_id: str | None = None,
        decision_type: str | None = None,
        min_impact: float | None = None,
    ) -> tuple[ContextDecision, ...]:
        """Query decisions by criteria."""
        # ... implementation ...
```

**Example output**:
```json
{
  "decision_type": "compress",
  "target_event_id": "evt_42",
  "reason": "token_budget_exceeded",
  "token_budget_before": 45000,
  "token_budget_after": 32000,
  "attention_score": 0.23,
  "phase": "IMPLEMENTING",
  "explanation": "Event evt_42 (tool result: repo_tree) was compressed from 12KB to 800 bytes because (1) token budget was exceeded by 5K tokens, (2) attention score (0.23) was below threshold (0.35), (3) task phase is IMPLEMENTING where exploration results are less critical. Alternative considered: evict entirely, but retained stub for continuity.",
  "alternatives": ["evict", "retain_full"]
}
```

---

## 4. Component Specification

### 4.1 AttentionRouter (Replaces/Enhances WindowCollector)

**Current**: `WindowCollector` in `pipeline/stages.py` collects active window based on pinned sequences + token budget.

**Target**: `AttentionRouter` uses AttentionGraph to make semantic relevance-based selections.

```python
class AttentionRouter(PipelineStage):
    """Stage 5 (v3): Routes context through attention graph."""

    def __init__(
        self,
        attention_graph: AttentionGraph,
        decision_log: ContextDecisionLog,
    ):
        self._attention_graph = attention_graph
        self._decision_log = decision_log

    def process(
        self,
        transcript: tuple[TranscriptEvent, ...],
        working_state: WorkingState,
        budget_plan: BudgetPlan,
    ) -> tuple[TranscriptEvent, ...]:
        # 1. Compute current intent embedding from latest user msg + goal
        intent_embedding = self._compute_intent_embedding(working_state)

        # 2. Score all events in attention graph
        self._attention_graph.score_all_events(
            intent_embedding=intent_embedding,
            working_state=working_state,
        )

        # 3. Select events by attention weight until budget exhausted
        selected = self._attention_graph.select_for_attention(
            token_budget=budget_plan.soft_limit,
            min_recent=self._policy.min_recent_messages_pinned,
        )

        # 4. Log all decisions
        for event in transcript:
            if event not in selected:
                self._decision_log.record(ContextDecision(
                    decision_type="exclude",
                    target_event_id=event.event_id,
                    reason="low_attention_score",
                    attention_score=self._attention_graph.get_score(event.event_id),
                    explanation=f"Excluded due to low relevance to current intent",
                ))

        return selected
```

### 4.2 PhaseAwareBudgetPlanner (Replaces BudgetPlanner)

**Current**: `BudgetPlanner` uses fixed ratios.

**Target**: `PhaseAwareBudgetPlanner` detects phase and adjusts allocations.

```python
class PhaseAwareBudgetPlanner(PipelineStage):
    """Stage 4 (v3): Plans token budget with phase-aware dynamic allocation."""

    def process(
        self,
        transcript: tuple[TranscriptEvent, ...],
        artifacts: tuple[ArtifactRecord, ...],
        working_state: WorkingState,
    ) -> BudgetPlan:
        phase = detect_task_phase(working_state)
        profile = PhaseAwareBudgetPlan._PHASE_PROFILES[phase]

        # Use phase-specific ratios instead of policy defaults
        window = self._resolved_context_window
        output_reserve = max(
            profile.output_reserve_min,
            int(window * profile.output_reserve_ratio),
        )
        # ... rest of budget computation with phase-aware values ...

        return BudgetPlan(
            # ... standard fields ...
            detected_phase=phase,
            phase_profile=profile,
        )
```

### 4.3 PredictiveCompressor (New Stage)

**New stage** inserted between BudgetPlanner and WindowCollector:

```python
class PredictiveCompressor(PipelineStage):
    """Stage 4.5 (v3): Pre-compresses context based on predicted needs."""

    def process(
        self,
        transcript: tuple[TranscriptEvent, ...],
        working_state: WorkingState,
        budget_plan: BudgetPlan,
    ) -> tuple[TranscriptEvent, ...]:
        prediction = self._predictor.predict_future_needs(working_state)

        # If prediction indicates we'll need more budget soon, pre-compress
        current_tokens = sum(_estimate_tokens(e.content) for e in transcript)
        if current_tokens > budget_plan.input_budget * 0.75:
            # Pre-compress to create headroom
            transcript = self._pre_compress(
                transcript,
                prediction,
                target_headroom=0.20,
            )

        return transcript
```

---

## 5. Implementation Roadmap

### Phase 1: Foundation (Week 1-2)

**Goal**: Build infrastructure without changing existing behavior.

| Task | File | Description |
|------|------|-------------|
| T1.1 | `context_os/attention/` | Create `AttentionGraph`, `AttentionNode`, `EmbeddingProviderPort` |
| T1.2 | `context_os/decision_log.py` | Create `ContextDecision`, `ContextDecisionLog` |
| T1.3 | `context_os/models_v2.py` | Add `TaskPhase`, `BudgetProfile`, `PhaseAwareBudgetPlan` |
| T1.4 | `context_os/content_store.py` | Extend `ContentStore` with `MultiResolutionStore` (behind flag) |
| T1.5 | `context_os/predictive.py` | Create `PredictiveCompressor` scaffold with no-op default |

**Validation**:
- All existing tests pass
- New components have >90% test coverage
- No performance regression in `project()` latency

### Phase 2: Attention-First Routing (Week 3-4)

**Goal**: Replace WindowCollector with AttentionRouter (behind feature flag).

| Task | File | Description |
|------|------|-------------|
| T2.1 | `context_os/pipeline/attention_router.py` | Implement `AttentionRouter` stage |
| T2.2 | `context_os/pipeline/runner.py` | Add `USE_ATTENTION_ROUTER` flag, integrate AttentionRouter |
| T2.3 | `context_os/embedding/` | Implement lightweight embedding provider (local + API fallback) |
| T2.4 | `context_os/tests/test_attention_router.py` | Unit tests for attention scoring |
| T2.5 | `context_os/evaluation.py` | Add attention quality metrics |

**Validation**:
- A/B test: AttentionRouter vs WindowCollector on 100 replay sessions
- Target: 15% improvement in "critical event retention" metric
- Target: <5ms additional latency per event

### Phase 3: Phase-Aware Budgeting (Week 5-6)

**Goal**: Implement dynamic budget allocation.

| Task | File | Description |
|------|------|-------------|
| T3.1 | `context_os/phase_detection.py` | Implement `detect_task_phase()` with comprehensive heuristics |
| T3.2 | `context_os/pipeline/phase_budget_planner.py` | Implement `PhaseAwareBudgetPlanner` |
| T3.3 | `context_os/policies.py` | Add phase-specific budget profiles |
| T3.4 | `context_os/tests/test_phase_detection.py` | Unit tests for phase detection accuracy |

**Validation**:
- Phase detection accuracy >85% on labeled test set
- Budget utilization improved (fewer emergency compressions)

### Phase 4: Predictive Compression (Week 7-8)

**Goal**: Implement need anticipation.

| Task | File | Description |
|------|------|-------------|
| T4.1 | `context_os/pattern_store.py` | Implement `TaskPatternStore` with similarity search |
| T4.2 | `context_os/predictive.py` | Implement full `PredictiveCompressor` |
| T4.3 | `context_os/pipeline/predictive_compressor.py` | Pipeline stage integration |
| T4.4 | `context_os/tests/test_predictive.py` | Unit tests |

**Validation**:
- Prediction accuracy: >60% of predicted needs are actually needed within 3 turns
- False positive rate: <30% (predicted but not needed)

### Phase 5: Multi-Resolution Store (Week 9-10)

**Goal**: Implement adaptive content resolution.

| Task | File | Description |
|------|------|-------------|
| T5.1 | `context_os/content_store.py` | Extend with multi-resolution support |
| T5.2 | `context_os/summarizers/` | Ensure all summarizers produce multi-resolution output |
| T5.3 | `context_os/tests/test_multi_resolution.py` | Unit tests |

**Validation**:
- Memory usage reduced by 30-50% under pressure
- Content reconstruction quality >90%

### Phase 6: Integration & Hardening (Week 11-12)

**Goal**: Full integration, feature flags removed, performance tuning.

| Task | File | Description |
|------|------|-------------|
| T6.1 | All | Remove feature flags, make v3 default |
| T6.2 | `context_os/metrics_collector.py` | Add attention, phase, prediction metrics |
| T6.3 | `context_os/evaluation.py` | Comprehensive evaluation suite |
| T6.4 | `docs/` | Documentation and ADR |

**Validation**:
- Full regression test suite passes
- Performance benchmarks meet targets
- Memory profiling shows no leaks

---

## 6. Metrics & Evaluation

### 6.1 Context Quality Metrics

```python
@dataclass
class ContextQualityReport:
    """Comprehensive context management quality assessment."""

    # Retention metrics
    critical_event_retention_rate: float  # % of error/exception events retained
    goal_relevance_score: float           # semantic similarity of context to current goal
    cross_reference_coverage: float       # % of referenced events that are accessible

    # Efficiency metrics
    token_utilization: float              # used / budget
    compression_quality_score: float      # fidelity of compressed vs original
    emergency_compression_rate: float     # % of turns triggering emergency

    # Latency metrics
    projection_latency_ms: float          # time to compute projection
    attention_scoring_latency_ms: float   # time for attention graph scoring

    # Decision quality
    decision_explainability: float        # % of decisions with full provenance
    prediction_accuracy: float            # % of predicted needs that materialize
    prediction_false_positive_rate: float

    # Cross-session metrics
    cross_session_recall_rate: float      # % of relevant past session content recalled
    memory_promotion_accuracy: float      # accuracy of working→episodic→semantic promotion
```

### 6.2 Target Values

| Metric | Current | Phase 2 Target | Phase 4 Target | Phase 6 Target |
|--------|---------|---------------|----------------|----------------|
| critical_event_retention | ~75% | 85% | 90% | 95% |
| token_utilization | ~82% | 85% | 88% | 90% |
| emergency_compression_rate | ~12% | 8% | 5% | 3% |
| projection_latency | ~45ms | 50ms | 55ms | 50ms |
| decision_explainability | 0% | 50% | 80% | 95% |
| prediction_accuracy | N/A | N/A | 60% | 70% |
| cross_session_recall | ~30% | 40% | 55% | 70% |

---

## 7. Risks & Mitigations

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| Embedding latency too high | Medium | High | Use lightweight local model (all-MiniLM-L6-v2, ~22MB); cache embeddings; async computation |
| Attention scoring introduces bias | Medium | High | A/B testing against baseline; human evaluation of context quality; adjustable similarity threshold |
| Phase detection inaccurate | Medium | Medium | Conservative defaults (bias toward PLANNING); manual override capability; continuous learning from corrections |
| Multi-resolution store complexity | Low | High | Incremental rollout (Phase 5); comprehensive property-based testing; fallback to single-resolution |
| Predictive compressor false positives | High | Low | False positives only waste space (don't lose data); tunable confidence threshold; pattern store pruning |
| Performance regression | Medium | High | Strict latency budgets per stage; async/offload heavy computation; feature flags for rollback |

---

## 8. Failure Modes & Defenses

| # | Failure Mode | Description | Defense |
|---|-------------|-------------|---------|
| **FM-1** | **Attention Drift** | 注意力逐渐偏向最近内容，忘记原始合同 | `contract_minimum_budget`, `contract_always_projected_stub`, `contract_overlap_score` |
| **FM-2** | **Summary Poisoning** | 错误摘要污染后续上下文 | `summary_lineage`, `summary_confidence`, `source_quote_required`, periodic rehydration |
| **FM-3** | **Memory Necromancy** | 旧记忆"复活"，覆盖新事实 | `freshness check`, `superseded_by`, `conflict detector`, `current-run-priority` |
| **FM-4** | **Phase Oscillation** | 任务阶段频繁切换，预算不断抖动 | `phase hysteresis`, `transition guard`, `minimum stable turns` (e.g. 2 turns minimum) |
| **FM-5** | **Compression Cascade** | 一次错误压缩导致后续越来越缺上下文 | `full content retained`, `rehydration trigger`, `compression quality check` |
| **FM-6** | **Decision Log Explosion** | 决策日志过大，反而成为负担 | `decision summary`, `sampled detailed traces`, `debug mode full trace` |
| **FM-7** | **Intelligence Overreach** | 智能层绕过 Deterministic Kernel 的硬规则 | `INV-7 enforcement`, `advisory-only contract`, `kernel veto power` |

### 8.1 Phase Transition Guards

```python
ALLOWED_TRANSITIONS = {
    TaskPhase.INTAKE: {TaskPhase.PLANNING, TaskPhase.EXPLORATION},
    TaskPhase.PLANNING: {TaskPhase.EXPLORATION, TaskPhase.IMPLEMENTATION},
    TaskPhase.EXPLORATION: {TaskPhase.PLANNING, TaskPhase.IMPLEMENTATION, TaskPhase.DEBUGGING},
    TaskPhase.IMPLEMENTATION: {TaskPhase.VERIFICATION, TaskPhase.DEBUGGING, TaskPhase.REVIEW},
    TaskPhase.VERIFICATION: {TaskPhase.IMPLEMENTATION, TaskPhase.DEBUGGING, TaskPhase.REVIEW},
    TaskPhase.DEBUGGING: {TaskPhase.IMPLEMENTATION, TaskPhase.EXPLORATION, TaskPhase.REVIEW},
    TaskPhase.REVIEW: {TaskPhase.IMPLEMENTATION},  # Reopen requires explicit reason
}

MINIMUM_PHASE_DURATION = 2  # turns
PHASE_CONFIDENCE_THRESHOLD = 0.7  # below this, keep current phase
```

---

## 8.2 Non-Goals

This blueprint explicitly does NOT include:

1. **Replacing the entire ContextOS** — We enhance, not rewrite
2. **New LLM models** — We use existing embedding/summarization models
3. **Distributed context storage** — Single-node architecture maintained
4. **Real-time collaborative editing** — Out of scope
5. **Multi-agent shared context** — Future work, not this blueprint

---

## 9. Related Documents

| Document | Relationship |
|----------|-------------|
| `CONTEXTOS_2_0_BLUEPRINT.md` | Predecessor — Phase 2-4 summarization |
| `CONTEXTOS_MEMORY_ARCHITECTURE_V2.md` | Predecessor — ContentStore design |
| `CONTEXT_ARCHITECTURE_REFACTOR_20260423.md` | Predecessor — Pipeline refactoring |
| `CONTEXT_PRUNING_RECOVERY_BLUEPRINT_20260412.md` | Complementary — Dead loop prevention |
| `ADR-0076-contextos-summarization-strategy.md` | Complementary — Summarization strategy |
| `ADR-0071-transaction-kernel-context-plane-isolation.md` | Constraint — Context plane isolation |

---

## 10. Appendix: Comparison with Industry Standards

| System | Strengths | ContextOS 3.0 Advantage |
|--------|-----------|------------------------|
| **Claude Code** | Excellent token budgeting | ContextOS 3.0 adds semantic attention + phase awareness |
| **Cursor** | Good file context management | ContextOS 3.0 adds cross-session memory + predictive compression |
| **GitHub Copilot** | Deep IDE integration | ContextOS 3.0 adds explainability + multi-resolution store |
| **LangGraph** | Graph-based state management | ContextOS 3.0 adds attention-first routing + decision logging |
| **MemGPT** | Explicit memory tiers | ContextOS 3.0 adds predictive promotion + semantic relevance |

**Competitive Moat**: No other system combines (1) semantic attention scoring, (2) phase-aware budgeting, (3) predictive compression, and (4) full decision explainability in a single integrated architecture.

---

**Blueprint Author**: Principal Architect
**Review Status**: Pending
**Next Milestone**: T1.1-T1.5 Foundation Phase Completion
