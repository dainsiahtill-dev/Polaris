# factory.verification_guard

## Owner boundary

This Cell owns two deliberately separate capabilities:

1. `verify_completion(VerifyCompletionCommandV1)` runs a caller-requested,
   allow-listed single-claim verifier. It is not project-completion authority.
2. `query_project_completion_diagnostics(QueryProjectCompletionDiagnosticsV1)`
   returns owner-bound residual diagnostics for one exact CE completion
   contract. The query contains only `(workspace, project_id, run_id,
   completion_contract_hash)`.

The project diagnostic path never accepts caller evidence, coverage status, or
source-tool authorization. During bootstrap,
`polaris.bootstrap.project_completion_diagnostics_owner` binds an observation
port that re-reads public owner projections from:

- `chief_engineer.blueprint`: immutable completion contract and exact
  `owner_task_id` per active obligation;
- `runtime.task_runtime`: task state for the exact Factory run;
- `control_plane.run_ledger`: TaskBoundary/verifier prerequisites plus the
  committed current JobToken set used to authorize physical dispatch;
- `runtime.execution_broker`: owner-sealed artifact and command receipts bound
  to exact input bytes and physical process results;
- `director.runtime`: read-only coverage plus plan-probe evidence, with no
  caller-selected source tool.

The same-Cell authority layer validates exact types and identities, then seals
an immutable `ProjectCompletionOwnerEvidenceBundleV1`. The deterministic
evaluator accepts only that sealed bundle. Evidence hashes bind workspace,
project, run, contract, obligation, owner task, owner module, status, receipt,
and exit code. `dataclasses.replace` cannot retag the sealed bundle or result.

## Fail-closed invariants

- Active obligations without a CE-authored `owner_task_id` are invalid.
- Missing evidence and failed evidence remain disjoint.
- Artifact and entrypoint obligations cannot pass without one current-run
  owner-sealed receipt bound to workspace, project, run, contract, owner task,
  obligation, path, and current artifact hash.
- Verification cannot pass without one typed receipt additionally bound to the
  exact modality, canonical argv/cwd, CE command-authority hash, input artifact
  hash, committed JobToken/policy hash, exit code, timeout state, and output
  hash. Artifact changes invalidate an earlier command receipt.
- `RunProjectCompletionEvidenceCommandV1` contains identity only. Caller cannot
  supply argv, evidence, status, runner, or verdict. Entrypoint obligations need
  a real typed `entrypoint` probe; absent API remains fail-closed.
- TaskRuntime completion, TaskBoundary `completed_verified`, disk existence,
  arbitrary evidence refs, and Run Ledger gate summaries are auxiliary facts;
  none can replace that receipt. No receipt means `missing`; a present nonzero
  or timed-out physical receipt means `failed`.
- Repair is schedulable only when `director.runtime` coverage and plan-probe
  produce one executable runtime source tool.
- Diagnostic dependencies must reference this result and form a full DAG.
- Diagnostics are residuals, not a competing project completion verdict.

## Verification

```bash
python -m pytest -q polaris/cells/factory/verification_guard/tests
python -m pytest -q polaris/tests/unit/bootstrap/test_project_completion_diagnostics_owner.py
```
