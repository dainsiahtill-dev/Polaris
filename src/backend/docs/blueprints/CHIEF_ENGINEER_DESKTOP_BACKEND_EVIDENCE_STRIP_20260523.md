# Chief Engineer Desktop Backend Evidence Strip - 2026-05-23

## Scope

- Desktop surface: `src/frontend/src/app/components/chief-engineer/ChiefEngineerWorkspace.tsx`
- Backend contracts surfaced:
  - `GET /v2/roles/capabilities/chief_engineer?host_kind=electron_workbench`
  - `GET /v2/role/chief_engineer/llm-events?limit=5`
- Test coverage: `src/frontend/src/app/components/chief-engineer/ChiefEngineerWorkspace.test.tsx`

## Root Cause

The Chief Engineer workspace exposed blueprint, diagnostic, and Director worker evidence, but did not show the shared role capability contract or recent role LLM event evidence on the desktop surface. Operators had to infer these through the embedded dialogue panel instead of seeing first-class Chief Engineer backend state.

## Fix

- Added a compact full-width Chief Engineer backend evidence strip below the workspace header.
- Loaded Chief Engineer electron workbench capabilities through the typed RoleSession capability service.
- Loaded recent Chief Engineer LLM events through the existing V2 role event service.
- Rendered exact endpoint labels, capability names, event count, latest event type, model, and token count.
- Extended the Chief Engineer workspace regression test to assert both backend calls and visible evidence.

## Verification

Run from repository root:

```bash
npx eslint src/frontend/src/app/components/chief-engineer/ChiefEngineerWorkspace.tsx src/frontend/src/app/components/chief-engineer/ChiefEngineerWorkspace.test.tsx
npm test -- src/app/components/chief-engineer/ChiefEngineerWorkspace.test.tsx
npm run typecheck
npm run build
git -c i18n.logOutputEncoding=UTF-8 diff --check
```

