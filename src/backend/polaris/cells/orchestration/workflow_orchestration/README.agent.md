# orchestration.workflow_orchestration Cell

## 职责
Owns the OrchestrationService public contract, RuntimeOrchestrator port, ProcessLauncher port, and EventStream port. Depends on workflow_runtime for the WorkflowEngine instance and workflow_activity for concrete workflow/activity definitions. Coordinates workflow submission, signals, cancellation, and progress tracking.

## 公开契约
模块: polaris.cells.orchestration.workflow_orchestration.public.contracts,
polaris.cells.orchestration.workflow_orchestration.public.project_completion

Durable project completion coordinator owns owner-outcome/diagnostic queries,
claim-bound action dispatch, receipt revalidation, budgets, and terminal policy.
`workflow_runtime` supplies only the private typed cursor/CAS port.
The lifespan supervisor recovers nonterminal commands from that durable cursor.
Local process events are latency hints, never restart or completion authority.
Bootstrap composition (outside this Cell) binds the public action port to the
exact `runtime.task_runtime` owner row. The backend-lifespan Factory driver
recovers committed pending local-rework actions and resumes the owning run;
an HTTP Router may submit work but is not the task-lifetime authority.

Project action/no-progress/dispatch budgets are retry bounds, never terminal
model-capability evidence. Budget exhaustion parks as `control_plane_blocked`
and preserves the owner diagnostic's `next_action`. Only an exact sealed
`workflow_runtime.ModelCeilingTerminalResultV1` whose status is
`MODEL_CEILING_QUALIFIED` and `terminal=true` may append `model_ceiling`; its
authority binding hash is persisted and revalidated on every replay. Raw
status strings, mappings, unqualified owner decisions, and changed replay
bindings remain nonterminal.

## 依赖
- orchestration.workflow_engine
- orchestration.workflow_runtime
- orchestration.workflow_activity
- runtime.execution_broker
- runtime.projection
- factory.verification_guard
- runtime.state_owner
- policy.workspace_guard
- audit.evidence

## 效果
- fs.read:runtime/**
- fs.write:runtime/state/orchestration/*
- process.spawn:workflow/*
