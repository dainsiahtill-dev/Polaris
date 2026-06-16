# Director Synthesis Integrity Gate Blueprint (2026-06-16)

## Status

Structural hardening. This blueprint isolates Director deterministic synthesis that embeds target-project business contracts.

## Problem

`polaris.cells.roles.adapters.internal.director.execute_method` contains deterministic repair hooks that can synthesize `audit.service.ts`, `task.service.ts`, `dag.service.ts`, `task.model.ts`, `tenant.model.ts`, and placeholder test files. These outputs are business-domain artifacts, not platform invariants. When enabled by default in the Director hot path, they can make a benchmark appear complete by writing historical project answers rather than exposing the true LLM/tool failure.

## Decision

Default Director execution must fail closed:

- Keep language/toolchain repairs that are generic, such as TypeScript syntax reshaping and declared dependency repair.
- Keep legacy business/scaffold synthesis only behind explicit opt-in environment gates.
- Default-off behavior must be covered by tests that call the materialization repair hot path, not just helper functions.

## Data Flow

```
artifact quality errors
  -> _apply_deterministic_materialization_quality_repairs
     -> generic language/package repairs remain active
     -> placeholder test synthesis requires KERNELONE_DIRECTOR_SCAFFOLD_SYNTHESIS=1
     -> business contract synthesis requires KERNELONE_DIRECTOR_BUSINESS_CONTRACT_SYNTHESIS=1
```

## Verification

- Red tests prove default hot path used to synthesize business/placeholder files.
- Green tests prove default hot path returns no writes for those cases.
- Existing legacy tests opt into the gate explicitly and continue to document historical fixture behavior.

