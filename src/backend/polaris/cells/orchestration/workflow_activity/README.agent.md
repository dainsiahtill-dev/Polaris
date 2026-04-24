# orchestration.workflow_activity Cell

## 职责
Owns the Activity and Workflow definition public contracts and DI port for injecting HandlerRegistry implementations (ActivityRegistry, WorkflowRegistry) into the WorkflowEngine. All concrete implementations live in workflow_runtime; this cell defines only the port contracts and the registry abstractions.

## 公开契约
模块: polaris.cells.orchestration.workflow_activity.public.contracts

## 依赖
- orchestration.workflow_engine
- orchestration.workflow_runtime

## 效果
- 无
