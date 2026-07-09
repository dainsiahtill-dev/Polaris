# Execution Control Plane Barrier Plan (2026-07-10)

## Scope

This plan closes the L1-01 r06-r11 control-plane gap without reopening TS repair,
WS2 read-model work, or legacy Director helpers.

## Blind-Spot Findings

1. Factory timeout cancellation can suspend TaskRuntime leases while a Director
   turn already decoded native tool calls and is entering dispatch. The resulting
   write failures surface as `session_not_active`.
2. A decoded tool batch with only failed tool results must never be projected as
   a successful completion. It is an execution-control-plane failure.
3. TaskBoundary entrypoint validation treats `package.json` build outputs such
   as `dist/main.js` as missing source targets. Build artifacts are verifier
   outputs, not source obligations.
4. Bench attribution needs to keep upstream task-boundary failures distinct from
   downstream QA/test symptoms. Missing downstream tests after an upstream
   compile/cancel failure are not evidence that the model cannot write tests.

## Design Invariants

- `decision_received -> tool_dispatch -> effect_receipt -> task_boundary` is the
  authoritative execution chain.
- Once a run has entered the tool-dispatch settlement window, Factory timeout is
  allowed to return a timeout projection, but must not immediately invalidate the
  TaskRuntime session that tools need to settle.
- Build output prefixes (`dist/`, `build/`, `out/`, `.next/`, `.nuxt/`,
  `coverage/`) are generated artifacts. Missing files in those prefixes must not
  trigger `missing_entrypoint_target` at task-boundary time.
- Bench failure taxonomy must prefer structured TaskBoundary and repair
  convergence facts over generic `integration_qa`, `delivery_depth`, or
  `runtime_environment:event_wait_timeout` symptoms.

## Implementation Plan

1. Gate generated package entrypoints in TaskBoundary.
2. Preserve/verify tool-batch all-failed fail-closed behavior.
3. Add Factory timeout barrier metadata and avoid session suspension when the
   timeout path is a tool-settlement barrier.
4. Strengthen bench taxonomy with structured `session_not_active`,
   `tool_dispatch_failed`, `repair_convergence`, and `task_boundary`
   attribution.
5. Validate with targeted unit tests, `py_compile`, Ruff, and a lightweight
   taxonomy smoke script where optional factory dependencies are unavailable.

## Implemented Decisions

1. TaskBoundary now treats generated package entrypoints under `dist/`,
   `build/`, `out/`, `.next/`, `.nuxt/`, and `coverage/` as verifier/build
   outputs rather than source-target obligations.
2. Director timeout settlement now checks the terminal run status first when a
   factory cancel signal is present. If the child run is still non-terminal and
   TaskRuntime reports active execution, the stage returns a barrier projection
   with `inflight_run_continues=true` and `cancel_signal_sent=false` instead of
   suspending the child lease.
3. Bench taxonomy now recognizes `session_not_active` and
   all-failed tool batches as `control_plane` failures before generic runtime or
   LLM-output classification.

## Verification Notes

- `PYTHONPATH=src/backend rtk pytest src/backend/polaris/cells/control_plane/run_ledger/tests/test_task_boundary.py -q`
  passed with 28 tests.
- `PYTHONPATH=src/backend rtk pytest src/backend/polaris/cells/roles/kernel/internal/transaction/tests/test_tool_batch_executor_metadata.py -q`
  passed with 11 tests.
- Ruff check, Ruff format check, `py_compile`, and `git diff --check` passed for
  the touched files.
- `src/backend/polaris/cells/factory/pipeline/tests/test_bench_gates.py` and
  `test_factory_stage_executor_characterization.py` are still collection-blocked
  in this local environment by missing optional `numpy` from the benchmark
  holographic import chain. That is an environment/dependency isolation debt,
  not an execution-control-plane code failure, and should be handled in a
  separate bucket.
