# DEO-4 Legacy Removal and Architecture Fence Design

**Status:** locked; implementation active  
**Owner:** Codex `/root`  
**Date:** 2026-07-20

## Goal

Close Directed Effect Operation v1 by deleting the remaining unscoped physical
executor construction seam and freezing the final repository-wide mutation,
receipt, recovery, parent-close, and terminal-settlement ownership boundaries.

## Current Evidence

- The original 38 `DirectorToolExecutor` construction/injection surfaces have
  converged to one production constructor and one `execute_tool` call, both in
  `directed_effect_mutation_port.py`.
- DEO-2D already proves zero unbound Director mutation surfaces and no raw patch,
  process, command-service, workspace-write, or repair-callback bypass.
- DEO-3 proves TaskRuntime-only receipt/recovery/dead-letter/parent-close facts,
  one terminal settlement path, read-only Run Ledger projection, and crash-safe
  session/recovery locking.
- The remaining seam is behavioral: `DirectorToolExecutor(workspace)` is still a
  generally callable constructor. A future production caller could recreate a
  raw physical executor before an architecture inventory is updated.

## Decision

### 1. Fail-closed construction authority

`DirectorToolExecutor` receives a module-private construction authority. Direct
construction without the exact process-local authority raises the typed
`DirectorToolExecutionAuthorityError` with code
`directed_effect_physical_executor_authority_required`.

The module-private `_create_director_tool_executor` factory is the only creator.
It stores the authority on the executor, and `execute_tool` rechecks it before
any read or mutation dispatch. Serialization/copy-based transport is forbidden.
This is process-local architecture control, not a cryptographic security claim.

### 2. One production composition edge

Only `directed_effect_mutation_port.py` may import/call the private factory. The
mutation port remains ordered as:

```text
normalize + validate -> current-policy revalidate -> one-shot fence consume
-> private physical factory/execute -> post-state observation -> durable receipt
```

No public adapter, role, Director Runtime, TaskRuntime, Factory, QA, CLI, or Run
Ledger module may construct or call the physical executor.

### 3. Test access is explicit and non-product

Low-level `roles.adapters` tests may import the private factory to test physical
tool mechanics. A dedicated regression must prove the legacy direct constructor
fails. Production-tree AST fences exclude tests but scan every production Python
file, so a test helper cannot silently become a production injection path.

### 4. Final ownership fences

The closure gate freezes:

- one private physical-executor constructor inside its factory;
- one production private-factory call in the directed-effect mutation port;
- one physical `execute_tool` call in that same mutation port;
- zero `executor_factory`, direct repair callback, subprocess, raw patch, or
  workspace mutation bypasses;
- TaskRuntime-only receipt/recovery/dead-letter/parent-close writers;
- one public settlement compatibility wrapper delegating to the typed terminal
  entry, with no standalone parent-close API;
- Run Ledger and QA as read-only evidence consumers.

## Non-goals

- No target-project code or business-specific repair.
- No new transaction, batch, receipt store, SSoT, transport, or provider call.
- No weakening of guarded FactStream CAS, policy revalidation, PID fence,
  recovery, terminal admission, or strict receipt validation.
- No Bench until DEO-4 closure and the separate pre-bench gate pass.

## Acceptance

1. Direct `DirectorToolExecutor(...)` construction fails with the typed code.
2. The private factory creates a usable executor only for reviewed internal
   tests and the canonical mutation port.
3. Repository AST inventory reports exactly `1/1/1` constructor/factory-call/
   physical-execute sites and zero bypasses.
4. Existing DEO-2D, DEO-3, roles.kernel, roles.adapters, TaskRuntime, Run Ledger,
   guarded FS/FactStream, and architecture suites remain green.
5. Ruff, format, mypy, compileall, YAML/catalog, import smoke, and diff pass.
6. Independent specification and quality/security reviews report zero Critical
   and Important findings.
7. DEO-4 alone closes; Provider/Bench/target-project effects remain zero.

