# 工具别名映射设计规范

**创建日期**: 2026-03-27
**更新日期**: 2026-03-28
**版本**: 2.0
**状态**: Canonical 策略 (已更新)

---

## 核心原则

### Canonical 策略 (2026-03-28)

```
唯一真相：请求名 == 授权名 == 执行名 == 审计名
```

**禁止跨工具语义映射** - 这是 P0 级别的政策违规。

---

## 1. 允许的别名类型

### 1.1 命令执行别名（唯一允许的跨名映射）

```python
TOOL_NAME_ALIASES = {
    # 所有这些都映射到 execute_command
    "run_command": "execute_command",
    "run_shell": "execute_command",
    "exec_cmd": "execute_command",
    "shell_execute": "execute_command",
    "system_call": "execute_command",
    "command_line": "execute_command",
}
```

### 1.2 参数别名（同一工具内）

```python
# repo_read_head 参数别名
"repo_read_head": {
    "arg_aliases": {
        "count": "n",
        "lines": "n",
        "max_lines": "n",
        "limit": "n",
        "file_path": "file",
    }
}

# repo_rg 参数别名
"repo_rg": {
    "arg_aliases": {
        "query": "pattern",
        "file": "path",
        "max": "max_results",
    }
}
```

---

## 2. 禁止的别名映射

### 2.1 跨工具语义映射（绝对禁止）

```python
# ❌ 错误：功能不同的工具互为别名
"repo_read_head": ["read_file", "read"]  # 禁止！

# ❌ 错误：导致无限循环
# LLM 调用 read_file() 想读取完整文件
# -> 归一化为 repo_read_head()
# -> 只返回前50行
# -> LLM 继续调用 read_file()
# -> 循环...
```

### 2.2 禁止的工具映射组

| 工具 A | 工具 B | 原因 |
|--------|--------|------|
| `repo_read_head` | `read_file` | 部分读取 vs 完整读取 |
| `repo_read_head` | `repo_read_tail` | 前 N 行 vs 后 N 行 |
| `repo_read_head` | `repo_read_slice` | 开头 vs 指定范围 |
| `repo_tree` | `read_file` | 目录列表 vs 文件读取 |
| `repo_rg` | `search_replace` | 搜索 vs 替换 |

---

## 3. Canonical 工具清单

### 3.1 Read Tools (只读)

| Canonical Name | 语义 | 禁止别名 |
|--------------|------|----------|
| `repo_read_head` | `head -n` | read_file, read |
| `repo_read_tail` | `tail -n` | - |
| `repo_read_slice` | `[start, end]` | - |
| `repo_read_around` | 上下文读取 | - |
| `repo_rg` | ripgrep | - |
| `repo_tree` | `tree -L` | ls, dir |
| `repo_map` | 代码骨架 | - |
| `repo_symbols_index` | 符号索引 | - |
| `repo_diff` | `git diff` | - |

### 3.2 Write Tools (写入)

| Canonical Name | 语义 | 禁止别名 |
|--------------|------|----------|
| `precision_edit` | 精确编辑 | - |
| `repo_apply_diff` | 应用补丁 | - |
| `write_file` | 全文件写入 | - |
| `search_replace` | 搜索替换 | - |
| `edit_file` | 行区间编辑 | - |
| `append_to_file` | 追加内容 | - |

---

## 4. 验证检查表

添加工具别名前，必须验证：

- [x] 工具功能是否完全相同？
- [x] 默认参数值是否兼容？
- [x] 返回值格式是否一致？
- [x] 是否有其他工具也使用这些别名？（冲突检查）
- [x] 单元测试是否覆盖别名映射？
- [x] **这是跨工具映射吗？（P0 禁止）**

---

## 5. 治理门禁

### 5.1 P0 检测

```bash
# 运行 canonical gate
python docs/governance/ci/scripts/run_tool_calling_canonical_gate.py \
  --workspace . --role director --mode hard-fail
```

### 5.2 违规类型

| Category | 严重级别 | 描述 |
|----------|----------|------|
| `forbidden_cross_tool_mapping` | P0 | 跨工具语义映射 |
| `alias_tool_name_used` | P2 | 使用非 canonical 别名 |
| `raw_observed_name_drift` | P2 | 工具名漂移 |

---

## 6. 已知问题案例

### 6.1 错误案例：无限循环

```
问题：在扩展工具别名映射时，将 read_file 添加为 repo_read_head 的别名

结果：
  LLM 调用: read_file()
    → 归一化为: repo_read_head()
    → 使用默认参数: n=50
    → LLM 期望读取完整文件，但只读了50行
    → LLM 继续调用 read_file()
    → 循环...
```

### 6.2 错误案例：语义漂移

```
问题：repo_read_head -> read_file

结果：
  用户请求：读取文件前10行
  → 映射到 read_file
  → read_file 返回完整文件（可能很大）
  → LLM 无法处理意外的大量数据
  → 语义发生漂移
```

---

## 7. 相关文件

| 文件 | 说明 |
|------|------|
| `polaris/kernelone/tools/contracts.py` | Canonical 工具定义 SSOT |
| `polaris/kernelone/llm/toolkit/tool_normalization.py` | 工具名/参数归一化 |
| `polaris/kernelone/llm/toolkit/executor/handlers/repo.py` | Canonical handlers |
| `polaris/cells/roles/kernel/internal/llm_caller.py` | LLM 工具注入 |
| `docs/governance/ci/scripts/run_tool_calling_canonical_gate.py` | 治理门禁 |
| `docs/governance/CANONICAL_TOOL_SPEC.md` | Canonical 工具规范 |

---

## 8. 版本历史

| 日期 | 版本 | 变更 |
|------|------|------|
| 2026-03-27 | 1.0 | 初始版本，记录错误教训 |
| 2026-03-28 | 2.0 | 更新为 Canonical 策略，移除 LLM Tool Adapter |
