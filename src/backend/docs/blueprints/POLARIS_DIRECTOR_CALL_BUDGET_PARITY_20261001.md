# Director call-budget parity — 2026-10-01

Status: implementation candidate; not Factory qualification.

## Dynamic evidence

MiMo same-run `factory_9aa169804636` empty-write repair recorded
`director_forced_write_output_budget.max_tokens=7000` in snapshot
`e355885c0a91234b241fe5a7`. Its pinned physical request
`9acd248ae352ac04f4e84d6f` sent `max_tokens=128000` and timed out at 120s.
The timeout is the existing forced-write retry policy, not the quality-loop
timeout or proof of provider unavailability.

## Owner and data flow

```text
roles.adapters: bounded per-call context (7000)
  -> director.tasking.public: apply task strategy (128000 ceiling)
  -> roles.kernel: request fact projection -> canonical preparer
  -> infrastructure: OpenAI-compatible payload -> physical dispatch
```

`apply_execution_strategy_overrides` currently replaces both call-scoped output
aliases with the larger strategy default. The structured adapter evidence is
left intact, falsely describing a bound no longer enforced.

## Minimal repair

Tasking strategy application must intersect valid explicit call-level output
budgets with its strategy ceiling, using KernelOne's existing output-key list.
Publish the effective value in context and metadata, without mutating the
immutable profile/strategy, task contract, envelope or authorization. Task
budgets are ceilings, not promises of output size. Invalid values are ignored;
absent bounds retain current strategy behavior. A smaller explicit value must
never be raised by a default/floor at this boundary.

No new public facade, provider-specific branch, grant, timer, QA relaxation or
Bench-target edit. Timeout and settlement reserve policy remain unchanged.

## Verification

First reproduce RED through the real adapter preparation, strategy application,
fact projection, canonical LLM preparer and native payload builder. Cover main,
empty-write and no-write stages; streaming/non-streaming; lower, higher and
invalid call budgets. Then run owner suites, affected budget/DEO gates, Ruff and
Mypy. Same-run Director recovery is separate live evidence; a later fresh
isolated completion remains required.

## Residuals

Correctly enforcing 7000 does not prove the declared multi-file workload can
finish within that cap or a 120s retry. Budget feasibility and continuation
must be checked after parity, rather than increasing timeouts blindly. The
historical epoch's 1800s metadata also needs reconciliation against the runner's
5400s request. Neither question is closed by this patch.
