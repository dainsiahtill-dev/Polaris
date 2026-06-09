# Finding: `workflow_activity` is a non-production duplicate of `workflow_runtime`

- **Date**: 2026-06-09 (deep-dive added 2026-06-10)
- **Status**: Open — dedicated wave required; a forced merge is empirically unsafe (see deep-dive)
- **Severity**: Low (internal duplication; no runtime impact, no user-facing effect)
- **Origin**: Resident Engineer availability audit, gap G5
- **Related blueprint**: `docs/blueprints/RESIDENT_AUTONOMY_ROUND2_BLUEPRINT_20260609.md`

## Verified facts

`polaris/cells/orchestration/workflow_activity` (4,279 LoC) has **zero production
importers**. The two references that look like imports are not:

- `polaris/domain/entities/workflow.py` — only a **docstring mention**
  (`- workflow_activity Cell`, `- polaris.cells.orchestration.workflow_activity.internal.models`),
  no `import` / `from` statement.
- `polaris/cells/orchestration/workflow_runtime/internal/runtime_engine/activities/director_activities.py:384`
  — a **string literal** in a mapping: `"workflow_activity": "workflow_runtime.director_execution"`,
  not an import.

The only real importers are **2 test files** (which live under `workflow_runtime/tests/`,
a cross-cell-internal test reach that is itself a smell):

- `polaris/cells/orchestration/workflow_runtime/tests/test_pm_workflow_timeouts.py`
- `polaris/cells/orchestration/workflow_runtime/tests/test_director_activities.py`

Both currently pass (11 tests green as of this finding).

The live workflow engine is `workflow_runtime` (15,663 LoC). The two cells have
**diverged** (≈3.6× line difference) — `workflow_activity` is not a byte-for-byte
copy, so any merge needs per-symbol equivalence verification, not a blind redirect.

## Deep-dive: divergence map (2026-06-10)

The two cells are **not** a clean copy — they have substantially diverged. Forcing a
re-export/merge would silently change `workflow_activity`'s behavior to
`workflow_runtime`'s.

Per-file divergence (`diff` changed-line count, activity vs runtime LoC):

| file | activity | runtime | changed lines |
|---|---|---|---|
| `pm_workflow.py` | 471 | 596 | 193 |
| `director_workflow.py` | 535 | 537 | 62 |
| `director_task_workflow.py` | 653 | 577 | 282 |
| `qa_workflow.py` | 168 | 175 | 155 |

The 2 parity tests (`workflow_runtime/tests/{test_pm_workflow_timeouts,test_director_activities}.py`)
only pin **4 private timeout-policy functions** as output-equivalent across the cells.
Byte-identity check of those 4:

| function | module | byte-identical? |
|---|---|---|
| `_director_child_workflow_timeout_seconds` | `pm_workflow` | ✅ identical |
| `_task_phase_timeout_seconds` | `director_task_workflow` | ✅ identical |
| `_task_run_timeout_seconds` | `director_workflow` | ✅ identical |
| `_task_dependencies` | `director_workflow` | ❌ different source, same tested output |

So even the "equivalent" subset is not uniformly mechanical: 3/4 are byte-identical, 1 has
divergent source. There ARE enforced boundary gates
(`polaris/tests/architecture/governance/test_semantic_boundary.py`,
`docs/governance/ci/scripts/check_semantic_boundary.py`) that a cross-cell-internal
re-export shim could trip. `workflow_runtime` is the live engine.

**Conclusion**: a behavior-preserving, ACGA-clean full consolidation is a genuine
dedicated-wave task, not a mechanical dedup, and must not be forced inline.

## Why no code change in this round

1. **Risk/value asymmetry.** A 4k-LoC workflow-engine consolidation touches code the
   live `workflow_runtime` engine path and its tests depend on; the only benefit is
   internal de-duplication with no user-facing value.
2. **Ambiguous architectural intent.** `workflow_activity` self-describes as
   "Owns Activity/Workflow definitions and registry implementations" — it may have been
   intended as the canonical *definitions* cell that `workflow_runtime` should reuse,
   yet `workflow_runtime` carries its own `director_workflow` / `pm_workflow`. Until that
   intent is decided, even a deprecation marker could mislabel the canonical source.
3. **Debt resolution ≠ deletion.** Per standing guidance, this debt should be resolved by
   reconciling/consolidating, not by deleting — and that requires the intent decision first.

To avoid descriptor-hash / governance-gate churn, **no `workflow_activity` source was
modified**; this note is the deliverable.

## Recommended dedicated wave

1. **Safe first step (low risk):** extract the 3 byte-identical timeout helpers
   (`_director_child_workflow_timeout_seconds`, `_task_phase_timeout_seconds`,
   `_task_run_timeout_seconds`) into a shared `kernelone` timeout-policy module both cells
   may import (kernelone is the shared base — no cross-cell rule violation). Behavior is
   provably unchanged. Reconcile `_task_dependencies` (divergent source) explicitly first.
2. Decide the canonical owner of director/pm workflow *definitions* (`workflow_runtime` is
   the live engine and the natural canonical).
3. Diff the two implementations symbol-by-symbol; pin behavioral equivalence with tests
   for each before collapsing — the 193/282/155-line divergences are real behavior, not
   formatting.
4. Collapse the non-canonical copy into a thin re-export of the canonical **public**
   surface (no deletion); expose the timeout policy publicly so the 2 cross-internal tests
   can move onto the public surface (removing the ACGA-smell internal reach).
5. Remove the misleading docstring/string-literal references that make `workflow_activity`
   look imported.
