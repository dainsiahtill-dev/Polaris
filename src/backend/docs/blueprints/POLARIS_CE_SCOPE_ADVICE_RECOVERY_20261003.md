# CE advisory scope recovery and current-candidate consistency

## Intended outcome

Recover already authored CE plans without another full planning call, while keeping
PM authority, strict schema/semantic validation, hashes and target immutability.
This is a bounded existing-flow fix under the approved unattended master plan.
No branch/commit or additional design approval; no target-project writes.

## Exact dynamic root

Fresh r05 `factory_b43737206ceb` retained candidate eec2bbe... with authored TASK-2
and TASK-3 beside task_plans. Native dictionary decode preserves that candidate hash;
the missing-member merge used an empty base and does not invent the nesting.
Production normalization aborts in `_normalize_task_plan_array_field` because TASK-1
scope_for_apply contains a mapping. The offered schema allows structured advice and
the canonical scope parser accepts path/file advice. One-variable memory-only probe
allowing that mapping lets the existing transactional normalizer recover all three
authored plans. Original candidate file bytes remain unchanged.

There is also a candidate-carrier risk: successful structural recovery updates
structured_output/tool arguments, but an earlier carried base candidate and hash may
remain stale. A subsequent repair must consume the normalized current candidate,
not prefer the carried pre-recovery tree.

## Design and rejected alternatives

Accept mapping leaves for scope_for_apply as already allowed for structured risk
advice; deep-copy/preserve their bytes. Do not interpret them as commands or grants.
Keep behavior_invariant_refs string-only, ambiguity/conflict rollback unchanged.
After full-schema structural recovery, update an existing carried base candidate/hash
atomically to the recovered tree; retain source/recovered hashes in recovery evidence.

Do not require all advisory task plans: an empty task_plans object is intentionally
supported. Do not fabricate missing plans, loosen semantic patch owner/scope checks,
rebind an old physical patch to a new live hash, or accept an empty delivery fallback.
Offline counterfactual patch replay is not Provider or fresh completion evidence.

## Ownership and gates

Existing public CE normalizer; Factory consumes it through the existing public seam.
No public API, graph dependency, state owner or effect permissions change.
Modify `_semantic_repair.py` and Factory `_mixin_02.py`, with focused normalizer/flow
regressions. Test supported mapping and item-wrapper preservation, string-only refs,
destination conflicts, unknown task rejection, current carried hash, input immutability
and empty-advice compatibility. Run CE public semantics and Factory repair suites,
Ruff/Mypy, exact retained-candidate read-only replay and final release before fresh.
