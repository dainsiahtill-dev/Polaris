# Context OS v2 Rollback Script

## 概述

`rollback_contextos_v2.sh` 是 Context OS v2 的回滚脚本,用于在系统异常或测试失败时快速回滚到上一个稳定状态。

**执行时间**: <= 30s

## 快速开始

```bash
# 预览所有回滚操作 (不实际执行)
./rollback_contextos_v2.sh all --dry-run

# 回滚上下文组件
./rollback_contextos_v2.sh context

# 回滚语义索引
./rollback_contextos_v2.sh semantic

# 回滚认知/推理状态
./rollback_contextos_v2.sh cognitive

# 回滚所有组件
./rollback_contextos_v2.sh all
```

## 回滚范围

| 范围 | 说明 | 目标文件/目录 |
|------|------|--------------|
| `context` | 上下文状态和缓存 | `context_gateway.py`, `models.py`, `history_materialization.py` |
| `semantic` | 语义索引 | `index_store.py`, `descriptor_cache.json`, `.semantic_index/` |
| `cognitive` | 认知/推理状态 | `rollback_manager.py`, `working_state.json`, `session_state.json` |
| `all` | 所有组件 | 上述所有文件 |

## 选项

| 选项 | 说明 |
|------|------|
| `--dry-run` / `-n` | 只输出将要执行的操作,不实际修改 |
| `--help` / `-h` | 显示帮助信息 |

## 工作流程

脚本执行以下5个阶段:

1. **Phase 1: 创建回滚前快照**
   - 在 `meta/backups/context_gateway/snapshots/` 下创建时间戳快照
   - 快照包含所有目标文件的副本

2. **Phase 2: 清理 Context 缓存**
   - 清理 `.cache/context/` 目录
   - 清理 semantic index 锁文件
   - 清理 Python `__pycache__` 缓存

3. **Phase 3: 执行回滚**
   - 从最新快照恢复文件到原始位置

4. **Phase 4: 验证**
   - 检查关键文件是否存在
   - 验证文件完整性

5. **Phase 5: 列出可用快照**
   - 显示所有可用的回滚快照

## 备份目录结构

```
workspace/
├── meta/
│   └── backups/
│       └── context_gateway/
│           └── snapshots/
│               ├── context_20260413_143052/
│               │   ├── context_gateway.py
│               │   └── snapshot_meta.json
│               ├── semantic_20260413_142830/
│               │   └── ...
│               └── cognitive_20260413_142901/
│                   └── ...
└── .cache/
    └── context/
```

## 快照元数据

每个快照包含 `snapshot_meta.json`:

```json
{
    "scope": "context",
    "timestamp": "20260413_143052",
    "created_at": "2026-04-13T14:30:52+08:00",
    "backup_path": "/path/to/snapshot"
}
```

## 示例输出

### DRY-RUN 模式

```bash
$ ./rollback_contextos_v2.sh all --dry-run

==============================================
  Context OS v2 Rollback Script (v2.1)
==============================================

配置:
  目标范围: all
  模式: DRY-RUN (只预览)
  备份目录: /workspace/meta/backups/context_gateway/snapshots
  超时限制: 30s

[WARNING] DRY-RUN 模式: 只显示将要执行的操作

--- Phase 1: 创建回滚前快照 ---
[INFO] 创建 all 快照: all_20260413_143210
  [DRY-RUN] 创建快照: /workspace/meta/backups/context_gateway/snapshots/all_20260413_143210

--- Phase 2: 清理 Context 缓存 ---
[INFO] 清理 Context 缓存...
  [DRY-RUN] 清理缓存目录: /workspace/.cache/context
  [DRY-RUN] 清理索引: /workspace/.semantic_index/

--- Phase 3: 执行回滚 ---
[INFO] 从快照恢复 context: context_20260413_143052
  [DRY-RUN] 恢复文件: /workspace/polaris/cells/roles/kernel/internal/context_gateway.py

...

[SUCCESS] DRY-RUN 完成 (1s)
```

### 执行模式

```bash
$ ./rollback_contextos_v2.sh context

==============================================
  Context OS v2 Rollback Script (v2.1)
==============================================

配置:
  目标范围: context
  模式: 执行
  备份目录: /workspace/meta/backups/context_gateway/snapshots
  超时限制: 30s

--- Phase 1: 创建回滚前快照 ---
[INFO] 创建 context 快照: context_20260413_143210
  快照: /workspace/polaris/cells/roles/kernel/internal/context_gateway.py
  快照: /workspace/polaris/kernelone/context/context_os/models.py
[SUCCESS] 快照已创建: context_20260413_143210

--- Phase 2: 清理 Context 缓存 ---
[INFO] 清理 Context 缓存...
  已清理: /workspace/.cache/context
[SUCCESS] 缓存已清理

--- Phase 3: 执行回滚 ---
[INFO] 从快照恢复 context: context_20260413_143052
  恢复文件: /workspace/polaris/cells/roles/kernel/internal/context_gateway.py
[SUCCESS] context 已从快照恢复

--- Phase 4: 验证 ---
[INFO] 验证 context 回滚...
[SUCCESS] context_gateway.py 验证通过

--- Phase 5: 可用快照 ---
[INFO] 可用快照:
  - context_20260413_143210 (scope: context, time: 20260413_143210)
  - context_20260413_143052 (scope: context, time: 20260413_143052)

[SUCCESS] 回滚完成 (2s)
==============================================
```

## 超时保护

脚本内置30秒超时保护。如果执行时间超过限制,脚本会立即停止并报告错误:

```
[ERROR] 操作超时 (35s > 30s)
```

## 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `WORKSPACE` | 工作区根目录 | `.` (当前目录) |

## 故障排除

### 快照不存在

如果回滚时提示 "未找到快照":

1. 检查快照目录是否存在: `ls meta/backups/context_gateway/snapshots/`
2. 确保之前执行过相关操作并成功创建了快照
3. 手动创建快照: `./rollback_contextos_v2.sh <scope> --dry-run`

### 权限错误

确保脚本有执行权限:

```bash
chmod +x rollback_contextos_v2.sh
```

### 文件冲突

如果回滚时文件被占用,脚本会跳过该文件并继续执行其他操作。可在文件释放后重新执行回滚。

## 相关文件

- 核心实现: `polaris/kernelone/cognitive/execution/rollback_manager.py`
- 上下文网关: `polaris/cells/roles/kernel/internal/context_gateway.py`
- 上下文模型: `polaris/kernelone/context/context_os/models.py`
