# Review Regression Closure Implementation Plan

> **For agentic workers:** REQUIRED superpowers:subagent-driven-development. User explicitly requests mutually exclusive parallel experts; no nested agents.

**Goal:** Close six review findings through native dynamic evidence and preserve verification/ownership/drain safety.
**Architecture:** Correct existing Factory native validation and lifecycle orchestration; no new execution path, Cell, truth source or target-project repair.
**Tech Stack:** Python >=3.10 API floor, actual3.12.3, bwrap, Cargo/Rustup, npm, NATS, pytest.
**Spec:** `src/backend/docs/blueprints/POLARIS_REVIEW_REGRESSION_CLOSURE_20261004.md`

## Global Constraints

- Main dirty work preserved; no branches/worktrees/commits, resets or evidence deletion.
- Only exact mutually exclusive scopes below; source/test expansion needs root ruling.
- No generated targets, live runtime/registry/service operations, credentials/config changes, paid Provider/Bench or nested dispatch by workers.
- Every shell starts RTK; CodeGraph MCP first, returned source not reread; UTF-8 explicit; terse reports.
- All subprocess/native tests in private PID/network/tmp/KernelOne homes; ordinary host target roots remain untouched.
- No fake receipts/IDs, lowered gates, writable host cache/source mounts, guessed cleanup, blind PM/CE restart or network fallback.
- A preserves existing Session method signatures; B only consumes them. Root owns shared docs and final integration.
- Full affected components + strict relevant gates first; do not silently call an incomplete/time-limited global sweep passed.

## Review Focus

- Symlink retarget or executable replacement after preparation remains rejected (A).
- Python relative imports and Rustup homes work without exposing host PYTHONPATH/cache writes (A).
- Preparation cannot authenticate foreign or unreceipted source mutations; dependency drift after freeze stays denied (B).
- Unproven drain stops heartbeat but does not roll back, dispose or settle; repeated cancellation preserves ownership (B).
- Owned exit, slow external INFO, simultaneous readiness/exit and repeated cancel remain distinct (C).

### Task 1: Native Python/Rust invocation and environment

mode=implementation. Modify only:
- `src/backend/polaris/cells/factory/pipeline/internal/native_validation_group.py`
- `src/backend/polaris/cells/factory/pipeline/internal/native_validation_session.py`
- Existing `test_native_validation_group.py`, `test_native_validation_session.py`, `test_native_validation_execution.py`, `test_native_validation_inputs.py`, `test_workspace_quality_pythonpath.py` in Factory tests when needed.
- New `src/backend/polaris/cells/factory/pipeline/tests/test_native_validation_language_regressions.py`.
Own artifacts: task-1-report.md/json, task-1-design-vc.md, task-1-only.diff.

Interfaces: preserve `Session.candidate(pending=...,results=...)`, before_repair,
restored and run_command. Optional toolchain/env binding additions stay internal,
backward-compatible and checked; no cross-bucket writer.

- [ ] Read current code and existing Cargo sandbox policy completely; locate configured executable/toolchain without credential dumps or downloads.
- [ ] RED real group `python src/main.py` importing `src.engine`; real Rustup cargo test with dependency-free fixture. Independently require exit0/literal output, not guessed argv success.
  ```python
  python_command = ["python", "src/main.py"]
  cargo_command = ["cargo", "test", "--quiet"]
  # Native result must execute the source, not a mock or host fallback.
  ```
- [ ] Implement separate invocation/resolved identity, sandbox-relative Python imports, declared Rust homes/toolchain mounts using existing isolation policy.
- [ ] Native GREEN plus alias/executable drift, mount/environment denial and no source/host-cache effects. Run full native validation affected suites, Ruff/format/Mypy; report source hashes and exact residuals.

### Task 2: Candidate preparation order and uncertain-drain heartbeat

mode=implementation. Modify only:
- `src/backend/polaris/cells/factory/pipeline/internal/factory_workspace_quality_impl.py`
- `src/backend/polaris/cells/factory/pipeline/tests/test_workspace_quality_group_integration.py`
- `src/backend/polaris/cells/factory/pipeline/tests/test_workspace_quality_repair_timeout_policy.py`
- New `src/backend/polaris/cells/factory/pipeline/tests/test_quality_review_dependency_heartbeat.py`.
Own artifacts: task-2-report.md/json, task-2-design-vc.md, task-2-only.diff.

Interfaces: consume Session unchanged. Authenticated candidate source remains
distinct from dependency witnesses and preparation; existing physical drain and
repair heartbeat helper remain owner-bound.

- [ ] RED native repair preparation twice changes dependency metadata and wrongly raises verification_dependency_drift before actual candidate validation.
- [ ] RED actual quality wrapper with ProcessTreeDrainError after claim leaves heartbeat renewing; track native heartbeat task termination and unchanged rollback/disposal/settlement.
  ```python
  # After the quality wrapper exits with its original drain error:
  assert heartbeat_task.done()
  assert heartbeat_stop.is_set()
  # Candidate source and staging must remain retained; no settlement fabricated.
  ```
- [ ] Complete authorized dependency preparation before fresh candidate group freeze, preserving independent source authentication. Stop/await pending heartbeat separately under uncertainty, without permitting cleanup or masking original error.
- [ ] GREEN actual wrapper/repair path; source-tamper/drift-after-freeze/repeated-cancel negatives. Run full relevant quality-loop components and static gates; report current hashes and limits.

### Task 3: Owned NATS exit while readiness probe waits

mode=implementation. Modify only:
- `src/backend/polaris/infrastructure/messaging/nats/server_runtime.py`
- `src/backend/polaris/tests/test_managed_nats_startup.py`
- `src/backend/polaris/tests/test_nats_server_runtime.py` if needed.
- New `src/backend/polaris/tests/test_nats_owned_exit_race.py`.
Own artifacts: task-3-report.md/json, task-3-design-vc.md, task-3-only.diff.

Interfaces: preserve ensure/shutdown facade and existing shared-service ownership;
only exact owned process may be watched/drained. No shared-service or KFS edits.

- [ ] Reproduce existing `test_owned_early_exit_is_reported_before_startup_deadline`, plus a real child exiting while native greeting waits.
- [ ] Race readiness with owned exit under one total startup budget. Cancel/await losing monitors and retain physical process drain semantics.
- [ ] Verify early exit code, pending-probe cancellation, simultaneous ready/exit policy, slow external non-replacement and repeated cancel/TERM-ignoring child. Run startup/shared/client/lifespan suites and static gates, preserve prior B fixes.

### Task 4: Native procfs process-disappearance race

mode=implementation. Necessary newly reproduced shared blocker, not scope creep.
Modify only `src/backend/polaris/kernelone/process/process_tree.py`, existing
`src/backend/polaris/tests/test_process_tree.py`, and new
`src/backend/polaris/tests/test_process_tree_proc_race.py`. Own task-4-* artifacts.
Pre-D archive preserves current dirty bytes before correction.

Interfaces: keep process runner/control/drain signatures and owner authority.
No changes to NativeValidationGroup/Session, Factory or NATS writer files.

- [ ] Dynamically reproduce `_owned_group_running` scanning a just-disappeared
  procfs entry: FileNotFoundError is already skipped but ProcessLookupError is
  incorrectly escalated. Native A/B traces independently captured this.
- [ ] RED controlled procfs ESRCH on a physically exited child while another
  actual owned group member remains; require continued scan and live-member true.
  ```python
  # Disappeared procfs entry is not inability to observe a remaining live owner.
  assert owned_group_running is True  # other owned writer remains observable
  ```
- [ ] Handle only departed-process FileNotFoundError/ProcessLookupError at the
  per-entry read boundary. Keep permission, malformed stat and other OS failures
  fail-closed. Group absence alone still never replaces leader reap/pipe drain.
- [ ] Native GREEN plus live member, permission/error, fast-exit and cancellation
  owner-proof negatives; full process tree suite, strict relevant static gates,
  exact task-only hashes/diff. No global OSError swallowing or fake drained flag.

### Task 5: One serial final integration-fix wave

mode=implementation, no sibling writer. Final review FINAL-I1/I2/I3 and current
release failure are handled together; initial review/report/gate failures preserved.

Allowed existing sources: native_validation_group.py, native_validation_session.py,
native_validation_sandbox.py, factory_workspace_quality_impl.py (only if needed),
nats/shared_service_runtime.py, and pyproject.toml (conditional TOML runtime dependency).
Allowed tests: existing A/B language/session/quality tests, existing shared NATS
lifecycle tests, new test_final_review_regressions.py. Own task-5-* artifacts.
Root must approve any new generic KernelOne directory primitive before editing it;
first reuse existing KFS public capability, never create fake files to pass a gate.

- [ ] RED real installed Rustup `stable`/canonical selector and missing/unavailable
  negative; resolve offline installed identity, not default-compiler fallback.
- [ ] RED actual repair with initially absent node_modules created by preparation;
  re-discover only platform-admitted roots while keeping source authentication,
  explicit denial and per-group post-freeze drift guards.
- [ ] Close relevant Python >=3.10 TOML import path in both native verifier modules.
  Use shared checked compatibility and declare tomli only for python<3.11 if needed;
  no version-floor increase or fake parser. Real tomli may be installed ONLY in
  an owned temporary validation directory, never global environment or app config.
- [ ] RED unchanged KFS release guard: actual hit is `_open_log` append `os.fdopen`,
  not store mkdir. Route guarded append handles through neutral KFS capability;
  route fresh store creation through real KernelOne filesystem capability without
  loosening store identity/migration/symlink/fence checks or changing allowlists.
- [ ] GREEN targeted native/compatibility/boundary negatives, full affected suites,
  Ruff/format/Mypy, exact final-wave-only diff/hashes. Preserve older unchanged
  assertions and clearly label unavailable native Python3.10/Windows evidence.
- [ ] Root performs one scoped final-fix rereview, then refreezes/retests/release.

### Task 6: Root acceptance and qualification

- [ ] Preserve pre-dispatch bytes and HEAD/dirty state; verify mutual scope and interface compatibility.
- [ ] Independent spec+quality review per task; original implementer fixes review findings, root does not bypass review with source edits.
- [ ] Personally rerun real Python/Cargo/native lifecycle and combined affected suites; record broader timeouts distinctly.
- [ ] Whole task-set integration review, frozen source hashes and KernelOne release gate; no unrelated baseline rewrites.
- [ ] Update engineering defect manifest, master progress and memory. Only then one authorized fresh isolated full-budget project; local proof is not COMPLETED_VERIFIED/all120.
