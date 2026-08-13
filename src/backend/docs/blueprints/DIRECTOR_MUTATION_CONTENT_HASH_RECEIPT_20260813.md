# Director Mutation Content-Hash Receipt

Status: Verified  
Date: 2026-08-13  
Cells: `roles.adapters` -> `factory.pipeline`

## Problem

An L1-03 stage-local QA repair physically edited `src/models/__init__.py` and
committed an authoritative directed-effect receipt, yet Factory settled the
same task as `workspace_quality_repair_no_mutation`. The physical tool result
carried `file`, `replacements=1`, and a durable effect receipt, but omitted the
content-level `before_sha256` and `after_sha256` fields required by Factory's
no-op-safe mutation predicate.

## Architecture

```text
Provider native edit_file
  -> roles.adapters DirectorToolExecutor
  -> policy-gated physical compare-and-replace
  -> result {path, before_sha256, after_sha256}
  -> directed-effect durable receipt
  -> Factory workspace-quality mutation predicate
  -> same Director task settles completed
  -> failed verifier reruns
```

`roles.adapters` owns physical tool-result construction. `factory.pipeline`
remains a read-only consumer and keeps its strict unequal-content-hash check.
No target-project code, Bench metric, QA assertion, or PM/CE contract is
changed.

## Invariants

1. A dispatched write is not proof of mutation.
2. A successful edit must expose the exact pre/post UTF-8 content hashes.
3. Equal hashes remain a no-op and cannot complete a repair task.
4. The durable directed-effect receipt remains authoritative; hashes enrich
   its physical result rather than create another source of truth.
5. Ordinary implementation failure stays on the exact Director task.

## Verification

- Direct `DirectorToolExecutor.edit_file` regression: exact expected hashes.
- Factory mutation predicate regression: the emitted result is accepted only
  when hashes differ.
- Roles adapter and Factory focused tests, Ruff, Mypy.
- Restart only the isolated L1-03 instance; retry `qa_gate` without rerunning
  PM or Chief Engineer; inspect final request, tool receipt, verifier, TaskRuntime,
  Run Ledger, and Factory terminal state.

## Live closure

The isolated L1-03 QA-only retry recorded three committed `edit_file`
mutations with unequal pre/post hashes. TaskRuntime event seq 163 then settled
the exact TASK-3 repair as
`workspace_quality_repair_mutation_committed`. PM and Chief Engineer were
not rerun. This closes the false no-mutation projection; later verifier
failures belong to repair-target ownership, not receipt settlement.
