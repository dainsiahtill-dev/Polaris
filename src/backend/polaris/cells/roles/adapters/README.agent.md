# Roles Adapters Cell

## Purpose

Provide the stable adapter factory and structured output schema boundary for
role execution entrypoints. During migration, Director's optional projection
execution backend is still routed through this adapter layer.

## Kind

`composite`

## Public Inputs

- `CreateRoleAdapterCommandV1`
- `ListSupportedRoleAdaptersQueryV1`

## Public Outputs

- `RoleAdapterResultV1`
- `RoleAdapterRegisteredEventV1`

## Depends On

- `roles.engine`
- `roles.kernel`
- `roles.profile`
- `roles.session`
- `llm.dialogue`
- `factory.pipeline`
- `runtime.task_runtime`
- `policy.workspace_guard`

## State Ownership

- None

## Effects Allowed

- `fs.read:workspace/**`
- `fs.write:runtime/signals/*`
- `fs.write:runtime/events/*`
- `ws.outbound:runtime/*`
- `process.spawn:roles/*`
- `llm.invoke:roles/*`

## Invariants

- adapter creation is role-id driven and deterministic
- public schema exports must remain stable for callers
- adapters should not expose internal implementation modules as the public API
- only the canonical directed-effect mutation port may create and consume a
  physical `DirectorToolExecutor`
- physical executor authority is exact process-local instance identity in a
  private registry; copied fields, manual clones, copy/deepcopy, and pickle do
  not grant authority
- after fence consume, executor construction or execution failure must enter
  durable recovery and replay must remain denied
- no public or direct constructor/import/re-export surface may bypass the
  repository architecture fence

## Typical Change Surface

- `public/contracts.py`
- `public/service.py`
- `internal/*.py`
- `internal/schemas/*.py`

## Verification

- `tests/test_role_adapters_taskboard_alignment.py`
- `tests/test_runtime_role_binding.py`
- `tests/test_role_chat_status.py`
- `polaris/cells/roles/adapters/tests/test_director_execution_authority.py`
- `polaris/cells/roles/adapters/tests/test_director_directed_effect_mutation_port.py`
- `polaris/tests/architecture/test_deo_2d_zero_unbound_mutation_surfaces.py`

## Notes

- Director adapter owns execution backend selection metadata and routing inside
  this cell boundary.
- Explicit `execution_backend` routing is opt-in only. Default Director work
  continues to use the classic `code_edit` path.
- DEO-4 legacy removal is closed with `100` focused and `1250` full
  roles.adapters tests plus `CLEAR/CLEAR` independent review. Pre-bench is the
  only active gate; Provider and Bench remain `not_schedulable`.
