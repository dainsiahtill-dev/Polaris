# delivery.cli

## Purpose

Canonical delivery cell for CLI command execution. Provides structured contracts
(`ExecuteCliCommandV1`, `CommandResultV1`, `CommandErrorV1`) and the
`CliExecutionService` that dispatches commands based on their execution mode.

## Kind

`capability`

## Public Inputs

- `ExecuteCliCommandV1` — primary command contract
- `QueryCliStatusV1` — status query

## Public Outputs

- `CommandResultV1` — unified result
- `CliCommandStartedEventV1` — execution started event
- `CliCommandCompletedEventV1` — execution completed event

## Architecture

```
Host (polaris-cli, pm_cli.py, cli_thin.py, director_service.py)
  → CliExecutionService.execute_command(command)
       │
       ├─ ExecutionMode.MANAGEMENT
       │    → registered handler (e.g. pm.status)
       │
       ├─ ExecutionMode.ROLE_EXECUTION
       │    → RoleRuntimeService facade
       │        → RoleExecutionKernel [tool loop]
       │
       └─ ExecutionMode.DAEMON (placeholder)
```

**Key constraint**: ROLE_EXECUTION commands MUST route through
`RoleRuntimeService.execute_role_session()`. The host layer must NOT
implement its own tool loop.

**Streaming constraint (hard gate)**: Any CLI/TUI chat streaming path MUST use
`RoleRuntimeService.stream_chat_turn()` and MUST NOT call
`polaris.cells.llm.dialogue.public.service.generate_role_response_streaming`
directly from host/delivery code.

The review gate is enforced by
`tests/test_director_console_host.py::test_director_console_host_constructor_exposes_runtime_service_only`.

**Product direction**: `polaris-cli` is the canonical unified host:
one host, multi-role, multi-mode. Role-specific TUI surfaces under
`roles.runtime/internal` are frozen legacy test windows only.

## Execution Modes

| Mode | Handler | LLM? | Tool loop? |
|------|---------|-------|-----------|
| MANAGEMENT | registered handler | No | No |
| ROLE_EXECUTION | RoleRuntimeService facade | Yes | RoleExecutionKernel |
| DAEMON | placeholder | TBD | TBD |

## Command Types

- `pm.*` — Project management commands (init, status, requirement_*, task_*)
- `director.*` — Director commands (run, serve, task, worker, console)
- `architect.*` — Architect commands (analyze, design)
- `chief_engineer.*` — Chief engineer commands (analysis, task)

## Depends On

- `roles.runtime` — for ROLE_EXECUTION mode delegation

## State Ownership

- None (stateless)

## Effects Allowed

- `fs.read:workspace/*` — read workspace files for command execution
- `fs.write:workspace/.polaris/*` — write runtime state
- `role.execute:*` — delegate to role runtime for ROLE_EXECUTION commands

## Invariants

- MANAGEMENT commands must not import or invoke LLM providers directly
- ROLE_EXECUTION commands must delegate to RoleRuntimeService facade
- DAEMON commands must not block the caller

## Read Order for AI

1. `cell.yaml`
2. `generated/context.pack.json`
3. `public/contracts.py`
4. `public/service.py`

## Verification

- Unit tests in `tests/cells/delivery/cli/` (to be created)
- Integration tests against actual `pm_cli.py` / `cli_thin.py` (to be wired)
