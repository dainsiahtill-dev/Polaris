# Unattended execution: separate task intent from assembled context

Status: local implementation and independent review verified; live recovery pending.

## Objective and evidence

The full goal remains fresh, high-quality, unattended L1-L12 development and
N-batch convergence. This change closes one execution invariant, not that goal.
Never manually modify generated targets, weaken QA, invent receipts, or restart
valid PM/CE for a Director-local code residual.

Exact r03 `factory_7244a26d3ae3`, Director call
`61612a068b354100b95bfdcaab9d1014`, physical snapshot
`93d4d4f6c611a0b6a6b02969`: readonly tools followed by a tools-empty FINALIZATION
request, then `director_no_materialized_changes`. PM/CE hashes stayed unchanged.
Read-only replay of the actual request demonstrates:

- The task requires delivery of source/build files.
- The assembled task message includes the platform clause “Do not remove existing
  scripts, aliases, or compiler/linter options unless the task explicitly asks.”
- Production delivery_intent_resolver returns mutation=false / analyze_only.
- The lower-level regex classifier returns DEVOPS / true. That is not the callback
  injected into ToolBatchExecutor: the Controller injects its production resolver.
- Task metadata phase=requirements also explains why the fresh-only mode pin did
  not protect this recovery. Phase provenance must not substitute for task intent.

The negation detector is behaving on the wrong input. Do not delete the protection
clause, weaken global negation checks, add domain-specific regex, or force every
Director turn to mutate.

## Existing-owner architecture

```
PM task goal/subject (unchanged) + strict CE handoff
  roles.adapters: pure task_instruction in existing platform_tool_contract
  roles.kernel: current-user-only task-instruction extraction
  delivery resolution / mutation admission / retry / finalization
  independent scope + JobToken + DEO effect authority (unchanged)
  real verifier / immutable artifact receipt / TaskRuntime / Factory settlement
```

TaskContractBuilder already supports task_instruction/instruction as distinct from
assembled prompt text. Reuse that field; do not add a second intent authority,
capability token, runtime state owner, provider route, or Cell.

## Contracts and constraints

1. The adapter derives instruction from current authored task goal, falling back
   to subject/description only when needed, before appending language/generic
   protective guidance. Preserve task input bytes/metadata and PM/CE hashes.
2. The extractor considers only the latest user message's explicit tool contract;
   older tasks and tool output cannot supply current intent. Absent instruction
   preserves ordinary natural-language classification. Malformed instruction is
   an explicit contract error, not coercion or silent fallback.
3. A current leading explicit delivery marker retains its existing precedence.
   Embedded history or quoted mode examples are not new authority.
4. Intent classification consumes the pure instruction consistently; target/file
   extraction still uses admitted scope and existing target inventories. Intent
   is not permission; JobToken, envelope, path policy and DEO remain authoritative.
5. Bounded read bootstrap can gather exact contents, but mandatory mutation cannot
   turn into successful readonly finalization. Existing retry/continuation budgets
   remain bounded and failures stay at the same Director task.
6. Retry narrowing preserves current task-instruction metadata alongside existing
   safe write guidance, never raw control history or previous-task instructions.
7. Genuine read-only tasks remain read-only. Already verified deliveries may be
   reused through the existing verified-scope path, not forced rewriting.

## Minimal execution plan

1. RED/GREEN receiver: current-only extraction; assemble+preserve-clause fixture;
   true readonly, malformed input, explicit mode, old-task/tool imitation negatives.
2. Route intent consumers through that boundary without changing file/command
   authorization. Exercise readonly batch rejection under a mutation contract and
   allow legitimate readonly completion. Preserve retry metadata.
3. RED/GREEN sender: Director message builder projects task_instruction into
   existing tool contract while full protective prompt and write boundaries remain.
4. Owner suites, Ruff/Mypy, independent review; freeze source; restart only owned
   r03; retry Director on the same run; audit final request/effects/verifier/settle.
5. Only after exact completion run fresh qualification; then advance projects
   sequentially under the unchanged full L1-L12 goal.

## Pre-mortem

- Do not accidentally classify a previous task or source-file string as current
  instruction; only current message metadata may supply the projection.
- Do not turn every allowed write scope into mandatory mutation.
- Do not strip original user negation or change capability boundaries.
- Do not conflate local fixtures, same-run recovery and fresh completion.
- Unknown live residuals get exact attribution before another paid attempt.

## Local implementation checkpoint

- Initial sender/receiver regressions: nine RED, one existing readonly PASS;
  ten GREEN after implementation.
- Actual ToolBatchExecutor readonly admission regression RED (no exception),
  then GREEN: mutation-required task rejects the read-only decision before
  tool effects or tools-empty finalization.
- Independent review found explicit readonly could be lost inline or upgraded
  by secondary intent, and whitespace/case role normalization could lift an
  earlier task during retry. Added RED cases, shared leading-mode interpretation,
  readonly precedence at sync/hybrid/write-only/stream boundaries, and consistent
  current-user normalization. Negative controls now pass.
- Real r03 snapshot replay, using current authored task8 goal: before mutation
  false, after true / materialize_changes; full platform protection retained;
  no Provider call or target mutation during replay.
- 511 related component tests PASS in15.03s; Mypy10 production files PASS;
  Ruff/diff PASS. Full repository and fresh completion unverified.
- PDB identified an unrelated cold test failure: the sequential fake patched
  an old façade after file splitting rather than its current _core owner. Fixture
  patch corrected; original assertions unchanged. A pre-existing stream typing
  mismatch was aligned with its decoder-validated mapping compatibility.

Source is frozen only after final review; next is owned Director-only r03 recovery,
not a new PM/CE run or an unattended completion claim.

Final narrow independent review:24 PASS/2.57s, both Important findings closed.
Additional318 handoff/receipt/adapter setup tests PASS/122.01s; two existing SWIG
deprecation warnings retained. Affected M02/M03/M04/M06 gates are being rerun.

## Live retry revealed another required-context boundary

Source1bc41eeacc1447d5, same r03 Director retry: readonly batch now correctly
enters the mutation retry and reuses original reads rather than finalizing.
The bootstrap follow-up builder independently drops system write guidance;
call e7a6838764ca4af383567f5b07e6a0ad/refb92cac2d35ab83fa17b3b009 then fails
preProvider qualification with task_write_guidance_not_projected. This is not
failure of the pure-intent classifier and not a new model-quality claim.

Ruling: legitimate required guidance must be bound at the final request assembler,
as sibling evidence already is, rather than relying on every retry/history builder.
Reuse KernelOne project_task_write_guidance plus existing final audit. Only a
supplied four-field guide matching current scope can be pinned. Missing, malformed,
control-contaminated or divergent guidance remains rejected; do not reconstruct a
grant or invent artifact facts. Insert before the current user turn, preserve
input objects and final-user invariant. New real-preparer RED/GREEN regression
and malformed/scope negatives must pass before another paid attempt.

Final assembler review checkpoint: compare all four guidance fields, not just
write_targets. Missing/substituted reference-only targets remain rejected.
Expected guidance uses current targets plus project_declared_target_files, matching
the adapter producer; request-fact projection now preserves that inventory. The
inventory supplies references only and cannot enlarge the admitted write scope.
Six real-preparer/projection and negative regressions pass. Related suites:303
PASS/9.57s; Mypy two production files PASS; Ruff/diff PASS. Historical split-owner,
missing-fixture and duplicate-definition test failures repaired without removing
unique assertions or weakening production guards. Independent read-only review
CLEAR. Same-run final-pin live recovery and fresh qualification remain unverified.
