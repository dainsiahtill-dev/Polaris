# Runtime State Owner

## Purpose

作为 `runtime/*` 事实层的唯一写拥有者，负责统一写入 `runtime/tasks/*`、`runtime/contracts/*`、`runtime/runs/*` 与 `runtime/state/*`，并为归档、投影和查询提供受控读取出口。

## Kind

`capability`

## Public Inputs

- `PersistRuntimeTaskStateCommandV1`
- `PersistRuntimeContractCommandV1`
- `PersistRuntimeRunCommandV1`
- `GetRuntimeSnapshotQueryV1`
- `GetRuntimeRunQueryV1`

## Public Outputs

- `RuntimeStateWriteResultV1`
- `RuntimeSnapshotResultV1`
- `RuntimeRunResultV1`
- `RuntimeStateChangedEventV1`
- `RuntimeStateWrittenEventV1`

## Depends On

- `storage.layout`
- `policy.workspace_guard`
- `audit.evidence`
- `runtime.projection`

## State Ownership

- `runtime_task_state`
- `runtime_contract_state`
- `runtime_run_state`
- `runtime_snapshot_state`

## Effects Allowed

- `fs.read:runtime/*`
- `fs.write:runtime/tasks/*`
- `fs.write:runtime/contracts/*`
- `fs.write:runtime/runs/*`
- `fs.write:runtime/state/*`

## Does Not

- directly write `workspace/history/*`
- bypass `storage.layout` 解析结果
- 修改 archive index

## Invariants

- runtime source-of-truth 只能由本 Cell 写入
- 所有文本写入必须显式 UTF-8
- 历史归档必须通过 `archive.*` Cell 完成

## Current Implementation Anchors

当前实现和迁移收口必须以以下 Cell 事实源为准：

- `polaris/cells/runtime/state_owner/**`
- `polaris/cells/runtime/task_runtime/**`
- `polaris/cells/runtime/task_market/**`
- `polaris/cells/runtime/projection/**`

已退休的 application services 分片不得作为迁移来源、上下文热点或 AI 读取入口。

## Read Order for AI

1. `cell.yaml`
2. `generated/context.pack.json`
3. 运行时状态相关 Contract
4. 当前 runtime Cell 兼容入口与状态写入热点
5. 仅在必要时再扩张到 `storage.layout`、`policy.workspace_guard`、`audit.evidence`

## Verification

- `cells/runtime/state_owner/tests/test_contracts.py`
- `cells/runtime/state_owner/tests/test_behavior.py`

## Notes

该模板是目标蓝图，不代表当前仓库已经具备独立的 `runtime.state_owner` Cell。
