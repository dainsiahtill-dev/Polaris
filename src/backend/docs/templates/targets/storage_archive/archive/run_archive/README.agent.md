# Run Archive

## Purpose

将终态 run 从 `runtime/runs/*` 发布到 `workspace/history/runs/*`，生成稳定 manifest 与 index，同时确保 archive 路径只读依赖 runtime，而不是反向篡改 runtime 事实。

## Kind

`capability`

## Public Inputs

- `ArchiveRunCommandV1`
- `ListHistoryRunsQueryV1`
- `GetArchiveManifestQueryV1`

## Public Outputs

- `ArchiveManifestV1`
- `HistoryRunsResultV1`
- `RunArchivedEventV1`

## Depends On

- `runtime.state_owner`
- `storage.layout`
- `policy.workspace_guard`
- `audit.evidence`

## State Ownership

- `history_run_archive`

## Effects Allowed

- `fs.read:runtime/runs/*`
- `fs.write:workspace/history/runs/*`
- `fs.write:workspace/history/runs.index.jsonl`

## Does Not

- 修改 runtime source-of-truth
- 写出 `workspace/history/runs/*` 之外的历史 run 内容
- 绕过 workspace 写入守卫

## Invariants

- history run manifest 一旦发布即视为 append-only
- history index 更新必须幂等
- 所有文本写入必须显式 UTF-8

## Migration Sources

当前最可能的迁移来源候选包括：

- `polaris/application/app/services/history_archive_service.py`
- `polaris/application/app/services/history_manifest_repository.py`

## Read Order for AI

1. `cell.yaml`
2. `generated/context.pack.json`
3. archive manifest / index contract
4. 当前 history archive 服务与 manifest repository
5. 必要时再扩张到 `runtime.state_owner` 与 `storage.layout`

## Verification

- `cells/archive/run_archive/tests/test_contracts.py`
- `cells/archive/run_archive/tests/test_behavior.py`

## Notes

该模板是目标蓝图；当前仓库中的相关行为仍大部分散落在兼容分片与历史服务实现中。
