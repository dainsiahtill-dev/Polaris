# 工具注册标准 (TOOL Registration Standard)

## 1. 单一事实来源 (SSOT)

所有工具规范定义在 `polaris/kernelone/tools/contracts.py` 的 `_TOOL_SPECS` 字典中。

**格式要求：**
```python
"工具名": {
    "category": "read|write|exec",
    "description": "描述",
    "aliases": ["可选别名列表"],
    "arg_aliases": {"alias": "canonical", ...},
    "arguments": [
        {"name": "参数名", "type": "string|integer|boolean", "required": True/False},
    ],
    "response_format_hint": "响应格式提示",
    "required_any": [["参数组"]],
    "required_doc": "必需参数文档",
},
```

## 2. 工具注册流程

### 2.1 添加新工具

1. 在 `contracts.py` 的 `_TOOL_SPECS` 中添加条目
2. `arg_aliases` 定义参数别名映射
3. `arguments` 定义参数 schema
4. **不再需要**手动创建 `_*.py` normalizer 文件
5. **不再需要**修改 `TOOL_NORMALIZERS` 字典

### 2.2 现有工具迁移（渐进式）

对于已有 `polaris/kernelone/llm/toolkit/tool_normalization/normalizers/_<tool>.py` 的工具：

1. 先确保 `contracts.py` 中有完整 schema
2. `tool_normalization/__init__.py` 会自动使用 schema-driven fallback
3. 观察运行时是否正常工作
4. 测试通过后，可选择删除 `_<tool>.py` 文件（如果 schema 驱动已覆盖）

## 3. Schema 设计原则

### 3.1 参数别名设计

- 优先使用 LLM 常见称呼作为别名
- 例：`{"query": "search", "find": "search", "text": "search"}`

### 3.2 类型映射

- `string` → Python `str`
- `integer` → Python `int` (通过 `_coerce_int`)
- `boolean` → Python `bool` (通过 `_coerce_bool`)

### 3.3 路径归一化

常量 `_PATH_CANONICAL_KEYS` 中的参数会自动调用 `_normalize_workspace_alias_path`：
- `path`, `file`, `filepath`, `file_path`
- `root`, `dir`, `directory`, `cwd`
- `target`, `source`

## 4. 临时迁移指南

### 4.1 遗留工具整理

旧工具（`grep`, `ripgrep`, `search_code` 等）已迁移完成。

### 4.2 Plan C 过渡期

当前架构采用 fallback 策略：
1. 先查 `TOOL_NORMALIZERS` (旧路径)
2. 未注册则使用 `SchemaDrivenNormalizer` (新路径)

逐步迁移完成后，可删除 `TOOL_NORMALIZERS` 分发表中的旧条目。

## 5. CI 门禁

运行：`python docs/governance/ci/scripts/run_tool_normalization_gate.py --workspace .`
