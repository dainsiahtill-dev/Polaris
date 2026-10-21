# Engineering workspace current-run bootstrap

> **For agentic workers:** Use superpowers:subagent-driven-development or executing-plans. Work directly on main; all disposable evidence stays under `~/.polaris/`.

**Goal:** Opening an instance from Launcher displays its existing engineering run and receives its real progress without requiring a manually appended run ID.

**Architecture:** Read current-run identity from the existing EngineeringRunService and EngineeringSessionStore. The instance registry remains discovery metadata. An initial workspace-scoped read and engineering WebSocket events bootstrap the frontend; explicit run identity remains authoritative for that view.

**Tech Stack:** Existing SQLite owner, FastAPI router, React hook/API client, Vitest and Playwright.

**Spec:** User-reported empty L1-03 instance page, reproduced with the Launcher URL and the native binding URL. Evidence: `~/.polaris/audit_archives/autonomous-delivery-20261018/engineering-v2-refactor-20261020/task9-full120-e700-cohort/audit-ui-blank/root-defect.json`.

## Global constraints

- No branches/worktrees, no target-project edits, no model/config changes.
- The three e700 native cases have ended and released their processes. Their outcomes and root quality findings remain unchanged.
- No new task table, controller, registry current-run fact, polling, or old-engine fallback.
- Reads cannot create/replay a run or tool, alter durable statuses, or inspect another workspace.
- All text reads/writes use UTF-8. All shell commands start with RTK.
- Remaining original Bench cases resume serially after this interface is integrated and qualified, under a new recorded source fingerprint.

## Interfaces

1. Store: `find_current_session_id(*, workspace: Path, engine_version: str) -> str | None`.
2. Owner: `current_snapshot(*, workspace: Path) -> dict[str, Any] | None`, validates workspace and reuses `snapshot` freshness/projection.
3. HTTP: authenticated `GET /v2/engineering/runs/current?workspace=...`, before `/runs/{session_id}`. Response is an existing snapshot or JSON null; ambiguous active runs use the existing conflict mapping (409).
4. Client: `getCurrentEngineeringRun(workspace: string): Promise<ApiResult<EngineeringRunSnapshot | null>>`.

Current selection reads existing rows only: filter exact workspace/engine version; prefer one pending/running run; reject multiple active runs; otherwise latest created terminal run, with deterministic tie ordering. Do not filter by role. Role discussions do not create engineering runs; nested consultation uses the parent row.

## Review focus

- Explicit URL IDs cannot be replaced by discovery or unrelated workspace events.
- An empty workspace can discover a later externally started run from runtime.v2 without HTTP timers.
- Stale initial/reconnect responses cannot hydrate another workspace or selected run.
- Null is a valid empty result; malformed or wrong-workspace snapshots remain errors.
- Cold reads preserve statuses and cause no LLM/tool/control effects; a static current route cannot be swallowed as a session ID.

## Task 1: Existing owner read path

Files owned by backend implementer:

- `src/backend/polaris/cells/roles/runtime/internal/engineering_session.py`
- `src/backend/polaris/cells/roles/runtime/public/engineering_service.py`
- `src/backend/polaris/delivery/http/routers/engineering_runs.py`
- New `src/backend/polaris/cells/roles/runtime/tests/test_engineering_current_run.py`
- `src/backend/polaris/delivery/http/tests/test_engineering_runs.py`

- [x] Add real SQLite/service/HTTP tests for empty, active versus terminal, multiple active, role independence, old engine filtering, workspace mismatch, cold read, static route ordering and no execution.
- [x] Watch the new-query tests fail against e700; retain raw output externally.
- [x] Implement the minimal SELECT/owner/router interface above, using the injected existing owner and a single read transaction for selection.
- [x] Run new and adjacent service/router tests plus Ruff/format/type checks. No independent source refactor or commits by the child; root integrates the full interface.

## Task 2: Frontend consumer and actual handoff

Files owned by root:

- `src/frontend/src/services/engineeringRunService.ts` and its tests
- `src/frontend/src/hooks/useEngineeringRun.ts` and its tests
- Relevant test-only engineering navigation fixture if required by the default-engine cutover

- [x] Add failing tests for no-ID bootstrap, explicit-ID precedence, null followed by a real same-workspace run-start event, foreign event rejection, stale response rejection and bounded reads.
- [x] Add the nullable API read and reuse snapshot validation.
- [x] Subscribe to the existing workspace engineering channel when no ID is selected; relevant committed run-start/update or resync events may request the authoritative current snapshot. Never infer a new workspace or a business verdict from an event.
- [x] Once selected, retain the existing session/workspace guards and snapshot watermark/reconnect handling. No timer fetch or global Bench subscription.
- [x] Run the relevant frontend tests, type/build checks and a real Launcher click into a native isolated instance. Verify owner snapshot identity, rendered role/progress, actual event.engineering subscription and subsequent WebSocket-driven progress, with no run-control action.

## Integration and stopping condition

- [x] Root checks complete diff and independent scoped review; fixes only causal findings.
- [x] Run adjacent engineering/instance/CLI tests and required KernelOne ALL gates; preserve known baseline failures separately.
- [x] Commit the interface together to main, verify qualified bytes, then freeze a new experiment batch.
- [x] Record e700 sample applicability separately. Continue original tasks serially; role-combination experiments use the same frozen candidate and actual independent quality checks, not source/test counts.

Ruling: the user has authorized routine reversible engineering and explicitly asked for this observed handoff to work. This fix uses the established owner and proceeds without an additional approval ceremony.

Final evidence: `~/.polaris/audit_archives/autonomous-delivery-20261018/engineering-v2-refactor-20261020/task11-current-run-bootstrap/`. Backend25/53 and independent45; frontend121 plus independent34; final browser current-query200, native tool results and WS seq8 to29, source8762 unchanged, auxiliary resourcesCLOSED. AdjacentCLI/instances215pass. KernelOne12stages368pass1skip applies to unchanged backend bytes, not a globally frozen run; front53baselinefail and broadbackendinterruption retained.

Deferred latent P2: hook prop r1->start(new)->r2->r1 may resurrect override. Current App captures initial URL once, so this is outside the current Launcher path. Must fix before adding live URL navigation. Counterexample in final-review; no claim of general navigation completeness.
