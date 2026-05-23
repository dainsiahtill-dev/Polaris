# Chief Engineer Dialogue Director Workflow Export Blueprint

Date: 2026-05-23
Status: implemented
Classification: pattern

## Problem

PM and Director desktop workbench surfaces expose RoleSession-to-workflow
handoff controls. Chief Engineer already uses `AIDialoguePanel`, which can
create RoleSessions and export them through the existing
`POST /v2/roles/sessions/{session_id}/actions/export-to-workflow` backend
contract, but the Chief Engineer workspace does not enable that export control.
This leaves Chief Engineer dialogue output harder to hand off to Director
execution than PM and Director workbench output.

## Scope

This change is limited to Chief Engineer desktop dialogue parity:

- Enable `workflowExportTarget="director"` on the Chief Engineer
  `AIDialoguePanel`.
- Use a role-specific export label so the control is clear in the desktop.
- Add a regression test that verifies the Chief Engineer dialogue exports its
  RoleSession to the Director workflow contract.

No new backend route, RoleSession schema, or orchestration runtime behavior is
introduced; the existing role-session export backend is reused.

## Architecture Sketch

```text
ChiefEngineerWorkspace
  -> AIDialoguePanel(dialogueRole="chief_engineer")
      -> createRoleSession(role="chief_engineer", host_kind="electron_workbench")
      -> exportRoleSessionToWorkflow(target="director")
          -> POST /v2/roles/sessions/{session_id}/actions/export-to-workflow
          -> Director workflow run_id
```

The RoleSession backend remains the source of truth for session persistence and
workflow export. Chief Engineer only enables the existing export affordance.

## Assumption Register

- Chief Engineer dialogue output is a valid upstream artifact for Director
  workflow handoff.
- The existing RoleSession export backend supports `target="director"`.
- `AIDialoguePanel` already handles RoleSession creation, export submission,
  and export status evidence.
- Enabling export does not change chat behavior or blueprint generation.

## Pre-Mortem

- Risk: Chief Engineer export could look like generic session export rather
  than Director handoff.
  Mitigation: Use the explicit label `导出 Director`.
- Risk: The export button could render before the RoleSession exists.
  Mitigation: Reuse AIDialoguePanel's existing disabled state until session
  creation completes.
- Risk: The regression could only check rendered text and miss backend payload.
  Mitigation: Click the export control and assert the `/v2/roles/sessions/...`
  request body contains `target: "director"`.

## Verification Plan

- `npm run test -- ChiefEngineerWorkspace roleSessionService AIDialoguePanel useAIDialogue`
- `npm run typecheck`
- `npm run lint`
- Cross-role regression:
  `npm run test -- PMPage ChiefEngineerPage PMWorkspace ChiefEngineerWorkspace DirectorWorkspace PMWorkbenchPanel DirectorWorkbenchPanel PMTaskPanel PMDocumentPanel DirectorTaskPanel PMDiagnosticsPanel pmService chiefEngineerService RoleChatPanel api.roleChatService`
