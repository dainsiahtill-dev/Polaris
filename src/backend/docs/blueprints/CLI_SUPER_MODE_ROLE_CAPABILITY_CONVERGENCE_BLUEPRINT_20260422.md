# CLI SUPER Mode Role Capability Convergence Blueprint (2026-04-22)

**Status**: Planned for immediate implementation  
**Scope**: `polaris/delivery/cli/**` + `polaris/cells/roles/kernel/**`

## 1. Objective

Eliminate the SUPER-mode dead loop where a code-change request is routed to `pm -> director`,
but the `pm` turn inherits `MATERIALIZE_CHANGES` and is then forced into an impossible
`read -> declare modification_plan -> write` loop even though PM is a read-only role.

## 2. Root Cause

The loop is structural, not model-random:

1. SUPER routes a code-delivery request to `pm` first.
2. The PM turn still carries the original mutation intent, so `resolve_delivery_mode()`
   returns `MATERIALIZE_CHANGES`.
3. PM has no write tools by profile design.
4. Mutation guards, continuation prompts, and retry enforcement then keep demanding write
   progress from a role that cannot write.

This creates an impossible state machine:

```text
SUPER request
  -> PM turn (read-only role)
    -> delivery mode = MATERIALIZE_CHANGES
    -> read/explore allowed first
    -> next turn requires write / modification_plan
    -> PM still has no write tools
    -> retry / continuation loops
```

## 3. Architecture Decision

Apply a two-layer fix:

### Layer A: Delivery-layer role-stage contract

SUPER must explicitly wrap read-only planning stages with an analyze-only override:

- `pm`, `architect`, `chief_engineer`, `qa` in SUPER orchestration:
  - force `[mode:analyze]`
  - add explicit planning-only instructions
  - preserve original user request as payload, not as raw mutation contract

- `director` execution handoff:
  - force `[mode:materialize]`
  - pass original request + PM output in structured handoff text

### Layer B: Kernel capability correction

If a turn resolves to `MATERIALIZE_CHANGES` but the exposed tool definitions contain no
write tool, the transaction kernel must downgrade the delivery contract before guard logic
starts:

- `MATERIALIZE_CHANGES` + no write tools -> `PROPOSE_PATCH`

Reason:

1. The role can still analyze and propose a plan.
2. Mutation guards must never demand impossible actions from a read-only role.
3. Direct `--role pm` / `--role architect` mutation requests should degrade safely instead
   of looping.

## 4. Module Changes

### `polaris/delivery/cli/super_mode.py`

Add helper(s) to build planning-stage readonly messages and explicit execution handoff
messages with mode markers.

### `polaris/delivery/cli/terminal_console.py`

Update `_run_super_turn()` so:

1. readonly stages use the SUPER planning wrapper
2. director handoff uses explicit materialize wrapper

### `polaris/cells/roles/kernel/internal/turn_transaction_controller.py`

Add role-capability correction:

1. inspect available tool definitions
2. if no write tool exists, coerce `MATERIALIZE_CHANGES -> PROPOSE_PATCH`
3. emit anomaly/audit flags for observability

## 5. Validation Plan

1. SUPER code-delivery request:
   - PM receives `[mode:analyze]`
   - Director receives `[mode:materialize]` handoff
2. Controller downgrade:
   - mutation request with read-only tool set becomes `PROPOSE_PATCH`
   - no mutation retry/write enforcement is triggered
3. Existing SUPER routing tests keep passing.

## 6. Expected Outcome

After the fix:

1. SUPER `pm -> director` becomes:
   - PM plans with read-only tools
   - Director executes with write tools
2. Direct read-only roles no longer dead-loop on mutation requests.
3. Mutation guards stay strict for writer roles, but become capability-aware for read-only roles.
