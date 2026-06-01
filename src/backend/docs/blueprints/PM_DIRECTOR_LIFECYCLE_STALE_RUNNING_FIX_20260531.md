# PM/Director Lifecycle Stale Running Fix

Date: 2026-05-31

## Problem

Real PM -> Director runs can leave runtime projections in a confusing state when
the controlling process is interrupted or disappears before the lifecycle file is
updated. The visible symptoms are:

- PM UI reports stale blocking or failed state while a newer run is active.
- `pm.lifecycle.json` can remain `status=running` after its PID is gone.
- execution broker process logs can contain a misleading
  `[execution_broker] terminal ... status=running` line during shutdown.

## Root Cause

The lifecycle projection trusted incomplete runtime artifacts too strongly:

- The PM status reader inferred success from an existing current contract even
  when the persisted lifecycle still said a specific PID was running.
- The execution broker log drain always wrote the final line with the
  `terminal` marker, even when the snapshot was still non-terminal because the
  drain task was cancelled before the process runtime finalized.
- Popen-backed process termination could return after `OSError` without moving
  the handle out of `running`.

## Design

Keep state ownership unchanged:

- `runtime.execution_broker` remains responsible only for process lifecycle and
  UTF-8 logs.
- `orchestration.pm_planning` remains responsible for PM lifecycle projection.

Changes:

- Broker logs only use `terminal` for terminal snapshots; early drain closure is
  recorded as `closed_before_terminal`.
- PM status treats a persisted running lifecycle with a dead PID as failed
  unless a current terminal engine state has already superseded it.
- Popen termination normalizes vanished processes to `cancelled` instead of
  preserving `running`.

## Verification

- Add regression coverage for broker shutdown logs.
- Add regression coverage for orphaned PM lifecycle recovery.
- Run ruff, mypy, and focused pytest for touched backend files.
