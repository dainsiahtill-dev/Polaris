# Factory R36: Director retry event-loop lock ownership

Status: closed  
Bench: not_schedulable after the consumed R38 acceptance exposed a new timeout-budget root  
Scope: Polaris platform only; target-project edits forbidden.

## Observed fact

Fresh isolated L1-04 run `b93c3b89980a` reached PM, Chief Engineer, and
Director physical Provider requests. Director committed `go.mod` through the
authoritative tool/effect-receipt chain, then entered
`empty_write_content_retry`. That local retry made zero Provider calls and
failed with:

`TransactionKernel execution failed: LLM call failed: asyncio.Lock is bound to a different event loop`

Factory run: `factory_8fa89496a1ad`. Director run:
`director-296ead26bd4f`.

## Evidence boundary

- PM final request snapshot: `edf443612b11f1b3207531ec`
- Chief Engineer primary: `b25c581522e5e84b403c34f5`
- Chief Engineer repair: `ea10b673c4d4c16e5e94e1d3`
- Director primary: `aa4ccb5d966a4099437dcd2b`
- All four final requests have correct role identity, tool schema, token/window,
  and required-context coverage.
- Director primary wrote only `go.mod`; TaskBoundary correctly rejected missing
  sibling targets.

Therefore R36 is a local async lifecycle defect, not model quality, context
loss, Provider transport, authorization, or target-project code.

## Required implementation

1. Find the persistent lock shared across independently driven role executions.
2. Keep the Factory lifecycle lock on its construction-time owner loop and
   marshal foreign role-loop cutoff operations back to that loop; never weaken
   mutual exclusion or create per-loop authority locks.
3. Prove two sequential Director attempts on distinct event loops can both
   enter the LLM path.
4. Prove same-loop concurrency remains serialized.
5. Preserve provider-attempt conservation, final-request audit, TaskRuntime
   identity, and tool/effect-receipt facts.

## Deferred roots

- Factory terminal text reports a missing CE blueprint id although the audited
  Director request has `has_chief_engineer_blueprint=true`.
- After R36, re-observe whether multi-file materialization completes. Do not
  mix either correction into this bucket.

## Exit gates

- Focused cross-loop and same-loop concurrency tests pass.
- Changed modules pass Ruff, strict Mypy where configured, and compileall.
- Roles Kernel/Runtime/Adapters regression suites pass in proportion to impact.
- Execution fact-chain and architecture gates remain green.
- Only then may pre-bench authorize one new isolated L1-04 run.

## Implementation evidence

- Exact traceback: `request_preparer._acquire_factory_evidence_binding` called
  `resolve_cutoff_proof`, which attempted the Factory-owned `_run_lock` from the
  Director worker loop.
- `FactoryRoleEvidenceAuthorityPort` now captures the construction-time Factory
  loop and routes foreign-loop `acquire_cutoff` and `resolve_cutoff_proof`
  critical sections through `asyncio.run_coroutine_threadsafe`.
- Cross-loop regression forces the lock to bind to the Factory loop before a
  foreign `asyncio.run` invokes both operations; both execute on the owner loop.
- Authority suite: `135 passed`; related authority/run-service/kernel/physical
  attempt suites: `506 passed`.
- Requalified execution fact chain: `2126 passed, 12 warnings`; full
  architecture: `1411 passed, 8 skipped`.
- Ruff, source Mypy, compileall, Ruff format check, diff check, and post-edit
  CodeGraph structural review: pass.
