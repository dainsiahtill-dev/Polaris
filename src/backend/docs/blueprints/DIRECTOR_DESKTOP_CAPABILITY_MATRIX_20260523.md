# Director Desktop Capability Matrix Blueprint

Date: 2026-05-23
Status: Implemented
Scope: Director desktop workspace frontend integration, reusing the existing Arsenal capability HTTP contract.

## Current Fact

The backend already exposes Director capabilities through:

- `GET /v2/director/capabilities`

The route is implemented in `polaris.delivery.http.routers.arsenal` and delegates to `polaris.domain.entities.capability.get_role_capabilities("director")`. The returned payload contains the role and a host-kind keyed capability matrix, such as `electron_workbench` and `workflow`.

## Target Data Flow

```text
DirectorWorkspace mount
  -> getDirectorCapabilities()
  -> GET /v2/director/capabilities
  -> normalize host-kind capability map
  -> render compact desktop capability strip
```

## Module Responsibilities

- `src/frontend/src/services/pmService.ts`
  - Provides the typed frontend wrapper for the existing Director capability route.
  - Does not create a second capability source of truth.

- `src/frontend/src/app/components/director/DirectorWorkspace.tsx`
  - Renders backend capability evidence near the Director controls.
  - Highlights whether `delete_files` is allowed or blocked.
  - Keeps capability visibility separate from task execution state.

## Verification Plan

- Frontend service tests verify the exact capability endpoint.
- Director desktop tests verify backend capability data is normalized and rendered.
- Backend Arsenal v2 tests verify the route contract remains intact.
