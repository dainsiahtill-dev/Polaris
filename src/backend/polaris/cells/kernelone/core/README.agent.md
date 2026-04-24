# kernelone.core

**Status**: Declared in cells.yaml — 目录待实现
**Directory**: polaris/cells/kernelone/core/（待创建）

## 职责

KernelOne 核心 Cell，提供 Context OS 和 Workflow Engine 等平台级基础能力。实际代码位于 `polaris/kernelone/` 下，通过此 Cell 声明将已有 kernelone 路径纳入 Cell 治理。

### 管理模块

- `polaris.kernelone.context.context_os.*` — Context OS: models, content_store, receipt_store, runtime, storage, snapshot, budget_optimizer, classifier, policies, ports
- `polaris.kernelone.workflow.engine` — Workflow Engine

## 公开契约

模块:
- `polaris.kernelone.context.context_os`
- `polaris.kernelone.workflow`

当前未定义具体 Command / Query / Event / Result / Error 契约（Contract gaps 已在 cells.yaml 中标记）。

## 依赖

无。

## 效果

- `fs.read:workspace/**`
- `fs.read:runtime/**`

## 验证

- 测试:
  - `polaris/kernelone/context/context_os/tests/**`
  - `polaris/kernelone/workflow/tests/**`
- Gaps:
  - Cell newly created to consolidate kernelone paths
  - Context OS contracts not yet fully defined
  - Workflow engine contracts not yet fully defined

## 备注

本 Cell 无独立的 `polaris/cells/kernelone/core/` 目录，owned_paths 指向 `polaris/kernelone/` 下的现有代码。
