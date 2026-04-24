# orchestration.workflow_engine Cell

## 职责
Owns the KernelOne WorkflowEngine — a self-hosted DAG/sequential workflow executor with retry, fail-fast, pause/resume, and signal support. Provides the HandlerRegistry protocol for Cell-internal registry injection. This cell owns the engine runtime and is the only cell that may modify kernelone/workflow/engine.py.

## 公开契约
模块: polaris.cells.orchestration.workflow_engine.public.contracts

## 依赖
- kernelone.process
- kernelone.trace
- infrastructure.db

## 效果
- fs.read:runtime/**
- db.read_write:workflow_runtime
