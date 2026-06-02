# ADR-0088: PM, ChiefEngineer, and Director Task-Market Routing

Status: Accepted
Date: 2026-06-03

## Context

Polaris supports two legitimate execution routes:

1. PM can publish simple, already-scoped work directly to Director.
2. PM can publish complex work to ChiefEngineer first, then Director executes after blueprint evidence is complete.

The current migration path has this capability split across rollout modes. Mainline routes everything to `pending_design`, while shadow publishes directly to `pending_exec`. This makes the architecture look mutually exclusive and weakens UI/E2E evidence for the complex full-chain flow.

## Decision

Use `runtime.task_market` as the single business broker for both routes.

PM dispatch records a per-task route:

- `direct_to_director` publishes to `pending_exec`.
- `chief_blueprint_required` publishes to `pending_design`.

ChiefEngineer only owns design evidence. It claims `pending_design`, persists a blueprint, and advances the same task to `pending_exec` with blueprint metadata. Director workers only claim `pending_exec`.

## Consequences

- PM direct and Chief-mediated routes can coexist without competing state owners.
- Complex E2E scenarios can require all blueprints before Director execution.
- Director UI must read task-market execution rows in addition to legacy runtime projection.
- Inline `mainline-full` remains a compatibility path, but it must preserve the same stage and metadata contract.

## Verification

The routing contract is verified by PM dispatch tests, ChiefEngineer consumer tests, Director task visibility tests, and the full-chain Electron audit.
