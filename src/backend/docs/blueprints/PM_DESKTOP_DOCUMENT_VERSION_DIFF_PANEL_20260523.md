# PM Desktop Document Version Diff Panel Blueprint

Date: 2026-05-23
Status: Implemented
Scope: PM desktop document workspace frontend integration, reusing existing PM document version contracts.

## Current Fact

The backend already exposes document version and diff routes:

- `GET /v2/pm/documents/{doc_path}/versions`
- `GET /v2/pm/documents/{doc_path}/compare?old_version={old}&new_version={new}`

The PM desktop document panel could read and save the current document content, but it did not expose persisted version history or version comparison evidence.

## Target Data Flow

```text
User selects a PM document
  -> pmDocumentService.get(path)
  -> pmDocumentService.versions(path)
  -> render current content and version history evidence

User clicks "compare latest"
  -> select latest two backend versions
  -> pmDocumentService.compare(path, oldVersion, newVersion)
  -> render diff text, changed sections, requirement deltas, and impact score
```

## Module Responsibilities

- `src/frontend/src/services/pmService.ts`
  - Adds typed wrappers for existing version and compare routes.

- `src/frontend/src/app/components/pm/PMDocumentPanel.tsx`
  - Keeps current content loading separate from version metadata.
  - Treats diff output as backend evidence and does not mutate the document body.
  - Refreshes versions after successful saves.

## Verification Plan

- Frontend service tests verify encoded version and compare endpoint paths.
- PM document panel tests verify version loading and latest-version diff rendering.
- Existing backend PM management tests continue to cover the v2 version and compare contracts.
