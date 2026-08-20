# ADR-0110: Preserve CE owner authority across terminal TaskRuntime drain

Status: Accepted  
Date: 2026-08-20  
Owner: `factory.pipeline` with `runtime.task_runtime` public contracts

## Context

Factory terminal drain freezes TaskRuntime projection, then removes live task
rows. A same-run QA retry must reclaim the exact Director task that owns a
failing source file. PM contracts can be intentionally coarse while Chief
Engineer blueprints expand concrete topology and bind it to a JobToken.

Live run `factory_ec5697b14a71` proved the mismatch: PM TASK-1/TASK-2 each named
only `requirements.txt`, while immutable CE handoffs assigned
`src/dream_subway/domain.py` to TASK-1 and
`src/dream_subway/line_editor.py` to TASK-2. After drain, restoration rebuilt
rows only from PM paths and failed closed with
`workspace_quality_repair_canonical_owner_missing` before any provider or tool
call.

## Decision

Terminal QA repair restoration reuses the same-run immutable CE blueprint and
its run-bound JobToken as owner authority. The bridge must:

1. require an exact task id and Factory run id match;
2. require the CE review row to be generated and handoff-ready;
3. require a readable blueprint with a run-bound JobToken;
4. project CE target files, blueprint identity, and JobToken into the restored
   TaskRuntime row before claiming repair;
5. fail closed when any identity or authority check fails.

The bridge does not infer ownership from disk, diagnostics, or project-wide
target inventories. It does not create a new helper task or a second state
owner. `runtime.task_runtime` remains the writer; Factory only supplies frozen
same-run authority through existing public services.

## Consequences

- QA-only retry survives destructive terminal drain without restarting PM/CE.
- Concrete CE topology remains authoritative over coarse PM placeholders.
- Missing/tampered blueprint or cross-run JobToken still blocks repair.
- Terminal projection may remain a compact read model; immutable CE artifacts
  supply the topology it intentionally does not duplicate.

## Verification

- Regression: frozen rows + coarse PM targets + exact CE JobToken restore the
  correct owner and reject the non-owner.
- Negative tests: mismatched run, non-ready handoff, or missing token stays
  `workspace_quality_repair_canonical_owner_missing`.
- Existing Factory workspace-quality characterization suite, Ruff, Mypy, and
  same-run isolated QA retry must pass.
