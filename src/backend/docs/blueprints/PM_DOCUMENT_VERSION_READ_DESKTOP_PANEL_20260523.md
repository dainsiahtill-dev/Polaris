# PM Document Version Read Desktop Panel Blueprint

Date: 2026-05-23
Status: implemented
Classification: pattern

## Problem

The PM document backend supports version-specific reads through
`GET /v2/pm/documents/{path}?version={version}`. The PM desktop document panel
can list versions and compare the latest two versions, but it cannot open a
specific historical document body. That makes version history visible but not
inspectable.

## Scope

This change is limited to PM document version inspection:

- Extend the typed `pmDocumentService.get` wrapper with an optional `version`
  query parameter.
- Add read-only "view version" controls to each visible version row.
- Show endpoint evidence for the selected versioned read.
- Disable editing while a historical version is selected to avoid accidentally
  saving old content as the current document.
- Provide a "current version" action that reloads the latest document body.

No backend route, document persistence behavior, graph edge, or Cell contract is
changed.

## Architecture Sketch

```text
PMDocumentPanel
  -> pmDocumentService.versions(path)
      -> GET /v2/pm/documents/{path}/versions
  -> pmDocumentService.get(path, version)
      -> GET /v2/pm/documents/{path}?version={version}
      -> read-only historical preview
  -> pmDocumentService.get(path)
      -> current editable document body
```

The backend remains the source of truth for stored versions. The desktop panel
only surfaces the existing version query and prevents edit/save from historical
content.

## Assumption Register

- The backend `version` query parameter returns the requested historical content
  in the same document detail payload shape.
- Historical version content should be previewed, not edited directly.
- Reloading the current version through `pmDocumentService.get(path)` returns
  the editable current document body.
- The existing version list is the correct desktop affordance for selecting a
  historical version.

## Verification Plan

- `npm run test -- PMDocumentPanel pmService`
- `npm run typecheck`
- `npm run lint`
- Cross-role regression:
  `npm run test -- PMPage ChiefEngineerPage PMWorkspace ChiefEngineerWorkspace DirectorWorkspace PMWorkbenchPanel DirectorWorkbenchPanel PMTaskPanel PMDocumentPanel DirectorTaskPanel PMDiagnosticsPanel pmService chiefEngineerService RoleChatPanel api.roleChatService`
