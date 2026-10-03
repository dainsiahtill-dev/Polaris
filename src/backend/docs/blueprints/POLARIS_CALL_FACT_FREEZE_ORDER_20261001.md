# Freeze call facts after tool/budget planning

Status: live budget parity verified; project delivery remains incomplete.

## Evidence

Factory factory_9aa169804636 physical refdc5ff2c5096c5b1ac480f322 sends128000
output tokens. Tool planning uses _apply_director_first_call_output_budget, whose
default admitted ceiling is7000. TransactionTurnExecutor constructs the kernel
before build_transaction_invocation_setup; create_transaction_kernel deep-copies
request facts at creation. Its LLM closure consequently consumes stale policy.
This proves a policy propagation defect, not the entire660-second latency cause.

## Change and boundaries

```
bound attempt check
early directed-effect composition/freshness validation (no provider dependency)
ContextOS + tool-surface + budget planning
tool conflict check
create TransactionKernel (fresh authority recheck + final fact snapshot)
existing semantic freeze / qualification / physical dispatch / receipts
```

Reuse _resolve_directed_effect_composition. Do not defer the first authorization
check until after context preparation, accept a caller-supplied prevalidated
grant, reread mutable authority after semantic freeze, or change budget/QA rules.
Both streaming and non-streaming paths require the same order. No public API,
Cell/state owner/effect/realtime rail changes; same roles.kernel owner.

## Verification

- Real invocation setup and kernel factory with missing synthetic targets;
  capture at the actual LLM boundary, run real request preparation, compare
  final max_tokens with a literal governed ceiling. No model/tool side effects.
- Missing/stale DEO blocks before context assembly/provider dependency.
- Authority that becomes invalid during context preparation is rechecked.
- Existing identity, wiring, request-facts, temperature-floor and DEO gates;
  Ruff/Mypy, affected module gates, then exact same-run Director retry.
- Fresh isolated Bench remains separate. Do not count diagnostic replies or
  local tests as an unattended delivered project.

## Acceptance evidence

Actual setup/factory/request-preparer regression RED128000 versus planned7000;
after fix both modes prepare/native-project7000. Early missing authority and
authority closed during ContextOS both reject before provider dependency.
Latest related suite229PASS; Ruff/MypyPASS; cascade9/9PASS.

Live sourcee737db0262c3fa47 startref6fa21fa5f79b84a8ff565fb2 has actual semantic
max7000, guidance and exact two-target scope. Primary returned110s, then retry
max8000. Final physical ref043db38efda113f17d1e7405 is auditPASS/max8000, but
fails with reasoning truncated / finish_reason=length / zero tools. No source
effects or project completion. Next work is governed phase reasoning policy,
not another unqualified request-size or timeout increase.
