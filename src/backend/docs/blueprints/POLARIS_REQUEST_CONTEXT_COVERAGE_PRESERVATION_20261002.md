# Request-context coverage must survive Factory authority binding

Status: locally verified; exact-run recovery still failed at a separate Director residual.

## Exact-run evidence

Factory `factory_8edf12521449`, L1-01 r02, Director TASK-3 call
`2f76fe861af84efd8209151abd4778ec`, snapshot
`f39c9923decb8d59114eaf91`: the original error event records
`missing_required_refs=[actual_sibling_exports]`. Backend rejected the dependency
snapshot because TASK-2 had no committed receipts for its declared entrypoint
artifacts. The persisted snapshot instead reports missing refs empty and coverage
PASS after the cutoff binder replaces request-specific requirements with basic
role slots. No physical Provider request was issued for this rejected call.

TASK-2 call `0926de18062b4703b61f76295a2aab72` was independently rejected for
`task_write_tool_scope_mismatch`. These are two causal failures, not seventeen
independent ReceiptStore failures. Current r02 reached quality_gate after later
Director recovery; historical events must remain intact.

## Narrow architecture

```
final request audit
  immutable observed required/included/missing refs
  Factory cutoff binding: authoritative role slots unchanged
  request-context residuals: observed missing refs outside all cutoff slots
  combined context-quality failure
  existing qualification gate / snapshot / event consumers
```

Owner: roles.kernel. Reuse existing audit binder and qualification gate. No public
execution contract, state owner, grant, cross-Cell import or effect is added.
Do not promote heuristic observations into cutoff authority; do not drop mandatory
context merely because basic role slots passed. Input audit remains unchanged.

## Assumptions and verification

- Confirmed: generic enforcement rejected r02 before Provider transport; this
  report does not claim a proven live bypass.
- Confirmed: basic role-slot coverage cannot prove actual sibling source bodies.
- Risk: retaining missing PM/CE heuristic findings would incorrectly reject valid
  cutoff-bound authority. Subtract every cutoff-owned slot, not just required ones.
- Regression: valid cutoff plus missing actual_sibling_exports must preserve that
  exact missing ref, produce an error finding and fail context qualification.
- Regression: satisfied context remains green; cutoff-controlled generic absences
  remain superseded; repeated binding is stable and never mutates the input.
- Run focused projection/binding/cutoff suites, Ruff, Mypy; full repository and
  fresh Bench remain separate acceptance milestones.

Generated target code stays read-only. Do not clear historical failures or edit
old content-addressed snapshots.

## Verification result

366 relevant tests pass in14.04s. Missing optional quality objects and already-bound
copies both preserve mandatory contextual rejection. Rebinding reconstructs those
residuals from retained observed refs instead of trusting the authority marker;
normal rebinding remains idempotent. Ruff/diff pass; full repository unverified.

Same-run Director recovery preserved PM/CE and source hashes at restart, but a new
read-only/finalization residual prevented completion. This is not fresh Bench PASS.
