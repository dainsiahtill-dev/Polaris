# runtime.execution_broker

## Purpose

`runtime.execution_broker` is the cell-layer unified execution gateway.
All runtime subprocess/thread/offload submissions in business cells should
route through this cell instead of calling `subprocess`/thread primitives
directly.

## Boundary

- Owns process launch/wait/terminate/cancel orchestration at cell layer.
- Owns log stream draining into UTF-8 text logs.
- Reuses `polaris.kernelone.runtime.execution_facade` as technical substrate.
- Does not own business task state (`runtime.state_owner` still owns writes).

## Public Surface

- `polaris.cells.runtime.execution_broker.public.contracts`
- `polaris.cells.runtime.execution_broker.public.service`

## Rules

1. All text log writes must remain explicit UTF-8.
2. All subprocess launches must include deterministic metadata.
3. Callers should pass workspace in command metadata for auditability.
