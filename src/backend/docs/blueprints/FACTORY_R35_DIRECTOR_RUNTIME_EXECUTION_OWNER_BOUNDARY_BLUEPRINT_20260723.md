# Factory R35 Director Runtime-Execution Owner Boundary

Status: closed

## Defect

Director sub-invocation preparation reads TaskRuntime-owned
`metadata.runtime_execution`, removes parent identity fields from a copied
mapping, and writes that altered projection into outbound role metadata.  This
violates the execution-state ownership boundary even though the original task
row is not mutated.

## Required design

1. Treat `metadata.runtime_execution` as read-only input used only to validate
   the authoritative parent identity.
2. Do not forward a modified or partial `runtime_execution` projection to a
   Director child role invocation.
3. Carry the parent/child relationship only through the typed
   `director.role_subinvocation.v1` evidence already used by R31.
4. Preserve fail-closed rejection for conflicting parent identities and keep
   the original caller context unchanged.

## Acceptance

- The TaskRuntime ownership architecture fence passes.
- Director adapter identity tests prove stable replay, distinct stage ids,
  conflict rejection, and absence of an altered runtime projection.
- Roles Adapter full tests, Ruff, mypy, and the pre-bench architecture gate are
  green before any Provider or Bench request is authorized.

## Closure evidence

- Focused Director sub-invocation identity tests: 5 passed.
- TaskRuntime owner architecture fence: 1 passed.
- Roles Adapter Cell suite: 1302 passed.
- Repository architecture suite: 1411 passed, 8 skipped.
- Ruff, TaskRuntime strict mypy, and compileall: passed.
