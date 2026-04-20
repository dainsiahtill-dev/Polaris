# Context Catalog

## Purpose

Build deterministic descriptor cards from graph assets and expose the first
Polaris search surface for `context.catalog`.

## Kind

`capability`

## Public Inputs

- `SearchCellsQueryV1`

## Public Outputs

- `SearchCellsResultV1`
- `CellDescriptorV1`

## Depends On

- None at the current Polaris graph level.

## State Ownership

- `workspace/meta/context_catalog/*`

## Effects Allowed

- `fs.read:docs/graph/**`
- `fs.write:workspace/meta/context_catalog/*`

## Invariants

- graph assets remain the source of truth
- descriptor cache is derived and rebuildable
- semantic ranking cannot widen graph boundaries
- current implementation uses lexical ranking only

## Read Order for AI

1. `cell.yaml`
2. `generated/context.pack.json`
3. `public/contracts.py`
4. `service.py`
5. graph assets only if needed
