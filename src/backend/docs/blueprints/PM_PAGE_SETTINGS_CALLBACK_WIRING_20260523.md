# PM Page Settings Callback Wiring

Date: 2026-05-23

## Finding

`PMWorkspace` exposes an `onOpenSettings` callback and renders a settings control
that is disabled when the callback is absent. The main `App` workspace switch
passes this callback, but the reusable `PMPage` wrapper did not declare or
forward it.

When PM is mounted through `PMPage`, the settings entry becomes inert even
though the PM desktop implementation already supports the action.

## Contract

- `PMPage` must expose `onOpenSettings?: () => void`.
- `PMPage` must forward the callback to `PMWorkspace`.
- Existing PM page rendering and LLM runtime overlay behavior remains unchanged.

## Boundary

- Frontend scope: PM page wrapper only.
- Backend scope: none; no backend behavior is changed.
- No target-project code is generated or modified.

## Verification

- PM page unit test proves the callback is forwarded.
- PM workspace/page focused frontend tests.
- TypeScript typecheck and lint.
