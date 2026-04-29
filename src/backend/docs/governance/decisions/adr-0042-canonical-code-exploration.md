# ADR-0042: KernelOne Context Strategy Framework with Canonical Default Profile

- Status: Proposed
- Date: 2026-03-25
- Scope: `kernelone.context`, `kernelone.tools`, `roles.kernel`, `polaris/cells/roles/kernel`
- Blueprint: `docs/KERNELONE_CONTEXT_STRATEGY_FRAMEWORK_BLUEPRINT_2026-03-25.md`
- Related ADRs: ADR-0045 (Session Continuity Memory), ADR-0042 (Turn Engine Triple Responsibility)
- Team Contact: tool-surface-engineer, context-assembler-engineer, cache-continuity-engineer

---

## Context

### Problem Statement

Current role agent code exploration suffers from three systemic pathologies:

1. **Premature Full-File Reads**: Agents reach for `read_file` (full content) on first encounter with any file, even when only a slice is needed. Files >500 lines are consumed entirely, burning context budget.

2. **No Exploration Sequence Discipline**: There is no enforced canonical phase order. Agents jump from user query directly to file reads without first building a repo map, which leads to fragmented understanding and repeated reads.

3. **Zero Cache for Repeated Slices**: The same file slice accessed across multiple turns is re-read every time, with no session-level hot-slice cache to absorb repeat traffic.

These pathologies compound: a 30-turn coding session can spend 40-60% of its context budget on redundant full-file reads that should have been slices.

### Evidence

- Turn engine tool call logs show `read_file` as the first tool call in >60% of exploration turns.
- Context budget exhaustion logs show `trim` and `pointerize` actions happening within 3-5 turns of a session start.
- Cache hit rate for code slices is 0% (no caching layer exists).

### Scope of This ADR

Canonical code exploration and strategy governance for the **KernelOne context subsystem** and **roles runtime path**. This ADR does NOT cover:

- Session continuity memory (governed by ADR-0045)
- Turn engine streaming semantics (governed by ADR-0042)
- LLM provider selection

---

## Decision

### Core Design: Strategy Framework + Default Profile

Introduce a **KernelOne Context Strategy Framework**. The framework owns strategy contracts, profile resolution, cache/budget receipts, and replay evaluation.

The current canonical exploration lifecycle is retained, but as the default built-in profile rather than the only policy.

The default profile remains a structured policy payload:

```python
EXPLORATION_PHASES = ["MAP", "SEARCH", "SLICE", "EXPAND", "DEEPEN"]

EXPLORATION_POLICY_DEFAULT = {
    "phase": "MAP",                          # current phase
    "exploration_sequence_required": True,   # enforce MAP before SEARCH/SLICE
    "large_file_threshold_lines": 100,
    "slice_required_above_lines": 200,
    "explicit_upgrade_required_above_lines": 2000,
    "budget_gate_threshold_pct": 80,        # trigger compaction at 80% window
    "cache_hot_slice_ttl_seconds": 600,
    "cache_enabled": True,
}
```

### Canonical Exploration Phases

| Phase   | Allowed Actions                                             | Prohibited Actions           |
|---------|------------------------------------------------------------|------------------------------|
| `MAP`   | `repo_read_map`, `repo_read_directory_tree`                | `read_file`, `repo_read_slice` |
| `SEARCH`| `repo_search_files`, `repo_search_symbols`                 | `read_file` (>100 lines)    |
| `SLICE` | `repo_read_slice`, `repo_read_around`                      | Full `read_file` (>200 lines) |
| `EXPAND`| `repo_read_slice` (adjacent), `repo_search_symbols` (caller/callee) | — |
| `DEEPEN`| `read_file` with explicit upgrade flag                     | — |

### Tool Surface Changes

#### Deprecated (in roles.kernel context)
- `read_file` for files >100 lines without explicit flag

#### New Canonical Read Tools
- `repo_read_map`: Returns repo structure summary (always first call in MAP phase)
- `repo_read_slice`: Reads line range `[start, end)` of a file, no full content
- `repo_read_around`: Reads `slice` + N lines of context around each result
- `repo_read_head`: Reads first N lines (for manifest/config discovery)
- `repo_read_tail`: Reads last N lines (for recent logs/error output)

#### Enforcement
- `RoleRuntimeService` resolves the effective strategy profile per turn
- `runtime_executor` in roles.kernel validates tool call sequence against current phase
- Violations produce a warning event and log entry, but do not block execution (enforcement is policy-gated)

### Budget Gate

The context budget gate monitors context window utilization and triggers compaction:

- **Threshold**: 80% of `max_tokens` or `max_chars`
- **Trigger**: Compaction starts proactively, not after exhaustion
- **Method**: LLM-based summary if client available; deterministic heuristic fallback
- **Output**: `compaction_log` with action, tokens saved, method used

### Five-Tier Cache Architecture

```
Tier 1: Session Continuity   (session_continuity pack, long-lived, keyed by session_id)
Tier 2: Repo Map              (directory tree, keyed by workspace hash)
Tier 3: Symbol Index         (search results, keyed by query hash, TTL: 5min)
Tier 4: Hot Slice             (recently accessed slices, keyed by file_path+range, TTL: 10min)
Tier 5: Continuity Projection (role-kernel projection, per-turn)
```

Cache lookup order: Tier 4 → Tier 3 → Tier 2 → Tier 1 → disk.
Cache write: always on Tier 4 after any slice read.

---

## Implementation Summary

### New Capabilities (Owned by `kernelone`)

| Capability ID | Module | Description |
|---|---|---|
| `kernelone.context.strategy_framework` | `polaris/kernelone/context/` | Strategy bundle contracts, registry, profile resolution, receipts, and evaluation harness |
| `kernelone.context.exploration_policy` | `polaris/kernelone/context/exploration_policy.py` | Exploration phase tracking and sequence enforcement |
| `kernelone.context.working_set_assembler` | `polaris/kernelone/context/working_set.py` | Assembles working set from repo map, symbols, slices |
| `kernelone.context.budget_gate` | `polaris/kernelone/context/budget_gate.py` | Context budget monitoring and compaction trigger |
| `kernelone.context.cache_manager` | `polaris/kernelone/context/cache.py` | 5-tier cache management |
| `kernelone.tools.canonical_read` | `polaris/kernelone/tools/contracts.py` | Canonical read tool contracts and runtime-executor-facing tool surface |

### Cell Boundaries

- **Owner**: `kernelone` owns these context strategy capabilities
- **Consumers**: `roles.runtime`, `roles.kernel`, `polaris/cells/roles/director`, `polaris/cells/roles/qa`
- **State Owners**: `kernelone` owns cache state at `workspace/.polaris/runtime/context_cache/`
- **Effects Allowed**: File reads (canonical slice reads), cache writes, event emission

---

## Consequences

### Positive

- Context budget utilization improves: fewer full-file reads means more room for actual tool results.
- Exploration turn sequence is auditable via event log.
- Cache hit rate for repeated slices reduces redundant I/O.
- Budget gate prevents emergency truncation mid-turn.
- Alternative strategies can be benchmarked without rewriting the runtime.
- Canonical behavior is still stable because the default profile remains fixed until promoted.

### Negative / Risks

- Existing agents that use `read_file` as a first call will see new policy warnings until they adapt.
- The 5-tier cache introduces memory pressure; TTL tuning requires production feedback.
- `repo_read_slice` still requires underlying `read_file` implementation — if the base tool is blocked, slice is blocked.

### Open Questions

1. Should the budget gate fire an event or silently compact?
2. What is the appropriate hot-slice TTL for long-running sessions (>2 hours)?
3. Should cache tier 3 (symbol index) be shared across roles or role-isolated?
4. Which score weighting should decide profile promotion in CI or shadow mode?

---

## Verification

Planned test coverage:

- `polaris/cells/roles/kernel/tests/test_canonical_exploration_e2e.py` (6 test cases)
- `polaris/kernelone/context/tests/` (kernelone context suite, existing 2648 tests must not regress)

Fitness rules enforced:
- `CE-001`: `read_file` must not be first call for files >100 lines
- `CE-002`: Full-file read on >2000 lines requires explicit upgrade flag
- `CE-003`: Compaction must not trigger before 80% window utilization

---

## Migration Notes

- The canonical read tools are additive; existing `read_file` remains functional as a fallback.
- Existing sessions continue under legacy policy until explicitly migrated.
- Cache tier 1 (session continuity) is already implemented (ADR-0045) and is reused, not replaced.
- The canonical exploration policy is retained as `canonical_balanced` inside the strategy framework rather than as a permanently hardcoded singleton.
