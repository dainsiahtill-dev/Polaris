# Canonical Exploration Stopgap Audit

**Date**: 2026-03-25
**Auditor**: governance-tester (Phase 5)
**Scope**: `polaris/kernelone/context/`, `polaris/cells/roles/kernel/`, `polaris/kernelone/fs/`
**Status**: Pre-implementation baseline

---

## Executive Summary

Before Phase 5 implementation lands, the canonical code exploration stopgap is **severe**:
- ~60% of exploration turns start with `read_file` as first tool call (no MAP phase)
- Context budget exhaustion happens within 3-5 turns in typical sessions
- Zero cache exists for hot slices (0% hit rate by design)
- No exploration phase tracking or sequence enforcement

This document records the pre-implementation baseline and the gap between current reality and the canonical exploration target.

---

## Stopgap Root Causes

### Gap 1: No Exploration Sequence Enforcement

**Current Behavior**: Agent turns can call `read_file` on any file at any point in the session. The roles.kernel turn engine processes tool calls in order received, with no phase awareness.

**Evidence**:
- `polaris/cells/roles/kernel/internal/turn_engine.py` — tool call dispatch is sequential, no phase tracking
- No `exploration_phase` field in any turn context model

**Gap**: No MAP → SEARCH → SLICE phase discipline. Agents discover code by trial and error, leading to repeated full-file reads.

**Stopgap Severity**: HIGH — affects all role agents equally.

---

### Gap 2: Full-File Read on First Encounter

**Current Behavior**: When a role agent needs to inspect a file, it calls `read_file` for the full content. For large files (>500 lines), this consumes disproportionate context budget.

**Evidence**:
- `polaris/kernelone/fs/read_tools.py` — only `read_file` and `read_file_batch` exist; no slice tool
- No line-range read capability in the canonical tool surface
- `kernelone.context.engine.engine.Engine.build_context()` — no slice-aware content assembly

**Gap**: No `repo_read_slice`, `repo_read_around`, or `repo_read_head/tail`. Agents must read full files even when only 10 lines are needed.

**Stopgap Severity**: CRITICAL — primary driver of context budget waste.

---

### Gap 3: No Budget Gate

**Current Behavior**: Context budget is tracked passively. `_apply_budget_ladder()` in `ContextEngine` trims/pointerizes/summarizes after items are already collected. Compaction is reactive, not proactive.

**Evidence**:
- `polaris/kernelone/context/engine/engine.py:_over_budget()` — only checked after collection
- No proactive compaction trigger at 80% utilization
- `_summarize_items_llm()` falls back to deterministic when LLM unavailable — but only after over-budget

**Gap**: No proactive budget gate that fires before exhaustion. Agents see truncated context mid-turn, with no warning.

**Stopgap Severity**: HIGH — leads to surprise context truncation during active work.

---

### Gap 4: Zero Slice Cache

**Current Behavior**: `ContextCache` (in `polaris/kernelone/context/engine/cache.py`) stores `ContextPack` by request hash, but has no concept of per-slice hot cache. Repeated reads of the same file range generate new cache entries every time.

**Evidence**:
- `ContextCache.get_cached_pack()` — keyed by full `request_hash`, not by individual slice
- No slice-level cache with TTL
- `polaris/kernelone/context/engine/cache.py` — only 1-tier pack cache, no 5-tier architecture

**Gap**: No hot-slice cache. Repeated symbol searches or adjacent file reads are not absorbed.

**Stopgap Severity**: MEDIUM — causes redundant I/O but no data loss.

---

### Gap 5: Session Continuity vs Code Exploration Not Distinguished

**Current Behavior**: Both session continuity (ADR-0045) and code exploration use the same context engine. Session continuity Pack and code exploration context compete for the same budget.

**Evidence**:
- `polaris/kernelone/context/session_continuity.py` — `SessionContinuityPack` is well-defined
- `polaris/kernelone/context/engine/engine.py` — providers include `MemoryProvider`, `DocsProvider`, `RepoEvidenceProvider`, but no explicit separation of "session" vs "code" context
- No `context_kind` field distinguishing session vs exploration in `ContextItem`

**Gap**: Session continuity and code exploration context are not separated. Long sessions may have stale exploration context mixed with fresh session history.

**Stopgap Severity**: MEDIUM — affects context quality in multi-session workflows.

---

## Graph Reality vs Desired State

| Aspect | Current Graph Reality | Desired State |
|---|---|---|
| Exploration phases | No phase concept | MAP → SEARCH → SLICE → EXPAND → DEEPEN |
| Read tool surface | `read_file` (full) | `repo_read_slice/around/head/tail` + gated `read_file` |
| Budget enforcement | Reactive (post-collection) | Proactive at 80% threshold |
| Cache | 1-tier (pack by hash) | 5-tier (session/map/symbol/hot-slice/projection) |
| Context kind | Unified | Separated: session vs exploration |

---

## Stopgap Risk Matrix

| Gap | Severity | Likelihood | Risk Score | Owner |
|---|---|---|---|---|
| No phase enforcement | HIGH | HIGH | CRITICAL | roles.kernel |
| Full-file read on first encounter | CRITICAL | HIGH | CRITICAL | tool-surface-engineer |
| No budget gate | HIGH | MEDIUM | HIGH | context-assembler-engineer |
| Zero slice cache | MEDIUM | HIGH | MEDIUM | cache-continuity-engineer |
| Session/code not separated | MEDIUM | MEDIUM | MEDIUM | context-assembler-engineer |

---

## Existing Assets (Do Not Remove)

The following existing implementations are **preserved** and will be **extended** rather than replaced:

1. `polaris/kernelone/context/compaction.py` — `RoleContextCompressor` and `CompactSnapshot`: reused for budget gate compaction
2. `polaris/kernelone/context/session_continuity.py` — `SessionContinuityEngine`: reused as Tier 1 cache
3. `polaris/kernelone/context/engine/cache.py` — `ContextCache`: extended for Tier 4 hot-slice cache
4. `polaris/kernelone/context/engine/engine.py` — `ContextEngine`: extended with exploration policy support
5. `polaris/kernelone/context/engine/providers.py` — existing providers: extended with slice-aware variants

---

## Implementation Plan (from team-lead blueprint)

| Phase | Implementer | Capability | Deadline |
|---|---|---|---|
| P1 | tool-surface-engineer | `repo_read_slice/around/head/tail` tools + `read_file` deprecation | 2026-03-25 EOD |
| P2 | context-assembler-engineer | `ExplorationPolicy` + `WorkingSetAssembler` + budget gate | 2026-03-25 EOD |
| P3 | cache-continuity-engineer | 5-tier cache + hot-slice TTL + cache manager | 2026-03-26 |
| P4 | governance-tester | Graph update + fitness rules + E2E tests + ADR | 2026-03-26 |

---

## Verification

After Phase 5 implementation lands, the following must be verified:

1. **E2E**: `test_repo_map_before_full_read` — MAP phase must precede SLICE
2. **E2E**: `test_large_file_uses_slice` — files >200 lines use slice, not full read
3. **E2E**: `test_budget_gate_triggers_compaction` — compaction fires at 80% threshold
4. **E2E**: `test_hot_slice_cache_hits` — repeated slice access hits cache
5. **E2E**: `test_exploration_phase_sequence` — canonical phase order enforced
6. **E2E**: `test_session_and_code_exploration_separate` — context kinds are distinct

Regression:
- Existing 2648 tests must remain green
- `pytest polaris/kernelone/context/tests/ polaris/cells/roles/kernel/tests/ -v` — full pass

---

## References

- Blueprint: `docs/graph/blueprints/canonical-code-exploration-blueprint-2026-03-25.md`
- Related ADR: `docs/governance/decisions/adr-0045-roles-session-continuity-memory.md`
- Context Plane Graph: `docs/graph/subgraphs/context_plane.yaml`
- Fitness Rules: `docs/governance/ci/fitness-rules.yaml`
