# CE effective completion obligation view implementation plan

> **For agentic workers:** Use superpowers:subagent-driven-development. User's
> approved master plan overrides repeated approval, branch/worktree and commit
> defaults: work in mutually exclusive dirty-main buckets; do not commit.

**Goal:** Remove the r07 raw-versus-final obligation vocabulary split so CE
repairs can correct denied references before dispatch, with unchanged authority.

**Architecture:** A pure CE-owned effective-obligation projection reuses final
PM scope/topology normalization, reports exclusions, and feeds provider context,
composer and early/final reference checks. Factory consumes a public CE result;
it does not duplicate artifact authority or import CE internals.

**Tech Stack:** Existing Python/KernelOne/Cell public contracts, pytest/Ruff/Mypy.

**Spec:** src/backend/docs/blueprints/POLARIS_CE_EFFECTIVE_OBLIGATION_VIEW_20261003.md

## Global constraints

- All120 catalog projects fresh isolated/unattended remains the goal; no unit
  or same-run recovery result can qualify it.
- No engineering target/runtime edits, Provider/model/config changes, benchmark
  threshold changes, scope expansion, fake receipts or implicit behavior removal.
- Preserve raw candidate/PM hashes, original task ownership and final feasibility.
- Optional/not_applicable retained IDs can be references, not required-depth proof.
- Every shell command RTK; CodeGraph first; apply_patch; explicit UTF-8.
- Preserve prior dirty work; no branches/worktrees/commits/service restarts.
- One implementer bucket, one independent review; no nested subagents.

## Review focus

1. Unauthorized root-config IDs are denied BEFORE expensive semantic repair;
   corrected behavior may reference real retained IDs but cannot gain permissions.
2. Optional/not_applicable legitimate identities remain usable without counting
   as required source/test depth.
3. Missing PM artifacts projected by the final normalizer have the same IDs/owners
   in the early view; ID collisions and ambiguous ownership remain rejected.
4. Newly upserted legal artifacts are reprojected from the patched candidate;
   stale pre-patch view cannot reject valid fresh IDs or admit denied ones.
5. No persistence/catalog re-enrollment/command grant is created by a purported
   pure observation helper; actual final command-authority validation remains.

### Task1: Shared effective view and end-to-end consumers

**Files:**
- Create one focused CE internal pure projection module only if needed; otherwise
  factor existing CE normalization without growing an unbounded helper directory.
- Modify CE public/service/_portfolio.py and _semantic_repair.py.
- Modify CE public/service/__init__.py and public/__init__.py only to expose the
  single projection capability needed by Factory; no parallel success facade.
- Modify factory_ce_evidence.py and factory_stage_executor/_mixin_01.py;
  _mixin_02.py for authenticated authority propagation to context/composer.
- Modify CE semantic repair contracts/_semantic_repair.py only if existing typed
  diagnostics lack the required exclusion/reference classification.
- New focused CE/Factory component tests; do not change old assertions/fixtures.

**Interfaces:**
Consume existing ChiefEngineerPortfolioTaskV1 tuple, raw immutable candidate,
validated PM scopes/topology and existing normalization semantics. Produce one
CE public read-only effective view containing retained artifact rows/IDs,
full retained completion IDs, excluded IDs with path/owner/reason, and a stable
basis binding. Exact type/name chosen against current existing contracts before
writing; record the seam. No synthetic execution/carrier authority.

- [ ] Read current CE normalizer, repair context/composer, early Factory validator
  and final feasibility; identify actual shared pure artifact projection seam.
- [ ] Add genuine REDs against current public provider context and actual early
  validator: root-denied OBL-3 must not be in allowed IDs or accepted references.
- [ ] Factor/reuse authority normalization once; do not copy an approximate PM
  path predicate into Factory or call the persistent portfolio builder as a query.
- [ ] Bind provider allowlist and composer closure to the effective view; report
  excluded IDs/reasons. Keep illegal existing refs as an explicit typed diagnosis,
  not silent deletion or a grant. Reproject after legitimate artifact upserts.
- [ ] Route the early Factory validator through the CE public view; keep the
  separate required-depth authority filter for coverage metrics.
- [ ] Align final artifact normalization with the same CE-owned projection. If
  entrypoint/verifier IDs depend on additional authority, reuse the actual final
  semantics or explicitly reject missing required proof; do not claim raw IDs are
  the complete effective roster without proving it.
- [ ] Run actual consumers with hand-derived expectations:

```python
# Literal expectations, not a helper computing both sides.
assert "OBL-3" not in context["allowed_completion_obligation_ids"]
assert "OBL-7" in context["allowed_completion_obligation_ids"]
assert excluded_by_id["OBL-3"]["path"] == "tsconfig.test.json"
assert rejected_behavior_patch.status != "accepted"
assert corrected_existing_behavior.statement == authored_replacement_statement
assert pm_tasks_after == pm_tasks_before
```

- [ ] Test legitimate required/optional/not_applicable identity retention; no
  unauthorized root config, source-role spoof, collision, ambiguous owner or
  stale-view acceptance. Test real final normalization consistency, not mocks.
- [ ] Verify existing semantic-repair, portfolio behavior/depth and Factory CE
  characterization/candidate recovery suites; Ruff/format/Mypy/diff.
- [ ] Independent spec/quality review; resume implementer for concrete findings.

### Task2: Generic current-instruction delivery and budget preservation

Actual private native runtime found current instruction9629chars clipped to4014,
breaking authoritative patch JSON. Read task-2-context-brief.md for exactscope,
dynamictrace and constraints. Source bucket roles.kernel security.py/gateway.py/
compression_engine.py, separate from Task1. No CE marker exception or cap increase.

- [ ] Trace active cutter and RED at actual current-message consumer/finalwire.
- [ ] Security-classify full current input, reserve from actualbudget, compress
  history separately; unfit current rejects beforeProvider.
- [ ] Anchor current by request lineage before any historical tool fallback,
  not messages[-1] role or find-last-user heuristic. Empty/None/whitespace request
  cannot promote historical user. Preserve tool evidence/history semantics.
- [ ] Keep injection classification/escaping, historical limits, system reservation
  and final native full-request window/schema guard.
- [ ] Fit/oversized current with history tools, tightbudget, malicious tail and
  actualprivatewire tests; exact authorized fixture-only mock corrections.
- [ ] Independent spec/quality review and scoped fixes; no fresh proof inferred.

### Task3: Dynamic acceptance, current release and fresh qualification

- [ ] Main independently reruns focused consumers and exact r07 readonly repro
  adapted to the new view; archive command/exits and final-request audit proof.
- [ ] Current release gate and source freeze; no new paid attempt before closure.
- [ ] Start next fresh isolated L1-01 with5400s/6000s budgets and current bound
  MiniMax. Preserve old PM/CE failure evidence, denominator and sequential order.
- [ ] Audit every final request, real effects/verifier/TaskRuntime/QA/settlement;
  failures generate new exact-run records, not a model-ceiling assumption.

No completion claim until actual fresh delivery and all catalog/N-batch proof.
