# director.task_consumer Cell

## 职责
DEPRECATED - Use DirectorPool instead. Poll task market for PENDING_EXEC tasks and coordinate execution with Safe Parallel support. Provides ScopeConflictDetector for detecting scope path conflicts with other in-progress tasks.

## 公开契约
模块: polaris.cells.director.task_consumer

## 依赖
- runtime.task_market

## 效果
- fs.read:runtime/task_market/*
- fs.read:workspace/**
