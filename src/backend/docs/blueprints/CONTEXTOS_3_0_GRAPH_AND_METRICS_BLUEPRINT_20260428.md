# ContextOS 3.0 Graph Propagation & Metrics Blueprint

**Version**: 1.0.0
**Date**: 2026-04-28
**Status**: Proposed
**Scope**: `polaris/kernelone/context/context_os/attention/`, `polaris/kernelone/context/context_os/metrics/`
**Classification**: Enhancement

---

## 1. Executive Summary

### 1.1 Current State

ContextOS 3.0 MVP已完成：
- Phase 0: Context Decision Log (Audit/Replay Layer) ✅
- Phase 1: Multi-Resolution Store ✅
- Phase 2: Phase-Aware Budgeting ✅
- Phase 3: Attention Scoring V1 (Multi-signal weighted scoring) ✅

### 1.2 Remaining Gaps

| Gap | Impact | Priority |
|-----|--------|----------|
| **No graph propagation** | Attention scores are local, miss cross-event relationships | P1 |
| **No Prometheus metrics** | Cannot observe ContextOS behavior in production | P2 |

### 1.3 Design Philosophy

**"Attention is advisory, Contract is authoritative."**

Graph propagation enhances attention scoring but never overrides contract protection.

---

## 2. Graph Propagation Architecture

### 2.1 Graph Structure

```text
Event Graph:
  Nodes: TranscriptEvents
  Edges: Relationships between events

Edge Types:
  - same_file: Events referencing the same file
  - same_symbol: Events referencing the same symbol/function/class
  - same_run_id: Events from the same run
  - mentions_same_task: Events mentioning the same task ID
  - derived_from_same_event: Events derived from the same source
  - contradicts: Events that contradict each other
  - supersedes: Events that supersede earlier events
```

### 2.2 Propagation Algorithm

**PageRank-style propagation** with edge-type-specific weights:

```python
node_score = base_score + Σ(neighbor_score * edge_weight * decay_factor ^ hops)
```

Where:
- `base_score`: Multi-signal score from AttentionScorer
- `edge_weight`: Weight based on edge type (same_file=0.3, contradicts=0.8, etc.)
- `decay_factor`: 0.85 (configurable)
- `max_hops`: 3 (configurable)

### 2.3 Edge Detection

| Edge Type | Detection Method | Weight |
|-----------|------------------|--------|
| `same_file` | Extract file paths from content, compare | 0.3 |
| `same_symbol` | Extract symbol names (function/class), compare | 0.5 |
| `same_run_id` | Compare `run_id` metadata | 0.2 |
| `mentions_same_task` | Extract task IDs from content, compare | 0.4 |
| `derived_from_same_event` | Compare `source_event_id` metadata | 0.6 |
| `contradicts` | Detect negation patterns (e.g., "not X" vs "X") | 0.8 |
| `supersedes` | Detect temporal ordering + same topic | 0.7 |

---

## 3. Prometheus Metrics Architecture

### 3.1 Metric Categories

| Category | Metrics | Type |
|----------|---------|------|
| **Content Store** | `content_store_entries`, `content_store_bytes`, `content_store_hit_rate` | Gauge |
| **Multi-Resolution** | `multi_resolution_count_by_level`, `multi_resolution_evictions` | Gauge/Counter |
| **Phase Detection** | `phase_transitions_total`, `phase_duration_seconds` | Counter/Histogram |
| **Attention Scoring** | `attention_score_distribution`, `attention_candidates_ranked` | Histogram/Counter |
| **Decision Log** | `decision_log_entries`, `decision_log_by_type` | Gauge/Counter |
| **Budget** | `budget_utilization_ratio`, `budget_overruns` | Gauge/Counter |

### 3.2 Metric Naming Convention

```
contextos_<category>_<metric_name>
```

Examples:
- `contextos_content_store_entries`
- `contextos_phase_transitions_total`
- `contextos_attention_score_distribution`

---

## 4. Implementation Plan

### 4.1 Graph Propagation (P1)

| Task | File | Description |
|------|------|-------------|
| T1.1 | `attention/graph.py` | Implement `EventGraph` with edge detection |
| T1.2 | `attention/propagation.py` | Implement `GraphPropagator` with PageRank-style algorithm |
| T1.3 | `attention/scorer.py` | Integrate graph propagation into `AttentionScorer` |
| T1.4 | `tests/test_graph_propagation.py` | Unit tests |

### 4.2 Prometheus Metrics (P2)

| Task | File | Description |
|------|------|-------------|
| T2.1 | `metrics/collectors.py` | Implement metric collectors |
| T2.2 | `metrics/exporters.py` | Implement Prometheus exporter |
| T2.3 | `pipeline/runner.py` | Integrate metrics into pipeline |
| T2.4 | `tests/test_metrics.py` | Unit tests |

---

## 5. Verification Plan

### 5.1 Graph Propagation Tests

```python
def test_same_file_edge_detection():
    """Events referencing same file should have same_file edge."""

def test_contradicts_edge_detection():
    """Contradicting events should have contradicts edge with high weight."""

def test_propagation_increases_related_scores():
    """Related events should have higher scores after propagation."""

def test_propagation_respects_max_hops():
    """Propagation should not exceed max_hops."""
```

### 5.2 Metrics Tests

```python
def test_content_store_metrics():
    """Content store metrics should be updated correctly."""

def test_phase_transition_metrics():
    """Phase transitions should increment counter."""

def test_attention_score_histogram():
    """Attention scores should be recorded in histogram."""
```

### 5.3 Quality Gates

```bash
ruff check polaris/kernelone/context/context_os/ --fix
ruff format polaris/kernelone/context/context_os/
mypy polaris/kernelone/context/context_os/
pytest polaris/kernelone/context/context_os/tests/ -v
```

---

## 6. Risks & Mitigations

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| Graph construction overhead | Medium | Medium | Lazy construction, cache edges |
| Propagation convergence | Low | High | Max hops limit, convergence check |
| Metrics cardinality explosion | Medium | Medium | Bounded label sets, aggregation |
| Backward compatibility | Low | High | Feature flags for all new features |

---

## 7. Success Criteria

1. Graph propagation improves attention score quality (measured by human evaluation)
2. All Prometheus metrics are exported correctly
3. No performance regression (<5% latency increase)
4. All existing tests pass (165+ tests)
5. New tests achieve >90% coverage

---

**Blueprint Author**: Principal Architect
**Review Status**: Pending
**Next Milestone**: T1.1-T1.4 Graph Propagation Implementation
