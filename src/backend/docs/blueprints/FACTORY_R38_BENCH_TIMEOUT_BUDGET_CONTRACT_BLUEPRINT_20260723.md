# Factory R38: Bench timeout budget contract

Status: closed  
Bench: exactly one fresh isolated L1-04 R39 authorized  
Scope: Polaris internal bench launch contract only; production deadline policy and target projects unchanged.

## Observed fact

Fresh isolated L1-04 run `cd224e7d6557` used the repository-standard command
with a `540s` Factory timeout. PM completed, then the Chief Engineer physical
request failed after exactly `188s` with `provider_stream_timeout:188s`.

The request itself was qualified:

- PM final physical snapshot: `1fd90a2e08c6fd9d9a8fe1c0`
- CE final physical snapshot: `403e6d605c6946e235e24fe7`
- role identities correct; PM contract and ten CE target files present
- missing required refs/tools: none
- PM request: `2596 / 262144` tokens
- CE request: `4889 / 262144` tokens
- Provider route: `kimi-for-coding` through the Anthropic-compatible endpoint

## Root

The production deadline policy correctly conserved the absolute Factory
deadline. For the three serial PM tasks it reserved `310s` for Director waves,
QA, settlement, and safety. After PM, only `498s` remained, so the requested
`600s` CE timeout was capped to `188s`.

The platform runner already defaults to `5400s`. The defect was the repository
Agent standard overriding that safe default with `--timeout 540` and an outer
`600s` process timeout. This converted a full acceptance run into a short smoke
while still presenting it as a runnable/COMPLETED_VERIFIED attempt.

## Fix

- Restore the full acceptance standard to `--timeout 5400`.
- Restore the outer runner guard to `6000s`.
- Synchronize AGENTS.md and CLAUDE.md.
- Keep short budgets legal only for explicit startup/failure-path smoke; they
  cannot support a runnable or COMPLETED_VERIFIED claim.
- Do not weaken Factory deadline conservation or silently extend a caller's
  explicit production deadline.

## Deferred audit findings

- CE failure projection lost provider/model metadata even though the final
  physical snapshot proves both values.
- ContextOS projected `control_plane.isolated=false` because prompt content
  contained `factory_run_id`; determine whether this is a false-positive token
  matcher or a real control-plane leak in a separate bucket.

## Exit gates

- AGENTS.md and CLAUDE.md carry the same full-acceptance budget.
- Four focused deadline-conservation tests pass.
- YAML/UTF-8/synchronized-contract and diff checks pass.
- Pre-bench authorizes exactly one R39 isolated L1-04; that authorization is
  consumed as soon as the runner starts.
