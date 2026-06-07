# Code Intelligence Engine Cell

## Purpose

Provide code indexing, context compilation, semantic analysis, and change
verification capabilities for Director and Chief Engineer flows.

## Public Contracts

- queries: `VerifyAstDependencyQueryV1`
- results: `AstDependencyVerificationResultV1`
- errors: `CodeIntelligenceEngineErrorV1`

## Public Service

- `verify_ast_dependency(VerifyAstDependencyQueryV1) ->
  AstDependencyVerificationResultV1` is the owner-cell RPC for CE AST and symbol
  verification. Callers must use this public service and must not import
  implementation handlers directly.

## Architecture

```
public/
  contracts.py   - frozen dataclasses for query, result, and error
  service.py     - owner-cell adapter over KernelOne TreeSitterSymbolHandler

internal/
  adapters/      - reserved for future code intelligence adapters
```

## State Ownership

This Cell is stateless. It reads workspace files through KernelOne filesystem
ports and returns typed verification results. It does not own role runtime
state, task market state, or blueprint state.

## Effects Allowed

- `fs.read:workspace/**`
- `process.spawn:code_intelligence/engine/**`

## Cross-Cell Rules

- `roles.runtime` may invoke `verify_ast_dependency` through the public service
  when a CE role object mounts `verify_ast_dependency`.
- Business role assets remain owned by their Cells. CE receives typed result
  references only; code intelligence does not write CE blueprint assets.
- `TreeSitterSymbolHandler.find_symbol` is an implementation port, not a public
  cross-Cell contract.

## Verification

```bash
python -m pytest -q polaris/cells/code_intelligence/engine/public/tests/test_contracts.py
```
