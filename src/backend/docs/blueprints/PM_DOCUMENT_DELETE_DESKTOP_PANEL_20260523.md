# PM Document Delete Desktop Panel Blueprint

Date: 2026-05-23
Status: implemented
Classification: pattern

## Problem

The PM management backend exposes `DELETE /v2/pm/documents/{path}` and already
supports the `delete_file` safety flag. The desktop PM document panel could
list, read, save, search, version, and compare documents, but it could not call
the existing delete route. This left a hidden PM backend capability and made the
desktop document workflow incomplete.

## Scope

This change is limited to PM document management:

- Add a typed `pmDocumentService.delete(path, deleteFile)` wrapper.
- Add a guarded delete affordance to `PMDocumentPanel`.
- Default the desktop flow to `delete_file=false` so a user can remove PM
  tracking evidence without deleting the backing file by accident.
- Expose an explicit checkbox when the backing file should also be deleted.
- Show endpoint evidence for `DELETE /v2/pm/documents/{path}` and refresh the
  document tree after a successful delete.

No backend route, Cell contract, graph edge, or PM document manager behavior is
changed.

## Architecture Sketch

```text
PMDocumentPanel
  -> pmDocumentService.delete(path, deleteFile)
      -> DELETE /v2/pm/documents/{encoded path}?delete_file={true|false}
      -> DocumentDeleteResponse
  -> clear selected document + refresh PM document tree
```

The backend remains the source of truth for document registry and file deletion.
The desktop panel only surfaces the existing route with an explicit destructive
operation checkpoint.

## Assumption Register

- The PM backend route is authoritative for document deletion.
- The `delete_file` query flag is the backend safety control for preserving or
  removing the actual file.
- PMDocumentPanel already owns document tree refresh and selected document
  projection, so it can clear the deleted selection and reload the tree.
- The UI must not default to deleting the backing file.

## Verification Plan

- `npm run test -- PMDocumentPanel pmService`
- `npm run typecheck`
- `npm run lint`
- Cross-role regression:
  `npm run test -- PMPage ChiefEngineerPage PMWorkspace ChiefEngineerWorkspace DirectorWorkspace PMWorkbenchPanel DirectorWorkbenchPanel PMTaskPanel PMDocumentPanel DirectorTaskPanel PMDiagnosticsPanel pmService chiefEngineerService RoleChatPanel api.roleChatService`
