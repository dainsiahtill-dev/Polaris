# Role Workspaces Product Closure Blueprint - 2026-05-07

## 1. Current Understanding

- Polaris is a desktop meta-tool platform. The product-critical chain is PM -> Chief Engineer -> Director -> QA, with runtime evidence visible in Electron.
- PM currently has a workspace and document panel, but user feedback indicates some document/status surfaces still feel like placeholders unless backed by runtime artifacts.
- Director has a workspace with task grouping and file edit event scaffolding, but the user-visible contract is incomplete:
  - "pending" does not clearly mean unclaimed.
  - claimed/running, blocked, failed, completed states are not presented as a task market lifecycle.
  - selecting a task does not yet expose a complete task detail panel with PM goal, checklist, acceptance, target files, dependencies, worker state, errors, and live file activity in one place.
- Chief Engineer has role naming in several widgets, but App currently exposes only main, PM, Director, Factory, and AGI workspaces. There is no first-class Chief Engineer workspace entry.

## 2. Evidence Inventory

- Read frontend entry/data flow:
  - `src/frontend/src/app/App.tsx`
  - `src/frontend/src/app/components/ControlPanel.tsx`
  - `src/frontend/src/app/components/pm/PMWorkspace.tsx`
  - `src/frontend/src/app/components/director/DirectorWorkspace.tsx`
  - `src/frontend/src/app/components/director/DirectorTaskPanel.tsx`
  - `src/frontend/src/types/task.ts`
  - `src/frontend/src/app/hooks/useRuntime.ts`
- Read backend Director API:
  - `src/backend/polaris/delivery/http/v2/director.py`
- Confirmed runtime/file edit path:
  - Backend websocket emits `type=file_edit` from `websocket_loop.py`.
  - Frontend parses file edit events in `runtimeParsing.ts` and stores them in `useRuntimeStore`.
  - Director workspace consumes `fileEditEvents` and computes task telemetry.
- Confirmed current App role route:
  - `activeRoleView` is `main | pm | director | factory | agi`.
  - No Chief Engineer role workspace branch exists.
- Subagent audits in progress:
  - PM document truthfulness.
  - Director backend task contract.
  - Chief Engineer frontend workspace.
  - Director TaskBoard frontend.
  - Realtime file-edit visibility.

## 3. Defect Analysis

### 3.1 Chief Engineer Is Not A First-Class Workspace

- Trigger: user wants to inspect blueprint, launch Director, and see Director list/status from Chief Engineer.
- Direct cause: App has no `chief_engineer` active role view and ControlPanel has no entry.
- Design cause: role surfaces grew around PM/Director first, while Chief Engineer remains embedded in labels/logs instead of a dedicated workflow surface.
- Impact: users cannot follow the PM -> CE -> Director handoff, so blueprints and Director status feel hidden or fake.

### 3.2 Director TaskBoard Lifecycle Is Not Product-Explicit

- Trigger: PM creates tasks and Director consumes them.
- Direct cause: frontend normalizes statuses to `pending/running/blocked/failed/completed`, but labels and details do not distinguish "unclaimed" from "claimed/running".
- Design cause: task rows are treated as generic cards rather than a task market lifecycle.
- Impact: users cannot tell what Director has not claimed, what is actively owned, what is blocked, and what needs attention.

### 3.3 Task Detail Surface Is Too Shallow

- Trigger: clicking a Director task.
- Direct cause: selection only changes terminal text and card highlight; task details remain spread across small card fragments.
- Design cause: selected task was not modeled as a primary inspectable object.
- Impact: PM contract quality, target files, acceptance criteria, dependencies, worker assignment, errors, and live file edits are not auditable from one place.

### 3.4 Realtime File Changes Exist But Are Not Anchored Enough

- Trigger: Director writes/edits/deletes files.
- Direct cause: file edit events are available, but the task detail view does not show per-task live activity as a first-class timeline.
- Design cause: code panel is global, while task execution diagnosis needs task-scoped evidence.
- Impact: user cannot see "this task is currently changing these files" without mentally correlating global logs.

## 4. Fix Plan

- Add a first-class Chief Engineer workspace:
  - App role state includes `chief_engineer`.
  - ControlPanel gets an entry.
  - New workspace component shows blueprint/document evidence, task handoff summary, Director list/status, and a Director launch/enter action.
- Make Director task lifecycle explicit:
  - Rename pending bucket to unclaimed.
  - Add claimed/running bucket semantics and visible owner/worker fields.
  - Preserve backend public interface while accepting richer metadata when present.
- Add selected task detail panel inside DirectorWorkspace:
  - PM goal/description.
  - execution checklist.
  - acceptance criteria.
  - target files/scope paths.
  - dependencies/blockers.
  - worker/claimed_by.
  - error/output.
  - task-scoped realtime file changes and line stats.
- Keep changes minimal:
  - Do not rewrite PM/Director orchestration.
  - Do not add a new framework.
  - Do not change target project files.
  - Use existing runtime snapshot, `/v2/director/tasks`, file edit events, and worker data.

## 5. Test Plan

- Frontend unit/Vitest:
  - App/ControlPanel exposes Chief Engineer entry.
  - Chief Engineer workspace renders blueprint evidence and Director action/status.
  - Director task selection displays complete detail.
  - Director task groups distinguish unclaimed/running/blocked/failed/completed.
  - Task detail shows task-scoped file edit activity.
- Backend unit/pytest:
  - If `/v2/director/tasks` contract is enriched, tests must prove metadata is preserved and statuses normalize safely.
- Electron/Playwright:
  - Open Electron.
  - Enter Chief Engineer workspace.
  - Verify blueprint/status area and Director entry.
  - Enter Director workspace.
  - Verify task groups, click a task, and confirm detail panel.
  - Confirm no false fatal dialog and no raw JSON fragment overlay.

## 6. Rollback Plan

- Revert this blueprint and the following expected files if regressions appear:
  - `src/frontend/src/app/App.tsx`
  - `src/frontend/src/app/components/ControlPanel.tsx`
  - `src/frontend/src/app/components/chief-engineer/*`
  - `src/frontend/src/app/components/director/DirectorWorkspace.tsx`
  - `src/frontend/src/types/task.ts`
  - any focused tests added under `src/frontend/src/app/components/**/__tests__`
- Re-run:
  - `npm run typecheck`
  - focused `npm run test -- ...`
  - `npm run test:e2e:acceptance`
