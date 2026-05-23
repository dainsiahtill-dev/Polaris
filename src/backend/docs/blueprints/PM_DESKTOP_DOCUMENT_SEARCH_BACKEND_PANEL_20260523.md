# PM Desktop Document Search Backend Panel Blueprint

Date: 2026-05-23
Status: Implemented
Scope: PM desktop document workspace frontend integration, reusing the existing PM management HTTP contract.

## Current Fact

The backend already exposes document indexing and content search through:

- `GET /v2/pm/documents`
- `GET /v2/pm/documents/{doc_path}`
- `POST /v2/pm/documents/{doc_path}`
- `GET /v2/pm/search/documents?q={query}&limit={limit}`

The PM desktop document panel listed and opened tracked documents, but its search box only filtered the already-loaded tree by path/name. That meant content matches returned by the backend search route were not visible in the desktop workflow.

## Target Data Flow

```text
PMDocumentPanel search input
  -> pmDocumentService.search(query, limit)
  -> GET /v2/pm/search/documents
  -> render backend result rows with path/snippet/score evidence
  -> user selects result
  -> pmDocumentService.get(path)
  -> GET /v2/pm/documents/{doc_path}
  -> render persisted PM document content
```

## Module Responsibilities

- `src/frontend/src/services/pmService.ts`
  - Owns typed frontend wrappers for PM management HTTP routes.
  - Adds no backend state and no alternate document index.

- `src/frontend/src/app/components/pm/PMDocumentPanel.tsx`
  - Keeps the tracked document tree sourced from `/v2/pm/documents`.
  - Adds a separate backend search result strip for content/path search evidence.
  - Opens a result through the existing document read route instead of trusting search snippets as document content.

## Verification Plan

- Frontend service tests verify the exact search endpoint and query encoding.
- PM document panel tests verify a backend search result can open the matching persisted document.
- Existing backend PM management tests continue to cover the v2 search route contract.
