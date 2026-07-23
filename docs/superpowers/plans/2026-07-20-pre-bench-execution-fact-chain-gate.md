# Pre-Bench Execution Fact Chain Gate Plan

**Design:**
`docs/superpowers/specs/2026-07-20-pre-bench-execution-fact-chain-gate-design.md`

## 1. Freeze the control-plane snapshot

- [x] Record HEAD, scoped dirty paths, deterministic diff hash, active instance
  registry, reserved main ports, and absence of foreign source writes during
  the gate.
- [x] Confirm DEO-1A through DEO-4 and B3.4-B3.6 closure records agree with the
  current executable source and tests.

## 2. Prove physical dispatch and final-request audit

- [x] Run Factory physical-attempt authority/conservation/replay gates.
- [x] Run roles.kernel final-provider-attempt, request projection, retry,
  fallback, structured and stream gates.
- [x] Prove PM/Architect/Chief Engineer/Director/QA role identity, tool schema,
  tool choice, response format, token/window and coverage enforcement.
- [x] Prove valid 24-hex same-workspace context snapshots and both read APIs.

## 3. Prove runtime and observability readiness

- [x] Run settlement wake bridge, fresh-workspace identity, guarded FS,
  TaskRuntime, Run Ledger, QA and isolated-instance startup gates.
- [x] Verify main remains on `49977/5173`; bench port allocation is isolated and
  no stale bench instance owns the candidate workspace.
- [x] Verify runtime.v2 WebSocket is the live projection path and no polling or
  shared-backend workspace switch is introduced.

## 4. Scheduling decision

- [x] Publish a machine-readable pre-bench card with exact commands, counts,
  snapshot fingerprint, failures and residual risk.
- [x] Set `BENCH_SCHEDULABLE=true` for one run only if every prior item passes;
  otherwise keep Provider/Bench at `not_schedulable` and fix the root cause.

## 5. Fresh isolated acceptance run

- [x] Run the first authorized L1-04 attempt and preserve its exact CE snapshot,
  rejection fact and bench/runtime evidence.
- [x] Close `final_request_role_included_refs_drift` with a five-role
  authoritative audit projection and re-run the complete pre-bench ladder.
- [x] Close `physical_wire_max_tokens_drift` by projecting the Engine-clamped
  final invoke budget across all native protocols without permitting expansion;
  re-run role/provider, TaskRuntime, Context/Instance/FS, Factory control-plane
  and architecture gates.
- [x] Close the independently discovered Factory repair-kernel and Run Ledger
  regressions before authorizing another Provider request.
- [ ] Run one project sequentially with `--launcher-instance-mode isolated` and
  `--bench-session-reporting off`, never using `49977/5173`.
- [ ] Audit every physical role call from its final provider request snapshot.
- [ ] Require real artifact, dependency/environment, build/test/lint and
  entrypoint evidence plus terminal `COMPLETED_VERIFIED`.
- [ ] On failure, emit a defect manifest, repair Polaris, reclose pre-bench and
  only then retry the same project.
