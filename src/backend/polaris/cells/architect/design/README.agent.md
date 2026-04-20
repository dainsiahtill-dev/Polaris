# Architect Design Cell

## Objective
Own architecture design generation for complex tasks, producing auditable design
artifacts and implementation guidance that downstream execution cells can
consume.

## Boundaries
- Owns architect runtime internals in `internal/**`.
- Exposes only contracts in `public/contracts.py`.
- Does not directly own Director task execution or workspace code writes.

## State Ownership
- `runtime/state/architect/*`

## Allowed Effects
- `fs.read:workspace/**`
- `fs.write:runtime/state/architect/*`
- `fs.write:runtime/events/runtime.events.jsonl`
- `llm.invoke:architect/*`

## Public Contracts
- `GenerateArchitectureDesignCommandV1`
- `QueryArchitectureDesignStatusV1`
- `ArchitectureDesignGeneratedEventV1`
- `ArchitectureDesignResultV1`
- `ArchitectDesignErrorV1`
