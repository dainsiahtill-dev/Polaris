# orchestration.workflow_orchestration Cell

## 职责
Owns the OrchestrationService public contract, RuntimeOrchestrator port, ProcessLauncher port, and EventStream port. Depends on workflow_runtime for the WorkflowEngine instance and workflow_activity for concrete workflow/activity definitions. Coordinates workflow submission, signals, cancellation, and progress tracking.

## 公开契约
模块: polaris.cells.orchestration.workflow_orchestration.public.contracts

## 依赖
- orchestration.workflow_engine
- orchestration.workflow_runtime
- orchestration.workflow_activity
- runtime.execution_broker
- runtime.state_owner
- policy.workspace_guard
- audit.evidence

## 效果
- fs.read:runtime/**
- fs.write:runtime/state/orchestration/*
- process.spawn:workflow/*
