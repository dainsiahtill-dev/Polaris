# Workflow QA blocked evidence closure

Status: closed  
Bench: frozen pending pre-bench source qualification; this gate repair does not authorize Provider or Bench calls.

## Fact

The WorkflowRuntime suite cannot collect because
`test_qa_blocked_activity.py` imports `record_qa_blocked`, but the activity was
never implemented. The test was committed independently of the runtime
activity. The live QA workflow skips directly to the cognitive receipt when
Director is non-completed, so `runtime/results/integration_qa.result.json` is
also absent for that path.

## Contract

- Add one registered `record_qa_blocked` activity in the owning runtime.
- Write UTF-8 JSON through the existing artifact-store path helper.
- Project `ran=false`, `passed=null`, reason, blocked stage, failure reason,
  project/run/workspace identity, and timestamp.
- A missing workspace is an explicit successful no-op because there is no safe
  artifact authority to address.
- Invoke it from the QA workflow's Director-non-completed branch before the
  cognitive receipt; expose the artifact path in QA evidence.
- Do not mark QA passed, hide the Director failure, or invent verifier success.

## Exit

- Existing four activity regressions pass.
- QA workflow tests, WorkflowRuntime full suite, Ruff, format, mypy, compileall,
  and architecture gates pass.

## Closure evidence

- Blocked-QA focused regressions: `4 passed`.
- WorkflowRuntime full: `215 passed`.
- Factory Pipeline full: `1232 passed, 2 warnings`.
- Architecture full: `1411 passed, 8 skipped`.
- Ruff, format, mypy, compileall, and `git diff --check`: pass.
- No Provider request, Bench attempt, QA-success fabrication, or target-project
  edit was made while closing this bucket.
