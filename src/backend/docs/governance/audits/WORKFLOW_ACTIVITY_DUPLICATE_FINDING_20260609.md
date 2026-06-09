# Finding: `workflow_activity` is a non-production duplicate of `workflow_runtime`

- **Date**: 2026-06-09
- **Status**: Open — recommend a dedicated, equivalence-verified consolidation wave
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

1. Decide the canonical owner of director/pm workflow *definitions* (`workflow_activity`
   vs `workflow_runtime`).
2. Diff the two implementations symbol-by-symbol; pin behavioral equivalence with tests
   before collapsing either copy.
3. Collapse the loser into a thin re-export of the canonical cell (no deletion), and
   move the 2 cross-internal tests onto the canonical public surface.
4. Remove the misleading docstring/string-literal references that made `workflow_activity`
   look imported.
