# PM Snapshot Contract Fallback Desktop 20260524

## Problem

The desktop can receive a successful PM process status with a concrete `contract_path`, while `/state/snapshot` still returns an empty `tasks` list because the projection only reads `runtime/contracts/pm_tasks.contract.json` from its locally resolved runtime root.

This makes the PM/Director UI show a generated plan and a failed dispatch banner, but no PM task rows for audit or recovery.

## Root Cause

- `get_pm_local_status()` drops PMService fields such as `contract_path`, `ok`, `terminal`, `exit_code`, and `error`.
- `build_snapshot_payload_from_projection()` reads PM contract/state/result/plan artifacts from one `cache_root` only.
- When the PM process wrote artifacts under the runtime root known by PMService, the snapshot projection could not recover them.

## Change

- Preserve PMService status fields in the runtime projection.
- Let snapshot construction infer an additional runtime root from `pm_local.contract_path`.
- Read PM contract, PM state, Director result, and plan/agents artifacts from the first available candidate runtime root.
- Keep the fallback restricted to paths shaped like `.../projects/<workspace-key>/runtime/contracts/pm_tasks.contract.json`.

## Verification

- Add unit coverage for PM status field preservation.
- Add unit coverage for snapshot task/state/result/plan recovery through PM status contract path.
- Run targeted pytest, ruff, format, and mypy on changed backend files.
