# ADR-0050: Role-Based Default Execution Domain Policy

Date: 2026-03-25

## Status

Accepted

## Context

`roles.runtime` introduced domain-aware strategy routing, but its fallback rule
remained global (`default=code`) for all roles when callers omitted `domain`.
This conflicted with role semantics:

1. PM / Architect / Chief Engineer are primarily document-design roles.
2. Director is the code execution role.
3. Domain resolution logic was duplicated across runtime paths, increasing drift risk.

## Decision

Introduce a centralized `RoleDomainPolicy` in `roles.runtime.internal` and make
all runtime domain resolution flow through it.

Resolution order is now:

1. Explicit domain (`command.domain`)
2. Context domain (`context["domain"]`)
3. Metadata domain (`metadata["domain"]`)
4. Role default domain (`pm/architect/chief_engineer -> document`, `director -> code`)
5. Global fallback (`code`)

Additional policy guarantees:

1. Normalize role aliases and common legacy spellings before role-default lookup.
2. Keep `general -> code` strategy mapping explicit and centralized.
3. Preserve backward compatibility for unknown roles via global fallback.

## Consequences

Positive:

1. Domain routing now matches role intent by default.
2. No more scattered fallback logic in request builders and stream paths.
3. New role-domain rules can be added in one policy module with focused tests.

Trade-offs:

1. Policy module becomes a critical routing dependency and must be well tested.
2. Alias table maintenance is required as legacy role names evolve.

## Verification

1. `polaris/cells/roles/runtime/tests/test_role_domain_policy.py`
2. `polaris/cells/roles/runtime/tests/test_role_runtime_strategy.py`
3. `pytest polaris/cells/roles/runtime/tests/test_role_domain_policy.py polaris/cells/roles/runtime/tests/test_role_runtime_strategy.py -q`

## Follow-up

1. Sync domain policy notes into runtime blueprint docs.
2. When adding new public roles, require explicit domain default declarations.
