# Factory R42: CE thinking-only bounded schema recovery

Status: closed  
Bench: R42 itself grants no Provider authority; pre-bench must authorize the next run  
Scope: Factory Chief Engineer portfolio-result classification and the existing bounded schema-repair path.

## Observed fact

Fresh isolated L1-04 R41 produced a qualified physical Chief Engineer request
with readable final-request snapshots and complete required PM/target evidence.
The provider returned a thinking-only response and no usable portfolio JSON.
The role result was:

`model returned thinking-only response; awaiting user clarification`

Factory recorded `chief_engineer.llm_review_failed`, generated `0/3`
blueprints, and stopped before Director.

## Root

Factory already has one separately claimed, deadline-readmitted, bounded Chief
Engineer schema-repair path. It is entered only when
`error_code == output_validation_failed`. A thinking-only/no-visible-output
result is the same portfolio-contract failure class, but it bypasses that path
and becomes fatal immediately.

The older `_ce_llm_failure_allows_blueprint_projection` classifier is dead
code after the project-portfolio migration: it has no call sites. Its unit test
therefore proves only a token predicate, not real stage recovery.

## Fix contract

- Reuse the existing separately claimed CE schema-repair operation; do not add
  an ungoverned retry loop or offline mainline blueprint bypass.
- Admit exactly `output_validation_failed` and typed/worded no-visible-output
  failures (`thinking-only`, empty response, no visible output, awaiting user
  clarification) into that one bounded repair.
- Keep timeouts, rate limits, circuit-open, genuine design rejection, invalid
  authority, missing final-request evidence, and a failed second response
  fail-closed.
- Preserve the primary failure evidence, snapshot ref, exact error class, and
  SHA-256/size of any excluded prior output in the repair request.
- Set the repair failure class truthfully (`thinking_only_response` versus
  `output_validation_failed`) instead of relabeling all failures.
- Remove the dead blueprint-projection classifier and replace its predicate-only
  test with stage-level regression evidence.
- Do not modify target-project code.

## Exit gates

- R41-shaped thinking-only primary result causes exactly two CE calls: primary
  plus one separately claimed schema repair; a valid repair reaches portfolio
  and task blueprint generation.
- A second thinking-only result stops after two calls and remains failed.
- A genuine non-output CE failure remains fatal after one call.
- Existing schema-repair, authority-cardinality, deadline, settlement, final
  request evidence, Factory full, static, and architecture tests pass.
- R42 closes before the separate R42B ContextOS prompt-hygiene bucket opens.

## Closure evidence

- Focused thinking-only classifier/recovery regressions: pass.
- Factory stage characterization: `261 passed`.
- Factory Pipeline full: `1237 passed, 2 warnings`.
- Architecture: `1411 passed, 8 skipped`.
- Ruff, format, mypy, compileall, and diff check: pass.
- Provider requests / Bench runs used for closure: `0 / 0`.
