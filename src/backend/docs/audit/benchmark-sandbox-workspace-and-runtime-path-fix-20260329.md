# Benchmark Sandbox Workspace 与 Runtime Path 修复报告

**日期**: 2026-03-29
**问题**: L7 benchmark 无限循环根因 — Workspace/Runtime Path 混淆
**状态**: 已修复

---

## 1. 问题现象

运行 L7 benchmark 测试时，Polaris 产生错误的嵌套路径：

```
C:\Temp\BenchmarkTest\.polaris\runtime\.polaris\projects\l1-directory-listing-xxx\runtime\events
```

出现双重 `.polaris` 嵌套，且 runtime artifacts 没有落到预期的 RAMDISK `X:/` 目录。

---

## 2. 根因分析

### 2.1 Workspace 职责混淆

Benchmark 框架传入的 `workspace` 参数同时承担了两个职责：

| 职责 | 期望 | 实际 |
|------|------|------|
| 工具执行 (Tool Execution) | sandbox workspace (fixture 文件) | `C:/Temp/BenchmarkTest/` |
| 产物落盘 (Artifact Path) | Polaris 标准路径 | 同左 |

### 2.2 Runtime Root 计算错误

`benchmark_root` (`C:/Temp/BenchmarkTest/`) 本身包含 `.polaris/` 目录，导致 `resolve_storage_roots()` 解析出嵌套路径：

```
workspace_abs = C:/Temp/BenchmarkTest/
key = slug + hash(C:/Temp/BenchmarkTest/)
runtime_project_root = {runtime_base}/.polaris/projects/{key}/runtime
                    = C:/Temp/BenchmarkTest/.polaris/runtime/.polaris/projects/{key}/runtime
```

### 2.3 RAMDISK 未启用

`KERNELONE_RUNTIME_ROOT` 未设置，Polaris 使用了 `benchmark_root/.polaris/runtime/` 作为 runtime_base，而非系统 RAMDISK `X:/`。

---

## 3. 修复方案

### 3.1 目录结构

```
benchmark_root/                    # 管理用，无 .polaris/
└── {case_id}/                    # 传给 agent 的 workspace (fixture 复制到这里)
    ├── src/
    └── tests/

X:/.polaris/projects/{key}/runtime   # Polaris runtime artifacts (RAMDISK)
```

### 3.2 关键原则

| 路径类型 | 说明 |
|----------|------|
| `benchmark_root/` | 框架管理用，必须无 `.polaris/` |
| `benchmark_root/{case_id}/` | 真正的 workspace，传给 agent |
| `X:/.../runtime/` | Polaris runtime artifacts 自动落在这里 |

### 3.3 修改内容

#### `polaris/cells/llm/evaluation/internal/tool_calling_matrix.py`

**`materialize_case_workspace()` 函数**：

```python
# 修复前 (错误)
target_dir = (
    Path(base_workspace) / ".polaris" / "runtime" / "llm_evaluations" / run_id / "sandboxes" / case.case_id
)

# 修复后 (正确)
target_dir = Path(benchmark_root) / case.case_id
```

参数名 `base_workspace` → `benchmark_root`，避免与真正 workspace 混淆。

**`_collect_stream_observation()` 和 `_collect_non_stream_observation()` 参数**：

- `benchmark_root`: benchmark 根目录（不传给 agent，用于 journal 写入）
- `workspace`: 执行 workspace = `sandbox_workspace` = `benchmark_root/{case_id}/`

#### `polaris/delivery/cli/agentic_eval.py`

**`run_agentic_eval_command()` 函数**：

```python
# Force runtime artifacts to RAMDISK X:/ for benchmark runs.
# This must be set before ensure_minimal_kernelone_bindings() so that
# storage-root resolution picks it up from the cache key.
import os
os.environ.setdefault("KERNELONE_RUNTIME_ROOT", "X:/")

# Clear storage roots cache so the new runtime_root takes effect.
from polaris.kernelone.storage.layout import clear_storage_roots_cache
clear_storage_roots_cache()
```

---

## 4. 验证方法

### 4.1 检查 runtime artifacts 路径

```bash
# 运行 benchmark
python -m polaris.delivery.cli.agentic_eval --workspace C:/Temp/BenchmarkTest --suite tool_calling_matrix

# 验证 runtime artifacts 在 RAMDISK
ls X:/.polaris/projects/*/runtime/events/
```

### 4.2 检查 workspace 结构

```bash
# 验证 sandbox workspace 存在且无 .polaris 嵌套
ls C:/Temp/BenchmarkTest/l1-*/

# 预期输出：l1-directory-listing-xxx/, l1-grep-search-xxx/ 等
# 每个 case_id 目录下应直接包含 src/, tests/ 等 fixture 文件
```

---

## 5. Polaris Storage Layout 参考

```
PolarisStorageRoots 关键路径:
├── workspace_abs             = 传入的 workspace（执行上下文）
├── workspace_key            = slug + hash(workspace_abs) → 用于路径隔离
│
├── project_persistent_root   = {workspace}/.polaris
├── workspace_persistent_root = {workspace}/.polaris
│
├── runtime_base             = 系统缓存路径（KERNELONE_RUNTIME_ROOT 或 RAMDISK）
├── runtime_projects_root    = {runtime_base}/.polaris/projects
├── runtime_project_root     = {runtime_projects_root}/{workspace_key}/runtime  ← 审计产物
└── runtime_root             = runtime_project_root（别名）
```

---

## 6. 修改文件清单

| 文件 | 修改类型 | 说明 |
|------|----------|------|
| `polaris/cells/llm/evaluation/internal/tool_calling_matrix.py` | 重构 | `materialize_case_workspace` 路径逻辑，参数名 `base_workspace` → `benchmark_root` |
| `polaris/delivery/cli/agentic_eval.py` | 新增 | 设置 `KERNELONE_RUNTIME_ROOT=X:/`，清理 storage cache |

---

## 7. 相关文档

- `docs/audit/benchmark-framework-audit-20260328.md` — Benchmark 框架整体审计
- `docs/blueprints/benchmark-framework-convergence-blueprint-20260328.md` — Benchmark 框架收敛蓝图
- `docs/blueprints/STREAM_NONSTREAM_PARITY_FIX_20260329.md` — Stream/Non-Stream 执行路径一致化修复
