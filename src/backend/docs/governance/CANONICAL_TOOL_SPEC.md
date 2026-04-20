# Canonical Tool Specification

**Version:** 1.0.0
**Date:** 2026-03-28
**Status:** Canonical Policy

---

## 1. 核心原则

### 1.1 唯一真相 (Single Source of Truth)

```
请求名 == 授权名 == 执行名 == 审计名
```

工具身份在整条调用链中必须保持一致。

### 1.2 禁止跨工具映射 (Cross-Tool Mapping Forbidden)

```
REPO_READ_HEAD (canonical) ≠ READ_FILE (canonical)

跨工具语义映射 = 政策违规 (P0)
```

### 1.3 仅允许同工具参数别名 (Same-Tool Argument Aliases Only)

参数别名是同一工具内部的不同参数命名风格，不改变工具语义。

---

## 2. Canonical 工具清单

### 2.1 Read Tools (只读)

| Canonical Name | 语义 | 参数 | 描述 |
|--------------|------|------|------|
| `repo_read_head` | `head -n` | `file`, `n` | 读取文件前 N 行 |
| `repo_read_tail` | `tail -n` | `file`, `n` | 读取文件后 N 行 |
| `repo_read_slice` | `[start, end]` | `file`, `start`, `end` | 读取精确行范围 |
| `repo_read_around` | 上下文读取 | `file`, `line`, `radius` | 围绕目标行读取 |
| `repo_rg` | ripgrep | `pattern`, `path`, `glob` | 正则搜索 |
| `repo_tree` | `tree -L` | `path`, `depth` | 目录树 |
| `repo_map` | 代码骨架 | `root`, `max_files` | 代码结构映射 |
| `repo_symbols_index` | 符号索引 | `paths`, `glob` | 符号索引 |
| `repo_diff` | `git diff` | `stat`, `mode` | Git 差异 |

### 2.2 Write Tools (写入)

| Canonical Name | 语义 | 参数 | 描述 |
|--------------|------|------|------|
| `precision_edit` | 精确编辑 | `file`, `search`, `replace` | 语义精确编辑 |
| `repo_apply_diff` | 应用补丁 | `diff` | 应用 unified diff |
| `write_file` | 全文件写入 | `file`, `content` | 全量覆盖写入 |
| `search_replace` | 搜索替换 | `file`, `search`, `replace` | 文本搜索替换 |
| `edit_file` | 行区间编辑 | `file`, `start_line`, `end_line`, `content` | 行区间编辑 |
| `append_to_file` | 追加内容 | `file`, `content` | 文件末尾追加 |

### 2.3 Exec Tools (执行)

| Canonical Name | 语义 | 参数 | 描述 |
|--------------|------|------|------|
| `execute_command` | shell 执行 | `command`, `timeout`, `shell` | 受限命令执行 |

### 2.4 Session Memory Tools

| Canonical Name | 语义 | 参数 | 描述 |
|--------------|------|------|------|
| `search_memory` | 状态搜索 | `query`, `kind`, `limit` | Context OS 搜索 |
| `read_artifact` | 读取 artifact | `artifact_id`, `start_line`, `end_line` | 读取 artifact |
| `read_episode` | 读取 episode | `episode_id` | 读取 episode |
| `get_state` | 读取状态 | `path` | 读取状态路径 |

---

## 3. 参数别名规范

### 3.1 允许的别名类型

```python
# 路径参数别名 (同一工具内)
file_path -> file
filepath -> file
path -> file  # 对于需要 file 参数的工具

# 范围参数别名 (同一工具内)
start_line -> start
end_line -> end
count -> n
lines -> n
limit -> n

# 搜索参数别名 (搜索类工具内)
query -> pattern
search -> pattern
keyword -> pattern
q -> pattern
```

### 3.2 禁止的别名

```python
# 禁止：跨工具参数映射
repo_read_head.n -> search_code.pattern  # 禁止

# 禁止：工具名作为参数别名
tool: "repo_read_head"
args: {n: 100}  # n 是 repo_read_head 的参数
# 不能映射到 search_code 的任何参数
```

---

## 4. 错误码规范

| 错误码 | 含义 | 严重级别 |
|--------|------|----------|
| `UNKNOWN_TOOL` | 工具名称不在 canonical 清单中 | P0 |
| `FORBIDDEN_CROSS_TOOL_MAPPING` | 检测到跨工具语义映射 | P0 |
| `MISSING_REQUIRED_TOOL` | 必需工具不在 raw_events 中 | P1 |
| `RAW_OBSERVED_COUNT_MISMATCH` | raw 与 observed 工具数量不一致 | P1 |
| `ALIAS_TOOL_NAME_USED` | 使用了非 canonical 工具名 | P2 |
| `RAW_OBSERVED_NAME_DRIFT` | raw 与 observed 工具名不一致 | P2 |

---

## 5. 治理门禁

### 5.1 Canonical Gate

```bash
python docs/governance/ci/scripts/run_tool_calling_canonical_gate.py \
  --workspace . \
  --role director \
  --mode hard-fail
```

### 5.2 验收标准

| 指标 | 要求 | 严重级别 |
|------|------|----------|
| `canonical_name_hit_rate` | = 1.0 (100%) | P0 |
| `non_canonical_call_count` | = 0 | P0 |
| `forbidden_cross_tool_mapping_count` | = 0 | P0 |
| `tool_name_downgrade_count` | = 0 | P0 |

### 5.3 违规处理

```
P0 违规 → 立即失败，不允许自动修正
P1 违规 → 记录并警告
P2 违规 → 记录审计
```

---

## 6. 执行器验收

### 6.1 必须通过的测试

```python
# 测试 1: Canonical 工具原生执行
executor.execute('repo_read_head', {'file': 'test.py', 'n': 10})
# 必须返回 ok=True，不能返回 "Unknown tool"

# 测试 2: 跨工具映射检测
executor.execute('repo_read_head', {'file': 'test.py', 'n': 10})
# 不能被静默映射到 read_file
```

### 6.2 白名单验证

```python
# Director 白名单必须包含所有 repo_* 工具
whitelist = profile.tool_policy.whitelist
assert 'repo_read_head' in whitelist
assert 'repo_rg' in whitelist
assert 'repo_tree' in whitelist
```

---

## 7. 实现清单

| 组件 | 文件 | 状态 |
|------|------|------|
| Canonical 工具定义 | `polaris/kernelone/tools/contracts.py` | ✅ |
| 执行器 handlers | `polaris/kernelone/llm/toolkit/executor/handlers/repo.py` | ✅ |
| 参数归一化 | `polaris/kernelone/llm/toolkit/tool_normalization.py` | ✅ |
| 白名单配置 | `polaris/cells/roles/profile/internal/builtin_profiles.py` | ✅ |
| LLM 工具注入 | `polaris/cells/roles/kernel/internal/llm_caller.py` | ✅ |
| 治理门禁 | `docs/governance/ci/scripts/run_tool_calling_canonical_gate.py` | ✅ |

---

## 8. 迁移指南

### 8.1 从旧名称迁移

| 旧名称 | 替代为 | 状态 |
|--------|--------|------|
| `read_file` (读取前 N 行) | `repo_read_head` | ✅ |
| `read_file` (读取后 N 行) | `repo_read_tail` | ✅ |
| `read_file` (读取范围) | `repo_read_slice` | ✅ |
| `grep` (正则搜索) | `repo_rg` | ✅ |
| `ls` / `dir` | `repo_tree` | ✅ |
| `read_file` (全文) | `read_file` | ✅ 保留 |
| `search_code` | `search_code` | ✅ 保留 |

### 8.2 兼容性说明

- `read_file` (全文读取) 保留用于完整文件读取场景
- `search_code` / `grep` / `ripgrep` 保留用于通用搜索场景
- 角色白名单继续支持旧名称以保持向后兼容
- 评测门禁检测是否使用了旧名称并记录

---

## 9. 参考

- [AGENTIC_TOOL_CALLING_MATRIX_V2_STANDARD.md](../AGENTIC_TOOL_CALLING_MATRIX_V2_STANDARD.md)
- [TOOL_CALLING_CANONICAL_GATE_STANDARD.md](./TOOL_CALLING_CANONICAL_GATE_STANDARD.md)
- [TOOL_ALIAS_DESIGN_GUIDE.md](./TOOL_ALIAS_DESIGN_GUIDE.md)
