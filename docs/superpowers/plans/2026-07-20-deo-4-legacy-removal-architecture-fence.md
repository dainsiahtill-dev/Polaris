# DEO-4 Legacy Removal and Architecture Fence Plan

**Goal:** Remove the last unscoped physical executor construction seam and
freeze the final DEO repository boundary.

**Locked design:**
`docs/superpowers/specs/2026-07-20-deo-4-legacy-removal-architecture-fence-design.md`

## Scope lock

- Production edits are limited to the Director physical executor and canonical
  mutation port; test edits cover their direct consumers and architecture fence.
- Metadata/docs change only after gates pass.
- Provider, Bench, and target-project effects are forbidden.

## Task 1: Freeze RED behavior

- [x] Add a regression proving direct `DirectorToolExecutor(workspace)`
  construction fails with a typed authority error.
- [x] Extend the production AST inventory to require the private constructor,
  factory call, and physical execute topology.
- [x] Run selected tests and record the expected RED failures.

## Task 2: Remove the legacy construction seam

- [x] Add process-local private construction authority and a typed error.
- [x] Add `_create_director_tool_executor`; register/revalidate exact
  process-local instance identity and reject
  serialization/copy transport.
- [x] Replace the mutation port's direct constructor with the private factory.
- [x] Migrate low-level physical-executor tests to explicit private-factory use.

## Task 3: Freeze the final repository architecture

- [x] Prove exactly one constructor in the private factory, one production
  factory call in the mutation port, and one physical execute call there.
- [x] Re-run zero-unbound-mutation, canonical gate order, TaskRuntime writer,
  terminal authority, Run Ledger read-only, and adversarial module-alias,
  assignment/taint, wildcard, dynamic-getattr, importlib, and self-factory fences.
- [x] Prove direct constructor, manual clone, pickle/copy transport, public re-export, and
  production import/call bypasses fail closed.

## Task 4: Broad proof and independent review

- [x] Run full roles.adapters, roles.kernel, TaskRuntime, Run Ledger, guarded
  FS/FactStream, and architecture gates.
- [x] Run Ruff, format, strict mypy, compileall, YAML/catalog, import smoke, and
  scoped diff checks.
- [x] Obtain independent specification and quality/security reviews with zero
  Critical/Important findings.

## Task 5: Closure and pre-bench handoff

- [x] Synchronize blueprint, gap ledger, Cell/catalog metadata, verification
  card, and `memory/MEMORY.md`.
- [x] Mark DEO-4 closed only with exact evidence. Move only the pre-bench gate
  to active; Provider and Bench remain `not_schedulable` until it passes.
