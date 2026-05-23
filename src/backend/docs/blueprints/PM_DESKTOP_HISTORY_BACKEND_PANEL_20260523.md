# PM Desktop History Backend Panel Blueprint

Date: 2026-05-23

## Scope

Replace the PM desktop history placeholder with backend-backed task history and Director dispatch history evidence.

## Existing Backend Contracts

- `GET /v2/pm/tasks/history`
- `GET /v2/pm/tasks/director`

Both routes are implemented in `polaris.delivery.http.routers.pm_management` and covered by PM management router tests.

## Frontend Plan

1. Add typed PM service wrappers for task history and Director dispatch history.
2. Load both histories when the PM History tab is opened.
3. Render compact evidence rows and keep the raw PM state snapshot as secondary context.
4. Preserve empty/error states without fabricated history.

## Verification

- Add service tests for both history routes.
- Add a focused PM workspace test proving the History tab calls backend history routes and renders returned evidence.
- Re-run PM/Director/Chief Engineer desktop regression slice, typecheck, build, and PM management backend tests.
