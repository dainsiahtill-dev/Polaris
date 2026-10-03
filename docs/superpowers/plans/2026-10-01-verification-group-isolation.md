# Verification Group Isolation Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development or superpowers:executing-plans task-by-task. Existing user authorization selects direct implementation and main-agent acceptance; no branch or automatic commit.

**Goal:** Failed candidate verification must not mutate the live workspace or poison later repairs.

**Architecture:** One source-bound disposable snapshot per candidate verification group; sequential commands share it. Existing process/receipt owners retain authority, and physical cancellation/drain precedes disposal.

**Tech Stack:** Python, existing KernelOne KFS/process contracts, bubblewrap, Factory verification, pytest.

**Spec:** src/backend/docs/blueprints/POLARIS_VERIFICATION_GROUP_ISOLATION_20261001.md

## Global Constraints

- Generated Bench targets remain read-only to engineering Agents.
- No new grant, receipt seal, role chain, polling transport or Bench production semantics.
- Only existing public contracts cross Cell boundaries; source/temporary I/O follows KFS.
- Maintain source identity, all real failure diagnostics and exact exit evidence.
- Isolation unavailable/drift/unsafe inputs fail closed; no live fallback.

## Review Focus

- Authored JS versus undeclared compiler outputs: use authority, not extension.
- Build/test/start share one isolated copy; new candidate never reuses prior output.
- Dependency/toolchain symlinks cannot expose writable live project roots.
- Cancellation with a grandchild writer requires terminal drain before disposal.
- Clone success without source hash/owner correspondence is not project success.

### Task 1: Source-bound verification group staging

Files: factory/pipeline/internal/native_validation_sandbox.py or a focused sibling owned by the same Cell; corresponding new owner tests.

Confirmed implementation bucket: new `internal/native_validation_group.py` and
its owner tests; existing Cargo implementation remains unchanged.

Interfaces: `verification_group(workspace: Path, candidate_id: str,
input_hashes: Mapping[str, str], dependency_roots: Mapping[str, Path],
toolchain_roots: Sequence[Path])` yields `VerificationGroup`.
`group.prepare_command(argv: Sequence[str], cwd: str = ".")` returns a prepared
logical/sandbox command and source-input evidence; `assert_inputs_current()`
checks drift. Result recording remains observational, not a receipt seal/verdict.
A caller boolean called `drained` cannot by itself authorize disposal. Until a
real process-owner terminal proof is integrated, issued commands retain staging
as pending; no `TemporaryDirectory` finalizer may silently delete it.

- [x] Write real-command RED proving TS6059 or a failed compiler can leave live derived files today.
- [x] Introduce a group context consuming workspace, candidate identity and authority-derived explicit input paths/hashes; yield prepared command/cwd and input evidence without minting authority.
- [x] Reuse copy/bwrap; reject unsafe paths, unknown stale outputs and unavailable isolation; dependencies/toolchain read-only.
- [x] Run a build/test/start group against the same disposable copy; assert live bytes/paths unchanged and hashes bound.
- [x] Test drift, declared JS, undeclared emitted JS and root/symlink negatives; run original Cargo isolation gates.

### Task 2: Physical cancellation/drain

Files: KernelOne process owner implementation/tests only if existing public cancellation cannot serve Task 1; main review required before expanding this bucket.

Confirmed bucket: `polaris/kernelone/process/process_tree.py`, public
`polaris/kernelone/process/__init__.py`, and `polaris/tests/test_process_tree.py`.
Consume single-use `ProcessTreeRunControl()`/`cancel()` via the public process
package; extend `run_process_tree_safe` with optional `cancel_control` only.
The spawn owner retains process/group identity and determines terminal state;
the caller cannot bind PID or supply drain truth. Parent normal exit while a
grandchild closes its pipes and later writes must still be contained.

- [x] Reproduce cancellation of to_thread while a descendant can still write.
- [x] Reuse or extend a public process-owner cancellation signal; stop whole tree inside the fixed PID namespace and await drain without product polling.
- [x] Test timeout, parent exit, cancellation and grandchild late-write; no alive writer at return/dispose.

### Task 3: Factory integration and truthful results

Files: factory_workspace_quality.py, factory_workspace_quality_impl.py and focused owner integration tests.

- [x] Reproduce the exact candidate-rollback pollution through the actual Factory quality-loop boundary.
- [x] Execute each candidate's command group in staged workspace; preserve raw and logical command evidence.
- [ ] Bind input/candidate hashes to existing public verifier receipts; do not forge seals or use broker internal imports.
- [x] On restore, verify the current restored state; old pre-round command rows remain historical only.
- [ ] Run affected quality/ownership/Cargo/receipt regressions, Ruff/Mypy and independent review.
- [ ] Freeze source, reload only owned isolated instance, retry QA with PM/CE/source assets preserved; inspect every new final request/effect/verifier/settlement.

Full fresh Bench qualification follows actual same-run closure and remains a separate required milestone.

## Current implementation evidence

- Main 229 related tests pass; source group36, input29, real DEO Factory candidate,
  registration-failure and repeated-cancellation cases are included.
- Independent failure/cancel/unproven-drain probes pass. Caller drain flags and
  fake isolation wrappers never authorize cleanup.
- Initial/current-source epoch, candidate epoch and restored epoch use distinct
  groups; source inputs remain immutable, authored JS kept, unknown outputs excluded.
- Existing prepare commands remain a distinct legacy path; raw-thread cancellation
  there remains open. Formal broker's separate live-RW verifier adapter and cold
  owner query side effects remain open; this plan does not claim full-platform sealing.
- Legacy r02 has nine of ten valid source baselines; render.ts's accepted old edit
  lacks matching project-asset registration. Preserve it for owner recovery, do not
  manually edit/reseal. The approved fresh canary is a separate diagnostic milestone.
