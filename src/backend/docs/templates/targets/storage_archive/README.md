# Storage Archive 目标 Cell 模板

## 定位

本目录承载 `runtime.state_owner` 与 `archive.*` 的目标模板资产。它们用于指导迁移与治理收口，不代表这些 Cell 已经在当前仓库中完成落地。

当前事实图谱仍以以下资产为准：

- `docs/graph/catalog/cells.yaml`
- `docs/graph/subgraphs/pm_pipeline.yaml`
- `docs/graph/subgraphs/director_pipeline.yaml`
- `docs/graph/subgraphs/context_plane.yaml`

对应的目标子图模板位于：

- `docs/graph/subgraphs/storage_archive_pipeline.template.yaml`

## 使用方式

建议按以下顺序读取：

1. `docs/FINAL_SPEC.md` 中关于状态、Effect、存储归档的章节。
2. `docs/graph/subgraphs/storage_archive_pipeline.template.yaml`
3. 本目录中对应 Cell 的 `cell.yaml`
4. 对应 `README.agent.md`
5. 对应 `generated/context.pack.json`
6. 仅在需要时再回到当前兼容分片与历史实现读取源码

## 模板清单

| Target Cell | 角色 | 当前迁移来源候选 |
| --- | --- | --- |
| `runtime.state_owner` | 统一运行时状态唯一写入口与受控查询出口 | `polaris/application/app/routers/runtime.py`、`polaris/application/app/services/task_board.py`、`polaris/application/app/services/task_board_refactored.py`、`polaris/application/app/services/orchestration_command_service.py` |
| `archive.run_archive` | 将终态 run 从 `runtime/runs/*` 发布到 `workspace/history/runs/*` | `polaris/application/app/services/history_archive_service.py`、`polaris/application/app/services/history_manifest_repository.py` |
| `archive.task_snapshot_archive` | 将 PM/Task 终态快照发布到 `workspace/history/tasks/*` | `polaris/application/app/services/archive_hook.py`、`polaris/application/app/services/history_archive_service.py` |
| `archive.factory_archive` | 将 Factory 终态产物发布到 `workspace/history/factory/*` | `polaris/application/app/services/archive_hook.py`、`polaris/application/app/services/factory_run_service.py`、`polaris/application/app/services/factory_store.py` |

## 约束

- 这些模板不得直接写回当前 `docs/graph/catalog/cells.yaml`，除非对应 Cell 已完成代码收口并通过最小治理门禁。
- 模板中的 `owned_paths` 表示目标归位路径，不等价于当前 owned paths 事实。
- 任何实现迁移都必须同步更新图谱、契约、状态所有权和验证目标。
