# Cognitive Runtime

## Purpose

`factory.cognitive_runtime` is the cross-role authority facade that sits above
`KernelOne State-First Context OS`.

It is responsible for:

- resolving prompt-facing context through canonical `context.engine`
- issuing edit-scope leases
- validating change sets against declared scope
- recording runtime receipts
- exporting typed handoff packs for later continuation
- rehydrating handoff packs back into local `state_first_context_os` overrides
- evaluating rollout readiness through Context OS quality gates

## Hard Boundaries

1. It must not replace `roles.session` as raw transcript owner.
2. It must not replace `context.engine` or `Context OS`.
3. It must not become a second resident-style long-term mind.
4. It must not invent a second truth store for memory or continuity.

## Reuse Rules

- `roles.session` remains the raw truth owner.
- `KernelOne Context OS` remains the working-memory runtime.
- `context.engine` remains the canonical context assembly public facade.
- `factory.pipeline` remains the projection/back-mapping authority.
- `write_gate` and `impact_analyzer` remain the canonical validation primitives.

## Persistence

The current runtime persists governance evidence in SQLite:

- `runtime/cognitive_runtime/cognitive_runtime.sqlite`

This is runtime-owned persistence, not a new business truth domain.

The write path is hardened with:

- single-writer queue discipline
- `WAL` journal mode
- bounded retry for transient `database is locked` conditions

## Not Yet In Scope

- production mainline orchestration wiring
- non-SQLite backend migration
