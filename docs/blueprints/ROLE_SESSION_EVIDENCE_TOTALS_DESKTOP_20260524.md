# RoleSession Evidence Totals Desktop Blueprint

Date: 2026-05-24

## Problem

PM, Chief Engineer, and Director desktop workbenches share the RoleSession evidence strip. The UI currently previews a limited page of messages and audit rows, but the visible count can read as the total. This makes long role conversations look smaller than they are and weakens audit confidence when a user exports a session into PM or Director workflow execution.

## Scope

- Backend delivery route: `polaris.delivery.http.routers.role_session`
- Backend response schema: `polaris.delivery.http.schemas.common`
- Frontend RoleSession service and shared evidence panel
- Existing role workbench tests for PM, Chief Engineer, and Director

## Data Flow

```text
Role workbench
  -> RoleSessionEvidencePanel
  -> roleSessionService evidence calls
  -> /v2/roles/sessions/{id}/messages|artifacts|audit
  -> roles.session / audit.evidence services
  -> UI renders total count and preview count
```

## Design

1. Keep the existing list endpoints and add `total` to their response payloads.
2. Preserve existing frontend list helpers for other callers.
3. Add explicit evidence helper functions that return `{ items, total }`.
4. Render total count as the primary badge and preview count as secondary evidence when paging hides rows.

## Verification

- Backend router unit tests assert totals for messages, artifacts, and audit.
- Frontend service tests assert metadata-preserving evidence helpers.
- Shared evidence panel and three role workbench tests assert truthful totals are visible.
