# Owner-bound settle repairs

Status: locally verified; release/module gates running; fresh proof pending.
Part of approved unattended master plan P04/P07/P08.
Success remains all120 catalog projects fresh isolated, no engineering intervention,
required artifact/environment/verifier/entrypoint/QA/settlement proof.

## Proven cause

Exact factory_7244a26d3ae3 wrote7 artifacts but failed TASK-1 boundary on
tests/verify.test.mjs. A later materialization settle created that path at
2026-10-01T21:51:18Z; CE declared tests/verify.test.ts. The post-settle scanner
seeing the new file does not contradict the earlier missing-file boundary.
Read-only real-producer replay still proves the generic authorization defect:
CE-owned=false, new JobToken.write-path=true, capability_audit.ok=true.

The Factory adds planner paths and generic extras before minting a fresh token,
and can manufacture a CE marker when blueprint text is missing. A plan proposes
an effect; it is not the authority to allow it. A helper attempt also loses the
original completion slice, so its effects cannot complete the original obligation.

## Architecture

```text
Exact diagnostics
  existing Factory causal owner discovery and canonical task claim
  strict chief_engineer.blueprint public handoff validation
  original capability + task completion slice + admitted envelope intersection
  existing Director runtime planning (no writes) / DEO commit
  physical revalidation
  execution_broker artifact registration
  TaskRuntime owner settlement
  canonical boundary re-evaluation; downstream dispatch only when authorized
```

No new Cell, token authority, success arbiter or Bench gate. Factory only
coordinates. director.runtime plans; roles.adapters commits under policy;
execution_broker owns artifacts; TaskRuntime owns the lease and terminal outcome.

## Decisions

1. Commit context requires an original repair_task. Missing/invalid/cross-run
   handoff or projection rejects before commit. Do not fabricate CE evidence.
2. Reuse the validated capability unchanged. Rebuild only existing Director
   profile/envelope projection through public tasking, preserving admitted
   ceilings; candidate paths never expand it.
3. Forward repair paths must fit current allowed write scope. Out-of-scope
   proposal is an explicit repair-authority residual, not a new grant.
4. Stage settle claims the existing diagnostic owner rather than a random
   factory-director-mat-settle helper. Planning receives that task. A successful
   physical effect refreshes the original completion assets before close;
   failed verifier never becomes completed_verified.
5. No manual target modifications, PM/CE regeneration, fake tests, threshold
   changes, provider/model changes, or broad unrelated source edits.

## Verification / pre-mortem

- Negative missing owner, CE rejection, run/task mismatch, token substitution,
  unsafe/foreign paths, explicit empty/narrow scope.
- Positive same owner preserves token bytes and registers effects to its own
  contract; input objects unchanged; no helper owner even in later waves.
- Asset registration failure must prevent successful terminal settlement.
- Build-prefix detector3RED/GREEN already locally verified; include it in gates.
- Same-run engineering recovery and fresh autonomous qualification remain distinct.
- Do not authorize a known build-output path merely because a planner proposed it.
- Existing unsupported repair source tools stay fail-closed; do not patch regex.

## Current implementation and limitations

Strict CE owner, original immutable token and completion projection now drive
commit context. Root and metadata admission constrain the same public tasking
projection. Stage claims original owner and renews its lease while planning;
renewal rejection prevents effects, cancellation stops the keeper without
inventing a drain/terminal receipt. Each successful effect refreshes assets before
any later candidate or verifier; late rejection retains physical/asset counts.
Asset record failure prevents successful close. 50 owner/stage tests PASS;328
related PASS/88.06s; Mypy4, Ruff/diff PASS. Real r03 strict-owner read-only replay
accepts owned package through the actual DEO capability receiver and rejects
unowned mjs; no Provider or target edit performed during these probes.

Independent review found root-admission robustness and late-effect-registration
issues; both received RED/GREEN fixes. Full command-only execution, cancellation
drain and fresh qualification were not reviewed/proven by these local tests.
Narrower admission currently rejects a token/envelope binding mismatch. This
deliberately avoids expansion; supporting a lawful narrower derived capability
is an open contract obligation, not grounds for silently broadening permission.

## r06 continuation design

The original scope guard remains authoritative. Global diagnostic planning can
return a candidate belonging to a different canonical CE task between two owned
candidates. Known foreign candidates must be deferred to their original owner,
not converted to permissions and not allowed to terminate remaining owned work.
Unknown, ambiguous and unsafe ownership remains a hard contract blocker.

After current-owner effects and truthful settlement, Factory uses existing
TaskRuntime claim and CE handoff APIs for the other original owner, then invokes
the pure planner again against current bytes/diagnostics. Old plans/hashes are
never transplanted between owners. Required evidence, dependency blocks, shared
deadline, heartbeat/cancellation and no-progress limits still apply. Prior failed
owners must be revalidated before canonical stage convergence; retained receipts
do not themselves authorize a completed verdict.
