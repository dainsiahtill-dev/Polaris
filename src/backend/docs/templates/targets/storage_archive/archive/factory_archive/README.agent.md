# Factory Archive

## Purpose

将 Factory 终态产物从 `workspace/factory/*` 发布到 `workspace/history/factory/*`，沉淀可审计、可追溯的历史归档，同时把 Factory 历史保留职责与 runtime 状态拥有职责彻底分离。

## Kind

`capability`

## Public Inputs

- `ArchiveFactoryRunCommandV1`
- `GetFactoryArchiveManifestQueryV1`

## Public Outputs

- `ArchiveManifestV1`
- `FactoryArchivedEventV1`

## Depends On

- `storage.layout`
- `policy.workspace_guard`
- `audit.evidence`

## State Ownership

- `history_factory_archive`

## Effects Allowed

- `fs.read:workspace/factory/*`
- `fs.write:workspace/history/factory/*`

## Does Not

- 修改 Factory 源产物
- 写出 `workspace/history/factory/*` 之外的 Factory 历史内容
- 绕过 workspace 写入守卫

## Invariants

- Factory archive 一旦发布即视为不可变
- manifest 必须保留 artifact lineage
- 所有文本写入必须显式 UTF-8

## Current Implementation Anchors

当前模板应指向已经收敛的 archive Cell 与共享 KernelOne 索引仓储：

- `polaris/cells/archive/factory_archive/internal/factory_archive_service.py`
- `polaris/cells/archive/factory_archive/public/service.py`
- `polaris/kernelone/storage/history_manifest_repository.py`

## Read Order for AI

1. `cell.yaml`
2. `generated/context.pack.json`
3. factory archive manifest contract
4. 当前 Factory run/store/archive 相关实现
5. 必要时再扩张到 `storage.layout` 与 `audit.evidence`

## Verification

- `cells/archive/factory_archive/tests/test_contracts.py`
- `cells/archive/factory_archive/tests/test_behavior.py`

## Notes

该模板的重点是切开 Factory 历史保留职责，而不是继续在已有 service 中叠补丁。
