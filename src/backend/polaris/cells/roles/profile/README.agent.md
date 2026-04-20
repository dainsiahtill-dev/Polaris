# Roles Profile Cell

## Purpose

Provide the canonical role profile schema, policy model, registry, and builtin
profile loading/serialization boundary.

## Kind

`capability`

## Public Inputs

- `RegisterRoleProfileCommandV1`
- `LoadRoleProfilesCommandV1`
- `SaveRoleProfilesCommandV1`
- `GetRoleProfileQueryV1`
- `ListRoleProfilesQueryV1`

## Public Outputs

- `RoleProfileResultV1`
- `RoleProfilesResultV1`
- `RoleProfileRegisteredEventV1`
- `RoleProfilesLoadedEventV1`

## Depends On

- `policy.workspace_guard`
- `storage.layout`
- `audit.evidence`

## State Ownership

- `polaris/cells/roles/profile/internal/config/core_roles.yaml`

## Effects Allowed

- `fs.read:workspace/**`
- `fs.write:workspace/**`
- `fs.read:polaris/cells/roles/profile/internal/config/*`
- `fs.write:polaris/cells/roles/profile/internal/config/*`

## Invariants

- role profile schema must remain serializable and deterministic
- registry is the source of truth for profile lookup, not caller-local caches
- session/adapter concerns do not belong here

## Typical Change Surface

- `public/contracts.py`
- `public/service.py`
- `internal/schema.py`
- `internal/registry.py`
- `internal/builtin_profiles.py`

## Verification

- `tests/test_role_events.py`
- `tests/test_role_kernel_write_budget.py`
- `tests/test_role_adapters_taskboard_alignment.py`
- `tests/test_role_dialogue_validation_retry.py`
