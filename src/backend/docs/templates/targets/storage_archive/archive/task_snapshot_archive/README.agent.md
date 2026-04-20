# Task Snapshot Archive

## Purpose

将 PM 计划与 Task 终态快照从 `runtime/tasks/*` 派生到 `workspace/history/tasks/*`，用于后续审计、回放与历史查询，同时严格避免 archive 路径变成第二个 runtime 写拥有者。

## Kind

`capability`

## Public Inputs

- `ArchiveTaskSnapshotCommandV1`
- `GetTaskSnapshotManifestQueryV1`

## Public Outputs

- `ArchiveManifestV1`
- `TaskSnapshotArchivedEventV1`

## Depends On

- `runtime.state_owner`
- `storage.layout`
- `policy.workspace_guard`
- `audit.evidence`

## State Ownership

- `history_task_snapshot_archive`

## Effects Allowed

- `fs.read:runtime/tasks/*`
- `fs.write:workspace/history/tasks/*`

## Does Not

- 修改 runtime plan/task 源文件
- 写出 `workspace/history/tasks/*` 之外的任务历史内容
- 绕过 workspace 写入守卫

## Invariants

- task snapshot archive 只能从终态 runtime 事实导出
- manifest 必须保持 task 与 plan 的可追踪关联
- 所有文本写入必须显式 UTF-8

## Migration Sources

当前最可能的迁移来源候选包括：

- `polaris/application/app/services/archive_hook.py`
- `polaris/application/app/services/history_archive_service.py`

## Read Order for AI

1. `cell.yaml`
2. `generated/context.pack.json`
3. task snapshot manifest contract
4. 当前 archive hook 与 history archive 实现
5. 必要时再扩张到 `runtime.state_owner`

## Verification

- `cells/archive/task_snapshot_archive/tests/test_contracts.py`
- `cells/archive/task_snapshot_archive/tests/test_behavior.py`

## Notes

该模板定义的是目标边界，不代表当前代码已经完成 task snapshot archive 的独立化。
