# Desktop Function Gap Audit Blueprint 2026-05-07

## Current Understanding

Polaris Desktop is the operational surface for the PM -> Chief Engineer -> Director -> QA workflow. The current UI already has PM, Chief Engineer, Director and runtime panels, but multiple surfaces render partial projections without making provenance, missing contracts, or runtime evidence clear enough.

The immediate user-reported failures map to these paths:

- PM page: task/document/quality evidence is split across snapshot, PM document service, and runtime events.
- Chief Engineer page: blueprint cards are inferred from task rows and can misclassify normal PM summaries as blueprints.
- Director page: task board shows groups and details, but selected execution does not carry task identity into the backend run request.
- Realtime UI: file edit events are captured but not visible in the global status/overlay.
- LLM settings: provider tests can pass while global save fails when partially configured role rows are posted.

## Evidence List

Read files:

- `src/frontend/src/app/App.tsx`
- `src/frontend/src/app/components/pm/PMWorkspace.tsx`
- `src/frontend/src/app/components/pm/PMTaskPanel.tsx`
- `src/frontend/src/app/components/pm/PMDocumentPanel.tsx`
- `src/frontend/src/app/components/chief-engineer/ChiefEngineerWorkspace.tsx`
- `src/frontend/src/app/components/director/DirectorWorkspace.tsx`
- `src/frontend/src/app/components/director/DirectorTaskPanel.tsx`
- `src/frontend/src/app/components/LlmRuntimeOverlay.tsx`
- `src/frontend/src/app/components/RealTimeStatusBar.tsx`
- `src/frontend/src/services/pmService.ts`
- `src/frontend/src/hooks/useProcessOperations.ts`
- `src/backend/AGENTS.md`
- `src/backend/docs/AGENT_ARCHITECTURE_STANDARD.md`
- `src/backend/docs/graph/catalog/cells.yaml`
- `src/backend/polaris/cells/runtime/projection/cell.yaml`
- `src/backend/polaris/cells/director/execution/cell.yaml`
- `src/backend/polaris/delivery/http/v2/director.py`
- `src/backend/polaris/cells/runtime/projection/internal/workflow_status.py`
- `src/backend/polaris/cells/chief_engineer/blueprint/internal/blueprint_persistence.py`
- `src/backend/polaris/kernelone/llm/config_store.py`

Confirmed call chains:

- PM quality gate: `useRuntime` parses `pm_quality_gate`, `App.tsx` has `qualityGate`, `PMWorkspace` currently does not render it.
- CE evidence: `ChiefEngineerWorkspace.buildBlueprintEvidence()` accepts `summary/goal` alone as blueprint evidence.
- Director selected task: `DirectorWorkspace.handleExecute()` logs selected task but calls `onToggleDirector()` without task id; backend `/v2/director/run` supports `task_filter`.
- Workflow task projection: `build_workflow_task_rows()` writes `metadata.pm_task_id` but not blueprint metadata from top-level task payload.
- Realtime file edits: `useRuntime` stores `fileEditEvents`; `RealTimeStatusBar` and `LlmRuntimeOverlay` do not consume them.
- LLM save: `LLMConfigSchema.RoleConfig.provider_id` is required for every role row, so partial optional roles can fail global config save.

## Defect Analysis

1. PM quality gate is invisible in the PM workspace.
   Trigger: PM emits quality gate event; user enters PM workspace.
   Root cause: `qualityGate` is only passed to overlay/main panel, not `PMWorkspace`.

2. CE blueprint evidence can be invented from normal task summary text.
   Trigger: a PM task has `summary` or `goal` but no `blueprint_id/path`.
   Root cause: blueprint evidence builder treats summary as sufficient proof.

3. Director selected-task execution is not real.
   Trigger: user selects one Director task and clicks execute.
   Root cause: UI only logs the selected task; no backend request carries `task_id/task_filter`.

4. Blueprint provenance can be dropped in Director task responses.
   Trigger: workflow task row has top-level blueprint fields.
   Root cause: projection and response model do not elevate `blueprint_id/path`.

5. Runtime file edits are hidden from global UI.
   Trigger: file edit events arrive over runtime websocket.
   Root cause: global status/overlay props do not include `fileEditEvents`.

6. LLM save can fail after provider tests pass.
   Trigger: visual/list config posts a partial optional role row.
   Root cause: backend schema requires `provider_id` for every role, even optional/unassigned roles.

## Fix Plan

Minimal modification points:

- Frontend PM: add `qualityGate` prop and structured task-detail contract sections; remove nonfunctional new/settings buttons.
- Frontend CE: require real blueprint id/path for task-derived evidence; optionally merge read-only `/v2/chief-engineer/blueprints` results when available.
- Frontend Director: use a new service method for `/v2/director/run`; pass selected task filter from `DirectorWorkspace`.
- Backend Director/runtime projection: add `blueprint_id`, `blueprint_path`, `runtime_blueprint_path` to task response and workflow metadata.
- Frontend realtime: show latest file edit in `RealTimeStatusBar` and `LlmRuntimeOverlay`.
- Backend LLM config: allow optional unassigned role rows, but keep required roles validated.

Not modifying:

- No large redesign of PM/CE/Director pages.
- No replacement of existing WS transport.
- No migration of Director internals or task market ownership.
- No target-project code changes.

## Test Plan

Happy path:

- PM task detail renders goal, steps, acceptance, files and provenance.
- CE page renders real blueprint evidence and runtime blueprints.
- Director selected execution calls `/v2/director/run` with a task filter.
- Status bar/overlay render the latest file edit.
- LLM config with optional unassigned `chief_engineer` role saves through validation.

Edge cases:

- PM task with summary but no blueprint id/path is not shown as CE blueprint.
- Empty CE blueprint list does not create fake entries.
- File edit event supports `filepath` and `size_bytes`.

Exception cases:

- CE blueprint detail for invalid id returns 400/404 without path traversal.
- Director run API failure is surfaced as action error/toast.

Regression cases:

- Existing `/v2/director/start/stop` service tests keep passing with corrected labels.
- Existing LLM runtime JSON-fragment filter remains active.

## Rollback Plan

Files expected to change:

- `src/frontend/src/app/App.tsx`
- `src/frontend/src/app/components/pm/PMWorkspace.tsx`
- `src/frontend/src/app/components/pm/PMTaskPanel.tsx`
- `src/frontend/src/app/components/chief-engineer/ChiefEngineerWorkspace.tsx`
- `src/frontend/src/app/components/director/DirectorWorkspace.tsx`
- `src/frontend/src/app/components/director/DirectorTaskPanel.tsx`
- `src/frontend/src/app/components/LlmRuntimeOverlay.tsx`
- `src/frontend/src/app/components/RealTimeStatusBar.tsx`
- `src/frontend/src/services/pmService.ts`
- `src/frontend/src/hooks/useProcessOperations.ts`
- `src/frontend/src/app/hooks/runtimeParsing.ts`
- `src/backend/polaris/delivery/http/v2/director.py`
- `src/backend/polaris/delivery/http/v2/chief_engineer.py`
- `src/backend/polaris/delivery/http/v2/__init__.py`
- `src/backend/polaris/cells/runtime/projection/internal/workflow_status.py`
- `src/backend/polaris/kernelone/llm/config_store.py`
- focused tests beside these components/services.

Rollback is a normal git revert of this batch. The behavioral checks to re-run after rollback are PM workspace render, Director task board render, LLM settings save, and backend director route tests.
